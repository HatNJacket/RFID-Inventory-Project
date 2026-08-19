"""Bad-chars flag + fix-on-the-spot (the ZWO 'Ⅱ' SKU case): the Check
step flags items whose SKU/barcode carries a literal '?' or a non-ASCII
char (SQL Server's VARCHAR mangles those to '?', so record matching
silently breaks), and the operator can fix the SKU/barcode right there
via the overwrite endpoints — for ANY item, flagged or not."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["SHOPIFY_WRITE_MODE"]="scan_station_only,verify_onhand"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_badchars_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

# The mangled twin pair: stored SKU carries a '?', live Shopify carries
# the actual Unicode char. A clean product rides along as control.
BC_BAD = "6977641321488"
BC_OK = "111"
SKU_BAD_STORED = "ZWO EOS-T2-?"
SKU_LIVE = "ZWO EOS-T2-\u2161"          # what Shopify really holds
SKU_FIXED = "ZWO EOS-T2-II"

live = {
    BC_BAD: {"shopify_variant_id":"gid:bad","shopify_product_id":"gid:pb",
             "product_title":"ZWO EOS-T2 Adapter","variant_title":None,
             "sku":SKU_LIVE,"barcode":BC_BAD,"bin_location":"F2-3"},
    BC_OK: {"shopify_variant_id":"gid:ok","shopify_product_id":"gid:po",
            "product_title":"Baader UHC Filter","variant_title":None,
            "sku":"OK-1","barcode":BC_OK,"bin_location":"F2-3"},
}
sku_writes = []
bc_writes = []

def fake_lookup(t):
    if t in live: return dict(live[t])
    for p in live.values():
        if p["sku"] == t: return dict(p)
    return None

with patch("app.shopify.lookup_barcode", side_effect=fake_lookup), \
     patch("app.shopify.lookup_barcode_all",
           side_effect=lambda t: [fake_lookup(t)] if fake_lookup(t) else []), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_on_hand", return_value=2), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.shopify.update_variant_sku",
           side_effect=lambda p, v, s: sku_writes.append((v, s))), \
     patch("app.shopify.update_variant_barcode",
           side_effect=lambda p, v, b: bc_writes.append((v, b))):
  with TestClient(app) as cl:
    bid = cl.post("/api/batches",
                  json={"bin":"F2-3","created_by":"Nick"}).json()["id"]
    cl.post(f"/api/batches/{bid}/scan", json={"code":BC_BAD})
    cl.post(f"/api/batches/{bid}/scan", json={"code":BC_OK})
    cl.post(f"/api/batches/{bid}/scan", json={"code":BC_OK})

    items = cl.get(f"/api/batches/{bid}").json()["items"]
    bad = next(i for i in items if i["barcode"] == BC_BAD)
    ok = next(i for i in items if i["barcode"] == BC_OK)
    # sqlite stores the Unicode char intact — force the prod artifact so
    # the flag sees exactly what Azure SQL would hold.
    from sqlalchemy.orm import Session as S
    from app.database import get_engine
    from app.models import BatchItem
    with S(get_engine()) as s:
        row = s.get(BatchItem, bad["id"]); row.sku = SKU_BAD_STORED
        s.commit()

    rev = cl.get(f"/api/batches/{bid}/review").json()
    by_id = {e["item"]["id"]: e for e in rev["items"]}
    check("mangled item is flagged bad-chars",
          "bad-chars" in by_id.get(bad["id"], {}).get("flags", []), rev)
    check("clean item is NOT flagged",
          "bad-chars" not in by_id.get(ok["id"], {}).get("flags", []),
          by_id.get(ok["id"]))

    # The raw Unicode form (before any DB round trip) flags too.
    with S(get_engine()) as s:
        row = s.get(BatchItem, bad["id"]); row.sku = SKU_LIVE
        s.commit()
    rev = cl.get(f"/api/batches/{bid}/review").json()
    by_id = {e["item"]["id"]: e for e in rev["items"]}
    check("raw-unicode SKU flags too",
          "bad-chars" in by_id.get(bad["id"], {}).get("flags", []), by_id)

    # Fix on the spot: overwrite the SKU via the barcode (the value that
    # still matches live Shopify), exactly what the Check editors send.
    r = cl.post("/api/sku-overwrites", json={
        "target": BC_BAD, "new_sku": SKU_FIXED,
        "changed_by": "Nick", "confirmed": True})
    check("SKU overwrite via clean barcode target succeeds",
          r.status_code == 201, (r.status_code, r.text))
    check("Shopify received the new SKU",
          sku_writes and sku_writes[-1] == ("gid:bad", SKU_FIXED),
          sku_writes)

    # The re-resolve pulls the fixed SKU into the batch row.
    live[BC_BAD]["sku"] = SKU_FIXED
    r = cl.post(f"/api/batches/{bid}/items/{bad['id']}/resolve").json()
    check("re-resolve refreshes the row", r.get("resolved") is True, r)
    check("row now carries the fixed SKU",
          r["item"]["sku"] == SKU_FIXED, r["item"])

    rev = cl.get(f"/api/batches/{bid}/review").json()
    by_id = {e["item"]["id"]: e for e in rev["items"]}
    check("flag clears once the SKU is clean",
          "bad-chars" not in by_id.get(bad["id"], {}).get("flags", []),
          by_id.get(bad["id"]))

    # Barcode overwrite works for ANY item, flagged or not.
    r = cl.post("/api/barcode-overwrites", json={
        "target": BC_OK, "new_barcode": "222",
        "changed_by": "Nick", "confirmed": True})
    check("barcode overwrite on an unflagged item succeeds",
          r.status_code == 201, (r.status_code, r.text))
    check("Shopify received the new barcode",
          bc_writes and bc_writes[-1] == ("gid:ok", "222"), bc_writes)

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("ALL PASS"); sys.exit(0)
