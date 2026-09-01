"""Receive entire shipment (Nick, 2026-09-01): load a whole TC-Planner
stock order into one receiving batch, print every remaining label,
settle, sweep the unused-label strip into a vendor HELD list, hand the
paired counts to the planner - and the 1-hour stock-update watchdog.
Held labels count in nothing and get consumed when they finally pair."""
import os, sys, tempfile
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_fullship_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_engine
from app.models import (BinMapEntry, HeldLabelItem, HeldLabelList,
                        OrderReceipt, RfidAssignment)
from sqlalchemy.orm import Session
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

ORDER = {"order_id": 77, "reference_number": "948", "vendor": "ZWO",
         "status": "open", "expected_date": "2026-09-07"}
LINES = [
    {"sku": "GOOD-1", "barcode": "501", "title": "Good One",
     "ordered": 5, "received": 2, "remaining": 3},
    {"sku": "GOOD-2", "barcode": "502", "title": "Good Two",
     "ordered": 2, "received": 0, "remaining": 2},
    {"sku": "MISSING-X", "barcode": None, "title": "Ghost Product",
     "ordered": 1, "received": 0, "remaining": 1},
]

ORDER2 = {"order_id": 78, "reference_number": "946", "vendor": "Antares",
          "status": "open", "expected_date": "2026-09-02"}
LINES2 = [{"sku": "CLEAN-1", "barcode": "503", "title": "Clean One",
           "ordered": 2, "received": 0, "remaining": 2}]

def fake_order_lines(ref, operator=None):
    key = (ref or "").strip().upper().replace("SO", "").strip()
    if key == "948":
        return {"configured": True, "ok": True, "order": ORDER,
                "items": [dict(x) for x in LINES]}
    if key == "946":
        return {"configured": True, "ok": True, "order": ORDER2,
                "items": [dict(x) for x in LINES2]}
    return {"configured": True, "ok": True, "order": None, "items": []}

def fake_open_orders(operator=None):
    return {"configured": True, "ok": True, "orders": [dict(ORDER)]}

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.planner.order_lines", side_effect=fake_order_lines), \
     patch("app.planner.open_orders", side_effect=fake_open_orders), \
     patch("app.main._maybe_refresh_bin_map", return_value=False):
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="GOOD-1", barcode="501",
                          product_title="Good One", bin="A1-1", qty=2,
                          shopify_variant_id="t:G1"))
        s.add(BinMapEntry(sku="GOOD-2", barcode="502",
                          product_title="Good Two", bin="A1-2", qty=0,
                          shopify_variant_id="t:G2"))
        s.add(BinMapEntry(sku="CLEAN-1", barcode="503",
                          product_title="Clean One", bin="A1-3", qty=0,
                          shopify_variant_id="t:C1"))
        s.commit()

    # ---- preview -----------------------------------------------------
    r = cl.get("/api/receiving/orders/SO 948")
    body = r.json()
    flags = {i["sku"]: i.get("flag") for i in body["items"]}
    check("preview resolves lines and flags the unknown one",
          r.status_code == 200 and flags["GOOD-1"] is None
          and flags["GOOD-2"] is None
          and "unknown" in (flags["MISSING-X"] or ""), r.text[:300])
    r = cl.get("/api/receiving/orders/123")
    check("an unknown order 404s", r.status_code == 404, r.status_code)

    # ---- create ------------------------------------------------------
    # A live same-vendor planner batch exists - the full shipment must
    # NOT merge into it (its settle step reads the whole batch as one
    # order's story).
    r = cl.post("/api/receiving/prints",
                json={"items": [{"sku": "GOOD-1", "quantity": 1}],
                      "requested_by": "Nick",
                      "reference": "SO 111 · ZWO"})
    planner_bid = r.json()["batch"]["id"]

    r = cl.post("/api/receiving/full-shipment",
                json={"order": "948", "requested_by": "Nick"})
    body = r.json()
    check("full shipment queues remaining labels (3+2), flags ghost",
          r.status_code == 201 and body["queued"] == 5
          and body["reused"] is False
          and len(body["skipped_unknown"]) == 1, r.text[:300])
    check("full shipment gets its OWN batch, never the vendor merge",
          body["batch"]["id"] != planner_bid
          and body["batch"]["created_by"].startswith("Full shipment ·"),
          str(body["batch"])[:200])
    bid = body["batch"]["id"]
    with Session(get_engine()) as s:
        rec = s.query(OrderReceipt).one()
    check("receipt tracks the order and batch",
          rec.stock_order_id == 77 and rec.batch_id == bid
          and rec.printed_at is not None and rec.settled_at is None,
          str(rec.as_dict()))

    r = cl.post("/api/receiving/full-shipment", json={"order": "948"})
    check("second press reuses the live batch",
          r.status_code == 201 and r.json()["reused"] is True
          and r.json()["batch"]["id"] == bid, r.text[:200])

    r = cl.get(f"/api/batches/{bid}")
    b = r.json()
    check("batch payload carries the order receipt",
          b["batch"]["order_receipt"]
          and b["batch"]["order_receipt"]["stock_order_id"] == 77,
          str(b["batch"].get("order_receipt")))
    items = {i["sku"]: i for i in b["items"] if i.get("sku")}

    # ---- pair what arrived: all 3 GOOD-1, 1 of 2 GOOD-2 --------------
    for n, epc in enumerate(["E100000000000000000000A1",
                             "E100000000000000000000A2",
                             "E100000000000000000000A3"]):
        cl.post(f"/api/batches/{bid}/pair",
                json={"epc": epc, "item_id": items["GOOD-1"]["id"],
                      "created_by": "C72"})
    cl.post(f"/api/batches/{bid}/pair",
            json={"epc": "E100000000000000000000B1",
                  "item_id": items["GOOD-2"]["id"], "created_by": "C72"})

    # ---- settle: the 1h clock starts, unpaired counts answer ---------
    r = cl.post(f"/api/batches/{bid}/settle-shipment",
                json={"created_by": "Nick"})
    body = r.json()
    check("settle stamps the clock and counts the leftover strip",
          r.status_code == 200
          and body["receipt"]["settled_at"] is not None
          and body["total_unpaired"] == 1
          and body["unpaired"][0]["sku"] == "GOOD-2",
          r.text[:300])
    check("planner hand-off carries only what physically arrived",
          {i["sku"]: i["qty"] for i in body["planner"]["items"]}
          == {"GOOD-1": 3, "GOOD-2": 1}
          and body["planner"]["order_id"] == 77,
          str(body["planner"]))

    # ---- held list: strip sweep, strays excluded, batch closes -------
    r = cl.post(f"/api/batches/{bid}/held-list",
                json={"epcs": ["HELD00000000000000000001",
                               "E100000000000000000000A1"],
                      "created_by": "Nick"})
    body = r.json()
    check("held list keeps the pool, excludes the real box's tag",
          r.status_code == 201 and body["pool_count"] == 1
          and body["excluded_assigned"] == 1
          and body["total_unpaired"] == 1, r.text[:300])
    r = cl.get(f"/api/batches/{bid}")
    check("the batch closes with the strip",
          r.json()["batch"]["status"] == "done", r.text[:150])
    with Session(get_engine()) as s:
        hl = s.query(HeldLabelList).one()
        hi = s.query(HeldLabelItem).one()
    check("strip records vendor, order and per-SKU count",
          hl.vendor == "ZWO" and hl.reference == "SO 948"
          and hi.sku == "GOOD-2" and hi.count == 1,
          f"{hl.as_dict()} {hi.as_dict()}")

    # ---- done shipments refuse a re-run ------------------------------
    r = cl.post("/api/receiving/full-shipment", json={"order": "948"})
    check("a done full-shipment receive refuses a twin",
          r.status_code == 409, r.status_code)

    # ---- held-aware printing everywhere ------------------------------
    r = cl.post("/api/receiving/prints",
                json={"items": [{"sku": "GOOD-2", "quantity": 2}],
                      "requested_by": "Nick",
                      "reference": "SO 999 · ZWO"})
    body = r.json()
    check("a later receive prints one FEWER and names the strip",
          r.status_code == 201 and body["queued"] == 1
          and body["held_notes"]
          and "ZWO strip" in body["held_notes"][0]
          and "take 1" in body["held_notes"][0], r.text[:300])

    # ---- pairing the held label consumes it --------------------------
    r = cl.post("/api/rfid-assignments", json={
        "rfid_id": "HELD00000000000000000001",
        "shopify_variant_id": "t:G2", "product_title": "Good Two",
        "sku": "GOOD-2", "bin_location": "A1-2", "assigned_by": "C72"})
    check("the held label pairs like any tag", r.status_code == 201,
          r.text[:150])
    with Session(get_engine()) as s:
        hl = s.query(HeldLabelList).one()
        hi = s.query(HeldLabelItem).one()
    check("pairing empties the pool and the per-SKU count",
          hi.count == 0 and hl.epc_set() == set(),
          f"count={hi.count} pool={hl.epc_set()}")

    # ---- the 1-hour watchdog -----------------------------------------
    with Session(get_engine()) as s:
        rec = s.query(OrderReceipt).one()
        rec.settled_at = datetime.utcnow() - timedelta(hours=2)
        rec.stock_updated_at = None
        s.commit()
    r = cl.get("/api/review-tasks")
    tasks = [t for t in r.json()["tasks"]
             if t.get("category") == "stock-not-updated"]
    check("the watchdog files ONE task after an hour",
          len(tasks) == 1 and "948" in tasks[0]["product_title"]
          and "TC-Planner" in tasks[0]["detail"], str(tasks)[:300])
    r = cl.get("/api/review-tasks")
    tasks2 = [t for t in r.json()["tasks"]
              if t.get("category") == "stock-not-updated"]
    check("re-reading the inbox never duplicates it",
          len(tasks2) == 1, len(tasks2))

    # ---- planner's stock-update ping resolves it ---------------------
    r = cl.post("/api/receiving/stock-updated",
                json={"stock_order_id": 77, "updated_by": "planner"})
    check("stock-updated stamps and closes the task",
          r.status_code == 200 and r.json()["tasks_closed"] == 1,
          r.text[:200])
    r = cl.get("/api/review-tasks?status=all")
    t = next(t for t in r.json()["tasks"]
             if t.get("category") == "stock-not-updated")
    check("the closure is an auto-close by the planner update",
          t["status"] == "resolved"
          and t["resolved_by"] == "planner-update", str(t)[:200])

    # ---- order-status (the planner's gray-out feed) ------------------
    r = cl.get("/api/receiving/order-status/77")
    body = r.json()
    check("order-status says printed with the paired hand-off",
          body["printed"] is True
          and body["receipt"]["stock_updated_at"] is not None
          and {i["sku"]: i["qty"] for i in body["planner"]["items"]}
          == {"GOOD-1": 3, "GOOD-2": 1}, r.text[:300])
    r = cl.get("/api/receiving/order-status/12345")
    check("unknown orders answer printed:false",
          r.json() == {"printed": False}, r.text[:100])

    # ---- vendor nicknames (Nick, 2026-09-01) -------------------------
    r = cl.post("/api/barcode-aliases",
                json={"alias_barcode": "Collimating Eyepiece For Newt",
                      "target": "GOOD-1", "kind": "nickname",
                      "created_by": "Nick"})
    check("vendor nickname saves as a nickname alias",
          r.status_code == 201
          and r.json()["alias"]["kind"] == "nickname", r.text[:200])
    r = cl.get("/api/products/by-barcode/collimating eyepiece for newt")
    check("typing the vendor's name finds the product",
          r.status_code == 200 and r.json()["sku"] == "GOOD-1",
          r.text[:200])
    # One per product: a new nickname replaces the old.
    r = cl.post("/api/barcode-aliases",
                json={"alias_barcode": "Newtonian Collimator Box",
                      "target": "GOOD-1", "kind": "nickname"})
    check("saving a new nickname replaces the old",
          r.status_code == 201, r.text[:150])
    from app.models import BarcodeAlias
    with Session(get_engine()) as s:
        nicks = s.query(BarcodeAlias).filter(
            BarcodeAlias.kind == "nickname").all()
    check("only ONE nickname row remains",
          len(nicks) == 1
          and nicks[0].alias_barcode == "Newtonian Collimator Box",
          str([n.alias_barcode for n in nicks]))
    r = cl.get(f"/api/batches/{bid}")
    it = next(i for i in r.json()["items"] if i.get("sku") == "GOOD-1")
    check("receiving items carry the nickname",
          it["nickname"] == "Newtonian Collimator Box",
          str(it.get("nickname")))
    r = cl.get("/api/receiving/orders/948")
    ln = next(l for l in r.json()["items"] if l["sku"] == "GOOD-1")
    check("the full-shipment preview carries it too",
          ln.get("nickname") == "Newtonian Collimator Box",
          str(ln)[:200])

    # ---- SO 946: everything arrives, batch auto-closes ---------------
    # (Nick, 2026-09-01: the auto-close beat the settle button, so the
    # planner hand-off vanished. Auto-close now settles the receipt -
    # the 1h clock starts at completion - and settle/held-list still
    # work on the done batch for the Continue-to-planner path.)
    r = cl.post("/api/receiving/full-shipment",
                json={"order": "946", "requested_by": "Nick"})
    bid2 = r.json()["batch"]["id"]
    r = cl.get(f"/api/batches/{bid2}")
    it2 = next(i for i in r.json()["items"] if i.get("sku") == "CLEAN-1")
    for epc in ["E200000000000000000000C1", "E200000000000000000000C2"]:
        r = cl.post(f"/api/batches/{bid2}/pair",
                    json={"epc": epc, "item_id": it2["id"],
                          "created_by": "C72"})
    check("fully-arrived shipment auto-closes on the last pair",
          r.json()["receiving_done"] is True, r.text[:200])
    with Session(get_engine()) as s:
        rec2 = s.query(OrderReceipt).filter(
            OrderReceipt.stock_order_id == 78).one()
    check("auto-close settles the receipt (the 1h clock starts)",
          rec2.settled_at is not None, str(rec2.as_dict()))
    r = cl.post(f"/api/batches/{bid2}/settle-shipment",
                json={"created_by": "Nick"})
    check("settle still answers on the done batch (0 unpaired)",
          r.status_code == 200 and r.json()["total_unpaired"] == 0
          and {i["sku"]: i["qty"] for i in r.json()["planner"]["items"]}
          == {"CLEAN-1": 2}, r.text[:250])
    r = cl.post(f"/api/batches/{bid2}/held-list",
                json={"epcs": [], "created_by": "Nick"})
    check("finishing the done batch holds nothing and hands off",
          r.status_code == 201 and r.json()["total_unpaired"] == 0
          and r.json()["list"] is None, r.text[:200])

    # ---- the planner's unprinted relay is a no-op for full shipments -
    # (Nick, 2026-09-01: the planner frontend doesn't hold RFID-printed
    # line ids, so it relayed them as unprinted - spurious task +
    # duplicate label-less batch. The reference carries the planner's
    # internal order id.)
    from app.models import Batch as _B
    with Session(get_engine()) as s:
        batches_before = s.query(_B).count()
    r = cl.post("/api/receiving/unprinted",
                json={"items": [{"sku": "CLEAN-1", "quantity": 2}],
                      "requested_by": "planner",
                      "reference": "SO 78 · Antares"})
    check("unprinted relay answers nothing-owed for a full shipment",
          r.status_code == 201 and r.json()["task_id"] is None
          and r.json()["batch"] is None
          and "already printed and paired" in r.json()["message"],
          r.text[:250])
    with Session(get_engine()) as s:
        batches_after = s.query(_B).count()
    check("no duplicate batch was booked", batches_after == batches_before,
          f"{batches_before} -> {batches_after}")
    r = cl.get("/api/review-tasks")
    check("no labels-not-printed task filed",
          not [t for t in r.json()["tasks"]
               if t.get("category") == "labels-not-printed"],
          str([t.get("category") for t in r.json()["tasks"]])[:200])

    # ---- the picker annotates printed orders -------------------------
    r = cl.get("/api/receiving/orders")
    o = r.json()["orders"][0]
    check("open-orders picker flags the printed order",
          o["already_printed"] is True and o["batch_id"] == bid,
          str(o))

print()
print(f"{'FAIL' if fails else 'OK'}  {len(fails)} failing")
sys.exit(1 if fails else 0)
