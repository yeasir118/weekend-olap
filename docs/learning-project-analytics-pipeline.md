# Mini project: an event-driven analytics pipeline, from OLTP to dashboards

A small, weekend-sized project for an engineer who knows OLTP systems well (Postgres, transactions,
APIs) but has never built the analytics side. It rebuilds, in miniature, the reporting architecture
TillPay uses (see `docs/reporting-flows.md`). The goal is not a complete product: it is to learn
**the shape of the pipeline and why each piece exists**, using the practices real teams use.

By the end you will have built, and then deliberately broken, every stage of this:

```
 Postgres ──(outbox)──▶ publisher ──▶ Kafka ──▶ ClickHouse raw ──▶ dbt: conformed ──▶ dbt: marts
 (OLTP)                                          (as delivered)     (clean, live)      (aggregated)
                                                                         │                  │
                                                                     report API          Superset
                                                                  (governed, live)   (self-service BI)
```

---

## 1 · The domain: a tiny coffee chain

Keep the business trivial so all your attention goes to the pipeline.

- An **order** is created, then moves through states: `CREATED → PAID → SERVED`, or ends in
  `CANCELLED` or `REFUNDED`.
- Each order has: `order_id`, `store` (3 stores), `channel` (`app`, `counter`), `customer_id`,
  `amount_minor` (cents), timestamps.
- A small script plays the "shop": it creates orders and moves them through states at random, some
  minutes or hours later. Some move after midnight, on purpose.

This is the same shape as TillPay's transaction (one entity, a lifecycle of states, events for every
change), which is all you need.

---

## 2 · The stack

Everything runs in one `docker compose` file on a laptop.

| Piece | Use | Why this one |
|---|---|---|
| **Postgres 16** | the OLTP database: `orders` and `outbox` | what you already know |
| **Publisher** | ~80 lines, in a language you like | moves outbox rows to Kafka |
| **Redpanda** (or Kafka) | the event bus, topic `shop.orders.v1` | Kafka-compatible, one container, no ZooKeeper. TillPay uses Kafka; the API is the same. |
| **ClickHouse 24.x** | the analytics database (OLAP) | column store; reads Kafka by itself |
| **dbt-core + dbt-clickhouse** | the SQL transformations, tests and docs | the industry standard for the "T" in ELT |
| **Report API** | ~200 lines (FastAPI or Go) | stands in for reportd: governed reports from YAML |
| **Apache Superset** | dashboards | stands in for TillPay's Superset |

---

## 3 · Concepts you will meet (the OLTP → OLAP shift)

| You know (OLTP) | You will learn (OLAP) |
|---|---|
| Normalised tables, updated in place | **Append-only events**; the current state is *computed* from them |
| Row store, indexes, point reads | **Column store**: scans of a few columns over millions of rows are cheap; point updates are not |
| One database is the truth | **The database is the source; the bus carries facts out**; analytics never reads OLTP tables |
| Exactly-once inside a transaction | **At-least-once delivery**: duplicates are normal and are removed downstream |
| `UPDATE` fixes data | **Recompute** fixes data: rebuild a day, a partition, or everything |
| Schema in migrations | **Layers**: raw → conformed → marts, each with an owner and a contract |
| App-level authorization | **Database users per consumer**, plus a governed API for anything personal |

---

## 4 · Milestones

Do them in order. Each ends with a **check** (prove it works) and a **break-it lab** (prove you
understand why it is built this way). The labs are the point of the project; do not skip them.

### M1 · OLTP plus the transactional outbox

**Build.**
```sql
CREATE TABLE orders (
  order_id     text PRIMARY KEY,
  store        text NOT NULL,
  channel      text NOT NULL,
  customer_id  text NOT NULL,
  amount_minor bigint NOT NULL,
  state        text NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE outbox (
  id           bigserial PRIMARY KEY,
  event_id     uuid NOT NULL DEFAULT gen_random_uuid(),
  aggregate_id text NOT NULL,          -- order_id; becomes the Kafka key
  event_type   text NOT NULL,          -- order.created, order.paid, ...
  payload      jsonb NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz
);
```
Every state change does **both writes in one database transaction**: update `orders`, insert into
`outbox`. Put the full current order in the payload, plus `occurred_at`.

**Why.** If you wrote to the database and then to Kafka directly, a crash between the two loses or
invents an event. The outbox makes "the change happened" and "the event exists" a single atomic
fact.

**Check.** Run the shop script for a minute. Every row in `orders` has at least one outbox row, and
the number of outbox rows for an order equals its number of state changes.

### M2 · The publisher and the bus

**Build.** A loop:
1. `SELECT … FROM outbox WHERE published_at IS NULL ORDER BY id LIMIT 100 FOR UPDATE SKIP LOCKED`
2. Produce each row to `shop.orders.v1`, with **key = `aggregate_id`** and header `event_id`.
3. Wait for the broker to acknowledge (flush).
4. `UPDATE outbox SET published_at = now() WHERE id = ANY(...)`, then commit.

**Why.**
- The key keeps all events of one order in **one partition**, so they stay in order.
- Produce-then-mark makes delivery **at-least-once**: a crash after step 3 and before step 4 sends
  the batch again. That is accepted on purpose and handled later.
- `SKIP LOCKED` lets you run two publishers safely.

**Check.** `rpk topic consume shop.orders.v1` shows the events, with keys and headers.

**Break-it lab.** Kill the publisher between steps 3 and 4 (add a `sleep` there). Restart it. The
same events are now on the topic **twice**, with the same `event_id` at new offsets. Keep them: M4
must cope with them.

### M3 · ClickHouse ingestion: queue table, materialized view, raw table

**Build.**
```sql
CREATE DATABASE shop;

-- 1. The queue table: a Kafka consumer, stores nothing.
CREATE TABLE shop.order_events_queue (
  order_id String, store String, channel String, customer_id String,
  amount_minor Int64, state String, occurred_at String
) ENGINE = Kafka
SETTINGS kafka_broker_list = 'redpanda:9092',
         kafka_topic_list = 'shop.orders.v1',
         kafka_group_name = 'clickhouse-order-events',
         kafka_format = 'JSONEachRow',
         kafka_handle_error_mode = 'stream';   -- bad messages become rows, not a stuck consumer

-- 2. The raw table: everything as delivered, duplicates included.
CREATE TABLE shop.order_events (
  order_id String, store LowCardinality(String), channel LowCardinality(String),
  customer_id String, amount_minor Int64, state LowCardinality(String),
  occurred_at DateTime64(3), event_id String,
  kafka_partition UInt64, kafka_offset UInt64, ingested_at DateTime64(3)
) ENGINE = ReplacingMergeTree
ORDER BY (order_id, kafka_partition, kafka_offset);

-- 3. The materialized view: an INSERT TRIGGER from the queue into the raw table.
CREATE MATERIALIZED VIEW shop.order_events_mv TO shop.order_events AS
SELECT order_id, store, channel, customer_id, amount_minor, state,
       parseDateTime64BestEffort(occurred_at, 3)            AS occurred_at,
       _headers.value[indexOf(_headers.name, 'event_id')]   AS event_id,
       _partition AS kafka_partition, _offset AS kafka_offset,
       now64(3)   AS ingested_at
FROM shop.order_events_queue
WHERE length(_error) = 0;
```
Add a second table, `order_events_errors`, and a second materialized view that keeps `_raw_message`
and `_error` for rows `WHERE length(_error) > 0`.

**Why.** No ingestion service to write or run: ClickHouse consumes the topic itself. The raw layer is
an **immutable record of what arrived**, which you can always rebuild everything else from.

**Check.** Within seconds of a state change, `SELECT count() FROM shop.order_events` grows.

**Break-it labs.**
- **Poison message.** Produce `{"oops":` to the topic by hand. Ingestion keeps flowing, and the
  message appears in `order_events_errors`.
- **Pause and replay.** `DETACH TABLE shop.order_events_queue`, reset the consumer group to the
  earliest offset (`rpk group seek clickhouse-order-events --to start`), `TRUNCATE` the raw table,
  `ATTACH` again. The raw table refills from the bus. This is how you recover from a bad
  transformation: **the bus is the replayable source**.

### M4 · dbt: the conformed layer (views)

**Build.** A dbt project with `models/staging` and `models/conformed`, and a `generate_schema_name`
macro so models land in `shop_conformed` and `shop_marts` instead of the raw database.

`stg_order_events`, one row per real event (removes the duplicates from M2):
```sql
select
  event_id,
  argMin(order_id,     kafka_offset) as order_id,
  argMin(state,        kafka_offset) as state,
  argMin(store,        kafka_offset) as store,
  argMin(channel,      kafka_offset) as channel,
  argMin(customer_id,  kafka_offset) as customer_id,
  argMin(amount_minor, kafka_offset) as amount_minor,
  argMin(occurred_at,  kafka_offset) as occurred_at,
  min(kafka_offset)                  as first_offset
from {{ source('raw', 'order_events') }}
group by event_id
```

`fct_order_latest`, one row per order at its latest state:
```sql
select
  order_id,
  argMax(state, (occurred_at, first_offset)) as state,
  any(store)        as store,
  any(channel)      as channel,
  any(customer_id)  as customer_id,
  any(amount_minor) as amount_minor,
  min(occurred_at)  as created_at,
  max(occurred_at)  as updated_at
from {{ ref('stg_order_events') }}
group by order_id
```
Materialise both as **views**. Add dbt tests: `unique` and `not_null` on the keys, and
`accepted_values` on `state`.

**Why.** This is where data becomes **trustworthy**: duplicates gone, events folded into entities.
Every consumer starts here instead of re-solving duplicates in its own SQL. Views mean zero lag.

**Check.** For the duplicated events from M2: the raw table has 2 rows, `stg_order_events` has 1.
`count()` on `fct_order_latest` equals `SELECT count(*) FROM orders` in Postgres.

**Break-it lab.** Write the naive dashboard query `SELECT count() FROM shop.order_events WHERE state
= 'PAID'` and compare it with the same count over `fct_order_latest`. The difference is the bug you
would ship without a conformed layer.

### M5 · dbt: the marts layer (incremental table)

**Build.** `mart_orders_daily`: day × store × channel × state, with `orders` and `amount` columns.
```sql
{{ config(materialized='incremental', incremental_strategy='insert_overwrite',
          partition_by='toYYYYMMDD(day)', order_by='(day, store, channel, state)') }}

select toDate(created_at, '{{ var("business_time_zone") }}') as day,
       store, channel, state,
       count()           as orders,
       sum(amount_minor) as amt
from {{ ref('fct_order_latest') }}
{% if is_incremental() %}
where toDate(created_at, '{{ var("business_time_zone") }}') >= today() - {{ var('lookback_days') }}
{% endif %}
group by day, store, channel, state
```
Write one **business rule** as a macro, for example `paid_states()` = `('PAID', 'SERVED')`, and use it.
Add a singular test that compares the mart with `fct_order_latest`, day by day. Schedule it with a
container that runs `dbt build` every 5 minutes.

**Why.**
- A mart is **pre-aggregated for a consumer**: cheap to read for months of history.
- `insert_overwrite` **recomputes whole days** instead of merging rows, so you never need to know
  which rows changed.
- The lookback window catches orders whose state changed after their day ended.

Note the column is named `amt`, not `amount_minor`. In ClickHouse, an alias that shadows a column
name can silently change what the query means; avoid it.

**Check.** The mart's totals equal Postgres's totals. `dbt docs serve` shows the lineage graph: raw →
staging → conformed → mart.

**Break-it labs.**
- **Late change.** Move an order from 10 days ago to `REFUNDED`. The mart does not change, because
  it is outside the lookback; the reconciliation test fails. Fix it with `dbt build --full-refresh`.
  Now you know why lookback windows and reconciliation exist.
- **Time zone.** Set `business_time_zone` to `Africa/Johannesburg` (UTC+2) and rebuild. Orders made
  just after local midnight move to a different day. "Which day" is a business decision, not a
  technical one.

### M6 · The report API (the reportd role)

**Build.** A small service that reads report definitions from YAML and serves them as JSON:
```yaml
id: orders-overview
permission: report.orders
dataset: { layer: conformed, source: fct_order_latest, time_column: created_at, key_column: order_id }
measures:
  orders:     { sql: "count()" }
  paid_value: { sql: "sumIf(amount_minor, state IN ('PAID','SERVED'))" }
dimensions:
  store: { column: store }
filters:
  - { name: period, type: DATE_RANGE, default: 7d }
  - { name: store,  type: CHOICE, dimensions: [store], options: [north, south, east] }
blocks:
  - { id: headline, kind: kpi,   measures: [orders, paid_value] }
  - { id: recent,   kind: table, page_size: 50, columns: [order_id, state, amount_minor, created_at] }
```
Rules the engine must enforce:
1. **Validate at start-up**: every name a block uses exists in the dataset; a bad file stops the service.
2. **Every query is bounded** by a period (or, for a lookup, a required id).
3. **Filter values are bound as ClickHouse parameters** (`{store:String}`), never pasted into SQL.
4. **The caller's permission is checked** on every run. Simulate an identity service with a token
   → permissions map; TillPay asks adminapi `GetSession`.
5. It connects as a **read-only ClickHouse user** that can read `shop_conformed.*` and nothing else.
6. Table paging uses **keyset pagination** on `(time_column, key_column)`, not `OFFSET`.

Then add a second report, `order-lookup`, with a **required** `order_id` filter and a block that shows
one order's events from the raw table. You should not need to change the engine, only add YAML (or
add one generic block kind).

**Why.** Some reports show **individual rows** (one customer's order, one support case). Those need
the application's permissions, live data, bounded queries and an audit trail: things a BI tool is not
built for.

**Check.** A token without `report.orders` gets 403. A store filter value of `x' OR 1=1 --` returns
zero rows, not an error and not everything.

**Break-it lab: rule drift.** Change "paid" in the report YAML to include `CREATED`, without changing
the dbt macro. The API and the mart now disagree. Decide which component should own the rule. (In
TillPay this is an open hardening item: make dbt the single owner.)

### M7 · Superset on the marts

**Build.**
- A ClickHouse user `superset_ro` with `readonly = 2` and `SELECT ON shop_marts.*` **only**.
- Superset, with `clickhouse-connect` installed, connected as that user.
- A dataset on `mart_orders_daily` with metrics `SUM(orders)` and `SUM(amt)`, three charts and a
  dashboard.
- **Export** the dashboard (Settings → Export, or the API) and commit the YAML. Write an init script
  that imports it with overwrite on start-up, so **git is the source of truth**, not clicks.

**Why.** Superset is for **self-service** exploration of aggregates by people who do not write code.
Keeping it on the marts gives it pre-cleaned, cheap, non-personal data.

**Check.** Dashboard totals equal the report API's totals for the same period, within the mart's lag.

**Break-it labs.**
- In Superset, add a dataset on `shop_conformed.fct_order_latest`. ClickHouse refuses it: the
  database user enforces the boundary, not good intentions.
- Create a role that sees only store `north`, using Superset **row-level security**. Compare a user
  with that role against an admin. (Possible, but now authorization lives in two systems; know the
  cost.)
- Drop Superset's metadata database and restart. The dashboard comes back from git.

---

## 5 · Stretch goals (pick one or two)

| Goal | What it teaches |
|---|---|
| Generate 5 million events; time `fct_order_latest` as a view vs an **incremental table** | When views stop being good enough, and what you pay in lag |
| A nightly **reconciliation job** (Postgres totals vs mart) that alerts on a mismatch | Trust in numbers is operated, not assumed |
| **Snapshots** of a closed month that never change after publication | Finance and regulators need figures that do not move |
| **Schema evolution**: add a `discount_minor` field to the event | Old and new messages on one topic; versioned topics (`v1`, `v2`) |
| **Single sign-on** for Superset with Keycloak, roles from your identity service | How TillPay avoids a second user database |
| dbt **source freshness** check (`warn_after: 1 hour`) | Detecting a stalled feed before users do |

---

## 6 · How this maps to TillPay

| Mini project | TillPay |
|---|---|
| `orders` + `outbox` | the kernel's tables + `outbox.event_outbox` |
| publisher | the kernel's outbox publisher (at-least-once) |
| `shop.orders.v1` | `tillpay.txn.v1` |
| `order_events_queue` → `order_events_mv` → `order_events` | `txn_events_queue` → `txn_facts_mv` → `txn_facts` |
| `order_events_errors` | `txn_events_errors` |
| `stg_order_events`, `fct_order_latest` (views) | `stg_txn_events`, `fct_txn_latest` in `tillpay_conformed` |
| `mart_orders_daily` (incremental, insert_overwrite) | `mart_txn_daily` in `tillpay_marts` |
| `paid_states()` macro | `succeeded_states()`, `not_completed_states()` |
| dbt every 5 minutes | the `dbt-scheduler` service |
| report API + YAML | reportd + `report/reports/*.yaml` |
| token → permissions map | adminapi `GetSession` |
| read-only users per consumer | `report_ro`, `superset_ro`, `dbt` |
| Superset assets in git | `warehouse/superset/assets/` + `superset-init.sh` |

---

## 7 · Done means

You can answer these without notes:

1. Why does the event go through an outbox instead of straight to Kafka?
2. Where do duplicates come from, and which layer removes them?
3. What is stored in a ClickHouse materialized view, and what triggers it?
4. Why is the conformed layer views and the mart a table, and when would that change?
5. Why does the mart rebuild whole days, and what does the lookback window protect against?
6. Why does the report API exist when Superset exists, and what does each refuse to do?
7. If the mart is wrong, how do you rebuild it? If the raw table is wrong, how do you rebuild that?
8. Which database user can read what, and why?
