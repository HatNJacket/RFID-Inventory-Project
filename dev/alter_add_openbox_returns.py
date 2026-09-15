"""One-off prod migration: create the rfid_openbox_returns table.

Open-box return watches (Nick, 2026-09-15): a sold, RFID-tagged product
that came back and now sells as its -O twin, while its old presumed-sold
tag is still somewhere on the packaging. sqlite test/dev databases
recreate themselves; Azure SQL does not, so run this ONCE against prod
before deploying:

    set DATABASE_URL=<prod mssql url, from the app's Azure settings>
    py dev/alter_add_openbox_returns.py

Safe to re-run: create() with checkfirst does nothing when the table
already exists.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import get_engine  # noqa: E402
from app.models import OpenboxReturn  # noqa: E402

engine = get_engine()
OpenboxReturn.__table__.create(engine, checkfirst=True)
print("rfid_openbox_returns is present.")
print("Done.")
