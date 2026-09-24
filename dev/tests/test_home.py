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

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("ALL PASS")
