"""Create the DuckDB warehouse file and its layer schemas.

Idempotent: safe to run any number of times. Other pipeline steps run it first.

    .venv\\Scripts\\python ingestion\\setup_warehouse.py
"""

import duckdb

from config import SCHEMAS, WAREHOUSE_PATH


def setup_warehouse() -> None:
    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Connecting to a path that doesn't exist creates the database file.
    with duckdb.connect(str(WAREHOUSE_PATH)) as con:
        for schema in SCHEMAS:
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

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
