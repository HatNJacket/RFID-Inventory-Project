"""The shared locate list: web queues a product (phist panel), the C72's
LOCATE tab lists it (with live tag context) and either side removes it.
Adds are idempotent per SKU (case-insensitive); every add/remove logs a
local-only History event.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_locq_test.db")
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
    from app.models import RfidAssignment

    with S(get_engine()) as s:
        s.add(RfidAssignment(rfid_id="AAAA0000000000000000000A",
                             shopify_variant_id="gid://v/1", sku="S20300",
                             product_title="Stuck Scope",
                             bin_location="G4-4"))
        s.add(RfidAssignment(rfid_id="AAAA0000000000000000000B",
                             shopify_variant_id="gid://v/1", sku="S20300",
                             product_title="Stuck Scope",
                             bin_location="G4-4"))
        s.commit()

    # Empty to start.
    r = cl.get("/api/locate-queue")
    check("empty list to start", r.status_code == 200
          and r.json()["entries"] == [], r.text)

    # Add with label + worker.
    r = cl.post("/api/locate-queue", json={
        "sku": "S20300", "label": "Stuck Scope", "worker": "Nick"})
    check("add returns 201 + id", r.status_code == 201
          and r.json()["id"] > 0 and r.json()["already"] is False, r.text)
    eid = r.json()["id"]

    # Re-add (different case) is a no-op, not a duplicate.
    r = cl.post("/api/locate-queue", json={"sku": "s20300"})
    check("re-add is idempotent", r.json()["already"] is True
          and r.json()["id"] == eid, r.text)
    r = cl.get("/api/locate-queue")
    ent = r.json()["entries"]
    check("one entry after double add", len(ent) == 1, r.text)
    check("entry carries live tag context",
          ent[0]["tag_count"] == 2 and ent[0]["bins"] == ["G4-4"]
          and ent[0]["added_by"] == "Nick"
          and ent[0]["label"] == "Stuck Scope", str(ent))

    # A SKU with no tags still lists (tag_count 0) — the walk may still
    # be worth it, and the gun says "no tags on file" on pick.
    r = cl.post("/api/locate-queue", json={"sku": "NOTAGS-1"})
    check("tagless SKU queues too", r.status_code == 201, r.text)
    r = cl.get("/api/locate-queue")
    ent = r.json()["entries"]
    check("newest first", len(ent) == 2 and ent[0]["sku"] == "NOTAGS-1"
          and ent[0]["tag_count"] == 0, str(ent))

    # History: the add shows on the product's paper trail, local-only.
    r = cl.get("/api/product-history?term=S20300")
    evs = [e for e in r.json()["events"] if e["type"] == "locate-list"]
    check("add logged to product history", len(evs) == 1
          and evs[0]["worker"] == "Nick"
          and evs[0]["shopify"] is False, str(evs))

    # Remove from the gun's side.
    r = cl.delete(f"/api/locate-queue/{eid}?worker=C72-gun")
    check("remove ok", r.status_code == 200
          and r.json()["sku"] == "S20300", r.text)
    r = cl.get("/api/locate-queue")
    check("removed entry gone",
          [e["sku"] for e in r.json()["entries"]] == ["NOTAGS-1"], r.text)

    # Double-remove is a clean 404, not a 500.
    r = cl.delete(f"/api/locate-queue/{eid}")
    check("double remove -> 404", r.status_code == 404, r.text)

    # Remove logged too (2 locate-list events on the SKU now).
    r = cl.get("/api/product-history?term=S20300")
    evs = [e for e in r.json()["events"] if e["type"] == "locate-list"]
    check("remove logged with worker", len(evs) == 2
          and any(e["worker"] == "C72-gun" for e in evs), str(evs))

    # Site History feed shows the events under the locate-list type.
    r = cl.get("/api/history?limit=50")
    types = [e.get("type") for e in r.json()["events"]]
    check("history feed carries locate-list",
          "locate-list" in types, str(types[:10]))

    # After the product's tags move bins, the list reflects it live.
    with S(get_engine()) as s:
        for a in s.query(RfidAssignment).filter_by(sku="S20300"):
            a.bin_location = "H1-1"
        s.commit()
    cl.post("/api/locate-queue", json={"sku": "S20300"})
    r = cl.get("/api/locate-queue")
    mine = [e for e in r.json()["entries"] if e["sku"] == "S20300"][0]
    check("bins are live, not a snapshot", mine["bins"] == ["H1-1"],
          str(mine))

print()
sys.exit(1 if fails else 0)
