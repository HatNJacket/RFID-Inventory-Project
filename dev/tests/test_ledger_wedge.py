"""The sold ledger can't be wedged by one bad line (2026-10-05): prod's
hourly ShipStation sync failed every hour for three days because a SKU
with a character the varchar column can't hold was stored as "?", never
matched again, re-inserted, and the duplicate rolled back the whole
batch. Two defences: SKUs are matched the way the database spells them,
and each shipment commits on its own so a refused row loses only itself.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_ledger_wedge_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from datetime import datetime, timezone
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
import app.main as M
import app.orders_sync as OS
from app.main import app
from app.database import get_engine
from app.models import SoldRecord, RfidAssignment
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)
def utc(y,m,d,h=12): return datetime(y,m,d,h,0, tzinfo=timezone.utc)
def ship(sid, oid, num, items, created):
    return {"shipment_id": sid, "ss_order_id": oid, "order_number": num,
            "store_id": 6421, "created_at": created, "ship_date": None,
            "voided": False, "items": [{"sku": k, "qty": q} for k, q in items]}

FANCY = "ZWO FD-M54-\u2161"          # the real SKU: a Roman numeral II
STORED = "ZWO FD-M54-?"              # what the varchar column kept
SHIPMENTS = [
    ship(1, 9, "50527", [(FANCY, 1), ("ZWO CH4", 1)], utc(2026,10,2)),
    ship(2, 10, "50528", [("SCOPE-1", 1)], utc(2026,10,3)),
]
ss_patches = dict(
    configured=lambda: True,
    store_kinds=lambda: ({6421}, set()),
    get_shipments_since=lambda since, max_pages=40: SHIPMENTS,
    get_voids_since=lambda since, max_pages=10: [],
    get_order_line_quantities=lambda oid: {},
)

check("the fold is a no-op on sqlite", OS._db_sku(FANCY) == FANCY)
OS._SKU_CODEPAGE = "cp1252"   # behave like the prod column
check("on the prod codepage the SKU folds to the stored spelling",
      OS._db_sku(FANCY) == STORED, OS._db_sku(FANCY))
check("  ...and plain SKUs are untouched", OS._db_sku("ZWO CH4") == "ZWO CH4")

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]):
  M._maybe_refresh_bin_map = lambda *a, **k: False
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        s.add(RfidAssignment(rfid_id="E1", shopify_variant_id="t:1",
              product_title="Scope", sku="SCOPE-1", bin_location="A1-1",
              assigned_at=utc(2026,8,10)))
        # Prod's state: the hour that half-landed before the wedge.
        s.add(SoldRecord(order_id="ss:9", order_name="50527", sku=STORED,
              quantity=1, retired=0, fulfilled_at=utc(2026,10,2),
              source="shipstation", ss_order_id="9", ss_shipments='{"1": 1}'))
        s.add(SoldRecord(order_id="ss:9", order_name="50527", sku="ZWO CH4",
              quantity=1, retired=0, fulfilled_at=utc(2026,10,2),
              source="shipstation", ss_order_id="9", ss_shipments='{"1": 1}'))
        s.commit()

    def run_sync():
        with patch.multiple("app.shipstation", **ss_patches), \
             patch("app.shopify.get_fulfilled_orders", return_value=[]), \
             patch("app.shopify.get_stock_info_by_skus", return_value={}), \
             patch("app.shopify.get_on_hand_by_skus", return_value={}):
            return cl.post("/api/orders-sync/run").json()

    r = run_sync()
    check("the sync runs clean against the stored spelling",
          r.get("ok") is True and not r.get("ss_error")
          and r.get("ss_failed_shipments", 0) == 0, r)
    with Session(get_engine()) as s:
        rows9 = s.scalars(select(SoldRecord).where(SoldRecord.ss_order_id == "9")).all()
        rows10 = s.scalars(select(SoldRecord).where(SoldRecord.ss_order_id == "10")).all()
    check("  ...the folded line matched its row (no duplicate)",
          sorted(r.sku for r in rows9) == sorted([STORED, "ZWO CH4"]), [r.sku for r in rows9])
    check("  ...and the next order landed", len(rows10) == 1 and rows10[0].sku == "SCOPE-1")
    with Session(get_engine()) as s:
        cursor1 = OS._get(s, OS.SS_CURSOR_KEY)
    check("  ...the cursor advanced", bool(cursor1), cursor1)

    # Belt and braces: make the lookup miss on purpose so order 9's line
    # is re-inserted and the database refuses it.
    OS._SKU_CODEPAGE = ""
    with Session(get_engine()) as s:
        OS._set(s, OS.SS_CURSOR_KEY, "2026-10-01T00:00:00Z"); s.commit()
    SHIPMENTS.append(ship(3, 11, "50529", [("SCOPE-1", 2)], utc(2026,10,4)))
    with patch("app.orders_sync._ss_rows_for_order",
               side_effect=lambda session, oid: {}):
        r = run_sync()
    # Orders 9 and 10 are both re-inserted and refused; 11 is new.
    check("refused shipments are skipped, not fatal",
          r.get("ok") is True and not r.get("ss_error")
          and r.get("ss_failed_shipments") == 2, r)
    with Session(get_engine()) as s:
        rows11 = s.scalars(select(SoldRecord).where(SoldRecord.ss_order_id == "11")).all()
        n9 = len(s.scalars(select(SoldRecord).where(SoldRecord.ss_order_id == "9")).all())
        cursor2 = OS._get(s, OS.SS_CURSOR_KEY)
    check("  ...the shipments after it still land", len(rows11) == 1 and rows11[0].quantity == 2, rows11)
    check("  ...the refused order kept its original rows only", n9 == 2, n9)
    check("  ...and the cursor still advanced",
          cursor2 and cursor2 != "2026-10-01T00:00:00Z", cursor2)

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("ALL PASS")
