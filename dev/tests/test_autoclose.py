"""Inventory checks follow the NEWEST count (Nick, 2026-08-26, the ZWO
M54-M54-7.5 case: a stale "0 counted vs 1" task outlived a newer batch
that counted the 1):
- a fresh batch count rewrites every open check's counted/on-hand
  figures and leaves a note naming the batch;
- when the fresh count AGREES with Shopify the task closes itself,
  History-tagged Auto-Resolved (never a person's click);
- a still-standing mismatch lands on the existing open task instead of
  stacking a duplicate."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_autoclose_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_engine
from app.models import BinMapEntry, ReviewNote, ReviewTask
from sqlalchemy.orm import Session
from sqlalchemy import select
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

def run_batch(cl, scans, qty_override=None):
    bid = cl.post("/api/batches", json={
        "bin": "F3-1", "created_by": "Nick"}).json()["id"]
    for _ in range(scans):
        cl.post(f"/api/batches/{bid}/scan", json={"code": "601"})
    if qty_override is not None:
        item = next(i for i in cl.get(f"/api/batches/{bid}").json()["items"]
                    if i["sku"] == "ZWO M54-7.5")
        cl.post(f"/api/batches/{bid}/items/{item['id']}/qty",
                json={"qty": qty_override})
    r = cl.post(f"/api/batches/{bid}/complete", json={
        "finalize": True, "created_by": "Nick"})
    return bid, r

def open_checks(s):
    return s.scalars(select(ReviewTask).where(
        ReviewTask.category == "inventory-check",
        ReviewTask.status == "open")).all()

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.main._kick_orders_sync_soon"):
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="ZWO M54-7.5", barcode="601",
                          product_title="ZWO M54-M54 7.5mm Extender",
                          bin="F3-1", qty=1, shopify_variant_id="t:M54"))
        s.commit()

    # 1) A wrong count files the check (scan once, correct to 0).
    b1, r = run_batch(cl, 1, qty_override=0)
    check("a disagreeing count files the check", r.status_code == 200,
          r.text[:200])
    with Session(get_engine()) as s:
        t = open_checks(s)
        check("the task remembers 0 counted vs on-hand 1",
              len(t) == 1 and "0 unit(s) counted" in t[0].detail
              and "on-hand is 1" in t[0].detail,
              [x.detail for x in t])
        task_id = t[0].id

    # 2) A newer batch counts the 1: the task closes ITSELF.
    b2, r = run_batch(cl, 1)
    with Session(get_engine()) as s:
        t = s.get(ReviewTask, task_id)
        check("the agreeing recount closes the task automatically",
              t.status == "resolved" and t.resolved_by == "batch-count",
              (t.status, t.resolved_by))
        check("the closure says a newer count matched",
              "closed automatically" in (t.resolution_note or ""),
              t.resolution_note)
        check("the counted figure was updated before closing",
              "1 unit(s) counted" in t.detail, t.detail)
        notes = s.scalars(select(ReviewNote).where(
            ReviewNote.task_key == str(task_id))).all()
        check("the recount left a note naming its batch",
              any(f"batch #{b2}" in (n.note or "") for n in notes),
              [n.note for n in notes])
        check("no new task was filed for the agreeing count",
              open_checks(s) == [], [x.detail for x in open_checks(s)])

    hist = cl.get("/api/history?limit=100").json()["events"]
    ev = next((e for e in hist if e["type"] == "review-autoclosed"), None)
    check("History tags the closure Auto-Resolved, not a click",
          ev is not None and ev["worker"] == "batch-count"
          and "closed automatically" in ev["detail"], ev)
    check("no plain review-resolved event doubles it",
          not any(e["type"] == "review-resolved"
                  and (e.get("sku") or "") == "ZWO M54-7.5" for e in hist),
          None)

    # 3) A fresh mismatch opens a task; the NEXT mismatch lands on the
    # SAME task (updated figures, no duplicate).
    run_batch(cl, 3)
    with Session(get_engine()) as s:
        t = open_checks(s)
        check("a fresh mismatch opens one task",
              len(t) == 1 and "3 unit(s) counted" in t[0].detail,
              [x.detail for x in t])
        t3_id = t[0].id
    b4, _ = run_batch(cl, 2)
    with Session(get_engine()) as s:
        t = open_checks(s)
        check("a later mismatch updates the SAME task - no duplicate",
              len(t) == 1 and t[0].id == t3_id
              and "2 unit(s) counted" in t[0].detail
              and t[0].batch_id == b4,
              [(x.id, x.detail, x.batch_id) for x in t])

    # 4) The other system closers wear the same Auto-Resolved tag.
    with Session(get_engine()) as s:
        from datetime import datetime, timezone
        t = s.get(ReviewTask, t3_id)
        t.status = "resolved"; t.resolved_by = "orders-sync"
        t.resolved_at = datetime.now(timezone.utc)
        t.resolution_note = "Tags, on-hand and the sold ledger agree again."
        s.commit()
    hist = cl.get("/api/history?limit=100").json()["events"]
    check("orders-sync closures read Auto-Resolved too",
          any(e["type"] == "review-autoclosed"
              and e["worker"] == "orders-sync" for e in hist), None)

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
