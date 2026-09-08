"""The C72 locate retool's server surface (3.88): the unavailable-move
write (own SHOPIFY_WRITE_MODE feature, bucket bookkeeping, staff-comment
append, snapshot kept honest), the not-in-storage retire kind (no ledger
consumption), tag-info's sold_cover (gates MARK PRESUMED SOLD on the
gun), and the stale-snapshot self-heal on equal-value on-hand raises.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["SHOPIFY_WRITE_MODE"] = (
    "scan_station_only,verify_onhand,unavailable_move")
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_locedit_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app import config
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_on_hand", return_value=None), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}):
  with TestClient(app) as cl:
    from sqlalchemy.orm import Session as S
    from app.database import get_engine
    from app.models import (BinMapEntry, RfidAssignment, RetiredTag,
                            SoldRecord, BarcodeChange)

    with S(get_engine()) as s:
        s.add(BinMapEntry(sku="ZWO-EDIT", bin="F1-2", qty=3,
                          unavailable=0, product_title="Edit Scope",
                          shopify_product_id="gid://shopify/Product/9"))
        s.add(RfidAssignment(rfid_id="EDIT000000000000000000A1",
                             shopify_variant_id="gid://v/1",
                             sku="ZWO-EDIT", product_title="Edit Scope",
                             bin_location="F1-2"))
        s.add(RfidAssignment(rfid_id="EDIT000000000000000000A2",
                             shopify_variant_id="gid://v/1",
                             sku="ZWO-EDIT", product_title="Edit Scope",
                             bin_location="F1-2"))
        s.commit()

    # ---- unavailable-move: the write gate is its OWN feature ----------
    saved = config.SHOPIFY_WRITE_MODE
    config.SHOPIFY_WRITE_MODE = "scan_station_only,verify_onhand"
    r = cl.post("/api/products/ZWO-EDIT/unavailable-move", json={
        "bucket": "damaged", "confirmed": True})
    check("gated behind its own write feature", r.status_code == 403
          and "unavailable_move" in r.json()["detail"], r.text)
    config.SHOPIFY_WRITE_MODE = saved

    # Unconfirmed is refused before Shopify is ever called.
    r = cl.post("/api/products/ZWO-EDIT/unavailable-move", json={
        "bucket": "damaged"})
    check("unconfirmed refused", r.status_code == 422, r.text)

    # Bad bucket never reaches the API either.
    r = cl.post("/api/products/ZWO-EDIT/unavailable-move", json={
        "bucket": "narnia", "confirmed": True})
    check("unknown bucket refused", r.status_code == 422, r.text)

    # ---- the happy path: move in, comment appended, snapshot honest ---
    calls = {}
    def fake_move(sku, bucket, qty=1, direction="in"):
        calls["move"] = (sku, bucket, qty, direction)
        return {"available_before": 3, "bucket_before": 0,
                "product_gid": "gid://shopify/Product/9"}
    def fake_comment(gid, text):
        calls["comment"] = (gid, text)
    with patch("app.shopify.move_unavailable", side_effect=fake_move), \
         patch("app.shopify.append_staff_comment",
               side_effect=fake_comment):
        r = cl.post("/api/products/ZWO-EDIT/unavailable-move", json={
            "bucket": "damaged", "direction": "in", "qty": 1,
            "comment": "Product moved to unavailable from C72.",
            "changed_by": "C72", "confirmed": True})
    check("move-in succeeds", r.status_code == 200
          and r.json()["bucket"] == "damaged", r.text)
    check("shopify got the move",
          calls.get("move") == ("ZWO-EDIT", "damaged", 1, "in"),
          str(calls))
    check("staff comment appended with the product gid",
          calls.get("comment") == ("gid://shopify/Product/9",
                                   "Product moved to unavailable from "
                                   "C72."), str(calls))
    check("message says on-hand unchanged",
          "On-hand is unchanged" in r.json()["message"], r.text)
    with S(get_engine()) as s:
        row = s.query(BinMapEntry).filter_by(sku="ZWO-EDIT").one()
        check("snapshot: shelf-effective down, unavailable up",
              row.qty == 2 and row.unavailable == 1,
              f"qty={row.qty} unavail={row.unavailable}")
        ev = s.query(BarcodeChange).filter_by(
            changed_field="unavailable-move").all()
        check("history logged the move", len(ev) == 1
              and ev[0].new_barcode == "damaged"
              and ev[0].old_barcode == "in:1", str(ev))

    # It rides the product's paper trail with the mapped event type.
    r = cl.get("/api/product-history?term=ZWO-EDIT")
    evs = [e for e in r.json()["events"]
           if e["type"] == "unavailable-move"]
    check("product history shows the set-aside", len(evs) == 1
          and evs[0]["shopify"] is True, str(evs)[:200])

    # ---- the audit's Unavailable-stock section (Nick, 2026-09-08) ----
    with patch("app.shopify.get_staff_comments_by_skus",
               return_value={"ZWO-EDIT": "Missing a piece - Nick"}):
        r = cl.get("/api/audit/unavailable")
    d = r.json()
    row = next((i for i in d["items"] if i["sku"] == "ZWO-EDIT"), None)
    check("unavailable section lists the set-aside",
          d["count"] == 1 and row is not None
          and row["unavailable"] == 1 and row["bins"] == ["F1-2"],
          str(d)[:300])
    check("...with when/who/bucket from History",
          row["set_at"] is not None and row["set_by"] == "C72"
          and row["bucket"] == "damaged", str(row)[:200])
    check("...and the live staff comment",
          row["staff_comments"] == "Missing a piece - Nick"
          and d["comments_live"] is True, str(row)[:200])

    # ---- direction out reverses the snapshot bookkeeping --------------
    with patch("app.shopify.move_unavailable", side_effect=fake_move), \
         patch("app.shopify.append_staff_comment",
               side_effect=fake_comment):
        r = cl.post("/api/products/ZWO-EDIT/unavailable-move", json={
            "bucket": "damaged", "direction": "out",
            "changed_by": "web", "confirmed": True})
    check("move-out succeeds", r.status_code == 200, r.text)
    with S(get_engine()) as s:
        row = s.query(BinMapEntry).filter_by(sku="ZWO-EDIT").one()
        check("snapshot restored on the way back",
              row.qty == 3 and row.unavailable == 0,
              f"qty={row.qty} unavail={row.unavailable}")
    with patch("app.shopify.get_staff_comments_by_skus",
               return_value={}):
        r = cl.get("/api/audit/unavailable")
    check("brought-back stock leaves the unavailable section",
          r.json()["count"] == 0, r.text[:200])

    # A Shopify refusal surfaces as 502, nothing committed locally.
    def angry_move(*a, **k):
        raise RuntimeError("stocked at 2 locations")
    with patch("app.shopify.move_unavailable", side_effect=angry_move):
        r = cl.post("/api/products/ZWO-EDIT/unavailable-move", json={
            "bucket": "reserved", "confirmed": True})
    check("shopify refusal -> 502", r.status_code == 502
          and "2 locations" in r.json()["detail"], r.text)
    with S(get_engine()) as s:
        row = s.query(BinMapEntry).filter_by(sku="ZWO-EDIT").one()
        check("failed move leaves the snapshot alone",
              row.qty == 3 and row.unavailable == 0,
              f"qty={row.qty} unavail={row.unavailable}")

    # ---- tag-info sold_cover gates MARK PRESUMED SOLD -----------------
    r = cl.get("/api/tag-info/EDIT000000000000000000A1")
    check("no sales -> sold_cover 0", r.status_code == 200
          and r.json()["sold_cover"] == 0, r.text[:200])
    with S(get_engine()) as s:
        s.add(SoldRecord(order_id="ord-9", order_name="#9001",
                         sku="ZWO-EDIT", quantity=1, retired=0))
        s.commit()
    r = cl.get("/api/tag-info/EDIT000000000000000000A1")
    check("an unretired sale -> sold_cover 1",
          r.json()["sold_cover"] == 1, r.text[:200])

    # ---- not-in-storage retire: tombstone, NO ledger consumption ------
    r = cl.post("/api/assignments/retire", json={
        "epcs": ["EDIT000000000000000000A2"], "kind": "not-in-storage",
        "changed_by": "C72", "note": "From C72 locate"})
    check("not-in-storage retire accepted", r.status_code == 200
          and r.json()["kind"] == "not-in-storage", r.text)
    with S(get_engine()) as s:
        gone = s.query(RfidAssignment).filter_by(
            rfid_id="EDIT000000000000000000A2").first()
        tomb = s.query(RetiredTag).filter_by(
            rfid_id="EDIT000000000000000000A2").first()
        sold = s.query(SoldRecord).filter_by(order_id="ord-9").one()
        check("tag moved to the retired table", gone is None
              and tomb is not None and tomb.kind == "not-in-storage",
              f"gone={gone} tomb={tomb}")
        check("ledger NOT consumed (unit was not sold)",
              sold.retired == 0, f"retired={sold.retired}")

    # presumed-sold still consumes, for contrast.
    r = cl.post("/api/assignments/retire", json={
        "epcs": ["EDIT000000000000000000A1"], "kind": "presumed-sold",
        "changed_by": "C72"})
    check("presumed-sold retire accepted", r.status_code == 200, r.text)
    with S(get_engine()) as s:
        sold = s.query(SoldRecord).filter_by(order_id="ord-9").one()
        check("presumed-sold consumes the sale", sold.retired == 1,
              f"retired={sold.retired}")

    # ---- equal-value on-hand raise self-heals the stale snapshot ------
    with S(get_engine()) as s:
        s.add(BinMapEntry(sku="STALE-1", bin="F1-2", qty=2,
                          unavailable=0, product_title="Stale Scope"))
        s.commit()
    # Shopify already says 3; the snapshot said 2 and offered a "(+1)".
    with patch("app.shopify.get_on_hand", return_value=3), \
         patch("app.shopify.set_on_hand") as never:
        r = cl.post("/api/onhand-updates", json={
            "sku": "STALE-1", "new_qty": 3, "changed_by": "web",
            "confirmed": True})
        check("equal raise -> friendly noop, not a 422",
              r.status_code in (200, 201) and r.json().get("noop") is True
              and "refreshed" in r.json()["message"], r.text)
        check("nothing written to Shopify",
              never.call_count == 0, str(never.call_count))
    with S(get_engine()) as s:
        row = s.query(BinMapEntry).filter_by(sku="STALE-1").one()
        check("snapshot healed to the live number", row.qty == 3,
              f"qty={row.qty}")
    # And a genuinely lower ask still gets the raise-only 422.
    with patch("app.shopify.get_on_hand", return_value=3):
        r = cl.post("/api/onhand-updates", json={
            "sku": "STALE-1", "new_qty": 2, "changed_by": "web",
            "confirmed": True})
        check("lower ask still refused raise-only",
              r.status_code == 422, r.text)

print()
sys.exit(1 if fails else 0)
