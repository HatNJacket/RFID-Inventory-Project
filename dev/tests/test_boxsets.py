"""Multi-box SETS (Nick, 2026-09-08, the S11230): boxes with their own
barcodes/SKUs (usually draft listings) sold only as one full product.
Creation from a batch (unresolved rows re-resolve as parts), part
resolution through the lookup chain, min-count arithmetic vs the FULL
product's on-hand, part exemptions in checks and audits, per-part
columns in Inventory, the Box N of M label note, and the re-label pass
for legacy double-counted stock.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_boxset_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app import orders_sync
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

STOCK = {"S11230": 2}
def fake_stock_info(skus):
    return {s: {"on_hand": STOCK[s], "unavailable": 0, "bin": "A7-1"}
            for s in skus if s in STOCK}

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_on_hand", return_value=None), \
     patch("app.shopify.get_stock_info_by_skus",
           side_effect=fake_stock_info), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.main._maybe_refresh_bin_map", return_value=False), \
     patch("app.main._kick_orders_sync_soon", create=True):
  with TestClient(app) as cl:
    from sqlalchemy import select
    from sqlalchemy.orm import Session as S
    from app.database import get_engine
    from app.models import (Batch, BatchItem, BinMapEntry, BoxSetPart,
                            PrintJob, RfidAssignment, ReviewTask)

    with S(get_engine()) as s:
        s.add(BinMapEntry(sku="S11230", barcode="7411230",
                          product_title="Explore FirstLight 10in Dob",
                          bin="A7-1", qty=2, unavailable=0,
                          shopify_variant_id="gid://v/11230",
                          shopify_product_id="gid://p/11230",
                          image_url="http://img/dob"))
        # S11230-2 has its own (draft-ish) bin map row - Nick's exact
        # situation; S11230-1 resolves nowhere.
        s.add(BinMapEntry(sku="S11230-2", barcode="7411230002",
                          product_title="FirstLight Dob - optical tube",
                          bin="A7-1", qty=0,
                          shopify_variant_id="gid://v/112302"))
        # A collect batch: box 2 resolved, box 1 unresolved.
        b = Batch(bin_name="A7-1", created_by="test")
        s.add(b); s.flush()
        s.add(BatchItem(batch_id=b.id, scanned_code="7411230002",
                        resolved=True, sku="S11230-2",
                        barcode="7411230002",
                        product_title="FirstLight Dob - optical tube",
                        qty_scanned=2))
        s.add(BatchItem(batch_id=b.id, scanned_code="7411230001",
                        resolved=False, qty_scanned=2))
        # Legacy double-count: two tags under the FULL SKU = 1 unit.
        s.add(RfidAssignment(rfid_id="B0X0000000000000000000A1",
                             shopify_variant_id="gid://v/11230",
                             sku="S11230", product_title="Dob",
                             bin_location="A7-1"))
        s.add(RfidAssignment(rfid_id="B0X0000000000000000000A2",
                             shopify_variant_id="gid://v/11230",
                             sku="S11230", product_title="Dob",
                             bin_location="A7-1"))
        s.commit()
        bid = b.id

    # ---- create the set from the batch --------------------------------
    r = cl.post("/api/box-sets", json={
        "set_code": "S11230",
        "parts": [
            {"sku": "S11230-1", "barcode": "7411230001"},
            {"sku": "S11230-2", "barcode": "7411230002"},
        ],
        "batch_id": bid, "changed_by": "Nick"})
    d = r.json()
    check("set created", r.status_code == 201
          and d["set_sku"] == "S11230"
          and [p["part_sku"] for p in d["parts"]]
          == ["S11230-1", "S11230-2"], r.text[:300])
    check("both batch rows re-resolved as parts",
          d["batch_items_updated"] == 2, d)
    check("legacy full-SKU tags reported for the re-label offer",
          d["full_tags"] == 2, d)
    with S(get_engine()) as s:
        items = s.scalars(select(BatchItem).where(
            BatchItem.batch_id == bid)).all()
        unres = [i for i in items if not i.resolved]
        check("no unresolved rows left", unres == [], unres)
        part1 = next(i for i in items if i.sku == "S11230-1")
        check("the unresolved row became box 1 with the set's identity",
              part1.shopify_variant_id == "gid://v/11230"
              and "Box 1 of 2" in (part1.product_title or "")
              and part1.qty_scanned == 2, part1.product_title)

    # A part can't join a second set.
    r = cl.post("/api/box-sets", json={
        "set_code": "S11230", "parts": [
            {"sku": "S11230-1"}, {"sku": "OTHER-9"}]})
    check("redefining the SAME set is allowed", r.status_code == 201,
          r.text[:200])
    cl.post("/api/box-sets", json={
        "set_code": "S11230", "parts": [
            {"sku": "S11230-1", "barcode": "7411230001"},
            {"sku": "S11230-2", "barcode": "7411230002"}]})
    with S(get_engine()) as s:
        s.add(BinMapEntry(sku="ZOTHER", barcode="999",
                          product_title="Other product", bin="B1-1",
                          qty=1, shopify_variant_id="gid://v/zo"))
        s.commit()
    r = cl.post("/api/box-sets", json={
        "set_code": "ZOTHER", "parts": [
            {"sku": "S11230-1"}, {"sku": "X-2"}]})
    check("a part claimed by another set is refused",
          r.status_code == 409, r.text)

    # ---- resolution: part SKU and part barcode both answer ------------
    r = cl.get("/api/products/by-barcode/S11230-1")
    check("part SKU resolves via the set registry",
          r.status_code == 200
          and r.json()["sku"] == "S11230-1"
          and "Box 1 of 2" in r.json()["product_title"]
          and r.json()["boxset"]["set_sku"] == "S11230"
          and r.json()["bin_location"] == "A7-1", r.text[:300])
    r = cl.get("/api/products/by-barcode/7411230001")
    check("part barcode resolves too",
          r.json().get("sku") == "S11230-1", r.text[:200])
    r = cl.get("/api/products/by-barcode/S11230-2")
    check("a part with its own listing still says which set",
          (r.json().get("boxset") or {}).get("set_sku") == "S11230",
          r.text[:300])
    r = cl.get("/api/products/by-barcode/S11230")
    check("the full product lists its parts",
          [p["part_sku"] for p in
           (r.json().get("boxset_full") or {}).get("parts", [])]
          == ["S11230-1", "S11230-2"], r.text[:300])

    # ---- counting: min over parts vs the FULL product -----------------
    with S(get_engine()) as s:
        for epc, sku in (("B0X0000000000000000000B1", "S11230-1"),
                         ("B0X0000000000000000000B2", "S11230-1"),
                         ("B0X0000000000000000000C1", "S11230-2")):
            s.add(RfidAssignment(rfid_id=epc,
                                 shopify_variant_id="gid://v/11230",
                                 sku=sku, product_title="Dob part",
                                 bin_location="A7-1"))
        # The legacy full-SKU tags leave (as the re-label pass would).
        for t in s.query(RfidAssignment).filter_by(sku="S11230"):
            s.delete(t)
        s.commit()
        check("tag_units(set) is min over parts (2 vs 1 -> 1)",
              orders_sync.tag_units(s, "S11230") == 1, "")
        check("tracked_skus includes the set through its parts",
              "S11230" in orders_sync.tracked_skus(s), "")

    # refresh files nothing for parts; the set's math uses min.
    with S(get_engine()) as s:
        orders_sync.refresh_mismatch_tasks(s); s.commit()
        part_tasks = s.scalars(select(ReviewTask).where(
            ReviewTask.sku.in_(("S11230-1", "S11230-2")),
            ReviewTask.status == "open")).all()
        check("no checks ever file for part SKUs", part_tasks == [],
              [t.sku for t in part_tasks])

    # ---- Inventory: per-part columns on the set row -------------------
    r = cl.get("/api/inventory/summary?fast=1")
    rows = r.json()["products"]
    set_row = next((p for p in rows if p["sku"] == "S11230"), None)
    check("the set gets an Inventory row with part columns",
          set_row is not None and set_row["unit_count"] == 1
          and [(bp["sku"], bp["units"]) for bp in set_row["box_parts"]]
          == [("S11230-1", 2), ("S11230-2", 1)], str(set_row)[:300])
    p1_row = next((p for p in rows if p["sku"] == "S11230-1"), None)
    check("part rows say which set they belong to",
          p1_row is not None and p1_row["boxset_part_of"] == "S11230",
          str(p1_row)[:200])

    # ---- audit surfaces -----------------------------------------------
    r = cl.get("/api/audit/bins")
    prods = [p for b in r.json()["bins"] for p in b["products"]]
    set_p = next((p for p in prods if p["sku"] == "S11230"), None)
    check("audit row: set units = min(parts), on-hand its own",
          set_p is not None and set_p["rfid_units"] == 1
          and set_p["on_hand"] == 2 and set_p["diff"] == -1
          and set_p["boxset"] == {"S11230-1": 2, "S11230-2": 1},
          str(set_p)[:300])
    check("part SKUs never score alone in the audit",
          not [p for p in prods if p["sku"] in
               ("S11230-1", "S11230-2")], "")

    r = cl.post("/api/bins/A7-1/check",
                json={"epcs": ["B0X0000000000000000000B1",
                                "B0X0000000000000000000C1"]})
    items = r.json()["items"]
    p1 = next((x for x in items if x["sku"] == "S11230-1"), None)
    setr = next((x for x in items if x["sku"] == "S11230"), None)
    check("bin sweep: parts audit against the SET's shelf number",
          p1 is not None and p1["expected_qty"] == 2
          and p1["boxset_of"] == "S11230", str(p1)[:300])
    check("bin sweep: the set row defers to its parts",
          setr is None or setr["expected_qty"] is None,
          str(setr)[:200])

    # ---- the re-label pass --------------------------------------------
    with S(get_engine()) as s:
        s.add(RfidAssignment(rfid_id="B0X0000000000000000000A9",
                             shopify_variant_id="gid://v/11230",
                             sku="S11230", product_title="Dob",
                             bin_location="A7-1"))
        s.commit()
    r = cl.post("/api/box-sets/S11230/relabel", json={
        "units": 1, "unlink_old": True, "confirmed": True,
        "changed_by": "Nick"})
    check("re-label queues one label per part and unlinks the old",
          r.status_code == 200 and r.json()["queued_labels"] == 2
          and r.json()["unlinked_old_tags"] == 1, r.text[:300])
    with S(get_engine()) as s:
        jobs = s.scalars(select(PrintJob).where(
            PrintJob.sku.in_(("S11230-1", "S11230-2")))).all()
        check("part labels carry the Box N of M bin note",
              len(jobs) == 2 and all(
                  ", Box " in (j.bin_location or "") for j in jobs)
              and any("Box 1 of 2" in j.bin_location for j in jobs),
              [(j.sku, j.bin_location) for j in jobs])
        gone = s.query(RfidAssignment).filter_by(sku="S11230").all()
        check("old full-SKU tags unlinked", gone == [], gone)

    # ---- linked barcodes: list, unlink receipt, shadow cleanup --------
    # Nick's S11810-1 case: an unresolved box barcode linked straight to
    # the full product. The manager lists it (who/when), unlink logs a
    # receipt, and creating a box set on that code clears the alias so
    # it can't shadow the part registry.
    r = cl.post("/api/barcode-aliases", json={
        "alias_barcode": "S11810-1", "target": "S11230",
        "created_by": "Nick"})
    check("alias created (the mis-link)", r.status_code == 201, r.text)
    r = cl.get("/api/barcode-aliases?sku=S11230")
    al = r.json()["aliases"]
    check("manager lists the link with who and when",
          r.json()["count"] >= 1
          and any(a["alias_barcode"] == "S11810-1"
                  and a["created_by"] == "Nick"
                  and a["created_at"] for a in al), str(al)[:300])

    # Creating a set whose part code matches the alias clears it.
    r = cl.post("/api/box-sets", json={
        "set_code": "S11230",
        "parts": [{"sku": "S11810-1"}, {"sku": "S11810-2"}],
        "changed_by": "Nick"})
    check("set creation clears the shadowing alias",
          r.status_code == 201 and r.json()["aliases_cleared"] == 1
          and "shadowed" in r.json()["message"], r.text[:300])
    r = cl.get("/api/barcode-aliases")
    check("the alias is gone",
          not any(a["alias_barcode"] == "S11810-1"
                  for a in r.json()["aliases"]), r.text[:200])
    r = cl.get("/api/product-history?term=S11230")
    evs = [e for e in r.json()["events"] if e["type"] == "alias-unlinked"]
    check("...with an unlink receipt in History", len(evs) == 1
          and evs[0]["shopify"] is False, str(evs)[:200])
    cl.delete("/api/box-sets/S11230?by=Nick")

    # Manual unlink through the manager logs its own receipt.
    cl.post("/api/barcode-aliases", json={
        "alias_barcode": "FOREIGN-99", "target": "ZOTHER",
        "created_by": "Nick"})
    r = cl.delete("/api/barcode-aliases/FOREIGN-99?by=Nick")
    check("manual unlink answers 204", r.status_code == 204, r.text)
    r = cl.get("/api/product-history?term=ZOTHER")
    evs = [e for e in r.json()["events"] if e["type"] == "alias-unlinked"]
    check("manual unlink logged with the worker", len(evs) == 1
          and evs[0]["worker"] == "Nick", str(evs)[:200])

    # ---- delete -------------------------------------------------------
    r = cl.post("/api/box-sets", json={
        "set_code": "S11230",
        "parts": [
            {"sku": "S11230-1", "barcode": "7411230001"},
            {"sku": "S11230-2", "barcode": "7411230002"},
        ]})
    r = cl.delete("/api/box-sets/S11230?by=Nick")
    check("set deletes", r.status_code == 200
          and r.json()["removed_parts"] == 2, r.text)
    r = cl.get("/api/box-sets")
    check("registry empty again", r.json()["count"] == 0, r.text)

    # ---- NEW boxes: auto-numbered SKUs + real DRAFT listings ----------
    # (Nick, 2026-09-08: most multi-box products have no draft
    # listings - the builder creates them, gated as its own write.)
    from app import config
    r = cl.post("/api/box-sets", json={
        "set_code": "S11230",
        "parts": [
            {"sku": "S11230-1", "barcode": "7411230001"},
            {"barcode": "NEWBOX-BC-2", "create_draft": True},
        ]})
    check("draft creation is gated behind its own write feature",
          r.status_code == 403
          and "draft_listings" in r.json()["detail"], r.text)

    saved_mode = config.SHOPIFY_WRITE_MODE
    config.SHOPIFY_WRITE_MODE = "scan_station_only,draft_listings"
    draft_calls = []
    def fake_draft(title, sku, barcode, bin_value):
        draft_calls.append((title, sku, barcode, bin_value))
        return {"product_gid": f"gid://p/{sku}",
                "variant_gid": f"gid://v/{sku}",
                "sku": sku, "barcode": barcode, "title": title}
    with patch("app.shopify.create_draft_listing",
               side_effect=fake_draft):
        r = cl.post("/api/box-sets", json={
            "set_code": "S11230",
            "parts": [
                {"sku": "S11230-1", "barcode": "7411230001"},
                {"barcode": "NEWBOX-BC-2", "create_draft": True,
                 "bin": "A7-1"},
                {"sku": "CUSTOM-9", "barcode": "NEWBOX-BC-3",
                 "create_draft": True},
            ], "changed_by": "Nick"})
    config.SHOPIFY_WRITE_MODE = saved_mode
    d = r.json()
    check("set with new boxes created", r.status_code == 201
          and d["drafts_created"] == ["S11230-2", "CUSTOM-9"],
          r.text[:300])
    check("blank SKU auto-numbers, skipping the taken -1",
          [p["part_sku"] for p in d["parts"]]
          == ["S11230-1", "S11230-2", "CUSTOM-9"], str(d)[:300])
    check("draft named exactly per Nick's format, bin carried",
          draft_calls[0][0].startswith(
              "DRAFT LISTING - INGREDIENT Explore FirstLight 10in Dob "
              "S11230-2")
          and draft_calls[0][3] == "A7-1"
          and draft_calls[1][1] == "CUSTOM-9", draft_calls)

    # A part whose barcode IS the set's own catalog barcode still
    # resolves to the PART - the S11810 collision.
    saved_mode = config.SHOPIFY_WRITE_MODE
    config.SHOPIFY_WRITE_MODE = "scan_station_only,draft_listings"
    with patch("app.shopify.create_draft_listing",
               side_effect=fake_draft):
        cl.post("/api/box-sets", json={
            "set_code": "S11230",
            "parts": [
                {"sku": "S11230-1", "barcode": "7411230001"},
                {"barcode": "7411230", "create_draft": True},
            ]})
    config.SHOPIFY_WRITE_MODE = saved_mode
    r = cl.get("/api/products/by-barcode/7411230")
    check("a box barcode colliding with the catalog resolves to the PART",
          r.json().get("sku") == "S11230-2"
          and (r.json().get("boxset") or {}).get("set_sku") == "S11230",
          r.text[:300])
    cl.delete("/api/box-sets/S11230?by=Nick")

print()
sys.exit(1 if fails else 0)
