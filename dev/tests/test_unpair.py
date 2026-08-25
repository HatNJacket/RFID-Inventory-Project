"""Manual unpair from the Inventory tab's product panel (Nick,
2026-08-25): a tag fell off (and was bad anyway), the sticker is gone,
and with a single unit there is no audit to run. The product panel now
lists the live tags (`tags` in /api/product-history) and each row can be
retired as dead through the existing retire endpoint - tombstone kept,
History row with one-click undo, Shopify untouched."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_unpair_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_engine
from app.models import RfidAssignment, RetiredTag
from sqlalchemy.orm import Session
from sqlalchemy import select
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

EPC1 = "AA000000000000000000C001"
EPC2 = "AA000000000000000000C002"

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.main._kick_orders_sync_soon"):
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        s.add(RfidAssignment(
            rfid_id=EPC1, shopify_variant_id="t:UNPAIR-1",
            product_title="Lone Telescope", sku="UNPAIR-1",
            bin_location="K1-1", assigned_by="Nick"))
        s.add(RfidAssignment(
            rfid_id=EPC2, shopify_variant_id="t:UNPAIR-1",
            product_title="Lone Telescope", sku="unpair-1",
            bin_location="K1-1", assigned_by="Nick"))
        s.commit()

    ph = cl.get("/api/product-history?term=UNPAIR-1").json()
    check("product panel lists the live tags",
          len(ph.get("tags", [])) == 2, ph.get("tags"))
    check("case-insensitive SKU match includes the lowercase row",
          sorted(t["epc"] for t in ph["tags"]) == [EPC1, EPC2],
          ph["tags"])
    check("tag rows carry bin and who paired them",
          ph["tags"][0]["bin"] == "K1-1"
          and ph["tags"][0]["assigned_by"] == "Nick", ph["tags"][0])
    check("tag_count agrees with the list", ph["tag_count"] == 2, ph)

    r = cl.post("/api/assignments/retire", json={
        "epcs": [EPC1], "kind": "dead", "changed_by": "Nick",
        "note": "manual unpair, Inventory tab"})
    check("manual unpair retires the tag", r.status_code == 200
          and r.json()["retired"] == [EPC1], r.text)

    ph = cl.get("/api/product-history?term=UNPAIR-1").json()
    check("the unpaired tag left the live list",
          [t["epc"] for t in ph["tags"]] == [EPC2], ph["tags"])
    check("tag_count follows", ph["tag_count"] == 1, ph)

    with Session(get_engine()) as s:
        rt = s.scalar(select(RetiredTag).where(RetiredTag.rfid_id == EPC1))
        check("a tombstone remains (future sweeps name it)",
              rt is not None and rt.kind == "dead",
              None if rt is None else rt.kind)
        check("dead retire never consumes sold-ledger units",
              rt is not None and (rt.ledger_consumed or 0) == 0,
              None if rt is None else rt.ledger_consumed)

    hist = cl.get("/api/history").json()
    ev = next((e for e in hist["events"]
               if e.get("undo", {}).get("kind") == "tag-retired"
               and e["undo"].get("epc") == EPC1), None)
    check("History shows the retire with a one-click undo", ev is not None,
          [e.get("type") for e in hist["events"]][:8])

    r = cl.post("/api/assignments/unretire",
                json={"epcs": [EPC1], "changed_by": "Nick"})
    check("undo restores the tag", r.status_code == 200, r.text)
    ph = cl.get("/api/product-history?term=UNPAIR-1").json()
    check("restored tag is back in the panel list",
          sorted(t["epc"] for t in ph["tags"]) == [EPC1, EPC2],
          ph["tags"])

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
