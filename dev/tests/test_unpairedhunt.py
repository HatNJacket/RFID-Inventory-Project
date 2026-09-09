"""Unresolved printed labels + the Unpaired Tags hunt (Nick,
2026-09-09): the receiving-only unpaired-label list (dismissable one
instance at a time), the batched EPC classifier the C72's hunt leans
on, and the locate pair - assignment created, one receiving label
instance consumed, Locate Assigned Tag history event with a working
undo that gives the instance back.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_unpairedhunt_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

KNOWN   = "5AAA0000000000000000000A"
RETIRED = "5AAA0000000000000000000B"
MYSTERY = "5AAA0000000000000000000C"

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.main._maybe_refresh_bin_map", return_value=False), \
     patch("app.main.oneleft"):
  with TestClient(app) as cl:
    from sqlalchemy import select
    from sqlalchemy.orm import Session as S
    from app.database import get_engine
    from app.models import (Batch, BatchItem, BarcodeChange, BinMapEntry,
                            PrintJob, RetiredTag, RfidAssignment)

    with S(get_engine()) as s:
        s.add(BinMapEntry(sku="ZWO-A", barcode="801",
                          product_title="ZWO thing A", bin="B1-1", qty=5,
                          shopify_variant_id="t:a",
                          shopify_product_id="gid://p/a"))
        rb = Batch(bin_name="RECEIVING", kind="receiving",
                   created_by="TC-Planner · SO 950")
        s.add(rb); s.flush()
        it = BatchItem(batch_id=rb.id, scanned_code="801", resolved=True,
                       sku="ZWO-A", barcode="801",
                       product_title="ZWO thing A", qty_scanned=3,
                       paired_count=1, bin_location="B1-1")
        s.add(it)
        for i in range(3):
            s.add(PrintJob(epc=f"6AB{i}000000000000000000A1",
                           status="done", shopify_variant_id="t:a",
                           product_title="ZWO thing A", sku="ZWO-A",
                           batch_id=rb.id, bin_location="B1-1"))
        # A BIN batch with a printed label: must never join the list.
        nb = Batch(bin_name="D1-1", created_by="n")
        s.add(nb); s.flush()
        s.add(BatchItem(batch_id=nb.id, scanned_code="802", resolved=True,
                        sku="OTHER-1", barcode="802",
                        product_title="Other", qty_scanned=1))
        s.add(PrintJob(epc="7AB0000000000000000000B1", status="done",
                       shopify_variant_id="t:o", product_title="Other",
                       sku="OTHER-1", batch_id=nb.id))
        s.add(RfidAssignment(rfid_id=KNOWN, shopify_variant_id="t:x",
                             product_title="Known", sku="KNOWN-1",
                             bin_location="A1-1"))
        s.add(RetiredTag(rfid_id=RETIRED, kind="dead"))
        s.commit()
        itid, rbid = it.id, rb.id

    # ---- the list: receiving only, printed minus paired ---------------
    r = cl.get("/api/receiving/unpaired-labels").json()
    check("one receiving product listed, bin batches excluded",
          r["count"] == 1 and r["products"][0]["sku"] == "ZWO-A", r)
    p = r["products"][0]
    check("count = printed 3 - paired 1", p["count"] == 2, p)
    check("row carries the order reference and batch",
          p["batch_id"] == rbid and "SO 950" in (p["reference"] or ""), p)
    check("EPC candidates match the count", len(p["epcs"]) == 2, p)

    # ---- the classifier the hunt leans on ------------------------------
    r = cl.post("/api/epcs/unlinked", json={
        "epcs": [KNOWN, RETIRED, MYSTERY, p["epcs"][0]]}).json()
    check("classifier: mystery + unpaired label EPCs are unlinked, "
          "known/retired are not",
          sorted(r["unlinked"]) == sorted([MYSTERY, p["epcs"][0]]), r)

    # ---- pairing consumes one receiving label instance ----------------
    r = cl.post("/api/locate/pair-unlinked", json={
        "epc": MYSTERY, "code": "801", "worker": "C72"})
    d = r.json()
    check("pair created", r.status_code == 201
          and d["assignment"]["sku"] == "ZWO-A", r.text[:300])
    check("one receiving instance consumed",
          d["bumped_item_id"] == itid, d)
    with S(get_engine()) as s:
        check("paired_count bumped 1 -> 2",
              s.get(BatchItem, itid).paired_count == 2, "")
    r = cl.get("/api/receiving/unpaired-labels").json()
    check("list dropped to 1 after the pair",
          r["products"] and r["products"][0]["count"] == 1, r)

    # ---- the unique history event, with a live undo --------------------
    hist = cl.get("/api/history?limit=50").json()
    ev = next((e for e in hist.get("events", hist.get("history", []))
               if e.get("changed_field") == "locate-paired"
               or e.get("field") == "locate-paired"
               or (e.get("undo") or {}).get("kind") == "locate-pair"),
              None)
    check("Locate Assigned Tag event present with undo descriptor",
          ev is not None and (ev.get("undo") or {}).get("kind")
          == "locate-pair"
          and (ev["undo"].get("item_id") == itid), ev)

    r = cl.post("/api/locate/pair-unlinked/undo", json={
        "epc": MYSTERY, "item_id": itid, "worker": "Nick"})
    check("undo answered", r.status_code == 200, r.text[:200])
    with S(get_engine()) as s:
        check("assignment removed", s.scalar(select(RfidAssignment).where(
            RfidAssignment.rfid_id == MYSTERY)) is None, "")
        check("receiving instance given back",
              s.get(BatchItem, itid).paired_count == 1, "")
    r = cl.get("/api/receiving/unpaired-labels").json()
    check("list back to 2 after the undo",
          r["products"] and r["products"][0]["count"] == 2, r)

    # ---- guards ---------------------------------------------------------
    r = cl.post("/api/locate/pair-unlinked", json={
        "epc": KNOWN, "code": "801"})
    check("pairing an already-owned tag refused", r.status_code == 409,
          r.status_code)
    r = cl.post("/api/locate/pair-unlinked", json={
        "epc": MYSTERY, "code": "no-such-code"})
    check("unknown barcode refused", r.status_code == 404, r.status_code)

    # ---- web dismissal retires one instance -----------------------------
    p = cl.get("/api/receiving/unpaired-labels").json()["products"][0]
    cl.post("/api/audit/dismiss-labels", json={
        "epcs": [p["epcs"][0]], "by": "Nick"})
    r = cl.get("/api/receiving/unpaired-labels").json()
    check("dismissing one label drops the count to 1",
          r["products"] and r["products"][0]["count"] == 1, r)

print()
if fails:
    print(f"{len(fails)} FAILURE(S):"); [print("  -", f) for f in fails]
    sys.exit(1)
print("ALL PASS")
