"""Bundle contents (the W9184B case): define once what ONE bundle
contains, and (1) batch collect stops listing the bundle as its own
countable product — the component's count covers it, reported as a
covered_bundles note; (2) the could-not-scan resolve context offers the
components; (3) clearing the contents makes the bundle countable again.
Every change leaves a History receipt.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_bundles_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
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
    from app.models import BinMapEntry, ReviewTask

    # A shelf with the component and two bundle listings of it.
    with S(get_engine()) as s:
        s.add(BinMapEntry(sku="W9184B", barcode="1", bin="D4-2", qty=63,
                          product_title="Antlia 3nm filter",
                          shopify_variant_id="t:1"))
        s.add(BinMapEntry(sku="W9184B-B10", barcode="2", bin="D4-2", qty=6,
                          product_title="BUNDLE: Antlia 3nm x10",
                          shopify_variant_id="t:2"))
        s.add(BinMapEntry(sku="W9184B-B5", barcode="3", bin="D4-2", qty=12,
                          product_title="BUNDLE: Antlia 3nm x5",
                          shopify_variant_id="t:3"))
        s.add(ReviewTask(category="could-not-scan", sku="W9184B-B10",
                         product_title="BUNDLE: Antlia 3nm x10",
                         detail="Bin D4-2: skipped during tagging."))
        s.commit()

    # ---- define contents ------------------------------------------------
    r = cl.post("/api/bundle-contents",
                json={"bundle_sku": "W9184B-B10",
                      "contents": [{"component_sku": "W9184B", "qty": 10}],
                      "updated_by": "Nick"})
    check("contents saved with a human-readable message",
          r.status_code == 201
          and "10× W9184B" in r.json()["message"], r.text)
    r = cl.get("/api/bundle-contents?sku=w9184b-b10")
    check("lookup is case-insensitive",
          r.json()["contents"] == [{"component_sku": "W9184B", "qty": 10}],
          r.text)
    ev = [e for e in cl.get("/api/history").json()["events"]
          if e["type"] == "bundle-contents-set"]
    check("History carries the definition receipt",
          len(ev) == 1 and "10× W9184B" in ev[0]["detail"], ev)
    r = cl.post("/api/bundle-contents",
                json={"bundle_sku": "W9184B-B10",
                      "contents": [{"component_sku": "W9184B", "qty": 0}]})
    check("a zero quantity is refused", r.status_code == 422, r.text)

    # ---- batch collect holds the defined bundle out --------------------
    r = cl.post("/api/batches", json={"bin": "D4-2", "created_by": "Nick"})
    b = r.json()
    seeded = {i["sku"] for i in b["items"]}
    check("the component and the UNDEFINED bundle still seed",
          "W9184B" in seeded and "W9184B-B5" in seeded, seeded)
    check("the DEFINED bundle is held out of the countable list",
          "W9184B-B10" not in seeded, seeded)
    cov = b.get("covered_bundles") or []
    check("…and reported as covered by its components",
          len(cov) == 1 and cov[0]["sku"] == "W9184B-B10"
          and cov[0]["contents"][0]["qty"] == 10, cov)
    cl.post(f"/api/batches/{b['id']}/abandon", json={"remove_ties": False})

    # ---- a batch seeded BEFORE the definition completes clean ----------
    # (Nick's live case: the open batch already carries the bundle as a
    # 0-count row — completing must not file a mismatch for it.)
    r = cl.post("/api/bundle-contents",
                json={"bundle_sku": "W9184B-B5",
                      "contents": [{"component_sku": "W9184B", "qty": 5}],
                      "updated_by": "Nick"})
    b2 = cl.post("/api/batches",
                 json={"bin": "D4-2", "created_by": "Nick"}).json()
    # Sneak the B5 bundle back into this batch as a pre-definition row.
    from app.models import BatchItem as BI
    with S(get_engine()) as s:
        s.add(BI(batch_id=b2["id"], scanned_code="3", resolved=True,
                 sku="W9184B-B5", barcode="3",
                 product_title="BUNDLE: Antlia 3nm x5",
                 qty_scanned=0, paired_count=0, expected_qty=12,
                 bin_location="D4-2", kind="bundle"))
        s.commit()
    done = cl.post(f"/api/batches/{b2['id']}/complete",
                   json={"created_by": "Nick", "finalize": True}).json()
    check("completing a defined 0-count bundle closes clean",
          done["batch"]["status"] == "done", done)

    # ---- import straight from Shopify (the Bundles.app relationship) ---
    BUNDLE_PROD = {"shopify_variant_id": "gid://v/b3",
                   "shopify_product_id": "gid://p/b3",
                   "product_title": "BUNDLE: Antlia 3nm x3",
                   "variant_title": None, "sku": "W9184B-B3",
                   "barcode": None, "bin_location": None}
    with patch("app.shopify.lookup_barcode",
               return_value=dict(BUNDLE_PROD)), \
         patch("app.shopify.get_bundle_components",
               return_value=[{"component_sku": "W9184B", "qty": 3}]):
        r = cl.post("/api/bundle-contents/import",
                    json={"sku": "W9184B-B3", "updated_by": "Nick"})
    check("import writes the components Shopify holds",
          r.status_code == 201
          and r.json()["contents"] == [{"component_sku": "W9184B",
                                        "qty": 3}], r.text)
    with patch("app.shopify.lookup_barcode",
               return_value=dict(BUNDLE_PROD)), \
         patch("app.shopify.get_bundle_components", return_value=[]):
        r = cl.post("/api/bundle-contents/import",
                    json={"sku": "W9184B-B3"})
    check("a non-bundle import answers 404 with hand-entry advice",
          r.status_code == 404 and "by hand" in r.json()["detail"], r.text)

    # ---- the parser reads all three Shopify shapes ---------------------
    import app.shopify as sh
    def shapes(payload):
        with patch("app.shopify.query_shopify",
                   return_value={"productVariant": payload}):
            return sh.get_bundle_components("gid://v/x")
    check("shape 1: native variant components",
          shapes({"productVariantComponents": {"nodes": [
              {"quantity": 2, "productVariant": {"sku": "A"}}]}})
          == [{"component_sku": "A", "qty": 2}], None)
    check("shape 2: the Bundles.app metafield (this store's case)",
          shapes({"bundlesApp": {"value":
              '[{"sku": "W9184B", "quantity": 10, "variant_id": 1}]'}})
          == [{"component_sku": "W9184B", "qty": 10}], None)
    check("shape 3: product-level bundle components",
          shapes({"product": {"bundleComponents": {"nodes": [
              {"quantity": 3,
               "componentVariants": {"nodes": [{"sku": "B"}]}}]}}})
          == [{"component_sku": "B", "qty": 3}], None)
    check("malformed app JSON falls through to empty, never raises",
          shapes({"bundlesApp": {"value": "not json"}}) == [], None)

    # ---- clearing restores countability --------------------------------
    r = cl.post("/api/bundle-contents",
                json={"bundle_sku": "W9184B-B10", "contents": [],
                      "updated_by": "Nick"})
    check("clearing answers with the countable-again message",
          "countable again" in r.json()["message"], r.text)
    r = cl.post("/api/batches", json={"bin": "D4-2", "created_by": "Nick"})
    seeded = {i["sku"] for i in r.json()["items"]}
    check("a cleared bundle seeds as countable once more",
          "W9184B-B10" in seeded, seeded)

    # ================= round 12: bundles.app pull + the surfaces =========
    import json as _json
    from datetime import datetime, timezone
    from sqlalchemy import select
    from app.models import LocateQueueEntry, SoldRecord
    from app import orders_sync as osync

    # Re-define B10 (cleared just above).
    cl.post("/api/bundle-contents", json={
        "bundle_sku": "W9184B-B10",
        "contents": [{"component_sku": "W9184B", "qty": 10}],
        "updated_by": "Nick"})

    # ---- the enriched index --------------------------------------------
    d = cl.get("/api/bundles").json()
    check("bundle index lists every defined bundle",
          {b["bundle_sku"] for b in d["bundles"]}
          == {"W9184B-B10", "W9184B-B5", "W9184B-B3"}, d)
    b10 = [b for b in d["bundles"] if b["bundle_sku"] == "W9184B-B10"][0]
    check("bundle title rides from the bin map",
          b10["title"] == "BUNDLE: Antlia 3nm x10", b10)
    comp = b10["contents"][0]
    check("components carry shelf context",
          comp["title"] == "Antlia 3nm filter" and comp["bin"] == "D4-2"
          and comp["on_hand"] == 63, comp)
    check("buildable = component stock // per-unit qty",
          b10["buildable"] == 6, b10)
    # A component with no stock snapshot makes buildable a guess - the
    # index says nothing rather than overstating.
    cl.post("/api/bundle-contents", json={
        "bundle_sku": "W9184B-BX",
        "contents": [{"component_sku": "W9184B", "qty": 5},
                     {"component_sku": "GHOST-COMP", "qty": 1}]})
    bx = cl.get("/api/bundles?sku=W9184B-BX").json()["bundles"][0]
    check("an unmapped component leaves buildable unknown",
          bx["buildable"] is None, bx)
    cl.post("/api/bundle-contents",
            json={"bundle_sku": "W9184B-BX", "contents": []})
    b3 = [b for b in d["bundles"] if b["bundle_sku"] == "W9184B-B3"][0]
    check("the Shopify import marked its bundle app-sourced, with title",
          b3["source"] == "app"
          and b3["title"] == "BUNDLE: Antlia 3nm x3", b3)
    d = cl.get("/api/bundles?component=W9184B").json()
    check("reverse lookup answers which bundles a product is part of",
          len(d["bundles"]) == 3, d)

    # ---- the bulk pull --------------------------------------------------
    APP_BUNDLES = [
        {"sku": "W9184B-B10", "title": "BUNDLE: Antlia 3nm x10",
         "components": [{"component_sku": "W9184B", "qty": 10}]},
        {"sku": "W9184B-B20", "title": "BUNDLE: Antlia 3nm x20",
         "components": [{"component_sku": "W9184B", "qty": 20}]},
    ]
    with patch("app.shopify.fetch_all_bundles", return_value=APP_BUNDLES):
        r = cl.post("/api/bundles/pull", json={"updated_by": "Nick"})
    d = r.json()
    check("pull builds the new bundle and counts the rest",
          r.status_code == 201 and d["found"] == 2 and d["created"] == 1
          and d["updated"] == 0 and d["unchanged"] == 1, d)
    check("hand-defined bundles missing from the app are left alone",
          d["local_only"] == ["W9184B-B3", "W9184B-B5"], d)
    b20 = cl.get("/api/bundles?sku=W9184B-B20").json()["bundles"][0]
    check("the pulled bundle is app-sourced with the app's contents",
          b20["source"] == "app" and b20["contents"][0]["qty"] == 20
          and b20["synced_at"], b20)
    ev = [e for e in cl.get("/api/history").json()["events"]
          if e["type"] == "bundles-pulled"]
    check("the pull leaves one History receipt", len(ev) == 1, ev)
    check("the index remembers when the pull ran",
          cl.get("/api/bundles").json()["last_pull"] is not None, None)
    # A re-pull with changed app contents re-syncs app bundles...
    APP2 = [{"sku": "W9184B-B20", "title": "BUNDLE: Antlia 3nm x20",
             "components": [{"component_sku": "W9184B", "qty": 19}]}]
    with patch("app.shopify.fetch_all_bundles", return_value=APP2):
        d = cl.post("/api/bundles/pull", json={}).json()
    b20 = cl.get("/api/bundles?sku=W9184B-B20").json()["bundles"][0]
    check("a re-pull re-syncs app-sourced bundles",
          d["updated"] == 1 and b20["contents"][0]["qty"] == 19, (d, b20))
    # ...but never overwrites one the operator tuned by hand.
    cl.post("/api/bundle-contents", json={
        "bundle_sku": "W9184B-B20",
        "contents": [{"component_sku": "W9184B", "qty": 21}],
        "updated_by": "Nick"})
    with patch("app.shopify.fetch_all_bundles", return_value=APP2):
        cl.post("/api/bundles/pull", json={})
    b20 = cl.get("/api/bundles?sku=W9184B-B20").json()["bundles"][0]
    check("a hand-tuned bundle survives the next pull",
          b20["source"] == "manual" and b20["contents"][0]["qty"] == 21,
          b20)

    # ---- audits: bundles leave the report as covered notes -------------
    d = cl.post("/api/bins/D4-2/check", json={"epcs": []}).json()
    skus = {i["sku"] for i in d["items"]}
    check("defined bundles leave the bin-check report",
          "W9184B-B10" not in skus and "W9184B-B5" not in skus
          and "W9184B" in skus, skus)
    check("…and come back as covered-by-components notes",
          {c["sku"] for c in d["covered_bundles"]}
          == {"W9184B-B10", "W9184B-B5"}
          and d["covered_bundles"][0]["contents"], d["covered_bundles"])

    # ---- locate: a bundle expands to its components --------------------
    r = cl.post("/api/locate-queue",
                json={"sku": "W9184B-B10", "worker": "Nick"})
    d = r.json()
    check("locating a bundle queues its components instead",
          d.get("expanded") == ["W9184B"] and "component" in d["message"],
          d)
    q = cl.get("/api/locate-queue").json()["entries"]
    check("the queue holds the component, never the bundle",
          [e["sku"] for e in q] == ["W9184B"], q)

    # ---- sold ledger: bundle sales are component sales -----------------
    with S(get_engine()) as s:
        defs = osync.bundle_defs(s)
        out = osync.explode_bundle_items(defs, [
            {"sku": "w9184b-b10", "qty": 1}, {"sku": "OTHER", "qty": 2}])
        check("feed line items explode one level, others pass through",
              out == [{"sku": "W9184B", "qty": 10},
                      {"sku": "OTHER", "qty": 2}], out)
        # Backfill: a row recorded under the bundle SKU re-books as its
        # component with quantity and parcel map scaled.
        s.add(SoldRecord(
            order_id="o-77", order_name="77", sku="W9184B-B10",
            quantity=2, retired=0,
            fulfilled_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
            source="shipstation", ss_order_id="777",
            ss_shipments='{"111": 2}', ss_line_qty=2))
        s.commit()
        stats = osync.explode_ledger_bundles(s)
        s.commit()
        check("the bundle ledger row explodes into component units",
              stats["rows_exploded"] == 1
              and stats["component_rows"] == 1, stats)
        rows = s.scalars(select(SoldRecord).where(
            SoldRecord.order_id == "o-77")).all()
        check("component row: 2 bundles = 20 units, parcel map scaled",
              len(rows) == 1 and rows[0].sku == "W9184B"
              and rows[0].quantity == 20 and (rows[0].retired or 0) == 0
              and _json.loads(rows[0].ss_shipments) == {"111": 20}
              and rows[0].ss_line_qty == 20,
              [(r2.sku, r2.quantity, r2.ss_shipments) for r2 in rows])

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
