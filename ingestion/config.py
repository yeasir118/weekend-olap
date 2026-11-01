"""Shared settings for the ingestion scripts.

Paths are anchored to this file, not the current working directory, so scripts
behave the same whether run from the repo root, from ingestion/, or from an IDE.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

WAREHOUSE_PATH = REPO_ROOT / "warehouse" / "analytics.duckdb"

# One schema per medallion layer, plus `meta` for pipeline bookkeeping (watermarks).
SCHEMAS = ("bronze", "silver", "gold", "meta")
