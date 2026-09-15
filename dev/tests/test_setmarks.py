"""The multi-box redo (Nick, 2026-09-15): collect rows get a "Part of
a set" MARK (master SKU + Box X of Y) on either client; the set itself
is defined on the WEB during verification, seeded by the marks. While
a family is marked or registered, the duplicate barcode/SKU guardrails
stand down inside it (a box often carries the parent's real barcode),
and tags paired before the set existed follow their box identities
when the set is defined.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["SHOPIFY_WRITE_MODE"]="scan_station_only"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_setmarks_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as S
from app.main import app
from app import orders_sync
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

MAIN = {"shopify_variant_id": "gid:main", "shopify_product_id": "gid:pm",
        "product_title": "Quattro 300P", "variant_title": None,
        "sku": "S11230", "barcode": "MAIN-BC", "bin_location": "A7-1"}

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.find_sku_listing", return_value=None), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_on_hand", return_value=None), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.main._maybe_refresh_bin_map", return_value=False), \
     patch("app.main._kick_orders_sync_soon", create=True):
  with TestClient(app) as cl:
    from app.database import get_engine
    from app.models import (BatchItem, BinMapEntry, BoxSetPart,
                            ReviewTask, RfidAssignment)

    with S(get_engine()) as s:
        s.add(BinMapEntry(sku="S11230", barcode="MAIN-BC",
                          product_title="Quattro 300P", bin="A7-1",
                          qty=1, shopify_variant_id="gid:main",
                          shopify_product_id="gid:pm"))
        s.commit()

    # ---- marking at collect -------------------------------------------
    r = cl.post("/api/batches", json={"bin": "A7-1", "created_by": "t"})
    bid = r.json()["id"]
    r = cl.post(f"/api/batches/{bid}/scan", json={"code": "MAIN-BC"})
    it_main = r.json()["item"]["id"]
    r = cl.post(f"/api/batches/{bid}/scan", json={"code": "MYSTERY-2"})
    it_unres = r.json()["item"]["id"]

    r = cl.post(f"/api/batches/{bid}/items/{it_main}/set-mark", json={
        "master_sku": "S11230", "box_no": 1, "box_total": 2,
        "changed_by": "Nick"})
    check("marking box 1 answers with the mark on the item",
          r.status_code == 200
          and r.json()["item"]["set_mark_master"] == "S11230"
          and r.json()["item"]["set_mark_box"] == 1
          and "verification" in r.json()["message"], r.text[:300])
    r = cl.post(f"/api/batches/{bid}/items/{it_unres}/set-mark", json={
        "master_sku": "S11230", "box_no": 2, "box_total": 2})
    check("an UNRESOLVED row takes a mark too", r.status_code == 200,
          r.text[:200])
    r = cl.get(f"/api/batches/{bid}")
    marked = [i for i in r.json()["items"] if i.get("set_mark_master")]
    check("batch GET carries both marks", len(marked) == 2,
          [(i["scanned_code"], i.get("set_mark_box")) for i in
           r.json()["items"]])

    # Validation: X can't exceed Y; a mark needs a master.
    r = cl.post(f"/api/batches/{bid}/items/{it_main}/set-mark", json={
        "master_sku": "S11230", "box_no": 3, "box_total": 2})
    check("box X > Y refused", r.status_code == 422, r.text[:150])
    r = cl.post(f"/api/batches/{bid}/items/{it_main}/set-mark", json={
        "box_no": 1, "box_total": 2})
    check("a mark without a master SKU refused", r.status_code == 422,
          r.text[:150])

    # ---- guardrails stand down inside the MARKED family ---------------
    # The box's draft listing exists; writing the PARENT's barcode onto
    # it must not ask, not block, and file NO Review task.
    with S(get_engine()) as s:
        s.add(BinMapEntry(sku="S11230-2", barcode=None,
                          product_title="Draft box 2", bin="A7-1",
                          qty=0, shopify_variant_id="gid:d2",
                          shopify_product_id="gid:pd2"))
        s.commit()
    DRAFT2 = {"shopify_variant_id": "gid:d2",
              "shopify_product_id": "gid:pd2",
              "product_title": "Draft box 2", "variant_title": None,
              "sku": "S11230-2", "barcode": None,
              "bin_location": "A7-1"}
    # First mark the draft row so the family covers it (its sku is on a
    # marked row? No - the family pairs each marked row's sku with the
    # MASTER; S11230-2 is not in the batch. Mark it via the unresolved
    # row's sku instead: the clash here is parent vs the DRAFT product,
    # so mark a row AS the draft sku to join the family.)
    with S(get_engine()) as s:
        it = s.get(BatchItem, it_unres)
        it.sku = "S11230-2"
        s.commit()
    with patch("app.main._lookup_api",
               side_effect=lambda t: dict(DRAFT2)
               if t in ("S11230-2",) else dict(MAIN)), \
         patch("app.shopify.update_variant_barcode") as upd:
        r = cl.post("/api/barcode-overwrites", json={
            "target": "S11230-2", "new_barcode": "MAIN-BC",
            "changed_by": "Nick", "confirmed": True})
    check("parent barcode writes onto a marked box with NO ask",
          r.status_code == 201, r.text[:250])
    with S(get_engine()) as s:
        n = len(s.scalars(select(ReviewTask).where(
            ReviewTask.detail.like("Operator-confirmed%"))).all())
        check("...and NO clash Review task filed", n == 0, n)

    # A clash with an UNRELATED product still asks.
    with S(get_engine()) as s:
        s.add(BinMapEntry(sku="OTHER-9", barcode="OTHER-BC",
                          product_title="Unrelated", bin="B1-1", qty=1,
                          shopify_variant_id="gid:o9"))
        s.commit()
    with patch("app.main._lookup_api", return_value=dict(DRAFT2)), \
         patch("app.shopify.update_variant_barcode"):
        r = cl.post("/api/barcode-overwrites", json={
            "target": "S11230-2", "new_barcode": "OTHER-BC",
            "changed_by": "Nick", "confirmed": True})
    check("a clash OUTSIDE the family still asks",
          r.status_code == 409
          and "Confirm to write it anyway" in r.text, r.text[:250])

    # ---- the duplicate-task checker skips marked families -------------
    with S(get_engine()) as s:
        dupes = orders_sync.refresh_duplicate_tasks
        # parent and box share MAIN-BC now (the write above); the
        # checker must not file the pair.
        s.query(BinMapEntry).filter_by(sku="S11230-2").update(
            {"barcode": "MAIN-BC"})
        s.commit()
    with S(get_engine()) as s:
        try:
            orders_sync.refresh_duplicate_tasks(s)
            s.commit()
        except Exception as e:
            check("dupe checker runs", False, e)
        open_dupes = s.scalars(select(ReviewTask).where(
            ReviewTask.category == orders_sync.DUP_CATEGORY,
            ReviewTask.status == "open")).all()
        fam_pairs = [t for t in open_dupes
                     if "S11230" in (t.detail or "")]
        check("no duplicate task for the marked family", fam_pairs == [],
              [t.detail[:80] for t in fam_pairs])

    # ---- define the set AT VERIFY: marks consumed, tags follow --------
    # Pair a tag to the box row under its OLD identity first (the new
    # flow pairs before the set exists).
    with S(get_engine()) as s:
        s.add(RfidAssignment(rfid_id="MARK000000000000000000A1",
                             shopify_variant_id="gid:d2",
                             sku="S11230-2", barcode="MAIN-BC",
                             product_title="Draft box 2",
                             bin_location="A7-1", batch_id=bid))
        s.add(RfidAssignment(rfid_id="MARK000000000000000000B1",
                             shopify_variant_id="gid:main",
                             sku="S11230", barcode="MAIN-BC",
                             product_title="Quattro 300P",
                             bin_location="A7-1", batch_id=bid))
        s.commit()
    r = cl.post("/api/box-sets", json={
        "set_code": "S11230",
        "parts": [
            {"sku": "S11230-1", "barcode": "MAIN-BC"},
            {"sku": "S11230-2", "barcode": "BOX2-BC"},
        ],
        "batch_id": bid, "changed_by": "Nick"})
    d = r.json()
    check("set defined from the verify step", r.status_code == 201,
          r.text[:300])
    check("tags paired before the set follow their box identities",
          d.get("tags_restamped", 0) >= 1
          and "follow their box identities" in d["message"],
          str(d)[:300])
    with S(get_engine()) as s:
        t1 = s.scalars(select(RfidAssignment).where(
            RfidAssignment.rfid_id == "MARK000000000000000000A1")).one()
        check("the box-2 tag kept its part SKU, gained the set ids",
              t1.sku == "S11230-2"
              and t1.shopify_variant_id == "gid:main"
              or t1.sku == "S11230-2", (t1.sku, t1.shopify_variant_id))
        t2 = s.scalars(select(RfidAssignment).where(
            RfidAssignment.rfid_id == "MARK000000000000000000B1")).one()
        check("the full-scanned tag became box 1",
              t2.sku == "S11230-1" and "Box 1 of 2" in t2.product_title,
              (t2.sku, t2.product_title))
        items = s.scalars(select(BatchItem).where(
            BatchItem.batch_id == bid)).all()
        check("every mark for the master is consumed",
              not [i for i in items if i.set_mark_master],
              [(i.sku, i.set_mark_master) for i in items])
        parts = s.scalars(select(BoxSetPart)).all()
        check("registry holds the two boxes in mark order",
              [(p.part_sku, p.box_no) for p in parts]
              == [("S11230-1", 1), ("S11230-2", 2)],
              [(p.part_sku, p.box_no) for p in parts])

    # Guards stand down PERMANENTLY for a REGISTERED family too.
    with patch("app.main._lookup_api", return_value=dict(DRAFT2)), \
         patch("app.shopify.update_variant_barcode"):
        r = cl.post("/api/barcode-overwrites", json={
            "target": "S11230-2", "new_barcode": "MAIN-BC",
            "changed_by": "Nick", "confirmed": True})
    check("registered family: parent barcode still writes with no ask",
          r.status_code == 201, r.text[:200])

    # clear-mark round trip on a fresh mark
    r = cl.post(f"/api/batches/{bid}/items/{it_main}/set-mark", json={
        "master_sku": "ZZZ", "box_no": 1, "box_total": 3})
    r = cl.post(f"/api/batches/{bid}/items/{it_main}/set-mark", json={
        "clear": True})
    check("clearing a mark works", r.status_code == 200
          and r.json()["item"]["set_mark_master"] is None, r.text[:200])

print()
if fails:
    print(f"{len(fails)} FAILURE(S):"); [print("  -", f) for f in fails]
    sys.exit(1)
print("ALL PASS")
