"""Whole-strip printing (Nick, 2026-09-16): a server-stored toggle. OFF
(default) keeps the old shape - a side trip's labels print the moment
the trip is created, one print burst per bin. ON holds a trip's labels
while the parent is still collecting; they queue WITH the parent's
labels at PRINT (parent's run first, then each trip grouped), so the
batch tears off as one strip. Held trips refuse to close before their
labels print; trips diverted AFTER the parent printed keep the
immediate print either way."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_stripmode_test.db")
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
        s.add(BatchItem(batch_id=b.id, scanned_code="800", resolved=True,
                        sku="MAIN-1", barcode="800",
                        product_title="Main Bin Product",
                        shopify_variant_id="t:M1", qty_scanned=2,
                        bin_location="D1-1"))
        s.add(BatchItem(batch_id=b.id, scanned_code="801", resolved=True,
                        sku="STRAY-1", barcode="801",
                        product_title="Stray One",
                        shopify_variant_id="t:S1", qty_scanned=3,
                        bin_location="G6-6"))
        s.add(BatchItem(batch_id=b.id, scanned_code="802", resolved=True,
                        sku="STRAY-2", barcode="802",
                        product_title="Stray Two",
                        shopify_variant_id="t:S2", qty_scanned=1,
                        bin_location="H7-7"))
        s.add(BatchItem(batch_id=b.id, scanned_code="803", resolved=True,
                        sku="OLD-TAGGED", barcode="803",
                        product_title="Old Tagged Stray",
                        shopify_variant_id="t:OT", qty_scanned=0,
                        tagged_before=2, bin_location="F5-5"))
        s.commit()
        bid = b.id

    # ---- default OFF: divert prints immediately (the old shape) --------
    r = cl.get("/api/print-agent/status")
    check("status reports strip mode off by default",
          r.status_code == 200 and r.json()["strip_at_once"] is False,
          r.text[:200])
    r = cl.post(f"/api/batches/{bid}/divert",
                json={"bin": "G6-6", "created_by": "C72"})
    body = r.json()
    check("toggle off: trip labels queue at divert",
          r.status_code == 201 and body["labels"] == 3
          and body["labels_held"] == 0
          and body["batch"]["status"] == "printing", r.text[:250])

    # ---- flip it ON ----------------------------------------------------
    r = cl.post("/api/print-strip-mode",
                json={"all_at_once": True, "worker": "Nick"})
    check("toggle flips on", r.status_code == 200
          and r.json()["all_at_once"] is True, r.text[:200])
    r = cl.get("/api/print-agent/status")
    check("status reports strip mode on",
          r.json().get("strip_at_once") is True, r.text[:200])

    # ---- ON: divert HOLDS the labels -----------------------------------
    r = cl.post(f"/api/batches/{bid}/divert",
                json={"bin": "H7-7", "created_by": "C72"})
    body = r.json()
    check("toggle on: trip labels are held",
          r.status_code == 201 and body["labels"] == 0
          and body["labels_held"] == 1
          and body["batch"]["status"] == "collecting", r.text[:250])
    check("held message names the parent's strip",
          "WITH D1-1's strip" in body["message"], body.get("message"))
    held_id = body["batch"]["id"]
    with Session(get_engine()) as s:
        n = s.query(PrintJob).filter(PrintJob.batch_id == held_id).count()
    check("really nothing queued for the held trip", n == 0, n)

    # ---- a held trip won't close before its labels print ---------------
    r = cl.post(f"/api/batches/{held_id}/close-divert")
    check("held trip refuses to close (409)",
          r.status_code == 409 and "haven't printed" in r.json()["detail"],
          r.text[:250])

    # ---- carry-only trips are never held (no labels to hold) -----------
    r = cl.post(f"/api/batches/{bid}/divert",
                json={"bin": "F5-5", "created_by": "C72"})
    body = r.json()
    check("carry-only trip still lands in pairing under the toggle",
          r.status_code == 201 and body["labels"] == 0
          and body["labels_held"] == 0
          and body["batch"]["status"] == "pairing", r.text[:250])

    # ---- parent PRINT flushes the held trip onto the same strip --------
    r = cl.post(f"/api/batches/{bid}/queue-labels",
                json={"requested_by": "Nick"})
    body = r.json()
    check("parent print queues its own labels",
          r.status_code == 201 and body["count"] == 2, r.text[:300])
    check("held side labels ride the same strip",
          body["side_labels"] == 1
          and body["side_trips"] == [
              {"id": held_id, "bin": "H7-7", "labels": 1}],
          str(body)[:300])
    with Session(get_engine()) as s:
        parent_jobs = s.query(PrintJob).filter(
            PrintJob.batch_id == bid).all()
        side_jobs = s.query(PrintJob).filter(
            PrintJob.batch_id == held_id).all()
        trip = s.get(Batch, held_id)
    check("side jobs exist and carry the side bin",
          len(side_jobs) == 1 and side_jobs[0].bin_location == "H7-7"
          and side_jobs[0].sku == "STRAY-2", str(side_jobs))
    check("strip order: main bin first, side trip after",
          parent_jobs and max(j.id for j in parent_jobs)
          < min(j.id for j in side_jobs), "")
    check("flushed trip moves to printing (pair after the strip)",
          trip.status == "printing" and trip.ui_step == "pair", trip.status)

    # ---- and now it closes normally after pairing-time -----------------
    r = cl.post(f"/api/batches/{held_id}/close-divert")
    check("flushed trip closes once its labels exist",
          r.status_code == 200 and r.json()["batch"]["status"] == "done",
          r.text[:200])

    # ---- divert AFTER the parent printed: immediate, even when ON ------
    with Session(get_engine()) as s:
        s.add(BatchItem(batch_id=bid, scanned_code="804", resolved=True,
                        sku="STRAY-3", barcode="804",
                        product_title="Late Stray",
                        shopify_variant_id="t:S3", qty_scanned=2,
                        bin_location="J1-1"))
        s.commit()
    r = cl.post(f"/api/batches/{bid}/divert",
                json={"bin": "J1-1", "created_by": "C72"})
    body = r.json()
    check("late trip (parent already printed) prints right away",
          r.status_code == 201 and body["labels"] == 2
          and body["labels_held"] == 0
          and body["batch"]["status"] == "printing", r.text[:250])

    # ---- flip OFF restores the old shape -------------------------------
    r = cl.post("/api/print-strip-mode",
                json={"all_at_once": False, "worker": "Nick"})
    check("toggle flips back off", r.status_code == 200
          and r.json()["all_at_once"] is False, r.text[:200])

print()
print(f"{'FAIL' if fails else 'OK'}  {len(fails)} failing")
sys.exit(1 if fails else 0)
