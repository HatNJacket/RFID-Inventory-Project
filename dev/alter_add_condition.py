"""One-off prod migration (Nick, 2026-09-16): per-BOX condition column
on the three tag lifecycle tables - live, retired, released - so a
box's condition survives retirement, unretire, release and re-apply.

    set DATABASE_URL=<prod mssql url>
    py dev/alter_add_condition.py

Idempotent: existing columns are skipped.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SHOPIFY_STORE", "t.myshopify.com")
os.environ.setdefault("SHOPIFY_CLIENT_ID", "x")
os.environ.setdefault("SHOPIFY_CLIENT_SECRET", "x")

from sqlalchemy import text  # noqa: E402

from app.database import get_engine  # noqa: E402

TABLES = ["rfid_assignments", "rfid_retired_tags", "rfid_released_tags"]

engine = get_engine()
with engine.connect() as conn:
    for table in TABLES:
        exists = conn.execute(text(
            "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = :t AND COLUMN_NAME = 'condition'"
        ), {"t": table}).first()
        if exists:
            print(f"OK    {table}.condition already exists")
            continue
        # "condition" is a reserved word on SQL Server - bracket it.
        conn.execute(text(
            f"ALTER TABLE {table} ADD [condition] varchar(20) NULL"))
        print(f"ADDED {table}.condition")
    conn.commit()
    print("DONE.")
