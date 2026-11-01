"""Shared settings for the ingestion scripts.

Paths are anchored to this file, not the current working directory, so scripts
behave the same whether run from the repo root, from ingestion/, or from an IDE.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

# Credentials and ports vary per machine, so they live in .env, not in code.
load_dotenv(REPO_ROOT / ".env")

WAREHOUSE_PATH = REPO_ROOT / "warehouse" / "analytics.duckdb"

# Landing folder for files delivered by other teams (e.g. marketing's campaign CSVs).
DATA_DIR = REPO_ROOT / "data"

# One schema per medallion layer, plus `meta` for pipeline bookkeeping (watermarks).
SCHEMAS = ("bronze", "silver", "gold", "meta")

# OLTP tables replicated into Bronze as bronze.raw_<table>. All have an `id` primary key
# and an `updated_at` column, which incremental sync relies on.
SOURCE_TABLES = ("users", "products", "orders", "order_items", "payments")


def postgres_dsn() -> str:
    """libpq connection string for the OLTP source database."""
    return (
        f"host=localhost port={os.environ['POSTGRES_PORT']} dbname={os.environ['POSTGRES_DB']} "
        f"user={os.environ['POSTGRES_USER']} password={os.environ['POSTGRES_PASSWORD']}"
    )
