"""Unavailable stock on the audit (2026-09-29, F9160A): unavailable units
may be set aside off the shelf, so they only widen the TOP of the shelf
range; the C72's one-tap "Note it" leaves a note the bin's checks read
until its next completed audit.
"""
import os, sys, tempfile
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_unavail_shelf_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
import app.main as M
from app.main import app
from app.database import get_engine
from app.models import BarcodeChange, BinAudit, BinMapEntry, RfidAssignment
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

def item(rep, sku):
    return [x for x in rep["items"] if x["sku"] == sku][0]

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}):
  M._maybe_refresh_bin_map = lambda *a, **k: False
  M._pickup_pending_map = lambda: {}
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        # F9160A: 0 sellable + 1 unavailable, untagged, set aside elsewhere.
        s.add(BinMapEntry(sku="F9160A", product_title="Filter A", bin="I1-2",
                          qty=0, unavailable=1, shopify_variant_id="t:A"))
        # B: 2 sellable + 1 unavailable, 3 tags, 2 heard.
        s.add(BinMapEntry(sku="B-2", product_title="Filter B", bin="I1-2",
                          qty=2, unavailable=1, shopify_variant_id="t:B"))
        for n in range(3):
            s.add(RfidAssignment(rfid_id=f"E0000000000000000000B00{n}",
                                 shopify_variant_id="t:B",
                                 product_title="Filter B", sku="B-2",
                                 bin_location="I1-2"))
        s.commit()

    heard = ["E0000000000000000000B000", "E0000000000000000000B001"]
    rep = cl.post("/api/bins/I1-2/check", json={"epcs": heard}).json()
    a = item(rep, "F9160A")
    check("an unavailable unit only widens the top: F9160A reads 0-1",
          a["shelf_lo"] == 0 and a["shelf_hi"] == 1, (a.get("shelf_lo"), a.get("shelf_hi")))
    check("  ...and nothing heard is inside it", a["in_range"] is True, a["in_range"])
    b = item(rep, "B-2")
    check("B: floor stays the sellable expectation, top adds the set-aside",
          b["shelf_lo"] == 2 and b["shelf_hi"] == 4, (b["shelf_lo"], b["shelf_hi"]))
    check("  ...2 heard is in range", b["in_range"] is True)
    check("no notes yet", a["unavailable_noted"] == 0 and b["unavailable_noted"] == 0)

    r = cl.post("/api/audit/unavailable-note", json={
        "sku": "F9160A", "bin": "i1-2", "qty": 1, "by": "C72"})
    check("noting answers", r.status_code == 200 and r.json()["bin"] == "I1-2", r.text)
    rep = cl.post("/api/bins/I1-2/check", json={"epcs": heard}).json()
    check("the check reads the note", item(rep, "F9160A")["unavailable_noted"] == 1)
    check("  ...only for that product", item(rep, "B-2")["unavailable_noted"] == 0)
    rep_r = cl.post("/api/bins/I1/check", json={"epcs": heard}).json()
    check("a rack-wide check reads it too",
          item(rep_r, "F9160A")["unavailable_noted"] == 1)

    cl.post("/api/audit/unavailable-note", json={
        "sku": "B-2", "bin": "I1-2", "qty": 5, "by": "C72"})
    rep = cl.post("/api/bins/I1-2/check", json={"epcs": heard}).json()
    check("a note never claims more than the unavailable bucket holds",
          item(rep, "B-2")["unavailable_noted"] == 1)

    cl.post("/api/audit/unavailable-note", json={
        "sku": "B-2", "bin": "I1-2", "qty": 0, "by": "C72"})
    rep = cl.post("/api/bins/I1-2/check", json={"epcs": heard}).json()
    check("the newest note stands (0 withdraws)",
          item(rep, "B-2")["unavailable_noted"] == 0)

    with Session(get_engine()) as s:
        ev = s.scalars(select(BarcodeChange).where(
            BarcodeChange.changed_field == "unavailable-noted")).all()
    check("every note is History-logged", len(ev) == 3, len(ev))

    # A completed audit after the note starts fresh.
    with Session(get_engine()) as s:
        s.add(BinAudit(bin="I1-2", audited_at=datetime.now(timezone.utc)
                       + timedelta(seconds=5), audited_by="Nick",
                       baseline="{}"))
        s.commit()
    rep = cl.post("/api/bins/I1-2/check", json={"epcs": heard}).json()
    check("notes older than the bin's last audit no longer count",
          item(rep, "F9160A")["unavailable_noted"] == 0)

    bad = cl.post("/api/audit/unavailable-note", json={"sku": "X", "bin": "I1-2", "qty": -1})
    check("a negative count is refused", bad.status_code == 422, bad.status_code)

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("ALL PASS")
