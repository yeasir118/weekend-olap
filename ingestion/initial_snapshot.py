"""Copy every OLTP source table from Postgres into Bronze, in full.

This is the starting point for replication: afterwards, incremental_sync.py only fetches
rows changed since the watermark recorded here.

For each table:
    postgres.public.<table>  ->  bronze.raw_<table>   (all source columns, unchanged)
                                 + ingested_at metadata
                                 + primary key on id   (needed by the incremental upsert)
    meta.sync_state.last_synced_at = max(updated_at) of the copied rows

Destructive for Bronze: existing raw_<table> tables are replaced. Re-running it is how you
recover from a broken sync (a "full refresh"). Postgres is attached read-only, so the
source is never modified.

    .venv\\Scripts\\python ingestion\\initial_snapshot.py
"""

import duckdb

from config import SOURCE_TABLES, WAREHOUSE_PATH, postgres_dsn
from setup_warehouse import setup_warehouse


def snapshot_table(con: duckdb.DuckDBPyConnection, table: str) -> tuple[int, int, object]:
    """Returns (source row count, bronze row count, watermark)."""
    con.execute(
        f"""
        CREATE OR REPLACE TABLE bronze.raw_{table} AS
        SELECT *, now() AS ingested_at
        FROM pg.public.{table}
        """
    )
    con.execute(f"ALTER TABLE bronze.raw_{table} ADD PRIMARY KEY (id)")

    # The watermark comes from the copied data, not the clock: "everything up to this
    # updated_at is in Bronze". The next sync asks Postgres for rows after it.
    bronze_rows, watermark = con.execute(
        f"SELECT count(*), max(updated_at) FROM bronze.raw_{table}"
    ).fetchone()
    source_rows = con.execute(f"SELECT count(*) FROM pg.public.{table}").fetchone()[0]

    con.execute(
        "INSERT OR REPLACE INTO meta.sync_state VALUES (?, ?, now(), 'snapshot', ?)",
        [table, watermark, bronze_rows],
    )
    return source_rows, bronze_rows, watermark


def main() -> None:
    setup_warehouse()
    with duckdb.connect(str(WAREHOUSE_PATH)) as con:
        con.execute(f"ATTACH '{postgres_dsn()}' AS pg (TYPE postgres, READ_ONLY)")

        # One warehouse transaction: either every table is refreshed or none is.
        con.execute("BEGIN")
        results = {table: snapshot_table(con, table) for table in SOURCE_TABLES}
        con.execute("COMMIT")

    print(f"{'table':<12} {'postgres':>9} {'bronze':>9}  watermark")
    for table, (source_rows, bronze_rows, watermark) in results.items():
        flag = "" if source_rows == bronze_rows else "  <-- MISMATCH"
        print(f"{table:<12} {source_rows:>9} {bronze_rows:>9}  {watermark}{flag}")


if __name__ == "__main__":
    main()
