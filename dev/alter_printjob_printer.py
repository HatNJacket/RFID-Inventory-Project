"""One-off prod migration: add rfid_print_jobs.printer (printer picker).

sqlite test/dev databases recreate themselves; Azure SQL does not, so run
this ONCE against prod before deploying the printer-selector feature:

    set DATABASE_URL=<prod mssql url, from the app's Azure settings>
    py dev/alter_printjob_printer.py

Safe to re-run: it checks INFORMATION_SCHEMA first and does nothing when
the column already exists. (rfid_printers is a NEW table, so the app's
normal auto-create handles it — only the added column needs this.)
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
        "WHERE TABLE_NAME = 'rfid_print_jobs' AND COLUMN_NAME = 'printer'"
    )).first()
    if exists:
        print("rfid_print_jobs.printer already exists — nothing to do.")
    else:
        conn.execute(text(
            "ALTER TABLE rfid_print_jobs ADD printer NVARCHAR(100) NULL"
        ))
        print("Added rfid_print_jobs.printer.")
print("Done.")
