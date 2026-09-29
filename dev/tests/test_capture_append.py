"""Re-sweeps send only NEW tags (2026-09-29): an audit's capture takes an
append, and a rack-scoped capture/append keeps this rack's tags plus
unpaired ones and hands back other racks' tags as "dropped".
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_capture_append_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import app.main as M
from app.main import app
from app.database import get_engine
from app.models import BinMapEntry, EpcCapture, RfidAssignment
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

I1A, I1B, F2A, UNK = ("E000000000000000000000A1", "E000000000000000000000A2",
                      "E000000000000000000000F1", "E000000000000000000000FF")
with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}):
  M._maybe_refresh_bin_map = lambda *a, **k: False
  M._pickup_pending_map = lambda: {}
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="I-1", product_title="I", bin="I1-1", qty=2,
                          shopify_variant_id="t:I"))
        for epc, sku, b in ((I1A, "I-1", "I1-1"), (I1B, "I-1", "I1-2"),
                            (F2A, "F-2", "F2-1")):
            s.add(RfidAssignment(rfid_id=epc, shopify_variant_id="t:" + sku,
                                 product_title=sku, sku=sku, bin_location=b))
        s.commit()

    r = cl.post("/api/epc-captures", json={
        "epcs": [I1A.lower(), F2A, UNK], "rack": "I1", "device": "C72",
        "bin": "I1-1", "note": "AUDIT I1-1"})
    d = r.json()
    check("a rack-scoped capture answers", r.status_code == 201, r.text)
    check("other racks' tags come back as dropped", d["dropped"] == [F2A], d)
    check("this rack's and unpaired tags are kept", d["epc_count"] == 2, d)
    cap = d["id"]

    r = cl.post(f"/api/epc-captures/{cap}/append", json={
        "epcs": [I1A, I1B, F2A], "rack": "I1", "device": "C72"})
    d = r.json()
    check("an append adds only what's new", r.status_code == 200
          and d["added"] == 1 and d["epc_count"] == 3, d)
    check("  ...and drops other racks again", d["dropped"] == [F2A], d)
    with Session(get_engine()) as s:
        row = s.get(EpcCapture, cap)
        have = set(row.epcs.split("\n"))
    check("the capture holds exactly the kept tags",
          have == {I1A, UNK, I1B}, have)

    rep = cl.post("/api/bins/I1-1/check", json={"capture_id": cap}).json()
    it = [x for x in rep["items"] if x["sku"] == "I-1"][0]
    # detected counts the product's tags heard anywhere in the sweep -
    # the appended I1-2 tag included.
    check("a check by the appended capture hears the new tag",
          it["detected"] == 2 and it["tags_here"] == 1, it)

    r = cl.post(f"/api/epc-captures/{cap}/append", json={"epcs": [I1A]})
    check("an append with nothing new is a no-op", r.json()["added"] == 0)
    r = cl.post("/api/epc-captures/99999/append", json={"epcs": [I1A]})
    check("an unknown capture 404s", r.status_code == 404)

    r = cl.post("/api/epc-captures", json={"epcs": [F2A], "rack": "I1"})
    d = r.json()
    check("all-foreign sweeps keep nothing and say so",
          d["id"] is None and d["dropped"] == [F2A], d)

    r = cl.post("/api/epc-captures", json={"epcs": [F2A, I1A]})
    check("no rack = the old behaviour (everything kept, no dropped key)",
          r.status_code == 201 and r.json()["epc_count"] == 2
          and "dropped" not in r.json(), r.json())

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("ALL PASS")
