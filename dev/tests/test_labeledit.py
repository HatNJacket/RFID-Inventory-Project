"""Queue-tab label editing + label bin-line notes. Since 2026-09-15
multi-box sets are gone: the only live note is OPEN BOX, and legacy
"Box X of Y" notes are STRIPPED whenever a job re-derives (queue,
reprint clones, refresh). Pending jobs stay editable/refreshable from
the Print queue.
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
    from app.models import (BarcodeChange, Batch, LabelName, PrintJob)

    # ---- legacy Box X of Y notes STRIP at queue time -------------------
    # (Sets are gone; a reprint that clones an old job's bin line must
    # not resurrect the dead note. OPEN BOX is the one live note and is
    # covered in test_openboxreturn.)
    r = cl.post("/api/print-jobs", json={
        "shopify_variant_id": "gid://v/x", "product_title": "Plain thing",
        "sku": "PLAIN-1", "barcode": "555111",
        "bin_location": "A1-1, Box 1 of 3"})
    check("legacy box note stripped at queue",
          r.status_code == 201
          and r.json()["jobs"][0]["bin_location"] == "A1-1",
          r.text[:200])
    r = cl.post("/api/print-jobs", json={
        "shopify_variant_id": "gid://v/x", "product_title": "Plain thing",
        "sku": "PLAIN-1", "bin_location": "B2-2"})
    check("plain bin line passes through untouched",
          r.json()["jobs"][0]["bin_location"] == "B2-2", r.text[:200])

    # ---- the Print-step reprint (void-and-requeue) strips too ----------
    with S(get_engine()) as s:
        b = Batch(bin_name="A1-1", created_by="test", status="printing")
        s.add(b); s.flush()
        s.add(PrintJob(epc="E0LBL00000000000000000B1", status="done",
                       batch_id=b.id, shopify_variant_id="gid://v/x",
                       product_title="Plain thing", sku="PLAIN-1",
                       barcode="555111",
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
        check("void-and-requeue clone drops the legacy note",
              len(fresh) == 1 and fresh[0].bin_location == "A1-1",
              [(j.status, j.bin_location) for j in fresh])

    # ---- edit a pending label from the queue ---------------------------
    r = cl.post("/api/print-jobs", json={
        "shopify_variant_id": "gid://v/x", "product_title": "Plain thing",
        "sku": "PLAIN-1", "barcode": "555111", "bin_location": "B2-2"})
    jid = r.json()["jobs"][0]["id"]
    r = cl.post(f"/api/print-jobs/{jid}/edit", json={
        "top_text": "Astronomik", "sku_line": "PLAIN-1 | 6nm",
        "bin_line": "B2-2 SHELF TOP", "edited_by": "Nick"})
    d = r.json()
    check("edit: typed lines land on the job",
          r.status_code == 200
          and d["job"]["label_name"] == "Astronomik"
          and d["job"]["label_placement"] == "header"
          and d["job"]["label_sku"] == "PLAIN-1 | 6nm"
          and d["job"]["bin_location"] == "B2-2 SHELF TOP", d)
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

    # ---- refresh: saved name + note strip ------------------------------
    with S(get_engine()) as s:
        s.add(LabelName(sku="PLAIN-2", label_name="Nice Name",
                        placement="header"))
        s.add(PrintJob(epc="E0LBL00000000000000000C1", status="pending",
                       shopify_variant_id="gid://v/y",
                       product_title="Plain two", sku="PLAIN-2",
                       barcode="555222",
                       bin_location="A1-1, Box 2 of 2"))
        s.commit()
        stale_id = s.scalar(select(PrintJob.id).where(
            PrintJob.epc == "E0LBL00000000000000000C1"))
    r = cl.post(f"/api/print-jobs/{stale_id}/refresh", json={
        "edited_by": "Nick"})
    d = r.json()
    check("refresh: saved name applied, legacy note stripped",
          r.status_code == 200 and d["changed"] is True
          and d["job"]["label_name"] == "Nice Name"
          and d["job"]["bin_location"] == "A1-1", d)
    r = cl.post(f"/api/print-jobs/{stale_id}/refresh", json={})
    check("second refresh reports nothing to do",
          r.json()["changed"] is False, r.text[:200])

    # ---- only pending jobs are editable --------------------------------
    with S(get_engine()) as s:
        j = s.get(PrintJob, stale_id); j.status = "done"; s.commit()
    r = cl.post(f"/api/print-jobs/{stale_id}/edit", json={
        "top_text": "x", "sku_line": "", "bin_line": ""})
    check("edit refused once printed", r.status_code == 409, r.text[:200])
    r = cl.post(f"/api/print-jobs/{stale_id}/refresh", json={})
    check("refresh refused once printed", r.status_code == 409,
          r.text[:200])

print()
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES'}")
sys.exit(1 if fails else 0)
