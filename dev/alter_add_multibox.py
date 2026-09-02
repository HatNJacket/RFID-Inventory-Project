"""One-off prod migration: multi-box units (Nick, 2026-09-02, S11740).

Creates rfid_multibox_products (the durable one-unit-several-cartons
mark with per-box bins) and rfid_companion_tags (the live tags on box
2..N - recognized everywhere, counted nowhere), and adds
rfid_print_jobs.kind ("companion" labels register companions instead
of assignments). Run ONCE against prod before deploying:

    set DATABASE_URL=<prod mssql url, from the app's Azure settings>
    py dev/alter_add_multibox.py

Safe to re-run: creates check first, the ALTER checks the column.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text  # noqa: E402
from app.database import get_engine  # noqa: E402
from app.models import CompanionTag, MultiboxProduct  # noqa: E402

engine = get_engine()
MultiboxProduct.__table__.create(engine, checkfirst=True)
print("rfid_multibox_products is present.")
CompanionTag.__table__.create(engine, checkfirst=True)
print("rfid_companion_tags is present.")

cols = [c["name"] for c in inspect(engine).get_columns("rfid_print_jobs")]
if "kind" in cols:
    print("rfid_print_jobs.kind already exists.")
else:
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE rfid_print_jobs ADD kind NVARCHAR(20) NULL"
        ))
    print("rfid_print_jobs.kind added.")
print("Done.")
