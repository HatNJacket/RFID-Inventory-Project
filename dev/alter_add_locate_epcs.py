"""One-off prod migration: add rfid_locate_queue.epcs.

The locate list can carry SPECIFIC EPCs to hunt (Nick, 2026-09-08:
the audit queues the SILENT tags - hunting every tag of the SKU let
the on-shelf boxes drown out the missing one, ...B3F1EB). Run ONCE
against prod before deploying:

    set DATABASE_URL=<prod mssql url, from the app's Azure settings>
    py dev/alter_add_locate_epcs.py

Safe to re-run: it checks for the column first.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text  # noqa: E402
from app.database import get_engine  # noqa: E402

engine = get_engine()
cols = [c["name"] for c in inspect(engine).get_columns("rfid_locate_queue")]
if "epcs" in cols:
    print("rfid_locate_queue.epcs already exists.")
else:
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE rfid_locate_queue ADD epcs NVARCHAR(MAX) NULL"
        ))
    print("rfid_locate_queue.epcs added.")
print("Done.")
