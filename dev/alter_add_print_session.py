"""One-off prod migration: add rfid_print_jobs.print_session.

Scan-station prints stamp one token per product load, so the Print
queue can group all labels printed between barcode resets. sqlite
test/dev databases recreate themselves; Azure SQL does not, so run
this ONCE against prod before deploying:

    set DATABASE_URL=<prod mssql url, from the app's Azure settings>
    py dev/alter_add_print_session.py

Safe to re-run: it checks INFORMATION_SCHEMA first and does nothing
when the column already exists.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app.database import get_engine  # noqa: E402

engine = get_engine()
with engine.begin() as conn:
    exists = conn.execute(text(
        "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_NAME = 'rfid_print_jobs' "
        "AND COLUMN_NAME = 'print_session'"
    )).first()
    if exists:
        print("rfid_print_jobs.print_session already exists - nothing to do.")
    else:
        conn.execute(text(
            "ALTER TABLE rfid_print_jobs ADD print_session VARCHAR(24) NULL"
        ))
        print("Added rfid_print_jobs.print_session (NULL).")
print("Done.")
