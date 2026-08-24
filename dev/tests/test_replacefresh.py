"""Overwrite freshness: replacing a product's barcode or SKU in Shopify
must also update the local bin map (the FIRST lookup source) and, for
SKU changes, the paired tags. Before this, the old value kept serving
until the next bin-map rebuild (Nick hit it in the field, 2026-08-24).
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["SHOPIFY_WRITE_MODE"]="scan_station_only,verify_onhand"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_replacefresh_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as S
from app.main import app
from app.database import get_engine
from app.models import BinMapEntry, RfidAssignment
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

# One catalog product whose bin-map barcode sits at its SKU (the
# never-set convention) plus an unrelated bystander that must not move.
VID = "gid://shopify/ProductVariant/424242"
SKU = "ZWO ASI120MINI"
NEW_BC = "6977641320481"
NEW_SKU = "ZWO ASI120MINI-V2"

live = {"shopify_variant_id": VID, "shopify_product_id": "gid:p",
        "product_title": "ZWO ASI120 Mini", "variant_title": None,
        "sku": SKU, "barcode": SKU, "bin_location": "F1-4"}

def fake_lookup(t):
    if t.strip().upper() in (SKU.upper(), live["barcode"].upper()):
        return dict(live)
    return None

with patch("app.shopify.lookup_barcode", side_effect=fake_lookup), \
     patch("app.shopify.lookup_barcode_all",
           side_effect=lambda t: [fake_lookup(t)] if fake_lookup(t) else []), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.shopify.update_variant_sku", return_value=None), \
     patch("app.shopify.update_variant_barcode", return_value=None):
  with TestClient(app) as cl:
    with S(get_engine()) as s:
        s.add(BinMapEntry(sku=SKU, barcode=SKU, product_title="ZWO ASI120",
                          bin="F1-4", qty=4, shopify_variant_id=VID,
                          vendor="ZWO"))
        s.add(BinMapEntry(sku="OTHER-1", barcode="999",
                          product_title="Bystander", bin="T1-1", qty=1,
                          shopify_variant_id="gid:other", vendor="X"))
        s.add(RfidAssignment(rfid_id="AAAA0000000000000000000A", sku=SKU,
                             barcode=SKU, product_title="ZWO ASI120",
                             shopify_variant_id=VID))
        s.add(RfidAssignment(rfid_id="BBBB0000000000000000000B",
                             sku="other-1".upper(), barcode="999",
                             product_title="Bystander",
                             shopify_variant_id="gid:other"))
        s.commit()

    # --- barcode overwrite refreshes the bin map ----------------------
    r = cl.post("/api/barcode-overwrites", json={
        "target": SKU, "new_barcode": NEW_BC,
        "changed_by": "Nick", "confirmed": True})
    check("barcode overwrite succeeds", r.status_code == 201,
          (r.status_code, r.text))
    check("response product carries the new barcode",
          r.json()["product"]["barcode"] == NEW_BC, r.json())
    with S(get_engine()) as s:
        bm = s.scalar(select(BinMapEntry).where(BinMapEntry.sku == SKU))
        other = s.scalar(select(BinMapEntry)
                         .where(BinMapEntry.sku == "OTHER-1"))
    check("bin-map row now holds the new barcode", bm.barcode == NEW_BC, bm.barcode)
    check("bystander bin-map row untouched", other.barcode == "999",
          other.barcode)
    # The very next scan of the new barcode resolves via the bin map.
    r = cl.get(f"/api/products/by-barcode/{NEW_BC}")
    check("new barcode resolves immediately (no rebuild wait)",
          r.status_code == 200 and r.json()["sku"] == SKU, r.text)

    # --- SKU overwrite refreshes bin map + paired tags ----------------
    live["barcode"] = NEW_BC
    r = cl.post("/api/sku-overwrites", json={
        "target": NEW_BC, "new_sku": NEW_SKU,
        "changed_by": "Nick", "confirmed": True})
    check("sku overwrite succeeds", r.status_code == 201,
          (r.status_code, r.text))
    with S(get_engine()) as s:
        bm = s.scalar(select(BinMapEntry)
                      .where(BinMapEntry.shopify_variant_id == VID))
        other = s.scalar(select(BinMapEntry)
                         .where(BinMapEntry.shopify_variant_id == "gid:other"))
        tag = s.scalar(select(RfidAssignment).where(
            RfidAssignment.rfid_id == "AAAA0000000000000000000A"))
        tag2 = s.scalar(select(RfidAssignment).where(
            RfidAssignment.rfid_id == "BBBB0000000000000000000B"))
    check("bin-map row now holds the new SKU", bm.sku == NEW_SKU, bm.sku)
    check("bystander bin-map row keeps its SKU", other.sku == "OTHER-1",
          other.sku)
    check("paired tag follows the SKU change", tag.sku == NEW_SKU, tag.sku)
    check("bystander tag keeps its SKU", tag2.sku == "OTHER-1", tag2.sku)

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
