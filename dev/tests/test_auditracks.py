"""GET /api/audit/racks (2026-09-29): the C72 Audit landing's feed - the
scored queue rolled up per rack in the web's order (overdue by drift,
then fresh by drift), open 1-left checks per rack, open walk sessions,
and every rack's bins in natural order for the gun's arrows.
"""
import os, sys, tempfile
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_auditracks_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import app.main as M
from app.main import app
from app.database import get_engine
from app.models import Batch, BinAudit, BinMapEntry, RfidAssignment
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

PENDING = {"ok": True, "items": [
    {"sku": "P-F2-1", "stock_bin": "F2-1"},
    {"sku": "P-F2-2", "stock_bin": ""},       # no bin: resolves via map
    {"sku": "NOWHERE", "stock_bin": ""},
]}

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.oneleft.get_pending", return_value=PENDING):
  M._maybe_refresh_bin_map = lambda *a, **k: False
  with TestClient(app) as cl:
    now = datetime.now(timezone.utc)
    with Session(get_engine()) as s:
        # Three racks tagged to completion: I1 (never audited, big
        # drift), F2 (audited long ago, checks open), T1 (audited
        # yesterday, small drift). K4 is in the map but never tagged.
        for b, sku, qty in [("I1-1","A1",5), ("I1-2","A2",3), ("I1-10","A3",2),
                            ("F2-1","P-F2-1",2), ("F2-2","P-F2-2",1),
                            ("T1-1","T1",4), ("K4-1","K1",1)]:
            s.add(BinMapEntry(sku=sku, product_title=sku, bin=b, qty=qty,
                              shopify_variant_id="t:"+sku))
        for b in ("I1-1","I1-2","I1-10","F2-1","F2-2","T1-1"):
            s.add(Batch(bin_name=b, status="done", completed_at=now))
        # one tag on T1 only -> T1 drift 3, the rest drift = their qty
        s.add(RfidAssignment(rfid_id="E1", shopify_variant_id="t:T1",
                             product_title="T1", sku="T1", bin_location="T1-1"))
        s.add(BinAudit(bin="F2-1", audited_at=now - timedelta(days=40),
                       audited_by="Nick", baseline="{}"))
        s.add(BinAudit(bin="F2-2", audited_at=now - timedelta(days=40),
                       audited_by="Nick", baseline="{}"))
        s.add(BinAudit(bin="T1-1", audited_at=now - timedelta(days=1),
                       audited_by="Nick", baseline="{}"))
        s.commit()

    r = cl.get("/api/audit/racks")
    check("racks endpoint answers", r.status_code == 200, r.text)
    d = r.json()
    rec = [x["rack"] for x in d["recommended"]]
    check("overdue racks first, biggest drift first",
          rec == ["I1", "F2"], rec)
    check("up-to-date racks follow", [x["rack"] for x in d["fresh"]] == ["T1"],
          d["fresh"])
    i1 = d["recommended"][0]
    check("a rack carries drift, bins, mismatches and never-audited",
          i1["score"] == 10 and i1["bins"] == 3 and i1["mismatched"] == 3
          and i1["any_never"] and i1["last_audited_at"] is None, i1)
    check("bins come in natural order for the arrows",
          i1["bin_names"] == ["I1-1", "I1-2", "I1-10"], i1["bin_names"])
    f2 = d["recommended"][1]
    check("open 1-left checks count per rack (map fallback included)",
          f2["checks_open"] == 2 and i1["checks_open"] == 0, d["checks_open"])
    check("worst bin named", i1["worst_bin"] == "I1-1", i1)
    check("every mapped rack resolves, tagged or not",
          set(d["racks"]) == {"I1", "F2", "T1", "K4"}
          and d["racks"]["K4"] == ["K4-1"], d["racks"])

    # An open walk session shows on its rack with its next bin.
    sess = cl.post("/api/audit-sessions", json={
        "name": "Rack I1", "kind": "bins", "rack": "I1", "worker": "Nick"}).json()
    cl.post(f"/api/audit-sessions/{sess['id']}/items/{sess['items'][0]['id']}/done",
            json={"done": True, "worker": "Nick"})
    d = cl.get("/api/audit/racks").json()
    i1 = d["recommended"][0]
    check("an open walk rides on its rack with progress + next bin",
          bool(i1["session"]) and i1["session"]["done"] == 1
          and i1["session"]["total"] == 3
          # session items sort as text, so I1-10 follows I1-1
          and i1["session"]["next"] == "I1-10",
          i1["session"])

    # The dashboard being down costs the chips, nothing else.
    with patch("app.oneleft.get_pending", side_effect=RuntimeError("down")):
        r = cl.get("/api/audit/racks")
    check("a dead 1-left dashboard is fail-soft",
          r.status_code == 200 and r.json()["checks_open"] == {}, r.text[:200])

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("ALL PASS")
