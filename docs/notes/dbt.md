# dbt in short

What dbt is, how a project is laid out, the concepts you use every day, the commands, and one
worked example that uses all of it. Written against dbt Core 1.12 (with the dbt-duckdb
adapter), but almost everything applies to any warehouse.

---

## 1. What dbt is

**dbt (data build tool) is the "T" in ELT.** Data is first *extracted* from sources and
*loaded* into the warehouse as-is, by other tools: here, Python scripts; elsewhere, Fivetran,
Airbyte or Debezium. dbt then *transforms* it, **inside the warehouse, using SQL**.

What dbt does:
- You write each transformation as a **`SELECT` statement in a `.sql` file**: a *model*.
- dbt works out the **order** to run them in, from the references between them (the DAG).
- It **wraps each `SELECT`** in the right DDL (`CREATE VIEW`, `CREATE TABLE AS`, `MERGE`, ...)
  and sends it to the warehouse.
- It **tests** the results, **documents** them, and draws the **lineage graph**.

What dbt does **not** do:
- Move data between systems. It has no extractors; source and target are the same warehouse.
- Process data itself. The warehouse does all the computation; dbt only generates and sends SQL.
- Schedule itself. Something else runs `dbt build`: cron, a script, Airflow, Dagster, dbt Cloud.

**Why teams use it instead of a folder of SQL scripts:** dependency order is automatic, the
same model runs in dev and prod against different schemas, every model is tested and
documented the same way, and it's all plain text in git, reviewed like application code.

**Flavours:**
- **dbt Core:** the free, open-source CLI. This project uses it.
- **dbt Cloud / the dbt platform:** the hosted product (IDE, scheduler, CI, hosted docs), built on the same project format.
- **Adapters:** plugins that teach dbt one warehouse's SQL dialect: `dbt-duckdb`,
  `dbt-postgres`, `dbt-snowflake`, `dbt-bigquery`, `dbt-databricks`, ...

---

## 2. The mental model

```
   .sql files with Jinja            dbt compile              dbt run / build
  ┌──────────────────────┐       ┌────────────────┐       ┌──────────────────────┐
  │ select ...           │       │ select ...     │       │ create table         │
  │ from {{ ref('x') }}  │  ──▶  │ from gold.x    │  ──▶  │   gold.y as (        │ ──▶ warehouse
  │ {% if ... %}         │       │ (plain SQL)    │       │   select ... )       │
  └──────────────────────┘       └────────────────┘       └──────────────────────┘
        you write                 target/compiled/          target/run/ + logs
```

1. **Parse:** read every file and build the **DAG** (directed acyclic graph) from `ref()` and
   `source()` calls. The graph decides the run order and what `--select` can pick.
2. **Compile:** render the Jinja into plain SQL, resolving `ref('x')` to a real table name.
3. **Execute:** wrap each compiled `SELECT` according to its **materialization** and run it, in
   DAG order, in parallel where the graph allows (`threads`).

Everything else (tests, docs, snapshots, seeds) is another kind of node in the same graph.

---

## 3. Project structure

```text
dbt_project/
├── dbt_project.yml        WHAT to build: project name, folders, default configs, vars
├── profiles.yml           WHERE to build it: connection per target (dev, prod). Usually in ~/.dbt/
├── packages.yml           Third-party dbt packages (dbt_utils, ...); installed by `dbt deps`
├── models/                Models (.sql) and their properties (.yml)
│   ├── sources.yml          Declares the raw tables dbt reads but doesn't build
│   ├── staging/             (silver/ here) 1:1 with sources: rename, cast, clean. Views
│   └── marts/               (gold/ here) facts, dimensions, marts. Tables
├── macros/                Reusable Jinja functions (SQL snippets, overrides of dbt behaviour)
├── tests/                 Singular tests: SQL queries that must return 0 rows
├── seeds/                 Small static CSVs loaded as tables by `dbt seed`
├── snapshots/             SCD Type 2 history of mutable tables (`dbt snapshot`)
├── analyses/              SQL that's compiled (ref() works) but never built
├── target/                OUTPUT: compiled SQL, manifest.json, run results, docs site. Gitignored
├── logs/                  OUTPUT: dbt.log with every SQL statement run. Gitignored
└── dbt_packages/          OUTPUT: installed packages. Gitignored
```

### dbt_project.yml

```yaml
name: weekend_olap
profile: weekend_olap            # which profile in profiles.yml to use

models:
  weekend_olap:                  # this project's models...
    silver:                      # ...in models/silver/
      +schema: silver
      +materialized: view
    gold:
      +schema: gold
      +materialized: table

vars:
  reporting_timezone: Asia/Dhaka # read in SQL with {{ var('reporting_timezone') }}
```

The `+` marks a config, as opposed to a sub-folder name.

### profiles.yml

```yaml
weekend_olap:
  target: dev                    # default target
  outputs:
    dev:
      type: duckdb
      path: ../warehouse/analytics.duckdb
      schema: main
      threads: 4
    # prod:
    #   type: snowflake
    #   ...                      # same project, different warehouse: dbt build --target prod
```

Profiles hold credentials, so they normally live outside the repo in `~/.dbt/profiles.yml`.
This project keeps it in the repo because a DuckDB profile is just a file path.

### Where configuration can be set

From most specific (wins) to least:
1. `{{ config(...) }}` at the top of the model file
2. The model's entry in a `.yml` properties file (`config:` block)
3. `dbt_project.yml`, by folder

---

## 4. Core concepts

### Models

A model is one `SELECT` in one `.sql` file. The file name is the model name, and the
resulting table or view takes the same name. No `CREATE`, no `INSERT`: dbt writes those.

### ref() and source(): how the DAG is built

```sql
select * from {{ source('bronze', 'raw_orders') }}   -- a table dbt didn't build (declared in sources.yml)
select * from {{ ref('stg_orders') }}                -- another model
```

- Both compile to the real table name for the current target (`bronze.raw_orders`,
  `silver.stg_orders`).
- Both **add an edge to the DAG**. That's how dbt knows `stg_orders` must be built before anything
  that refs it.
- **Never hard-code table names.** A hard-coded `from silver.stg_orders` works once, but it's
  invisible to the DAG (wrong run order, missing from lineage) and doesn't switch with the target.

### Sources

```yaml
sources:
  - name: bronze
    schema: bronze
    config:
      loaded_at_field: ingested_at           # when each row arrived
      freshness:
        warn_after:  {count: 24, period: hour}
        error_after: {count: 48, period: hour}
    tables:
      - name: raw_orders
```

Sources document where raw data comes from, put it in the lineage graph, and let
`dbt source freshness` alert when ingestion has silently stopped.

### Materializations

| Materialization | What dbt creates | Use for |
|---|---|---|
| `view` | `CREATE VIEW`: stores the query, computed on every read | Staging; light models |
| `table` | `CREATE TABLE AS`: computes and stores all rows each run | Facts, dims, marts at normal scale |
| `incremental` | First run: a table. Later runs: only new or changed rows, merged in | Very large, append-mostly facts |
| `ephemeral` | Nothing: inlined as a CTE into the models that ref it | Small reusable steps you never query directly |
| `materialized_view` | A database materialized view (adapters that support one) | Database-managed refresh |

Incremental in one snippet:

```sql
{{ config(materialized='incremental', unique_key='order_id') }}
select * from {{ ref('stg_orders') }}
{% if is_incremental() %}
where ingested_at > (select max(ingested_at) from {{ this }})   -- only rows newer than what's there
{% endif %}
```

`is_incremental()` is false on the first run and with `--full-refresh`. Logic changes, late
data and deletes don't reach old rows without a full refresh. See
`materializations-and-incremental-models.md` for the full story.

### Jinja, macros and vars

Every model is a Jinja template rendered before it reaches the warehouse.

```sql
{% set statuses = ['paid', 'shipped'] %}                        -- a variable
where status in ({% for s in statuses %}'{{ s }}'{% if not loop.last %}, {% endif %}{% endfor %})
```

- **`{{ ... }}`** prints a value; **`{% ... %}`** is logic (if, for, set); **`{# ... #}`** is a comment.
- **Macros** are Jinja functions in `macros/`, called like `{{ to_reporting_date('ordered_at') }}`.
  Use them for logic repeated across models.
- **vars** are project-level settings (`{{ var('reporting_timezone') }}`), overridable per run:
  `dbt build --vars '{reporting_timezone: UTC}'`.
- **Built-in context:** `{{ this }}` (the current model's table), `{{ target.name }}` (dev or
  prod), `{{ env_var('X') }}` (environment variables, e.g. for secrets in profiles).
- **Overriding dbt itself:** a macro with a built-in name replaces dbt's behaviour. This project's
  `generate_schema_name` makes models land in `silver`, not dbt's default `main_silver`.

Keep Jinja light. SQL that needs a debugger to read is worse than a little repetition.

### Tests

**Generic tests:** declared in YAML, one per column property:

```yaml
models:
  - name: fct_orders
    columns:
      - name: order_id
        data_tests: [unique, not_null]
      - name: customer_key
        data_tests:
          - relationships:
              arguments:
                to: ref('dim_customers')
                field: customer_key
      - name: order_status
        data_tests:
          - accepted_values:
              arguments:
                values: [pending, paid, shipped, cancelled]
              config:
                severity: warn           # report, don't fail the build
```

The four built-ins are `unique`, `not_null`, `relationships` and `accepted_values`. Packages
(`dbt_utils`, `dbt_expectations`) add many more, and you can write your own as a macro.

**Singular tests:** a `.sql` file in `tests/` that selects the *bad* rows. 0 rows = pass.

```sql
-- tests/assert_order_totals_reconcile.sql
select order_id from {{ ref('fct_orders') }} o
join (select order_id, sum(line_total) t from {{ ref('fct_order_items') }} group by 1) l using (order_id)
where o.order_total <> l.t
```

**Unit tests** (dbt 1.8+): test a model's *logic* on fixed, made-up input rows, without real data:

```yaml
unit_tests:
  - name: cancelled_orders_are_not_revenue
    model: fct_orders
    given:
      - input: ref('stg_orders')
        rows:
          - {order_id: 1, order_status: paid,      order_total: 10}
          - {order_id: 2, order_status: cancelled, order_total: 5}
    expect:
      rows:
        - {order_id: 1, revenue: 10}
        - {order_id: 2, revenue: 0}
```

Data tests check **the data**: did today's load break an assumption? Unit tests check **the
code**: does my CASE expression do what I meant? Both matter.

Useful test configs: `severity: warn`, `error_if` / `warn_if` thresholds (e.g. `">10"`), `where:`
to test a subset, and `store_failures: true` to save failing rows to a table for inspection.

### Seeds

Small, static, version-controlled CSVs: country codes, a status mapping, a holiday list.
`dbt seed` loads them as tables; models use them with `ref('country_regions')`. Not for real
data loads; that's ingestion's job.

### Snapshots (SCD Type 2)

OLTP overwrites: when a customer moves, the old city is gone. A snapshot keeps every version:

```yaml
# snapshots/customers.yml (dbt 1.9+ YAML style)
snapshots:
  - name: customers_snapshot
    relation: ref('stg_users')
    config:
      schema: snapshots
      unique_key: customer_id
      strategy: timestamp        # a row changed when updated_at moved ('check' compares columns instead)
      updated_at: updated_at
```

Each `dbt snapshot` run compares the current rows with the snapshot table, closes changed rows
(`dbt_valid_to` = now) and inserts the new version (`dbt_valid_from` = now, `dbt_valid_to` =
null). It can only capture changes that exist when it runs, so run it on a schedule. Not to be
confused with an ingestion "initial snapshot", which is a full copy of a source.

### Analyses

SQL files in `analyses/` are compiled (so `ref()` works) but never built. Good for reports and
ad-hoc questions that should stay in sync with model names. Run with `dbt show -s <name>`, or
take the compiled SQL from `target/compiled/`.

### Documentation

`description:` on models and columns in YAML, plus `dbt docs generate` and `dbt docs serve`,
gives a browsable site: every model, its columns and types, its tests, its source and compiled
SQL, and the lineage graph. For longer text, `{% docs name %}...{% enddocs %}` blocks in `.md`
files are reusable with `description: '{{ doc("name") }}'`.

### Less frequent, worth knowing

- **Packages:** `packages.yml` + `dbt deps`. `dbt_utils` is nearly universal
  (`generate_surrogate_key`, `date_spine`, extra tests).
- **Hooks:** `pre-hook` / `post-hook` SQL per model, `on-run-start` / `on-run-end` per run
  (grants, audit logging).
- **Model contracts:** `contract: {enforced: true}` with declared column types. The build fails if
  the model's output doesn't match: a guarantee to downstream consumers.
- **Exposures:** declare the dashboards and apps that use your models, so they appear in lineage.
- **Tags:** `+tags: ['nightly']` to group models for selection (`-s tag:nightly`).

---

## 5. Commands

Run from the project folder (where `dbt_project.yml` is).

| Command | What it does |
|---|---|
| `dbt init <name>` | Create a new project skeleton |
| `dbt debug` | Check the setup: profile found, connection works. First stop when things won't connect |
| `dbt deps` | Install packages from `packages.yml` into `dbt_packages/` |
| `dbt parse` | Parse the project and write `manifest.json`; catches YAML/Jinja errors fast, runs nothing |
| `dbt compile` | Render Jinja to plain SQL in `target/compiled/`. Runs nothing. "What *will* dbt run?" |
| `dbt run` | Build models (views, tables, incremental merges) in DAG order. No tests |
| `dbt test` | Run data tests and unit tests |
| `dbt seed` | Load CSVs from `seeds/` into tables |
| `dbt snapshot` | Run snapshots: record changes as SCD Type 2 rows |
| `dbt build` | seed + run + snapshot + test, in DAG order; each node is tested right after it's built, and anything downstream of a failure is **skipped**. The default command |
| `dbt show -s <node>` | Run a model or analysis and print a preview (`--limit N`); writes nothing |
| `dbt ls` | List what a selection matches, without running it |
| `dbt source freshness` | Check sources against their `warn_after` / `error_after` |
| `dbt docs generate` | Build the docs data (`manifest.json` + `catalog.json`, read from the warehouse) |
| `dbt docs serve --port N` | Serve the docs site and lineage graph locally |
| `dbt retry` | Re-run only what failed or was skipped in the previous invocation |
| `dbt clean` | Delete `target/` and `dbt_packages/` (the `clean-targets`) |
| `dbt run-operation <macro>` | Run a macro on its own (admin tasks: grants, cleanup) |
| `dbt clone` | Copy relations from another environment's state (zero-copy where supported); for CI |

### Common flags

| Flag | Purpose |
|---|---|
| `-s` / `--select` | What to run (syntax below) |
| `--exclude` | What to leave out of the selection |
| `--full-refresh` | Rebuild incremental models (and seeds) from scratch |
| `--target prod` | Use another output from `profiles.yml` |
| `--vars '{key: value}'` | Override project vars for this run |
| `--threads N` | Parallelism |
| `--fail-fast` / `-x` | Stop at the first failure |
| `--resource-type model` | Filter `ls` and `build` by node type |

### Selection syntax

| Selector | Picks |
|---|---|
| `fct_orders` | One node |
| `+fct_orders` | It and everything **upstream** (its inputs, recursively) |
| `fct_orders+` | It and everything **downstream** |
| `+fct_orders+` | Both directions |
| `1+fct_orders` | It and one level upstream only |
| `@fct_orders` | Downstream, plus everything those need upstream (a safe "rebuild after changing this") |
| `gold.marts` / `path:models/gold/marts` | A folder |
| `source:bronze.raw_orders+` | Everything fed by a source table |
| `tag:nightly` | Nodes with a tag |
| `test_type:unit` | Only unit tests (or `data`) |
| `state:modified+` | What changed vs a saved manifest, plus downstream (`--state path`): "slim CI" |
| `a b` / `a,b` | Space = union; comma = intersection |

---

## 6. A complete example

A shop with customers and orders, from raw tables to a tested mart, using every concept above.
The raw tables `raw.customers(id, name, country_code, updated_at, loaded_at)` and
`raw.orders(id, customer_id, status, amount, created_at, loaded_at)` are loaded by an ingestion
tool.

### The files

```text
shop/
├── dbt_project.yml
├── profiles.yml
├── seeds/country_regions.csv
├── macros/to_local_date.sql
├── models/
│   ├── sources.yml
│   ├── staging/stg_customers.sql
│   ├── staging/stg_orders.sql
│   ├── staging/_staging.yml
│   ├── marts/dim_customers.sql
│   ├── marts/fct_orders.sql
│   ├── marts/mart_revenue_by_region.sql
│   └── marts/_marts.yml
├── snapshots/customers_snapshot.yml
├── tests/assert_revenue_not_negative.sql
└── analyses/top_regions.sql
```

**`dbt_project.yml`**
```yaml
name: shop
profile: shop
models:
  shop:
    staging: {+schema: staging, +materialized: view}
    marts:   {+schema: marts,   +materialized: table}
vars:
  local_timezone: Asia/Dhaka
```

**`seeds/country_regions.csv`**: a static lookup, version-controlled
```csv
country_code,region
BD,South Asia
IN,South Asia
US,North America
```

**`macros/to_local_date.sql`**: one definition of "a business day"
```sql
{% macro to_local_date(ts) %}
    cast(({{ ts }} at time zone 'UTC') at time zone '{{ var("local_timezone") }}' as date)
{% endmacro %}
```

**`models/sources.yml`**
```yaml
sources:
  - name: raw
    schema: raw
    config:
      loaded_at_field: loaded_at
      freshness: {warn_after: {count: 24, period: hour}}
    tables:
      - name: customers
      - name: orders
```

**`models/staging/stg_orders.sql`**: 1:1 with the source: rename, cast, nothing else
```sql
select
    id                          as order_id,
    customer_id,
    lower(status)               as order_status,
    cast(amount as decimal(12,2)) as order_total,
    created_at                  as ordered_at
from {{ source('raw', 'orders') }}
```
(`stg_customers.sql` is the same idea: `id as customer_id`, `name`, `country_code`, `updated_at`.)

**`models/marts/dim_customers.sql`**: joins a seed
```sql
select
    c.customer_id,
    c.name,
    c.country_code,
    coalesce(r.region, 'Unknown') as region
from {{ ref('stg_customers') }} c
left join {{ ref('country_regions') }} r using (country_code)
```

**`models/marts/fct_orders.sql`**: grain: one row per order; a macro and a business rule
```sql
{% set revenue_statuses = ['paid', 'shipped'] %}

select
    order_id,
    customer_id,
    {{ to_local_date('ordered_at') }}                         as order_date,
    order_status,
    order_total,
    case when order_status in ('{{ revenue_statuses | join("', '") }}')
         then order_total else 0 end                           as revenue
from {{ ref('stg_orders') }}
```

**`models/marts/mart_revenue_by_region.sql`**: one question, ready to read
```sql
select d.region, date_trunc('month', f.order_date) as month, sum(f.revenue) as revenue
from {{ ref('fct_orders') }} f
join {{ ref('dim_customers') }} d using (customer_id)
group by all
```

**`models/marts/_marts.yml`**: docs, data tests and a unit test
```yaml
models:
  - name: fct_orders
    description: One row per order. Sum `revenue`, not `order_total`.
    columns:
      - name: order_id
        data_tests: [unique, not_null]
      - name: customer_id
        data_tests:
          - relationships:
              arguments: {to: ref('dim_customers'), field: customer_id}
      - name: order_status
        data_tests:
          - accepted_values:
              arguments: {values: [pending, paid, shipped, cancelled]}

unit_tests:
  - name: only_paid_and_shipped_count_as_revenue
    model: fct_orders
    given:
      - input: ref('stg_orders')
        rows:
          - {order_id: 1, order_status: paid,      order_total: 10, ordered_at: 2026-01-01 10:00:00}
          - {order_id: 2, order_status: cancelled, order_total: 5,  ordered_at: 2026-01-01 10:00:00}
    expect:
      rows:
        - {order_id: 1, revenue: 10}
        - {order_id: 2, revenue: 0}
```

**`tests/assert_revenue_not_negative.sql`**: a singular test returns the bad rows
```sql
select * from {{ ref('fct_orders') }} where revenue < 0
```

**`snapshots/customers_snapshot.yml`**: keep every version of a customer
```yaml
snapshots:
  - name: customers_snapshot
    relation: ref('stg_customers')
    config:
      schema: snapshots
      unique_key: customer_id
      strategy: timestamp
      updated_at: updated_at
```

**`analyses/top_regions.sql`**: a report that follows model renames
```sql
-- Question: which regions brought the most revenue this year?
select region, sum(revenue) as revenue
from {{ ref('mart_revenue_by_region') }}
where month >= date_trunc('year', current_date)
group by region order by revenue desc
```

### The DAG dbt builds from those refs

```
seed: country_regions ─────────────────────┐
source: raw.customers → stg_customers ─────┼→ dim_customers ─┐
                              └→ customers_snapshot            ├→ mart_revenue_by_region → (analysis) top_regions
source: raw.orders    → stg_orders ──────────→ fct_orders ─────┘
                                                └ tests: unique, not_null, relationships, accepted_values,
                                                         assert_revenue_not_negative, unit test
```

### Running it

```powershell
dbt debug                          # connection OK?
dbt source freshness               # is ingestion still delivering?
dbt build                          # everything, in DAG order
```

What `dbt build` does. It follows the DAG, and nodes that don't depend on each other run in
parallel (`threads`), so the exact log order varies; what never varies is that a node runs
after everything it depends on.
1. **Seed** `country_regions` → table `country_regions`.
2. **Unit test** `only_paid_and_shipped_count_as_revenue`: runs *before* `fct_orders` is built,
   on the fake rows. If the CASE logic is wrong, `fct_orders` is never built.
3. **Build** `stg_customers`, `stg_orders` (views), then test them.
4. **Snapshot** `customers_snapshot` (first run: every customer, `dbt_valid_to` null).
5. **Build** `dim_customers`, `fct_orders` (tables), then run their tests. If `relationships`
   fails on `fct_orders`, the mart below is **skipped**, so no wrong numbers get published.
6. **Build** `mart_revenue_by_region`.
7. The analysis is compiled, not built.

What `fct_orders` compiles to (`dbt compile -s fct_orders`, in `target/compiled/`):

```sql
select
    order_id,
    customer_id,
    cast((ordered_at at time zone 'UTC') at time zone 'Asia/Dhaka' as date) as order_date,
    order_status,
    order_total,
    case when order_status in ('paid', 'shipped')
         then order_total else 0 end as revenue
from "shop"."main_staging"."stg_orders"
```

Note the schema: `main_staging`, not `staging`. By default dbt prefixes a custom schema with the
target's schema (`main` from `profiles.yml`), so two developers on the same warehouse don't
overwrite each other. A `generate_schema_name` macro overrides that; this repo uses one so models
land in plain `silver` and `gold`.

And what dbt actually sends (in `logs/dbt.log`): that `SELECT`, wrapped as
`create table "shop"."main_marts"."fct_orders__dbt_tmp" as (...)`, then a rename swap, so readers
never see a half-built table.

Day-to-day variations:

```powershell
dbt build -s +fct_orders           # changed fct_orders: rebuild it and its inputs
dbt build -s fct_orders+           # changed fct_orders: rebuild it and everything that uses it
dbt test  -s test_type:unit        # just the logic tests
dbt show  -s top_regions           # run the report
dbt build --vars '{local_timezone: UTC}'   # same project, different business day
dbt build --target prod            # same code, production warehouse
dbt docs generate; dbt docs serve --port 8085   # browse it all, with lineage
```

---

## 7. Conventions and pitfalls

**Conventions that most teams follow:**
- Layers: **staging** (`stg_`: 1:1 with sources, rename and cast only) → **intermediate**
  (`int_`: optional reusable joins) → **marts** (`dim_`, `fct_`, `mart_`). Here: silver = staging, gold = marts.
- Only staging models call `source()`; everything else uses `ref()`.
- Dependencies point one way: a staging model or test never refs a mart.
- Every model gets at least a primary key test (`unique` + `not_null`).
- One model per file, named after what one row is (`fct_orders`, not `orders_final_v2`).

**Pitfalls seen in this project:**
- **Running dbt from the wrong folder.** It can't find `dbt_project.yml`, or relative paths in
  `profiles.yml` point at the wrong place.
- **Hard-coded table names** instead of `ref()`: wrong build order, broken lineage.
- **Expecting YAML `columns:` to shape the table.** They're documentation and tests only; the
  `SELECT` defines the columns (unless a contract is enforced).
- **Incremental models drifting.** Logic changes need `--full-refresh`.
- **A view "doesn't need dbt run" for new data, a table does.** `dbt run` on a view only
  re-creates the definition.
- **Database locks** (DuckDB is single-writer): close other connections before `dbt build`.
- **`dbt show` adds its own `LIMIT`.** A query that ends in `limit 10` breaks under it.

---

## 8. Learn more

- [dbt docs](https://docs.getdbt.com/docs/introduction), and especially the
  [best-practice guide on project structure](https://docs.getdbt.com/best-practices/how-we-structure/1-guide-overview)
- [Node selection syntax](https://docs.getdbt.com/reference/node-selection/syntax)
- [dbt Fundamentals](https://learn.getdbt.com/) (free course)
- In this repo: `dbt_project/` for a real, working project, and `dimensional-modeling.md`
  for the Kimball side of what the marts are built on
