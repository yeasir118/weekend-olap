# Mini OLAP pipeline: Postgres + CSV → DuckDB → dbt

A weekend-sized analytics pipeline built to learn the OLAP side of data: how data leaves an
OLTP application database, lands in a warehouse, and gets shaped into a star schema that
answers business questions.

An e-commerce app (Postgres) and a marketing team (CSV files) feed a DuckDB warehouse with
Bronze, Silver and Gold layers. dbt builds and tests the Silver and Gold layers.

## Architecture

```text
  SOURCES                    INGESTION (Python + DuckDB SQL)       WAREHOUSE: warehouse/analytics.duckdb
  ───────                    ───────────────────────────────       ──────────────────────────────────────

  Postgres 17 (Docker)  ──▶  initial_snapshot.py   (first load)  ─┐
  users, products,           incremental_sync.py   (watermark +   │
  orders, order_items,        5-min lookback, upsert by id)       │    bronze.raw_*        as delivered
  payments                                                        ├──▶ meta.sync_state     watermarks
                                                                  │          │
  data/marketing_        ──▶  ingest_csv.py  (one delivery per    │          │ dbt (views)
  campaigns_*.csv             file; bad lines quarantined)       ─┘          ▼
                                                                       silver.stg_*        renamed, cast, UTC,
                                                                             │             validated
                                                                             │ dbt (tables)
                                                                             ▼
                                                                       gold.dim_*          star schema
                                                                       gold.fct_*
                                                                       gold.mart_*         one table per question
                                                                             │
                                                                             ▼
                                                                       analyses/, BI tools
```

**Stack:** Postgres 17 · DuckDB 2.0 · dbt Core 1.12 + dbt-duckdb 1.11 · Python 3.13 · Docker ·
PowerShell (Windows).

## Repository layout

```text
docker-compose.yaml        Postgres source database (credentials and port from .env)
run_pipeline.ps1           One command: source up → ingest → dbt build
postgres-source/           Plays "the app": schema, seed data, live-change simulator
ingestion/                 Postgres and CSV → Bronze; warehouse setup; shared config
data/                      Marketing's CSV deliveries (marketing_campaigns_YYYY-MM-DD.csv)
warehouse/                 analytics.duckdb (gitignored build artifact)
dbt_project/
  models/sources.yml       Bronze tables as dbt sources, with freshness checks
  models/silver/           stg_* views: one per source table, 1:1, no joins
  models/gold/dimensions/  dim_date, dim_customers, dim_products, dim_campaigns
  models/gold/facts/       fct_orders, fct_order_items, fct_payments
  models/gold/marts/       mart_monthly_revenue, mart_campaign_performance
  macros/                  to_reporting_date(): UTC timestamp → business date
  tests/                   Singular tests (reconciliation, campaign validity)
  analyses/                Analytical queries: compiled by dbt, never built
sql/oltp_vs_gold/          The same question asked of Postgres and of Gold
docs/                      Outline, backlog, tickets, notes
```

## Getting started

**Prerequisites:** Docker Desktop and Python 3.13. (dbt doesn't support older Pythons.)

```powershell
# 1. Python environment
py -3.13 -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 2. Configuration: copy and set a password
Copy-Item .env.example .env

# 3. Start Postgres and create the source schema
docker compose up -d --wait
Get-Content postgres-source\ecommerce.sql -Raw |
    docker exec -i oltp-db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1'

# 4. Fill it with ~10 months of history (deterministic; truncates the tables first)
.venv\Scripts\python postgres-source\seed.py

# 5. Build the warehouse
.\run_pipeline.ps1
```

`run_pipeline.ps1` ends with `dbt build`, which should show `PASS=92 WARN=1 ERROR=0`. The
warning is intentional: two campaigns in marketing's CSV have unreadable values, and they're
reported, not hidden.

## Day-to-day

```powershell
.venv\Scripts\python postgres-source\simulate_changes.py   # the app does things: inserts, updates, deletes
.\run_pipeline.ps1                                          # incremental sync + dbt build
.\run_pipeline.ps1 -FullRefresh                             # re-snapshot + rebuild everything
```

`run_pipeline.ps1` decides between an initial snapshot and an incremental sync from the
warehouse's own state: no watermarks in `meta.sync_state` means a snapshot. Deleting
`warehouse\analytics.duckdb` and re-running rebuilds everything from the sources.

**Close other connections to `analytics.duckdb` first.** DuckDB allows one writer at a time
(D8). Open it read-only for exploring:

```python
duckdb.connect("warehouse/analytics.duckdb", read_only=True)
```

### Exploring

Run these from `dbt_project\`:

```powershell
..\.venv\Scripts\dbt show --select top_customers            # run an analysis, preview the result
..\.venv\Scripts\dbt docs generate
..\.venv\Scripts\dbt docs serve --port 8085                 # model docs + lineage graph
```

## The data model

| Layer | Objects | Materialization | Purpose |
|---|---|---|---|
| Bronze | `raw_users`, `raw_products`, `raw_orders`, `raw_order_items`, `raw_payments`, `raw_campaigns`, `raw_campaigns_rejects` | Tables, loaded by Python | Source data as delivered, plus `ingested_at` |
| Silver | `stg_*`, one per source table | Views | Renamed, typed, UTC timestamps, `is_deleted` flags; campaigns validated |
| Gold: dimensions | `dim_date`, `dim_customers`, `dim_products`, `dim_campaigns` | Tables | Who, what, when. md5 surrogate keys, SCD Type 1 |
| Gold: facts | `fct_orders` (one row per order), `fct_order_items` (one per order line), `fct_payments` (one per payment) | Tables | Measurable events at a declared grain |
| Gold: marts | `mart_monthly_revenue`, `mart_campaign_performance` | Tables | One business question each, ready to read |

**Revenue definition** (in `fct_orders`, reused everywhere): an order counts when its status is
`paid` or `shipped` and it isn't soft-deleted. Sum the `revenue` column, not `order_total`.

**Business dates** are in `Asia/Dhaka` (dbt var `reporting_timezone`). Silver keeps UTC, and
Gold converts with the `to_reporting_date()` macro.

## Design decisions

The original outline had gaps; these were settled before building (from `docs/backlog.md`):

| # | Issue | Decision |
|---|---|---|
| D1 | The product query read `product_id` from an order-level fact: wrong grain | Added `fct_order_items` (one row per order line). Product questions use it |
| D2 | A Silver test pointed at a Gold dimension, so the DAG ran backwards | Silver tests point at Silver, Gold tests at Gold |
| D3 | The campaign CSV connected to nothing | Nullable `orders.campaign_id`, plus `dim_campaigns` and `mart_campaign_performance` |
| D4 | How to write Bronze | Upsert by primary key (`INSERT OR REPLACE`); append-only was an optional variant |
| D5 | Postgres doesn't maintain `updated_at` by itself | `BEFORE UPDATE` trigger on every source table |
| D6 | Where watermarks live | `meta.sync_state` in the warehouse, committed in the same transaction as the data |
| D7 | How to extract | DuckDB's `postgres` extension (`ATTACH ... READ_ONLY`): Python orchestrates, SQL copies, no rows pass through Python |
| D8 | DuckDB is single-writer | Close clients before writes; open read-only for exploring |

Decisions made while building:
- **Watermark = `max(updated_at)` of the loaded data, minus a 5-minute lookback.** Postgres
  stamps `now()` at transaction *start*, so late commits would otherwise be skipped.
- **Unchanged re-reads are dropped before the upsert**, so `ingested_at` only moves when a row
  really changed.
- **Campaign CSVs:** every delivery is kept (append with `source_file`). Silver uses
  `try_cast` with validation flags, because human-made files contain things like "TBD".
  Structurally broken lines are quarantined; a mostly broken file is rejected whole.
- **Surrogate keys are `md5(natural key)`:** stable across rebuilds, with no lookup needed.
- **Soft-deleted rows are kept and flagged**, never filtered, so their history still joins.
- **Campaign relationship tests only warn:** a campaign's CSV can arrive after its first orders.
- **Gold is all `table`; no incremental models.** At this size a full rebuild takes seconds.
  The experiment is in `docs/notes/materializations-and-incremental-models.md`.

## Known limitations

- **Hard deletes are invisible to incremental sync.** A row deleted in Postgres stays in Bronze
  until `-FullRefresh`. Log-based CDC (Debezium) would fix this (`docs/notes.md`, OLAP-405).
- **Dimensions are SCD Type 1:** a customer who moves shows their new city on all past orders.
  Type 2 history is the optional OLAP-901.
- **Campaign attribution is last touch** (the `campaign_id` on the order), and revenue isn't profit.
- **A transaction open longer than the 5-minute lookback can still be missed.**

## Further reading

- `docs/outline.md`: the original project brief
- `docs/backlog.md`: epics, tickets, decisions
- `docs/notes.md`: notes per ticket (CDC, materialization experiment, OLTP vs Gold)
- `docs/notes/dimensional-modeling.md`: Kimball, facts vs dimensions, reading list
- `docs/notes/materializations-and-incremental-models.md`: view, table, incremental, and late data
