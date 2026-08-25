"""One-off prod migration: create the rfid_non_taggable table.

Products not worth individual tags (bins of thumbscrews, dew-heater
straps): never seeded into batches, never labelled, skipped by audits.
sqlite test/dev databases recreate themselves; Azure SQL does not, so
run this ONCE against prod before deploying:

    set DATABASE_URL=<prod mssql url, from the app's Azure settings>
    py dev/alter_add_non_taggable.py

Safe to re-run: create() with checkfirst does nothing when the table
already exists.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import get_engine  # noqa: E402
from app.models import NonTaggable  # noqa: E402

engine = get_engine()
NonTaggable.__table__.create(engine, checkfirst=True)
print("rfid_non_taggable is present.")
print("Done.")
