"""One-off prod migration: the "Receive entire shipment" tables.

rfid_order_receipts  - lifecycle of a full-shipment receive (printed /
                       settled / stock-updated; drives the 1h watchdog)
rfid_held_lists      - strips of printed-but-unpaired labels kept in
                       vendor containers (EPC pool per strip)
rfid_held_items      - per-SKU unused-label counts on each strip

sqlite test/dev databases recreate themselves; Azure SQL does not, so
run this ONCE against prod before deploying:

    set DATABASE_URL=<prod mssql url, from the app's Azure settings>
    py dev/alter_add_order_receipts.py

Safe to re-run: create() with checkfirst does nothing when a table
already exists.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import get_engine  # noqa: E402
from app.models import HeldLabelItem, HeldLabelList, OrderReceipt  # noqa: E402

engine = get_engine()
for model in (OrderReceipt, HeldLabelList, HeldLabelItem):
    model.__table__.create(engine, checkfirst=True)
    print(f"{model.__tablename__} is present.")
print("Done.")
