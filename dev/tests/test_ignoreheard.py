"""Sweep write-off of unpaired stickers (Nick, 2026-09-14): the blank
roll / broken / test labels by the desk answer every sweep and drown
the unpaired locate list. /api/epcs/ignore-heard dismisses every
OWNERLESS EPC in a sweep (raw list from the C72's WRITE OFF button, or
a sent capture picked on the web) - owned tags are untouched, printed
receiving labels are counted and named, the locate entry prunes
instantly, and one History event undoes the whole batch. Also covers
bin_check's new capture_id shorthand (no EPC re-upload per bin step).
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_ignoreheard_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

OWNED   = "CCCC0000000000000000000A"
BLANK1  = "CCCC0000000000000000000B"
BLANK2  = "CCCC0000000000000000000C"
PRINTED = "CCCC0000000000000000000D"   # a real printed receiving label
KEEP    = "CCCC0000000000000000000E"   # unpaired but NOT in the junk sweep

with patch("app.main.oneleft") as ol:
  with TestClient(app) as cl:
    from sqlalchemy import select
    from sqlalchemy.orm import Session as S
    from app.database import get_engine
    from app.models import (BarcodeChange, LabelDismissal,
                            LocateQueueEntry, PrintJob, RfidAssignment)

    with S(get_engine()) as s:
        s.add(RfidAssignment(rfid_id=OWNED, shopify_variant_id="t:1",
                             product_title="Owned", sku="OWN-1",
                             bin_location="A1-1"))
        s.add(PrintJob(epc=PRINTED, status="done",
                       shopify_variant_id="t:9",
                       product_title="Printed never paired", sku="OWED-1",
                       barcode="999", bin_location="A1-1"))
        s.commit()

    # A warehouse sweep already stashed all the strays on the hunt list.
    r = cl.post("/api/epc-captures", json={
        "device": "C72-test",
        "epcs": [OWNED, BLANK1, BLANK2, PRINTED, KEEP],
    })
    check("seed capture accepted", r.status_code == 201, r.text[:200])
    check("4 strays stashed on the hunt",
          r.json().get("unlinked_stashed") == 4, r.json())

    # ---- the desk-pile sweep, raw list (the C72's WRITE OFF) ----------
    r = cl.post("/api/epcs/ignore-heard", json={
        "epcs": [OWNED, BLANK1, BLANK2, PRINTED],
        "dismissed_by": "Nick",
    })
    d = r.json()
    check("write-off answered", r.status_code == 200, r.text[:300])
    check("only the 3 ownerless EPCs written off", d.get("ignored") == 3, d)
    check("the owned tag was untouched",
          d.get("heard") == 4 and d.get("ignored") == 3, d)
    check("the printed receiving label is counted and named",
          d.get("printed_labels") == 1 and "printed" in d["message"], d)
    marker = d.get("marker")
    check("write-off carries an undo marker", bool(marker), d)

    with S(get_engine()) as s:
        dis = s.scalars(select(LabelDismissal).where(
            LabelDismissal.dismissed_by == marker)).all()
        check("3 dismissal rows under the marker", len(dis) == 3,
              [x.epc for x in dis])
        owned_dis = s.scalars(select(LabelDismissal).where(
            LabelDismissal.epc == OWNED)).all()
        check("no dismissal row for the owned tag", owned_dis == [], "")
        ev = s.scalars(select(BarcodeChange).where(
            BarcodeChange.changed_field == "unpaired-ignored")).all()
        check("one History event for the write-off", len(ev) == 1
              and ev[0].new_barcode == marker[:64], ev)

    # ---- the hunt entry pruned instantly, KEEP survives ---------------
    q = cl.get("/api/locate-queue").json()["entries"]
    e = next((x for x in q if x.get("epc_hunt")), {})
    check("hunt entry keeps only the unswept stray",
          e.get("tag_count") == 1 and e.get("epcs") == [KEEP], e)

    # ---- written-off stickers never re-stash --------------------------
    r = cl.post("/api/epc-captures", json={
        "device": "C72-test", "epcs": [BLANK1, BLANK2, PRINTED]})
    check("a later sweep re-stashes none of them",
          r.json().get("unlinked_stashed", 0) == 0, r.json())

    # ---- capture-id path (web: pick a sent sweep) ---------------------
    cap_id = r.json().get("id") or r.json().get("capture", {}).get("id")
    if not cap_id:
        with S(get_engine()) as s:
            from app.models import EpcCapture
            cap_id = s.scalars(select(EpcCapture).order_by(
                EpcCapture.id.desc())).first().id
    r = cl.post("/api/epcs/ignore-heard", json={
        "capture_id": cap_id, "dismissed_by": "Nick"})
    check("a fully-written-off capture reports nothing to do",
          r.status_code == 200 and r.json().get("ignored") == 0,
          r.text[:200])
    r = cl.post("/api/epcs/ignore-heard", json={
        "capture_id": 999999, "dismissed_by": "Nick"})
    check("unknown capture id refused", r.status_code == 404, r.text[:200])

    # ---- undo restores the whole batch as a unit ----------------------
    r = cl.post("/api/epcs/ignore-heard/undo", json={
        "marker": marker, "worker": "Nick"})
    check("undo answered", r.status_code == 200, r.text[:200])
    check("undo restored all 3", r.json().get("restored") == 3, r.json())
    with S(get_engine()) as s:
        left = s.scalars(select(LabelDismissal).where(
            LabelDismissal.dismissed_by == marker)).all()
        check("marker dismissals gone", left == [], left)
    r = cl.post("/api/epcs/ignore-heard/undo", json={
        "marker": marker, "worker": "Nick"})
    check("second undo finds nothing", r.status_code == 404, r.text[:200])
    r = cl.post("/api/epc-captures", json={
        "device": "C72-test", "epcs": [BLANK1]})
    check("after undo the sticker re-stashes on the next sweep",
          r.json().get("unlinked_stashed") == 1, r.json())

    # ---- bin_check by capture id (no EPC re-upload) -------------------
    with S(get_engine()) as s:
        from app.models import BinMapEntry, EpcCapture
        s.add(BinMapEntry(sku="OWN-1", barcode="111",
                          product_title="Owned", bin="A1-1", qty=1,
                          shopify_variant_id="t:1"))
        s.add(EpcCapture(device="C72-test", epc_count=1, epcs=OWNED))
        s.commit()
        cid = s.scalars(select(EpcCapture).order_by(
            EpcCapture.id.desc())).first().id
    r = cl.post("/api/bins/A1-1/check", json={"capture_id": cid})
    d = r.json()
    check("bin check by capture id answered", r.status_code == 200,
          r.text[:300])
    row = next((x for x in d.get("items", [])
                if (x.get("sku") or "").upper() == "OWN-1"), {})
    check("the named capture's EPCs were the sweep",
          d.get("swept") == 1 and row.get("detected") == 1, d)
    r = cl.post("/api/bins/A1-1/check", json={"capture_id": 999999})
    check("bin check with unknown capture id refused",
          r.status_code == 404, r.text[:200])

print()
if fails:
    print(f"{len(fails)} FAILURE(S):"); [print("  -", f) for f in fails]
    sys.exit(1)
print("ALL PASS")
