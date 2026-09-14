"""Manual product refresh (Nick, 2026-09-14): live Shopify is the
source of truth - /api/products/refresh re-reads the exact variant by
gid and the catalog row, tag records and open-batch rows all follow
(a tag's physical bin stays its own). Code clashes with another local
product ask to confirm and file a Review task, never block.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_prodrefresh_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.main import app
from app.database import get_engine
from app.models import (BarcodeChange, Batch, BatchItem, BinMapEntry,
                        ReviewTask, RfidAssignment)
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

GID = "gid://shopify/PV/500"
FRESH = {
    "shopify_variant_id": GID,
    "shopify_product_id": "gid://shopify/P/50",
    "product_title": "Renamed Widget",
    "variant_title": "Default Title",
    "sku": "NEW-SKU",
    "barcode": "NEW-BC",
    "bin_location": "Z9-9",
    "image_url": "https://img/new.png",
}

with patch("app.main.oneleft"):
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="OLD-SKU", barcode="OLD-BC",
                          product_title="Old Widget", bin="A1-1", qty=2,
                          shopify_variant_id=GID))
        s.add(RfidAssignment(rfid_id="AA00000000000000000000R1",
                             shopify_variant_id=GID, sku="OLD-SKU",
                             barcode="OLD-BC",
                             product_title="Old Widget",
                             bin_location="A1-1"))
        b = Batch(bin_name="A1-1", created_by="n")
        s.add(b); s.flush()
        s.add(BatchItem(batch_id=b.id, scanned_code="OLD-BC",
                        sku="OLD-SKU", barcode="OLD-BC", resolved=True,
                        qty_scanned=1, product_title="Old Widget",
                        shopify_variant_id=GID))
        s.commit()
        bid = b.id

    # ---- clean refresh: everything follows Shopify --------------------
    with patch("app.shopify.lookup_variant_by_gid",
               return_value=dict(FRESH)):
        r = cl.post("/api/products/refresh", json={
            "variant_gid": GID, "changed_by": "Nick"})
    d = r.json()
    check("refresh answered", r.status_code == 200, r.text[:300])
    check("changed fields named",
          {"sku", "barcode", "product_title", "bin",
           "image_url"} <= set(d.get("changed", [])), d.get("changed"))
    check("fresh product returned for the card",
          d["product"]["sku"] == "NEW-SKU", d.get("product"))
    with Session(get_engine()) as s:
        bm = s.scalars(select(BinMapEntry).where(
            BinMapEntry.shopify_variant_id == GID)).first()
        check("catalog row follows (sku, barcode, title, bin)",
              bm.sku == "NEW-SKU" and bm.barcode == "NEW-BC"
              and bm.product_title == "Renamed Widget"
              and bm.bin == "Z9-9", bm.as_dict()
              if hasattr(bm, "as_dict") else bm.sku)
        t = s.scalars(select(RfidAssignment)).first()
        check("tag record follows identity, keeps its PHYSICAL bin",
              t.sku == "NEW-SKU" and t.barcode == "NEW-BC"
              and t.bin_location == "A1-1",
              (t.sku, t.barcode, t.bin_location))
        it = s.scalars(select(BatchItem).where(
            BatchItem.batch_id == bid)).first()
        check("open batch row follows",
              it.sku == "NEW-SKU", it.sku)
        ev = s.scalars(select(BarcodeChange).where(
            BarcodeChange.changed_field == "product-refreshed")).all()
        check("one Product Refreshed history row",
              len(ev) == 1 and ev[0].changed_by == "Nick", ev)

    # ---- clash: asks to confirm, then files Review --------------------
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="RIVAL-1", barcode="NEW-BC",
                          product_title="Rival", bin="B1-1", qty=1,
                          shopify_variant_id="gid://shopify/PV/777"))
        s.commit()
    with patch("app.shopify.lookup_variant_by_gid",
               return_value=dict(FRESH)):
        r = cl.post("/api/products/refresh", json={
            "variant_gid": GID, "changed_by": "Nick"})
        check("a clash asks instead of blocking (409 + confirm text)",
              r.status_code == 409
              and "Confirm to refresh anyway" in r.text
              and "RIVAL-1" in r.text, r.text[:250])
        r = cl.post("/api/products/refresh", json={
            "variant_gid": GID, "changed_by": "Nick",
            "confirmed": True})
        check("confirmed refresh applies", r.status_code == 200
              and "clash" in r.json()["message"].lower(), r.text[:250])
    with Session(get_engine()) as s:
        clash = s.scalars(select(ReviewTask).where(
            ReviewTask.detail.like("Operator-confirmed%"))).all()
        check("the clash landed as a Review task",
              len(clash) == 1 and "RIVAL-1" in clash[0].detail,
              [t.detail[:90] for t in clash])

    # ---- gone from Shopify --------------------------------------------
    with patch("app.shopify.lookup_variant_by_gid", return_value=None):
        r = cl.post("/api/products/refresh", json={"variant_gid": GID})
        check("a deleted variant answers 404 with the story",
              r.status_code == 404 and "gone from Shopify" in r.text,
              r.text[:200])

    # ---- SKU overwrite clash also asks + files -----------------------
    RIVAL = {"shopify_variant_id": "gid://shopify/PV/777",
             "sku": "RIVAL-1", "product_title": "Rival"}
    ME = {"shopify_variant_id": GID, "shopify_product_id":
          "gid://shopify/P/50", "sku": "NEW-SKU",
          "product_title": "Renamed Widget"}
    with patch("app.main._lookup_api", return_value=dict(ME)), \
         patch("app.main._resolve", return_value=dict(RIVAL)), \
         patch("app.shopify.update_variant_sku", return_value={}):
        r = cl.post("/api/sku-overwrites", json={
            "target": "NEW-SKU", "new_sku": "RIVAL-1",
            "confirmed": True, "changed_by": "Nick"})
        check("sku clash asks first",
              r.status_code == 409
              and "Confirm to write it anyway" in r.text, r.text[:200])
        r = cl.post("/api/sku-overwrites", json={
            "target": "NEW-SKU", "new_sku": "RIVAL-1",
            "confirmed": True, "force": True, "changed_by": "Nick"})
        check("forced sku clash writes + files Review",
              r.status_code == 201, r.text[:200])
    with Session(get_engine()) as s:
        n = len(s.scalars(select(ReviewTask).where(
            ReviewTask.detail.like("Operator-confirmed%"))).all())
        check("second clash task filed", n == 2, n)

print()
if fails:
    print(f"{len(fails)} FAILURE(S):"); [print("  -", f) for f in fails]
    sys.exit(1)
print("ALL PASS")
