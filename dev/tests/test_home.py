"""Home landing page (Nick, 2026-09-24): sidebar navigation + tiles that
route to existing features + the global lookup's typeahead endpoint.

Navigation layer only - no feature was rebuilt, so this suite checks the
new surface exists without disturbing the old contracts: the served page
carries the sidebar (same .tabs__tab/data-tab hooks), the Home section,
the printer chip and the resume card; /api/products/suggest serves the
cached (sku, title, barcode) triples from the bin map."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_home_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.main import app
import app.main as main_mod
from app.database import get_engine
from app.models import BinMapEntry
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

with patch("app.main._maybe_refresh_bin_map", return_value=False), \
     patch("app.main._kick_orders_sync_soon"):
  with TestClient(app) as cl:
    # ---- the served page carries the new navigation surface ------------
    html = cl.get("/").text
    check("sidebar keeps the .tabs__tab/data-tab contract",
          'data-tab="home"' in html and 'data-tab="scan"' in html
          and 'class="tabs__tab' in html, "")
    check("home is the active default and scan starts hidden",
          'data-tab="home" role="tab"' in html
          and '<main class="flow" id="tab-scan" hidden>' in html, "")
    check("home section, tiles and product card are served",
          'id="tab-home"' in html and 'class="tile"' in html
          and 'id="pcard"' in html and 'id="home-lookup"' in html, "")
    check("printer chip + sidebar toggle + resume card are served",
          'id="agent-chip"' in html and 'id="side-toggle"' in html
          and 'id="resume-card"' in html, "")
    check("every tile routes to an existing tab",
          all(f'data-go="{t}"' in html
              for t in ("batch", "scan", "audits", "review")), "")

    # ---- typeahead endpoint --------------------------------------------
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="ASK-M54-OAG", barcode="697039",
                          product_title="Askar M54 OAG", bin="G2-1",
                          qty=3, shopify_variant_id="t:1"))
        s.add(BinMapEntry(sku="ask-m54-oag", barcode="697039",
                          product_title="Askar M54 OAG (dupe row)",
                          bin="B17", qty=1, shopify_variant_id="t:1"))
        s.add(BinMapEntry(sku="F9340A", barcode="050081",
                          product_title="NexYZ Adapter",
                          variant_title="3-Axis", bin="A1-1",
                          qty=2, shopify_variant_id="t:2"))
        s.add(BinMapEntry(sku="", barcode="000",
                          product_title="No SKU thing", bin="Z9-9",
                          qty=1, shopify_variant_id="t:3"))
        s.commit()
    main_mod._suggest_cache["body"] = None  # seed landed after any warmup
    r = cl.get("/api/products/suggest")
    check("suggest answers", r.status_code == 200, r.status_code)
    prods = r.json()["products"]
    by_sku = {p[0].upper(): p for p in prods}
    check("one entry per SKU, case-insensitively deduped",
          len(prods) == 2 and "ASK-M54-OAG" in by_sku and "F9340A" in by_sku,
          prods)
    check("variant titles fold into the display name",
          by_sku["F9340A"][1] == "NexYZ Adapter - 3-Axis", by_sku["F9340A"])
    check("barcode rides along for wedge matches",
          by_sku["ASK-M54-OAG"][2] == "697039", by_sku["ASK-M54-OAG"])
    check("skuless rows are left out",
          all(p[0] for p in prods), prods)

    # The cache answers without re-querying (poke the DB, same body).
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="NEW-1", barcode="1", product_title="New",
                          bin="C1", qty=1, shopify_variant_id="t:4"))
        s.commit()
    r2 = cl.get("/api/products/suggest")
    check("suggest is cached between calls",
          len(r2.json()["products"]) == 2, len(r2.json()["products"]))
    main_mod._suggest_cache["at"] = 0.0
    r3 = cl.get("/api/products/suggest")
    check("cache expiry picks up new products",
          len(r3.json()["products"]) == 3, len(r3.json()["products"]))

    # ---- the agent-status shape the printer chip reads ------------------
    r = cl.get("/api/print-agent/status")
    body = r.json()
    check("print-agent status still serves the chip's fields",
          r.status_code == 200
          and all(k in body for k in
                  ("online", "fault", "wedged", "holding",
                   "win_jobs", "last_seen_seconds")), body)

    # ---- round 3 (2026-09-24): settings tab + suggest images ------------
    check("settings is a sidebar tab with its own section",
          'data-tab="settings"' in html and 'id="tab-settings"' in html
          and "settings-menu" not in html, "")
    check("embedded branch carries the App Bridge nav menu",
          "ui-nav-menu" in open(os.path.join(
              os.path.dirname(os.path.dirname(os.path.dirname(
                  os.path.abspath(__file__)))),
              "app", "templates", "index.html"), encoding="utf-8").read(),
          "")
    check("suggest rows carry an image slot",
          all(len(p) == 4 for p in r3.json()["products"]),
          r3.json()["products"][:2])

    # ---- four-box label editor: extras save + claim overlay -------------
    r = cl.put("/api/label-names/ASK-M54-OAG", json={
        "top_text": "Telescopes Canada", "sku_line": "Askar M54 OAG",
        "barcode_mode": "sku", "bin_text": "SHOW-1",
        "updated_by": "Nick"})
    check("four-box save answers with the extras",
          r.status_code == 200 and r.json()["barcode_mode"] == "sku"
          and r.json()["bin_text"] == "SHOW-1", r.text[:200])
    r = cl.get("/api/label-names/ASK-M54-OAG")
    check("extras round-trip on GET",
          r.json()["barcode_mode"] == "sku"
          and r.json()["bin_text"] == "SHOW-1", r.json())
    # queue a job with the OLD product values, then claim as the agent:
    # the claim payload must wear the CURRENT label settings.
    r = cl.post("/api/print-jobs", json={
        "quantity": 1, "sku": "ASK-M54-OAG", "barcode": "697039",
        "product_title": "Askar M54 OAG",
        "bin_location": "G2-1", "shopify_variant_id": "t:1"})
    check("job queued", r.status_code == 201, r.text[:200])
    r = cl.post("/api/print-jobs/claim?limit=5")
    j = next((x for x in r.json()["jobs"]
              if x["sku"] == "ASK-M54-OAG"), None)
    centre = j and (j["label_sku"] or (
        j["label_name"] if (j["label_placement"] or "") in ("sku", "both")
        else None))
    check("claim overlays the saved label: SKU-encoded barcode + custom bin",
          j is not None and j["barcode"] == "ASK-M54-OAG"
          and j["bin_location"] == "SHOW-1"
          and centre == "Askar M54 OAG",
          j)
    # clearing the extras restores the product's own values on claim
    cl.put("/api/label-names/ASK-M54-OAG", json={
        "barcode_mode": "auto", "bin_text": ""})
    r = cl.post("/api/print-jobs", json={
        "quantity": 1, "sku": "ASK-M54-OAG", "barcode": "697039",
        "product_title": "Askar M54 OAG",
        "bin_location": "G2-1", "shopify_variant_id": "t:1"})
    r = cl.post("/api/print-jobs/claim?limit=5")
    j = next((x for x in r.json()["jobs"]
              if x["sku"] == "ASK-M54-OAG"), None)
    check("cleared extras fall back to the product's barcode and bin",
          j is not None and j["barcode"] == "697039"
          and j["bin_location"] == "G2-1", j)

    # ---- round 6: stock-breakdown carries changes + sales stats ---------
    from app.models import SoldRecord, BarcodeChange
    from datetime import datetime, timezone
    with Session(get_engine()) as s:
        s.add(SoldRecord(order_id="gid://shopify/Order/9", order_name="7001",
                         sku="ASK-M54-OAG", quantity=2,
                         fulfilled_at=datetime.now(timezone.utc)))
        s.add(BarcodeChange(sku="ASK-M54-OAG", changed_field="on-hand",
                            old_barcode="3", new_barcode="5",
                            changed_by="Nick"))
        s.commit()
    with patch("app.shopify.get_quantity_breakdown",
               return_value={"available": 3, "committed": 1,
                             "on_hand": 5, "unavailable": 1}):
        r = cl.get("/api/products/ASK-M54-OAG/stock-breakdown")
    body = r.json()
    kinds = {c["kind"]: c for c in body.get("inventory_changes", [])}
    check("inventory changes list sold and manual movements",
          r.status_code == 200
          and kinds.get("sold", {}).get("units") == -2
          and kinds.get("sold", {}).get("who") == "#7001"
          and kinds.get("manual", {}).get("units") == 2
          and kinds.get("manual", {}).get("who") == "Nick", body)
    check("sales stats ride along (12 weekly buckets, totals)",
          len(body.get("sales", {}).get("weekly", [])) == 12
          and body["sales"]["total_units"] == 2, body.get("sales"))

    # ---- round 8 (2026-09-24): snapshots + hover-diffs + daily sales ----
    from sqlalchemy import select as sa_select
    from app.models import StockSnapshot
    select_snaps = sa_select(StockSnapshot).where(
        StockSnapshot.sku == "ASK-M54-OAG")
    bd_a = {"available": 3, "committed": 1, "on_hand": 5, "unavailable": 1}
    with patch("app.shopify.get_quantity_breakdown", return_value=bd_a):
        cl.get("/api/products/ASK-M54-OAG/stock-breakdown")
    with Session(get_engine()) as s:
        n1 = len(s.scalars(select_snaps).all())
    check("identical breakdown reads store ONE snapshot (deduped)",
          n1 == 1, n1)
    bd_b = {"available": 4, "committed": 1, "on_hand": 6, "unavailable": 1}
    with patch("app.shopify.get_quantity_breakdown", return_value=bd_b):
        r = cl.get("/api/products/ASK-M54-OAG/stock-breakdown")
    with Session(get_engine()) as s:
        n2 = len(s.scalars(select_snaps).all())
    check("a changed breakdown stores a second snapshot", n2 == 2, n2)
    body = r.json()
    chs = body["inventory_changes"]
    check("every change carries before/after bucket estimates",
          chs and all(
              set(c.get("before", {})) == set(c.get("after", {}))
              == {"available", "committed", "on_hand", "unavailable"}
              for c in chs), chs[:2])
    by_kind = {c["kind"]: c for c in chs}
    sold_c, man_c = by_kind.get("sold"), by_kind.get("manual")
    check("sold hover-diff: committed and on-hand both step by the units",
          sold_c
          and sold_c["after"]["on_hand"] - sold_c["before"]["on_hand"] == -2
          and sold_c["after"]["committed"] - sold_c["before"]["committed"] == -2
          and sold_c["after"]["available"] == sold_c["before"]["available"],
          sold_c)
    check("manual hover-diff: on-hand and available step, committed holds",
          man_c
          and man_c["after"]["on_hand"] - man_c["before"]["on_hand"] == 2
          and man_c["after"]["available"] - man_c["before"]["available"] == 2
          and man_c["after"]["committed"] == man_c["before"]["committed"],
          man_c)
    daily = body["sales"].get("daily")
    check("sales carry the per-day series for the graph dropdowns",
          daily and sum(u for _, u in daily) == body["sales"]["total_units"],
          daily)
    appjs = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))),
        "app", "static", "app.js"), encoding="utf-8").read()
    check("round-8 client surfaces are in app.js "
          "(Locate button, Name/SKU toggle, Chart.js pane)",
          "prow__locate" in appjs and "lab-desc-mode" in appjs
          and "pship-chart" in appjs and "pship-duration" in appjs, "")
    check("Chart.js is vendored and wired into the page",
          "vendor/chart.umd.min.js" in open(os.path.join(
              os.path.dirname(os.path.dirname(os.path.dirname(
                  os.path.abspath(__file__)))),
              "app", "templates", "index.html"), encoding="utf-8").read()
          and os.path.getsize(os.path.join(
              os.path.dirname(os.path.dirname(os.path.dirname(
                  os.path.abspath(__file__)))),
              "app", "static", "vendor", "chart.umd.min.js")) > 100000, "")

    # ---- F9152B regression (Nick, 2026-09-24): audit sold is WINDOWED --
    from app.models import RfidAssignment as RA, BinMapEntry as BME
    with Session(get_engine()) as s:
        s.add(BME(sku="EYEP-9", product_title="Eyepiece 9mm", bin="I9-1",
                  qty=0, shopify_variant_id="t:e9"))
        for i, e in enumerate(("E1A", "E1B", "E1C")):
            s.add(RA(rfid_id=e, shopify_variant_id="t:e9",
                     product_title="Eyepiece 9mm", sku="EYEP-9",
                     bin_location="I9-1",
                     assigned_at=datetime(2026, 8, 6, tzinfo=timezone.utc)))
        # one sale BEFORE the pool existed (must not count), two after
        s.add(SoldRecord(order_id="gid://shopify/Order/70", sku="EYEP-9",
                         quantity=1,
                         fulfilled_at=datetime(2026, 7, 1, tzinfo=timezone.utc)))
        s.add(SoldRecord(order_id="gid://shopify/Order/71", sku="EYEP-9",
                         quantity=1,
                         fulfilled_at=datetime(2026, 8, 12, tzinfo=timezone.utc)))
        s.add(SoldRecord(order_id="gid://shopify/Order/72", sku="EYEP-9",
                         quantity=1,
                         fulfilled_at=datetime(2026, 9, 2, tzinfo=timezone.utc)))
        s.commit()
    r = cl.post("/api/bins/I9-1/check", json={"epcs": [], "skus": []})
    row = next(x for x in r.json()["items"] if x["sku"] == "EYEP-9")
    check("audit row: snapshot 0 on-hand, 3 records, WINDOWED sold of 2",
          row["expected_qty"] == 0 and row["units_here"] == 3
          and row["sold_unretired"] == 2, row)
    check("no pickup info reads as zero, never an error",
          row.get("pickup_pending") == 0 and row.get("pickup_orders") == [],
          row)

    # ---- F9168A regression (Nick, 2026-09-24): ready-for-pickup --------
    # Two boxes staged at the desk: no fulfillment, no ledger row, tags
    # silent. The audit row must carry the open-pickup counts so the UI
    # can explain the silence instead of flagging shrinkage.
    with Session(get_engine()) as s:
        s.add(BME(sku="PICKUP-1", product_title="Pickup Scope", bin="I9-2",
                  qty=2, shopify_variant_id="t:pk"))
        for e in ("P1A", "P1B"):
            s.add(RA(rfid_id=e, shopify_variant_id="t:pk",
                     product_title="Pickup Scope", sku="PICKUP-1",
                     bin_location="I9-2",
                     assigned_at=datetime(2026, 9, 1, tzinfo=timezone.utc)))
        s.commit()
    with patch("app.main._pickup_pending_map", return_value={
            "PICKUP-1": {"qty": 2, "orders": ["#50894", "#50895"]}}):
        r = cl.post("/api/bins/I9-2/check", json={"epcs": [], "skus": []})
    row = next(x for x in r.json()["items"] if x["sku"] == "PICKUP-1")
    check("pickup row: 2 silent records explained by 2 pickup-pending",
          row["expected_qty"] == 2 and row["units_here"] == 2
          and row["detected_units"] == 0 and row["pickup_pending"] == 2
          and row["pickup_orders"] == ["#50894", "#50895"], row)
    appjs = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))),
        "app", "static", "app.js"), encoding="utf-8").read()
    check("audit UI reads the pickup explanation (chip + lower guard)",
          "binAuditPickupNote" in appjs and "pickup_pending" in appjs
          and "pickupExplains" in appjs, "")

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("ALL PASS")
