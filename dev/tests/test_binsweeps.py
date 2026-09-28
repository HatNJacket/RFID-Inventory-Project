"""Per-location saved sweeps (2026-09-28): audit captures carry their
bin, checks claim unstamped captures for the location they ran
against, /api/bins/{bin}/sweeps lists a location's history (rack
included), and batch verify accepts a capture_id instead of a
re-uploaded EPC list.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_binsweeps_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
import app.main as M
from app.main import app
from app.database import get_engine
from app.models import BinMapEntry, EpcCapture
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

E1 = "AA000000000000000000D001"
E2 = "AA000000000000000000D002"

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.shopify.get_shelf_on_hand", return_value=None), \
     patch("app.main._kick_orders_sync_soon"):
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        s.add(BinMapEntry(bin="F9-1", sku="SWEEPY-1", barcode="111",
                          product_title="Sweepy Scope", qty=2,
                          shopify_variant_id="t:SWEEPY-1"))
        s.commit()

    # A capture that names its bin is on that location's history.
    r = cl.post("/api/epc-captures", json={
        "device": "C72", "note": "AUDIT F9-1", "bin": "f9-1",
        "epcs": [E1]})
    check("capture stores its bin (uppercased)",
          r.status_code == 201 and r.json()["bin"] == "F9-1", r.text)
    cap1 = r.json()["id"]

    r = cl.get("/api/bins/F9-1/sweeps")
    check("the bin lists its own sweep",
          [x["id"] for x in r.json()["sweeps"]] == [cap1], r.text)

    # A RACK-stamped capture shows on its bins' histories too.
    r = cl.post("/api/epc-captures", json={
        "device": "C72", "note": "AUDIT F9", "bin": "F9",
        "epcs": [E1, E2]})
    cap2 = r.json()["id"]
    ids = [x["id"] for x in cl.get("/api/bins/F9-1/sweeps").json()["sweeps"]]
    check("a rack sweep covers the bin's history (newest first)",
          ids == [cap2, cap1], ids)
    ids = [x["id"] for x in cl.get("/api/bins/F9/sweeps").json()["sweeps"]]
    check("the rack's own history is rack-stamped sweeps only",
          ids == [cap2], ids)

    # An UNSTAMPED capture gets claimed by the first location it is
    # checked against.
    r = cl.post("/api/epc-captures", json={
        "device": "C72", "epcs": [E2]})
    cap3 = r.json()["id"]
    r = cl.post("/api/bins/F9-1/check", json={"capture_id": cap3})
    check("check by capture answers", r.status_code == 200, r.text)
    with Session(get_engine()) as s:
        row = s.get(EpcCapture, cap3)
        check("the check claimed the capture for its bin",
              row.bin == "F9-1", row.bin)
    ids = [x["id"] for x in cl.get("/api/bins/F9-1/sweeps").json()["sweeps"]]
    check("the claimed sweep joins the history",
          ids == [cap3, cap2, cap1], ids)

    # Batch verify by capture_id: the EPC list never crosses twice.
    r = cl.post("/api/batches", json={"bin": "F9-1",
                                      "created_by": "Nick"})
    bid = r.json()["id"]
    r = cl.post(f"/api/batches/{bid}/verify", json={"capture_id": cap1})
    check("batch verify accepts a saved capture",
          r.status_code == 200 and r.json()["scanned_epcs"] == 1, r.text)
    r = cl.post(f"/api/batches/{bid}/verify", json={"capture_id": 999999})
    check("a missing capture is a clean 404", r.status_code == 404, r.text)

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("ALL PASS")
