# Mini OLAP Pipeline — Work Breakdown

Source: [outline.md](outline.md). This file breaks the outline into epics and tickets that can be turned into Jira/Linear items later.

**Conventions**
- Ticket IDs: `OLAP-<epic><nn>` (e.g. `OLAP-301` = Epic 3, ticket 1).
- Estimates are focused hours. Total core scope ≈ 10.5h; stretch tickets are marked **(stretch)**.
- Every ticket has a **Learn** line: the concept you should be able to explain once it's done. That's the actual deliverable of this project. The code is how you get there.

---

## Design decisions (resolve before starting)

These are corrections or gaps in the outline. Each has a recommended default, so you can start right away.

| # | Issue in outline | Recommendation |
|---|---|---|
| D1 | **Grain conflict.** The "Product Performance" query reads `product_id` and `quantity` from `fct_orders`. But `fct_orders` is order-level (one row per order, `total_amount`), and an order has many products. | Add `fct_order_items` (grain: one row per order line). Keep `fct_orders` at order grain. Product queries use `fct_order_items`. |
| D2 | **Backward dependency in tests.** The example tests `stg_orders.customer_id → dim_customers`. That makes a Silver test depend on Gold. | Silver relationship tests point at Silver (`stg_users`). Gold relationship tests point at Gold dims. |
| D3 | **Campaign CSV is orphaned.** Nothing in the OLTP data references a campaign, so the CSV never reaches Gold. | Add a nullable `campaign_id` to `orders` (simple attribution). Build `dim_campaigns` + `mart_campaign_performance`. |
| D4 | **Bronze write strategy.** The outline says "upsert into Bronze". | Do the upsert as written (it's what the test steps assume). Optional stretch: an append-only variant, dedup in Silver (OLAP-406). |
| D5 | **`updated_at` doesn't update itself.** Postgres won't change it on `UPDATE` unless the app or a trigger does. | Add a `BEFORE UPDATE` trigger on every source table. |
| D6 | **Where the watermark lives.** | A `meta.sync_state` table in DuckDB (`table_name`, `last_synced_at`, `last_run_at`, `rows_loaded`). |
| D7 | **Extraction mechanism.** | Use DuckDB's `postgres` extension (`ATTACH`). Python orchestrates; SQL does the copy. No ORM. |
| D8 | **DuckDB is single-writer.** If a SQL client or Metabase holds the `.duckdb` file open, `dbt run` or ingestion will fail with a lock error. | Close clients before writes, or open them read-only. |

---

## Epic 0 — Concepts Primer (2h)

*Goal: build a mental model before writing code. Write your notes in `docs/notes.md`.*

**OLAP-001 · OLTP vs OLAP, ETL vs ELT, warehouse basics** — 1h
- Read: outline §1, Databricks medallion page, dbt intro.
- AC: `notes.md` answers in your own words: why OLAP exists alongside OLTP; row vs columnar storage; ETL vs ELT (and why this project is ELT); why analytics shouldn't run on the production DB.
- Learn: checklist items 1–3.

**OLAP-002 · Medallion architecture + dimensional modeling** — 1h
- Read: Kimball resources (star schema, grain, facts/dims, surrogate keys).
- AC: `notes.md` contains a hand-drawn (ASCII is fine) star schema for this project and the grain of each fact table, written as one sentence each.
- Learn: checklist items 7, 11.

---

## Epic 1 — Environment & Scaffolding (1h)

**OLAP-101 · Repo skeleton + git** — 10m
- Create the folder layout from outline §7, plus `docker-compose.yml`, `requirements.txt`, `.gitignore` (ignore `*.duckdb`, `.venv`, `dbt_project/target`, `logs`).
- AC: `git init`, first commit.

**OLAP-102 · Postgres via Docker** — 15m
- `docker-compose.yml` with `postgres:16`, a named volume, port 5432, credentials in `.env`.
- AC: `docker compose up -d` works and `psql` connects.

**OLAP-103 · Python env** — 10m
- `.venv`, `requirements.txt`: `duckdb`, `dbt-core>=1.10`, `dbt-duckdb`, `python-dotenv`, `faker`.
- AC: `python -c "import duckdb"` and `dbt --version` both work.

**OLAP-104 · DuckDB warehouse + schemas** — 10m
- `ingestion/setup_warehouse.py` creates `warehouse/analytics.duckdb` with schemas `bronze`, `silver`, `gold`, `meta`. Idempotent (`CREATE SCHEMA IF NOT EXISTS`).
- Learn: schemas are logical boundaries, not separate servers.

**OLAP-105 · dbt project init + profile** — 15m
- `dbt init` inside `dbt_project/`. Add a `profiles.yml` pointing at the DuckDB file. Configure `dbt_project.yml` so `silver/` models build into the `silver` schema and `gold/` models into `gold`.
- Watch out: by default dbt *prefixes* custom schemas (`main_silver`). Override with a `generate_schema_name` macro. This is the one macro worth writing.
- AC: `dbt debug` passes.

---

## Epic 2 — OLTP Source System (1h)

*You know this side already. Do it quickly, but get the change-tracking columns right, because ingestion depends on them.*

**OLAP-201 · Source DDL** — 25m
- `postgres-source/ecommerce.sql`: `users`, `products`, `orders`, `order_items`, `payments`.
- Every table gets `created_at`, `updated_at timestamptz not null default now()`, `deleted_at timestamptz null`.
- `orders.campaign_id int null` (D3). `orders.status` from a fixed set (`pending`, `paid`, `shipped`, `cancelled`). `payments.method` likewise.
- `BEFORE UPDATE` trigger that sets `updated_at = now()` (D5).
- AC: the script runs cleanly against a fresh DB.

**OLAP-202 · Seed data generator** — 25m
- `postgres-source/seed.py` using Faker: ~200 users, ~50 products, ~1,000 orders across 6+ months, 1–5 items per order, one payment per paid order. About 30% of orders get a `campaign_id` between 1 and 3.
- Spread `created_at` over past months so the monthly mart has something to show.
- AC: row counts are as expected and FKs are valid.

**OLAP-203 · Change simulator** — 10m
- `postgres-source/simulate_changes.py`: inserts N new users/orders, updates M existing rows, soft-deletes one user, and **hard-deletes** one row.
- Purpose: feeds the incremental-sync tests (OLAP-404) and shows the hard-delete blind spot.

---

## Epic 3 — CSV Ingestion (30m + stretch)

**OLAP-301 · Campaign CSV + loader** — 20m
- `data/marketing_campaigns.csv` (3 rows from the outline).
- `ingestion/ingest_csv.py`: loads with `read_csv(..., all_varchar=true)` into `bronze.raw_campaigns`, adding `source_file` and `ingested_at`.
- Why all_varchar: Bronze keeps values as the source sent them. Type casting is Silver's job.
- AC: re-running doesn't duplicate rows. Pick a strategy (delete-then-insert by `source_file`, or skip already-loaded files) and write it down.
- Learn: batch file ingestion, source metadata, idempotency.

**OLAP-302 · Malformed record handling** — 10m
- Add `data/marketing_campaigns_bad.csv` with a non-numeric budget, a bad date, and a row with a missing column.
- Decide: does Bronze accept it raw (recommended, then Silver flags or filters it) or is it rejected at ingest? Try `ignore_errors` / `store_rejects` in DuckDB to see the rejected rows.
- AC: you can say where bad rows end up and why.
- Learn: schema inference vs explicit schema, data validation, quarantine.

---

## Epic 4 — PostgreSQL Ingestion (1.5h + stretch)

**OLAP-401 · Initial snapshot** — 30m
- `ingestion/initial_snapshot.py`: `ATTACH` Postgres (read-only). For each of the 5 tables, `CREATE OR REPLACE TABLE bronze.raw_<t> AS SELECT *, now() AS ingested_at, 'postgres.<t>' AS source FROM pg.<t>`.
- Set `meta.sync_state.last_synced_at = max(updated_at)` for each table.
- AC: Bronze row counts match Postgres.
- Learn: what a snapshot is, and why the warehouse keeps its own copy.

**OLAP-402 · Sync state table** — 10m
- `meta.sync_state` (D6), created in `setup_warehouse.py`.
- AC: after a snapshot, one row per source table.

**OLAP-403 · Incremental sync** — 35m
- `ingestion/incremental_sync.py`, per table:
  1. Read the watermark.
  2. `SELECT … FROM pg.<t> WHERE updated_at > :watermark`.
  3. Upsert into `bronze.raw_<t>` on the primary key (`INSERT OR REPLACE`, or `ON CONFLICT DO UPDATE`). This requires a PK on the Bronze table, so add it in the snapshot.
  4. Set the watermark to the `max(updated_at)` *of the fetched rows* (not `now()`). Do this in the same transaction as the upsert.
- Log rows fetched per table.
- Learn: watermarks, upserts, and why you take the watermark from the data and not the clock (clock skew, rows committed late).

**OLAP-404 · Verify sync scenarios** — 15m
- Run `simulate_changes.py`, then `incremental_sync.py`, then check:
  - [ ] the new user is in Bronze
  - [ ] the updated user shows its new values in Bronze
  - [ ] the soft-deleted user has `deleted_at` set in Bronze
  - [ ] the **hard-deleted row is still in Bronze** ← expected, and this is the point of the exercise
- AC: write up the results in `notes.md`.
- Learn: soft vs hard deletes, and why CDC exists (WAL / logical replication, Debezium as concepts only).

**OLAP-405 · CDC write-up** — 10m (reading)
- In `notes.md`: how log-based CDC would catch the hard delete; replication vs transformation.

**OLAP-406 · (stretch) Append-only Bronze variant** — 30m
- Copy the sync to append every fetched version instead of upserting. Silver then picks the latest per key with `qualify row_number() over (partition by id order by updated_at desc) = 1`.
- Learn: why many teams keep Bronze append-only (history, replayability).

---

## Epic 5 — Silver Layer with dbt (1h)

**OLAP-501 · Declare sources** — 10m
- `models/sources.yml`: source `bronze` with all 6 raw tables. Add `loaded_at_field: ingested_at` and a freshness check.
- AC: `dbt source freshness` runs.

**OLAP-502 · Staging models (OLTP)** — 35m
- `stg_users`, `stg_products`, `stg_orders`, `stg_order_items`, `stg_payments`. All materialized as `view`.
- Rules: consistent snake_case names; `users.id → customer_id`, `orders.id → order_id`, etc. Explicit casts. Lowercase and trim categoricals. Add an `is_deleted` boolean from `deleted_at`.
- Decide and document: do soft-deleted rows get filtered here or passed through with the flag? Recommendation: pass them through with the flag and let Gold decide.
- Learn: `source()`, view materialization, what "standardize" means in practice.

**OLAP-503 · Staging model (campaigns)** — 10m
- `stg_campaigns`: `try_cast` budget and date. Rows that fail to cast are flagged as `is_valid = false` (or filtered, matching your OLAP-302 decision).
- Learn: handling malformed records downstream of ingestion.

**OLAP-504 · Silver tests** — 10m
- `models/silver/_silver.yml`: `unique` + `not_null` on every PK; `relationships` from `stg_orders.customer_id → stg_users` and from `stg_order_items.order_id → stg_orders` (D2); `accepted_values` on `orders.status` and `payments.method`.
- AC: `dbt build --select silver` is green.

---

## Epic 6 — Gold Layer: Dimensional Model (1.5h)

**OLAP-601 · `dim_date`** — 10m
- Generate a date range with DuckDB `range()` / `generate_series` (no packages). Columns: `date_day`, `year`, `quarter`, `month`, `month_name`, `day_of_week`, `is_weekend`.
- Learn: why a date dimension exists when `date_trunc` already does.

**OLAP-602 · `dim_customers`, `dim_products`, `dim_campaigns`** — 25m
- Materialized as `table`. Add a surrogate key (`md5(cast(natural_key as varchar))` or `row_number()`) alongside the natural key.
- `dim_customers` can denormalize attributes such as `first_order_date` and `lifetime_orders`.
- Learn: surrogate vs natural keys, denormalization.

**OLAP-603 · `fct_orders`** — 15m
- Grain: **one row per order.** Columns: keys for customer, date, and campaign; `status`; `item_count`; `order_total`.
- Exclude or flag cancelled and soft-deleted orders. Document the business definition of "revenue".
- Learn: grain, measures vs dimensions.

**OLAP-604 · `fct_order_items`** (D1) — 15m
- Grain: **one row per order line.** Columns: order, product, customer, and date keys; `quantity`; `unit_price`; `line_total`.
- AC: `sum(line_total)` reconciles with `fct_orders.order_total`. Make this a dbt test (see OLAP-607).

**OLAP-605 · `fct_payments`** — 10m
- Grain: one row per payment.

**OLAP-606 · Marts** — 10m
- `mart_monthly_revenue` (month, orders, revenue, AOV, distinct customers), `table`.
- `mart_campaign_performance` (campaign, budget, attributed orders and revenue, revenue/budget).
- Learn: what makes a mart different from a fact table (curated for one business question or domain).

**OLAP-607 · Gold tests + reconciliation** — 5m
- PK/FK tests from facts to dims. Add one singular test in `tests/`: order totals reconcile between `fct_orders` and `fct_order_items`.
- AC: `dbt build` (full project) is green.

**OLAP-608 · Docs + DAG** — 5m
- Add model and column descriptions for Gold. Run `dbt docs generate && dbt docs serve`.
- AC: you can walk the lineage graph from `raw_orders` to `mart_monthly_revenue` and explain each hop.

---

## Epic 7 — Materializations & Analytics (1.5h)

**OLAP-701 · Materialization experiment** — 30m
- Take `fct_orders` and build it three ways: `view`, `table`, `incremental` (`unique_key='order_id'`, filtered on `is_incremental()` by `updated_at`).
- Run `simulate_changes → incremental_sync → dbt run` and compare what happens with each.
- In `notes.md`: view vs table vs dbt incremental vs native materialized view (concept only), and why dbt's `table` is not a materialized view.
- Learn: checklist item 10.

**OLAP-702 · Analytical queries** — 30m
- `dbt_project/analyses/` (or `sql/analytics/`): monthly revenue, top 10 customers (join `dim_customers` for names), product performance (from `fct_order_items`, D1), campaign ROI, plus one window-function query (month-over-month growth or running total).
- AC: each query has a comment stating the question it answers.

**OLAP-703 · OLTP vs Gold comparison** — 20m
- Write the monthly revenue query once against the raw Postgres tables (joins, status filters, deleted handling) and once against `mart_monthly_revenue`.
- In `notes.md`: compare complexity, who can write each one, and the load on production.
- Learn: why BI reads Gold.

**OLAP-704 · (stretch) Metabase** — 45m
- Run Metabase in Docker with the community DuckDB driver (or point it at a Postgres copy of Gold if the driver is a hassle). Build one dashboard from the marts.
- Watch the file lock (D8).

---

## Epic 8 — Wrap-up (30m)

**OLAP-801 · One-command pipeline** — 15m
- `run_pipeline.ps1` (or a Makefile): setup → ingest CSV → snapshot or incremental → `dbt build`.
- AC: deleting `analytics.duckdb` and running the script rebuilds everything.

**OLAP-802 · README + learning checklist** — 15m
- README: architecture diagram, how to run, the decisions D1–D8.
- Go through outline §13 and answer each item in `notes.md` without looking things up. Anything you can't answer becomes a follow-up.

---

## Epic 9 — Stretch: Related Topics Not in the Outline

*Do these only if the core is done early, or the following weekend. They are listed in order of value.*

**OLAP-901 · Slowly Changing Dimensions (SCD Type 2) with `dbt snapshot`** — 40m
- Snapshot `stg_users` with the `timestamp` strategy on `updated_at`. Build `dim_customers_history` with `valid_from` / `valid_to`.
- Test it: change a user's city, sync, run `dbt snapshot`, and see two rows for that user.
- Bonus question: should a 2025 order show the customer's *old* city or the *current* one? (point-in-time join)
- Learn: how a warehouse keeps history that OLTP overwrites. Also, name collision: a dbt "snapshot" is not the same thing as the "initial snapshot" in E4.

**OLAP-902 · Schema drift exercise** — 20m
- Run a "migration" on Postgres: add a column to `users`, and rename one in `products`. Re-run the pipeline and see what breaks and where.
- Learn: why an innocent OLTP migration can break analytics. This leads to data contracts, and it's the most useful lesson here for your day job.

**OLAP-903 · Columnar storage + Parquet** — 25m
- `COPY bronze.raw_orders TO 'data/lake/orders.parquet'`. Query the file directly with DuckDB. Compare file size to CSV.
- Run `EXPLAIN ANALYZE` on a mart query in DuckDB and the equivalent query in Postgres.
- Learn: row vs columnar storage in practice, and what a "data lake" file actually is.

**OLAP-904 · Backfill / full refresh** — 15m
- Corrupt the watermark on purpose, recover with a full re-snapshot, then run `dbt build --full-refresh`.
- Learn: idempotency and re-runnability, which are daily concerns in real data work.

---

## Changes applied to outline.md (2026-11-01)

| Type | Change | Why |
|---|---|---|
| Modify | §9 product query reads from `fct_order_items`; §3 and §7 add `fct_order_items.sql` | Fixes the grain bug (D1) |
| Modify | §8 example test: `relationships` to `ref('stg_users')` | Silver shouldn't depend on Gold (D2) |
| Modify | §2B: add `orders.campaign_id`; §3 Gold adds `dim_campaigns`, `mart_campaign_performance` | Gives the CSV a purpose downstream (D3) |
| Modify | §2B: mention the `updated_at` trigger and taking the watermark from `max(updated_at)` | Common real-world mistakes (D5, OLAP-403) |
| Modify | §10: interleave the 2h of reading with the build, e.g. read about CDC right before OLAP-404 | Concepts stick better next to the code that uses them |
| Add | §4: SCD Type 2 (concept required; OLAP-901 optional) | Core Kimball concept; the outline covers surrogate keys but not why they exist (history) |
| Add | §2: idempotency and schema drift as concepts | The two things that break pipelines most often |
| Add | §7: `docker-compose.yml`, `requirements.txt`, `profiles.yml`, `analyses/`, `_silver.yml` / `_gold.yml`, `meta.sync_state` | Missing from the structure but required |
| Add | §1: one paragraph on row vs columnar storage | It's the actual reason OLAP engines are fast; the outline never says it |
| Move | Metabase → explicitly stretch | Driver setup can eat an hour with little learning value |
| Keep | §12 "What to skip" as is | Scoping is good; resist adding Airflow and similar |

---

## Dependency graph

```text
E0 (concepts) ──────────────────────────────┐
E1 setup ─┬─> E2 OLTP source ─> E4 PG ingest ─┐
          └─> E3 CSV ingest ──────────────────┼─> E5 Silver ─> E6 Gold ─> E7 Analytics ─> E8 Wrap-up
                                              │
                         OLAP-203 ─> OLAP-404 ┘   (OLAP-701 also reuses 203/403)
```

## Suggested weekend split

| Session | Tickets | ~Time |
|---|---|---:|
| Sat AM | E0, E1 | 3h |
| Sat PM | E2, E3, E4 | 3h |
| Sun AM | E5, E6 | 2.5h |
| Sun PM | E7, E8 | 2h |
