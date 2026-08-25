"""Print fixes (Nick, 2026-08-25):
- /api/print-jobs fills label content from the SAVED label store when
  the client sends none - a Scan Station print used to ignore a freshly
  edited SKU line (the ZWO Softbag1 case);
- /api/products/tags carries the saved label lines + on-hand for the
  card's preview;
- batch labels queue in the operator's WALKING order (first-scanned
  first), not the seeded alphabetical order;
- printer commands (re-align feed) queue and claim once."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_printorder_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_engine
from app.models import BinMapEntry, PrintJob
from sqlalchemy.orm import Session
from sqlalchemy import select
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

CAT = {
    "701": {"shopify_variant_id":"t:ALPHA","shopify_product_id":"g:1",
            "product_title":"Alpha Adapter","variant_title":None,
            "sku":"ALPHA-1","barcode":"701","bin_location":"P1-1"},
    "702": {"shopify_variant_id":"t:BRAVO","shopify_product_id":"g:2",
            "product_title":"Bravo Barlow","variant_title":None,
            "sku":"BRAVO-1","barcode":"702","bin_location":"P1-1"},
    "703": {"shopify_variant_id":"t:CHARLIE","shopify_product_id":"g:3",
            "product_title":"Charlie Cap","variant_title":None,
            "sku":"CHARLIE-1","barcode":"703","bin_location":"P1-1"},
}
def fake_lookup(t):
    if t in CAT: return dict(CAT[t])
    for p in CAT.values():
        if p["sku"] == t: return dict(p)
    return None

with patch("app.shopify.lookup_barcode", side_effect=fake_lookup), \
     patch("app.shopify.lookup_barcode_all",
           side_effect=lambda t: [fake_lookup(t)] if fake_lookup(t) else []), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.shopify.get_on_hand", return_value=7), \
     patch("app.main._kick_orders_sync_soon"):
  with TestClient(app) as cl:
    # Saved two-line label for ALPHA-1.
    cl.put("/api/label-names/ALPHA-1", json={
        "top_text": "Telescopes Canada",
        "sku_line": "Alpha Adapter Mk II", "updated_by": "Nick"})

    # 1) Scan Station print with NO label_name -> the saved line applies.
    r = cl.post("/api/print-jobs", json={
        "quantity": 1, "shopify_variant_id": "t:ALPHA",
        "product_title": "Alpha Adapter", "sku": "ALPHA-1",
        "barcode": "701", "requested_by": "Nick"})
    job = r.json()["jobs"][0]
    # A custom centre line with a default top is stored as
    # placement="sku" (the agent then keeps the store header and puts
    # the name on the SKU line) - assert THAT shape.
    check("scan-station print pulls the SAVED label lines",
          r.status_code == 201
          and job["label_name"] == "Alpha Adapter Mk II"
          and job["label_placement"] == "sku", job)
    # An explicit label_name (the serial flow) is never overridden.
    r = cl.post("/api/print-jobs", json={
        "quantity": 1, "shopify_variant_id": "t:ALPHA",
        "product_title": "Alpha Adapter", "sku": "ALPHA-1",
        "label_name": "UHC Filter 2in", "requested_by": "Nick"})
    job = r.json()["jobs"][0]
    check("an explicit label_name wins untouched",
          job["label_name"] == "UHC Filter 2in"
          and not job.get("label_sku"), job)

    # 2) The card's data ride-along.
    tg = cl.get("/api/products/tags?sku=ALPHA-1").json()
    check("tags endpoint carries the saved label lines",
          tg.get("label_name") == "Alpha Adapter Mk II"
          and tg.get("label_placement") == "sku", tg)
    check("tags endpoint carries on-hand", tg.get("on_hand") == 7, tg)

    # 3) Walking order: the bin pre-seeds alphabetically; scanning
    # CHARLIE then ALPHA (BRAVO untouched, no boxes) must queue
    # CHARLIE's labels first.
    bid = cl.post("/api/batches",
                  json={"bin": "P1-1", "created_by": "Nick"}).json()["id"]
    cl.post(f"/api/batches/{bid}/scan", json={"code": "703"})
    cl.post(f"/api/batches/{bid}/scan", json={"code": "703"})
    cl.post(f"/api/batches/{bid}/scan", json={"code": "701"})
    r = cl.post(f"/api/batches/{bid}/queue-labels", json={})
    check("labels queue", r.status_code in (200, 201), r.text[:200])
    with Session(get_engine()) as s:
        rows = s.scalars(select(PrintJob).where(PrintJob.batch_id == bid)
                         .order_by(PrintJob.id)).all()
        seq = [r.sku for r in rows]
    check("jobs come out in the WALKING order (Charlie first)",
          seq == ["CHARLIE-1", "CHARLIE-1", "ALPHA-1"], seq)

    # 4) Printer commands: queue once, claim once, then empty.
    r = cl.post("/api/printer-commands", json={
        "kind": "feed", "requested_by": "Nick"})
    check("re-align command queues", r.status_code == 201, r.text)
    r = cl.post("/api/printer-commands/claim")
    check("agent claims the command",
          r.status_code == 200 and r.json()["count"] == 1
          and r.json()["commands"][0]["kind"] == "feed", r.text)
    r = cl.post("/api/printer-commands/claim")
    check("a claim clears the queue", r.json()["count"] == 0, r.text)

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
