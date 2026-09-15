"""Pairing anywhere consumes owed printed labels (Nick, 2026-09-15:
"pairing any label from any of these lists should count towards any
Unpaired Labels list, including open batch tagging tasks"). The
credit is EPC-exact first (print jobs carry their encoded EPCs), then
falls back to the SKU - newest owing receiving batch, then open bin
batches. Station pairs, sweep pairs and the locate pair all credit;
finished bin batches never change.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_paircredit_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as S
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.main._maybe_refresh_bin_map", return_value=False), \
     patch("app.main._kick_orders_sync_soon", create=True):
  with TestClient(app) as cl:
    from app.database import get_engine
    from app.models import Batch, BatchItem, BinMapEntry, PrintJob

    with S(get_engine()) as s:
        s.add(BinMapEntry(sku="F9123A", barcode="BC-F9123A",
                          product_title="Svbony F9123A", bin="F9-1",
                          qty=3, shopify_variant_id="gid:f9123a"))
        s.add(BinMapEntry(sku="G100", barcode="BC-G100",
                          product_title="Bin product G100", bin="G1-1",
                          qty=2, shopify_variant_id="gid:g100"))
        # Receiving batch: 3 labels printed for F9123A, none paired.
        rb = Batch(bin_name="RECEIVING", kind="receiving",
                   status="collecting", created_by="t")
        s.add(rb); s.flush()
        s.add(BatchItem(batch_id=rb.id, scanned_code="BC-F9123A",
                        resolved=True, sku="F9123A",
                        barcode="BC-F9123A",
                        product_title="Svbony F9123A", qty_scanned=3))
        for i in range(3):
            s.add(PrintJob(epc=f"F9123A00000000000000000{i}",
                           status="done", sku="F9123A",
                           product_title="Svbony F9123A",
                           shopify_variant_id="gid:f9123a",
                           batch_id=rb.id))
        # OPEN bin batch: 2 labels printed for G100, none paired.
        ob = Batch(bin_name="G1-1", status="collecting", created_by="t")
        s.add(ob); s.flush()
        s.add(BatchItem(batch_id=ob.id, scanned_code="BC-G100",
                        resolved=True, sku="G100", barcode="BC-G100",
                        product_title="Bin product G100",
                        qty_scanned=2))
        for i in range(2):
            s.add(PrintJob(epc=f"G10000000000000000000A0{i}",
                           status="done", sku="G100",
                           product_title="Bin product G100",
                           shopify_variant_id="gid:g100",
                           batch_id=ob.id))
        # DONE bin batch with an owed label - must never be credited.
        dbat = Batch(bin_name="Z9-9", status="done", created_by="t")
        s.add(dbat); s.flush()
        s.add(BatchItem(batch_id=dbat.id, scanned_code="BC-G100",
                        resolved=True, sku="G100", barcode="BC-G100",
                        product_title="Bin product G100",
                        qty_scanned=1))
        s.add(PrintJob(epc="G100DONE000000000000000X",
                       status="done", sku="G100",
                       product_title="Bin product G100",
                       shopify_variant_id="gid:g100",
                       batch_id=dbat.id))
        s.commit()
        rb_id, ob_id, done_id = rb.id, ob.id, dbat.id

    def paired(bid, sku):
        with S(get_engine()) as s:
            it = s.scalars(select(BatchItem).where(
                BatchItem.batch_id == bid,
                BatchItem.sku == sku)).first()
            return it.paired_count if it else None

    # ---- station pair of the printed label EPC: EPC-exact credit ------
    r = cl.post("/api/rfid-assignments", json={
        "rfid_id": "F9123A000000000000000000",
        "shopify_variant_id": "gid:f9123a",
        "product_title": "Svbony F9123A", "sku": "F9123A",
        "barcode": "BC-F9123A", "assigned_by": "Nick"})
    check("station pair of a printed label answers",
          r.status_code == 201, r.text[:200])
    check("...and credits the receiving batch item (EPC-exact)",
          paired(rb_id, "F9123A") == 1, paired(rb_id, "F9123A"))

    # ---- station pair of a FOREIGN tag, same sku: SKU fallback --------
    r = cl.post("/api/rfid-assignments", json={
        "rfid_id": "AAAA00000000000000000001",
        "shopify_variant_id": "gid:f9123a",
        "product_title": "Svbony F9123A", "sku": "F9123A",
        "assigned_by": "Nick"})
    check("a hand-tagged box still consumes an owed label by SKU",
          r.status_code == 201 and paired(rb_id, "F9123A") == 2,
          paired(rb_id, "F9123A"))

    # ---- open BIN batch tagging tasks are credited too ----------------
    r = cl.post("/api/rfid-assignments", json={
        "rfid_id": "G10000000000000000000A00",
        "shopify_variant_id": "gid:g100",
        "product_title": "Bin product G100", "sku": "G100",
        "assigned_by": "Nick"})
    check("a bin batch's printed label credits ITS batch (EPC-exact)",
          r.status_code == 201 and paired(ob_id, "G100") == 1,
          paired(ob_id, "G100"))
    check("the DONE batch stays untouched", paired(done_id, "G100") == 0,
          paired(done_id, "G100"))

    # ---- locate pair-unlinked flows through the same credit -----------
    with patch("app.main._still_unlinked",
               side_effect=lambda s_, e: set(
                   x.upper() for x in e)):
        r = cl.post("/api/locate/pair-unlinked", json={
            "epc": "G10000000000000000000A01",
            "code": "BC-G100", "worker": "C72"})
    check("locate pair credits the open bin batch",
          r.status_code == 201
          and r.json()["bumped_item_id"] is not None
          and paired(ob_id, "G100") == 2,
          (r.text[:200], paired(ob_id, "G100")))

    # ---- credit never exceeds what was printed ------------------------
    r = cl.post("/api/rfid-assignments", json={
        "rfid_id": "AAAA00000000000000000002",
        "shopify_variant_id": "gid:g100",
        "product_title": "Bin product G100", "sku": "G100",
        "assigned_by": "Nick"})
    check("nothing owed = nothing credited (no over-bump)",
          r.status_code == 201 and paired(ob_id, "G100") == 2
          and paired(done_id, "G100") == 0, paired(ob_id, "G100"))

    # ---- sweep pairs credit one per tag, capped at owed ---------------
    r = cl.post("/api/rfid-assignments/sweep", json={
        "epcs": ["F9123A000000000000000001",
                 "F9123A000000000000000002",
                 "AAAA00000000000000000003"],
        "shopify_variant_id": "gid:f9123a",
        "product_title": "Svbony F9123A", "sku": "F9123A",
        "assigned_by": "Nick"})
    check("sweep pair answers", r.status_code == 201
          and r.json()["count"] == 3, r.text[:200])
    check("sweep credits per tag, capped at the printed count",
          paired(rb_id, "F9123A") == 3, paired(rb_id, "F9123A"))

    # The unpaired-labels list agrees: nothing owed anywhere now.
    r = cl.get("/api/receiving/unpaired-labels")
    check("the receiving unpaired-labels list is empty",
          r.json()["total_labels"] == 0, r.text[:200])

print()
if fails:
    print(f"{len(fails)} FAILURE(S):"); [print("  -", f) for f in fails]
    sys.exit(1)
print("ALL PASS")
