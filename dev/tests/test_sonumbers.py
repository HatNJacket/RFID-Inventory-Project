"""SO-number intake belt (Nick, 2026-09-09): the planner's Print-labels
payload carried its INTERNAL order id where the SO number belonged, so
History and open batches read "SO 1266" for SO 943. Incoming receiving
references now translate internal ids to the real reference number -
but ONLY when the planner order under that id names the same vendor
AND is still open-ish, because ids and reference numbers overlap
(a correct "SO 943" collides with a closed askar order's id 943).
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_sonumbers_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
import app.main as m
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

ORDERS = {
    # internal id -> planner order
    1266: {"reference_number": 943, "vendor": "Svbony",
           "status": "partial_received"},
    943:  {"reference_number": 595, "vendor": "askar", "status": "closed"},
    948:  {"reference_number": 601, "vendor": "Buckeye Stargazer",
           "status": "closed"},
    1271: {"reference_number": 948, "vendor": "Buckeye Stargazer",
           "status": "open"},
}
def fake_get(path, params=None, operator=None):
    oid = int(path.rsplit("/", 1)[1])
    if oid in ORDERS:
        return ORDERS[oid]
    raise RuntimeError("404")

with patch("app.planner._get", side_effect=fake_get), \
     patch("app.main._maybe_refresh_bin_map", return_value=False), \
     patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.main.oneleft"):
  with TestClient(app) as cl:
    m._so_ref_cache.clear()

    check("internal id translates (vendor + open-ish matched)",
          m._normalize_so_reference("SO 1266 · Svbony")
          == "SO 943 · Svbony",
          m._normalize_so_reference("SO 1266 · Svbony"))
    check("a REAL SO number colliding with a closed order's id stays",
          m._normalize_so_reference("SO 943 · Svbony")
          == "SO 943 · Svbony", "")
    check("same-vendor collision blocked by the closed-status gate",
          m._normalize_so_reference("SO 948 · Buckeye Stargazer")
          == "SO 948 · Buckeye Stargazer", "")
    check("open same-vendor internal id still translates",
          m._normalize_so_reference("SO 1271 · Buckeye Stargazer")
          == "SO 948 · Buckeye Stargazer", "")
    check("vendor mismatch never translates",
          m._normalize_so_reference("SO 1266 · Celestron")
          == "SO 1266 · Celestron", "")
    check("no-SO references pass through",
          m._normalize_so_reference("whatever · Svbony")
          == "whatever · Svbony", "")
    with patch("app.planner._get", side_effect=RuntimeError("down")):
        m._so_ref_cache.clear()
        check("planner outage keeps the reference exactly as sent",
              m._normalize_so_reference("SO 1266 · Svbony")
              == "SO 1266 · Svbony", "")
    m._so_ref_cache.clear()

    # ---- end to end: the batch label stores the REAL number -----------
    r = cl.post("/api/receiving/prints", json={
        "items": [{"sku": "SVB-1", "quantity": 1}],
        "requested_by": "planner",
        "reference": "SO 1266 · Svbony",
    })
    d = r.json()
    check("intake answered", r.status_code in (200, 201), r.text[:300])
    check("batch label carries the real SO number",
          "SO 943" in (d.get("batch") or {}).get("created_by", "")
          and "1266" not in (d.get("batch") or {}).get("created_by", ""),
          d.get("batch"))

print()
if fails:
    print(f"{len(fails)} FAILURE(S):"); [print("  -", f) for f in fails]
    sys.exit(1)
print("ALL PASS")
