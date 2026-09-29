"""TC-Planner streamlining round (Nick, 2026-08-26, built NOT deployed):
- /api/receiving/unprinted: the Update-stock safety net. Stock pushed
  to Shopify without labels books into the stock order's receiving
  batch with NO labels queued; the waiting work shows on the batch
  itself (the review inbox is gone, 2026-09-28) and the batch's own
  print pass queues it, no-bin items held out.
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
                        RfidAssignment)
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
    check("the message names the waiting work and the bin-less product",
          "3 waiting labels" in out["message"]
          and "Binless Thing" in out["message"], out["message"])
    with Session(get_engine()) as s:
        jobs = s.scalars(select(PrintJob).where(
            PrintJob.batch_id == bid)).all()
        check("no labels were actually queued", jobs == [],
              [j.sku for j in jobs])
        evs = s.scalars(select(BarcodeChange).where(
            BarcodeChange.changed_field == "labels-not-printed")).all()
        check("History keeps the safety-net record",
              len(evs) == 1 and f"#{bid}" in evs[0].new_barcode,
              [e.new_barcode for e in evs])

    # --- 2) a second push folds into the SAME task --------------------
    r = cl.post("/api/receiving/unprinted", json={
        "items": [{"sku": "AG-KIT", "quantity": 2}],
        "requested_by": "Nick", "reference": "SO 900 · AG"})
    check("a repeat push reuses the batch",
          r.json()["batch"]["id"] == bid, r.json()["batch"])
    check("the owed-label count accumulates",
          r.json()["labels_waiting"] == 5, r.json())
    # --- 3) the batch's OWN print pass queues what it can; no-bin
    # products are held out and named -----------------------------------
    r = cl.post(f"/api/batches/{bid}/queue-labels",
                json={"requested_by": "Nick"})
    d = r.json()
    check("the print pass queues every printable label",
          r.status_code in (200, 201) and d["count"] == 5, r.text[:250])
    check("no-bin products are held out and named",
          d["skipped_no_bin"] == ["Binless Thing"], r.text[:250])
    with Session(get_engine()) as s:
        jobs = s.scalars(select(PrintJob).where(
            PrintJob.batch_id == bid)).all()
        check("labels carry the item's home bin",
              len(jobs) == 5 and all(j.bin_location == "I5-1"
                                     for j in jobs),
              [(j.sku, j.bin_location) for j in jobs])
    # Assign the bin, then a second pass queues the remainder.
    with Session(get_engine()) as s:
        for it in s.scalars(select(BatchItem).where(
                BatchItem.batch_id == bid)):
            if it.sku == "NOBIN-2":
                it.bin_location = "J9-9"
        s.commit()
    r = cl.post(f"/api/batches/{bid}/queue-labels",
                json={"requested_by": "Nick"})
    check("with the bin assigned, the next pass queues the rest",
          r.status_code in (200, 201) and r.json()["count"] == 2,
          r.text[:250])
    with Session(get_engine()) as s:
        jobs = s.scalars(select(PrintJob).where(
            PrintJob.batch_id == bid)).all()
        check("the once-binless labels queued to the new bin",
              sorted(j.bin_location for j in jobs
                     if j.sku == "NOBIN-2") == ["J9-9", "J9-9"],
              [(j.sku, j.bin_location) for j in jobs])

    # --- 4) a later label-less push files a FRESH task ----------------
    cl.post("/api/receiving/unprinted", json={
        "items": [{"sku": "AG-KIT", "quantity": 1}],
        "requested_by": "Nick", "reference": "SO 900 · AG"})
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

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
