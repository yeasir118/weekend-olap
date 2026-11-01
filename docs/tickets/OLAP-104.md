# OLAP-104 · Create the DuckDB warehouse and its layer schemas

**Epic:** E1 — Environment & Scaffolding
**Estimate:** 15–20 min · **Depends on:** OLAP-103 · **Blocks:** OLAP-105, OLAP-301, OLAP-401, OLAP-402

## Context

Postgres is the source system. DuckDB is the warehouse: the place where all copied and transformed data lives. Unlike Postgres, DuckDB has no server. The whole warehouse is a single file (`warehouse/analytics.duckdb`), and any process that opens the file *is* the database engine. SQLite works the same way, but DuckDB stores data in columns instead of rows.

This ticket creates that file and the four schemas that mark the architecture's boundaries:

| Schema | Holds | Written by |
|---|---|---|
| `bronze` | Raw copies of source data | Python ingestion scripts |
| `silver` | Cleaned, standardized models | dbt |
| `gold` | Facts, dimensions, marts | dbt |
| `meta` | Pipeline bookkeeping (sync watermarks) | Python ingestion scripts |

These are logical boundaries within one database, not separate servers. They're the same idea as Postgres schemas.

## What to do

1. Write `ingestion/setup_warehouse.py`. When run, it:
   - opens (and so creates, if missing) `warehouse/analytics.duckdb`
   - creates the four schemas
   - prints what exists afterwards, so a run gives visible confirmation
2. Make it **idempotent**: running it twice in a row must succeed and change nothing the second time. Every later script will assume it's safe to call this script first.
3. Resolve the warehouse path **relative to the repo, not the current working directory**. The script should work the same whether you run it from the repo root, from `ingestion/`, or from VS Code's run button.
4. Keep the warehouse path in one place. Later scripts (CSV ingestion, snapshot, sync) will need the same path, so put it somewhere they can reuse rather than copy-paste. A small shared module or an entry in `.env` both work. Pick one and say why.

## Acceptance criteria

- [ ] With no warehouse file, `python ingestion/setup_warehouse.py` creates `warehouse/analytics.duckdb` with schemas `bronze`, `silver`, `gold` and `meta`.
- [ ] Running it a second time succeeds with no errors and no changes.
- [ ] It works from both the repo root and the `ingestion/` folder.
- [ ] The script closes its connection when done. Use a context manager or an explicit close.
- [ ] The `.duckdb` file is not tracked by git (`git status` doesn't show it).

## Hints and gotchas

- Look at `CREATE SCHEMA IF NOT EXISTS`. For checking the result, `information_schema.schemata` works in DuckDB just as it does in Postgres.
- In DuckDB, the **database (catalog) name comes from the file name**. Your tables will be fully addressable as `analytics.bronze.raw_users`. You'll also see a default `main` schema; leave it alone.
- `pathlib.Path(__file__)` is the usual way to anchor paths to the script's own location.
- **Single writer:** only one process can have the file open for writing. If you have the file open in DBeaver or a Python REPL, the script will fail with a lock error. That's DuckDB working as designed, not a bug in your code.
- You may see a `analytics.duckdb.wal` file appear next to the database. That's the write-ahead log, the same concept as Postgres's WAL. It's already gitignored.
- DuckDB 2.x: use `con.execute(...).fetchall()` to read results. Older tutorials show `con.sql(...).fetchone()`, which no longer works.
- Use the venv interpreter (`.venv\Scripts\python`). Plain `python` on this machine is 3.7.

## Out of scope

- The `meta.sync_state` table. That's OLAP-402, which extends this same script.
- Any tables in `bronze`, `silver` or `gold`.
- dbt configuration (OLAP-105). dbt will connect to this same file.

## Questions you should be able to answer afterwards

1. Postgres needed a container, a port and credentials. DuckDB needed none of those. What does "embedded database" mean, and what do you give up in exchange (think concurrent writers)?
2. Why does the pipeline rely on scripts being idempotent? What would go wrong in OLAP-801 (the one-command rebuild) if this script failed when schemas already existed?
3. Why is it fine to gitignore the entire warehouse file? What makes it reproducible?
