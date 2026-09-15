"""C72 unresolved-box draft creation (Nick, 2026-09-15): when NO
listing owns a scanned box, the gun drafts one on the spot - typed
SKU, scanned code as barcode, bin metafields - with the INGREDIENT
title mark for multi-box bundle parts. Duplicate SKUs are refused.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ["SHOPIFY_WRITE_MODE"]="scan_station_only,draft_listings"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_draftcreate_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

made_calls = []
def fake_create(title, sku, barcode, bin_value):
    made_calls.append((title, sku, barcode, bin_value))
    return {"product_gid": "gid://shopify/Product/900",
            "variant_gid": "gid://shopify/ProductVariant/901",
            "sku": sku, "barcode": barcode, "title": title}

with patch("app.main.oneleft"), \
     patch("app.main.shopify.find_sku_listing") as find, \
     patch("app.main.shopify.create_draft_listing",
           side_effect=fake_create):
  with TestClient(app) as cl:
    # ---- full product ------------------------------------------------
    find.return_value = None
    r = cl.post("/api/products/create-draft", json={
        "sku": "NEW-1", "barcode": "0501112223334", "bin": "G2-1",
        "worker": "C72-test"})
    d = r.json()
    check("full-product draft created", r.status_code == 201, r.text[:200])
    check("title defaults to the SKU, no INGREDIENT mark",
          made_calls and made_calls[-1][0] == "NEW-1", made_calls)
    check("barcode + bin passed through",
          made_calls[-1][2] == "0501112223334"
          and made_calls[-1][3] == "G2-1", made_calls)
    check("gids returned", d.get("variant_gid") ==
          "gid://shopify/ProductVariant/901", d)

    # ---- ingredient --------------------------------------------------
    r = cl.post("/api/products/create-draft", json={
        "sku": "NEW-2", "title": "Big Set Box 2", "ingredient": True,
        "worker": "C72-test"})
    check("ingredient draft created", r.status_code == 201, r.text[:200])
    check("title wears INGREDIENT",
          made_calls[-1][0] == "INGREDIENT Big Set Box 2", made_calls)
    check("message names the bundle purpose",
          "INGREDIENT" in r.json().get("message", ""), r.json())

    # ---- duplicate SKU refused --------------------------------------
    find.return_value = {"product_title": "Existing Thing",
                         "status": "ACTIVE", "sku": "NEW-1"}
    n_before = len(made_calls)
    r = cl.post("/api/products/create-draft", json={"sku": "NEW-1"})
    check("existing SKU refused with 409", r.status_code == 409,
          r.text[:200])
    check("no draft attempted for the duplicate",
          len(made_calls) == n_before, made_calls)
    check("refusal names the owner",
          "Existing Thing" in r.json().get("detail", ""), r.json())

    # ---- history -----------------------------------------------------
    h = cl.get("/api/history?limit=10").json()["events"]
    ev = [x for x in h if x["type"] == "draft-created"]
    check("two draft-created history events", len(ev) == 2, h[:4])
    check("event carries sku + title",
          ev and ev[0]["sku"] == "NEW-2"
          and "INGREDIENT" in (ev[0].get("title") or ""), ev)

print()
if fails:
    print(f"{len(fails)} FAILURE(S):"); [print("  -", f) for f in fails]
    sys.exit(1)
print("ALL PASS")
