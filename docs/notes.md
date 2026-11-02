# Notes

## OLAP-405 · CDC (change data capture)

**The problem we saw (OLAP-404).** `simulate_changes.py` hard-deleted order 9 and its two
items. After the incremental sync they were still in Bronze. A watermark sync asks
"which rows have `updated_at > watermark`?", and a deleted row isn't there to answer.

**How log-based CDC catches it.**
- Postgres writes every change to its write-ahead log (WAL) before applying it. Inserts,
  updates *and deletes* all go through the WAL.
- With `wal_level = logical`, a replication slot decodes the WAL into a stream of row
  events: `insert`, `update` (before and after values), `delete` (the deleted key).
- A tool like Debezium reads the slot and publishes the events (often to Kafka). The
  warehouse applies them in order. A `delete` event becomes a tombstone in Bronze
  (e.g. `_deleted = true`), so downstream models know the row is gone.

**What it fixes, compared with our `updated_at` sync.**
- Hard deletes are captured.
- Every intermediate version is captured, not just the latest one at sync time
  (an order that went pending → paid → shipped between two syncs shows all three steps).
- No reliance on the app maintaining `updated_at`, and no late-commit problem: the log
  position (LSN) is the watermark, and it's assigned in commit order, so no lookback is needed.
- Light on the source: it reads the log instead of querying the tables.

**What it costs.**
- More infrastructure (Debezium, usually Kafka), plus Postgres config and privileges.
- A replication slot keeps WAL until the consumer reads it. If the consumer is down for
  days, the production database's disk fills up. That's an OLTP outage caused by analytics.
- It still needs an initial snapshot. Debezium takes one and then streams from that exact
  log position, which also fixes the "tables copied at different moments" gap in our snapshot.

**Without CDC, the usual options:** only soft deletes (an agreement with the app team),
periodic key comparison (the `ANTI JOIN` check from OLAP-404, costly on big tables),
periodic full refresh, or a delete trigger that writes tombstones to an audit table.

**Replication vs transformation.**
- *Replication* (E4, Fivetran, Debezium): copy the source faithfully, with no business logic.
- *Transformation* (dbt, E5–E6): reshape that copy for analysis.
- Keeping them separate means a transformation bug is fixed by re-running dbt on Bronze,
  without re-extracting from production, and the replication code stays generic.

## OLAP-701 · Materializations: view, table, incremental

> The full explanation, with worked examples, is in
> `notes/materializations-and-incremental-models.md`. This section is the experiment log.

Experiment (steps in `tickets/OLAP-701.md`): `fct_orders` built three ways. Each round:
`simulate_changes` (5 new orders, 3 status changes) → `incremental_sync` → look at
`fct_orders` before and after `dbt run`. `fct_orders` was restored to `table` afterwards.

| | What DuckDB stores | After sync, before `dbt run` | What `dbt run` does |
|---|---|---|---|
| `view` | Only the SQL | **Already current** (1005 → 1010 rows) | Re-creates the view definition |
| `table` | The result rows | **Stale** (1010 while Bronze had 1015) | Rebuilds every row |
| `incremental` | The result rows | **Stale**, like a table | Processes only the changed rows (8 of 1020) and applies them |

### Round 1: view

- `fct_orders` was **fresh without any dbt run**. A view stores only the `SELECT`, and the
  database runs it whenever *anyone* queries the view: DuckDB, a BI tool, a Python script.
  dbt isn't involved at read time. `dbt run` only (re)creates the definition, so you need it
  when the SQL changes, not when the data changes.
- `mart_monthly_revenue` stayed **stale**. It's a table built from `fct_orders`, so it only
  changes when dbt rebuilds it. A view is only as fresh as its inputs, and a table is only as
  fresh as its last build.
- The cost is paid on every read: this view reads Silver views, which read Bronze, so the
  whole chain runs on each query. Cheap to build, expensive to query. That suits Silver
  (light renaming, read mostly by dbt), not Gold that dashboards hit all day.

### Round 2: table

- After the sync, `fct_orders` was **stale** until `dbt run`, then current again.
- The data is stored, so reads are fast and predictable, and nothing upstream runs at
  query time.
- The costs: storage, a full recompute on every run (0.1s at 1,000 rows; the reason
  incremental exists at a billion), and freshness that depends on when the orchestrator
  runs dbt.

### Round 3: incremental

```sql
{{ config(materialized='incremental', unique_key='order_id') }}

with orders as (
    select * from {{ ref('stg_orders') }}
    {% if is_incremental() %}
    where ingested_at > (select max(existing.ingested_at) from {{ this }} as existing)
    {% endif %}
),
-- ... rest of fct_orders unchanged, plus ingested_at in the final select
```

- `is_incremental()` is false on the first run and on `--full-refresh`. The filter
  disappears and it builds like a table. On later runs (target exists, not a full refresh)
  it's true: only rows newer than what's already in the target.
- With `unique_key`, dbt-duckdb used **delete + insert**: it deleted the 8 `order_id`s from
  the target and inserted the 8 fresh rows. Other adapters often use `merge`; without a
  `unique_key` it's append-only.
- Correctness check: the incremental result was identical to a full rebuild (`EXCEPT` both
  ways, 0 rows).
- **First run against the existing table failed** with `WHERE clause cannot contain
  aggregates`. The old table had no `ingested_at` column, so in the subquery
  `max(ingested_at)` silently bound to the **outer** query's `stg_orders.ingested_at` (SQL's
  correlated-subquery scoping), which turns the aggregate into an invalid `WHERE`.
  Qualifying the column (`existing.ingested_at`) turns it into a clear "not found" error.
  Fix: `dbt run --full-refresh -s fct_orders`. Any change to an incremental model's columns
  needs one.

### Round 4: breaking it

**a) Logic changes don't reach old rows.** With `is_revenue` changed to "shipped only", a
normal incremental run left **32 old paid orders still counted as revenue**, and the build
was green. Only rows that happened to change got the new logic. `--full-refresh` fixed it.
Any change to business logic in an incremental model needs a full refresh, and someone has
to remember to run it.

**b) Filter on `ingested_at`, not `updated_at`** (worked out, not yet run; the ticket has
the steps to see it). It's the late-commit problem from OLAP-403,
one layer up. A row whose `updated_at` is older than the newest row already in the fact, but
which reached Bronze late (the sync's lookback caught it), never passes an `updated_at`
filter. It's skipped forever. `ingested_at` is set by our own loader when the row lands and
only moves forward, so "newer than what I've processed" is exactly right. Rule: incremental
models filter on *when the warehouse received the row*, not on the source's business timestamp.

**c) Hard deletes.** Like Bronze, an incremental model only learns about rows that show up in
its filter. A row deleted upstream stays in the target until a full refresh.

So the three ways an incremental model quietly drifts from the truth are: logic changes,
late data, and deletes. That's why teams schedule a periodic `--full-refresh` (e.g. weekly)
as a safety net.

### Which to choose (industry default)

- `view` for staging, `table` for facts and marts.
- `incremental` only when a full rebuild is too slow or too expensive. Typically large,
  append-mostly event facts (clicks, logs, order lines at scale).
- `fct_orders` here stays a `table`: 1,000 rows rebuild in a fraction of a second.

### dbt's `table` is not a materialized view

- A database **materialized view** is an object the database owns. It stores both the
  definition and the result, and the *database* refreshes it: manually with `REFRESH
  MATERIALIZED VIEW` in Postgres, automatically or incrementally in Snowflake dynamic
  tables, ClickHouse and BigQuery.
- A dbt `table` is a plain table. The database doesn't know how it was built. dbt runs
  `CREATE TABLE ... AS SELECT` into a temp name and swaps it in. Freshness depends entirely
  on when the orchestrator runs dbt.
- So a dbt table won't refresh itself. In exchange, it works on any database, its logic lives
  in version-controlled SQL, and it's tested like everything else. (DuckDB has no
  materialized views. dbt offers a `materialized_view` materialization on adapters that do.)
