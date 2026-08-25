"""One-off prod migration: add rfid_barcode_aliases.kind.

Label lines saved onto a product now double as lookup aliases (kind
'label', ephemeral - replaced when the line changes); operator-made
links stay kind 'manual'. sqlite test/dev databases recreate
themselves; Azure SQL does not, so run this ONCE against prod before
deploying:

    set DATABASE_URL=<prod mssql url, from the app's Azure settings>
    py dev/alter_add_alias_kind.py

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
        "WHERE TABLE_NAME = 'rfid_barcode_aliases' "
        "AND COLUMN_NAME = 'kind'"
    )).first()
    if exists:
        print("rfid_barcode_aliases.kind already exists - nothing to do.")
    else:
        conn.execute(text(
            "ALTER TABLE rfid_barcode_aliases "
            "ADD kind VARCHAR(20) NOT NULL DEFAULT 'manual'"
        ))
        print("Added rfid_barcode_aliases.kind (default 'manual').")
print("Done.")
