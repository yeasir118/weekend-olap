
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
- Metabase (optional) — BI and visualization

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

---

# 2. Data Ingestion

Learn two different ingestion approaches.

## A. CSV Ingestion

Scenario: The marketing department provides campaign information through CSV files.

Example:

`marketing_campaigns.csv`

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
   - `ingested_at`
5. Transform the data into Silver models using dbt.

Concepts to understand:

- File ingestion
- Batch ingestion
- Schema inference
- Source metadata
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

Add an `updated_at` column to your PostgreSQL source tables.

Implement the following logic:

```text
1. Read the last successful sync timestamp.
2. Fetch records updated after that timestamp.
3. Upsert the records into Bronze.
4. Update the sync timestamp.
```

Test this by:

- Inserting a new user.
- Updating an existing user.
- Running the ingestion script again.
- Verifying that Bronze contains the latest records.

Also introduce a `deleted_at` field to understand soft deletes.

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
gold.dim_date
gold.fct_orders
gold.fct_payments
gold.mart_monthly_revenue
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
dim_date

fct_orders
fct_payments

mart_monthly_revenue
```

Learn the following concepts:

- Fact tables
- Dimension tables
- Star schema
- Measures and dimensions
- Grain of a fact table
- Denormalization
- Surrogate keys

Understand why analytical systems often use a different schema from operational systems.

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
  |
  v
mart_monthly_revenue
```

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
├── postgres-source/
│   └── ecommerce.sql
│
├── data/
│   └── marketing_campaigns.csv
│
├── ingestion/
│   ├── initial_snapshot.py
│   └── incremental_sync.py
│
├── warehouse/
│   └── analytics.duckdb
│
└── dbt_project/
    └── models/
        │
        ├── sources.yml
        │
        ├── silver/
        │   ├── stg_users.sql
        │   ├── stg_products.sql
        │   ├── stg_orders.sql
        │   ├── stg_order_items.sql
        │   ├── stg_payments.sql
        │   └── stg_campaigns.sql
        │
        └── gold/
            │
            ├── dimensions/
            │   ├── dim_customers.sql
            │   ├── dim_products.sql
            │   └── dim_date.sql
            │
            ├── facts/
            │   ├── fct_orders.sql
            │   └── fct_payments.sql
            │
            └── marts/
                └── mart_monthly_revenue.sql
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
                to: ref('dim_customers')
                field: customer_id
```

Also understand why data quality testing is important in analytical pipelines.

---

# 9. Analytical Queries

Write queries against your Gold layer.

Examples:

### Monthly Revenue

```sql
SELECT
    date_trunc('month', order_date) AS month,
    SUM(total_amount) AS revenue
FROM gold.fct_orders
GROUP BY 1
ORDER BY 1;
```

### Top 10 Customers

```sql
SELECT
    customer_id,
    SUM(total_amount) AS total_revenue
FROM gold.fct_orders
GROUP BY customer_id
ORDER BY total_revenue DESC
LIMIT 10;
```

### Product Performance

```sql
SELECT
    product_id,
    COUNT(*) AS total_orders,
    SUM(quantity) AS units_sold
FROM gold.fct_orders
GROUP BY product_id
ORDER BY units_sold DESC;
```

Understand why these analytical queries are executed against Gold models instead of directly against the OLTP tables.

---

# 10. Weekend Schedule

Target: 9–11 hours.

| Activity | Time |
|---|---:|
| OLTP vs OLAP, ETL/ELT, warehouse concepts | 1 hour |
| Medallion architecture and dimensional modeling | 1 hour |
| PostgreSQL + DuckDB setup | 1 hour |
| CSV ingestion into Bronze | 30 minutes |
| PostgreSQL snapshot + incremental sync | 1.5 hours |
| dbt Silver transformations | 1 hour |
| Gold models, marts and DAG | 1.5 hours |
| Views, tables and materialization concepts | 30 minutes |
| dbt tests and analytical queries | 1 hour |

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
- [ ] What a data warehouse does.
- [ ] How data moves from an OLTP database into a warehouse.
- [ ] How CSV ingestion differs from database ingestion.
- [ ] What initial snapshots and incremental loading mean.
- [ ] What CDC is and why it matters.
- [ ] Why Bronze, Silver and Gold layers exist.
- [ ] How dbt transforms data.
- [ ] How dbt builds a dependency DAG.
- [ ] How views differ from tables and materialized views.
- [ ] What fact and dimension tables are.
- [ ] What data marts are.
- [ ] How data quality tests protect downstream analytics.
- [ ] Why BI tools consume Gold-layer datasets.

## Final Goal

By the end of this weekend, you should have built a working miniature OLAP pipeline:

**PostgreSQL + CSV → Ingestion → Bronze → Silver → Gold → Data Marts → Analytics**

You don't need to master the entire data engineering ecosystem yet.

You just need to understand the architecture, the responsibilities of each component, and how data flows through the system.
