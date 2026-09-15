"""TC-Planner streamlining round (Nick, 2026-08-26, built NOT deployed):
- /api/receiving/unprinted: the Update-stock safety net. Stock pushed to
  Shopify without labels books into the stock order's receiving batch
  (same rows and problem handling as Print labels) with NO labels
  queued, and ONE open Review task per batch tracks what's owed; repeat
  pushes fold into it.
- /api/review-tasks/{id}/queue-labels: resolution queues the missing
  labels exactly like a print pass (home bins, no-bin items held out).
- /api/epc-captures/latest-summary: the bulk-link chip's feed - the
  newest sweep's UNTAGGED count, counted like batch tagging counts a
  sweep."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_safety_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_engine
from app.models import (BarcodeChange, BatchItem, BinMapEntry, PrintJob,
                        ReviewNote, ReviewTask, RfidAssignment,
                        SoldRecord)
from sqlalchemy.orm import Session
from sqlalchemy import select
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.main._maybe_refresh_bin_map", return_value=False), \
     patch("app.main._kick_orders_sync_soon"):
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="AG-KIT", barcode="701",
                          product_title="AirGradient Kit", bin="I5-1",
                          qty=3, shopify_variant_id="t:AGK"))
        s.add(BinMapEntry(sku="NOBIN-2", barcode="702",
                          product_title="Binless Thing", bin="",
                          qty=1, shopify_variant_id="t:NB2"))
        s.commit()

    # --- 1) a stock push without labels books rows, queues NOTHING ----
    r = cl.post("/api/receiving/unprinted", json={
        "items": [{"sku": "AG-KIT", "quantity": 3},
                  {"sku": "NOBIN-2", "quantity": 2},
                  {"sku": "GHOST-7", "quantity": 1}],
        "requested_by": "Nick", "reference": "SO 900 · AG"})
    check("the safety-net push is accepted", r.status_code == 201,
          r.text[:250])
    out = r.json()
    bid = out["batch"]["id"]
    check("it counts the labels a print pass WOULD queue",
          out["labels_waiting"] == 3, out)
    with Session(get_engine()) as s:
        jobs = s.scalars(select(PrintJob).where(
            PrintJob.batch_id == bid)).all()
        check("no labels were actually queued", jobs == [],
              [j.sku for j in jobs])
        tasks = s.scalars(select(ReviewTask).where(
            ReviewTask.category == "labels-not-printed",
            ReviewTask.status == "open")).all()
        check("one safety-net task tracks the batch",
              len(tasks) == 1 and tasks[0].batch_id == bid
              and "3 label(s)" in tasks[0].detail
              and "held for a bin" in tasks[0].detail,
              [t.detail for t in tasks])
        task_id = tasks[0].id

    # --- 2) a second push folds into the SAME task --------------------
    r = cl.post("/api/receiving/unprinted", json={
        "items": [{"sku": "AG-KIT", "quantity": 2}],
        "requested_by": "Nick", "reference": "SO 900 · AG"})
    check("a repeat push reuses the batch",
          r.json()["batch"]["id"] == bid, r.json()["batch"])
    check("the owed-label count accumulates",
          r.json()["labels_waiting"] == 5, r.json())
    with Session(get_engine()) as s:
        tasks = s.scalars(select(ReviewTask).where(
            ReviewTask.category == "labels-not-printed",
            ReviewTask.status == "open")).all()
        check("still exactly one open task",
              len(tasks) == 1 and tasks[0].id == task_id
              and "5 label(s)" in tasks[0].detail,
              [t.detail for t in tasks])
        notes = s.scalars(select(ReviewNote).where(
            ReviewNote.task_key == str(task_id))).all()
        check("each push leaves a unit-count note",
              len(notes) == 2
              and any("5 unit(s) across 2 product(s)" in n.note
                      for n in notes)
              and any("2 unit(s) across 1 product(s)" in n.note
                      for n in notes),
              [n.note for n in notes])

    # --- 3) resolution queues the labels like a print pass ------------
    r = cl.post("/api/review-tasks/9999/queue-labels",
                json={"changed_by": "Nick"})
    check("a missing task 404s", r.status_code == 404, r.text[:100])
    r = cl.post(f"/api/review-tasks/{task_id}/queue-labels",
                json={"changed_by": "Nick"})
    check("resolving queues every owed label",
          r.status_code == 200 and r.json()["queued"] == 5,
          r.text[:250])
    check("no-bin products are held out and named",
          r.json()["skipped_no_bin"] == ["Binless Thing"],
          r.json()["skipped_no_bin"])
    with Session(get_engine()) as s:
        jobs = s.scalars(select(PrintJob).where(
            PrintJob.batch_id == bid)).all()
        check("labels carry the item's home bin",
              len(jobs) == 5 and all(j.bin_location == "I5-1"
                                     for j in jobs),
              [(j.sku, j.bin_location) for j in jobs])
        t = s.get(ReviewTask, task_id)
        check("the task resolved with the queue receipt",
              t.status == "resolved" and t.resolved_by == "Nick"
              and "5 label(s) queued" in (t.resolution_note or ""),
              (t.status, t.resolution_note))
    r = cl.post(f"/api/review-tasks/{task_id}/queue-labels",
                json={"changed_by": "Nick"})
    check("a resolved task refuses a second queue",
          r.status_code == 409, r.text[:120])

    # --- 4) a later label-less push files a FRESH task ----------------
    cl.post("/api/receiving/unprinted", json={
        "items": [{"sku": "AG-KIT", "quantity": 1}],
        "requested_by": "Nick", "reference": "SO 900 · AG"})
    with Session(get_engine()) as s:
        open_now = s.scalars(select(ReviewTask).where(
            ReviewTask.category == "labels-not-printed",
            ReviewTask.status == "open")).all()
        check("after resolution a new push opens a new task",
              len(open_now) == 1 and open_now[0].id != task_id,
              [t.id for t in open_now])

    # --- 5) the bulk-link chip's sweep summary ------------------------
    r = cl.get("/api/epc-captures/latest-summary").json()
    check("no sweep yet reads exists=False",
          r["ok"] and r["exists"] is False, r)
    with Session(get_engine()) as s:
        s.add(RfidAssignment(rfid_id="AB000000000000000000AB01",
                             shopify_variant_id="t:AGK",
                             product_title="AirGradient Kit",
                             sku="AG-KIT", bin_location="I5-1"))
        s.commit()
    cl.post("/api/epc-captures", json={
        "epcs": ["AB000000000000000000AB01", "AB000000000000000000AB02",
                 "AB000000000000000000AB03"],
        "device": "C72-test", "note": "link sweep"})
    r = cl.get("/api/epc-captures/latest-summary").json()
    check("the summary counts only UNTAGGED tags, like batch tagging",
          r["exists"] and r["epc_count"] == 3 and r["untagged"] == 2,
          r)
    check("the summary carries freshness", r["age_seconds"] is not None
          and r["age_seconds"] < 60, r.get("age_seconds"))

    # --- 6) "the unlabelled units were sold" (Nick, 2026-09-15) -------
    # Products leave (sold or set aside) before anyone labels them: the
    # OTHER resolution writes the owed labels off instead of printing -
    # counts drop to what was labelled, matching recorded sales are
    # consumed, History gets a receipt per SKU.
    with Session(get_engine()) as s:
        open_t = s.scalars(select(ReviewTask).where(
            ReviewTask.category == "labels-not-printed",
            ReviewTask.status == "open")).first()
        sold_task_id = open_t.id
        s.add(SoldRecord(order_id="gid://o/sold1", order_name="#900",
                         sku="AG-KIT", quantity=1))
        s.commit()
    r = cl.post(f"/api/review-tasks/{sold_task_id}/unprinted-sold",
                json={"changed_by": "Nick"})
    d = r.json()
    check("sold write-off answers with the write-off list",
          r.status_code == 200
          and sorted((w["sku"], w["units"]) for w in d["written_off"])
          == [("AG-KIT", 1), ("NOBIN-2", 2)], r.text[:300])
    check("matching recorded sales consumed (AG-KIT only had one)",
          d["sales_consumed"] == 1
          and "1 recorded sale(s) consumed" in d["message"],
          str(d)[:300])
    with Session(get_engine()) as s:
        items = {(i.sku or ""): i for i in s.scalars(
            select(BatchItem).where(BatchItem.batch_id == bid))}
        check("counts drop to what was actually labelled",
              items["AG-KIT"].qty_scanned == 5
              and items["NOBIN-2"].qty_scanned == 0,
              [(k, v.qty_scanned) for k, v in items.items()])
        sr = s.scalars(select(SoldRecord).where(
            SoldRecord.sku == "AG-KIT")).first()
        check("the ledger row is retired", sr.retired == 1, sr.retired)
        t = s.get(ReviewTask, sold_task_id)
        check("task resolved with the write-off story",
              t.status == "resolved"
              and "sold/set aside" in (t.resolution_note or "")
              and "2x NOBIN-2" in t.resolution_note,
              (t.status, t.resolution_note))
        evs = s.scalars(select(BarcodeChange).where(
            BarcodeChange.changed_field == "unprinted-sold")).all()
        check("History receipt per written-off SKU",
              sorted(e.sku for e in evs) == ["AG-KIT", "NOBIN-2"]
              and all("unlabelled unit(s) sold" in e.new_barcode
                      for e in evs),
              [(e.sku, e.new_barcode) for e in evs])
    r = cl.post(f"/api/review-tasks/{sold_task_id}/unprinted-sold",
                json={"changed_by": "Nick"})
    check("a resolved task refuses a second write-off",
          r.status_code == 409, r.text[:120])
    r = cl.get("/api/product-history?term=AG-KIT")
    check("the receipt reads in product history",
          any(e["type"] == "unprinted-sold"
              for e in r.json()["events"]), r.text[:300])

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
