"""ShipStation-fed sold ledger (Nick, 2026-09-23): shipments are the
PRIMARY outflow source, Shopify on-hand stays the stock source, and the
two feeds may never double-record a sale.

Covers: new rows from labels, adoption of Shopify-feed rows (retired
count kept), multi-parcel merge capped at the order line (reprint
overlap can't double-count), void subtraction (may lower, floors at
retired), manual-store orders recorded, voided/rate-browser shipments
skipped, first-run backfill pre-settling history, Shopify-feed dedupe +
gap-filling, and the mismatch arithmetic riding the new rows."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_shipstation_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from datetime import datetime, timezone
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.main import app
from app.database import get_engine
from app.models import ReviewTask, RfidAssignment, SoldRecord
from app import orders_sync
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

def utc(y,m,d,h=12): return datetime(y,m,d,h,0, tzinfo=timezone.utc)

def tag(s, epc, sku, title):
    s.add(RfidAssignment(rfid_id=epc, shopify_variant_id="t:1",
        product_title=title, sku=sku, bin_location="A1-1",
        assigned_at=utc(2026,8,10)))

def ship(sid, oid, num, store, items, created, voided=False):
    return {"shipment_id": sid, "ss_order_id": oid, "order_number": num,
            "store_id": store, "created_at": created, "ship_date": None,
            "voided": voided,
            "items": [{"sku": k, "qty": q} for k, q in items]}

SHIPMENTS = [
    # backfill history: before SCOPE-1's oldest pairing -> pre-settled
    ship(9000, 110, "1000", 6421, [("SCOPE-1",1)], utc(2026,8,1)),
    ship(9001, 111, "1001", 6421, [("SCOPE-1",1)], utc(2026,9,20)),
    # adopts the Shopify-feed row for the same order line
    ship(9002, 222, "2002", 6421, [("CAM-2",2)], utc(2026,9,20)),
    # a real split: 2 units across 2 parcels, line qty 2
    ship(9003, 333, "3003", 6421, [("TRIPOD-3",1)], utc(2026,9,20)),
    ship(9004, 333, "3003", 6421, [("TRIPOD-3",1)], utc(2026,9,21)),
    # a reprint: BOTH labels list the full 2 units -> cap at line qty
    ship(9005, 444, "4004", 6421, [("MOUNT-4",2)], utc(2026,9,20)),
    ship(9006, 444, "4004", 6421, [("MOUNT-4",2)], utc(2026,9,21)),
    # manual (non-Shopify) store order: Shopify never sees this sale
    ship(9007, 555, "M77", 6420, [("LENS-5",1)], utc(2026,9,20)),
    # voided in the main feed -> skipped
    ship(9008, 666, "6006", 6421, [("SCOPE-1",5)], utc(2026,9,20), voided=True),
    # rate-browser store -> excluded
    ship(9009, 777, "7007", 56792, [("SCOPE-1",5)], utc(2026,9,20)),
]
LINE_QTYS = {333: {"TRIPOD-3": 2}, 444: {"MOUNT-4": 2}}
line_calls = []
def fake_lines(oid):
    line_calls.append(oid)
    return LINE_QTYS[oid]
VOIDS = {"rows": []}
ORDERS = [
    {"order_id":"gid://shopify/Order/111","name":"#1001",
     "fulfilled_at":"2026-09-20T15:00:00Z","lines":[{"sku":"SCOPE-1","qty":1}]},
    {"order_id":"gid://shopify/Order/222","name":"#2002",
     "fulfilled_at":"2026-09-19T15:00:00Z","lines":[{"sku":"CAM-2","qty":2}]},
    # fulfilled WITHOUT a ShipStation label (pickup) -> the gap-filler
    {"order_id":"gid://shopify/Order/888","name":"#8008",
     "fulfilled_at":"2026-09-20T15:00:00Z","lines":[{"sku":"PICKUP-8","qty":1}]},
]
ON_HAND = {"SCOPE-1":1,"CAM-2":2,"TRIPOD-3":2,"MOUNT-4":2,"LENS-5":2,
           "PICKUP-8":1}

with TestClient(app) as cl:
  with Session(get_engine()) as s:
    for e in ("AA1","AA2"): tag(s,e,"SCOPE-1","Big Scope")
    for e in ("BB1","BB2","BB3"): tag(s,e,"CAM-2","Astro Cam")
    for e in ("CC1","CC2","CC3","CC4"): tag(s,e,"TRIPOD-3","Tripod")
    for e in ("DD1","DD2","DD3","DD4"): tag(s,e,"MOUNT-4","Mount")
    for e in ("EE1","EE2"): tag(s,e,"LENS-5","Lens")
    for e in ("FF1","FF2"): tag(s,e,"PICKUP-8","Pickup Thing")
    # The Shopify feed recorded order 2002 before ShipStation was wired
    # in, and an audit already retired one of its units.
    s.add(SoldRecord(order_id="gid://shopify/Order/222", order_name="#2002",
                     sku="CAM-2", quantity=2, retired=1,
                     fulfilled_at=utc(2026,9,19,15)))
    s.commit()

  ss_patches = dict(
      configured=lambda: True,
      store_kinds=lambda: ({6421}, {56792}),
      get_shipments_since=lambda since, max_pages=40: SHIPMENTS,
      get_voids_since=lambda since, max_pages=10: VOIDS["rows"],
      get_order_line_quantities=fake_lines,
  )
  def run_sync():
    with patch.multiple("app.shipstation", **ss_patches), \
         patch("app.shopify.get_fulfilled_orders", return_value=ORDERS), \
         patch("app.shopify.get_stock_info_by_skus",
               side_effect=lambda skus: {
                   k: {"on_hand": v, "unavailable": 0, "bin": ""}
                   for k, v in ON_HAND.items()
                   if k in [x.upper() for x in skus]}), \
         patch("app.shopify.get_on_hand_by_skus",
               side_effect=lambda skus: {k:v for k,v in ON_HAND.items()
                                         if k in [x.upper() for x in skus]}):
        return cl.post("/api/orders-sync/run").json()

  # ---- first run: backfill + both feeds --------------------------------
  r = run_sync()
  check("run reports ok", r.get("ok") is True, r)
  check("shipments were seen", r.get("ss_shipments_seen")==10, r)
  check("voided + rate-browser shipments skipped",
        r.get("ss_voided_skipped")==1, r)
  check("one Shopify-feed row adopted (not duplicated)",
        r.get("ss_adopted")==1, r)
  check("backfill pre-settled the pre-tag-era sale",
        r.get("ss_backfilled_settled")==1, r)
  check("the pickup order still lands via the Shopify feed",
        r.get("recorded")==1, r)
  check("the Shopify feed skipped the label-covered line",
        r.get("shopify_lines_covered_by_ss")==1, r)
  with Session(get_engine()) as s:
    rows = {(x.order_name, x.sku.upper()): x
            for x in s.scalars(select(SoldRecord)).all()}
    check("one ledger row per order+SKU",
          len(rows)==7, sorted(rows))
    r0 = rows.get(("1000","SCOPE-1"))
    check("history row arrived settled (retired == quantity)",
          r0 is not None and r0.quantity==1 and r0.retired==1,
          r0 and (r0.quantity, r0.retired))
    r1 = rows.get(("1001","SCOPE-1"))
    check("recent label is a live unretired sale from shipstation",
          r1 is not None and r1.quantity==1 and r1.retired==0
          and r1.source=="shipstation", r1 and r1.as_dict())
    r2 = rows.get(("#2002","CAM-2"))
    check("adoption kept the row, its name and its retired count",
          r2 is not None and r2.ss_order_id=="222" and r2.retired==1
          and r2.quantity==2 and r2.source=="shipstation",
          r2 and r2.as_dict())
    r3 = rows.get(("3003","TRIPOD-3"))
    check("split parcels merged to one row of 2",
          r3 is not None and r3.quantity==2, r3 and r3.as_dict())
    r4 = rows.get(("4004","MOUNT-4"))
    check("reprint overlap capped at the order line (2, not 4)",
          r4 is not None and r4.quantity==2 and r4.ss_line_qty==2,
          r4 and r4.as_dict())
    check("order lines fetched once per multi-parcel order",
          sorted(line_calls)==[333,444], line_calls)
    r5 = rows.get(("M77","LENS-5"))
    check("manual-store sale recorded as ss-manual",
          r5 is not None and r5.source=="ss-manual" and r5.quantity==1,
          r5 and r5.as_dict())
    r8 = rows.get(("#8008","PICKUP-8"))
    check("gap-filler row is Shopify-sourced",
          r8 is not None and r8.source=="shopify", r8 and r8.as_dict())
    task = s.scalars(select(ReviewTask).where(
        ReviewTask.category=="inventory-check",
        ReviewTask.status=="open")).all()
    check("exactly the manual-store SKU mismatches (Shopify on-hand "
          "never dropped): tags 2 vs expected 3",
          len(task)==1 and task[0].sku=="LENS-5"
          and "1 sold or shipped" in task[0].detail,
          [(t.sku, t.detail) for t in task])

  # ---- second run: nothing doubles -------------------------------------
  line_calls.clear()
  r = run_sync()
  with Session(get_engine()) as s:
    all_rows = s.scalars(select(SoldRecord)).all()
    check("re-run adds no rows", len(all_rows)==7,
          [(x.order_name,x.sku,x.quantity) for x in all_rows])
    check("re-run moves no quantities",
          sorted((x.order_name, x.sku.upper(), x.quantity)
                 for x in all_rows)
          == sorted([("1000","SCOPE-1",1),("1001","SCOPE-1",1),
                     ("#2002","CAM-2",2),("3003","TRIPOD-3",2),
                     ("4004","MOUNT-4",2),("M77","LENS-5",1),
                     ("#8008","PICKUP-8",1)]),
          sorted((x.order_name, x.sku.upper(), x.quantity)
                 for x in all_rows))
    check("no line refetch for already-capped orders", line_calls==[],
          line_calls)

  # ---- a label gets voided after it was recorded -----------------------
  VOIDS["rows"] = [ship(9004, 333, "3003", 6421, [("TRIPOD-3",1)],
                        utc(2026,9,21), voided=True)]
  r = run_sync()
  check("void applied", r.get("ss_voids_applied")==1, r)
  with Session(get_engine()) as s:
    r3 = s.scalars(select(SoldRecord).where(
        SoldRecord.sku=="TRIPOD-3")).first()
    check("voided parcel's unit came back off the ledger",
          r3.quantity==1, r3.as_dict())
    # retire the remaining unit, then void the other parcel too: the
    # quantity floors at what audits already retired.
    r3.retired = 1; s.commit()
  VOIDS["rows"] = [ship(9003, 333, "3003", 6421, [("TRIPOD-3",1)],
                        utc(2026,9,20), voided=True),
                   ship(9004, 333, "3003", 6421, [("TRIPOD-3",1)],
                        utc(2026,9,21), voided=True)]
  r = run_sync()
  with Session(get_engine()) as s:
    r3 = s.scalars(select(SoldRecord).where(
        SoldRecord.sku=="TRIPOD-3")).first()
    check("void never lowers below the retired count",
          r3 is not None and r3.quantity==1 and r3.retired==1,
          r3 and r3.as_dict())

  # ---- unconfigured = the old world, untouched --------------------------
  with patch("app.shipstation.configured", return_value=False), \
       patch("app.shopify.get_fulfilled_orders", return_value=[]), \
       patch("app.shopify.get_stock_info_by_skus", return_value={}), \
       patch("app.shopify.get_on_hand_by_skus", return_value={}):
    r = cl.post("/api/orders-sync/run").json()
    check("no creds is a state, not an error",
          r.get("shipstation")=="not configured" and r.get("ok") is True, r)

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("ALL PASS")
