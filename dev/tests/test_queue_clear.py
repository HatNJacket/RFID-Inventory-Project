"""Print queue guards (2026-10-06, SO 969):
- /fail never flips a printed (done) label to error - the agent lost
  the printer's status mid-run and reported a whole burst failed, which
  made the next print pass print 90 labels again.
- Clear stopped: a stopped run can be let go for good - the labels stay
  canceled, leave the Queue tab, and Resume has nothing to offer.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_queue_clear_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
import app.main as M
from app.main import app
from app.database import get_engine
from app.models import BarcodeChange, PrintJob
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

def statuses(ids):
    with Session(get_engine()) as s:
        rows = {j.id: j for j in s.scalars(select(PrintJob).where(PrintJob.id.in_(ids)))}
    return [(rows[i].status, rows[i].error) for i in ids]

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]):
  M._maybe_refresh_bin_map = lambda *a, **k: False
  with TestClient(app) as cl:
    r = cl.post("/api/print-jobs", json={
        "quantity": 3, "shopify_variant_id": "t:1", "product_title": "T",
        "sku": "SKU-1"})
    ids = [j["id"] for j in r.json()["jobs"]]
    cl.post("/api/print-jobs/claim", params={"limit": 10})
    cl.post(f"/api/print-jobs/{ids[0]}/complete", json={"create_assignment": False})

    # ---- /fail guard ----
    r = cl.post(f"/api/print-jobs/{ids[0]}/fail", json={"error": "status lost"})
    check("failing a printed label is refused (409)", r.status_code == 409, r.text)
    check("  ...and it stays done", statuses(ids[:1]) == [("done", None)], statuses(ids[:1]))
    r = cl.post(f"/api/print-jobs/{ids[1]}/fail", json={"error": "status lost"})
    check("failing a label still printing works", r.status_code == 200
          and statuses(ids[1:2]) == [("error", "status lost")], r.text)

    # ---- Clear stopped ----
    r = cl.post("/api/print-jobs", json={
        "quantity": 2, "shopify_variant_id": "t:2", "product_title": "U",
        "sku": "SKU-2"})
    ids2 = [j["id"] for j in r.json()["jobs"]]
    r = cl.post("/api/print-jobs/stop", json={"requested_by": "Nick"})
    check("stop cancels the waiting labels", r.status_code == 200 and r.json()["canceled"] == 2, r.text)
    q = cl.get("/api/print-jobs").json()
    check("  ...and Resume has 2 to offer", q["resumable_stopped"] == 2, q["resumable_stopped"])

    r = cl.post("/api/print-jobs/clear-stopped", json={"requested_by": "Nick"})
    check("clear stopped answers with the count", r.status_code == 200 and r.json()["cleared"] == 2, r.text)
    check("  ...the labels stay canceled, marked cleared",
          statuses(ids2) == [("canceled", M.STOPPED_CLEARED)] * 2, statuses(ids2))
    q = cl.get("/api/print-jobs").json()
    check("  ...Resume has nothing left", q["resumable_stopped"] == 0, q["resumable_stopped"])
    r = cl.post("/api/print-jobs/resume", json={"requested_by": "Nick"})
    check("  ...and says so when pressed", r.status_code == 422, r.status_code)
    r = cl.post("/api/print-jobs/clear-stopped", json={"requested_by": "Nick"})
    check("clearing twice is refused", r.status_code == 422, r.status_code)
    with Session(get_engine()) as s:
        ev = s.scalars(select(BarcodeChange).where(
            BarcodeChange.changed_field == "print-clear")).all()
    check("the clear is History-logged", len(ev) == 1 and ev[0].changed_by == "Nick",
          [(e.changed_field, e.changed_by) for e in ev])
    h = cl.get("/api/history").json()
    types = {e.get("type") for e in h.get("events", [])}
    check("  ...as its own event type", "stopped-cleared" in types, sorted(types)[:12])

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("ALL PASS")
