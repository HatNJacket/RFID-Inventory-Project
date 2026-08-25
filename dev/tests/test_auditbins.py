"""Audit 'Shopify vs RFID by bin' truth fixes (Nick, 2026-08-25):
- sold adjustment is WINDOWED to the tag-pool baseline (pre-tagging
  sales produced '3 in Shopify, 4 tags, difference of -19'), and a SKU
  with no live tags gets no sold adjustment at all;
- bundles and dropped products leave the audit instead of scoring it;
- non-taggable products (bins of loose thumbscrews) leave the audit,
  batch seeding, and the mismatch arithmetic entirely, and their
  hand-paired bag marker never orphan-flags."""
import os, sys, tempfile
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_auditbins_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_engine
from app.models import (BinMapEntry, ProductKind, RfidAssignment,
                        SoldRecord)
from sqlalchemy.orm import Session
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

NOW = datetime.now(timezone.utc)

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.main._kick_orders_sync_soon"):
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        # GLASS-1: Nick's case shape. 4 tags paired recently, on-hand 3,
        # 20 units sold long BEFORE tagging -> those sales explain
        # nothing; honest diff is +1.
        s.add(BinMapEntry(sku="GLASS-1", product_title="Filter",
                          bin="A1-1", qty=3,
                          shopify_variant_id="t:GLASS-1"))
        for i in range(4):
            s.add(RfidAssignment(
                rfid_id=f"AA0000000000000000000{i:03d}",
                shopify_variant_id="t:GLASS-1", product_title="Filter",
                sku="GLASS-1", bin_location="A1-1",
                assigned_at=NOW - timedelta(days=2)))
        s.add(SoldRecord(order_id="o1", order_name="#1", sku="GLASS-1",
                         quantity=20, retired=0,
                         fulfilled_at=NOW - timedelta(days=40)))
        # GLASS-2: one unit sold AFTER tagging -> that sale legitimately
        # raises expected tags by 1 (tag left with the box).
        s.add(BinMapEntry(sku="GLASS-2", product_title="Filter II",
                          bin="A1-1", qty=1,
                          shopify_variant_id="t:GLASS-2"))
        for i in range(2):
            s.add(RfidAssignment(
                rfid_id=f"BB0000000000000000000{i:03d}",
                shopify_variant_id="t:GLASS-2", product_title="Filter II",
                sku="GLASS-2", bin_location="A1-1",
                assigned_at=NOW - timedelta(days=2)))
        s.add(SoldRecord(order_id="o2", order_name="#2", sku="GLASS-2",
                         quantity=1, retired=0,
                         fulfilled_at=NOW - timedelta(days=1)))
        # NOTAGS-1: never tagged, sales on file -> sold adjustment 0.
        s.add(BinMapEntry(sku="NOTAGS-1", product_title="Untagged",
                          bin="A1-1", qty=2,
                          shopify_variant_id="t:NOTAGS-1"))
        s.add(SoldRecord(order_id="o3", order_name="#3", sku="NOTAGS-1",
                         quantity=7, retired=0,
                         fulfilled_at=NOW - timedelta(days=10)))
        # BUND-1: a saved bundle - components carry the tags.
        s.add(BinMapEntry(sku="BUND-1", product_title="BUNDLE: kit",
                          bin="A1-1", qty=5,
                          shopify_variant_id="t:BUND-1"))
        s.add(ProductKind(sku="BUND-1", kind="bundle"))
        # THUMB-1: the thumbscrew bin, with a hand-paired bag marker.
        s.add(BinMapEntry(sku="THUMB-1", product_title="Thumbscrew M4",
                          bin="Z9-9", qty=500,
                          shopify_variant_id="t:THUMB-1"))
        s.add(RfidAssignment(
            rfid_id="CC00000000000000000000MK",
            shopify_variant_id="t:THUMB-1", product_title="Thumbscrew M4",
            sku="THUMB-1", bin_location="Z9-9", assigned_at=NOW))
        s.commit()

    r = cl.put("/api/products/THUMB-1/non-taggable",
               json={"non_taggable": True, "changed_by": "Nick"})
    check("non-taggable flag sets", r.status_code == 200, r.text)
    ph = cl.get("/api/product-history?term=THUMB-1").json()
    check("product panel reports non_taggable",
          ph.get("non_taggable") is True, ph.get("non_taggable"))
    check("the flip is History-logged", any(
        e["type"] == "non-taggable" for e in ph["events"]),
          [e["type"] for e in ph["events"]][:6])

    audit = cl.get("/api/audit/bins").json()
    rows = {p["sku"]: p for b in audit["bins"] for p in b["products"]}
    check("pre-tagging sales no longer inflate the diff (was -19-style)",
          rows["GLASS-1"]["diff"] == 1
          and rows["GLASS-1"]["sold_unretired"] == 0, rows.get("GLASS-1"))
    check("a sale AFTER tagging still raises expected tags",
          rows["GLASS-2"]["diff"] == 0
          and rows["GLASS-2"]["sold_unretired"] == 1, rows.get("GLASS-2"))
    check("a never-tagged product gets no sold adjustment",
          rows["NOTAGS-1"]["diff"] == -2
          and rows["NOTAGS-1"]["sold_unretired"] == 0,
          rows.get("NOTAGS-1"))
    check("bundles leave the audit", "BUND-1" not in rows,
          sorted(rows))
    check("non-taggable products leave the audit", "THUMB-1" not in rows,
          sorted(rows))
    check("the bag marker never orphan-flags", not any(
        "THUMB" in (p["sku"] or "") for b in audit["bins"]
        for p in b["products"]), None)
    check("skip counts are reported",
          audit["skipped_bundles"] == 1
          and audit["skipped_non_taggable"] == 1,
          (audit["skipped_bundles"], audit["skipped_non_taggable"]))

    # Batch seeding: the thumbscrew bin never seeds its product.
    nb = cl.post("/api/batches",
                 json={"bin": "Z9-9", "created_by": "Nick"}).json()
    skus = [i["sku"] for i in nb.get("items", [])]
    check("batch seeding skips non-taggable products",
          "THUMB-1" not in skus, skus)

    # And coming back is one call.
    cl.put("/api/products/THUMB-1/non-taggable",
           json={"non_taggable": False, "changed_by": "Nick"})
    audit = cl.get("/api/audit/bins").json()
    rows = {p["sku"] for b in audit["bins"] for p in b["products"]}
    check("unmarking brings it back into the audit", "THUMB-1" in rows,
          sorted(rows))

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
