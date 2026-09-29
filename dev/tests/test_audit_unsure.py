"""Unsure silent tags (2026-09-29): the C72 parks a silent tag it can't
call with an optional note; the check marks it; the web's Marked-unsure
list works it (resolve), and a row whose tag is gone resolves itself.
"""
import os, sys, tempfile
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_audit_unsure_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
import app.main as M
from app.main import app
from app.database import get_engine
from app.models import BarcodeChange, BinMapEntry, RfidAssignment
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}):
  M._maybe_refresh_bin_map = lambda *a, **k: False
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="F9143D", product_title="Filter", bin="I1-1",
                          qty=0, shopify_variant_id="t:F9143D"))
        for epc in ("E00000000000000000000A01", "E00000000000000000000A02"):
            s.add(RfidAssignment(rfid_id=epc, shopify_variant_id="t:F9143D",
                                 product_title="Filter", sku="F9143D",
                                 bin_location="I1-1"))
        s.commit()

    r = cl.post("/api/audit/unsure", json={
        "epc": "e00000000000000000000a01", "bin": "I1-1",
        "note": "  maybe behind the scopes  ", "by": "C72"})
    check("marking a silent tag unsure answers", r.status_code == 200, r.text)
    u = r.json()
    check("the row carries product, bin and the trimmed note",
          u["sku"] == "F9143D" and u["bin"] == "I1-1"
          and u["note"] == "maybe behind the scopes" and u["active"], u)

    r2 = cl.post("/api/audit/unsure", json={
        "epc": "E00000000000000000000A01", "note": "", "by": "C72"})
    lst = cl.get("/api/audit/unsure").json()
    check("re-marking updates the one open row (note cleared)",
          lst["count"] == 1 and lst["entries"][0]["note"] is None
          and r2.json()["id"] == u["id"], lst)

    bad = cl.post("/api/audit/unsure", json={"epc": "E0000000DEAD"})
    check("an unpaired EPC is refused", bad.status_code == 404, bad.text)

    with Session(get_engine()) as s:
        ev = s.scalars(select(BarcodeChange).where(
            BarcodeChange.changed_field == "audit-unsure")).all()
    check("every mark is History-logged", len(ev) == 2, len(ev))

    # The check names the unsure one among the silent tags (nothing swept).
    rep = cl.post("/api/bins/I1-1/check", json={"epcs": []}).json()
    it = [x for x in rep["items"] if x["sku"] == "F9143D"][0]
    check("the check marks unsure silent tags",
          it["unsure_epcs"] == ["E00000000000000000000A01"]
          and len(it["silent_epcs"]) == 2, it.get("unsure_epcs"))

    # Resolve: dismissed logs, then the list drops it.
    rr = cl.post(f"/api/audit/unsure/{u['id']}/resolve",
                 json={"resolution": "dismissed", "by": "Nick"})
    check("resolving answers", rr.status_code == 200
          and rr.json()["status"] == "resolved", rr.text)
    check("a resolved row leaves the open list",
          cl.get("/api/audit/unsure").json()["count"] == 0)
    check("resolved rows still list under status=all",
          cl.get("/api/audit/unsure?status=all").json()["count"] == 1)
    rb = cl.post("/api/audit/unsure/9999/resolve",
                 json={"resolution": "sold"})
    check("an unknown row 404s", rb.status_code == 404, rb.text)

    # A tag that's gone (unpaired elsewhere) resolves itself on read.
    cl.post("/api/audit/unsure", json={"epc": "E00000000000000000000A02",
                                       "by": "C72"})
    cl.delete("/api/rfid-assignments/E00000000000000000000A02?by=Nick")
    lst = cl.get("/api/audit/unsure").json()
    check("an open row whose tag is gone resolves itself",
          lst["count"] == 0, lst)
    al = cl.get("/api/audit/unsure?status=all").json()["entries"]
    gone = [e for e in al if e["epc"] == "E00000000000000000000A02"][0]
    check("  ...filed as tag-gone", gone["resolution"] == "tag-gone"
          and gone["status"] == "resolved", gone)

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("ALL PASS")
