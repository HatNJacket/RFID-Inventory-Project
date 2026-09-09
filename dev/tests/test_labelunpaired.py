"""Label-not-paired watchdog, back for receiving (Nick, 2026-09-09):
workers label boxes without RFID-pairing them. A receiving batch whose
printed labels are still unpaired 2+ hours after the last print gets
ONE Review task (category label-unpaired); labels on a held vendor
strip (kept sheets) and companion labels never count; the task closes
itself once everything is accounted for. The open-batches list tags
receiving batches with their unpaired count.
"""
import os, sys, tempfile
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_labelunpaired_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from fastapi.testclient import TestClient
import app.main as m
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

def rearm():
    m._unpaired_check_last = 0.0

with TestClient(app) as cl:
    from sqlalchemy import select
    from sqlalchemy.orm import Session as S
    from app.database import get_engine
    from app.models import (Batch, BatchItem, HeldLabelItem, HeldLabelList,
                            PrintJob, ReviewTask)

    old = datetime.utcnow() - timedelta(hours=3)
    with S(get_engine()) as s:
        rb = Batch(bin_name="RECEIVING", kind="receiving",
                   created_by="planner")
        s.add(rb); s.flush()
        s.add(BatchItem(batch_id=rb.id, scanned_code="801", resolved=True,
                        sku="ZWO-A", barcode="801",
                        product_title="ZWO thing A", qty_scanned=3,
                        paired_count=1, bin_location="B1-1"))
        s.add(BatchItem(batch_id=rb.id, scanned_code="802", resolved=True,
                        sku="ZWO-B", barcode="802",
                        product_title="ZWO thing B", qty_scanned=2,
                        paired_count=2, bin_location="B1-2"))
        for i in range(3):
            s.add(PrintJob(epc=f"1AB{i}000000000000000000A1", status="done",
                           shopify_variant_id="t:a", product_title="A",
                           sku="ZWO-A", batch_id=rb.id, created_at=old))
        for i in range(2):
            s.add(PrintJob(epc=f"2AB{i}000000000000000000B1", status="done",
                           shopify_variant_id="t:b", product_title="B",
                           sku="ZWO-B", batch_id=rb.id, created_at=old))
        # A companion label (box 2 of a multi-box unit): never a
        # counting label, must not trip the watchdog or the badge.
        s.add(PrintJob(epc="3AB0000000000000000000C1", status="done",
                       shopify_variant_id="t:a", product_title="A",
                       sku="ZWO-A", kind="companion", batch_id=rb.id,
                       created_at=old))
        # A normal bin batch: never wears the receiving tag.
        nb = Batch(bin_name="D1-1", created_by="n")
        s.add(nb); s.flush()
        s.add(BatchItem(batch_id=nb.id, scanned_code="803", resolved=True,
                        sku="OTHER-1", barcode="803",
                        product_title="Other", qty_scanned=1))
        s.commit()
        rbid, nbid = rb.id, nb.id

    # ---- the open-batches list wears the tag ---------------------------
    r = cl.get("/api/batches?status=open")
    rows = {b["id"]: b for b in r.json()["batches"]}
    check("receiving batch tagged 2 not-RFID-paired (3+2 printed, 3 "
          "paired, companion excluded)",
          rows.get(rbid, {}).get("unpaired_labels") == 2, rows.get(rbid))
    check("bin batches carry no tag",
          "unpaired_labels" not in rows.get(nbid, {}), rows.get(nbid))

    # ---- the watchdog files ONE task ----------------------------------
    rearm()
    cl.get("/api/review-tasks?status=open")
    with S(get_engine()) as s:
        tasks = s.scalars(select(ReviewTask).where(
            ReviewTask.category == "label-unpaired")).all()
        check("one label-unpaired task filed", len(tasks) == 1
              and tasks[0].batch_id == rbid, [t.as_dict() for t in tasks])
        check("detail names the shortfall per SKU",
              tasks and "2× ZWO-A" in tasks[0].detail, tasks
              and tasks[0].detail)
    rearm()
    cl.get("/api/review-tasks?status=open")
    with S(get_engine()) as s:
        n = len(s.scalars(select(ReviewTask).where(
            ReviewTask.category == "label-unpaired",
            ReviewTask.status == "open")).all())
        check("no duplicate while one is open", n == 1, n)

    # ---- fresh prints stay in the grace window ------------------------
    with S(get_engine()) as s:
        rb2 = Batch(bin_name="RECEIVING", kind="receiving",
                    created_by="planner")
        s.add(rb2); s.flush()
        s.add(BatchItem(batch_id=rb2.id, scanned_code="804", resolved=True,
                        sku="FRESH-1", barcode="804",
                        product_title="Fresh", qty_scanned=1))
        s.add(PrintJob(epc="4AB0000000000000000000D1", status="done",
                       shopify_variant_id="t:f", product_title="F",
                       sku="FRESH-1", batch_id=rb2.id))
        s.commit(); rb2id = rb2.id
    rearm()
    cl.get("/api/review-tasks?status=open")
    with S(get_engine()) as s:
        n = len(s.scalars(select(ReviewTask).where(
            ReviewTask.category == "label-unpaired",
            ReviewTask.batch_id == rb2id)).all())
        check("a just-printed batch files nothing (2h grace)", n == 0, n)

    # ---- a held vendor strip accounts for the leftovers ----------------
    with S(get_engine()) as s:
        hl = HeldLabelList(batch_id=rbid, created_by="Nick")
        s.add(hl); s.flush()
        s.add(HeldLabelItem(list_id=hl.id, sku="ZWO-A",
                            product_title="A", count=2))
        s.commit()
    rearm()
    cl.get("/api/review-tasks?status=open")
    with S(get_engine()) as s:
        t = s.scalar(select(ReviewTask).where(
            ReviewTask.category == "label-unpaired",
            ReviewTask.batch_id == rbid))
        check("held strip (kept sheet) closes the task itself",
              t is not None and t.status == "resolved"
              and t.resolved_by == "auto", t.as_dict() if t else None)
    r = cl.get("/api/batches?status=open")
    rows = {b["id"]: b for b in r.json()["batches"]}
    check("the list tag clears once the strip holds the labels",
          rows.get(rbid, {}).get("unpaired_labels") == 0, rows.get(rbid))

print()
if fails:
    print(f"{len(fails)} FAILURE(S):"); [print("  -", f) for f in fails]
    sys.exit(1)
print("ALL PASS")
