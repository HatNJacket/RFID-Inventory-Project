"""Side-trip flow round (Nick, 2026-09-01): a stray whose boxes were
tagged in an earlier session (tagged_before, no labels) can still take
a trip - nothing prints, nothing pairs, the operator carries the boxes
and closes it. Bundles-only trips stay refused, and the normal
labelled trip is unchanged."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_tripflow_test.db")
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
        # A stray tagged in an earlier session: units yes, labels no.
        s.add(BatchItem(batch_id=b.id, scanned_code="900", resolved=True,
                        sku="OLD-TAGGED", barcode="900",
                        product_title="Old Tagged Stray",
                        shopify_variant_id="t:OT", qty_scanned=0,
                        tagged_before=2, bin_location="F5-5"))
        # A normal fresh stray with boxes to label.
        s.add(BatchItem(batch_id=b.id, scanned_code="901", resolved=True,
                        sku="FRESH-1", barcode="901",
                        product_title="Fresh Stray",
                        shopify_variant_id="t:FS", qty_scanned=3,
                        bin_location="G6-6"))
        s.commit()
        bid = b.id

    # ---- zero-label trip: already-tagged boxes just get carried ---------
    r = cl.post(f"/api/batches/{bid}/divert",
                json={"bin": "F5-5", "created_by": "C72"})
    body = r.json()
    check("tagged_before-only stray can take a trip",
          r.status_code == 201 and body["moved"] == 1
          and body["labels"] == 0, r.text[:250])
    check("zero-label trip says carry, not pair",
          "no labels are needed" in body["message"]
          and "Carry" in body["message"], body.get("message"))
    trip1 = body["batch"]
    check("zero-label trip lands in pairing (not printing)",
          trip1["status"] == "pairing" and trip1["parent_batch_id"] == bid,
          str(trip1)[:200])
    with Session(get_engine()) as s:
        jobs = s.query(PrintJob).filter(
            PrintJob.batch_id == trip1["id"]).count()
    check("really no labels queued", jobs == 0, jobs)
    r = cl.post(f"/api/batches/{trip1['id']}/close-divert")
    check("carry-confirm closes it",
          r.status_code == 200 and r.json()["batch"]["status"] == "done",
          r.text[:200])

    # ---- the normal labelled trip is unchanged --------------------------
    r = cl.post(f"/api/batches/{bid}/divert",
                json={"bin": "G6-6", "created_by": "C72"})
    body = r.json()
    check("fresh stray still queues its labels",
          r.status_code == 201 and body["labels"] == 3
          and body["batch"]["status"] == "printing"
          and "label(s) queued" in body["message"], r.text[:250])

    # ---- bundles-only stays refused -------------------------------------
    with Session(get_engine()) as s:
        b2 = Batch(bin_name="D2-2", status="collecting", created_by="Nick")
        s.add(b2)
        s.flush()
        s.add(BatchItem(batch_id=b2.id, scanned_code="902", resolved=True,
                        sku="BUNDLE-1", barcode="902",
                        product_title="Bundle Stray", kind="bundle",
                        shopify_variant_id="t:BU", qty_scanned=1,
                        bin_location="H7-7"))
        s.commit()
        b2id = b2.id
    r = cl.post(f"/api/batches/{b2id}/divert",
                json={"bin": "H7-7", "created_by": "C72"})
    check("a bundles-only trip is still refused",
          r.status_code == 422 and "bundle" in r.json()["detail"].lower(),
          r.text[:200])

print()
print(f"{'FAIL' if fails else 'OK'}  {len(fails)} failing")
sys.exit(1 if fails else 0)
