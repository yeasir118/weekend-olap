# OLAP-701 · Materialization experiment

**Epic:** E7 — Materializations & Analytics
**Estimate:** 30–45 min · **Depends on:** OLAP-403, OLAP-603 · **Blocks:** nothing (learning ticket)

## Context

A dbt model is just a `SELECT`. The **materialization** decides what dbt turns it into inside
the warehouse, and that decides three things: how fresh the data is, how fast reads are, and
how much each `dbt run` costs.

| Materialization | What the warehouse stores | What `dbt run` does |
|---|---|---|
| `view` | Only the SQL | Re-creates the view definition |
| `table` | The result rows | Rebuilds every row |
| `incremental` | The result rows | Processes only new or changed rows and applies them to the existing table |

This is an **experiment, not a feature**. You'll switch `fct_orders` between the three, push
real changes through the pipeline, and watch what each one does. At the end, `fct_orders`
goes back to being a `table`.

**The one rule:** before every check, write down what you *expect* to see. Then run it. The
surprises are the learning.

## Setup

Run from the repo root in PowerShell. dbt commands run from `dbt_project\` as
`..\.venv\Scripts\dbt ...`. Close any Python shell holding the warehouse open before running
dbt or the sync (file lock).

```powershell
docker compose up -d                       # Postgres, if it's not running
```

Create a check script outside the repo, e.g. `$env:TEMP\check.py`:

```python
import duckdb
con = duckdb.connect("warehouse/analytics.duckdb", read_only=True)
q = lambda s: con.execute(s).fetchall()
print("fct_orders is a:", q("""select 'table' from duckdb_tables() where schema_name='gold' and table_name='fct_orders'
                                union all select 'view' from duckdb_views() where schema_name='gold' and view_name='fct_orders'"""))
print("fct_orders  :", q("select count(*), max(order_id), sum(revenue) from gold.fct_orders")[0])
print("bronze      :", q("select count(*), max(id) from bronze.raw_orders")[0])
print("mart revenue:", q("select sum(revenue) from gold.mart_monthly_revenue")[0])
```

**"The cycle"** = new activity in the source, landed in Bronze:

```powershell
.venv\Scripts\python postgres-source\simulate_changes.py
.venv\Scripts\python ingestion\incremental_sync.py
```

**"Check"** = `.venv\Scripts\python $env:TEMP\check.py`

## What to do

### Round 1: view

1. Add as the first line of `models/gold/facts/fct_orders.sql`:
   `{{ config(materialized='view') }}`. A `config()` in the model file overrides
   `+materialized: table` from `dbt_project.yml`.
2. `dbt run -s fct_orders`, then check.
3. Run the cycle. Check again **without running dbt**.

Answer:
- Did `fct_orders` change? Did `mart_monthly_revenue` change? Why is one fresh and the other not?
- What runs when *anyone* (not just dbt) does `select * from gold.fct_orders`?

### Round 2: table

1. Remove the config line. `dbt run -s fct_orders`, then check. Is it a table now?
2. Run the cycle. Check: compare `fct_orders` with Bronze.
3. `dbt run -s fct_orders`, then check again.

Answer: compared with a view, what does a table cost you and what does it buy you?

### Round 3: incremental

Change the model in three places:

```sql
{{ config(materialized='incremental', unique_key='order_id') }}   -- first line

-- in the orders CTE:
select * from {{ ref('stg_orders') }}
{% if is_incremental() %}
where ingested_at > (select max(ingested_at) from {{ this }})
{% endif %}

-- at the end of the final select:
    o.ordered_at,
    o.ingested_at
```

1. `dbt run -s fct_orders` **without** `--full-refresh`. It fails. Work out why from the
   error before reading the hints.
2. `dbt run -s fct_orders --full-refresh`, then check.
3. Run the cycle, then `dbt run -s fct_orders`.
4. Find what dbt actually ran: search `dbt_project\logs\dbt.log` for the last `delete from`
   and `insert into`. How many rows did it touch? Verify with:
   ```sql
   select ingested_at, count(*), list(order_id) from gold.fct_orders group by 1 order by 1 desc limit 3;
   ```

Answer: what does `is_incremental()` evaluate to on the first run vs later runs? Confirm with
`dbt compile -s fct_orders` and read the compiled SQL in `target\compiled\`.

### Round 4: break it

**a) A logic change doesn't reach old rows.**
1. In the `flagged` CTE, change `in ('paid', 'shipped')` to `= 'shipped'`.
2. `dbt run -s fct_orders`, *not* a full refresh.
3. Run `select count(*) from gold.fct_orders where order_status = 'paid' and is_revenue`.
   Did the build fail? Is the number right?
4. `--full-refresh` and count again. Then revert the change.

**b) Why `ingested_at` and not `updated_at` (a late-arriving row).**
1. Switch the filter to `where updated_at > (select max(updated_at) from {{ this }})`, add
   `o.updated_at` to the select, and run `--full-refresh`.
2. Open psql (user and database from `.env`):
   `docker exec -it oltp-db psql -U <POSTGRES_USER> -d <POSTGRES_DB>`
3. Insert an order that "committed late": its `updated_at` is 2 minutes old. That's inside
   the sync's 5-minute lookback, but older than what's already in the fact.
   ```sql
   insert into orders (user_id, status, total_amount, created_at, updated_at)
   values (1, 'pending', 0, now() - interval '2 minutes', now() - interval '2 minutes')
   returning id;
   ```
4. Exit psql. Run the sync, then `dbt run -s fct_orders`. Is the order in Bronze? In `fct_orders`?
5. Switch the filter back to `ingested_at` (full refresh, insert another late row, sync, run). Compare.
6. Clean up: `delete from orders where id in (...)` in Postgres. The sync can't see hard
   deletes, so also `delete from bronze.raw_orders where id in (...)` in DuckDB.

### Cleanup

```powershell
git checkout -- dbt_project/models/gold/facts/fct_orders.sql
cd dbt_project
..\.venv\Scripts\dbt build                 # replaces the incremental table; expect PASS=92, WARN=1
```

### Write up

Add your findings to `notes.md`, one short section per round, plus:
- view vs table vs dbt incremental vs a native materialized view (concept only)
- why dbt's `table` is not a materialized view

## Acceptance criteria

- [ ] Each round run, with your prediction written down before each check.
- [ ] You can answer:
  - Which materialization would you pick for `stg_*`, for `fct_orders` here, and for a
    billion-row clickstream fact? Why?
  - What are the three ways an incremental model can quietly drift from the truth?
  - Why is a dbt `table` not a materialized view?
- [ ] `fct_orders` restored; full `dbt build` green.
- [ ] Findings in `notes.md`.

## Hints

- **The round 3 error** ("WHERE clause cannot contain aggregates"): the *existing* table has
  no `ingested_at` column yet. So which table does `ingested_at` inside the subquery resolve
  to? Look up SQL correlated subqueries. Qualifying it (`from {{ this }} as existing`,
  `max(existing.ingested_at)`) makes the error honest.
- **Changed a model's columns or logic?** An incremental model needs `--full-refresh`.
- dbt-duckdb applies increments with `unique_key` as **delete + insert**. Other adapters
  often use `merge`. Without a `unique_key` it's append-only.
- The view round is easy to misread: the view is fresh with no dbt run at all. dbt only
  re-creates the *definition*.

## Out of scope

- Keeping `fct_orders` incremental. At 1,000 rows, a full rebuild takes a fraction of a second.
- Incremental strategies beyond the default (`merge`, `insert_overwrite`, microbatch).
- Native materialized views (DuckDB doesn't have them). Concept only, in the notes.
