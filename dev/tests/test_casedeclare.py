"""Box of multiple products (Nick, 2026-09-16): declaring an item's
boxes as sealed cases BY HAND - no registered case barcode needed. One
label + one tag per box, each worth N units; already-scanned boxes
convert from loose so nothing double-counts; undo opens them back to
loose scans. Refused for bundles, mismatched sizes, and once labels
are queued."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_casedeclare_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_engine
from app.models import Batch, BatchItem, BinMapEntry, CaseCode, PrintJob
from sqlalchemy.orm import Session
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.main._maybe_refresh_bin_map", return_value=False):
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        b = Batch(bin_name="D1-1", status="collecting", created_by="Nick")
        s.add(b)
        s.flush()
        a = BatchItem(batch_id=b.id, scanned_code="810", resolved=True,
                      sku="CASE-1", barcode="810",
                      product_title="Boxed Product",
                      shopify_variant_id="t:C1", qty_scanned=3,
                      bin_location="D1-1")
        bun = BatchItem(batch_id=b.id, scanned_code="811", resolved=True,
                        sku="BUN-1", barcode="811",
                        product_title="A Bundle", kind="bundle",
                        shopify_variant_id="t:B1", qty_scanned=1,
                        bin_location="D1-1")
        s.add_all([a, bun])
        s.commit()
        bid, aid, bunid = b.id, a.id, bun.id

    # ---- declare: 2 sealed boxes of 6, both already scanned loose ------
    r = cl.post(f"/api/batches/{bid}/items/{aid}/case",
                json={"units": 6, "boxes": 2, "changed_by": "C72"})
    body = r.json()
    it = body.get("item") or {}
    check("declared 2 cases of 6, converting 2 loose scans",
          r.status_code == 200 and body["converted"] == 2
          and it["case_count"] == 2 and it["case_units"] == 6
          and it["qty_scanned"] == 1, r.text[:300])
    check("message spells out the label",
          '"6 x CASE-1"' in body["message"], body.get("message"))

    # ---- one row, one case size ----------------------------------------
    r = cl.post(f"/api/batches/{bid}/items/{aid}/case",
                json={"units": 8, "boxes": 1})
    check("mismatched case size refused 409",
          r.status_code == 409 and "one case size" in r.json()["detail"],
          r.text[:250])

    # ---- add one more identical box ------------------------------------
    r = cl.post(f"/api/batches/{bid}/items/{aid}/case",
                json={"units": 6, "boxes": 1})
    it = r.json().get("item") or {}
    check("adding one more box converts the last loose scan",
          r.status_code == 200 and it["case_count"] == 3
          and it["qty_scanned"] == 0, r.text[:250])

    # ---- undo opens them back into loose scans -------------------------
    r = cl.post(f"/api/batches/{bid}/items/{aid}/case",
                json={"undo": True})
    it = r.json().get("item") or {}
    check("undo restores 3 loose scans, cases cleared",
          r.status_code == 200 and it["qty_scanned"] == 3
          and it["case_count"] == 0 and it["case_units"] is None,
          r.text[:250])

    # ---- unresolved rows: say what's INSIDE, code becomes a case -------
    with Session(get_engine()) as s:
        u = BatchItem(batch_id=bid, scanned_code="UNK-99",
                      resolved=False,
                      product_title="Unresolved: UNK-99",
                      qty_scanned=2)
        s.add(u)
        s.add(BinMapEntry(sku="INNER-1", barcode="850", bin="D1-1",
                          product_title="Inner Product", qty=10,
                          shopify_variant_id="t:IN"))
        s.commit()
        uid = u.id
    r = cl.post(f"/api/batches/{bid}/items/{uid}/case",
                json={"units": 12, "boxes": 2})
    check("unresolved without contains asks what's inside",
          r.status_code == 422
          and "inside" in r.json()["detail"], r.text[:250])
    r = cl.post(f"/api/batches/{bid}/items/{uid}/case",
                json={"units": 12, "boxes": 2, "contains": "850"})
    body = r.json()
    it = body.get("item") or {}
    check("contains resolves the row and declares the cases",
          r.status_code == 200 and it["resolved"] is True
          and it["sku"] == "INNER-1" and it["case_count"] == 2
          and it["case_units"] == 12 and it["qty_scanned"] == 0
          and body["converted"] == 2, r.text[:350])
    check("message says the code is now a case barcode",
          "case barcode" in body["message"], body.get("message"))
    with Session(get_engine()) as s:
        cc = s.get(CaseCode, "UNK-99")
        check("CaseCode registered: UNK-99 = 12 x INNER-1",
              cc is not None and cc.sku == "INNER-1"
              and cc.units == 12, cc)
    # Same batch already answered SEALED (the declare), so a re-scan
    # of the code just adds one more box - no dialog (Nick, 2026-09-16).
    r = cl.post(f"/api/batches/{bid}/scan", json={"code": "UNK-99"})
    d = r.json()
    check("re-scan in the same batch auto-adds a sealed box",
          r.status_code == 201 and d.get("case_auto") is True
          and d.get("needs_case_decision") is None
          and d["item"]["case_count"] == 3, r.text[:300])

    # A FRESH batch asks once, then repeats on its own.
    with Session(get_engine()) as s:
        b3 = Batch(bin_name="D3-3", status="collecting",
                   created_by="Nick")
        s.add(b3)
        s.commit()
        b3id = b3.id
    r = cl.post(f"/api/batches/{b3id}/scan", json={"code": "UNK-99"})
    check("first scan in a new batch still asks opened/sealed",
          r.status_code == 201
          and r.json().get("needs_case_decision") is True,
          r.text[:250])
    r = cl.post(f"/api/batches/{b3id}/scan",
                json={"code": "UNK-99", "case_action": "sealed"})
    check("answering sealed counts the first box",
          r.status_code == 201
          and r.json()["item"]["case_count"] == 1, r.text[:250])
    r = cl.post(f"/api/batches/{b3id}/scan", json={"code": "UNK-99"})
    d = r.json()
    check("second scan repeats the answer - one more box, no dialog",
          r.status_code == 201 and d.get("case_auto") is True
          and d["item"]["case_count"] == 2
          and d["item"]["units_total"] == 24, r.text[:300])

    # ---- guards ---------------------------------------------------------
    r = cl.post(f"/api/batches/{bid}/items/{bunid}/case",
                json={"units": 4})
    check("bundles are refused",
          r.status_code == 422 and "bundle" in r.json()["detail"].lower(),
          r.text[:200])
    r = cl.post(f"/api/batches/{bid}/items/{aid}/case", json={})
    check("missing units asks for it", r.status_code == 422, r.text[:200])
    r = cl.post(f"/api/batches/{bid}/items/{aid}/case", json={"units": 1})
    check("a case of 1 is refused by validation", r.status_code == 422,
          r.text[:200])

    # ---- labels: one per loose box + one per sealed case ---------------
    r = cl.post(f"/api/batches/{bid}/items/{aid}/case",
                json={"units": 4, "boxes": 1})
    check("re-declared 1 case of 4", r.status_code == 200, r.text[:200])
    r = cl.post(f"/api/batches/{bid}/queue-labels",
                json={"requested_by": "Nick"})
    body = r.json()
    # 2 loose + 1 case of CASE-1, plus the 3 INNER-1 cases (2 declared
    # + 1 auto-added by the same-batch re-scan above).
    check("strip holds every loose box and sealed case",
          r.status_code == 201 and body["count"] == 6, r.text[:250])
    with Session(get_engine()) as s:
        jobs = s.query(PrintJob).filter(PrintJob.batch_id == bid).all()
        c1 = [j for j in jobs if j.case_units and j.sku == "CASE-1"]
        c2 = [j for j in jobs if j.case_units and j.sku == "INNER-1"]
    check("case labels carry their unit counts",
          len(c1) == 1 and c1[0].case_units == 4
          and len(c2) == 3 and all(j.case_units == 12 for j in c2),
          f"CASE-1:{c1} INNER-1:{c2}")

    # ---- printed labels lock the split ---------------------------------
    r = cl.post(f"/api/batches/{bid}/items/{aid}/case",
                json={"units": 4, "boxes": 1})
    check("after labels queue, the split is locked 409",
          r.status_code == 409 and "desync" in r.json()["detail"],
          r.text[:250])

print()
print(f"{'FAIL' if fails else 'OK'}  {len(fails)} failing")
sys.exit(1 if fails else 0)
