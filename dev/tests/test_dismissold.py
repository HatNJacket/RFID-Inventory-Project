"""Sold-before-label dismissal (Nick, 2026-09-09): receiving stock
that sold before a label reached it. Dismissal touches OUR accounting
only - the row leaves the working list, its outstanding printed labels
stop counting as owed, the shipment can close itself, completion files
NO review task for it, and nothing writes a quantity anywhere. Undo
restores the row and exactly its label dismissals.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_dismissold_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.main._maybe_refresh_bin_map", return_value=False), \
     patch("app.main.oneleft"):
  with TestClient(app) as cl:
    from sqlalchemy import select
    from sqlalchemy.orm import Session as S
    from app.database import get_engine
    from app.models import (Batch, BatchItem, LabelDismissal, PrintJob,
                            ReviewTask)

    with S(get_engine()) as s:
        rb = Batch(bin_name="RECEIVING", kind="receiving",
                   created_by="TC-Planner · SO 955")
        s.add(rb); s.flush()
        sold = BatchItem(batch_id=rb.id, scanned_code="801", resolved=True,
                         sku="ZWO-A", barcode="801",
                         product_title="ZWO thing A", qty_scanned=2,
                         paired_count=0, bin_location="B1-1")
        okay = BatchItem(batch_id=rb.id, scanned_code="802", resolved=True,
                         sku="ZWO-B", barcode="802",
                         product_title="ZWO thing B", qty_scanned=1,
                         paired_count=1, bin_location="B1-2")
        s.add_all([sold, okay])
        for i in range(2):
            s.add(PrintJob(epc=f"8AB{i}000000000000000000A1",
                           status="done", shopify_variant_id="t:a",
                           product_title="ZWO thing A", sku="ZWO-A",
                           batch_id=rb.id))
        # A bin batch for the guard test.
        nb = Batch(bin_name="D2-2", created_by="n")
        s.add(nb); s.flush()
        nit = BatchItem(batch_id=nb.id, scanned_code="803", resolved=True,
                        sku="OTHER-1", barcode="803",
                        product_title="Other", qty_scanned=1)
        s.add(nit)
        s.commit()
        rbid, soldid, okayid = rb.id, sold.id, okay.id
        nbid, nitid = nb.id, nit.id

    r = cl.get("/api/receiving/unpaired-labels").json()
    check("2 labels owed before the dismissal",
          r["total_labels"] == 2, r)

    # ---- guard: bin batches refuse ------------------------------------
    r = cl.post(f"/api/batches/{nbid}/items/{nitid}/dismiss-sold",
                json={"worker": "Nick"})
    check("bin batches refuse the sold dismissal", r.status_code == 422,
          r.status_code)

    # ---- the dismissal --------------------------------------------------
    r = cl.post(f"/api/batches/{rbid}/items/{soldid}/dismiss-sold",
                json={"worker": "Nick"})
    d = r.json()
    check("dismissed", r.status_code == 200
          and d["item"]["skipped"] is True, r.text[:300])
    check("both outstanding labels covered", d["labels_covered"] == 2, d)
    check("the shipment closed itself (everything else was paired)",
          d["receiving_done"] is True, d)
    r = cl.get("/api/receiving/unpaired-labels").json()
    check("unresolved list stops asking", r["total_labels"] == 0, r)
    with S(get_engine()) as s:
        it = s.get(BatchItem, soldid)
        check("counts untouched (qty 2, paired 0)",
              it.qty_scanned == 2 and (it.paired_count or 0) == 0,
              (it.qty_scanned, it.paired_count))
        dl = s.scalars(select(LabelDismissal)).all()
        check("dismissals wear the traceable marker",
              len(dl) == 2 and all(
                  x.dismissed_by == f"dismiss-sold #{soldid}" for x in dl
              ), [x.dismissed_by for x in dl])

    r = cl.post(f"/api/batches/{rbid}/items/{soldid}/dismiss-sold",
                json={})
    check("double dismissal refused", r.status_code == 409, r.status_code)

    # ---- history event carries the undo --------------------------------
    hist = cl.get("/api/history?limit=50").json()
    ev = next((e for e in hist.get("events", hist.get("history", []))
               if (e.get("undo") or {}).get("kind") == "receiving-dismiss"),
              None)
    check("Sold Before Label event with undo descriptor",
          ev is not None and ev["undo"]["item_id"] == soldid
          and ev["undo"]["batch_id"] == rbid, ev)

    # ---- undo restores the story ---------------------------------------
    r = cl.post(
        f"/api/batches/{rbid}/items/{soldid}/dismiss-sold/undo",
        json={"worker": "Nick"})
    d = r.json()
    check("undo answered, labels owed again",
          r.status_code == 200 and d["labels_restored"] == 2,
          r.text[:300])
    with S(get_engine()) as s:
        it = s.get(BatchItem, soldid)
        check("row rejoined the list", it.skipped is False
              and it.skip_reason is None, it.as_dict())
        check("marker dismissals removed",
              s.scalars(select(LabelDismissal)).all() == [], "")
    r = cl.get("/api/receiving/unpaired-labels").json()
    check("unresolved list owes 2 again", r["total_labels"] == 2, r)
    r = cl.post(
        f"/api/batches/{rbid}/items/{soldid}/dismiss-sold/undo", json={})
    check("undo without a dismissal refused", r.status_code == 409,
          r.status_code)

    # ---- completion files NO review task for a sold dismissal ---------
    with S(get_engine()) as s:
        rb2 = Batch(bin_name="RECEIVING", kind="receiving",
                    created_by="planner")
        s.add(rb2); s.flush()
        s.add(BatchItem(batch_id=rb2.id, scanned_code="804", resolved=True,
                        sku="SOLD-2", barcode="804",
                        product_title="Sold two", qty_scanned=1))
        s.add(BatchItem(batch_id=rb2.id, scanned_code="805", resolved=True,
                        sku="SKIP-2", barcode="805",
                        product_title="Skipped two", qty_scanned=1,
                        skipped=True, skip_reason="wrapped"))
        s.commit()
        rb2id = rb2.id
        s2id = s.scalars(select(BatchItem).where(
            BatchItem.batch_id == rb2id,
            BatchItem.sku == "SOLD-2")).one().id
    cl.post(f"/api/batches/{rb2id}/items/{s2id}/dismiss-sold", json={})
    cl.post(f"/api/batches/{rb2id}/complete", json={})
    with S(get_engine()) as s:
        tasks = s.scalars(select(ReviewTask).where(
            ReviewTask.batch_id == rb2id,
            ReviewTask.category == "could-not-scan")).all()
        check("completion: ordinary skip files a task, sold dismissal "
              "files NOTHING",
              len(tasks) == 1 and tasks[0].sku == "SKIP-2",
              [t.sku for t in tasks])

print()
if fails:
    print(f"{len(fails)} FAILURE(S):"); [print("  -", f) for f in fails]
    sys.exit(1)
print("ALL PASS")
