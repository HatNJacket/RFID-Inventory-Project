"""Label lines double as ephemeral lookup aliases (Nick, 2026-08-25):
saving a custom top/SKU line links that string to the product, so typing
what the sticker says finds it. Replaced lines lose their alias (the
ZWO Softbag example: "ZWO Softbag Small" resolves only while the label
still says so). Real identities always win; manual aliases are never
touched by label saves; a line already linked elsewhere is not stolen."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_labelalias_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_engine
from app.models import BarcodeAlias, BinMapEntry
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
        s.add(BinMapEntry(sku="SOFT-1", product_title="ZWO Softbag1",
                          bin="D1-1", qty=2, barcode="611",
                          shopify_variant_id="t:SOFT-1"))
        s.add(BinMapEntry(sku="SOFT-2", product_title="ZWO Softbag2",
                          bin="D1-1", qty=2, barcode="612",
                          shopify_variant_id="t:SOFT-2"))
        s.commit()

    r = cl.put("/api/label-names/SOFT-1", json={
        "top_text": "Telescopes Canada",
        "sku_line": "ZWO Softbag Small", "updated_by": "Nick"})
    check("label save succeeds", r.status_code == 200, r.text)

    r = cl.get("/api/products/by-barcode/ZWO%20Softbag%20Small")
    check("the saved SKU line resolves to the product",
          r.status_code == 200 and r.json()["sku"] == "SOFT-1", r.text)
    check("resolution is flagged as an alias hit",
          r.json().get("alias_warning") is True, r.json())
    r = cl.get("/api/products/by-barcode/zwo%20softbag%20small")
    check("label-line lookup is case-insensitive",
          r.status_code == 200 and r.json()["sku"] == "SOFT-1", r.text)

    # Changing the line replaces the alias - the old string stops
    # resolving (Nick's exact example).
    cl.put("/api/label-names/SOFT-1", json={
        "top_text": "Telescopes Canada",
        "sku_line": "ZWO Softbag-S", "updated_by": "Nick"})
    r = cl.get("/api/products/by-barcode/ZWO%20Softbag%20Small")
    check("the REPLACED line no longer resolves", r.status_code == 404,
          r.status_code)
    r = cl.get("/api/products/by-barcode/ZWO%20Softbag-S")
    check("the new line resolves instead",
          r.status_code == 200 and r.json()["sku"] == "SOFT-1", r.text)

    # A second product saving the SAME line must not steal the link.
    cl.put("/api/label-names/SOFT-2", json={
        "top_text": "Telescopes Canada",
        "sku_line": "ZWO Softbag-S", "updated_by": "Nick"})
    r = cl.get("/api/products/by-barcode/ZWO%20Softbag-S")
    check("a duplicate line elsewhere doesn't steal the alias",
          r.status_code == 200 and r.json()["sku"] == "SOFT-1", r.text)

    # A custom TOP line gets an alias too.
    cl.put("/api/label-names/SOFT-2", json={
        "top_text": "The Big Padded Bag",
        "sku_line": "SOFT-2", "updated_by": "Nick"})
    r = cl.get("/api/products/by-barcode/The%20Big%20Padded%20Bag")
    check("a custom top line resolves to its product",
          r.status_code == 200 and r.json()["sku"] == "SOFT-2", r.text)

    # Manual aliases survive label edits; label aliases die with the line.
    r = cl.post("/api/barcode-aliases", json={
        "alias_barcode": "777000111222", "target": "SOFT-1",
        "created_by": "Nick"})
    check("manual link still works alongside", r.status_code == 201, r.text)
    cl.put("/api/label-names/SOFT-1", json={
        "top_text": "Telescopes Canada", "sku_line": "SOFT-1",
        "updated_by": "Nick"})
    r = cl.get("/api/products/by-barcode/ZWO%20Softbag-S")
    check("clearing the label clears its alias", r.status_code == 404,
          r.status_code)
    r = cl.get("/api/products/by-barcode/777000111222")
    check("the manual alias is untouched by label edits",
          r.status_code == 200 and r.json()["sku"] == "SOFT-1", r.text)

    with Session(get_engine()) as s:
        kinds = {a.alias_barcode: a.kind for a in s.scalars(
            select(BarcodeAlias))}
        check("rows carry their kind (manual vs label)",
              kinds.get("777000111222") == "manual"
              and all(k in ("manual", "label") for k in kinds.values()),
              kinds)

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
