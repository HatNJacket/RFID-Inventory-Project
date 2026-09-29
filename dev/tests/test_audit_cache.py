"""The scored audit queue cache (2026-09-29: the C72 landing took 8 s).
Prod-only in real life (sqlite skips it); forced on here to prove it
serves the last computation, re-scores after a LOG AUDIT, refreshes in
the background once stale, and that a saved sweep is stamped once.
"""
import os, sys, tempfile, time
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_audit_cache_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import app.main as M
from app.main import app
from app.database import get_engine
from app.models import Batch, BinMapEntry, RfidAssignment
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

def score(cl, b):
    d = cl.get("/api/audit/bins").json()
    return {x["bin"]: x["score"] for x in d["bins"]}.get(b)

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}):
  M._maybe_refresh_bin_map = lambda *a, **k: False
  M._pickup_pending_map = lambda: {}
  with TestClient(app) as cl:
    M._audit_q_enabled = lambda: True
    M._audit_q_invalidate()
    now = datetime.now(timezone.utc)
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="A1", product_title="A", bin="I1-1", qty=5,
                          shopify_variant_id="t:A1"))
        s.add(Batch(bin_name="I1-1", status="done", completed_at=now))
        s.commit()
    check("first read computes", score(cl, "I1-1") == 5)
    with Session(get_engine()) as s:
        for n in range(2):
            s.add(RfidAssignment(rfid_id=f"E00000000000000000000C0{n}",
                                 shopify_variant_id="t:A1", product_title="A",
                                 sku="A1", bin_location="I1-1"))
        s.commit()
    check("a fresh cache answers from the last computation",
          score(cl, "I1-1") == 5)
    racks = cl.get("/api/audit/racks").json()
    check("the landing rolls up the cached queue",
          racks["recommended"][0]["score"] == 5, racks["recommended"])

    # A LOG AUDIT re-scores the next read exactly.
    r = cl.post("/api/bins/I1-1/audit-complete",
                json={"epcs": [], "worker": "Nick"})
    check("a sign-off drops the cached queue",
          r.status_code == 201 and M._audit_q["data"] is None,
          (r.status_code, r.text[:120]))
    d = cl.get("/api/audit/bins").json()
    i1 = [x for x in d["bins"] if x["bin"] == "I1-1"][0]
    check("  ...so the next read is exact (the sign-off shows)",
          i1["last_audited_by"] == "Nick", i1.get("last_audited_by"))

    # Stale: served at once, refreshed behind.
    M._audit_q["at"] = time.time() - M._AUDIT_Q_FRESH - 5
    stale_at = M._audit_q["at"]
    cl.get("/api/audit/bins")
    for _ in range(50):
        if M._audit_q["at"] != stale_at and not M._audit_q["running"]:
            break
        time.sleep(0.1)
    check("a stale cache refreshes itself in the background",
          M._audit_q["at"] > stale_at)

    # A saved sweep is stamped once, however many bins check it.
    cap = cl.post("/api/epc-captures", json={
        "device": "C72", "epcs": ["E00000000000000000000C00"]}).json()
    calls = []
    real = M._stamp_heard
    M._stamp_heard = lambda s, e: calls.append(1) or real(s, e)
    for b in ("I1-1", "I1-2", "I1-3"):
        cl.post(f"/api/bins/{b}/check", json={"capture_id": cap["id"]})
    M._stamp_heard = real
    # Creating the capture stamped its tags (2026-09-29, append work):
    # checking it against any number of bins stamps nothing more.
    check("a capture checked against three bins never re-stamps",
          len(calls) == 0, len(calls))

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("ALL PASS")
