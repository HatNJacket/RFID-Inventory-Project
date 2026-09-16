"""Open-box return with the box IN HAND (Nick, 2026-09-16): scanning
the old tag while filing unpairs/flips it on the spot - live tags
retire as replaced, presumed-sold tombstones flip to replaced - and
NO watch opens (the unit's tag is accounted for). The twin
find-or-create and the -O label print path are untouched.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_obxpeel_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

LIVE   = "0B0E000000000000000000A1"   # still live on the returned box
SOLD   = "0B0E000000000000000000A2"   # already presumed-sold
OTHER  = "0B0E000000000000000000A3"   # live tag of a DIFFERENT product

def fake_draft(title, sku, barcode, bin_value):
    return {"product_gid": "gid://shopify/Product/700",
            "variant_gid": "gid://shopify/ProductVariant/701",
            "sku": sku, "barcode": barcode, "title": title}

with patch("app.main.oneleft"), \
     patch("app.main.shopify.find_sku_listing", return_value=None), \
     patch("app.main.shopify.create_draft_listing",
           side_effect=fake_draft):
  with TestClient(app) as cl:
    from sqlalchemy import select
    from sqlalchemy.orm import Session as S
    from app.database import get_engine
    from app.models import (OpenboxReturn, RetiredTag, ReviewTask,
                            RfidAssignment)

    with S(get_engine()) as s:
        s.add(RfidAssignment(rfid_id=LIVE, shopify_variant_id="t:1",
                             product_title="Peel Test", sku="PEEL-1",
                             bin_location="B1-1", condition="used"))
        s.add(RetiredTag(rfid_id=SOLD, sku="PEEL-2",
                         product_title="Peel Test 2",
                         kind="presumed-sold"))
        s.add(RfidAssignment(rfid_id=OTHER, shopify_variant_id="t:3",
                             product_title="Other", sku="OTHER-1",
                             bin_location="B1-2"))
        s.commit()

    # ---- guards -------------------------------------------------------
    r = cl.post("/api/openbox-returns", json={
        "sku": "PEEL-1", "create_draft": True, "peel_old": True})
    check("peel without an EPC refused 422", r.status_code == 422,
          r.text[:200])
    r = cl.post("/api/openbox-returns", json={
        "sku": "PEEL-1", "create_draft": True, "peel_old": True,
        "epc": OTHER})
    check("another product's live tag refused 422",
          r.status_code == 422 and "OTHER-1" in r.text, r.text[:200])

    # ---- live tag in hand: unpair now, no watch -----------------------
    r = cl.post("/api/openbox-returns", json={
        "sku": "PEEL-1", "product_title": "Peel Test",
        "create_draft": True, "watch": True, "peel_old": True,
        "epc": LIVE, "created_by": "Nick"})
    d = r.json()
    check("in-hand filing accepted", r.status_code == 201, r.text[:250])
    check("old tag named unpaired in the answer",
          "unpaired" in (d.get("old_tag") or ""), d)
    check("no watch opened despite watch:true",
          d.get("return") is None and "No watch needed" in d["message"], d)
    check("twin draft created", d.get("created_listing") is True, d)
    with S(get_engine()) as s:
        live = s.scalar(select(RfidAssignment).where(
            RfidAssignment.rfid_id == LIVE))
        rt = s.scalar(select(RetiredTag).where(
            RetiredTag.rfid_id == LIVE))
        check("live tag gone from the active table", live is None, live)
        check("retired as replaced with the peel note",
              rt is not None and rt.kind == "replaced"
              and "peeled" in (rt.note or ""), rt)
        check("condition carried onto the tombstone",
              rt is not None and rt.condition == "used", rt)
        check("no OpenboxReturn row, no task",
              s.scalar(select(OpenboxReturn)) is None
              and s.scalar(select(ReviewTask).where(
                  ReviewTask.category == "openbox-return")) is None, "")

    # ---- presumed-sold tag in hand: kind flips ------------------------
    r = cl.post("/api/openbox-returns", json={
        "sku": "PEEL-2", "product_title": "Peel Test 2",
        "create_draft": True, "peel_old": True, "epc": SOLD,
        "created_by": "Nick"})
    d = r.json()
    check("retired-tag filing accepted", r.status_code == 201,
          r.text[:250])
    check("answer says already retired, marked peeled",
          "already retired" in (d.get("old_tag") or ""), d)
    with S(get_engine()) as s:
        rt = s.scalar(select(RetiredTag).where(
            RetiredTag.rfid_id == SOLD))
        check("presumed-sold flipped to replaced",
              rt is not None and rt.kind == "replaced", rt)

    # ---- no peel = old behavior (watch opens) -------------------------
    r = cl.post("/api/openbox-returns", json={
        "sku": "PEEL-3", "product_title": "Watcher",
        "create_draft": True, "watch": True, "created_by": "Nick"})
    d = r.json()
    check("plain filing still opens the watch",
          r.status_code == 201 and d.get("return") is not None
          and "watch open" in d["message"], d)

print()
if fails:
    print(f"{len(fails)} FAILURE(S):"); [print("  -", f) for f in fails]
    sys.exit(1)
print("ALL PASS")
