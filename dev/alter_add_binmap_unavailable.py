"""One-off prod migration: add rfid_bin_map.unavailable.

Shelf math subtracts Shopify's Unavailable bucket from on-hand (Nick,
2026-09-01, W9160A); the bin map snapshot stores the unavailable count
per row so audits can EXPLAIN an over-count. Run ONCE against prod
before deploying:

    set DATABASE_URL=<prod mssql url, from the app's Azure settings>
    py dev/alter_add_binmap_unavailable.py

Safe to re-run: it checks for the column first.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text  # noqa: E402
from app.database import get_engine  # noqa: E402

engine = get_engine()
cols = [c["name"] for c in inspect(engine).get_columns("rfid_bin_map")]
if "unavailable" in cols:
    print("rfid_bin_map.unavailable already exists.")
else:
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE rfid_bin_map ADD unavailable INT NOT NULL "
            "CONSTRAINT DF_binmap_unavail DEFAULT 0"
        ))
    print("rfid_bin_map.unavailable added.")
print("Done.")
