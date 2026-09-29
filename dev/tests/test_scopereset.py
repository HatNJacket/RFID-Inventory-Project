"""The 2026-09-28 scope-reset build: heard stamps, audit anchors +
expected ranges + Sales agree, the scored queue split, packing scans
with exact retirement, un-bundling, and the draft-naming format."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ["SHOPIFY_WRITE_MODE"]="on"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_scopereset_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.main import app
from app.database import get_engine
from app.models import (BinAudit, BinMapEntry, BundleContent, PackScan,
                        PrintJob, RetiredTag, RfidAssignment, SoldRecord)
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

NOW = datetime.now(timezone.utc)

with patch("app.main._maybe_refresh_bin_map", return_value=False), \
     patch("app.main._kick_orders_sync_soon"), \
     patch("app.main._pickup_pending_map", return_value={}):
  with TestClient(app) as cl:
    # ---- seed: one bin, one product, three tags, sales around them ----
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="AUD-1", product_title="Audited Widget",
                          bin="Q1-1", qty=2, shopify_variant_id="t:a1"))
        for i, e in enumerate(("A1", "A2", "A3")):
            s.add(RfidAssignment(
                rfid_id=f"AAAA00000000000000000{e}",
                shopify_variant_id="t:a1", product_title="Audited Widget",
                sku="AUD-1", bin_location="Q1-1",
                assigned_at=NOW - timedelta(days=30),
            ))
        # one sale AFTER the tags were last heard (will explain A3)
        s.add(SoldRecord(order_id="o1", order_name="9001", sku="AUD-1",
                         quantity=1, fulfilled_at=NOW - timedelta(days=2)))
        s.commit()

    # ---- heard stamps: a capture stamps last_heard_at ------------------
    r = cl.post("/api/epc-captures", json={
        "device": "T", "epcs": ["AAAA00000000000000000A1",
                                "AAAA00000000000000000A2"]})
    check("capture accepted", r.status_code == 201, r.text[:150])
    with Session(get_engine()) as s:
        heard = {t.rfid_id[-2:]: t.last_heard_at for t in
                 s.scalars(select(RfidAssignment))}
    check("sweep stamps last_heard_at on heard tags only",
          heard["A1"] is not None and heard["A2"] is not None
          and heard["A3"] is None, heard)

    # ---- bin check: range + silent detail + Sales agree ----------------
    r = cl.post("/api/bins/Q1-1/check", json={
        "epcs": ["AAAA00000000000000000A1", "AAAA00000000000000000A2"]})
    row = next(x for x in r.json()["items"] if x["sku"] == "AUD-1")
    # on-hand 2, tags 3, sold 1 -> range [2, 2]; heard 2 -> in range
    check("expected is the range [min,max] of {H, T-S}",
          row["range_lo"] == 2 and row["range_hi"] == 2
          and row["in_range"] is True, row)
    check("silent tags carry last-heard detail",
          len(row["silent_tags"]) == 1
          and row["silent_tags"][0]["epc"].endswith("A3"), row["silent_tags"])
    check("Sales agree: the silent tag predates a matching sale",
          row["sales_agree"] is True, row)

    # a sale OLDER than every hearing cannot explain a silence
    with Session(get_engine()) as s:
        sr = s.scalars(select(SoldRecord)).one()
        sr.fulfilled_at = NOW - timedelta(days=60)
        s.commit()
    r = cl.post("/api/bins/Q1-1/check", json={
        "epcs": ["AAAA00000000000000000A1", "AAAA00000000000000000A2"]})
    row = next(x for x in r.json()["items"] if x["sku"] == "AUD-1")
    check("times that don't line up refuse the note (sold before pool "
          "baseline drops out of the window)",
          row["sales_agree"] in (False, None)
          and row["sold_unretired"] == 0, row)
    with Session(get_engine()) as s:
        sr = s.scalars(select(SoldRecord)).one()
        sr.fulfilled_at = NOW - timedelta(days=2)
        s.commit()

    # ---- audit sign-off writes the anchor ------------------------------
    r = cl.post("/api/bins/Q1-1/audit-complete", json={
        "epcs": ["AAAA00000000000000000A1", "AAAA00000000000000000A2"],
        "worker": "Nick"})
    check("audit-complete answers", r.status_code == 201, r.text[:200])
    with Session(get_engine()) as s:
        anchor = s.scalars(select(BinAudit)).one()
    check("anchor stores the per-SKU heard baseline",
          anchor.bin == "Q1-1" and anchor.baseline_map().get("AUD-1") == 2,
          (anchor.bin, anchor.baseline))

    # a sale BEFORE the anchor no longer explains anything; one after does
    r = cl.post("/api/bins/Q1-1/check", json={
        "epcs": ["AAAA00000000000000000A1", "AAAA00000000000000000A2"]})
    row = next(x for x in r.json()["items"] if x["sku"] == "AUD-1")
    check("sold window anchors at the audit (older sale drops out)",
          row["sold_unretired"] == 0 and row["range_lo"] == 2
          and row["range_hi"] == 3, row)

    # ---- the scored queue: anchored diff + overdue split ---------------
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="NEW-9", product_title="Never Audited",
                          bin="Z9-1", qty=4, shopify_variant_id="t:n9"))
        # sale after the anchor moves the walked-forward estimate
        s.add(SoldRecord(order_id="o2", order_name="9002", sku="AUD-1",
                         quantity=1, fulfilled_at=NOW + timedelta(minutes=5)))
        s.commit()
    r = cl.get("/api/audit/bins")
    d = r.json()
    q1 = next(b for b in d["bins"] if b["bin"].upper() == "Q1-1")
    z9 = next(b for b in d["bins"] if b["bin"].upper() == "Z9-1")
    check("anchored bin walks forward: |H - (heard - sold + recv)| "
          "= |2 - (2-1+0)| = 1",
          q1["score"] == 1 and q1["overdue"] is False
          and q1["last_audited_by"] == "Nick", q1)
    check("never-audited bin is overdue with the fallback arithmetic",
          z9["overdue"] is True, z9)
    check("response carries the queue numbers",
          d["threshold_days"] >= 1 and "overdue_count" in d, d.keys())

    # ---- review endpoints are gone, history stays ----------------------
    check("review inbox endpoints answer 404/405",
          cl.get("/api/review-tasks").status_code in (404, 405)
          and cl.post("/api/review-tasks/1/resolve", json={}).status_code
          in (404, 405), "")

    # ---- packing scans -------------------------------------------------
    ORDERS = [{
        "orderId": 501, "orderNumber": "9100",
        "orderDate": "2026-09-27T09:00:00",
        "items": [{"sku": "AUD-1", "quantity": 1}],
    }]
    with patch("app.shipstation.configured", return_value=True), \
         patch("app.shipstation.get_awaiting_shipment_orders",
               return_value=ORDERS):
        r = cl.post("/api/packing/scans", json={
            "code": "AAAA00000000000000000A1", "worker": "Nick"})
        check("RFID pack scan allocates against the oldest open order",
              r.status_code == 201 and r.json()["status"] == "allocated"
              and "#9100" in r.json()["message"], r.text[:200])
        r = cl.post("/api/packing/scans", json={
            "code": "AAAA00000000000000000A2", "worker": "Nick"})
        check("a second unit the orders don't want reads duplicate",
              r.json()["status"] == "duplicate", r.text[:200])
        r = cl.post("/api/packing/scans", json={"code": "no-such-code"})
        check("an unknown code says so", r.json()["status"] == "unknown",
              r.text[:150])
    # the hourly sync's ledger row flips the scan to shipped and
    # retires EXACTLY the scanned tag
    with Session(get_engine()) as s:
        s.add(SoldRecord(order_id="gid://o/501", order_name="9100",
                         sku="AUD-1", quantity=1, ss_order_id="501",
                         source="shipstation", fulfilled_at=NOW))
        s.commit()
    r = cl.get("/api/packing/scans")
    rows = {x["code"]: x for x in r.json()["scans"]}
    check("the allocated scan flips to shipped",
          rows["AAAA00000000000000000A1"]["status"] == "shipped",
          rows.get("AAAA00000000000000000A1"))
    with Session(get_engine()) as s:
        gone = s.scalar(select(RfidAssignment).where(
            RfidAssignment.rfid_id == "AAAA00000000000000000A1"))
        tomb = s.scalar(select(RetiredTag).where(
            RetiredTag.rfid_id == "AAAA00000000000000000A1"))
    check("EXACTLY the packed tag retired (sold, by packing-scan)",
          gone is None and tomb is not None and tomb.kind == "sold"
          and tomb.retired_by == "packing-scan", (gone, tomb))
    # mis-scan removal
    with Session(get_engine()) as s:
        dup_id = s.scalar(select(PackScan.id).where(
            PackScan.status == "duplicate"))
    r = cl.request("DELETE", f"/api/packing/scans/{dup_id}")
    check("a mis-scan removes", r.status_code == 200, r.text[:150])

    # ---- un-bundling ---------------------------------------------------
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="BUN-1", product_title="Bundle Kit",
                          bin="B1-1", qty=1, shopify_variant_id="t:b1"))
        s.add(BinMapEntry(sku="PART-A", barcode="111222",
                          product_title="Part A", bin="B1-2", qty=3,
                          shopify_variant_id="t:pa"))
        s.add(RfidAssignment(rfid_id="BBBB0000000000000000000B",
                             shopify_variant_id="t:b1",
                             product_title="Bundle Kit", sku="BUN-1",
                             bin_location="B1-1"))
        s.add(BundleContent(bundle_sku="BUN-1", component_sku="PART-A",
                            qty=2))
        s.commit()
    r = cl.post("/api/bundles/BUN-1/unbundle", json={
        "units": 2, "contents": [{"sku": "PART-A", "qty": 2}],
        "worker": "Nick"})
    check("unconfirmed un-bundle asks with the numbers",
          r.status_code == 409 and "4 component labels" in
          r.json()["detail"], r.text[:250])
    r = cl.post("/api/bundles/BUN-1/unbundle", json={
        "units": 2, "contents": [{"sku": "PART-A", "qty": 2}],
        "worker": "Nick", "confirmed": True})
    check("confirmed un-bundle retires the bundle tag and queues labels",
          r.status_code == 201 and r.json()["retired_tags"] == 1
          and r.json()["labels_queued"] == 4, r.text[:250])
    with Session(get_engine()) as s:
        tomb = s.scalar(select(RetiredTag).where(
            RetiredTag.rfid_id == "BBBB0000000000000000000B"))
        jobs = s.scalars(select(PrintJob).where(
            PrintJob.sku == "PART-A",
            PrintJob.status == "pending")).all()
    check("bundle tag tombstoned as unbundled; component jobs pending",
          tomb is not None and tomb.kind == "unbundled" and len(jobs) == 4,
          (tomb and tomb.kind, len(jobs)))

    # ---- draft naming --------------------------------------------------
    made = {}
    def fake_draft(title, sku, barcode, bin_):
        made["title"] = title
        return {"variant_gid": "gid://v/9", "product_gid": "gid://p/9"}
    with patch("app.shopify.find_sku_listing", return_value=None), \
         patch("app.shopify.create_draft_listing", side_effect=fake_draft):
        r = cl.post("/api/products/create-draft", json={
            "sku": "ABC-2", "ingredient": True, "worker": "Nick"})
    check("ingredient draft wears the settled format with the derived "
          "main SKU",
          r.status_code == 201
          and made["title"] == "ABC-2 DRAFT BUNDLE COMPONENT -> ABC",
          (r.status_code, made))
    with patch("app.shopify.find_sku_listing", return_value=None), \
         patch("app.shopify.create_draft_listing", side_effect=fake_draft):
        r = cl.post("/api/products/create-draft", json={
            "sku": "XYZ-3", "ingredient": True, "main_sku": "MAINX",
            "worker": "Nick"})
    check("an explicit main SKU wins over the suffix rule",
          made["title"] == "XYZ-3 DRAFT BUNDLE COMPONENT -> MAINX", made)

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("ALL PASS")
