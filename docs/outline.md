
# Weekend Project: Building a Mini OLAP Data Pipeline

## Objective

Build a miniature, production-inspired analytical data architecture in one weekend.

The goal is to understand the complete data lifecycle:

- OLTP data ingestion
- CSV/file ingestion
- Database mirroring and incremental synchronization
- Bronze, Silver, Gold (Medallion Architecture)
- Data transformation with dbt
- Dimensional modeling
- Views, tables, and materialized views
- Data marts
- Data quality testing
- Analytical SQL queries

**Estimated time:** 9–11 hours

**Tech stack:**
- PostgreSQL — OLTP source database
- Python — ingestion scripts
- DuckDB — analytical database / local warehouse
- dbt Core — data transformation and modeling
- Docker — runs PostgreSQL locally
- Metabase (stretch) — BI and visualization. Only attempt once the core pipeline works; driver setup can take an hour.

---

# 1. Architecture Overview

Build the following architecture:

```text
                    DATA SOURCES
                         |
             +-----------+-----------+
             |                       |
       PostgreSQL                  CSV Files
        (OLTP)                 (External Data)
             |                       |
             |                       |
     Initial Snapshot           File Ingestion
     Incremental Sync                 |
             |                       |
             +-----------+-----------+
                         |
                         v
                  BRONZE LAYER
                   (Raw Data)
                         |
                         v
                  SILVER LAYER
               (Cleaned / Standardized)
                         |
                         v
                   GOLD LAYER
             (Business-Ready Models)
                         |
              +----------+----------+
              |                     |
          Data Marts           Analytics SQL
              |                     |
              +----------+----------+
                         |
                         v
                   BI / Dashboard
```

All three layers will be implemented as separate schemas in DuckDB.

For example:

- `bronze`
- `silver`
- `gold`

These are logical separation boundaries, not separate database servers.

A fourth schema, `meta`, holds pipeline bookkeeping such as sync watermarks.

## Why a separate analytical engine? Row vs columnar storage

PostgreSQL stores data **row by row**. That's ideal for OLTP: fetching or updating one order touches one place on disk.

DuckDB stores data **column by column**. An analytical query like "sum revenue by month" reads only the `order_date` and `total_amount` columns across millions of rows, skips every other column, and compresses well because values in one column look alike.

Same SQL, but opposite physical layouts, each optimized for a different access pattern. This is the core technical reason OLAP systems exist alongside OLTP systems.

**Note:** DuckDB allows only one writer at a time. Close SQL clients (or open them read-only) before running ingestion or dbt, or you'll hit file-lock errors.

---

# 2. Data Ingestion

Learn two different ingestion approaches.

## A. CSV Ingestion

Scenario: The marketing department provides campaign information through CSV files.

Example:

`marketing_campaigns_2026-11-01.csv`

Each delivery is named `marketing_campaigns_YYYY-MM-DD.csv` (the delivery date). Without a date, each new file would overwrite the last and history would be lost.

```csv
campaign_id,campaign_name,channel,budget,start_date
1,Winter Sale,Email,5000,2026-01-01
2,New Year Ads,Social,8000,2026-01-05
3,Spring Launch,Search,6000,2026-02-01
```

Tasks:

1. Read CSV files using Python or DuckDB.
2. Load the records into the Bronze layer.
3. Preserve original source columns and values.
4. Track ingestion metadata:
   - `source_file`
   - `delivered_on` (parsed from the file name)
   - `ingested_at`
5. Make ingestion **idempotent**: running it twice on the same file must not duplicate rows. Reject files that don't follow the naming convention.
6. Create a second file with malformed rows (non-numeric budget, bad date, missing column) and decide where bad rows end up.
7. Transform the data into Silver models using dbt.

Tip: load CSV columns into Bronze as text (`all_varchar=true` in DuckDB). Type casting is Silver's job, so a bad value doesn't fail the whole load.

Concepts to understand:

- File ingestion
- Batch ingestion
- Schema inference
- Source metadata
- Idempotency
- Data validation
- Handling malformed records

## B. PostgreSQL (OLTP) Ingestion

Scenario: An e-commerce application uses PostgreSQL as its production database.

Create the following source tables:

- `users`
- `products`
- `orders`
- `order_items`
- `payments`

Add a nullable `campaign_id` column to `orders`. This links orders to the marketing CSV so campaign data reaches the Gold layer.

Every source table gets `created_at`, `updated_at` and `deleted_at` columns.

Tasks:

### 1. Initial Snapshot

Copy existing PostgreSQL tables into DuckDB.

For example:

```text
PostgreSQL.users
       |
       v
DuckDB.bronze.raw_users
```

Understand:

- What is a snapshot?
- Why do analytical systems maintain copies of operational data?
- Why shouldn't analytical queries run directly against production databases?

### 2. Incremental Synchronization

Simulate incremental ingestion.

Use the `updated_at` column on your PostgreSQL source tables.

**Important:** PostgreSQL does not update `updated_at` automatically. Add a `BEFORE UPDATE` trigger that sets `updated_at = now()`. Without it, incremental sync silently misses changed rows.

Store watermarks in a `meta.sync_state` table in DuckDB (one row per source table).

Implement the following logic:

```text
1. Read the last successful sync timestamp.
2. Fetch records updated after that timestamp.
3. Upsert the records into Bronze.
4. Update the sync timestamp to max(updated_at) of the fetched rows.
```

Set the new watermark from the data (`max(updated_at)`), not from the clock (`now()`). Clock skew and late-committing transactions can otherwise cause missed rows. Update the watermark in the same transaction as the upsert.

Test this by:

- Inserting a new user.
- Updating an existing user.
- Soft-deleting a user (setting `deleted_at`).
- Hard-deleting a row.
- Running the ingestion script again.
- Verifying that Bronze contains the latest records.
- Observing that the hard-deleted row is **still in Bronze**. This is expected, and it is the point of the exercise.

**Important:** An `updated_at` watermark alone cannot detect hard deletes. Real production systems often use CDC (Change Data Capture) to capture inserts, updates, and deletes.

For this weekend, understand CDC conceptually. Do not implement a full CDC pipeline.

### Concepts to understand

- Initial snapshot
- Incremental loading
- Upsert
- Watermarks
- CDC
- Soft deletes vs hard deletes
- Data replication vs data transformation

### 3. Schema Drift

Simulate a routine application migration: add a column to `users` and rename a column in `products`. Re-run the pipeline and observe what breaks, and where.

This shows why OLTP schema changes are a top cause of broken analytics pipelines, and why teams introduce **data contracts** between application and data teams.

---

# 3. Medallion Architecture

Implement three logical layers.

## Bronze — Raw Layer

Purpose: Preserve source data with minimal transformation.

Example tables:

```text
bronze.raw_users
bronze.raw_products
bronze.raw_orders
bronze.raw_order_items
bronze.raw_payments
bronze.raw_campaigns
```

Responsibilities:

- Store ingested source data.
- Preserve source-level structure.
- Track ingestion timestamps.
- Track source information.
- Avoid unnecessary transformations.

## Silver — Cleaned Layer

Purpose: Convert raw data into reliable, standardized datasets.

Example models:

```text
silver.stg_users
silver.stg_products
silver.stg_orders
silver.stg_order_items
silver.stg_payments
silver.stg_campaigns
```

Responsibilities:

- Standardize column names.
- Cast data types.
- Handle null values.
- Remove duplicates.
- Validate records.
- Standardize timestamps and formats.
- Apply basic business rules.

Use dbt to create these models.

## Gold — Business Layer

Purpose: Prepare datasets for analytical queries and business consumption.

Example models:

```text
gold.dim_customers
gold.dim_products
gold.dim_campaigns
gold.dim_date
gold.fct_orders
gold.fct_order_items
gold.fct_payments
gold.mart_monthly_revenue
gold.mart_campaign_performance
```

Responsibilities:

- Create fact tables.
- Create dimension tables.
- Apply business definitions.
- Aggregate data.
- Build reusable analytical datasets.
- Prepare data for BI tools.

---

# 4. Dimensional Modeling

Understand the difference between transactional and analytical data models.

## OLTP Tables

```text
users
products
orders
order_items
payments
```

These tables are designed around application operations.

## OLAP Tables

```text
dim_customers
dim_products
dim_campaigns
dim_date

fct_orders          -- grain: one row per order
fct_order_items     -- grain: one row per order line
fct_payments        -- grain: one row per payment

mart_monthly_revenue
mart_campaign_performance
```

Learn the following concepts:

- Fact tables
- Dimension tables
- Star schema
- Measures and dimensions
- Grain of a fact table
- Denormalization
- Surrogate keys
- Slowly Changing Dimensions (SCD Type 1 vs Type 2)

Understand why analytical systems often use a different schema from operational systems.

## Grain

Always state a fact table's grain in one sentence before writing it. An order contains many products, so product-level measures (`product_id`, `quantity`) cannot live in an order-grain table. That's why `fct_orders` and `fct_order_items` are separate.

## Slowly Changing Dimensions

OLTP overwrites values: when a customer moves city, the old city is gone. A warehouse often needs history: "revenue by the customer's city *at the time of the order*."

- **SCD Type 1:** overwrite, so no history is kept.
- **SCD Type 2:** keep one row per version, with `valid_from` / `valid_to`.

This is the reason surrogate keys exist: one customer can have several dimension rows, so the natural key is no longer unique.

dbt implements SCD Type 2 with `dbt snapshot`. Note that a dbt snapshot is unrelated to the "initial snapshot" in section 2. Implementing it is stretch work, but understanding it isn't optional.

---

# 5. Views, Tables and Materialized Views

Understand how transformed data is stored and queried.

| Concept | Description |
|---|---|
| View | Saved SQL query; results computed when queried |
| Table | Persisted dataset |
| Materialized View | Persisted/precomputed query results |
| Data Mart | Curated dataset for a business domain |

For this project:

- Bronze: Physical tables.
- Silver: Mostly views.
- Gold: Tables.
- Monthly revenue: Persisted analytical table.

Also understand the difference between:

- dbt table materialization
- dbt view materialization
- dbt incremental materialization
- Database-native materialized views

Note: A dbt table materialization and a native materialized view are not the same thing.

DuckDB's dbt adapter supports table, view, incremental and ephemeral materializations.

Do not spend time implementing native materialized views this weekend. Understand the concept first.

---

# 6. dbt Transformations

Use dbt to transform Bronze data into Silver and Gold datasets.

Understand:

- dbt models
- Sources
- Ref dependencies
- Model materializations
- DAG (Directed Acyclic Graph)
- Tests
- Documentation
- Incremental models

Example transformation flow:

```text
Bronze
  |
  v
stg_orders
stg_users
stg_products
  |
  v
dim_customers
dim_products
  |
  v
fct_orders
fct_order_items
  |
  v
mart_monthly_revenue
```

dbt prefixes custom schemas by default (`main_silver` instead of `silver`). Override the `generate_schema_name` macro so models land in `silver` and `gold`. This is the one macro worth writing this weekend.

Use `source()` to reference raw Bronze tables and `ref()` to reference other dbt models.

Example:

```sql
select
    order_id,
    customer_id,
    cast(order_date as date) as order_date,
    total_amount
from {{ source('bronze', 'raw_orders') }}
```

Example downstream model:

```sql
select
    customer_id,
    count(*) as total_orders,
    sum(total_amount) as total_revenue
from {{ ref('stg_orders') }}
group by customer_id
```

Explore the dbt DAG and generated documentation.

---

# 7. Final Project Structure

Organize the project as follows:

```text
olap-weekend/
│
├── docker-compose.yml
├── requirements.txt
├── .env
├── run_pipeline.ps1
│
├── postgres-source/
│   ├── ecommerce.sql
│   ├── seed.py
│   └── simulate_changes.py
│
├── data/
│   └── marketing_campaigns_YYYY-MM-DD.csv   -- one file per delivery
│
├── ingestion/
│   ├── setup_warehouse.py
│   ├── ingest_csv.py
│   ├── initial_snapshot.py
│   └── incremental_sync.py
│
├── warehouse/
│   └── analytics.duckdb          -- schemas: bronze, silver, gold, meta
│
└── dbt_project/
    ├── dbt_project.yml
    ├── profiles.yml
    ├── macros/
    │   └── generate_schema_name.sql
    ├── analyses/                  -- analytical queries
    ├── tests/                     -- singular tests
    └── models/
        │
        ├── sources.yml
        │
        ├── silver/
        │   ├── _silver.yml
        │   ├── stg_users.sql
        │   ├── stg_products.sql
        │   ├── stg_orders.sql
        │   ├── stg_order_items.sql
        │   ├── stg_payments.sql
        │   └── stg_campaigns.sql
        │
        └── gold/
            │
            ├── _gold.yml
            │
            ├── dimensions/
            │   ├── dim_customers.sql
            │   ├── dim_products.sql
            │   ├── dim_campaigns.sql
            │   └── dim_date.sql
            │
            ├── facts/
            │   ├── fct_orders.sql
            │   ├── fct_order_items.sql
            │   └── fct_payments.sql
            │
            └── marts/
                ├── mart_monthly_revenue.sql
                └── mart_campaign_performance.sql
```

---

# 8. Data Quality Testing

Use dbt tests to validate your models.

Implement:

- `unique` — Ensure primary identifiers are unique.
- `not_null` — Ensure required fields aren't null.
- `relationships` — Validate foreign-key relationships.
- `accepted_values` — Validate allowed categorical values.

Example:

```yaml
models:
  - name: stg_orders
    columns:
      - name: order_id
        data_tests:
          - unique
          - not_null

      - name: customer_id
        data_tests:
          - not_null
          - relationships:
              arguments:
                to: ref('stg_users')
                field: customer_id
```

Test relationships **within the same layer**: Silver tests reference Silver models, Gold tests reference Gold models. A Silver test that references a Gold model creates a backward dependency in the DAG.

Also add one singular test (a SQL file in `tests/`) that reconciles totals: the sum of `fct_order_items.line_total` per order must equal `fct_orders.order_total`.

Also understand why data quality testing is important in analytical pipelines.

---

# 9. Analytical Queries

Write queries against your Gold layer.

Examples:

### Monthly Revenue

```sql
SELECT
    date_trunc('month', order_date) AS month,
    SUM(order_total) AS revenue
FROM gold.fct_orders
GROUP BY 1
ORDER BY 1;
```

### Top 10 Customers

```sql
SELECT
    c.customer_name,
    SUM(f.order_total) AS total_revenue
FROM gold.fct_orders f
JOIN gold.dim_customers c USING (customer_key)
GROUP BY c.customer_name
ORDER BY total_revenue DESC
LIMIT 10;
```

### Product Performance

Product-level measures come from the order-line fact table, not `fct_orders`:

```sql
SELECT
    p.product_name,
    COUNT(DISTINCT f.order_id) AS total_orders,
    SUM(f.quantity) AS units_sold
FROM gold.fct_order_items f
JOIN gold.dim_products p USING (product_key)
GROUP BY p.product_name
ORDER BY units_sold DESC;
```

### Month-over-Month Growth (window function)

```sql
SELECT
    month,
    revenue,
    revenue / LAG(revenue) OVER (ORDER BY month) - 1 AS mom_growth
FROM gold.mart_monthly_revenue
ORDER BY month;
```

Understand why these analytical queries are executed against Gold models instead of directly against the OLTP tables.

To make this concrete, write the monthly revenue query once against the raw PostgreSQL tables (joins, status filters, deleted-row handling) and compare it to the Gold version.

---

# 10. Weekend Schedule

Target: 9–11 hours.

Mix the reading into the build rather than doing it all up front. Read each concept right before the step that uses it.

| Session | Activity | Read first | Time |
|---|---|---|---:|
| Sat AM | OLTP vs OLAP, ETL/ELT, row vs columnar | §1 | 45 minutes |
| Sat AM | PostgreSQL + DuckDB + dbt setup, source schema and seed data | — | 1.5 hours |
| Sat PM | CSV ingestion into Bronze | Idempotency, schema inference | 30 minutes |
| Sat PM | PostgreSQL snapshot + incremental sync + delete tests | Watermarks, CDC | 1.5 hours |
| Sun AM | dbt Silver transformations + tests | dbt intro, medallion | 1 hour |
| Sun AM | Gold models, marts and DAG | Kimball: grain, star schema, SCD | 1.5 hours |
| Sun PM | Materialization experiment (view vs table vs incremental) | dbt materializations | 30 minutes |
| Sun PM | Analytical queries, schema drift exercise, wrap-up | — | 1.5 hours |

**Stretch (if time remains):** SCD Type 2 with `dbt snapshot`, Parquet export, Metabase dashboard.

---

# 11. Learning Resources

Keep the resource list short.

1. Medallion Architecture:
   https://www.databricks.com/glossary/medallion-architecture

2. DuckDB PostgreSQL Extension:
   https://duckdb.org/docs/stable/extensions/postgres

3. DuckDB Documentation:
   https://duckdb.org/docs/stable/

4. dbt Introduction:
   https://docs.getdbt.com/docs/introduction

5. dbt Materializations:
   https://docs.getdbt.com/docs/build/materializations

6. Kimball Dimensional Modeling:
   https://www.kimballgroup.com/data-warehouse-business-intelligence-resources/

7. dbt Fundamentals:
   https://learn.getdbt.com/

---

# 12. What to Skip This Weekend

Deliberately avoid:

- Apache Spark
- Kafka
- Flink
- Airflow
- Airbyte
- Fivetran
- Snowflake internals
- Databricks internals
- Complex CDC implementations
- Advanced dbt macros and Jinja
- Streaming architectures
- Advanced lakehouse architectures

Build the ingestion scripts yourself instead of hiding the process behind tools such as Airbyte or Fivetran.

Introduce orchestration and dedicated ingestion tools later, once you understand the fundamentals.

---

# 13. Final Learning Checklist

By Sunday evening, you should be able to explain:

- [ ] Why OLAP exists alongside OLTP.
- [ ] How row and columnar storage differ, and why it matters.
- [ ] What a data warehouse does.
- [ ] How data moves from an OLTP database into a warehouse.
- [ ] How CSV ingestion differs from database ingestion.
- [ ] What initial snapshots and incremental loading mean.
- [ ] What CDC is and why it matters.
- [ ] Why ingestion must be idempotent.
- [ ] How an OLTP schema migration can break the pipeline.
- [ ] Why Bronze, Silver and Gold layers exist.
- [ ] How dbt transforms data.
- [ ] How dbt builds a dependency DAG.
- [ ] How views differ from tables and materialized views.
- [ ] What fact and dimension tables are.
- [ ] What the grain of a fact table is, and how to pick it.
- [ ] How SCD Type 2 preserves history that OLTP overwrites.
- [ ] What data marts are.
- [ ] How data quality tests protect downstream analytics.
- [ ] Why BI tools consume Gold-layer datasets.

## Final Goal

By the end of this weekend, you should have built a working miniature OLAP pipeline:

**PostgreSQL + CSV → Ingestion → Bronze → Silver → Gold → Data Marts → Analytics**

You don't need to master the entire data engineering ecosystem yet.

You just need to understand the architecture, the responsibilities of each component, and how data flows through the system.
