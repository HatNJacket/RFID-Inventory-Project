"""Label strips (2026-10-07): every print job queued in one transaction
shares a strip, numbered in the order added; the next print starts a new
strip. FIND A LABEL (/api/labels/locate) answers a scanned barcode (or a
SKU) with the batch, the strip's label count, this product's positions
on it, its pairing progress and the batch item to select; failed labels
don't count; open batches come first."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"] = "t.myshopify.com"
os.environ["SHOPIFY_CLIENT_ID"] = "x"
os.environ["SHOPIFY_CLIENT_SECRET"] = "x"
os.environ["ORDERS_SYNC_DISABLE"] = "1"
os.environ.pop("STATION_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_labelstrips_test.db")
if os.path.exists(db):
    os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\", "/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.main import app
from app.database import get_engine
from app.models import Batch, BatchItem, BinMapEntry, PrintJob

fails = []


def check(label, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + label + ("" if cond else f"  <- {extra}"))
    if not cond:
        fails.append(label)


n = [0]


def job(batch_id, sku, barcode, **kw):
    n[0] += 1
    return PrintJob(epc=f"E2{n[0]:022d}", status=kw.get("status", "done"), sku=sku,
                    barcode=barcode, product_title=f"Product {sku}",
                    shopify_variant_id=f"gid://v/{sku}", batch_id=batch_id)


with patch("app.main._maybe_refresh_bin_map", return_value=False):
    with TestClient(app) as cl:
        with Session(get_engine()) as s:
            s.add(BinMapEntry(sku="W9139B", barcode="111222333", product_title="Red dot", bin="I2-3"))
            b1 = Batch(bin_name="SO 1042 Svbony", status="pairing", kind="receiving")
            b2 = Batch(bin_name="SO 1040 Svbony", status="done", kind="receiving")
            s.add_all([b1, b2])
            s.flush()
            s.add(BatchItem(batch_id=b1.id, scanned_code="111222333", resolved=True,
                            sku="W9139B", barcode="111222333", paired_count=1))
            # One print press: 6 labels, W9139B are the 3rd-5th, one failed.
            s.add_all([job(b1.id, "F9172B", "900"), job(b1.id, "F9172A", "901"),
                       job(b1.id, "W9139B", "111222333"), job(b1.id, "W9139B", "111222333"),
                       job(b1.id, "W9139B", "111222333"), job(b1.id, "W9196A", "902", status="error"),
                       job(b1.id, "W9105B", "903")])
            s.commit()
            b1_id, b2_id = b1.id, b2.id
        with Session(get_engine()) as s:
            # An older, finished batch printed the same product once.
            s.add_all([job(b2_id, "W9139B", "111222333"), job(b2_id, "F9122A", "904")])
            s.commit()
        with Session(get_engine()) as s:
            rows = s.scalars(select(PrintJob).order_by(PrintJob.id)).all()
            first = {r.strip_id for r in rows[:7]}
            check("one print's labels share a strip, numbered in order",
                  len(first) == 1 and [r.strip_pos for r in rows[:7]] == [1, 2, 3, 4, 5, 6, 7],
                  str([(r.strip_id, r.strip_pos) for r in rows]))
            check("the next print starts a new strip",
                  rows[7].strip_id not in first and rows[7].strip_pos == 1)
        r = cl.get("/api/labels/locate", params={"code": "111222333"}).json()["results"]
        top = r[0] if r else {}
        check("a scanned label names its batch, strip size and positions",
              top.get("batch", {}).get("id") == b1_id and top.get("strip_labels") == 6
              and top.get("positions") == [3, 4, 5] and top.get("positions_text") == "3-5", str(top))
        check("the batch item to select comes back with its pairing progress",
              top.get("item") and top["item"]["paired"] == 1, str(top.get("item")))
        check("an open batch is listed before a finished one",
              len(r) == 2 and r[1]["batch"]["id"] == b2_id and r[1]["positions"] == [1], str(r))
        r = cl.get("/api/labels/locate", params={"code": "w9139b"}).json()["results"]
        check("a SKU works as well as the barcode", r and r[0]["positions"] == [3, 4, 5], str(r[:1]))
        r = cl.get("/api/labels/locate", params={"code": "nope"}).json()["results"]
        check("an unknown code finds nothing", r == [])
        check("an empty scan is refused", cl.get("/api/labels/locate", params={"code": " "}).status_code == 400)

print()
print(f"{len(fails)} failed" if fails else "all passed")
sys.exit(1 if fails else 0)
