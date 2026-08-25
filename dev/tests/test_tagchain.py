"""History's Assigned Tag undo chain (Nick, 2026-08-25): Assigned Tag
events (single and grouped) offer Undo, which RELEASES the tags while
keeping a full snapshot; the release is logged (tag-released) and offers
its own Undo, which RE-APPLIES the tags exactly - original pairing date
included - logged again (tag-reapplied) with Undo. The loop is endless
by design and entirely manual."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_tagchain_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_engine
from app.models import ReleasedTag, RfidAssignment
from sqlalchemy.orm import Session
from sqlalchemy import select
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

EPCS = ["AA00000000000000000000%02d" % i for i in range(1, 4)]

def hist(cl, typ, sku=None):
    evs = cl.get("/api/history?limit=200").json()["events"]
    return [e for e in evs if e["type"] == typ
            and (sku is None or e.get("sku") == sku)]

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.main._kick_orders_sync_soon"):
  with TestClient(app) as cl:
    # A sweep pairs three tags to ALPHA-1 (one shared timestamp).
    r = cl.post("/api/rfid-assignments/sweep", json={
        "epcs": EPCS, "shopify_variant_id": "t:ALPHA",
        "product_title": "Alpha Adapter", "sku": "ALPHA-1",
        "barcode": "701", "bin_location": "P1-1", "assigned_by": "Nick"})
    check("sweep pairs three tags", r.json()["count"] == 3, r.text[:200])
    with Session(get_engine()) as s:
        original_at = s.scalar(select(RfidAssignment.assigned_at).where(
            RfidAssignment.rfid_id == EPCS[0]))

    # 1) The grouped Assigned Tag event carries the release undo.
    ev = hist(cl, "tag-assigned", "ALPHA-1")
    check("grouped Assigned Tag offers Undo (release)",
          ev and ev[0].get("undo", {}).get("kind") == "tag-assign"
          and sorted(ev[0]["undo"]["epcs"]) == sorted(EPCS), ev)

    # 2) Release: rows leave the active table, full snapshots kept.
    r = cl.post("/api/tags/release", json={
        "epcs": EPCS, "sku": "ALPHA-1", "by": "Nick"})
    check("release removes all three", r.json()["count"] == 3, r.text[:200])
    with Session(get_engine()) as s:
        live = s.scalars(select(RfidAssignment).where(
            RfidAssignment.rfid_id.in_(EPCS))).all()
        snaps = s.scalars(select(ReleasedTag).where(
            ReleasedTag.rfid_id.in_(EPCS))).all()
    check("released tags are no longer assigned", not live, live)
    check("full snapshots wait in the released table",
          len(snaps) == 3 and all(x.sku == "ALPHA-1" for x in snaps)
          and all(x.assigned_at is not None for x in snaps), snaps)

    # 3) The release is logged as ONE grouped event with its own undo.
    ev = hist(cl, "tag-released", "ALPHA-1")
    check("release logs a grouped Released Tag event",
          len(ev) == 1 and len(ev[0].get("epcs", [])) == 3, ev)
    check("Released Tag offers Undo (re-apply)",
          ev[0].get("undo", {}).get("kind") == "tag-release", ev[0])
    check("the Assigned Tag event is gone while released",
          not hist(cl, "tag-assigned", "ALPHA-1"), "still listed")

    # 4) Re-apply: assignments come back exactly, original date included.
    r = cl.post("/api/tags/reapply", json={
        "epcs": EPCS, "sku": "ALPHA-1", "by": "Steve"})
    check("re-apply restores all three", r.json()["count"] == 3,
          r.text[:200])
    with Session(get_engine()) as s:
        back = s.scalar(select(RfidAssignment).where(
            RfidAssignment.rfid_id == EPCS[0]))
        check("the original pairing date and operator survive",
              back is not None and back.assigned_at == original_at
              and back.assigned_by == "Nick" and back.barcode == "701",
              back and back.as_dict())
        check("the snapshots are consumed",
              not s.scalars(select(ReleasedTag).where(
                  ReleasedTag.rfid_id.in_(EPCS))).all(), "rows remain")

    # 5) The re-apply is logged and undoable; the old release event stops
    # offering its undo (nothing is waiting any more).
    ev = hist(cl, "tag-reapplied", "ALPHA-1")
    check("re-apply logs a grouped Re-applied Tag event with Undo",
          len(ev) == 1 and ev[0].get("undo", {}).get("kind") == "tag-assign",
          ev)
    ev = hist(cl, "tag-released", "ALPHA-1")
    check("a consumed release stops offering Undo",
          ev and "undo" not in ev[0], ev)
    ev = hist(cl, "tag-assigned", "ALPHA-1")
    check("the Assigned Tag event is back with Undo",
          ev and ev[0].get("undo", {}).get("kind") == "tag-assign", ev)

    # 6) Around again - the loop is endless, and that's fine.
    r = cl.post("/api/tags/release", json={
        "epcs": EPCS, "sku": "ALPHA-1", "by": "Nick"})
    check("the chain loops (second release works)",
          r.json()["count"] == 3, r.text[:200])

    # 7) Guards: a release scoped to the wrong product touches nothing.
    cl.post("/api/rfid-assignments/sweep", json={
        "epcs": ["BB00000000000000000000FF"],
        "shopify_variant_id": "t:BRAVO", "product_title": "Bravo Barlow",
        "sku": "BRAVO-1", "assigned_by": "Nick"})
    r = cl.post("/api/tags/release", json={
        "epcs": ["BB00000000000000000000FF"], "sku": "ALPHA-1"})
    check("the SKU guard refuses another product's tag",
          r.status_code == 422, r.text[:200])

    # 8) A tag claimed by something else while released is never stolen
    # back.
    with Session(get_engine()) as s:
        s.add(RfidAssignment(
            rfid_id=EPCS[0], shopify_variant_id="t:BRAVO",
            product_title="Bravo Barlow", sku="BRAVO-1"))
        s.commit()
    r = cl.post("/api/tags/reapply", json={"epcs": [EPCS[0]]})
    check("re-apply never steals a re-claimed tag",
          r.status_code == 422, r.text[:200])
    r = cl.post("/api/tags/reapply", json={"epcs": EPCS[1:]})
    check("the unclaimed tags still re-apply", r.json()["count"] == 2,
          r.text[:200])

    # 9) A single-tag Assigned Tag event offers the same undo.
    ev = hist(cl, "tag-assigned", "BRAVO-1")
    single = [e for e in ev if "BB00000000000000000000FF"
              in (e.get("undo", {}).get("epcs") or [])]
    check("single Assigned Tag events offer Undo too", bool(single), ev)

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
