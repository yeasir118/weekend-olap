# OLAP-105 · Initialize the dbt project and connect it to the warehouse

**Epic:** E1 — Environment & Scaffolding
**Estimate:** 20–30 min · **Depends on:** OLAP-103, OLAP-104 · **Blocks:** all of E5 and E6

## Context

dbt is the "T" in ELT. It doesn't move data: ingestion has already put the data in the warehouse. dbt takes `SELECT` statements you write as files, works out the order they depend on each other, and runs them *inside* the warehouse as views or tables.

A dbt setup has two separate halves:

| File | Answers | Lives |
|---|---|---|
| `dbt_project.yml` | *What* to build: model folders, materializations, schemas | In the project, committed |
| `profiles.yml` | *Where* to build it: connection and credentials, per environment ("target") | Normally in `~/.dbt/`, outside the repo |

This ticket sets up both halves, points dbt at `warehouse/analytics.duckdb`, and makes models land in the `silver` and `gold` schemas you created in OLAP-104.

## What to do

1. **Create the project** in a folder named `dbt_project/` at the repo root, using `dbt init`.
   - `dbt init` creates a folder named after the *project name* you give it. Decide how you'll end up with the folder named `dbt_project/`: name the project to match, or rename afterwards. The project name appears inside `dbt_project.yml`, so pick something meaningful.
   - Skip the interactive profile setup. You'll write `profiles.yml` by hand, which teaches you more.
   - Delete the generated `models/example/` folder and its config in `dbt_project.yml`.
2. **Write `dbt_project/profiles.yml`** using the `duckdb` adapter, pointing at the warehouse file.
   - Keep it **in the repo** rather than `~/.dbt/`. A DuckDB profile contains no secrets (it's just a file path), and keeping it with the project means a fresh clone works without extra setup.
   - The profile name must match the `profile:` key in `dbt_project.yml`.
   - Define a single target named `dev`.
3. **Configure model folders in `dbt_project.yml`:**
   - `models/silver/` → schema `silver`, materialized as **view**
   - `models/gold/` → schema `gold`, materialized as **table**
   - These defaults come from outline §5. Individual models can override them later.
4. **Override `generate_schema_name`** with a macro in `dbt_project/macros/` so models land in `silver`, not `main_silver` (see gotchas). This is the only macro in the project, so keep it short and add a comment explaining why it exists.
5. **Smoke test:** add a throwaway model (e.g. `select 1 as ok`) in `models/silver/`, run it, confirm where it landed, then delete it and the view it created.

## Acceptance criteria

- [ ] `dbt debug` (run from `dbt_project/`) passes all checks, including "Connection test: OK".
- [ ] `dbt_project.yml` has no leftover `example` config. `dbt ls` doesn't list the example models.
- [ ] The smoke-test model is created as a **view** in **`silver`** (`analytics.silver.<model>`), **not** in `main_silver`. Check with the read-only query from OLAP-104 against `information_schema.tables`.
- [ ] After cleanup, no smoke-test model or view is left behind.
- [ ] `git status` doesn't show `target/`, `logs/` or `.user.yml`. They're already gitignored, so confirm the ignore rules work.

## Hints and gotchas

- **Where dbt looks for `profiles.yml`:** the `--profiles-dir` flag, then the `DBT_PROFILES_DIR` environment variable, then the **current directory**, then `~/.dbt/`. If you always run dbt from inside `dbt_project/`, the in-repo file is found automatically.
- **Path to the warehouse:** a relative path in `profiles.yml` is resolved from where you *run* dbt, not from where the file lives. `../warehouse/analytics.duckdb` works from `dbt_project/` and breaks anywhere else. Either accept "always run dbt from `dbt_project/`" as a project convention, or look at dbt's `env_var()` function. Decide, and add a comment in the profile explaining your choice.
- **Why `main_silver` happens:** by default dbt builds the schema name as `<target schema>_<custom schema>`. That's deliberate: on a shared warehouse, each developer's target schema is personal (`dbt_alice`), so their models land in `dbt_alice_silver` and don't overwrite each other or production. You're the only user here, so the prefix is just noise. dbt's docs on "custom schemas" show the macro to override it.
- **The target schema still matters:** the profile's `schema:` (typically `main`) is the fallback for any model *without* a custom schema. Your macro should keep that fallback.
- **File lock:** dbt opens the warehouse for writing. Close any Python sessions, DuckDB UI or DBeaver connections on the file before `dbt run`.
- **Use the venv's dbt:** `..\.venv\Scripts\dbt` from inside `dbt_project/`, or activate the venv first. You'll see a "dbt v2 is available" notice. Ignore it; this project is pinned to v1.
- `dbt debug` checks connectivity but **doesn't** check your schema config. Only the smoke test does that.

## Out of scope

- `sources.yml` and declaring Bronze tables (OLAP-501).
- Any real Silver or Gold models.
- Multiple targets (`dev`/`prod`). That's worth knowing about, but one target is enough here.
- dbt packages (`dbt_utils` etc.). The outline deliberately avoids them.

## Questions you should be able to answer afterwards

1. Why does dbt keep *what to build* (`dbt_project.yml`) separate from *where to build it* (`profiles.yml`)? What would change in each when moving from your laptop to a production warehouse?
2. What is a "target", and how would a team use `dev` and `prod` targets against the same warehouse?
3. Why does dbt prefix custom schemas by default, and in what kind of setup would removing the prefix be a mistake?
4. When dbt "runs a model", what SQL does it actually send to DuckDB? (Look in `target/run/` after the smoke test.)
