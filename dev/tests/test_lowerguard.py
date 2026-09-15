"""On-hand lowering past recorded sales (Nick, 2026-09-15): allowed
from audits and repeat batch tags ONCE a product has completed a batch
tagging - never on the very first. The unbacked units are shrinkage
(no ledger consumed for them); sales-backed drops behave as before.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ["SHOPIFY_WRITE_MODE"]=(
    "scan_station_only,verify_onhand,verify_onhand_lower")
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_lowerguard_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

FIRST_T1 = "FEED000000000000000000A1"   # FIRST-1: never batch tagged
PRIOR_T1 = "FEED000000000000000000B1"   # PRIOR-1: tagged in a done batch
PRIOR_T2 = "FEED000000000000000000B2"

with patch("app.main.oneleft"), \
     patch("app.main.shopify.get_on_hand", return_value=5), \
     patch("app.main.shopify.set_on_hand", return_value=5) as set_oh, \
     patch("app.main.shopify.get_shelf_on_hand", return_value=5):
  with TestClient(app) as cl:
    from sqlalchemy import select
    from sqlalchemy.orm import Session as S
    from app.database import get_engine
    from app.models import (Batch, BatchItem, RetiredTag, RfidAssignment)

    with S(get_engine()) as s:
        # PRIOR-1 went through a COMPLETED batch tagging.
        done = Batch(bin_name="G1-1", status="done", created_by="Nick")
        s.add(done); s.flush()
        s.add(BatchItem(batch_id=done.id, scanned_code="222",
                        resolved=True, sku="PRIOR-1", barcode="222",
                        product_title="Prior Tagged", qty_scanned=2,
                        bin_location="G1-1"))
        for epc in (PRIOR_T1, PRIOR_T2):
            s.add(RfidAssignment(rfid_id=epc, shopify_variant_id="t:2",
                                 product_title="Prior Tagged",
                                 sku="PRIOR-1", bin_location="G1-1"))
        # FIRST-1 has tags but NO completed batch anywhere.
        s.add(RfidAssignment(rfid_id=FIRST_T1, shopify_variant_id="t:1",
                             product_title="First Timer", sku="FIRST-1",
                             bin_location="G1-2"))
        s.commit()

    # ---- first tagging: unbacked drop still refused -------------------
    r = cl.post("/api/onhand-updates/lower", json={
        "sku": "FIRST-1", "bin_name": "G1-2", "new_qty": 3,
        "epcs": [], "changed_by": "Nick", "confirmed": True})
    check("first-tagging unbacked drop refused (422)",
          r.status_code == 422
          and "never completed a batch tagging" in r.text,
          (r.status_code, r.text[:200]))

    # ---- prior-tagged: unbacked drop allowed as shrinkage -------------
    r = cl.post("/api/onhand-updates/lower", json={
        "sku": "PRIOR-1", "bin_name": "G1-1", "new_qty": 3,
        "epcs": [PRIOR_T1], "changed_by": "Nick", "confirmed": False})
    check("unconfirmed prior-tagged drop 409s and names shrinkage",
          r.status_code == 409 and "shrinkage" in r.text
          and "NO backing sale" in r.text, (r.status_code, r.text[:250]))

    r = cl.post("/api/onhand-updates/lower", json={
        "sku": "PRIOR-1", "bin_name": "G1-1", "new_qty": 3,
        "epcs": [PRIOR_T1], "changed_by": "Nick", "confirmed": True})
    d = r.json() if r.status_code == 201 else {}
    check("prior-tagged unbacked lower succeeds",
          r.status_code == 201 and d.get("retired") == [PRIOR_T1],
          (r.status_code, r.text[:250]))
    check("success message flags the shrinkage",
          "shrinkage" in d.get("message", ""), d)
    check("Shopify write went out", set_oh.called, set_oh.call_args)
    with S(get_engine()) as s:
        rt = s.scalar(select(RetiredTag).where(
            RetiredTag.rfid_id == PRIOR_T1))
        check("silent tag retired presumed-sold with 0 ledger consumed",
              rt is not None and rt.kind == "presumed-sold"
              and (rt.ledger_consumed or 0) == 0, rt)

    # ---- undo still reverses it cleanly -------------------------------
    low_id = d.get("change_id")
    r = cl.post(f"/api/onhand-updates/{low_id}/undo-lower",
                json={"changed_by": "Nick", "confirmed": True})
    check("undo-lower reverses the shrinkage write",
          r.status_code == 200, r.text[:200])
    with S(get_engine()) as s:
        back = s.scalar(select(RfidAssignment).where(
            RfidAssignment.rfid_id == PRIOR_T1))
        check("tag restored live", back is not None, back)

print()
if fails:
    print(f"{len(fails)} FAILURE(S):"); [print("  -", f) for f in fails]
    sys.exit(1)
print("ALL PASS")
