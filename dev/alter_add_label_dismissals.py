"""One-off prod migration: create the rfid_label_dismissals table.

Dismissed "printed label answered but never paired" warnings (Nick,
2026-09-01): the EPC stops resurfacing on every audit sweep. Run ONCE
against prod before deploying:

    set DATABASE_URL=<prod mssql url, from the app's Azure settings>
    py dev/alter_add_label_dismissals.py

Safe to re-run: create() with checkfirst does nothing when the table
already exists.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import get_engine  # noqa: E402
from app.models import LabelDismissal  # noqa: E402

engine = get_engine()
LabelDismissal.__table__.create(engine, checkfirst=True)
print("rfid_label_dismissals is present.")
print("Done.")
