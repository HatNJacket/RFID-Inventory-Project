"""Audit sessions: named, resumable audits (Audits tab hub). A session
bundles a scope — bins to walk-scan, or a slice of the 1-left queue —
and tracks per-item completion. Local records only; 1-left items tick
themselves when a dashboard confirm for their SKU lands after the
session opened.
"""
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
os.environ["ONELEFT_MODE"] = "confirm"
os.environ["ONELEFT_URL"] = "http://oneleft.test/api/api"
db = os.path.join(tempfile.gettempdir(), "rfid_audsess_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")

from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as S
from app.main import app
from app.database import get_engine
from app.models import BinMapEntry, OneLeftCheck
import app.main as M

fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

PENDING = [
    {"sku": "CEL-1", "product_title": "Celestron thing", "vendor": "Celestron",
     "stock_bin": "I1-1", "barcode": "", "detected_date": "2026-08-01T00:00:00+00:00",
     "current_stock": {"available": 1, "on_hand": 1}},
    {"sku": "CEL-2", "product_title": "Other Celestron thing", "vendor": "Celestron",
     "stock_bin": "", "barcode": "", "detected_date": "2026-08-01T00:00:00+00:00",
     "current_stock": "?"},
    {"sku": "ZWO-1", "product_title": "ZWO thing", "vendor": "ZWO",
     "stock_bin": "B2-1", "barcode": "", "detected_date": "2026-08-01T00:00:00+00:00",
     "current_stock": {"available": 1, "on_hand": 1}},
]

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_on_hand", return_value=None), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.oneleft._get", return_value={"success": True,
                                             "count": len(PENDING),
                                             "items": PENDING}), \
     patch("app.oneleft._post", return_value={"success": True}):
  M._maybe_refresh_bin_map = lambda *a, **k: False
  with TestClient(app) as cl:
    with S(get_engine()) as s:
        for b in ("I1-1", "I1-2", "B2-1"):
            s.add(BinMapEntry(sku=f"P-{b}", product_title=f"Product {b}",
                              bin=b, qty=1))
        s.commit()

    # ---- bins session: rack prefix + explicit bin, deduped ------------
    r = cl.post("/api/audit-sessions", json={
        "name": "Rack I1 walk", "kind": "bins", "rack": "I1",
        "bins": ["B2-1", "I1-1"], "worker": "Nick"})
    check("bins session created", r.status_code == 201, r.text)
    sess = r.json()
    keys = sorted(i["key"] for i in sess["items"])
    check("rack expanded + explicit bin, deduped",
          keys == ["B2-1", "I1-1", "I1-2"], str(keys))
    check("progress starts 0", sess["done"] == 0 and sess["total"] == 3,
          str(sess))
    sid = sess["id"]
    item = sess["items"][0]

    r = cl.post(f"/api/audit-sessions/{sid}/items/{item['id']}/done",
                json={"done": True, "worker": "Nick", "note": "clean"})
    check("item ticked", r.json()["done"] == 1, r.text)
    got = [i for i in r.json()["items"] if i["id"] == item["id"]][0]
    check("tick carries who and note",
          got["done_by"] == "Nick" and got["note"] == "clean", str(got))
    r = cl.post(f"/api/audit-sessions/{sid}/items/{item['id']}/done",
                json={"done": False, "worker": "Nick"})
    check("item unticked", r.json()["done"] == 0, r.text)

    # Unknown scope refused.
    r = cl.post("/api/audit-sessions", json={
        "name": "Nothing", "kind": "bins", "rack": "ZZZ9"})
    check("empty bins scope -> 422", r.status_code == 422, r.text)

    # ---- 1-left session: vendor slice + self-ticking ------------------
    r = cl.post("/api/audit-sessions", json={
        "name": "Celestron 1-left blitz", "kind": "oneleft",
        "vendor": "Celestron", "worker": "Nick"})
    check("oneleft session created", r.status_code == 201, r.text)
    ol = r.json()
    check("vendor slice only", sorted(i["key"] for i in ol["items"])
          == ["CEL-1", "CEL-2"], str(ol["items"]))
    # A dashboard confirm lands AFTER the session opened -> self-ticks.
    with S(get_engine()) as s:
        s.add(OneLeftCheck(sku="CEL-1", action="auto", employee="Steve",
                           operator="Nick", ok=True, evidence_units=1))
        s.commit()
    r = cl.get("/api/audit-sessions?status=open")
    mine = [x for x in r.json()["sessions"] if x["id"] == ol["id"]][0]
    ticked = [i for i in mine["items"] if i["key"] == "CEL-1"][0]
    check("confirmed check ticks itself", ticked["done"] is True
          and mine["done"] == 1, str(mine))
    # A failed receipt must NOT tick.
    check("unconfirmed stays open",
          [i for i in mine["items"] if i["key"] == "CEL-2"][0]["done"]
          is False, str(mine["items"]))

    # ---- finish / abandon / index filters -----------------------------
    r = cl.post(f"/api/audit-sessions/{sid}/finish", json={"worker": "Nick"})
    check("finish with open items allowed",
          r.status_code == 200 and r.json()["status"] == "done", r.text)
    r = cl.post(f"/api/audit-sessions/{sid}/finish", json={})
    check("double finish -> 409", r.status_code == 409, r.text)
    r = cl.post(f"/api/audit-sessions/{ol['id']}/abandon",
                json={"worker": "Nick"})
    check("abandon works", r.json()["status"] == "abandoned", r.text)
    open_now = cl.get("/api/audit-sessions?status=open").json()["sessions"]
    done_now = cl.get("/api/audit-sessions?status=done").json()["sessions"]
    check("index filters split open vs finished",
          len(open_now) == 0 and len(done_now) == 2,
          f"open={len(open_now)} done={len(done_now)}")

    # ---- history --------------------------------------------------------
    ev = cl.get("/api/history?limit=100").json()["events"]
    aud = [e for e in ev if e["type"] == "audit-session"]
    check("history carries session start + end events", len(aud) >= 4,
          str([(e['title'], e['detail']) for e in aud]))

print()
sys.exit(1 if fails else 0)
