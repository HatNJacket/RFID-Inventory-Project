"""Clear queue & reprint all (Nick, 2026-08-25): the printer ran out of
wax mid-run, printed blanks, and marked all 46 jobs done. The Print
step's new button voids every label queued for the batch (auto-created
tag records die with the blanks), queues a fresh full set in the same
walking order, refuses without confirmation, and refuses once pairing
has started."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_reprintall_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_engine
from app.models import BatchItem, PrintJob, RfidAssignment
from sqlalchemy.orm import Session
from sqlalchemy import select
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

CAT = {
    "801": {"shopify_variant_id":"t:DELTA","shopify_product_id":"g:1",
            "product_title":"Delta Diagonal","variant_title":None,
            "sku":"DELTA-1","barcode":"801","bin_location":"Q1-1"},
    "802": {"shopify_variant_id":"t:ECHO","shopify_product_id":"g:2",
            "product_title":"Echo Eyepiece","variant_title":None,
            "sku":"ECHO-1","barcode":"802","bin_location":"Q1-1"},
}
def fake_lookup(t):
    if t in CAT: return dict(CAT[t])
    for p in CAT.values():
        if p["sku"] == t: return dict(p)
    return None

with patch("app.shopify.lookup_barcode", side_effect=fake_lookup), \
     patch("app.shopify.lookup_barcode_all",
           side_effect=lambda t: [fake_lookup(t)] if fake_lookup(t) else []), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.main._kick_orders_sync_soon"):
  with TestClient(app) as cl:
    bid = cl.post("/api/batches",
                  json={"bin": "Q1-1", "created_by": "Nick"}).json()["id"]
    # walking order: ECHO first, then DELTA twice
    cl.post(f"/api/batches/{bid}/scan", json={"code": "802"})
    cl.post(f"/api/batches/{bid}/scan", json={"code": "801"})
    cl.post(f"/api/batches/{bid}/scan", json={"code": "801"})
    r = cl.post(f"/api/batches/{bid}/queue-labels", json={})
    check("labels queue (3 jobs)", r.json().get("count") == 3, r.text[:200])

    # The agent "prints" everything - out of wax, but every job reports
    # done and auto-creates its tag assignment.
    claimed = cl.post("/api/print-jobs/claim?limit=10").json()["jobs"]
    for j in claimed:
        cl.post(f"/api/print-jobs/{j['id']}/complete"
                "?create_assignment=true")
    with Session(get_engine()) as s:
        n_assign = len(s.scalars(select(RfidAssignment)).all())
    check("blank labels created tag records", n_assign == 3, n_assign)
    old_epcs = [j["epc"] for j in claimed]

    r = cl.post(f"/api/batches/{bid}/reprint-all",
                json={"requested_by": "Nick"})
    check("refuses without the binned-strip confirmation",
          r.status_code == 409, r.status_code)

    r = cl.post(f"/api/batches/{bid}/reprint-all",
                json={"requested_by": "Nick", "confirmed": True})
    check("reprint-all voids and requeues",
          r.status_code == 200 and r.json()["voided"] == 3
          and r.json()["queued"] == 3
          and r.json()["tags_unlinked"] == 3, r.text[:300])

    with Session(get_engine()) as s:
        jobs = s.scalars(select(PrintJob).order_by(PrintJob.id)).all()
        old = [j for j in jobs if j.epc in old_epcs]
        new = [j for j in jobs if j.epc not in old_epcs]
        check("old jobs are voided",
              all(j.status == "voided" for j in old),
              [j.status for j in old])
        check("fresh jobs are pending with new EPCs",
              len(new) == 3 and all(j.status == "pending" for j in new),
              [(j.status, j.epc) for j in new])
        check("fresh jobs keep the walking order (ECHO first)",
              [j.sku for j in new] == ["ECHO-1", "DELTA-1", "DELTA-1"],
              [j.sku for j in new])
        check("the blank labels' tag records are gone",
              len(s.scalars(select(RfidAssignment)).all()) == 0, None)

    hist = cl.get("/api/history").json()
    check("History logs the batch reprint", any(
        e["type"] == "batch-reprinted" for e in hist["events"]),
          [e["type"] for e in hist["events"]][:6])

    # Once pairing has started, the button refuses.
    with Session(get_engine()) as s:
        item = s.scalars(select(BatchItem).where(
            BatchItem.batch_id == bid,
            BatchItem.sku == "ECHO-1")).one()
        item.paired_count = 1
        s.commit()
    r = cl.post(f"/api/batches/{bid}/reprint-all",
                json={"requested_by": "Nick", "confirmed": True})
    check("refuses once pairing has started",
          r.status_code == 409 and "paired" in r.json()["detail"],
          r.text[:200])

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
