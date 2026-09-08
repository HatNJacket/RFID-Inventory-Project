"""One-off prod migration: create the rfid_mislabel_flags table.

Vendor mis-label warnings (Nick, 2026-09-08, the EXOS2CWB5 barcode on
EXOS2CW boxes): a per-product flag that warns on every scan. Run ONCE
against prod before deploying:

    set DATABASE_URL=<prod mssql url, from the app's Azure settings>
    py dev/alter_add_mislabel_flags.py

Safe to re-run: create() with checkfirst does nothing when the table
already exists.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import get_engine  # noqa: E402
from app.models import MislabelFlag  # noqa: E402

engine = get_engine()
MislabelFlag.__table__.create(engine, checkfirst=True)
print("rfid_mislabel_flags is present.")
print("Done.")
