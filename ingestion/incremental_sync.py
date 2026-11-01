"""Bring Bronze up to date with rows changed in Postgres since the last sync.

For each table:
    1. read the watermark from meta.sync_state
    2. stage rows from postgres.public.<table> with updated_at > watermark - LOOKBACK
    3. drop staged rows Bronze already has at the same version (the lookback overlap)
    4. upsert the rest into bronze.raw_<table> on id
    5. move the watermark to max(updated_at) of the staged rows

All tables are synced in one warehouse transaction: rows and watermarks are committed
together or not at all, so a failed run can simply be re-run.

Why LOOKBACK: Postgres sets now() at the *start* of a transaction. A transaction that
started before our last sync but committed after it carries an updated_at older than the
watermark, and a strict `updated_at > watermark` would miss it forever. Re-reading a short
window behind the watermark catches those rows; the upsert makes re-reading harmless.

Known blind spot: hard-deleted rows produce nothing to fetch, so they stay in Bronze.
Only log-based CDC sees deletes (see OLAP-404/405).

Requires initial_snapshot.py to have run first (it creates the tables and watermarks).

    .venv\\Scripts\\python ingestion\\incremental_sync.py
"""

import duckdb

from config import SOURCE_TABLES, WAREHOUSE_PATH, postgres_dsn
from setup_warehouse import setup_warehouse

LOOKBACK = "5 minutes"


def sync_table(con: duckdb.DuckDBPyConnection, table: str) -> tuple[int, int, int, object]:
    """Returns (new rows, updated rows, re-read unchanged rows, watermark)."""
    state = con.execute(
        "SELECT last_synced_at FROM meta.sync_state WHERE table_name = ?", [table]
    ).fetchall()
    if not state:
        raise RuntimeError(f"No watermark for {table}. Run initial_snapshot.py first.")
    watermark = state[0][0]

    # Stage first, then merge: Postgres is read once, and the batch can be inspected
    # (counted, classified) before anything in Bronze changes.
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE staged AS
        SELECT *, now() AS ingested_at
        FROM pg.public.{table}
        WHERE updated_at > ?::TIMESTAMPTZ - INTERVAL '{LOOKBACK}'
        """,
        [watermark],
    )

    new_rows, updated_rows, unchanged_rows, max_updated_at = con.execute(
        f"""
        SELECT
            count(*) FILTER (WHERE b.id IS NULL),
            count(*) FILTER (WHERE s.updated_at > b.updated_at),
            count(*) FILTER (WHERE s.updated_at <= b.updated_at),
            max(s.updated_at)
        FROM staged s
        LEFT JOIN bronze.raw_{table} b ON b.id = s.id
        """
    ).fetchall()[0]

    # Rows from the lookback window that Bronze already has: skip them, so ingested_at
    # keeps recording when each version actually arrived.
    con.execute(
        f"""
        DELETE FROM staged
        WHERE id IN (
            SELECT s.id FROM staged s
            JOIN bronze.raw_{table} b ON b.id = s.id AND s.updated_at <= b.updated_at
        )
        """
    )
    con.execute(f"INSERT OR REPLACE INTO bronze.raw_{table} BY NAME SELECT * FROM staged")

    # The watermark only moves forward, and only as far as the data we actually saw.
    new_watermark = max(watermark, max_updated_at) if max_updated_at else watermark
    con.execute(
        "INSERT OR REPLACE INTO meta.sync_state VALUES (?, ?, now(), 'incremental', ?)",
        [table, new_watermark, new_rows + updated_rows],
    )
    return new_rows, updated_rows, unchanged_rows, new_watermark


def main() -> None:
    setup_warehouse()
    with duckdb.connect(str(WAREHOUSE_PATH)) as con:
        con.execute(f"ATTACH '{postgres_dsn()}' AS pg (TYPE postgres, READ_ONLY)")

        con.execute("BEGIN")
        results = {table: sync_table(con, table) for table in SOURCE_TABLES}
        con.execute("COMMIT")

    print(f"{'table':<12} {'new':>5} {'updated':>8} {'re-read':>8}  watermark")
    for table, (new_rows, updated_rows, unchanged_rows, watermark) in results.items():
        print(f"{table:<12} {new_rows:>5} {updated_rows:>8} {unchanged_rows:>8}  {watermark}")


if __name__ == "__main__":
    main()
