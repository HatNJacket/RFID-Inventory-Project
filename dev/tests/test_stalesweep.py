"""Stale-sweep guard (Nick, 2026-09-09, the ASI676MC): an on-hand
raise or lower justified by sweep evidence is refused when Shopify's
stock moved AFTER the sweep ran - an 11AM sweep re-raised a count a
1PM sale had already taken down. Writes with no sweep evidence (a
human counting the shelf now) pass; an unreadable stamp never blocks.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ["SHOPIFY_WRITE_MODE"]="verify_onhand,verify_onhand_lower"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_stalesweep_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

SWEEP_11AM = "2026-09-09T15:00:00Z"   # 11AM Toronto = 15:00 UTC
SALE_1PM   = "2026-09-09T17:00:00Z"   # the sale that made it stale

writes = []
def fake_set(sku, qty):
    writes.append((sku, qty))
    return 1  # the before value

with patch("app.shopify.get_on_hand", return_value=1), \
     patch("app.shopify.set_on_hand", side_effect=fake_set), \
     patch("app.shopify.get_onhand_updated_at",
           return_value=SALE_1PM) as stamp, \
     patch("app.main._maybe_refresh_bin_map", return_value=False), \
     patch("app.main.oneleft"):
  with TestClient(app) as cl:
    # ---- raise from a stale sweep is refused ---------------------------
    r = cl.post("/api/onhand-updates", json={
        "sku": "ZWO ASI676MC", "new_qty": 2, "confirmed": True,
        "changed_by": "Nick", "sweep_at": SWEEP_11AM})
    check("raise from an 11AM sweep refused after a 1PM stock move",
          r.status_code == 409 and "STALE SWEEP" in r.text
          and writes == [], (r.status_code, r.text[:200], writes))

    # ---- lower from a stale sweep is refused, even unconfirmed --------
    r = cl.post("/api/onhand-updates/lower", json={
        "sku": "ZWO ASI676MC", "bin_name": "F1-2", "new_qty": 0,
        "changed_by": "Nick", "sweep_at": SWEEP_11AM})
    check("lower preview from a stale sweep refused too",
          r.status_code == 409 and "STALE SWEEP" in r.text,
          (r.status_code, r.text[:200]))

    # ---- a FRESH sweep passes ------------------------------------------
    r = cl.post("/api/onhand-updates", json={
        "sku": "ZWO ASI676MC", "new_qty": 2, "confirmed": True,
        "changed_by": "Nick", "sweep_at": "2026-09-09T18:30:00Z"})
    check("a sweep newer than the stock move writes normally",
          r.status_code == 201 and writes == [("ZWO ASI676MC", 2)],
          (r.status_code, r.text[:200], writes))

    # ---- no sweep evidence = a live human count: no guard --------------
    writes.clear()
    r = cl.post("/api/onhand-updates", json={
        "sku": "ZWO ASI676MC", "new_qty": 2, "confirmed": True,
        "changed_by": "Nick"})
    check("a typed count with no sweep passes",
          r.status_code == 201 and len(writes) == 1,
          (r.status_code, writes))

    # ---- fail-open: guard protects against KNOWN newer truth ----------
    writes.clear()
    stamp.side_effect = RuntimeError("Shopify hiccup")
    r = cl.post("/api/onhand-updates", json={
        "sku": "ZWO ASI676MC", "new_qty": 2, "confirmed": True,
        "changed_by": "Nick", "sweep_at": SWEEP_11AM})
    check("an unreadable stamp never blocks the write",
          r.status_code == 201 and len(writes) == 1,
          (r.status_code, writes))
    stamp.side_effect = None
    stamp.return_value = None
    writes.clear()
    r = cl.post("/api/onhand-updates", json={
        "sku": "ZWO ASI676MC", "new_qty": 2, "confirmed": True,
        "changed_by": "Nick", "sweep_at": SWEEP_11AM})
    check("a missing stamp never blocks the write",
          r.status_code == 201 and len(writes) == 1,
          (r.status_code, writes))

print()
if fails:
    print(f"{len(fails)} FAILURE(S):"); [print("  -", f) for f in fails]
    sys.exit(1)
print("ALL PASS")
