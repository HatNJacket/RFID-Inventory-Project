"""Sorter one-pile alternative (Nick, 2026-09-09): 6 boxes all fit SO
943, but one product's line was already fully received there, so
strict coverage split a lone box off to SO 931. sort-match now offers
"pack it all into one order" whenever a single order's LINE LIST -
exhausted lines included - covers every matched product; extras ride
as flagged overflow. Offered beside the verdict, never forced.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_sortrepile_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

PLAN = {"configured": True, "ok": True, "orders": [
    {"order_id": 1266, "reference_number": 943, "vendor": "Svbony",
     "items": [
         {"sku": "SVB-A", "barcode": None, "title": "A", "remaining": 3},
         {"sku": "SVB-B", "barcode": None, "title": "B", "remaining": 2},
         # Already fully received on THIS order - the Nick case.
         {"sku": "SVB-C", "barcode": None, "title": "C", "remaining": 0},
     ]},
    {"order_id": 1254, "reference_number": 931, "vendor": "Svbony",
     "items": [
         {"sku": "SVB-C", "barcode": None, "title": "C", "remaining": 1},
     ]},
]}

with patch("app.planner.open_orders_lines", return_value=PLAN), \
     patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.main._maybe_refresh_bin_map", return_value=False), \
     patch("app.main.oneleft"):
  with TestClient(app) as cl:
    r = cl.post("/api/receiving/sort-match", json={
        "counts": [
            {"code": "SVB-A", "count": 3},
            {"code": "SVB-B", "count": 2},
            {"code": "SVB-C", "count": 1},
        ],
        "requested_by": "C72",
    })
    d = r.json()
    check("sort-match answered", r.status_code == 200, r.text[:300])
    v = d["verdict"]
    refs = [o["reference_number"] for o in v["orders"]]
    check("default verdict still splits (SO 943 + SO 931)",
          not v["consolidated"] and sorted(map(str, refs))
          == ["931", "943"], v)
    alt = v.get("one_order_alternative")
    check("one-pile alternative offered: SO 943",
          alt is not None and str(alt["reference_number"]) == "943", alt)
    check("the exhausted line's box rides as flagged overflow",
          alt and alt["overflow_boxes"] == 1
          and any(p["code"] == "SVB-C" and p["overflow"] == 1
                  for p in alt["products"]), alt)
    check("in-quantity products carry no overflow",
          alt and all(p["overflow"] == 0 for p in alt["products"]
                      if p["code"] != "SVB-C"), alt)

    # ---- a pallet one order covers outright: no alternative noise ----
    r = cl.post("/api/receiving/sort-match", json={
        "counts": [{"code": "SVB-A", "count": 2}],
        "requested_by": "C72",
    })
    v = r.json()["verdict"]
    check("clean consolidation offers no alternative",
          v["consolidated"] and v.get("one_order_alternative") is None,
          v)

print()
if fails:
    print(f"{len(fails)} FAILURE(S):"); [print("  -", f) for f in fails]
    sys.exit(1)
print("ALL PASS")
