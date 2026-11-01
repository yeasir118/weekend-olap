"""Load marketing campaign CSV deliveries into bronze.raw_campaigns.

Bronze keeps each file as delivered: every column is loaded as text, unchanged, plus
metadata recording which delivery each row came from and when it was loaded.
Casting and validation happen later, in Silver.

File naming convention: marketing_campaigns_YYYY-MM-DD.csv (the delivery date).
Each delivery is kept, so history accumulates and nothing is overwritten by the next file.
Files that don't follow the convention are rejected before anything is loaded.

Idempotent per delivery: re-loading a file replaces only the rows from that same file,
so a re-run (or a corrected re-delivery for the same date) never duplicates rows.

Malformed records:
  - Value problems (budget "TBD", date "31/12/2026") load into Bronze as-is; Silver validates.
  - Structural problems (missing or extra columns) can't be mapped to columns, so the raw
    line is quarantined in bronze.raw_campaigns_rejects with the error, and the rest loads.
  - If more than MAX_REJECT_RATIO of a file is structurally broken (wrong delimiter, wrong
    file), the whole file is rejected and nothing from it is loaded.

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

MAX_REJECT_RATIO = 0.5

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

# Quarantine ("dead-letter") table for lines that couldn't be parsed into columns.
CREATE_REJECTS_TABLE = """
CREATE TABLE IF NOT EXISTS bronze.raw_campaigns_rejects (
    source_file    VARCHAR      NOT NULL,
    delivered_on   DATE         NOT NULL,
    line_number    BIGINT       NOT NULL,
    error_type     VARCHAR      NOT NULL,
    error_message  VARCHAR,
    raw_line       VARCHAR,
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


def ingest_file(con: duckdb.DuckDBPyConnection, path: Path, delivered_on: date) -> tuple[int, int]:
    """Load one delivery. Returns (rows loaded, lines rejected)."""
    # Repo-relative and forward-slashed, so the value is the same on any machine.
    source_file = path.resolve().relative_to(REPO_ROOT).as_posix()

    con.execute("BEGIN")
    con.execute("DELETE FROM bronze.raw_campaigns WHERE source_file = ?", [source_file])
    con.execute("DELETE FROM bronze.raw_campaigns_rejects WHERE source_file = ?", [source_file])

    # store_rejects skips unparseable lines and records them in the connection's temp
    # reject_errors table. It accumulates across reads, so reset it for this file
    # (it's created on first use, hence DROP rather than DELETE).
    con.execute("DROP TABLE IF EXISTS temp.reject_errors")
    con.execute("DROP TABLE IF EXISTS temp.reject_scans")
    con.execute(
        """
        INSERT INTO bronze.raw_campaigns BY NAME
        SELECT
            *,
            ? AS source_file,
            ? AS delivered_on,
            now() AS ingested_at
        FROM read_csv(?, header = true, all_varchar = true, store_rejects = true)
        """,
        [source_file, delivered_on, str(path)],
    )
    con.execute(
        """
        INSERT INTO bronze.raw_campaigns_rejects
        SELECT DISTINCT ON (line)
            ?, ?, line, error_type::VARCHAR, error_message, csv_line, now()
        FROM reject_errors
        ORDER BY line
        """,
        [source_file, delivered_on],
    )

    loaded, rejected = con.execute(
        """
        SELECT
            (SELECT count(*) FROM bronze.raw_campaigns WHERE source_file = $1),
            (SELECT count(*) FROM bronze.raw_campaigns_rejects WHERE source_file = $1)
        """,
        [source_file],
    ).fetchone()

    total = loaded + rejected
    if total and rejected / total > MAX_REJECT_RATIO:
        con.execute("ROLLBACK")
        raise ValueError(
            f"{path.name}: {rejected} of {total} lines are malformed "
            f"(limit {MAX_REJECT_RATIO:.0%}). File rejected, nothing loaded."
        )

    con.execute("COMMIT")
    return loaded, rejected


def main(paths: list[Path]) -> None:
    if not paths:
        print(f"No marketing_campaigns*.csv files found in {DATA_DIR}")
        return

    # Validate every name first, so a badly named file loads nothing rather than half a batch.
    deliveries = [(path, delivery_date(path)) for path in paths]

    setup_warehouse()
    with duckdb.connect(str(WAREHOUSE_PATH)) as con:
        con.execute(CREATE_TABLE)
        con.execute(CREATE_REJECTS_TABLE)
        for path, delivered_on in sorted(deliveries, key=lambda d: d[1]):
            loaded, rejected = ingest_file(con, path, delivered_on)
            print(f"Loaded {loaded} rows from {path.name} into bronze.raw_campaigns")
            if rejected:
                print(f"  WARNING: {rejected} malformed lines quarantined in bronze.raw_campaigns_rejects")


if __name__ == "__main__":
    # The glob is deliberately looser than FILE_NAME, so misnamed deliveries are rejected, not skipped.
    main([Path(arg) for arg in sys.argv[1:]] or sorted(DATA_DIR.glob("marketing_campaigns*.csv")))
