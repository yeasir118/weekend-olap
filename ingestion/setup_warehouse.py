"""Create the DuckDB warehouse file, its layer schemas and pipeline bookkeeping tables.

Idempotent: safe to run any number of times. Other pipeline steps run it first.

    .venv\\Scripts\\python ingestion\\setup_warehouse.py
"""

import duckdb

from config import SCHEMAS, WAREHOUSE_PATH

# One row per replicated source table: how far ingestion has got.
CREATE_SYNC_STATE = """
CREATE TABLE IF NOT EXISTS meta.sync_state (
    table_name      VARCHAR      PRIMARY KEY,
    last_synced_at  TIMESTAMPTZ,              -- watermark: max(updated_at) loaded so far
    last_run_at     TIMESTAMPTZ  NOT NULL,
    last_run_type   VARCHAR      NOT NULL,    -- 'snapshot' or 'incremental'
    rows_loaded     BIGINT       NOT NULL     -- rows written by the last run
)
"""


def setup_warehouse() -> None:
    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Connecting to a path that doesn't exist creates the database file.
    with duckdb.connect(str(WAREHOUSE_PATH)) as con:
        for schema in SCHEMAS:
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        con.execute(CREATE_SYNC_STATE)

        existing = con.execute(
            """
            SELECT schema_name
            FROM information_schema.schemata
            WHERE catalog_name = current_database()
            ORDER BY schema_name
            """
        ).fetchall()

    print(f"Warehouse: {WAREHOUSE_PATH}")
    print("Schemas:   " + ", ".join(name for (name,) in existing))


if __name__ == "__main__":
    setup_warehouse()
