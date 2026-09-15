"""Sold detection: fulfilled orders lower the EXPECTED tag count.

The ledger (rfid_sold_ledger) records fulfilled-order lines for tracked
SKUs; expected tags = live on-hand + sold-unretired. The sync files ONE
tag-onhand-mismatch review task per SKU whose numbers don't add up and
closes it again itself; audits get MARK SOLD when a sweep's silence is
fully explained by sales. Shopify is never written to."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_orders_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.main import app
from app.database import get_engine
from app.models import (BarcodeChange, BinMapEntry, ReviewTask,
                        RfidAssignment, SoldRecord)
from app import orders_sync
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

from datetime import datetime, timezone

def tag(s, epc, sku, title, bin_="A1-1", case=None):
    # Tags predate the fixture's sales (Aug 17/18): the mismatch math is
    # windowed to the tag pool's baseline, so a sale only counts when it
    # was fulfilled AFTER tagging (the 2026-08-24 truth rework).
    s.add(RfidAssignment(rfid_id=epc, shopify_variant_id="t:1",
        product_title=title, sku=sku, bin_location=bin_, case_units=case,
        assigned_at=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)))

ORDERS = [
    {"order_id":"gid://shopify/Order/1","name":"#1001","fulfilled_at":"2026-08-17T14:00:00Z",
     "lines":[{"sku":"SCOPE-1","qty":1},{"sku":"UNTRACKED-9","qty":3}]},
    {"order_id":"gid://shopify/Order/2","name":"#1002","fulfilled_at":"2026-08-18T09:00:00Z",
     "lines":[{"sku":"cam-2","qty":2}]},  # lower-case: CI matching
]
ON_HAND = {"SCOPE-1": 2, "CAM-2": 2}

with TestClient(app) as cl:
  with Session(get_engine()) as s:
    # SCOPE-1: 3 tags, on-hand 2, 1 sold -> 3 == 2+1, consistent.
    for e in ("AA01","AA02","AA03"): tag(s,e,"SCOPE-1","Big Scope")
    # CAM-2: 3 tags, on-hand 2, 2 sold -> expected 4 != 3, mismatch.
    for e in ("BB01","BB02","BB03"): tag(s,e,"CAM-2","Astro Cam","B2-2")
    s.add(BinMapEntry(sku="SCOPE-1", product_title="Big Scope",
                      bin="A1-1", qty=2))
    s.commit()

  with patch("app.shopify.get_fulfilled_orders", return_value=ORDERS), \
       patch("app.shopify.get_stock_info_by_skus",
             side_effect=lambda skus: {
                 k: {"on_hand": v, "unavailable": 0, "bin": ""}
                 for k, v in ON_HAND.items()
                 if k in [x.upper() for x in skus]}), \
       patch("app.shopify.get_on_hand_by_skus",
             side_effect=lambda skus: {k:v for k,v in ON_HAND.items()
                                       if k in [x.upper() for x in skus]}):
    r = cl.post("/api/orders-sync/run").json()
    check("run reports ok", r.get("ok") is True, r)
    check("untracked SKU ignored, tracked recorded",
          r.get("recorded")==2, r)
    check("one mismatch task opened", r.get("tasks_opened")==1, r)

    with Session(get_engine()) as s:
        rows = s.scalars(select(SoldRecord)).all()
        check("ledger holds the two tracked lines",
              sorted((x.sku, x.quantity) for x in rows)
              ==[("SCOPE-1",1),("cam-2",2)],
              [(x.sku,x.quantity) for x in rows])

    # Idempotent re-run: same orders, no duplicate rows, no second task.
    r = cl.post("/api/orders-sync/run").json()
    check("re-run records nothing new", r.get("recorded")==0, r)
    with Session(get_engine()) as s:
        check("no duplicate ledger rows",
              len(s.scalars(select(SoldRecord)).all())==2, "")
        open_tasks = s.scalars(select(ReviewTask).where(
            ReviewTask.category=="inventory-check",
            ReviewTask.status=="open")).all()
        check("still exactly one open mismatch task",
              len(open_tasks)==1 and open_tasks[0].sku.upper()=="CAM-2",
              [(t.sku,t.status) for t in open_tasks])
        check("task explains the arithmetic",
              "on-hand 2" in open_tasks[0].detail
              and "2 sold" in open_tasks[0].detail, open_tasks[0].detail)

    # The task closes itself once the world adds up (found a lost tag).
    with Session(get_engine()) as s:
        tag(s,"BB04","CAM-2","Astro Cam","B2-2"); s.commit()
    cl.post("/api/orders-sync/run")
    with Session(get_engine()) as s:
        t = s.scalars(select(ReviewTask).where(
            ReviewTask.category=="inventory-check")).first()
        check("mismatch task auto-resolved when numbers agree",
              t.status=="resolved" and t.resolved_by=="orders-sync", t.status)

    # Bin audit: sweep hears AA01+AA02 but not AA03; 1 sold -> MARK SOLD
    # material: silent_epcs named, sold_unretired attached.
    rep = cl.post("/api/bins/A1-1/check",
                  json={"epcs":["AA01","AA02"]}).json()
    item = next(i for i in rep["items"] if i["sku"]=="SCOPE-1")
    check("silent tag named for the audit",
          item["silent_epcs"]==["AA03"], item)
    check("sold count rides the audit row",
          item["sold_unretired"]==1, item)

    # MARK SOLD: wrong-product refusal, then the real one.
    r = cl.post("/api/assignments/mark-sold",
                json={"sku":"CAM-2","epcs":["AA03"],"changed_by":"Nick"})
    check("refuses tags of another product", r.status_code==409, r.status_code)
    r = cl.post("/api/assignments/mark-sold",
                json={"sku":"SCOPE-1","epcs":["AA03"],"changed_by":"Nick"})
    d = r.json()
    check("mark-sold removes the tag and retires the sale",
          r.status_code==200 and d["removed_tags"]==1
          and d["retired_against_orders"]==1, d)
    with Session(get_engine()) as s:
        check("assignment gone",
              s.scalars(select(RfidAssignment).where(
                  RfidAssignment.rfid_id=="AA03")).first() is None, "")
        bc = s.scalars(select(BarcodeChange).where(
            BarcodeChange.changed_field=="tag-sold")).all()
        check("History row filed as tag-sold",
              len(bc)==1 and bc[0].changed_by=="Nick", [b.changed_field for b in bc])
        sr = s.scalars(select(SoldRecord).where(
            SoldRecord.sku=="SCOPE-1")).first()
        check("ledger row retired", sr.retired==1, sr.retired)
    check("sold ledger now empty for SCOPE-1",
          orders_sync.sold_unretired_map(Session(get_engine()), ["SCOPE-1"])
          == {}, "")

    # History carries the sold story.
    h = cl.get("/api/product-history?term=SCOPE-1").json()
    types = [e["type"] for e in h["events"]]
    check("order-sold event in product history", "order-sold" in types, types)
    check("tag-sold event in product history", "tag-sold" in types, types)

  # Missing scope: fail-soft state, not an exception.
  with patch("app.shopify.get_fulfilled_orders",
             side_effect=RuntimeError("Shopify GraphQL errors: ACCESS_DENIED")):
    r = cl.post("/api/orders-sync/run").json()
    check("missing read_orders is a state, not a crash",
          r.get("waiting_scope") is True and r.get("ok") is False, r)
    st = cl.get("/api/orders-sync/status").json()
    check("status endpoint reports the waiting state",
          st["last_run"]["waiting_scope"] is True, st)


  # The refresh plumbing: marker cleared, duration logged as orders-sync.
  stats = cl.get("/api/refresh-stats").json()
  check("no stuck running marker", "orders-sync" not in stats["running"], stats)
  check("sync durations feed the shared ETA log",
        "orders-sync" in stats["stats"], stats)

# Line-level counting (Nick, 2026-09-15, order #50260: the 8h0045 line
# shipped while a sibling stayed backordered and the old whole-order
# gate hid the sale). get_fulfilled_orders counts SHIPPED units per
# line on both fulfilled and partially fulfilled orders.
from app import shopify as _sh
PAGE = {"orders": {"pageInfo": {"hasNextPage": False, "endCursor": None},
  "nodes": [
    {"id": "gid://o/1", "name": "#1", "displayFulfillmentStatus": "FULFILLED",
     "fulfillments": [{"createdAt": "2026-09-11T14:00:00Z"}],
     "lineItems": {"nodes": [
         {"sku": "A-1", "quantity": 2, "unfulfilledQuantity": 0}]}},
    {"id": "gid://o/2", "name": "#2",
     "displayFulfillmentStatus": "PARTIALLY_FULFILLED",
     "fulfillments": [{"createdAt": "2026-09-11T14:14:31Z"}],
     "lineItems": {"nodes": [
         {"sku": "8h0045", "quantity": 1, "unfulfilledQuantity": 0},
         {"sku": "BACKORDERED", "quantity": 1, "unfulfilledQuantity": 1},
         {"sku": "HALF", "quantity": 4, "unfulfilledQuantity": 3}]}},
    {"id": "gid://o/3", "name": "#3",
     "displayFulfillmentStatus": "ON_HOLD", "fulfillments": [],
     "lineItems": {"nodes": [
         {"sku": "NOPE", "quantity": 1, "unfulfilledQuantity": 0}]}},
  ]}}
seen_search = {}
def fake_q(query, variables):
    seen_search.update(variables)
    return PAGE
with patch("app.shopify.query_shopify", side_effect=fake_q):
    out = _sh.get_fulfilled_orders("2026-09-01T00:00:00Z")
check("the order search includes PARTIAL fulfillment",
      "fulfillment_status:partial" in (seen_search.get("search") or ""),
      seen_search)
by_name = {o["name"]: o for o in out}
check("a fully fulfilled order counts as before",
      by_name["#1"]["lines"] == [{"sku": "A-1", "qty": 2}],
      by_name.get("#1"))
check("a partial order counts its SHIPPED lines only",
      sorted((l["sku"], l["qty"]) for l in by_name["#2"]["lines"])
      == [("8h0045", 1), ("HALF", 1)], by_name.get("#2"))
check("other statuses never count", "#3" not in by_name, list(by_name))

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
