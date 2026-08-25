"""One-off prod migration: create the rfid_released_tags table.

Full snapshots of assignments released via History's Assigned Tag undo
(Nick, 2026-08-25), so the release itself can be undone: re-apply
restores every field, original pairing date included. sqlite test/dev
databases recreate themselves; Azure SQL does not, so run this ONCE
against prod before deploying:

    set DATABASE_URL=<prod mssql url, from the app's Azure settings>
    py dev/alter_add_released_tags.py

Safe to re-run: create() with checkfirst does nothing when the table
already exists.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import get_engine  # noqa: E402
from app.models import ReleasedTag  # noqa: E402

engine = get_engine()
ReleasedTag.__table__.create(engine, checkfirst=True)
print("rfid_released_tags is present.")
print("Done.")
