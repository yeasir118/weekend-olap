# dbt project: weekend_olap

Builds the Silver (`stg_*` views) and Gold (dimensions, facts, marts) layers in
`../warehouse/analytics.duckdb` from the Bronze tables loaded by `../ingestion/`.

Always run dbt from this folder: `profiles.yml` lives here, and the warehouse path in it is
relative to this folder.

```powershell
..\.venv\Scripts\dbt build                      # all models + tests
..\.venv\Scripts\dbt build -s +fct_orders       # one model and everything upstream
..\.venv\Scripts\dbt show -s top_customers      # run an analysis
```

The architecture, setup and design decisions are in the [root README](../README.md).
