"""TC-Planner receiving sync (2026-09-29, SO 964): the planner sends each
line's CUMULATIVE received count; this app books only what it hasn't
booked for that stock order yet (across open AND closed batches), so a
repeat call never double-counts and a missed one is repaired by the
next. print=true queues the owed labels - only for the asked SKUs.
so-labels reports booked / queued / owed per SKU for the planner."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_receiving_sync_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_engine
from app.models import Batch, BarcodeChange, BinMapEntry, OrderReceipt, PrintJob
from sqlalchemy.orm import Session
from sqlalchemy import func, select
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

def jobs_for(sku):
    with Session(get_engine()) as s:
        return s.scalar(select(func.count()).select_from(PrintJob)
                        .where(PrintJob.sku == sku)) or 0

def sku_row(out, sku):
    return next((x for x in out["skus"] if x["sku"] == sku), None)

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.main._kick_orders_sync_soon"):
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="93230", product_title="Celestron A",
                          bin="C1-1", qty=0, barcode="050234932301",
                          shopify_variant_id="t:A"))
        s.add(BinMapEntry(sku="81035", product_title="Celestron B",
                          bin="C1-2", qty=0, barcode="050234810357",
                          shopify_variant_id="t:B"))
        s.add(BinMapEntry(sku="93704", product_title="Moon Map",
                          bin="", qty=0, barcode="050234937047",
                          shopify_variant_id="t:M"))
        s.add(BinMapEntry(sku="104988", product_title="Moon Atlas",
                          bin="F5-1", qty=0, barcode="9780228104988",
                          shopify_variant_id="t:ATLAS"))
        s.commit()
    ref = "SO 964 · Celestron"
    items = [
        {"sku": "93230", "barcode": "050234932301", "received_total": 8},
        {"sku": "81035", "barcode": "050234810357", "received_total": 12},
        {"sku": "93704", "barcode": "050234937047", "received_total": 4},
    ]

    # Save: book, no labels.
    r = cl.post("/api/receiving/sync", json={
        "reference": ref, "requested_by": "Nick", "items": items})
    check("sync answers", r.status_code == 200, r.text[:300])
    out = r.json()
    check("every received unit is booked", sum(a["quantity"] for a in out["booked"]) == 24,
          out["booked"])
    check("  ...with no labels queued", out["queued"] == 0 and jobs_for("93230") == 0)
    with Session(get_engine()) as s:
        b = s.scalars(select(Batch).where(Batch.kind == "receiving")).all()
        ev = s.scalars(select(BarcodeChange).where(
            BarcodeChange.changed_field == "receiving-booked")).all()
    check("one receiving batch named for the SO",
          len(b) == 1 and b[0].created_by == "TC-Planner · SO 964 · Celestron",
          [x.created_by for x in b])
    check("booking is History-logged", len(ev) == 1)
    a = sku_row(out, "93230")
    check("status: booked 8, 8 labels owed", a and a["booked_units"] == 8
          and a["labels_owed"] == 8 and a["labels_queued"] == 0, a)
    check("the binless line says why it can't print",
          "bin" in (sku_row(out, "93704")["problem"] or "").lower())

    # Push: the same cumulative totals again - nothing new.
    out = cl.post("/api/receiving/sync", json={
        "reference": ref, "items": items}).json()
    check("a repeat sync books nothing (no double count)",
          out["booked"] == [] and sku_row(out, "93230")["booked_units"] == 8, out)

    # Per-line Print: just 93230.
    out = cl.post("/api/receiving/sync", json={
        "reference": ref, "items": items, "print": True,
        "print_skus": ["93230"]}).json()
    check("per-line print queues only that line's labels",
          out["queued"] == 8 and jobs_for("93230") == 8 and jobs_for("81035") == 0,
          (out["queued"], jobs_for("81035")))
    check("  ...and it shows as printed",
          sku_row(out, "93230")["labels_owed"] == 0
          and sku_row(out, "93230")["labels_queued"] == 8)
    out = cl.post("/api/receiving/sync", json={
        "reference": ref, "items": items, "print": True,
        "print_skus": ["93230"]}).json()
    check("printing the same line again queues nothing", out["queued"] == 0)

    # More arrive: cumulative 12 -> 15 books just 3 more.
    items[1]["received_total"] = 15
    out = cl.post("/api/receiving/sync", json={
        "reference": ref, "items": items, "print": True}).json()
    check("a later receive books only the difference",
          sum(a["quantity"] for a in out["booked"]) == 3
          and sku_row(out, "81035")["booked_units"] == 15, out["booked"])
    check("print-all queues every owed label (binless held)",
          jobs_for("81035") == 15 and jobs_for("93704") == 0
          and out["skipped_no_bin"] == ["Moon Map"], (jobs_for("81035"), out["skipped_no_bin"]))
    st = cl.get("/api/receiving/so-labels", params={"reference": "SO 964"}).json()
    check("so-labels reports the whole order",
          st["labels_owed"] == 4 and sku_row(st, "81035")["labels_queued"] == 15, st)

    # The bin gets added in Shopify (the planner insists before printing):
    # the print re-reads it live and the held labels go out with it.
    with patch("app.shopify.lookup_barcode",
               return_value={"bin_location": "C3-3", "sku": "93704"}):
        out = cl.post("/api/receiving/sync", json={
            "reference": ref, "items": items, "print": True,
            "print_skus": ["93704"]}).json()
    with Session(get_engine()) as s:
        bins = {j.bin_location for j in s.scalars(
            select(PrintJob).where(PrintJob.sku == "93704"))}
    check("a bin added after booking reaches the labels",
          out["queued"] == 4 and out["skipped_no_bin"] == [] and bins == {"C3-3"},
          (out["queued"], out["skipped_no_bin"], bins))

    # The batch closes, then a late unit arrives: count the closed batch.
    with Session(get_engine()) as s:
        for bb in s.scalars(select(Batch).where(Batch.kind == "receiving")):
            bb.status = "done"
        s.commit()
    items[0]["received_total"] = 9
    out = cl.post("/api/receiving/sync", json={
        "reference": ref, "items": items}).json()
    check("closed batches still count as booked (only 1 new unit)",
          sum(a["quantity"] for a in out["booked"]) == 1, out["booked"])
    with Session(get_engine()) as s:
        n_open = s.scalar(select(func.count()).select_from(Batch).where(
            Batch.kind == "receiving", Batch.status != "done"))
    check("  ...on a fresh batch for the SO", n_open == 1)

    # A full-shipment order is left alone.
    with Session(get_engine()) as s:
        s.add(OrderReceipt(stock_order_id=1300, reference="SO 977",
                           batch_id=1))
        s.commit()
    out = cl.post("/api/receiving/sync", json={
        "reference": "SO 977 · ZWO", "items": items, "print": True}).json()
    check("a full-shipment order books and prints nothing",
          out["full_shipment"] is True and out["queued"] == 0)
    bad = cl.post("/api/receiving/sync", json={"reference": "PO 12", "items": items})
    check("a reference without an SO number is refused", bad.status_code == 422)

    # ---- the planner's Undo receive (SO 977's case; SO 978 here) ----
    ref2 = "SO 978 · Firefly Books"
    atlas = lambda n: [{"sku": "104988", "barcode": "9780228104988",
                        "received_total": n}]
    def atlas_row():
        with Session(get_engine()) as s:
            from app.models import BatchItem
            return s.scalars(select(BatchItem).where(BatchItem.sku == "104988")).first()
    def atlas_jobs():
        with Session(get_engine()) as s:
            return sorted(j.status for j in s.scalars(
                select(PrintJob).where(PrintJob.sku == "104988")))
    cl.post("/api/receiving/sync", json={"reference": ref2, "items": atlas(3)})
    out = cl.post("/api/receiving/sync", json={"reference": ref2, "items": atlas(2)}).json()
    check("a plain sync never lowers a booking",
          out.get("unbooked") == [] and atlas_row().qty_scanned == 3, out.get("unbooked"))
    out = cl.post("/api/receiving/sync", json={
        "reference": ref2, "items": atlas(2), "allow_lower": True}).json()
    check("the undo lowers the booking to the planner's total",
          out["unbooked"] == [{"sku": "104988", "quantity": 1}]
          and atlas_row().qty_scanned == 2 and atlas_row().expected_qty == 2,
          out["unbooked"])
    cl.post("/api/receiving/sync", json={
        "reference": ref2, "items": atlas(2), "print": True})
    check("  two labels queued for the two left", atlas_jobs() == ["pending", "pending"])
    out = cl.post("/api/receiving/sync", json={
        "reference": ref2, "items": atlas(1), "allow_lower": True}).json()
    check("undoing a unit cancels its waiting label",
          atlas_jobs() == ["canceled", "pending"] and out["labels_owed"] == 0
          and out["labels_voided"] == 0, atlas_jobs())
    with Session(get_engine()) as s:
        for j in s.scalars(select(PrintJob).where(PrintJob.sku == "104988",
                                                   PrintJob.status == "pending")):
            j.status = "done"
        s.commit()
    out = cl.post("/api/receiving/sync", json={
        "reference": ref2, "items": atlas(0), "allow_lower": True}).json()
    check("undoing a printed unit voids its label and says so",
          atlas_jobs() == ["canceled", "voided"] and out["labels_voided"] == 1
          and "discard" in out["message"], (atlas_jobs(), out["message"]))
    check("  ...and the emptied row is gone", atlas_row() is None)
    st = cl.get("/api/receiving/so-labels", params={"reference": "SO 978"}).json()
    check("nothing is owed for the undone order", st["labels_owed"] == 0, st)

    # A tagged box stays booked however far the undo goes.
    cl.post("/api/receiving/sync", json={"reference": ref2, "items": atlas(2)})
    with Session(get_engine()) as s:
        from app.models import RfidAssignment
        r = atlas_row()
        s.add(RfidAssignment(rfid_id="E0000000000000000000A7A5",
                             shopify_variant_id="t:ATLAS", product_title="Moon Atlas",
                             sku="104988", bin_location="F5-1", batch_id=r.batch_id))
        s.commit()
    cl.post("/api/receiving/sync", json={
        "reference": ref2, "items": atlas(0), "allow_lower": True})
    check("the undo never goes below the boxes already tagged",
          atlas_row() is not None and atlas_row().qty_scanned == 1,
          atlas_row() and atlas_row().qty_scanned)
    with Session(get_engine()) as s:
        n = len(s.scalars(select(BarcodeChange).where(
            BarcodeChange.changed_field == "receiving-unbooked")).all())
    check("every undo is History-logged", n == 4, n)

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("ALL PASS")
