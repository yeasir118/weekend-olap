"""Connection to the OLTP source database, shared by the scripts that play "the app"."""

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent


def connect() -> psycopg.Connection:
    load_dotenv(REPO_ROOT / ".env")
    return psycopg.connect(
        host="localhost",
        port=os.environ["POSTGRES_PORT"],
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )
