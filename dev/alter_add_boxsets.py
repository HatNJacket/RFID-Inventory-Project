"""One-off prod migration: create rfid_boxset_parts (multi-box SETS -
Nick, 2026-09-08, the S11230: boxes with their own barcodes/SKUs sold
only as one full product). Run ONCE against prod before deploying:

    set DATABASE_URL=<prod mssql url, from the app's Azure settings>
    py dev/alter_add_boxsets.py

Safe to re-run: create() with checkfirst does nothing when the table
already exists.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import get_engine  # noqa: E402
from app.models import BoxSetPart  # noqa: E402

engine = get_engine()
BoxSetPart.__table__.create(engine, checkfirst=True)
print("rfid_boxset_parts is present.")
print("Done.")
