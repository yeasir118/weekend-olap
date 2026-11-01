"""Load marketing campaign CSV deliveries into bronze.raw_campaigns.

Bronze keeps each file as delivered: every column is loaded as text, unchanged, plus
metadata recording which delivery each row came from and when it was loaded.
Casting and validation happen later, in Silver.

File naming convention: marketing_campaigns_YYYY-MM-DD.csv (the delivery date).
Each delivery is kept, so history accumulates and nothing is overwritten by the next file.
Files that don't follow the convention are rejected before anything is loaded.

Idempotent per delivery: re-loading a file replaces only the rows from that same file,
so a re-run (or a corrected re-delivery for the same date) never duplicates rows.

    .venv\\Scripts\\python ingestion\\ingest_csv.py                                    # all deliveries in data/
    .venv\\Scripts\\python ingestion\\ingest_csv.py data\\marketing_campaigns_2026-11-01.csv  # specific file(s)
"""

import re
import sys
from datetime import date
from pathlib import Path

import duckdb

from config import DATA_DIR, REPO_ROOT, WAREHOUSE_PATH
from setup_warehouse import setup_warehouse

FILE_NAME = re.compile(r"marketing_campaigns_(\d{4}-\d{2}-\d{2})\.csv")

# Explicit columns rather than CREATE TABLE AS from the first file: if marketing changes
# the file layout, the load fails loudly instead of silently creating a different table.
CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS bronze.raw_campaigns (
    campaign_id    VARCHAR,
    campaign_name  VARCHAR,
    channel        VARCHAR,
    budget         VARCHAR,
    start_date     VARCHAR,
    source_file    VARCHAR      NOT NULL,
    delivered_on   DATE         NOT NULL,
    ingested_at    TIMESTAMPTZ  NOT NULL
)
"""


def delivery_date(path: Path) -> date:
    match = FILE_NAME.fullmatch(path.name)
    if not match:
        raise ValueError(f"{path.name}: expected a name like marketing_campaigns_YYYY-MM-DD.csv")
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        raise ValueError(f"{path.name}: {match.group(1)} is not a valid date") from None


def ingest_file(con: duckdb.DuckDBPyConnection, path: Path, delivered_on: date) -> int:
    # Repo-relative and forward-slashed, so the value is the same on any machine.
    source_file = path.resolve().relative_to(REPO_ROOT).as_posix()

    con.execute("BEGIN")
    con.execute("DELETE FROM bronze.raw_campaigns WHERE source_file = ?", [source_file])
    con.execute(
        """
        INSERT INTO bronze.raw_campaigns BY NAME
        SELECT
            *,
            ? AS source_file,
            ? AS delivered_on,
            now() AS ingested_at
        FROM read_csv(?, header = true, all_varchar = true)
        """,
        [source_file, delivered_on, str(path)],
    )
    loaded = con.execute(
        "SELECT count(*) FROM bronze.raw_campaigns WHERE source_file = ?", [source_file]
    ).fetchone()[0]
    con.execute("COMMIT")
    return loaded


def main(paths: list[Path]) -> None:
    if not paths:
        print(f"No marketing_campaigns*.csv files found in {DATA_DIR}")
        return

    # Validate every name first, so a badly named file loads nothing rather than half a batch.
    deliveries = [(path, delivery_date(path)) for path in paths]

    setup_warehouse()
    with duckdb.connect(str(WAREHOUSE_PATH)) as con:
        con.execute(CREATE_TABLE)
        for path, delivered_on in sorted(deliveries, key=lambda d: d[1]):
            loaded = ingest_file(con, path, delivered_on)
            print(f"Loaded {loaded} rows from {path.name} into bronze.raw_campaigns")


if __name__ == "__main__":
    # The glob is deliberately looser than FILE_NAME, so misnamed deliveries are rejected, not skipped.
    main([Path(arg) for arg in sys.argv[1:]] or sorted(DATA_DIR.glob("marketing_campaigns*.csv")))
