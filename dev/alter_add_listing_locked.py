"""One-off prod migration: add rfid_batch_items.listing_locked.

USE THIS LISTING (the reassign endpoint) now records the operator's
explicit choice so the "ambiguous" multi-listing flag stops re-raising
for that row. sqlite test/dev databases recreate themselves; Azure SQL
does not, so run this ONCE against prod before deploying:

    set DATABASE_URL=<prod mssql url, from the app's Azure settings>
    py dev/alter_add_listing_locked.py

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
        "AND COLUMN_NAME = 'listing_locked'"
    )).first()
    if exists:
        print("rfid_batch_items.listing_locked already exists "
              "— nothing to do.")
    else:
        conn.execute(text(
            "ALTER TABLE rfid_batch_items "
            "ADD listing_locked BIT NOT NULL DEFAULT 0"
        ))
        print("Added rfid_batch_items.listing_locked (default 0).")
print("Done.")
