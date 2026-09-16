"""Box of multiple products (Nick, 2026-09-16): declaring an item's
boxes as sealed cases BY HAND - no registered case barcode needed. One
label + one tag per box, each worth N units; already-scanned boxes
convert from loose so nothing double-counts; undo opens them back to
loose scans. Refused for bundles, mismatched sizes, and once labels
are queued."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_casedeclare_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_engine
from app.models import Batch, BatchItem, PrintJob
from sqlalchemy.orm import Session
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.main._maybe_refresh_bin_map", return_value=False):
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        b = Batch(bin_name="D1-1", status="collecting", created_by="Nick")
        s.add(b)
        s.flush()
        a = BatchItem(batch_id=b.id, scanned_code="810", resolved=True,
                      sku="CASE-1", barcode="810",
                      product_title="Boxed Product",
                      shopify_variant_id="t:C1", qty_scanned=3,
                      bin_location="D1-1")
        bun = BatchItem(batch_id=b.id, scanned_code="811", resolved=True,
                        sku="BUN-1", barcode="811",
                        product_title="A Bundle", kind="bundle",
                        shopify_variant_id="t:B1", qty_scanned=1,
                        bin_location="D1-1")
        s.add_all([a, bun])
        s.commit()
        bid, aid, bunid = b.id, a.id, bun.id

    # ---- declare: 2 sealed boxes of 6, both already scanned loose ------
    r = cl.post(f"/api/batches/{bid}/items/{aid}/case",
                json={"units": 6, "boxes": 2, "changed_by": "C72"})
    body = r.json()
    it = body.get("item") or {}
    check("declared 2 cases of 6, converting 2 loose scans",
          r.status_code == 200 and body["converted"] == 2
          and it["case_count"] == 2 and it["case_units"] == 6
          and it["qty_scanned"] == 1, r.text[:300])
    check("message spells out the label",
          '"6 x CASE-1"' in body["message"], body.get("message"))

    # ---- one row, one case size ----------------------------------------
    r = cl.post(f"/api/batches/{bid}/items/{aid}/case",
                json={"units": 8, "boxes": 1})
    check("mismatched case size refused 409",
          r.status_code == 409 and "one case size" in r.json()["detail"],
          r.text[:250])

    # ---- add one more identical box ------------------------------------
    r = cl.post(f"/api/batches/{bid}/items/{aid}/case",
                json={"units": 6, "boxes": 1})
    it = r.json().get("item") or {}
    check("adding one more box converts the last loose scan",
          r.status_code == 200 and it["case_count"] == 3
          and it["qty_scanned"] == 0, r.text[:250])

    # ---- undo opens them back into loose scans -------------------------
    r = cl.post(f"/api/batches/{bid}/items/{aid}/case",
                json={"undo": True})
    it = r.json().get("item") or {}
    check("undo restores 3 loose scans, cases cleared",
          r.status_code == 200 and it["qty_scanned"] == 3
          and it["case_count"] == 0 and it["case_units"] is None,
          r.text[:250])

    # ---- unresolved rows welcome (Nick: where it's needed most) --------
    with Session(get_engine()) as s:
        u = BatchItem(batch_id=bid, scanned_code="UNK-99",
                      resolved=False,
                      product_title="Unresolved: UNK-99",
                      qty_scanned=2)
        s.add(u)
        s.commit()
        uid = u.id
    r = cl.post(f"/api/batches/{bid}/items/{uid}/case",
                json={"units": 12, "boxes": 2})
    body = r.json()
    it = body.get("item") or {}
    check("unresolved row declares cases too",
          r.status_code == 200 and it["case_count"] == 2
          and it["case_units"] == 12 and it["qty_scanned"] == 0
          and body["converted"] == 2, r.text[:300])
    check("message names the scanned code",
          "UNK-99" in body["message"], body.get("message"))

    # ---- guards ---------------------------------------------------------
    r = cl.post(f"/api/batches/{bid}/items/{bunid}/case",
                json={"units": 4})
    check("bundles are refused",
          r.status_code == 422 and "bundle" in r.json()["detail"].lower(),
          r.text[:200])
    r = cl.post(f"/api/batches/{bid}/items/{aid}/case", json={})
    check("missing units asks for it", r.status_code == 422, r.text[:200])
    r = cl.post(f"/api/batches/{bid}/items/{aid}/case", json={"units": 1})
    check("a case of 1 is refused by validation", r.status_code == 422,
          r.text[:200])

    # ---- labels: one per loose box + one per sealed case ---------------
    r = cl.post(f"/api/batches/{bid}/items/{aid}/case",
                json={"units": 4, "boxes": 1})
    check("re-declared 1 case of 4", r.status_code == 200, r.text[:200])
    r = cl.post(f"/api/batches/{bid}/queue-labels",
                json={"requested_by": "Nick"})
    body = r.json()
    check("strip holds 2 loose + 1 case label",
          r.status_code == 201 and body["count"] == 3, r.text[:250])
    with Session(get_engine()) as s:
        jobs = s.query(PrintJob).filter(PrintJob.batch_id == bid).all()
        case_jobs = [j for j in jobs if j.case_units]
    check("the case label carries its unit count",
          len(case_jobs) == 1 and case_jobs[0].case_units == 4
          and case_jobs[0].sku == "CASE-1", str(case_jobs))

    # ---- printed labels lock the split ---------------------------------
    r = cl.post(f"/api/batches/{bid}/items/{aid}/case",
                json={"units": 4, "boxes": 1})
    check("after labels queue, the split is locked 409",
          r.status_code == 409 and "desync" in r.json()["detail"],
          r.text[:250])

print()
print(f"{'FAIL' if fails else 'OK'}  {len(fails)} failing")
sys.exit(1 if fails else 0)
