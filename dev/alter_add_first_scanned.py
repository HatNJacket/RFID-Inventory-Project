"""One-off prod migration: add rfid_batch_items.first_scanned_at.

Records when a batch row was FIRST physically scanned, so labels queue
in the operator's walking order instead of the seeded (alphabetical)
order. sqlite test/dev databases recreate themselves; Azure SQL does
not, so run this ONCE against prod before deploying:

    set DATABASE_URL=<prod mssql url, from the app's Azure settings>
    py dev/alter_add_first_scanned.py

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
        "WHERE TABLE_NAME = 'rfid_batch_items' "
        "AND COLUMN_NAME = 'first_scanned_at'"
    )).first()
    if exists:
        print("rfid_batch_items.first_scanned_at already exists "
              "- nothing to do.")
    else:
        conn.execute(text(
            "ALTER TABLE rfid_batch_items "
            "ADD first_scanned_at DATETIMEOFFSET NULL"
        ))
        print("Added rfid_batch_items.first_scanned_at (NULL).")
print("Done.")
