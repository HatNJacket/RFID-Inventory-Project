"""Unlinked-sticker hunt (Nick, 2026-09-09): every sweep upload stashes
the EPCs it heard that belong to NOTHING (no tag record, not retired,
not dismissed, not a companion) - printed-but-never-paired labels
included - onto ONE locate-list entry the C72 hunts by raw EPC. The
entry merges across sweeps, self-prunes as stickers get paired or
retired, and disappears when empty.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_unlinked_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

KNOWN    = "AAAA0000000000000000000A"
RETIRED  = "AAAA0000000000000000000B"
DISMISSED= "AAAA0000000000000000000C"
COMPANION= "AAAA0000000000000000000D"
PRINTED  = "AAAA0000000000000000000E"   # label printed, never paired
MYSTERY  = "AAAA0000000000000000000F"   # nothing anywhere
MYSTERY2 = "AAAA000000000000000000FF"

with patch("app.main.oneleft") as ol:
  with TestClient(app) as cl:
    from sqlalchemy import select
    from sqlalchemy.orm import Session as S
    from app.database import get_engine
    from app.models import (CompanionTag, LabelDismissal, LocateQueueEntry,
                            PrintJob, RetiredTag, RfidAssignment)

    with S(get_engine()) as s:
        s.add(RfidAssignment(rfid_id=KNOWN, shopify_variant_id="t:1",
                             product_title="Known", sku="KNOWN-1",
                             bin_location="A1-1"))
        s.add(RetiredTag(rfid_id=RETIRED, sku="GONE-1", kind="presumed-sold"))
        s.add(LabelDismissal(epc=DISMISSED, dismissed_by="Nick"))
        s.add(CompanionTag(epc=COMPANION, sku="BIG-1", box_no=2,
                           box_count=2))
        s.add(PrintJob(epc=PRINTED, status="done",
                       shopify_variant_id="t:9",
                       product_title="Printed never paired", sku="OWED-1",
                       barcode="999", bin_location="A1-1"))
        s.commit()

    # ---- a sweep hears everything -------------------------------------
    r = cl.post("/api/epc-captures", json={
        "device": "C72-test",
        "epcs": [KNOWN, RETIRED, DISMISSED, COMPANION, PRINTED, MYSTERY],
    })
    d = r.json()
    check("capture accepted", r.status_code == 201, r.text[:200])
    check("exactly the printed-unpaired + mystery EPCs stashed",
          d.get("unlinked_stashed") == 2, d)

    q = cl.get("/api/locate-queue").json()["entries"]
    unl = [e for e in q if e.get("epc_hunt")]
    check("one raw-EPC hunt entry on the locate list", len(unl) == 1, q)
    e = unl[0] if unl else {}
    check("entry carries both unlinked EPCs",
          e.get("tag_count") == 2
          and sorted(e.get("epcs", [])) == sorted([PRINTED, MYSTERY]), e)
    check("entry is labelled, not a bare pseudo-SKU",
          bool(e.get("label")), e)

    # ---- a later sweep merges, known EPCs never join ------------------
    r = cl.post("/api/epc-captures", json={
        "device": "C72-test", "epcs": [MYSTERY, MYSTERY2, KNOWN]})
    check("second sweep stashes only the NEW mystery",
          r.json().get("unlinked_stashed") == 1, r.text[:200])
    q = cl.get("/api/locate-queue").json()["entries"]
    e = next((x for x in q if x.get("epc_hunt")), {})
    check("entry merged to 3", e.get("tag_count") == 3, e)

    # ---- pairing a sticker prunes it off the hunt ---------------------
    with S(get_engine()) as s:
        s.add(RfidAssignment(rfid_id=MYSTERY, shopify_variant_id="t:2",
                             product_title="Now paired", sku="FIXED-1",
                             bin_location="A1-1"))
        s.commit()
    q = cl.get("/api/locate-queue").json()["entries"]
    e = next((x for x in q if x.get("epc_hunt")), {})
    check("paired sticker pruned on listing",
          e.get("tag_count") == 2 and MYSTERY not in e.get("epcs", []), e)

    # ---- retiring + dismissing the rest empties and removes it --------
    with S(get_engine()) as s:
        s.add(RetiredTag(rfid_id=MYSTERY2, kind="dead"))
        s.add(LabelDismissal(epc=PRINTED, dismissed_by="Nick"))
        s.commit()
    q = cl.get("/api/locate-queue").json()["entries"]
    check("emptied entry disappears from the list",
          not any(x.get("epc_hunt") for x in q), q)
    with S(get_engine()) as s:
        left = s.scalars(select(LocateQueueEntry)).all()
        check("emptied entry deleted from the table", left == [], left)

    # ---- a re-heard sweep re-creates it fresh -------------------------
    r = cl.post("/api/epc-captures", json={
        "device": "C72-test", "epcs": ["BBBB0000000000000000000A"]})
    check("fresh mystery re-creates the entry",
          r.json().get("unlinked_stashed") == 1, r.text[:200])

print()
if fails:
    print(f"{len(fails)} FAILURE(S):"); [print("  -", f) for f in fails]
    sys.exit(1)
print("ALL PASS")
