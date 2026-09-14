"""Packed-order sweep + ghost guards (Nick, 2026-09-14):

- /api/epcs/retire-sold retires a sweep's heard OWNED tags as sold,
  capped per product at unretired fulfilled sales (the guard) - a
  stray read of an uncovered product stays live. Preview first;
  unowned and already-retired EPCs counted, never touched; undo via
  unretire hands the ledger unit back.
- The Scan-Station over-pair guard: pairing past what stock explains
  answers with a warning (the Aug-18 bracket re-sticker trap).
- /api/assignments/cleanup-silent: a confirmed shelf's ghost records
  split oldest-first into presumed-sold (ledger-covered) + replaced.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_retiresold_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from datetime import datetime, timedelta
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.main import app
from app.database import get_engine
from app.models import (BinMapEntry, EpcCapture, RetiredTag,
                        RfidAssignment, SoldRecord)
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

E = lambda n: f"E28069150000AAAA0000{n:04d}"

with patch("app.main.oneleft") as ol:
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        # SKU-A: 3 tags, 2 unretired sales. SKU-B: 1 tag, no sales.
        for i in (1, 2, 3):
            s.add(RfidAssignment(rfid_id=E(i), shopify_variant_id="t:1",
                                 product_title="Prod A", sku="SKU-A",
                                 bin_location="F1-1"))
        s.add(RfidAssignment(rfid_id=E(4), shopify_variant_id="t:2",
                             product_title="Prod B", sku="SKU-B",
                             bin_location="F1-2"))
        s.add(RetiredTag(rfid_id=E(5), sku="OLD-1", kind="presumed-sold"))
        for n, oid in ((1, "o1"), (1, "o2")):
            s.add(SoldRecord(order_id=oid, order_name="#"+oid,
                             sku="SKU-A", quantity=n, retired=0,
                             fulfilled_at=datetime.utcnow()
                             - timedelta(days=3)))
        s.commit()

    heard = [E(1), E(2), E(3), E(4), E(5), E(9)]  # E9 = unpaired junk

    # ---- preview: the plan, nothing written ---------------------------
    r = cl.post("/api/epcs/retire-sold", json={
        "epcs": heard, "worker": "Nick", "preview": True})
    d = r.json()
    check("preview answered", r.status_code == 200, r.text[:300])
    pa = next((p for p in d["plan"] if p["sku"] == "SKU-A"), {})
    pb = next((p for p in d["plan"] if p["sku"] == "SKU-B"), {})
    check("SKU-A: 2 of 3 heard covered by sales",
          pa.get("retire") == 2 and pa.get("skipped") == 1, d)
    check("SKU-B: no fulfilled sale - nothing retires",
          pb.get("retire") == 0 and pb.get("skipped") == 1, d)
    check("unowned + already-retired counted, not planned",
          d.get("unowned") == 1 and d.get("already_retired") == 1, d)
    check("preview wrote nothing", d.get("applied") is False, d)
    with Session(get_engine()) as s:
        live = s.scalars(select(RfidAssignment)).all()
        check("all 4 tags still live after preview", len(live) == 4,
              len(live))

    # ---- apply: guard holds ------------------------------------------
    r = cl.post("/api/epcs/retire-sold", json={
        "epcs": heard, "worker": "Nick"})
    d = r.json()
    check("apply answered", r.status_code == 200, r.text[:300])
    check("2 retired, 2 stayed live",
          d.get("retire_total") == 2 and d.get("skip_total") == 2, d)
    check("message names the uncovered stay-live tags",
          "stayed live" in d.get("message", ""), d)
    with Session(get_engine()) as s:
        live = {a.rfid_id for a in s.scalars(select(RfidAssignment))}
        check("oldest two SKU-A records retired, third + SKU-B live",
              live == {E(3), E(4)}, live)
        tomb = s.scalars(select(RetiredTag).where(
            RetiredTag.sku == "SKU-A")).all()
        check("tombstones are presumed-sold with the sweep note",
              len(tomb) == 2 and all(
                  t.kind == "presumed-sold"
                  and "packed-order sweep" in (t.note or "")
                  for t in tomb), [(t.kind, t.note) for t in tomb])
        led = s.scalars(select(SoldRecord).where(
            SoldRecord.sku == "SKU-A")).all()
        check("both ledger units consumed",
              all((x.retired or 0) == x.quantity for x in led),
              [(x.order_name, x.retired) for x in led])

    # ---- re-run: nothing left to cover -------------------------------
    r = cl.post("/api/epcs/retire-sold", json={
        "epcs": heard, "worker": "Nick"})
    check("re-run retires nothing more",
          r.json().get("retire_total", -1) == 0, r.text[:300])

    # ---- undo: unretire hands the ledger unit back --------------------
    r = cl.post("/api/assignments/unretire", json={
        "epcs": [E(1)], "changed_by": "Nick"})
    check("unretire restores the tag", r.status_code == 200
          and r.json().get("restored") == [E(1)], r.text[:200])
    with Session(get_engine()) as s:
        led = s.scalars(select(SoldRecord).where(
            SoldRecord.sku == "SKU-A")).all()
        check("one ledger unit handed back",
              sum((x.quantity - (x.retired or 0)) for x in led) == 1,
              [(x.order_name, x.retired) for x in led])

    # ---- capture path + refusals --------------------------------------
    with Session(get_engine()) as s:
        s.add(EpcCapture(device="C72", epc_count=1, epcs=E(1),
                         note="packed orders"))
        s.commit()
        cid = s.scalars(select(EpcCapture).order_by(
            EpcCapture.id.desc())).first().id
    r = cl.post("/api/epcs/retire-sold", json={
        "capture_id": cid, "worker": "Nick"})
    check("capture path retires the restored tag against the unit",
          r.status_code == 200 and r.json().get("retire_total") == 1,
          r.text[:300])
    r = cl.post("/api/epcs/retire-sold", json={"capture_id": 424242})
    check("unknown capture refused", r.status_code == 404, r.text[:200])

    # ---- capture list pager fields (Nick, 2026-09-14) -----------------
    r = cl.get("/api/epc-captures?limit=1&offset=0").json()
    check("capture list carries total + offset for the pager",
          r.get("total", 0) >= 1 and r.get("offset") == 0
          and len(r.get("captures", [])) == 1, r)
    r = cl.get(f"/api/epc-captures?limit=1&offset={r['total']}").json()
    check("offset past the end returns an empty page",
          r.get("captures") == [] and r.get("total", 0) >= 1, r)
    r = cl.post("/api/epcs/retire-sold", json={"epcs": []})
    check("empty sweep refused", r.status_code == 400, r.text[:200])

    # ==== over-pair guard =============================================
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="SKU-C", barcode="777",
                          product_title="Prod C", bin="G1-1", qty=1,
                          shopify_variant_id="t:3"))
        s.commit()
    body = {"shopify_variant_id": "t:3", "product_title": "Prod C",
            "sku": "SKU-C", "bin_location": "G1-1",
            "assigned_by": "Nick"}
    r = cl.post("/api/rfid-assignments", json={
        "rfid_id": E(20), **body})
    check("first pair up to stock: no warning",
          r.status_code == 201 and "warning" not in r.json(),
          r.text[:200])
    r = cl.post("/api/rfid-assignments", json={
        "rfid_id": E(21), **body})
    d = r.json()
    check("second pair past stock: pair stands WITH a warning",
          r.status_code == 201 and "tag record(s)" in
          d.get("warning", ""), d)
    r = cl.post("/api/rfid-assignments/sweep", json={
        "epcs": [E(22)], **body})
    check("sweep-pair past stock warns too",
          r.status_code == 201 and "warning" in r.json(),
          r.text[:300])

    # ==== cleanup-silent ==============================================
    with Session(get_engine()) as s:
        for i in (30, 31, 32, 33, 34):
            s.add(RfidAssignment(rfid_id=E(i), shopify_variant_id="t:4",
                                 product_title="Prod D", sku="SKU-D",
                                 bin_location="H1-1"))
        s.add(SoldRecord(order_id="o9", order_name="#o9", sku="SKU-D",
                         quantity=2, retired=0,
                         fulfilled_at=datetime.utcnow()))
        s.commit()
    silent = [E(30), E(31), E(32)]
    r = cl.post("/api/assignments/cleanup-silent", json={
        "sku": "SKU-D", "epcs": silent, "worker": "Nick",
        "preview": True})
    d = r.json()
    check("cleanup preview splits oldest-first by coverage",
          d.get("presumed_sold") == [E(30), E(31)]
          and d.get("replaced") == [E(32)], d)
    r = cl.post("/api/assignments/cleanup-silent", json={
        "sku": "SKU-D", "epcs": silent, "worker": "Nick"})
    check("cleanup applied", r.status_code == 200
          and r.json().get("applied") is True, r.text[:300])
    with Session(get_engine()) as s:
        kinds = {t.rfid_id: t.kind for t in s.scalars(
            select(RetiredTag).where(RetiredTag.sku == "SKU-D"))}
        check("kinds: 2 presumed-sold + 1 replaced",
              kinds == {E(30): "presumed-sold", E(31): "presumed-sold",
                        E(32): "replaced"}, kinds)
        led = s.scalars(select(SoldRecord).where(
            SoldRecord.sku == "SKU-D")).all()
        check("cleanup consumed exactly the covered units",
              led[0].retired == 2, led[0].retired)
        live = s.scalars(select(RfidAssignment).where(
            RfidAssignment.sku == "SKU-D")).all()
        check("unswept tags untouched", len(live) == 2, len(live))
    r = cl.post("/api/assignments/cleanup-silent", json={
        "sku": "SKU-A", "epcs": [E(33)], "worker": "Nick"})
    check("mixed-product cleanup refused", r.status_code == 409,
          r.text[:200])

print()
if fails:
    print(f"{len(fails)} FAILURE(S):"); [print("  -", f) for f in fails]
    sys.exit(1)
print("ALL PASS")
