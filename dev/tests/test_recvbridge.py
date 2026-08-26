"""TC-Planner -> RFID receiving bridge (Nick, 2026-08-25): the planner's
"Print labels" button POSTs received items to /api/receiving/prints.
The RFID app creates (or reuses, per stock-order reference) a receiving
batch, adds the quantities, and queues labels like a receiving PRINT
pass - each label carrying the item's home bin, no-bin items held out,
unknown and non-taggable SKUs skipped and named. The queue listing
carries batch info so the Print queue tab can group jobs."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_recvbridge_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_engine
from app.models import BinMapEntry, NonTaggable, PrintJob
from sqlalchemy.orm import Session
from sqlalchemy import select
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.main._kick_orders_sync_soon"):
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="ZWO FL-HLDR-M54x15",
                          product_title="ZWO Filter Holder M54",
                          bin="G2-1", qty=3, barcode="911",
                          shopify_variant_id="t:HLDR"))
        s.add(BinMapEntry(sku="ZWO EAF-5V", product_title="ZWO EAF",
                          bin="G2-2", qty=1, barcode="912",
                          shopify_variant_id="t:EAF"))
        s.add(BinMapEntry(sku="NOBIN-1", product_title="Binless Widget",
                          bin="", qty=1, barcode="913",
                          shopify_variant_id="t:NOBIN"))
        s.add(BinMapEntry(sku="SCREW-1", product_title="Thumbscrew",
                          bin="Z9-9", qty=500, barcode="914",
                          shopify_variant_id="t:SCREW"))
        s.add(NonTaggable(sku="SCREW-1", set_by="Nick"))
        s.commit()

    r = cl.post("/api/receiving/prints", json={
        "items": [
            {"sku": "ZWO FL-HLDR-M54x15", "quantity": 3},
            {"sku": "ZWO EAF-5V", "quantity": 1},
            {"sku": "NOBIN-1", "quantity": 2},
            {"sku": "SCREW-1", "quantity": 50},
            {"sku": "GHOST-1", "quantity": 4},
        ],
        "requested_by": "Nick",
        "reference": "SO 42 · ZWO",
    })
    check("bridge call succeeds", r.status_code == 201, r.text[:300])
    out = r.json()
    bid = out["batch"]["id"]
    check("a receiving batch was created",
          out["batch"]["kind"] == "receiving"
          and out["batch"]["created_by"] == "TC-Planner · SO 42 · ZWO",
          out["batch"])
    check("labels queued for binned products only (3 + 1)",
          out["queued"] == 4, out)
    check("the no-bin product is held out and named",
          out["skipped_no_bin"] == ["Binless Widget"], out["skipped_no_bin"])
    check("unknown SKUs are skipped and named",
          out["skipped_unknown"] == ["GHOST-1"], out["skipped_unknown"])
    check("non-taggable SKUs are skipped and named",
          out["skipped_non_taggable"] == ["SCREW-1"],
          out["skipped_non_taggable"])
    with Session(get_engine()) as s:
        jobs = s.scalars(select(PrintJob).where(
            PrintJob.batch_id == bid).order_by(PrintJob.id)).all()
        check("labels carry each item's HOME bin",
              [j.bin_location for j in jobs]
              == ["G2-1", "G2-1", "G2-1", "G2-2"],
              [j.bin_location for j in jobs])

    # Saving more of the same order reuses the batch, prints only the
    # NEW boxes.
    r = cl.post("/api/receiving/prints", json={
        "items": [{"sku": "ZWO FL-HLDR-M54x15", "quantity": 2}],
        "requested_by": "Nick",
        "reference": "SO 42 · ZWO",
    })
    check("a repeat save reuses the same receiving batch",
          r.json()["batch"]["id"] == bid, r.json()["batch"])
    check("only the newly received boxes queue", r.json()["queued"] == 2,
          r.json())

    # A different stock order gets its own batch.
    r = cl.post("/api/receiving/prints", json={
        "items": [{"sku": "ZWO EAF-5V", "quantity": 1}],
        "requested_by": "Nick",
        "reference": "SO 43 · ZWO",
    })
    check("a different order gets its own batch",
          r.json()["batch"]["id"] != bid, r.json()["batch"])

    listing = cl.get("/api/print-jobs?limit=200").json()
    binfo = listing.get("batches", {}).get(str(bid)) \
        or listing.get("batches", {}).get(bid)
    check("the queue listing carries batch info for grouping",
          binfo is not None and binfo["kind"] == "receiving"
          and "SO 42" in (binfo["created_by"] or ""), listing.get("batches"))

    # The receiving batch resumes in Batch tagging with printed counts.
    items = cl.get(f"/api/batches/{bid}").json()["items"]
    hldr = next(i for i in items if i["sku"] == "ZWO FL-HLDR-M54x15")
    check("the batch row carries printed vs paired for the web list",
          hldr["qty_scanned"] == 5 and hldr["printed_count"] == 5
          and hldr["paired_count"] == 0, hldr)

    # --- The stepless receiving list (Nick, 2026-08-25) -----------------
    # expected_qty tracks the PLANNER's cumulative number, apart from the
    # received count, so Update count can correct one while showing the
    # other.
    check("expected_qty is the planner's cumulative number",
          hldr["expected_qty"] == 5, hldr)

    # What couldn't print stays ON the batch as a flagged row that can
    # explain itself, instead of only being named in a response.
    ghost = next((i for i in items if i["scanned_code"] == "GHOST-1"), None)
    check("an unknown SKU becomes a flagged row",
          ghost is not None and not ghost["resolved"]
          and ghost["qty_scanned"] == 4
          and "Not found" in (ghost["skip_reason"] or ""), ghost)
    screw = next((i for i in items if i["sku"] == "SCREW-1"), None)
    check("a non-taggable SKU becomes a flagged row with its product",
          screw is not None and screw["skipped"]
          and "non-taggable" in (screw["skip_reason"] or ""), screw)

    # A repeat save of the same unknown SKU reuses its flagged row.
    cl.post("/api/receiving/prints", json={
        "items": [{"sku": "GHOST-1", "quantity": 1}],
        "requested_by": "Nick", "reference": "SO 42 · ZWO",
    })
    items = cl.get(f"/api/batches/{bid}").json()["items"]
    ghost = next(i for i in items if i["scanned_code"] == "GHOST-1")
    check("repeat saves reuse the flagged row",
          ghost["qty_scanned"] == 5 and ghost["expected_qty"] == 5, ghost)

    # Reprint labels: per-item, receiving labels carry the item's HOME
    # bin (never the RECEIVING sentinel), count untouched.
    r = cl.post(f"/api/batches/{bid}/items/{hldr['id']}/labels",
                json={"quantity": 2, "requested_by": "Nick"})
    check("per-item reprint queues", r.status_code == 201, r.text[:200])
    with Session(get_engine()) as s:
        newest = s.scalars(select(PrintJob).where(
            PrintJob.batch_id == bid).order_by(PrintJob.id.desc())).all()[:2]
        check("receiving reprints carry the item's home bin",
              all(j.bin_location == "G2-1" for j in newest),
              [j.bin_location for j in newest])
    items = cl.get(f"/api/batches/{bid}").json()["items"]
    hldr = next(i for i in items if i["sku"] == "ZWO FL-HLDR-M54x15")
    check("reprinting does not change the received count",
          hldr["qty_scanned"] == 5, hldr)

    # Flagged rows never print.
    r = cl.post(f"/api/batches/{bid}/items/{screw['id']}/labels",
                json={"quantity": 1})
    check("a flagged row refuses to print, naming its reason",
          r.status_code == 422 and "flagged" in r.text, r.text[:200])

    # Update count corrects the received number; the planner's stays.
    r = cl.post(f"/api/batches/{bid}/items/{hldr['id']}/qty",
                json={"qty": 7})
    check("update count sets the received number",
          r.status_code == 200 and r.json()["qty_scanned"] == 7
          and r.json()["expected_qty"] == 5, r.text[:200])

    # --- Planner-driven closure + the Link-to-product fix ---------------
    # A fresh order: one known product, one unknown code.
    r = cl.post("/api/receiving/prints", json={
        "items": [{"sku": "ZWO EAF-5V", "quantity": 1},
                  {"sku": "GHOST-9", "quantity": 1}],
        "requested_by": "Nick", "reference": "SO 44 · ZWO",
    })
    b2 = r.json()["batch"]["id"]
    items2 = cl.get(f"/api/batches/{b2}").json()["items"]
    eaf = next(i for i in items2 if i["sku"] == "ZWO EAF-5V")
    ghost9 = next(i for i in items2 if i["scanned_code"] == "GHOST-9")

    # Tagging the known box does NOT close the shipment - the unfixed
    # unknown row is real untagged stock.
    r = cl.post(f"/api/batches/{b2}/pair", json={
        "epc": "AB000000000000000000AB01", "item_id": eaf["id"],
        "created_by": "Nick"})
    check("an unfixed unknown row keeps the shipment open",
          r.status_code == 201 and r.json()["receiving_done"] is False,
          r.text[:200])

    # Link the code to the real product: alias + resolve. The row merges
    # into the existing product row, the planner count folds in, and the
    # box's label queues by itself.
    r = cl.post("/api/barcode-aliases", json={
        "alias_barcode": "GHOST-9", "target": "ZWO EAF-5V",
        "created_by": "Nick"})
    check("the planner's code links as an alias", r.status_code == 201,
          r.text[:200])
    r = cl.post(f"/api/batches/{b2}/items/{ghost9['id']}/resolve", json={})
    out = r.json()
    check("resolving merges the fixed row and queues its label",
          out["resolved"] and out["merged"] and out["queued"] == 1
          and out["item"]["qty_scanned"] == 2
          and out["item"]["expected_qty"] == 2, out)

    # The LAST pair closes the shipment by itself - no Finish button.
    r = cl.post(f"/api/batches/{b2}/pair", json={
        "epc": "AB000000000000000000AB02", "item_id": out["item"]["id"],
        "created_by": "Nick"})
    check("the last pair closes the shipment",
          r.json()["receiving_done"] is True, r.text[:200])
    b2row = cl.get(f"/api/batches/{b2}").json()["batch"]
    check("the batch is done with a completion time",
          b2row["status"] == "done" and b2row["completed_at"], b2row)

    # A count corrected DOWN to what's tagged also closes the shipment.
    r = cl.post("/api/receiving/prints", json={
        "items": [{"sku": "ZWO EAF-5V", "quantity": 2}],
        "requested_by": "Nick", "reference": "SO 45 · ZWO",
    })
    b3 = r.json()["batch"]["id"]
    it3 = cl.get(f"/api/batches/{b3}").json()["items"][0]
    cl.post(f"/api/batches/{b3}/pair", json={
        "epc": "AB000000000000000000AB03", "item_id": it3["id"],
        "created_by": "Nick"})
    r = cl.post(f"/api/batches/{b3}/items/{it3['id']}/qty", json={"qty": 1})
    check("lowering the count to the tagged number closes the shipment",
          r.json().get("receiving_done") is True, r.text[:200])

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
