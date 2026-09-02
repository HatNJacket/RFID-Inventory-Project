"""One-off prod migration: create the rfid_sort_handoffs table.

C72 sort-a-shipment passes handed to the web terminal (Nick,
2026-09-02): the gun's counted codes travel here, the web sorter picks
them up pre-filled with its label-match tooling. Run ONCE against prod
before deploying:

    set DATABASE_URL=<prod mssql url, from the app's Azure settings>
    py dev/alter_add_sort_handoff.py

Safe to re-run: create() with checkfirst does nothing when the table
already exists.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import get_engine  # noqa: E402
from app.models import SortHandoff  # noqa: E402

engine = get_engine()
SortHandoff.__table__.create(engine, checkfirst=True)
print("rfid_sort_handoffs is present.")
print("Done.")
