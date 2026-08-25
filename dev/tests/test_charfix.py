"""Broken-character rescue + keep-the-old-code-linked (Nick, 2026-08-25,
the ZWO 'Ⅱ' pair):
- lookups that miss retry with NFKC folding (a REAL 'Ⅱ' scan finds the
  record fixed to plain 'II') and with non-ASCII folded to '?' (finds a
  record the VARCHAR database mangled);
- SKU/barcode overwrites that replace a BROKEN value auto-link the old
  string as a 'legacy' alias, so already-printed labels keep scanning;
  clean replaced values are never auto-linked;
- aliases anchored to a SKU follow it through a SKU overwrite."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["SHOPIFY_WRITE_MODE"]="scan_station_only,verify_onhand"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_charfix_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

ROMAN2 = "\u2161"  # the single-character roman numeral II
CAT = [
    # Case 2's shape: the Shopify barcode/SKU were already FIXED to
    # plain text; old labels still carry the real unicode character.
    {"shopify_variant_id": "gid:nik", "shopify_product_id": "gid:pn",
     "product_title": "ZWO Nikon-T2 Adapter II", "variant_title": None,
     "sku": "ZWO Nikon-T2-II", "barcode": "912", "bin_location": "F4-1"},
    # Case 1's shape: the stored/live value still carries the mangled '?'.
    {"shopify_variant_id": "gid:fd", "shopify_product_id": "gid:pf",
     "product_title": "ZWO FD-M42 Adapter", "variant_title": None,
     "sku": "ZWO FD-M42-?", "barcode": "ZWO FD-M42-?",
     "bin_location": "F4-1"},
    # Clean control product.
    {"shopify_variant_id": "gid:ok", "shopify_product_id": "gid:po",
     "product_title": "Baader UHC Filter", "variant_title": None,
     "sku": "OK-1", "barcode": "111", "bin_location": "F4-1"},
]
def fake_lookup(t):
    for p in CAT:
        if p["barcode"] == t or p["sku"] == t:
            return dict(p)
    return None
def upd_sku(pid, vid, s):
    for p in CAT:
        if p["shopify_variant_id"] == vid:
            p["sku"] = s
def upd_bc(pid, vid, b):
    for p in CAT:
        if p["shopify_variant_id"] == vid:
            p["barcode"] = b

with patch("app.shopify.lookup_barcode", side_effect=fake_lookup), \
     patch("app.shopify.lookup_barcode_all",
           side_effect=lambda t: [fake_lookup(t)] if fake_lookup(t) else []), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.shopify.update_variant_sku", side_effect=upd_sku), \
     patch("app.shopify.update_variant_barcode", side_effect=upd_bc), \
     patch("app.main._kick_orders_sync_soon"):
  with TestClient(app) as cl:
    # 1) An old label carrying the REAL unicode char finds the record
    # that was since fixed to plain text (NFKC fold).
    r = cl.get("/api/products/by-barcode/"
               + f"ZWO Nikon-T2-{ROMAN2}")
    check("real-unicode scan finds the FIXED record (NFKC)",
          r.status_code == 200 and r.json()["sku"] == "ZWO Nikon-T2-II",
          r.text[:120])
    check("the rescue names what the scan really said",
          r.json().get("charfold_from") == f"ZWO Nikon-T2-{ROMAN2}",
          r.json().get("charfold_from"))

    # 2) The same scan against a record the DATABASE mangled to '?'
    # (the '?'-fold path).
    r = cl.get("/api/products/by-barcode/"
               + f"ZWO FD-M42-{ROMAN2}")
    check("real-unicode scan finds the MANGLED record ('?'-fold)",
          r.status_code == 200 and r.json()["sku"] == "ZWO FD-M42-?",
          r.text[:120])

    # 3) Fixing the broken SKU keeps the old string linked (legacy).
    r = cl.post("/api/sku-overwrites", json={
        "target": "ZWO FD-M42-?", "new_sku": "ZWO FD-M42-II",
        "changed_by": "C72", "confirmed": True})
    check("SKU fix succeeds and reports the legacy link",
          r.status_code == 201 and r.json().get("legacy_linked") is True,
          r.text[:200])
    r = cl.get("/api/products/by-barcode/ZWO%20FD-M42-%3F")
    check("the old broken SKU still resolves after the fix (alias)",
          r.status_code == 200 and r.json()["sku"] == "ZWO FD-M42-II",
          r.text[:200])
    hist = cl.get("/api/history").json()
    legacy = next((e for e in hist["events"]
                   if e.get("type") == "barcode-linked"
                   and "old code kept" in (e.get("detail") or "")), None)
    check("History names the kept code with its unlink undo",
          legacy is not None and legacy.get("undo", {}).get("kind")
          == "barcode-alias", legacy)

    # 4) A CLEAN replaced barcode is never auto-linked.
    r = cl.post("/api/barcode-overwrites", json={
        "target": "OK-1", "new_barcode": "222",
        "changed_by": "C72", "confirmed": True})
    check("clean barcode overwrite works, no legacy link",
          r.status_code == 201 and r.json().get("legacy_linked") is False,
          r.text[:200])
    r = cl.get("/api/products/by-barcode/111")
    check("the clean old barcode is gone for real", r.status_code == 404,
          r.status_code)

    # 5) Aliases anchored to a SKU follow it through a SKU overwrite.
    r = cl.post("/api/barcode-aliases", json={
        "alias_barcode": "555000111", "target": "OK-1",
        "created_by": "Nick"})
    check("manual alias created", r.status_code == 201, r.text[:120])
    cl.post("/api/sku-overwrites", json={
        "target": "OK-1", "new_sku": "OK-2",
        "changed_by": "C72", "confirmed": True})
    r = cl.get("/api/products/by-barcode/555000111")
    check("the alias survives the SKU change and resolves to the new SKU",
          r.status_code == 200 and r.json()["sku"] == "OK-2",
          r.text[:200])

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
