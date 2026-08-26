"""Check-step polish (Nick, 2026-08-26, four asks):
1. bad-chars names WHICH field broke and shows the offending character
   bracketed - the real character recovered from live Shopify, falling
   back to the stored '?' when the live fetch fails;
2. a SKU/barcode overwrite pushes the new value into open-batch rows
   and live tag records, so web and C72 both show the fix immediately;
3. the Check list orders biggest problems first - count-mismatch is
   explicitly the LEAST important and sinks to the bottom;
4. the product preview payload carries the Shopify-admin URL so the
   title can link to the product."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["SHOPIFY_WRITE_MODE"]="scan_station_only,verify_onhand"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_checkpolish_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_engine
from app.models import BinMapEntry, RfidAssignment
from sqlalchemy.orm import Session
from sqlalchemy import select
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

PRIME = "\u2033"  # the real double-prime character Shopify holds
V_SKU = "gid://shopify/ProductVariant/11"
V_BC  = "gid://shopify/ProductVariant/22"

# Live catalog for the API-side lookups (overwrites need real gids).
CAT = [
    {"shopify_variant_id": V_SKU, "shopify_product_id":
     "gid://shopify/Product/11", "product_title": "ZWO Ha 7nm Filter",
     "variant_title": None, "sku": "ZWO-HA 7nm 1.25?", "barcode": "901",
     "bin_location": "F1-1"},
    {"shopify_variant_id": V_BC, "shopify_product_id":
     "gid://shopify/Product/22", "product_title": "Okay Widget",
     "variant_title": None, "sku": "OKAY-2", "barcode": "AB?CD",
     "bin_location": "F1-1"},
]
def fake_lookup(t):
    for p in CAT:
        if p["barcode"] == t or p["sku"] == t:
            return dict(p)
    return None
def fake_idents(vid):
    if vid == V_SKU:
        # Shopify still holds the REAL character the VARCHAR mangled.
        return {"sku": f"ZWO-HA 7nm 1.25{PRIME}", "barcode": "901"}
    raise RuntimeError("no answer")  # exercises the stored-'?' fallback

with patch("app.shopify.lookup_barcode", side_effect=fake_lookup), \
     patch("app.shopify.lookup_barcode_all",
           side_effect=lambda t: [fake_lookup(t)] if fake_lookup(t) else []), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.shopify.get_variant_idents", side_effect=fake_idents), \
     patch("app.shopify.update_variant_barcode"), \
     patch("app.shopify.update_variant_sku"), \
     patch("app.main._kick_orders_sync_soon"):
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="ZWO-HA 7nm 1.25?", barcode="901",
                          product_title="ZWO Ha 7nm Filter", bin="F1-1",
                          qty=1, shopify_variant_id=V_SKU,
                          shopify_product_id="gid://shopify/Product/11"))
        s.add(BinMapEntry(sku="OKAY-2", barcode="AB?CD",
                          product_title="Okay Widget", bin="F1-1",
                          qty=1, shopify_variant_id=V_BC,
                          shopify_product_id="gid://shopify/Product/22"))
        s.add(BinMapEntry(sku="WRONG-1", barcode="903",
                          product_title="Stray Widget", bin="G9-9",
                          qty=1, shopify_variant_id="t:W1"))
        s.add(BinMapEntry(sku="COUNT-1", barcode="904",
                          product_title="Count Widget", bin="F1-1",
                          qty=5, shopify_variant_id="t:C1"))
        s.commit()

    bid = cl.post("/api/batches", json={
        "bin": "F1-1", "created_by": "Nick"}).json()["id"]
    for code in ("901", "AB?CD", "903", "904"):
        cl.post(f"/api/batches/{bid}/scan", json={"code": code})

    review = cl.get(f"/api/batches/{bid}/review").json()["items"]
    by_sku = {e["item"]["sku"]: e for e in review}

    # --- 1) bad-chars names the field and brackets the character -------
    e = by_sku.get("ZWO-HA 7nm 1.25?")
    check("a broken SKU is flagged with the field named",
          e is not None and "bad-chars" in e["flags"]
          and (e.get("bad_chars") or {}).get("sku")
          == f"ZWO-HA 7nm 1.25[{PRIME}]", e and e.get("bad_chars"))
    check("the clean barcode is NOT blamed alongside it",
          "barcode" not in (e.get("bad_chars") or {}), e.get("bad_chars"))
    e = by_sku.get("OKAY-2")
    check("a broken barcode brackets the stored '?' when live fails",
          e is not None and (e.get("bad_chars") or {}).get("barcode")
          == "AB[?]CD" and "sku" not in (e.get("bad_chars") or {}),
          e and e.get("bad_chars"))

    # --- 3) ordering: biggest problems first, count-mismatch last ------
    order = [e["item"]["sku"] for e in review]
    check("bad-chars rows lead the Check list (shelf order held inside)",
          sorted(order[:2]) == sorted(["ZWO-HA 7nm 1.25?", "OKAY-2"]),
          order)
    check("wrong-bin outranks a bare count nudge",
          order[2] == "WRONG-1", order)
    ct = by_sku.get("COUNT-1")
    check("the count-mismatch-only row sinks to the bottom",
          order[-1] == "COUNT-1"
          and ct is not None and ct["flags"] == ["count-mismatch"],
          (order, ct and ct["flags"]))

    # --- 2) overwrites reach open-batch rows and tag records -----------
    items = cl.get(f"/api/batches/{bid}").json()["items"]
    okay = next(i for i in items if i["sku"] == "OKAY-2")
    cl.post(f"/api/batches/{bid}/pair", json={
        "epc": "CD000000000000000000CD01", "item_id": okay["id"],
        "created_by": "Nick"})
    r = cl.post("/api/barcode-overwrites", json={
        "target": "AB?CD", "new_barcode": "61230045678",
        "changed_by": "Nick", "confirmed": True})
    check("barcode overwrite succeeds", r.status_code == 201, r.text[:200])
    items = cl.get(f"/api/batches/{bid}").json()["items"]
    okay = next(i for i in items if i["sku"] == "OKAY-2")
    check("the open batch row shows the new barcode immediately",
          okay["barcode"] == "61230045678", okay["barcode"])
    with Session(get_engine()) as s:
        tag = s.scalar(select(RfidAssignment).where(
            RfidAssignment.shopify_variant_id == V_BC))
        check("the live tag record follows the barcode too",
              tag is not None and tag.barcode == "61230045678",
              tag and tag.barcode)

    r = cl.post("/api/sku-overwrites", json={
        "target": "901", "new_sku": "ZWO-HA 7nm 1.25 II",
        "changed_by": "Nick", "confirmed": True})
    check("SKU overwrite succeeds", r.status_code == 201, r.text[:200])
    items = cl.get(f"/api/batches/{bid}").json()["items"]
    check("the open batch row shows the new SKU immediately",
          any(i["sku"] == "ZWO-HA 7nm 1.25 II" for i in items),
          [i["sku"] for i in items])

    # --- 4) the preview payload carries the admin link -----------------
    data = cl.get("/api/product-history?term=61230045678").json()
    check("the product preview carries its Shopify-admin URL",
          (data.get("product") or {}).get("admin_url")
          == "https://t.myshopify.com/admin/products/22",
          data.get("product", {}).get("admin_url"))

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
