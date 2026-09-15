"""Queue-tab label editing + the box-note auto-refresh (Nick,
2026-09-15): reprints cloned the old job's bin line, so S11830-3's
reprint still said "Box 1 of 3" after its renumber. Every queue path
now re-derives a registered box-set part's "Box N of M" note from the
CURRENT registry, and pending jobs are editable/refreshable from the
Print queue.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_labeledit_test.db")
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
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.find_sku_listing", return_value=None), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_on_hand", return_value=None), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.main._maybe_refresh_bin_map", return_value=False):
  with TestClient(app) as cl:
    from sqlalchemy import select
    from sqlalchemy.orm import Session as S
    from app.database import get_engine
    from app.models import (BarcodeChange, Batch, BoxSetPart, LabelName,
                            PrintJob)

    # A registered 2-box set: SETQ-1 = box 1, SETQ-2 = box 2.
    with S(get_engine()) as s:
        s.add(BoxSetPart(set_sku="SETQ", set_title="Quad Kit",
                         set_variant_id="gid://v/q", part_sku="SETQ-1",
                         box_no=1, part_barcode="9000001"))
        s.add(BoxSetPart(set_sku="SETQ", set_title="Quad Kit",
                         set_variant_id="gid://v/q", part_sku="SETQ-2",
                         box_no=2, part_barcode="9000002"))
        s.commit()

    # ---- auto-refresh at queue time (the Queue-tab reprint path posts
    # the OLD job's bin line back verbatim) ------------------------------
    r = cl.post("/api/print-jobs", json={
        "shopify_variant_id": "gid://v/q", "product_title": "Quad Kit",
        "sku": "SETQ-2", "barcode": "9000002",
        "bin_location": "A1-1, Box 1 of 3"})
    d = r.json()
    check("queue reprint: stale box note re-derived from the registry",
          r.status_code == 201
          and d["jobs"][0]["bin_location"] == "A1-1, Box 2 of 2", d)
    r = cl.post("/api/print-jobs", json={
        "shopify_variant_id": "gid://v/q", "product_title": "Quad Kit",
        "sku": "SETQ-1", "barcode": "9000001", "bin_location": "A1-1"})
    check("part without a note gets the current one",
          r.json()["jobs"][0]["bin_location"] == "A1-1, Box 1 of 2",
          r.text[:200])
    r = cl.post("/api/print-jobs", json={
        "shopify_variant_id": "gid://v/x", "product_title": "Plain thing",
        "sku": "PLAIN-1", "bin_location": "B2-2"})
    check("non-part bin line passes through untouched",
          r.json()["jobs"][0]["bin_location"] == "B2-2", r.text[:200])

    # ---- the Print-step reprint (void-and-requeue) re-derives too ------
    with S(get_engine()) as s:
        b = Batch(bin_name="A1-1", created_by="test", status="printing")
        s.add(b); s.flush()
        s.add(PrintJob(epc="E0LBL00000000000000000B1", status="done",
                       batch_id=b.id, shopify_variant_id="gid://v/q",
                       product_title="Quad Kit", sku="SETQ-2",
                       barcode="9000002",
                       bin_location="A1-1, Box 1 of 3"))
        s.commit(); bid = b.id
        old_id = s.scalar(select(PrintJob.id).where(
            PrintJob.epc == "E0LBL00000000000000000B1"))
    r = cl.post(f"/api/batches/{bid}/reprint-jobs", json={
        "job_ids": [old_id], "confirmed": True, "requested_by": "Nick"})
    check("reprint-jobs accepted", r.status_code == 200, r.text[:200])
    with S(get_engine()) as s:
        fresh = s.scalars(select(PrintJob).where(
            PrintJob.batch_id == bid,
            PrintJob.status == "pending")).all()
        check("void-and-requeue clone carries the CURRENT box note",
              len(fresh) == 1
              and fresh[0].bin_location == "A1-1, Box 2 of 2",
              [(j.status, j.bin_location) for j in fresh])

    # ---- edit a pending label from the queue ---------------------------
    r = cl.post("/api/print-jobs", json={
        "shopify_variant_id": "gid://v/x", "product_title": "Plain thing",
        "sku": "PLAIN-1", "barcode": "555111", "bin_location": "B2-2"})
    jid = r.json()["jobs"][0]["id"]
    r = cl.post(f"/api/print-jobs/{jid}/edit", json={
        "top_text": "Astronomik", "sku_line": "PLAIN-1 | 6nm",
        "bin_line": "B2-2, Box 9 of 9", "edited_by": "Nick"})
    d = r.json()
    check("edit: typed lines land on the job",
          r.status_code == 200
          and d["job"]["label_name"] == "Astronomik"
          and d["job"]["label_placement"] == "header"
          and d["job"]["label_sku"] == "PLAIN-1 | 6nm"
          and d["job"]["bin_location"] == "B2-2, Box 9 of 9", d)
    with S(get_engine()) as s:
        rows = s.scalars(select(BarcodeChange).where(
            BarcodeChange.changed_field == "label-edit")).all()
        check("edit logged to History", len(rows) == 1
              and rows[0].sku == "PLAIN-1", [r.changed_field for r in rows])
    r = cl.post(f"/api/print-jobs/{jid}/edit", json={
        "top_text": "Telescopes Canada", "sku_line": "PLAIN-1",
        "bin_line": "B2-2"})
    d = r.json()
    check("edit back to defaults clears the custom fields",
          d["job"]["label_name"] is None and d["job"]["label_sku"] is None
          and d["job"]["bin_location"] == "B2-2", d)

    # ---- refresh: saved name + current registry note -------------------
    with S(get_engine()) as s:
        s.add(LabelName(sku="SETQ-2", label_name="Quad Kit OTA",
                        placement="header"))
        # A job queued before the renumber, note and all.
        s.add(PrintJob(epc="E0LBL00000000000000000C1", status="pending",
                       shopify_variant_id="gid://v/q",
                       product_title="Quad Kit", sku="SETQ-2",
                       barcode="9000002",
                       bin_location="A1-1, Box 1 of 3"))
        s.commit()
        stale_id = s.scalar(select(PrintJob.id).where(
            PrintJob.epc == "E0LBL00000000000000000C1"))
    r = cl.post(f"/api/print-jobs/{stale_id}/refresh", json={
        "edited_by": "Nick"})
    d = r.json()
    check("refresh: saved name + current box note applied",
          r.status_code == 200 and d["changed"] is True
          and d["job"]["label_name"] == "Quad Kit OTA"
          and d["job"]["bin_location"] == "A1-1, Box 2 of 2", d)
    r = cl.post(f"/api/print-jobs/{stale_id}/refresh", json={})
    check("second refresh reports nothing to do",
          r.json()["changed"] is False, r.text[:200])

    # ---- renumber then refresh: Nick's exact sequence ------------------
    r = cl.post("/api/box-sets/SETQ/renumber", json={
        "part_sku": "SETQ-2", "box_no": 1, "changed_by": "Nick"})
    check("renumber accepted", r.status_code == 200, r.text[:200])
    r = cl.post(f"/api/print-jobs/{stale_id}/refresh", json={})
    check("refresh after renumber picks up the new number",
          r.json()["job"]["bin_location"] == "A1-1, Box 1 of 2",
          r.text[:200])

    # ---- only pending jobs are editable --------------------------------
    with S(get_engine()) as s:
        j = s.get(PrintJob, stale_id); j.status = "done"; s.commit()
    r = cl.post(f"/api/print-jobs/{stale_id}/edit", json={
        "top_text": "x", "sku_line": "", "bin_line": ""})
    check("edit refused once printed", r.status_code == 409, r.text[:200])
    r = cl.post(f"/api/print-jobs/{stale_id}/refresh", json={})
    check("refresh refused once printed", r.status_code == 409,
          r.text[:200])

    # ---- set MARKS reach the stickers (Nick, 2026-09-15, S30810) -------
    # The set is only DEFINED at verify, but labels print first: a mark
    # made at collect must put its Box X of Y on the label, and a mark
    # saved AFTER the labels queued must update them.
    from app.models import BatchItem
    with S(get_engine()) as s:
        mb = Batch(bin_name="C7-1", created_by="test",
                   status="collecting")
        s.add(mb); s.flush()
        s.add(BatchItem(batch_id=mb.id, scanned_code="30810",
                        resolved=True, sku="S30810", barcode="30810",
                        product_title="Mount box 1", qty_scanned=1,
                        set_mark_master="S30800", set_mark_box=1,
                        set_mark_total=4))
        it2 = BatchItem(batch_id=mb.id, scanned_code="30820",
                        resolved=True, sku="S30820", barcode="30820",
                        product_title="Mount box 2", qty_scanned=1)
        s.add(it2)
        s.commit(); mbid = mb.id; it2id = it2.id
    r = cl.post("/api/print-jobs", json={
        "shopify_variant_id": "gid://v/m1", "product_title": "Mount",
        "sku": "S30810", "bin_location": "C7-1"})
    check("a collect-step mark puts Box X of Y on the label",
          r.json()["jobs"][0]["bin_location"] == "C7-1, Box 1 of 4",
          r.text[:200])
    r = cl.post("/api/print-jobs", json={
        "shopify_variant_id": "gid://v/m2", "product_title": "Mount",
        "sku": "S30820", "bin_location": "C7-1"})
    j2 = r.json()["jobs"][0]
    check("unmarked box queues without a note",
          j2["bin_location"] == "C7-1", j2)
    r = cl.post(f"/api/batches/{mbid}/items/{it2id}/set-mark", json={
        "master_sku": "S30800", "box_no": 2, "box_total": 4,
        "changed_by": "Nick"})
    d = r.json()
    check("marking after the queue restamps the pending label",
          r.status_code == 200
          and "1 queued label(s) picked up" in d["message"], d)
    with S(get_engine()) as s:
        j = s.get(PrintJob, j2["id"])
        check("pending label now says Box 2 of 4",
              j.bin_location == "C7-1, Box 2 of 4", j.bin_location)
    # The registry outranks a mark for the same SKU.
    with S(get_engine()) as s:
        row = s.scalars(select(BatchItem).where(
            BatchItem.sku == "S30810")).first()
        row.set_mark_box = 9  # nonsense on purpose; registry SKUs win
        s.commit()
    r = cl.post("/api/print-jobs", json={
        "shopify_variant_id": "gid://v/q", "product_title": "Quad Kit",
        "sku": "SETQ-2", "barcode": "9000002", "bin_location": "A1-1"})
    check("registry still outranks marks for registered parts",
          r.json()["jobs"][0]["bin_location"] == "A1-1, Box 1 of 2",
          r.text[:200])
    # Clearing the mark takes the note back off the pending label.
    r = cl.post(f"/api/batches/{mbid}/items/{it2id}/set-mark", json={
        "clear": True})
    check("clear restamps too", r.status_code == 200
          and "1 queued label(s) updated" in r.json()["message"],
          r.text[:200])
    with S(get_engine()) as s:
        j = s.get(PrintJob, j2["id"])
        check("cleared mark removes the note",
              j.bin_location == "C7-1", j.bin_location)

    # ---- a mark on a REGISTERED part renumbers the registry ------------
    # (Nick, 2026-09-15, S11810: -1 is physically box 2 - the operator's
    # mark is the newest word, so the registry swaps to match instead of
    # the suffix-derived rows overriding it.)
    from app.models import BoxSetPart
    with S(get_engine()) as s:
        b2 = Batch(bin_name="A1-1", created_by="test",
                   status="collecting")
        s.add(b2); s.flush()
        itq = BatchItem(batch_id=b2.id, scanned_code="9000002",
                        resolved=True, sku="SETQ-2", barcode="9000002",
                        product_title="Quad Kit - Box 1 of 2",
                        qty_scanned=1)
        s.add(itq); s.commit(); b2id = b2.id; itqid = itq.id
    # SETQ-2 sits at box 1 after the renumber above; the operator says
    # it is really box 2.
    r = cl.post(f"/api/batches/{b2id}/items/{itqid}/set-mark", json={
        "master_sku": "SETQ", "box_no": 2, "box_total": 2,
        "changed_by": "Nick"})
    d = r.json()
    check("mark on a registered part renumbers the registry",
          r.status_code == 200
          and "registry renumbered" in d["message"], d)
    with S(get_engine()) as s:
        rows = {p.part_sku: p.box_no
                for p in s.scalars(select(BoxSetPart))}
        check("the displaced box took the vacated slot",
              rows.get("SETQ-2") == 2 and rows.get("SETQ-1") == 1, rows)
    r = cl.post("/api/print-jobs", json={
        "shopify_variant_id": "gid://v/q", "product_title": "Quad Kit",
        "sku": "SETQ-2", "barcode": "9000002", "bin_location": "A1-1"})
    check("labels follow the operator's numbering",
          r.json()["jobs"][0]["bin_location"] == "A1-1, Box 2 of 2",
          r.text[:200])
    # A number past the registered size cannot renumber anything.
    r = cl.post(f"/api/batches/{b2id}/items/{itqid}/set-mark", json={
        "master_sku": "SETQ", "box_no": 3, "box_total": 4,
        "changed_by": "Nick"})
    check("mark beyond the set size leaves the registry alone",
          r.status_code == 200
          and "left alone" in r.json()["message"], r.text[:300])
    with S(get_engine()) as s:
        rows = {p.part_sku: p.box_no
                for p in s.scalars(select(BoxSetPart))}
        check("registry unchanged by the oversize mark",
              rows.get("SETQ-2") == 2 and rows.get("SETQ-1") == 1, rows)

    # ---- OPEN BOX outranks the box-set note (Nick, 2026-09-15) ---------
    r = cl.post("/api/print-jobs", json={
        "shopify_variant_id": "gid://v/q",
        "product_title": "Quad Kit - Open Box",
        "sku": "SETQ-2", "barcode": "9000002", "bin_location": "A1-1"})
    check("open box outranks the registered part's Box X of Y",
          r.json()["jobs"][0]["bin_location"] == "A1-1, OPEN BOX",
          r.text[:200])

print()
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES'}")
sys.exit(1 if fails else 0)

