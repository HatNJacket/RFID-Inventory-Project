"""Per-box conditions (Nick, 2026-09-16): every physical unit can carry
a condition (open-box, used, damaged, needs-parts, display,
safety-stock; absent = good). Set via /api/tags/{epc}/condition,
History-logged, carried through retire -> unretire and release ->
re-apply, and auto-seeded when a tag pairs to a -O twin. Semantics are
inert: no count/audit math reads it yet.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_condition_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

T1 = "C0DE000000000000000000A1"
T2 = "C0DE000000000000000000A2"   # retire/unretire round trip
T3 = "C0DE000000000000000000A3"   # release/re-apply round trip
OB = "C0DE000000000000000000B1"   # pairs to a -O twin

with patch("app.main.oneleft"):
  with TestClient(app) as cl:
    from sqlalchemy import select
    from sqlalchemy.orm import Session as S
    from app.database import get_engine
    from app.models import ReleasedTag, RetiredTag, RfidAssignment

    for epc in (T1, T2, T3):
        cl.post("/api/rfid-assignments", json={
            "rfid_id": epc, "shopify_variant_id": "t:1",
            "product_title": "Cond Test", "sku": "COND-1",
            "barcode": "111", "bin_location": "A1-1"})

    # ---- set / clear / validate --------------------------------------
    r = cl.post(f"/api/tags/{T1}/condition",
                json={"condition": "damaged", "worker": "Nick"})
    check("condition set", r.status_code == 200
          and r.json()["condition"] == "damaged", r.text[:200])
    r = cl.get(f"/api/tag-info/{T1}").json()
    check("tag-info carries condition + label",
          r.get("condition") == "damaged"
          and r.get("condition_label") == "Damaged"
          and r["assignment"]["condition"] == "damaged", r)
    check("tag-info notes name the condition",
          any("Damaged" in n for n in r.get("notes", [])), r.get("notes"))
    r = cl.post(f"/api/tags/{T1}/condition",
                json={"condition": "damaged"})
    check("same value is a no-op answer", "Already" in r.json()["message"],
          r.text[:200])
    r = cl.post(f"/api/tags/{T1}/condition", json={"condition": "good"})
    check("good clears back to default (NULL)",
          r.status_code == 200 and r.json()["condition"] is None,
          r.text[:200])
    r = cl.post(f"/api/tags/{T1}/condition", json={"condition": "shiny"})
    check("unknown condition refused 422", r.status_code == 422,
          r.text[:200])
    r = cl.post(f"/api/tags/{'F00D000000000000000000FF'}/condition",
                json={"condition": "used"})
    check("unknown EPC refused 404", r.status_code == 404, r.text[:200])

    h = cl.get("/api/history?limit=10").json()["events"]
    ev = [x for x in h if x["type"] == "condition-set"]
    check("history logs condition-set events", len(ev) >= 2, h[:3])

    # ---- retire -> unretire carries it -------------------------------
    cl.post(f"/api/tags/{T2}/condition", json={"condition": "used"})
    r = cl.post("/api/assignments/retire", json={
        "epcs": [T2], "kind": "replaced", "changed_by": "Nick"})
    check("retire accepted", r.status_code == 200, r.text[:200])
    with S(get_engine()) as s:
        rt = s.scalar(select(RetiredTag).where(RetiredTag.rfid_id == T2))
        check("retired row carries the condition",
              rt is not None and rt.condition == "used", rt)
    r = cl.post("/api/assignments/unretire", json={
        "epcs": [T2], "changed_by": "Nick"})
    check("unretire accepted", r.status_code == 200, r.text[:200])
    with S(get_engine()) as s:
        back = s.scalar(select(RfidAssignment).where(
            RfidAssignment.rfid_id == T2))
        check("unretired tag still Used",
              back is not None and back.condition == "used", back)

    # ---- -O pairing seeds open-box -----------------------------------
    cl.post("/api/rfid-assignments", json={
        "rfid_id": OB, "shopify_variant_id": "t:9",
        "product_title": "Cond Test - Open Box", "sku": "COND-1-O",
        "barcode": "111-O", "bin_location": "A1-1"})
    with S(get_engine()) as s:
        ob = s.scalar(select(RfidAssignment).where(
            RfidAssignment.rfid_id == OB))
        check("pairing to a -O twin seeds open-box",
              ob is not None and ob.condition == "open-box", ob)

print()
if fails:
    print(f"{len(fails)} FAILURE(S):"); [print("  -", f) for f in fails]
    sys.exit(1)
print("ALL PASS")
