"""The C72 AUDIT tab's server side (Nick, 2026-09-01): rack-as-one-zone
bin checks with ghosts / debt / finds annotations, the audit-finds
lifecycle (open -> printed -> resolved by pairing, dismiss, 1h expiry),
natural bin ordering for the picker arrows, and the completed-audits
History log. The FINAL SWEEP is the only counter of physical presence -
finds are work items, never audit evidence."""
import os, sys, tempfile
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_auditwalk_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_engine
from app.models import (AuditFind, BackorderDebt, Batch, BinMapEntry,
                        PrintJob, RetiredTag, RfidAssignment)
from sqlalchemy.orm import Session
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.orders_sync.sold_unretired_map",
           side_effect=lambda s, skus: {k: 0 for k in skus}), \
     patch("app.main._maybe_refresh_bin_map", return_value=False):
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        # Rack F1 = three bins; E1-1 sorts before them; F10 after F2.
        s.add(BinMapEntry(sku="AAA-1", barcode="7001", product_title="Alpha",
                          bin="F1-1", qty=3, shopify_variant_id="t:A"))
        s.add(BinMapEntry(sku="BBB-1", barcode="7002", product_title="Beta",
                          bin="F1-2", qty=2, shopify_variant_id="t:B"))
        # Same SKU on TWO levels of the rack - one merged row expected.
        s.add(BinMapEntry(sku="AAA-1", barcode="7001", product_title="Alpha",
                          bin="F1-3", qty=1, shopify_variant_id="t:A"))
        s.add(BinMapEntry(sku="CCC-1", barcode="7003", product_title="Gamma",
                          bin="E1-1", qty=1, shopify_variant_id="t:C"))
        s.add(BinMapEntry(sku="DDD-1", barcode="7004", product_title="Delta",
                          bin="F2-1", qty=1, shopify_variant_id="t:D"))
        s.add(BinMapEntry(sku="EEE-1", barcode="7005", product_title="Eps",
                          bin="F10-1", qty=1, shopify_variant_id="t:E"))
        # Tags: AAA has one in F1-1 (heard), one in F1-3 (silent).
        s.add(RfidAssignment(rfid_id="EPCAAA1", sku="AAA-1",
                             product_title="Alpha", shopify_variant_id="t:A",
                             bin_location="F1-1"))
        s.add(RfidAssignment(rfid_id="EPCAAA2", sku="AAA-1",
                             product_title="Alpha", shopify_variant_id="t:A",
                             bin_location="F1-3"))
        # A ghost: BBB tag retired presumed-sold but it ANSWERS the sweep.
        s.add(RetiredTag(rfid_id="EPCGHOST", sku="BBB-1",
                         product_title="Beta", kind="presumed-sold",
                         retired_by="tester"))
        # Backorder debt raises BBB's shelf expectation.
        s.add(BackorderDebt(sku="BBB-1", units=1, source="test"))
        # A printed-label EPC nobody paired (answers as productless).
        s.add(PrintJob(epc="EPCLABEL", status="done", sku="BBB-1",
                       product_title="Beta", shopify_variant_id="t:B",
                       bin_location="F1-2"))
        # F1-2 was batch tagged before; F1-1/F1-3 never.
        s.add(Batch(bin_name="F1-2", status="done", created_by="t"))
        s.commit()

    # ---- natural bin order for the arrows --------------------------------
    r = cl.get("/api/bins/names")
    bins = r.json()["bins"]
    check("bins/names natural order: E first, F2 before F10",
          bins == ["E1-1", "F1-1", "F1-2", "F1-3", "F2-1", "F10-1"],
          str(bins))

    # ---- rack = one zone -------------------------------------------------
    epcs = ["EPCAAA1", "EPCGHOST", "EPCLABEL", "EPCNOBODY"]
    r = cl.post("/api/bins/F1/check", json={"epcs": epcs})
    rep = r.json()
    check("dash-less location expands to the rack",
          rep["rack"] and rep["bins_covered"] == ["f1-1", "f1-2", "f1-3"],
          str(rep.get("bins_covered")))
    rows = {i["sku"]: i for i in rep["items"]}
    check("same SKU on two levels merges into one row with summed qty",
          len([k for k in rows if k == "AAA-1"]) == 1
          and rows["AAA-1"]["expected_qty"] == 4
          and sorted(rows["AAA-1"]["bins"]) == ["F1-1", "F1-3"],
          str(rows.get("AAA-1")))
    check("rack counts tags from every covered level",
          rows["AAA-1"]["tags_here"] == 2
          and rows["AAA-1"]["detected"] == 1
          and rows["AAA-1"]["silent_epcs"] == ["EPCAAA2"],
          str(rows.get("AAA-1")))
    check("ghost: a retired tag that answers is reported on its product",
          len(rows["BBB-1"]["ghosts"]) == 1
          and rows["BBB-1"]["ghosts"][0]["epc"] == "EPCGHOST"
          and rows["BBB-1"]["ghosts"][0]["kind"] == "presumed-sold",
          str(rows.get("BBB-1")))
    check("backorder debt rides the product row",
          rows["BBB-1"]["backorder_debt"] == 1, str(rows.get("BBB-1")))
    check("a heard printed-label EPC is named, not an anonymous unknown",
          rep["printed_labels_heard"]
          and rep["printed_labels_heard"][0]["epc"] == "EPCLABEL"
          and rep["printed_labels_heard"][0]["sku"] == "BBB-1"
          and "EPCLABEL" not in rep["unknown_epcs"]
          and "EPCGHOST" not in rep["unknown_epcs"]
          and rep["unknown_epcs"] == ["EPCNOBODY"],
          str(rep.get("printed_labels_heard")) + str(rep.get("unknown_epcs")))
    check("per-bin batch-done state rides the report",
          rep["bins_batch_done"] == ["f1-2"] and rep["batch_done"] is False,
          str(rep.get("bins_batch_done")))

    # ---- dismissing the printed-label warning for good -------------------
    r = cl.post("/api/audit/dismiss-labels",
                json={"epcs": ["EPCLABEL"], "by": "Nick"})
    check("dismiss-labels accepts the EPC",
          r.status_code == 200 and r.json()["dismissed"] == 1,
          r.text[:150])
    r = cl.post("/api/bins/F1/check", json={"epcs": epcs})
    rep_d = r.json()
    check("a dismissed label vanishes from the report entirely",
          not rep_d["printed_labels_heard"]
          and "EPCLABEL" not in rep_d["unknown_epcs"],
          str(rep_d.get("printed_labels_heard"))
          + str(rep_d.get("unknown_epcs")))
    r = cl.post("/api/audit/dismiss-labels",
                json={"epcs": ["EPCLABEL"], "by": "Nick"})
    check("re-dismissing is a no-op", r.json()["dismissed"] == 0,
          r.text[:120])

    # Single-bin call unchanged (compatibility).
    r = cl.post("/api/bins/F1-2/check", json={"epcs": []})
    rep2 = r.json()
    check("single-bin check keeps its shape and batch_done truth",
          rep2["rack"] is False and rep2["bins_covered"] == ["f1-2"]
          and rep2["batch_done"] is True
          and rep2["batch_done_id"] is not None, str(rep2)[:200])

    # ---- audit finds lifecycle ------------------------------------------
    r = cl.post("/api/audit/finds", json={"code": "7002", "by": "Nick"})
    check("a tagless box becomes an OPEN find with its home bin",
          r.status_code == 201 and r.json()["find"]["status"] == "open"
          and r.json()["find"]["bin_location"] == "F1-2"
          and r.json()["no_home_bin"] is False
          and r.json()["open_for_sku"] == 1, r.text[:250])
    r = cl.post("/api/audit/finds", json={"code": "7002", "by": "Nick"})
    check("scanning the same barcode again = a second box",
          r.status_code == 201 and r.json()["open_for_sku"] == 2,
          r.text[:200])

    r = cl.post("/api/bins/F1/check", json={"epcs": []})
    rows = {i["sku"]: i for i in r.json()["items"]}
    check("open finds ride the bin check per product",
          rows["BBB-1"]["finds_open"] == 2, str(rows.get("BBB-1")))

    r = cl.post("/api/audit/finds/print-all", json={"by": "Nick"})
    check("print-all queues one label per open find (home bin labels)",
          r.status_code == 200 and r.json()["queued"] == 2
          and not r.json()["skipped"], r.text[:250])
    with Session(get_engine()) as s:
        finds = s.query(AuditFind).order_by(AuditFind.id).all()
        jobs = s.query(PrintJob).filter(
            PrintJob.sku == "BBB-1", PrintJob.status == "pending").all()
    check("printed finds remember their label EPCs",
          all(f.status == "printed" and f.print_epc for f in finds)
          and len(jobs) == 2
          and all(j.bin_location == "F1-2" for j in jobs),
          f"{[f.status for f in finds]} jobs={len(jobs)}")

    # Pairing the label's EPC ANYWHERE resolves the find lazily.
    with Session(get_engine()) as s:
        s.add(RfidAssignment(rfid_id=finds[0].print_epc, sku="BBB-1",
                             product_title="Beta",
                             shopify_variant_id="t:B",
                             bin_location="F1-2"))
        s.commit()
    r = cl.get("/api/audit/finds")
    body = r.json()
    check("a paired label resolves its find; the other stays printed",
          body["count"] == 1 and body["finds"][0]["status"] == "printed",
          r.text[:250])
    with Session(get_engine()) as s:
        done = s.query(AuditFind).filter(
            AuditFind.status == "resolved").all()
    check("resolution is recorded as by-pairing",
          len(done) == 1 and done[0].resolved_by == "pairing",
          str([f.status for f in done]))

    # Dismiss the leftover.
    fid = body["finds"][0]["id"]
    r = cl.post(f"/api/audit/finds/{fid}/dismiss", json={"by": "Nick"})
    check("dismiss closes a find", r.status_code == 200
          and r.json()["status"] == "dismissed", r.text[:150])
    r = cl.post(f"/api/audit/finds/{fid}/dismiss", json={"by": "Nick"})
    check("double-dismiss refuses", r.status_code == 409, r.status_code)

    # ---- expiry: never-printed finds die after an hour -------------------
    r = cl.post("/api/audit/finds", json={"code": "7001", "by": "Nick"})
    old_id = r.json()["find"]["id"]
    with Session(get_engine()) as s:
        f = s.get(AuditFind, old_id)
        f.created_at = datetime.utcnow() - timedelta(hours=2)
        s.commit()
    r = cl.get("/api/audit/finds")
    check("an hour-old unprinted find expires quietly",
          all(x["id"] != old_id for x in r.json()["finds"]), r.text[:200])
    with Session(get_engine()) as s:
        check("expiry is recorded on the row",
              s.get(AuditFind, old_id).status == "expired",
              s.get(AuditFind, old_id).status)

    # ---- SKU-consume rule: the ZD220t pairs FACTORY EPCs -----------------
    # (never the queued label EPC), so a pairing through either real
    # endpoint answers the oldest active find for that SKU.
    r = cl.post("/api/audit/finds", json={"code": "7004", "by": "Nick"})
    check("find for the pairing test", r.status_code == 201, r.text[:150])
    r = cl.post("/api/rfid-assignments", json={
        "rfid_id": "ABCDEF012345678901234567",
        "shopify_variant_id": "t:D", "product_title": "Delta",
        "sku": "DDD-1", "bin_location": "F2-1", "assigned_by": "C72"})
    check("station pairing accepted", r.status_code == 201, r.text[:150])
    with Session(get_engine()) as s:
        f = s.query(AuditFind).filter(AuditFind.sku == "DDD-1").one()
    check("a factory-EPC pairing consumes the find by SKU",
          f.status == "resolved" and f.resolved_by == "pairing",
          f.status)

    # ---- auto-print mode -------------------------------------------------
    r = cl.post("/api/audit/finds",
                json={"code": "7003", "by": "Nick", "auto_print": True})
    check("auto-print queues the label at scan time",
          r.status_code == 201 and r.json()["printed"] is True
          and r.json()["find"]["status"] == "printed", r.text[:250])
    # A product with no home bin refuses to auto-print and says so.
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="NOBIN-1", barcode="7009",
                          product_title="Nowhere", bin="No bin assigned",
                          qty=1, shopify_variant_id="t:N"))
        s.commit()
    r = cl.post("/api/audit/finds",
                json={"code": "7009", "by": "Nick", "auto_print": True})
    check("no home bin: find recorded, print refused, flag raised",
          r.status_code == 201 and r.json()["no_home_bin"] is True
          and r.json()["printed"] is False
          and r.json()["print_error"], r.text[:250])

    r = cl.post("/api/audit/finds", json={"code": "NO-SUCH-CODE-77"})
    check("an unknown code cannot become a find", r.status_code == 404,
          r.status_code)

    # ---- completed-audits log --------------------------------------------
    r = cl.post("/api/audit/complete",
                json={"location": "F1", "by": "Nick",
                      "summary": "3 products, 1 raised, all else clear"})
    check("audit complete files the log row", r.status_code == 201, r.text)
    r = cl.get("/api/history?limit=50")
    evs = [e for e in r.json()["events"] if e["type"] == "bin-audited"]
    check("History carries the bin-audited event",
          len(evs) == 1 and "F1" in (evs[0].get("title") or "")
          and evs[0].get("detail")
          == "3 products, 1 raised, all else clear",
          str(evs[:2])[:300])

print()
print(f"{'FAIL' if fails else 'OK'}  {len(fails)} failing")
sys.exit(1 if fails else 0)
