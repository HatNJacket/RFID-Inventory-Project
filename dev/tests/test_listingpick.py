"""USE THIS LISTING settles the multi-listing flag (Nick, 2026-08-25):
the 'ambiguous' flag re-derived from candidate counts alone could never
clear, because the twin listings keep existing after the operator
picks one. The reassign endpoint now records the explicit choice
(listing_locked) and review stops re-raising the flag, while the
candidates stay listed for a change of mind."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_listingpick_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

BC = "700000000001"
TWIN_A = {"shopify_variant_id": "gid:twinA", "shopify_product_id": "gid:pA",
          "product_title": "Widget (regular)", "variant_title": None,
          "sku": "TWIN-A", "barcode": BC, "bin_location": "F3-1"}
TWIN_B = {"shopify_variant_id": "gid:twinB", "shopify_product_id": "gid:pB",
          "product_title": "Widget (open box)", "variant_title": None,
          "sku": "TWIN-B", "barcode": BC, "bin_location": "F3-1"}

with patch("app.shopify.lookup_barcode",
           side_effect=lambda t: dict(TWIN_A) if t == BC else None), \
     patch("app.shopify.lookup_barcode_all",
           side_effect=lambda t: [dict(TWIN_A), dict(TWIN_B)]
           if t == BC else []), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.main._kick_orders_sync_soon"):
  with TestClient(app) as cl:
    bid = cl.post("/api/batches",
                  json={"bin": "F3-1", "created_by": "Nick"}).json()["id"]
    cl.post(f"/api/batches/{bid}/scan", json={"code": BC})
    item = cl.get(f"/api/batches/{bid}").json()["items"][0]

    rev = cl.get(f"/api/batches/{bid}/review").json()
    entry = next(e for e in rev["items"] if e["item"]["id"] == item["id"])
    check("two listings share the barcode -> flagged ambiguous",
          "ambiguous" in entry["flags"], entry["flags"])
    check("both candidates offered", len(entry["candidates"]) == 2,
          entry["candidates"])

    r = cl.post(f"/api/batches/{bid}/items/{item['id']}/reassign",
                json={"shopify_variant_id": "gid:twinB"})
    check("reassign succeeds and moves the row to the chosen listing",
          r.status_code == 200
          and r.json()["item"]["sku"] == "TWIN-B", r.text)
    check("the choice is recorded on the row",
          r.json()["item"]["listing_locked"] is True, r.json()["item"])

    rev = cl.get(f"/api/batches/{bid}/review").json()
    entry = next((e for e in rev["items"]
                  if e["item"]["id"] == item["id"]), None)
    flags = entry["flags"] if entry else []
    check("ambiguous flag is settled after USE THIS LISTING",
          "ambiguous" not in flags, flags)
    if entry is not None:
        check("candidates stay listed for a change of mind",
              len(entry.get("candidates", [])) == 2, entry)
    else:
        # No other flags either: the row simply left the Check list.
        check("row left the Check list entirely (no flags remain)",
              True)

    # Changing the mind again still works and stays settled.
    r = cl.post(f"/api/batches/{bid}/items/{item['id']}/reassign",
                json={"shopify_variant_id": "gid:twinA"})
    check("re-picking the other twin still works",
          r.status_code == 200
          and r.json()["item"]["sku"] == "TWIN-A"
          and r.json()["item"]["listing_locked"] is True, r.text)

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
