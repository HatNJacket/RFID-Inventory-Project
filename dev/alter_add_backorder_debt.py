"""One-off prod migration: create the rfid_backorder_debt table.

Units Shopify's on-hand runs behind the shelf because stock went
negative before a delivery arrived (Nick, 2026-08-26, the AirGradient
case). sqlite test/dev databases recreate themselves; Azure SQL does
not, so run this ONCE against prod before deploying:

    set DATABASE_URL=<prod mssql url, from the app's Azure settings>
    py dev/alter_add_backorder_debt.py

Safe to re-run: create() with checkfirst does nothing when the table
already exists.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import get_engine  # noqa: E402
from app.models import BackorderDebt  # noqa: E402

engine = get_engine()
BackorderDebt.__table__.create(engine, checkfirst=True)
print("rfid_backorder_debt is present.")
print("Done.")
