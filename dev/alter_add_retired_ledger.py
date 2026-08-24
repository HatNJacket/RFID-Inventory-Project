"""One-off prod migration: add rfid_retired_tags.ledger_consumed.

Presumed-sold retirements now consume matching sold-ledger units; this
column records how many landed per tag so undo can hand exactly that
many back. sqlite test/dev databases recreate themselves; Azure SQL does
not, so run this ONCE against prod before deploying the sales-math
rework:

    set DATABASE_URL=<prod mssql url, from the app's Azure settings>
    py dev/alter_add_retired_ledger.py

Safe to re-run: it checks INFORMATION_SCHEMA first and does nothing when
the column already exists. Existing rows get 0 (their retirements never
consumed ledger units, which is historically accurate).
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
        "WHERE TABLE_NAME = 'rfid_retired_tags' "
        "AND COLUMN_NAME = 'ledger_consumed'"
    )).first()
    if exists:
        print("rfid_retired_tags.ledger_consumed already exists "
              "— nothing to do.")
    else:
        conn.execute(text(
            "ALTER TABLE rfid_retired_tags "
            "ADD ledger_consumed INT NOT NULL DEFAULT 0"
        ))
        print("Added rfid_retired_tags.ledger_consumed (default 0).")
print("Done.")
