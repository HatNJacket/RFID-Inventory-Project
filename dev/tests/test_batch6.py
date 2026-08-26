"""Nick's 2026-08-26 afternoon batch:
- vendor overwrites (product-level Shopify write, audited);
- product-history payload carries vendor + presumed-sold tombstones;
- tag-onhand-mismatch never fires for a SKU with ZERO live tags (the
  ZWO ANTI-DEW case);
- a wrong-bin product scanned by accident and zeroed asserts NOTHING:
  no inventory-check at complete, no verify row;
- the Review inventory-check manual recount endpoint."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["SHOPIFY_WRITE_MODE"]="scan_station_only,verify_onhand"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_batch6_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from datetime import datetime, timezone
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app import orders_sync
from app.database import get_engine
from app.models import (
    BinMapEntry, RetiredTag, ReviewTask, RfidAssignment, SoldRecord,
)
from sqlalchemy.orm import Session
from sqlalchemy import select
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

CAT = {
    "701": {"shopify_variant_id":"gid://shopify/ProductVariant/1",
            "shopify_product_id":"gid://shopify/Product/11",
            "product_title":"Alpha Adapter","variant_title":None,
            "sku":"ALPHA-1","barcode":"701","bin_location":"P1-1"},
}
def fake_lookup(t):
    for p in CAT.values():
        if p["barcode"] == t or p["sku"] == t:
            return dict(p)
    return None
VENDOR_WRITES = []
def fake_vendor(pid, vendor):
    VENDOR_WRITES.append((pid, vendor))
    return {"id": pid, "vendor": vendor}

with patch("app.shopify.lookup_barcode", side_effect=fake_lookup), \
     patch("app.shopify.lookup_barcode_all",
           side_effect=lambda t: [fake_lookup(t)] if fake_lookup(t) else []), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.shopify.update_product_vendor", side_effect=fake_vendor), \
     patch("app.shopify.get_on_hand_by_skus",
           side_effect=lambda skus: {s: 0 for s in skus}), \
     patch("app.main._kick_orders_sync_soon"):
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="ALPHA-1", product_title="Alpha Adapter",
                          bin="P1-1", qty=2, barcode="701",
                          shopify_variant_id="gid://shopify/ProductVariant/1",
                          shopify_product_id="gid://shopify/Product/11",
                          vendor="OldBrand"))
        s.add(BinMapEntry(sku="FOREIGN-1", product_title="Foreign Widget",
                          bin="Z9-9", qty=4, barcode="888",
                          shopify_variant_id="t:F1"))
        s.commit()

    # 1) Vendor overwrite: writes to Shopify, updates the bin map row,
    # logs to History.
    r = cl.post("/api/vendor-overwrites", json={
        "target": "ALPHA-1", "new_vendor": "NewBrand",
        "changed_by": "Nick", "confirmed": True})
    check("vendor overwrite succeeds", r.status_code == 201, r.text[:200])
    check("Shopify got the product-level write",
          VENDOR_WRITES == [("gid://shopify/Product/11", "NewBrand")],
          VENDOR_WRITES)
    hist = cl.get("/api/history").json()["events"]
    ev = next((e for e in hist if e["type"] == "vendor-updated"), None)
    check("History shows the vendor change",
          ev is not None and "OldBrand" in (ev["detail"] or ""), ev)
    r = cl.post("/api/vendor-overwrites", json={
        "target": "ALPHA-1", "new_vendor": "X", "confirmed": False})
    check("unconfirmed vendor change is refused", r.status_code == 422,
          r.status_code)

    # 2) Product history carries vendor + presumed-sold tombstones.
    with Session(get_engine()) as s:
        s.add(RfidAssignment(rfid_id="AA000000000000000000AA01",
                             shopify_variant_id="t:A",
                             product_title="Alpha Adapter", sku="ALPHA-1",
                             bin_location="P1-1"))
        s.add(RetiredTag(rfid_id="AA000000000000000000AA02",
                         sku="ALPHA-1", product_title="Alpha Adapter",
                         bin_location="P1-1", kind="presumed-sold",
                         retired_by="Nick"))
        s.add(RetiredTag(rfid_id="AA000000000000000000AA03",
                         sku="ALPHA-1", product_title="Alpha Adapter",
                         kind="dead", retired_by="Nick"))
        s.commit()
    d = cl.get("/api/product-history?term=ALPHA-1").json()
    check("product history carries the (updated) vendor",
          d.get("vendor") == "NewBrand", d.get("vendor"))
    check("presumed-sold tombstones ride along, dated; dead ones don't",
          len(d.get("sold_tags") or []) == 1
          and d["sold_tags"][0]["epc"] == "AA000000000000000000AA02"
          and d["sold_tags"][0]["retired_at"], d.get("sold_tags"))

    # 3) Zero live tags = no tag-onhand-mismatch (the ANTI-DEW case:
    # on-hand 0, one old unretired ledger row, nothing tagged).
    with Session(get_engine()) as s:
        s.add(SoldRecord(order_id="o1", order_name="#1", sku="ANTI-DEW",
                         quantity=1,
                         fulfilled_at=datetime.now(timezone.utc)))
        s.commit()
        out = orders_sync.refresh_mismatch_tasks(s)
        open_now = s.scalars(select(ReviewTask).where(
            ReviewTask.category == "tag-onhand-mismatch",
            ReviewTask.status == "open")).all()
    check("an untagged SKU with old sales files NO mismatch task",
          not any((t.sku or "").upper() == "ANTI-DEW" for t in open_now),
          [(t.sku, t.detail[:60]) for t in open_now])

    # An existing stale task for a now-untagged SKU auto-closes.
    with Session(get_engine()) as s:
        s.add(ReviewTask(category="tag-onhand-mismatch", sku="ANTI-DEW",
                         detail="stale", created_by="orders-sync"))
        s.commit()
        orders_sync.refresh_mismatch_tasks(s)
        stale = s.scalar(select(ReviewTask).where(
            ReviewTask.sku == "ANTI-DEW",
            ReviewTask.category == "tag-onhand-mismatch"))
        check("a stale mismatch task for an untagged SKU auto-closes",
              stale.status == "resolved"
              and "No live tags" in (stale.resolution_note or ""),
              (stale.status, stale.resolution_note))

    # 4) Wrong-bin accidental scan, zeroed: asserts nothing. Batch on
    # P1-1 counts ALPHA (2 of 2 expected); FOREIGN-1 (home Z9-9) gets
    # scanned by mistake and decremented to 0.
    bid = cl.post("/api/batches",
                  json={"bin": "P1-1", "created_by": "Nick"}).json()["id"]
    cl.post(f"/api/batches/{bid}/scan", json={"code": "701"})
    cl.post(f"/api/batches/{bid}/scan", json={"code": "701"})
    it = cl.post(f"/api/batches/{bid}/scan",
                 json={"code": "888"}).json()["item"]
    check("the foreign scan resolved to its own bin",
          it["bin_location"] == "Z9-9", it)
    cl.post(f"/api/batches/{bid}/items/{it['id']}/qty", json={"qty": 0})
    ver = cl.post(f"/api/batches/{bid}/verify", json={"epcs": []}).json()
    check("verify never lists the zeroed wrong-bin row",
          not any(r["sku"] == "FOREIGN-1" for r in ver["items"]),
          [r["sku"] for r in ver["items"]])
    cl.post(f"/api/batches/{bid}/queue-labels", json={})
    done = cl.post(f"/api/batches/{bid}/complete",
                   json={"finalize": True, "created_by": "Nick"})
    check("complete succeeds", done.status_code == 200, done.text[:200])
    tasks = cl.get("/api/review-tasks?status=open").json()["tasks"]
    check("no inventory check filed against the foreign product",
          not any(t["sku"] == "FOREIGN-1" for t in tasks),
          [(t["sku"], t["category"]) for t in tasks])

    # 5) Manual recount on an inventory-check task.
    with Session(get_engine()) as s:
        s.add(ReviewTask(category="inventory-check", sku="ALPHA-1",
                         product_title="Alpha Adapter",
                         detail="Bin P1-1: 0 unit(s) counted but Shopify "
                                "on-hand is 1. Recommend a product check.",
                         created_by="batch"))
        s.commit()
        tid = s.scalars(select(ReviewTask).where(
            ReviewTask.category == "inventory-check")).all()[-1].id
    r = cl.post(f"/api/review-tasks/{tid}/recount",
                json={"count": 1, "changed_by": "Nick"})
    out = r.json()
    check("manual recount rewrites the counted figure",
          r.status_code == 200 and "1 unit(s) counted" in out["task"]["detail"]
          and out["old_count"] == 0, out)
    hist = cl.get("/api/history").json()["events"]
    ev = next((e for e in hist if e["type"] == "manual-recount"), None)
    check("the recount is logged to History",
          ev is not None and "0 → 1" in (ev["detail"] or ""), ev)
    r = cl.post(f"/api/review-tasks/{tid}/recount", json={"count": 3})
    check("recounting an open task again keeps working",
          r.status_code == 200 and r.json()["old_count"] == 1, r.text[:200])

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
