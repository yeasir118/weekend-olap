# Materializations and incremental models

How dbt turns a model into something in the warehouse, how incremental models work, and the
ways they can quietly go wrong. Every example uses this project's pipeline:

```
Postgres (OLTP app)
  → ingestion/incremental_sync.py (Python)  → bronze.raw_*      tables, one per source table
  → dbt silver models                       → silver.stg_*     views: rename, cast, UTC
  → dbt gold models                         → gold.fct_*, dim_*, mart_*   tables
```

Two timestamps matter throughout:
- `updated_at`: set by the **source app** (Postgres) when a row changes.
- `ingested_at`: set by **our loader** when the row lands in Bronze.

---

## 1. What `dbt run` does

Every model is a `SELECT`. `dbt run` wraps that `SELECT` in a statement, and the
**materialization** decides which one:

| Materialization | dbt wraps your `SELECT` in | When the data is computed |
|---|---|---|
| `view` | `CREATE OR REPLACE VIEW fct_orders AS <select>` | Later: every time anyone queries the view |
| `table` | `CREATE TABLE fct_orders__dbt_tmp AS <select>`, then swap it in | Now: all rows |
| `incremental` | First run: same as `table`. Later runs: compute only new rows and apply them (section 3) | Now: only what changed |

- For a **view**, `dbt run` saves the query; no data moves. The database runs the query
  whenever anyone reads the view: DuckDB, a BI tool, a Python script. dbt is not involved at
  read time.
- For a **table**, `dbt run` computes and stores every row. The table is as fresh as the
  last run.

The materialization is set in `dbt_project.yml` per folder (here `silver` = view, `gold` =
table) or per model with `{{ config(materialized='...') }}` at the top of the file; the
model file wins.

---

## 2. The three compared (experiment)

`fct_orders` (one row per order) was built each way. Each round: the app simulator made
5 new orders and 3 status changes, `incremental_sync.py` loaded them into Bronze, and we
looked at `fct_orders` before and after `dbt run`.

| | What DuckDB stores | After sync, before `dbt run` | What `dbt run` did |
|---|---|---|---|
| `view` | Only the SQL | **Already current** (1005 → 1010 rows) | Re-created the definition |
| `table` | The result rows | **Stale** (1010 while Bronze had 1015) | Rebuilt all 1015 rows |
| `incremental` | The result rows | **Stale**, like a table | Processed 8 rows of 1020 |

Note that in the view round, `mart_monthly_revenue` (a table built *from* `fct_orders`) stayed
stale even though the view was fresh. A view is only as fresh as its inputs, and a table is only
as fresh as its last build.

**Trade-offs:**

| | Build cost | Read cost | Freshness |
|---|---|---|---|
| `view` | ~zero | The whole upstream chain runs on every read | Always current |
| `table` | Everything, every run | Fast, predictable | As of the last run |
| `incremental` | Only what changed | Fast, predictable | As of the last run, *if* the filter caught everything (section 5) |

---

## 3. How an incremental model works

### It's the ingestion sync pattern, inside the warehouse

`incremental_sync.py` does three things per table:

1. **Watermark:** "what's the newest thing I already have?" (stored in `meta.sync_state`).
2. **Stage:** fetch only rows newer than that into a temp table.
3. **Upsert:** replace matching rows by primary key; insert the new ones.

A dbt incremental model does exactly the same thing, from Silver into Gold. The only difference
is that there's no state table: the model reads its watermark **from its own data**, for
example `max(ingested_at)` of the table it's about to update.

### The model

```sql
{{ config(materialized='incremental', unique_key='order_id') }}

with orders as (
    select * from {{ ref('stg_orders') }}
    {% if is_incremental() %}
    where ingested_at > (select max(existing.ingested_at) from {{ this }} as existing)
    {% endif %}
),
-- ... joins and flags as in the table version, plus ingested_at in the final select
```

- `{{ this }}` = the model's own existing table (`gold.fct_orders`).
- `is_incremental()` is **true** only when all three hold: the model is incremental, its table
  already exists, and you didn't pass `--full-refresh`. Otherwise it's false and the `where`
  disappears, so the model builds like a plain table.
- So the model has **two versions of its SQL**, and dbt picks one at compile time. See it
  with `dbt compile -s fct_orders` and look in `target/compiled/`.
- `unique_key` tells dbt which column identifies a row, so a changed row *replaces* its old
  version instead of being added as a duplicate.

### One run, step by step

**Before:** `gold.fct_orders` has 1,012 orders, all ingested at or before 06:49:24.

**Source activity:** orders 1016–1020 are created; 947, 956 and 964 move from paid to
shipped. The sync writes those 8 rows to Bronze with `ingested_at = 06:50:47`.

**`dbt run -s fct_orders`:**

1. **Compile.** `is_incremental()` is true, so the filter stays in:
   ```sql
   select * from silver.stg_orders
   where ingested_at > (select max(existing.ingested_at) from gold.fct_orders as existing)
   --                    └── 06:49:24: the model's own watermark
   ```
2. **Stage.** dbt runs the filtered model into a temp table:
   ```sql
   create temporary table fct_orders__dbt_tmp as ( ...filtered model... )
   -- 8 rows: 1016–1020 (new), 947, 956, 964 (changed)
   ```
   Joins, flags and keys run for those 8 orders only.
3. **Apply** (the `delete+insert` strategy, the dbt-duckdb default with a `unique_key`):
   ```sql
   delete from gold.fct_orders
   where order_id in (select order_id from fct_orders__dbt_tmp);  -- removes old 947, 956, 964
   insert into gold.fct_orders
   select * from fct_orders__dbt_tmp;                             -- inserts all 8 fresh rows
   ```

**After:** 1,020 rows. 947, 956 and 964 show `shipped`, and the other 1,012 rows were never
touched. The new watermark is 06:50:47, which is where the next run starts.

**Checked:** the result was identical to a full rebuild (`EXCEPT` both ways: 0 rows).

### Why bother

A table run costs time proportional to **all of history**. An incremental run costs time
proportional to **what changed**. At 1,000 orders that's 0.1 s vs 0.1 s. At a billion
events, it's hours vs minutes.

### Strategies (how the batch is applied)

| Strategy | What it does | Typical use |
|---|---|---|
| `append` | Insert the batch; no `unique_key` | Immutable events (logs, clicks) |
| `delete+insert` | Delete rows whose key is in the batch, insert the batch | What we used; dbt-duckdb default with a key |
| `merge` | One `MERGE`: update matches, insert the rest | Snowflake, BigQuery, Databricks default |
| `insert_overwrite` | Replace whole partitions (e.g. whole days) | Big partitioned tables (BigQuery, Spark) |
| `microbatch` | dbt processes the table in time slices (e.g. one day per batch) and can backfill ranges | Large time-series facts (dbt 1.9+) |

Availability depends on the adapter. The idea is the same for all of them: compute a batch,
apply it to the existing table.

---

## 4. A gotcha when switching an existing table to incremental

The first incremental run against the old `table` version failed with:

```
Binder Error: WHERE clause cannot contain aggregates!
```

The old table had no `ingested_at` column. Inside the subquery, `max(ingested_at)` didn't find
the column on `{{ this }}`, so SQL's scoping rules bound it to the **outer** query's
`stg_orders.ingested_at`, a correlated subquery. That put an aggregate of the outer row inside
`WHERE`, which isn't allowed.

- Qualifying the column (`max(existing.ingested_at) ... as existing`) turns it into an honest
  "not found" error.
- The fix is `dbt run --full-refresh -s fct_orders`. **Whenever an incremental model's columns
  change, it needs a full refresh.** (Or set `on_schema_change`: `append_new_columns`,
  `sync_all_columns` or `fail`; the default `ignore` silently skips new columns.)

---

## 5. Where incremental goes wrong

All the risks come from one blind spot:

> An incremental run only touches rows that **pass the filter** ("newer than what I already
> have"). Every other row keeps whatever value it had, whether or not that is still correct.

A full table rebuild has no blind spot; it recomputes everything. Each case below is a
situation where `fct_orders` *should* change but no row passes the filter.

### Risk 1: You change the logic

**Monday.** Rule: revenue = paid or shipped. dbt runs.

| order_id | status | is_revenue |
|---|---|---|
| 500 | paid | **true** |

**Tuesday.** Finance: "only count it once it's shipped." You edit the rule to
`order_status = 'shipped'`. One new order arrives overnight, 600 (paid). Incremental run:

- The filter picks up only order 600. It's computed with the **new** rule: `false`. ✓
- Order 500 didn't change in the data, so it doesn't pass the filter and keeps Monday's answer.

| order_id | status | is_revenue | |
|---|---|---|---|
| 500 | paid | **true** | ✗ old rule |
| 600 | paid | false | ✓ new rule |

Two paid orders, two answers. Revenue is overstated, the build is green, and every test passes.
**In the experiment, 32 old paid orders were still counted as revenue after the rule change.**
Only `--full-refresh` fixed them.

*The rows didn't change, the SQL did, and the filter only looks at data.*

### Risk 2: A row arrives "in the past"

Suppose the filter used the app's timestamp, `updated_at`. Postgres stamps `updated_at` with
`now()`, which is the time the **transaction started**, not when it committed.

| Time | What happens |
|---|---|
| 10:00:00 | The app starts a transaction for order **700**: `updated_at = 10:00:00` |
| 10:00:10 | Order **701** is created and commits fast: `updated_at = 10:00:10` |
| 10:00:15 | Sync runs. 700 isn't committed yet, so it can't be seen. Only 701 reaches Bronze |
| 10:00:20 | dbt runs. `fct_orders` gets 701, so `max(updated_at) = 10:00:10` |
| 10:00:30 | Order 700 finally commits, still stamped 10:00:00 |
| 10:05:00 | The sync's lookback window catches 700 (section 6). ✓ It's in Bronze |
| 10:05:10 | dbt runs: `where updated_at > 10:00:10`. 700 has **10:00:00**, so it doesn't pass ✗ |

Order 700 is in Bronze and Silver, but **never** in `fct_orders`, and every later run only
raises the watermark. It's lost until a full refresh.

**Fix:** filter on `ingested_at`, which asks "when did *our warehouse* receive it?" Order 700
landed at 10:05:00, later than anything already in the fact, so it passes. ✓
**Rule: incremental models filter on load time, not on the source's business timestamp.**

### Risk 3: A row gets deleted

Order 9999 was a test order. Someone deletes it from Bronze:

```sql
delete from bronze.raw_orders where id = 9999;
```

It's now gone from Bronze, and from `stg_orders` (a view over it). dbt runs:

- **As a table:** full rebuild from `stg_orders`. 9999 isn't there, so it's gone. ✓
- **As incremental:** the filter asks "which rows are newer?" A deleted row isn't a row at all,
  so nothing comes through the filter, and delete+insert only touches keys that are in the
  batch. 9999 **stays in `fct_orders` forever**. ✗

An incremental model can only add or update rows; it never notices that something
disappeared. (It's the same blind spot the ingestion sync has for rows hard-deleted in
Postgres: a watermark can't see absence.)

### The pattern

| Situation | Should the fact change? | Does a row pass the filter? | Result |
|---|---|---|---|
| Logic changed | Yes, every row | No: the data didn't change | Old rows wrong |
| Row arrived late | Yes, add it | No: its timestamp looks old | Row missing |
| Row deleted | Yes, remove it | No: there's no row | Row stays |

---

## 6. Late data and the lookback window

Risk 2 is a general problem: **a watermark assumes rows arrive in timestamp order, and they
don't.** The standard fix, used by the ingestion sync, is a **lookback**: re-read a little
further back than the watermark, and let the upsert absorb the duplicates.

### The problem without a lookback

`incremental_sync.py` asks Postgres for `updated_at > watermark`, where the watermark is the
largest `updated_at` it loaded last time.

| Time | Postgres | Sync |
|---|---|---|
| 10:00:00 | Transaction A starts: order 700, `updated_at = 10:00:00` (now() = transaction start) | |
| 10:00:10 | Transaction B: order 701, `updated_at = 10:00:10`, commits | |
| 10:00:15 | | Reads `updated_at > 09:55:00`. Sees 701 only (A not committed). **Watermark → 10:00:10** |
| 10:00:30 | Transaction A commits. Order 700 is now visible, stamped **10:00:00** | |
| 10:05:00 | | Reads `updated_at > 10:00:10`. 700 (10:00:00) **doesn't qualify**: lost forever ✗ |

The same thing happens with **ties**: two rows with the same `updated_at`, one committed just
after the sync ran. `>` skips the second.

### With the lookback (what the script does)

```python
LOOKBACK = "5 minutes"
... WHERE updated_at > watermark - INTERVAL '5 minutes'
```

| Time | Sync |
|---|---|
| 10:00:15 | Watermark is 09:55:00, so it reads `updated_at > 09:50:00`. Gets 701. Watermark → 10:00:10 |
| 10:05:00 | Reads `updated_at > 10:00:10 - 5 min = 09:55:10`. Gets **700** ✓ (and 701 again) |

Order 701 is read twice; that's the cost. The script handles the re-reads:

1. **Stage** everything inside the window into a temp table.
2. **Drop re-reads that haven't changed**: rows where Bronze already has the same or newer
   `updated_at`. These show in the sync's output as the `re-read` column.
3. **Upsert** the rest by primary key (`INSERT OR REPLACE`), so nothing is duplicated.
4. Move the watermark to the largest `updated_at` actually loaded, and never backwards.

Step 2 matters downstream too: unchanged re-reads are dropped before the upsert, so their
`ingested_at` doesn't move, and a dbt incremental model filtering on `ingested_at` doesn't
reprocess them.

### Choosing the window

- It must be longer than the longest transaction the app runs (plus clock skew, if the app
  and database clocks differ). A transaction open longer than the window is still lost.
- Longer is safer but re-reads more rows every run. Minutes are typical for app tables,
  hours or days for sources known to deliver late (mobile events, partner files).
- The only complete fixes are ordering by something assigned at **commit**, not at start: the
  database log position, which is what CDC tools like Debezium use (they read the WAL), or
  Postgres commit timestamps (`track_commit_timestamp`).

### Lookback in dbt incremental models

The same idea is common in dbt:

```sql
{% if is_incremental() %}
where ingested_at > (select max(existing.ingested_at) from {{ this }} as existing) - interval 3 day
{% endif %}
```

It needs a `unique_key`, so re-processed rows replace themselves instead of duplicating.

Here it isn't needed. The sync loads all tables in one DuckDB transaction, and DuckDB's
single-writer file lock means dbt can never run while a sync is half-committed, so
`ingested_at` arrives in order. On a shared cloud warehouse, where loads and dbt runs
overlap, `ingested_at` has the same late-commit problem as `updated_at`, and a lookback is
standard.

---

## 7. Making incremental models safe

- **Filter on load time** (`ingested_at`, `_loaded_at`), not on the source's `updated_at`.
- **Always set `unique_key`** unless the data is truly append-only, so re-processed rows
  replace instead of duplicate.
- **Add a lookback** when loads can overlap with dbt runs or data can arrive late.
- **Full-refresh after any logic or column change** to the model, or to anything upstream
  that changes its output.
- **Schedule a periodic full refresh** (e.g. weekly) to clear out whatever has drifted.
- **Handle deletes deliberately:** prefer soft deletes (`is_deleted` rows flow through the
  filter like any update), or tombstone rows from CDC. Hard deletes need a full refresh or a
  separate cleanup step.
- **Prove it:** compare the incremental table with a full rebuild (`EXCEPT` both ways), at
  least once and after big changes.

---

## 8. Which to choose

- **`view`:** staging and light renaming models, read mostly by dbt itself. Here, `silver.stg_*`.
- **`table`:** facts, dimensions and marts at small and medium scale. Here, everything in
  `gold`. 1,000 orders rebuild in a fraction of a second.
- **`incremental`:** only when a full rebuild is too slow or too expensive. Typically
  large, append-mostly event facts (clicks, logs, order lines at millions or billions of rows).
  You're buying speed with the correctness risks in section 5.

---

## 9. A dbt `table` is not a materialized view

- A database **materialized view** is an object the *database* owns. It stores the definition
  and the result, and the database refreshes it: manually (`REFRESH MATERIALIZED VIEW` in
  Postgres), or automatically/incrementally (Snowflake dynamic tables, ClickHouse, BigQuery).
- A **dbt `table`** is a plain table. The database doesn't know how it was built. dbt runs
  `CREATE TABLE ... AS SELECT` and swaps it in. Freshness depends entirely on when an
  orchestrator runs dbt.
- So a dbt table won't refresh itself. In exchange, it works on any database, its logic lives
  in version-controlled SQL, and it's tested like any other model. (DuckDB has no
  materialized views. dbt offers a `materialized_view` materialization on adapters that do.)

---

## 10. Try it

The hands-on version of everything above (switch `fct_orders` between the three
materializations, run the simulator, break the incremental model in each of the three ways) is
in `docs/tickets/OLAP-701.md`.
