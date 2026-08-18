"""Duplicate products: detected once per sync run (tagged SKUs only,
never per scan), merged with a chosen SKU + name, inventory check filed.
Plus: bin-updated History rows now carry an undo payload."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ["SHOPIFY_WRITE_MODE"]="scan_station_only,verify_onhand"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_dupes_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.main import app
from app.database import get_engine
from app.models import (BarcodeChange, BinMapEntry, ReviewTask,
                        RfidAssignment)
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

GOOD = "ZWO ASIAIR MountingBrackets"
TYPO = "ZWO AISAIR MountingBrackets"

def tag(s, epc, sku, title, barcode=None):
    s.add(RfidAssignment(rfid_id=epc, shopify_variant_id="t:1",
                         product_title=title, sku=sku, barcode=barcode,
                         bin_location="B1-1"))

with patch("app.shopify.get_fulfilled_orders",
           side_effect=RuntimeError("ACCESS_DENIED")), \
     patch("app.shopify.get_on_hand_by_skus", return_value={}):
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        # The real ZWO case: same saved barcode under two SKUs.
        for e in ("A1","A2","A3"):
            tag(s, e, GOOD, "ZWO ASIAIR Mounting Bracket", "697")
        for e in ("B1","B2"): tag(s, e, TYPO, "AISAIR bracket", "697")
        tag(s, "C1", "CAM-100", "Unrelated Camera", "555")
        # One character apart WITHOUT shared evidence: legitimate
        # neighbours, must NOT be flagged (the old fuzzy rule drowned
        # Review in these).
        tag(s, "N1", "SV-105", "Svbony camera", "701")
        tag(s, "N2", "SV-106", "Svbony guide scope", "702")
        # Open-box twin shares the barcode on purpose: ignored.
        tag(s, "O1", "CAM-100-OB", "Unrelated Camera OPEN BOX", "555")
        s.add(BinMapEntry(sku=GOOD, barcode="697", product_title="ZWO ASIAIR Mounting Bracket",
                          bin="B1-1", qty=5, shopify_product_id="gid://shopify/Product/9",
                          shopify_variant_id="gid://shopify/PV/9"))
        s.commit()

    # The dup check rides the sync run and must work WITHOUT read_orders.
    r = cl.post("/api/orders-sync/run").json()
    check("dup check runs even while the scope is missing",
          r.get("dupes_opened")==1 and r.get("waiting_scope") is True, r)
    tasks = cl.get("/api/review-tasks?status=open").json()["tasks"]
    dup = [t for t in tasks if t["category"]=="duplicate-product"]
    check("one duplicate task filed for the shared-barcode pair",
          len(dup)==1 and "barcode 697" in dup[0]["detail"], dup)
    check("one-character-apart SKUs NOT flagged",
          "SV-105" not in dup[0]["detail"], dup[0]["detail"])
    check("open-box twin ignored despite the shared barcode",
          all("CAM-100" not in t["detail"] for t in dup), dup)

    # Idempotent: a second run files nothing new.
    r = cl.post("/api/orders-sync/run").json()
    check("re-run files no second task", r.get("dupes_opened")==0, r)

    # Context: both sides with units, names, catalog standing.
    ctx = cl.get(f"/api/review-tasks/{dup[0]['id']}/context").json()
    sides = {x["sku"]: x for x in ctx.get("sides", [])}
    check("context carries both sides",
          set(sides)=={GOOD, TYPO}, ctx)
    check("units + catalog standing per side",
          sides[GOOD]["units"]==3 and sides[GOOD]["in_catalog"] is True
          and sides[TYPO]["units"]==2 and sides[TYPO]["in_catalog"] is False,
          sides)

    # Merge: typo -> good, keeping the catalog name.
    r = cl.post("/api/products/merge",
                json={"from_sku": TYPO, "into_sku": GOOD,
                      "title": "ZWO ASIAIR Mounting Bracket",
                      "changed_by": "Nick"})
    d = r.json()
    check("merge moves the tags", r.status_code==200 and d["moved_tags"]==2, d)
    with Session(get_engine()) as s:
        good_tags = s.scalars(select(RfidAssignment).where(
            RfidAssignment.sku==GOOD)).all()
        typo_tags = s.scalars(select(RfidAssignment).where(
            RfidAssignment.sku==TYPO)).all()
        check("all five tags under the survivor",
              len(good_tags)==5 and not typo_tags,
              (len(good_tags), len(typo_tags)))
        check("every tag wears the chosen name + catalog identity",
              all(t.product_title=="ZWO ASIAIR Mounting Bracket"
                  and t.shopify_product_id=="gid://shopify/Product/9"
                  for t in good_tags), [t.product_title for t in good_tags])
        merged_ev = s.scalars(select(BarcodeChange).where(
            BarcodeChange.changed_field=="product-merged")).all()
        check("History records the merge", len(merged_ev)==1
              and merged_ev[0].changed_by=="Nick", merged_ev)
        dup_task = s.get(ReviewTask, dup[0]["id"])
        check("duplicate task closed by the merge",
              dup_task.status=="resolved", dup_task.status)
        inv = s.scalars(select(ReviewTask).where(
            ReviewTask.category=="inventory-check",
            ReviewTask.status=="open")).all()
        check("inventory check filed for the merged product",
              len(inv)==1 and inv[0].sku==GOOD and "Merged duplicate"
              in inv[0].detail, [(t.sku, t.detail[:40]) for t in inv])

    # The pair never comes back (task exists, resolved).
    r = cl.post("/api/orders-sync/run").json()
    check("merged pair is not re-flagged", r.get("dupes_opened")==0, r)

    # Dismissed pairs stay dismissed.
    with Session(get_engine()) as s:
        tag(s, "D1", "FILTER-X1", "Filter A"); tag(s, "D2", "FILTERX1", "Filter B")
        s.commit()
    r = cl.post("/api/orders-sync/run").json()
    check("normalized-equal SKUs flagged", r.get("dupes_opened")==1, r)
    t2 = [t for t in cl.get("/api/review-tasks?status=open").json()["tasks"]
          if t["category"]=="duplicate-product"][0]
    cl.post(f"/api/review-tasks/{t2['id']}/resolve",
            json={"resolved_by":"Nick","dismissed":True})
    r = cl.post("/api/orders-sync/run").json()
    check("dismissed pair never re-flagged", r.get("dupes_opened")==0, r)

    # OPEN tasks from the fuzzy era close themselves on the next run —
    # in BOTH stored formats: the original "⇄" and the "?" SQL Server's
    # VARCHAR mangled it into (the prod 8k-ghost-task bug).
    with Session(get_engine()) as s:
        s.add(ReviewTask(category="duplicate-product", sku="SV-105",
              detail="Possible duplicate products: SV-105 ⇄ SV-106 — "
                     "old fuzzy flag.", created_by="dupe-check"))
        s.add(ReviewTask(category="duplicate-product", sku="SV-205",
              detail="Possible duplicate products: SV-205 ? SV-206 — "
                     "old mangled flag.", created_by="dupe-check"))
        s.commit()
    r = cl.post("/api/orders-sync/run").json()
    check("stale fuzzy-era tasks auto-close (both stored formats)",
          r.get("dupes_closed")==2, r)

    # A dismissed pair in the OLD mangled format still blocks re-filing.
    with Session(get_engine()) as s:
        tag(s, "P1", "PAIR-A", "Pair A", "909"); tag(s, "P2", "PAIR-B", "Pair B", "909")
        s.add(ReviewTask(category="duplicate-product", sku="PAIR-A",
              status="resolved",
              detail="Possible duplicate products: PAIR-A ? PAIR-B — "
                     "old mangled, dismissed.", created_by="dupe-check"))
        s.commit()
    r = cl.post("/api/orders-sync/run").json()
    check("old-format dismissal still blocks re-filing",
          r.get("dupes_opened")==0, r)

    # ---- SPLIT: they really are two products -------------------------
    with Session(get_engine()) as s:
        tag(s, "S1", "SPLIT-A", "Widget A", "303")
        tag(s, "S2", "SPLITA", "Widget B", "303")
        s.commit()
    r = cl.post("/api/orders-sync/run").json()
    check("split candidates flagged", r.get("dupes_opened")==1, r)

    # Still-colliding identities are refused.
    r = cl.post("/api/products/split", json={"sides":[
        {"sku":"SPLIT-A","new_sku":"SPLIT-A","new_barcode":None},
        {"sku":"SPLITA","new_sku":"SPLIT.A","new_barcode":None}],
        "changed_by":"Nick"})
    check("same-normalized SKUs refused", r.status_code==422, r.text)
    r = cl.post("/api/products/split", json={"sides":[
        {"sku":"SPLIT-A","new_sku":"SPLIT-A","new_barcode":"WIDGETB"},
        {"sku":"SPLITA","new_sku":"WIDGET-B","new_barcode":None}],
        "changed_by":"Nick"})
    check("barcode crossing the other SKU refused", r.status_code==422, r.text)

    # The Svbony convention: each barcode = its OWN new SKU is fine.
    r = cl.post("/api/products/split", json={"sides":[
        {"sku":"SPLIT-A","new_sku":"SPLIT-A","new_barcode":"SPLIT-A"},
        {"sku":"SPLITA","new_sku":"WIDGET-B","new_barcode":"WIDGET-B"}],
        "changed_by":"Nick"})
    d = r.json()
    check("split accepted with own-SKU barcodes",
          r.status_code==200 and d["tasks_closed"]==1, r.text[:200])
    with Session(get_engine()) as s:
        a = s.scalars(select(RfidAssignment).where(
            RfidAssignment.rfid_id=="S1")).first()
        b = s.scalars(select(RfidAssignment).where(
            RfidAssignment.rfid_id=="S2")).first()
        check("both sides re-identified locally",
              a.sku=="SPLIT-A" and a.barcode=="SPLIT-A"
              and b.sku=="WIDGET-B" and b.barcode=="WIDGET-B",
              (a.sku, a.barcode, b.sku, b.barcode))
    r = cl.post("/api/orders-sync/run").json()
    check("split pair never re-flagged", r.get("dupes_opened")==0, r)

    # ---- barcode = the product's OWN SKU (the Svbony overwrite) ------
    PROD = {"shopify_variant_id":"gid://shopify/PV/77",
            "shopify_product_id":"gid://shopify/Product/77",
            "product_title":"Svbony SV223","sku":"W9180A","barcode":"OLD-1",
            "bin_location":"C1-1"}
    with patch("app.main._lookup_api", return_value=dict(PROD)), \
         patch("app.main._resolve", return_value=dict(PROD)), \
         patch("app.shopify.update_variant_barcode", return_value={}):
        r = cl.post("/api/barcode-overwrites", json={
            "target":"W9180A","new_barcode":"W9180A",
            "confirmed":True,"changed_by":"Nick"})
        check("own-SKU barcode overwrite accepted",
              r.status_code==201, r.text[:200])
    OTHER = dict(PROD, shopify_variant_id="gid://shopify/PV/88", sku="ZZZ-9")
    with patch("app.main._lookup_api", return_value=dict(PROD)), \
         patch("app.main._resolve", return_value=OTHER), \
         patch("app.shopify.update_variant_barcode", return_value={}):
        r = cl.post("/api/barcode-overwrites", json={
            "target":"W9180A","new_barcode":"ZZZ-9",
            "confirmed":True,"changed_by":"Nick"})
        check("another product's code still refused",
              r.status_code==409, r.status_code)

    # Bin-updated History rows carry the undo payload.
    with Session(get_engine()) as s:
        s.add(BarcodeChange(sku=GOOD, changed_field="bin",
                            old_barcode="B1-1", new_barcode="C2-2",
                            changed_by="Steve"))
        s.commit()
    ev = [e for e in cl.get("/api/history").json()["events"]
          if e["type"]=="bin-updated"]
    check("bin update offers undo",
          ev and ev[0].get("undo", {}).get("kind")=="bin"
          and ev[0]["undo"]["old_bin"]=="B1-1"
          and ev[0]["undo"]["new_bin"]=="C2-2", ev[:1])

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
