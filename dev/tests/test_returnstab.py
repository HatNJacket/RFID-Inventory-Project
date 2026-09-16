"""C72 Returns tab server side (Nick, 2026-09-16): /api/returns/tag
tells the returned box's whole story (live / retired / printed-only /
unknown, product + condition + returns-app matches), and
/api/returns/process settles the RFID side: as-new and used restore a
retired tag to live (condition cleared / set to used), unsellable and
display tombstone it. Local records only - no Shopify writes."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ["SHOPIFY_WRITE_MODE"]="returns_restock"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
os.environ.pop("RETURNS_API_URL", None); os.environ.pop("RETURNS_API_TOKEN", None)
db = os.path.join(tempfile.gettempdir(), "rfid_returnstab_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_engine
from app.models import (BinMapEntry, PrintJob, RetiredTag,
                        RfidAssignment, SoldRecord)
from sqlalchemy import select
from sqlalchemy.orm import Session
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

LIVE  = "0C0E000000000000000000B1"
SOLD  = "0C0E000000000000000000B2"
PRNT  = "0C0E000000000000000000B3"
GHOST = "0C0E000000000000000000B4"

FAKE_ORDERS = [{
    "order_id": 1, "order_number": 47082, "order_name": "#47082",
    "created_at": "2026-09-10", "return_status": "requested",
    "customer": "Jane D.", "customer_email": "x@y.z",
    "return_items": [{"title": "Ret Cam", "sku": "RET-1",
                      "quantity": 1, "reason": "doesn't fit",
                      "reason_note": None, "return_status": "open"}],
}]

with patch("app.main._returns_app_orders", return_value=FAKE_ORDERS):
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        s.add(RfidAssignment(rfid_id=LIVE, shopify_variant_id="t:R1",
                             product_title="Ret Cam", sku="RET-1",
                             bin_location="G2-1"))
        s.add(RetiredTag(rfid_id=SOLD, sku="RET-1",
                         product_title="Ret Cam",
                         shopify_variant_id="t:R1",
                         bin_location="G2-1", kind="presumed-sold",
                         condition="open-box"))
        s.add(PrintJob(epc=PRNT, status="done", sku="RET-2",
                       product_title="Never Paired",
                       shopify_variant_id="t:R2",
                       bin_location="H1-1"))
        s.add(BinMapEntry(sku="RET-1", barcode="777", bin="G2-1",
                          product_title="Ret Cam", qty=3,
                          image_url="http://img/x.jpg",
                          shopify_variant_id="t:R1"))
        s.commit()

    # ---- the tag's whole story -----------------------------------------
    r = cl.get(f"/api/returns/tag/{LIVE}")
    d = r.json()
    check("live tag: state + product + bin-map enrich",
          r.status_code == 200 and d["state"] == "live"
          and d["sku"] == "RET-1" and d["barcode"] == "777"
          and d["bin"] == "G2-1" and d["image_url"], str(d)[:250])
    check("live tag: returns-app match rides along",
          len(d["matches"]) == 1
          and d["matches"][0]["order"] == "#47082"
          and d["matches"][0]["customer"] == "Jane D."
          and "customer_email" not in d["matches"][0], str(d["matches"]))
    r = cl.get(f"/api/returns/tag/{SOLD}")
    d = r.json()
    check("retired tag: state, kind and condition",
          d["state"] == "retired" and d["kind"] == "presumed-sold"
          and d["condition"] == "open-box"
          and "presumed-sold" in d["state_text"], str(d)[:250])
    r = cl.get(f"/api/returns/tag/{PRNT}")
    check("printed-only label answered honestly",
          r.json()["state"] == "printed-only"
          and r.json()["sku"] == "RET-2", r.text[:200])
    r = cl.get(f"/api/returns/tag/{GHOST}")
    check("unknown EPC answered as unknown",
          r.json()["state"] == "unknown", r.text[:200])

    # ---- process: as-new restores a retired tag ------------------------
    r = cl.post("/api/returns/process",
                json={"epc": SOLD, "action": "as-new",
                      "changed_by": "C72"})
    d = r.json()
    check("as-new accepted with the shelve-it message",
          r.status_code == 200 and "restored to live" in d["message"]
          and "home bin" in d["message"], r.text[:300])
    with Session(get_engine()) as s:
        a = s.scalar(select(RfidAssignment).where(
            RfidAssignment.rfid_id == SOLD))
        rt = s.scalar(select(RetiredTag).where(
            RetiredTag.rfid_id == SOLD))
        check("tag is live again, tombstone gone, condition cleared",
              a is not None and rt is None and a.condition is None,
              f"live={a} retired={rt}")

    # ---- process: used marks the live tag ------------------------------
    r = cl.post("/api/returns/process",
                json={"epc": SOLD, "action": "used"})
    check("used on the (now live) tag sets condition",
          r.status_code == 200, r.text[:200])
    with Session(get_engine()) as s:
        a = s.scalar(select(RfidAssignment).where(
            RfidAssignment.rfid_id == SOLD))
        check("condition is used", a.condition == "used", a.condition)

    # ---- process: unsellable tombstones a live tag ---------------------
    r = cl.post("/api/returns/process",
                json={"epc": LIVE, "action": "unsellable"})
    check("unsellable accepted", r.status_code == 200
          and "Parts" in r.json()["message"], r.text[:250])
    with Session(get_engine()) as s:
        a = s.scalar(select(RfidAssignment).where(
            RfidAssignment.rfid_id == LIVE))
        rt = s.scalar(select(RetiredTag).where(
            RetiredTag.rfid_id == LIVE))
        check("tag retired as unsellable",
              a is None and rt is not None
              and rt.kind == "unsellable", f"{a} {rt}")

    # ---- process: display re-marks an existing tombstone ---------------
    r = cl.post("/api/returns/process",
                json={"epc": LIVE, "action": "display"})
    check("display re-marks the tombstone", r.status_code == 200,
          r.text[:200])
    with Session(get_engine()) as s:
        rt = s.scalar(select(RetiredTag).where(
            RetiredTag.rfid_id == LIVE))
        check("kind display, condition display",
              rt.kind == "display" and rt.condition == "display",
              f"{rt.kind} {rt.condition}")

    # ---- guards ---------------------------------------------------------
    r = cl.post("/api/returns/process",
                json={"epc": GHOST, "action": "as-new"})
    check("unknown EPC refused 404", r.status_code == 404, r.text[:200])
    r = cl.post("/api/returns/process",
                json={"epc": SOLD, "action": "nonsense"})
    check("unknown action refused 422", r.status_code == 422,
          r.text[:200])

    # ---- restock: the box is back, on-hand goes up with it -------------
    SOLD2 = "0C0E000000000000000000B5"
    with Session(get_engine()) as s:
        s.add(RetiredTag(rfid_id=SOLD2, sku="RET-1",
                         product_title="Ret Cam",
                         shopify_variant_id="t:R1",
                         kind="presumed-sold"))
        s.commit()
    with patch("app.main.shopify.get_on_hand", return_value=5), \
         patch("app.main.shopify.set_on_hand", return_value=5):
        r = cl.post("/api/returns/process",
                    json={"epc": SOLD2, "action": "as-new",
                          "restock": True, "changed_by": "C72"})
        d = r.json()
        check("as-new + restock raises on-hand 5 -> 6",
              r.status_code == 200
              and "RET-1: 5 → 6" in (d.get("restock") or "")
              and "5 → 6" in d["message"], r.text[:350])
        fake_listing = {"shopify_variant_id": "t:RO",
                        "shopify_product_id": "p:RO",
                        "product_title": "Ret Cam - Open Box",
                        "status": "ACTIVE", "sku": "RET-1-O",
                        "barcode": None}
        with patch("app.main.shopify.find_sku_listing",
                   return_value=fake_listing):
            r = cl.post("/api/openbox-returns",
                        json={"sku": "RET-1", "create_draft": False,
                              "peel_old": True, "epc": SOLD2,
                              "restock": True, "created_by": "C72"})
            d = r.json()
            check("open-box + restock raises the TWIN's on-hand",
                  r.status_code == 201
                  and "RET-1-O: 5 → 6" in (d.get("restock") or ""),
                  r.text[:350])
    with patch("app.main.shopify.get_on_hand",
               side_effect=RuntimeError("boom")):
        with Session(get_engine()) as s:
            s.add(RetiredTag(rfid_id="0C0E000000000000000000B6",
                             sku="RET-1", product_title="Ret Cam",
                             shopify_variant_id="t:R1",
                             kind="presumed-sold"))
            s.commit()
        r = cl.post("/api/returns/process",
                    json={"epc": "0C0E000000000000000000B6",
                          "action": "as-new", "restock": True})
        d = r.json()
        check("Shopify hiccup is a note, never a rollback",
              r.status_code == 200
              and "raise it by hand" in d["message"], r.text[:350])
        with Session(get_engine()) as s:
            a = s.scalar(select(RfidAssignment).where(
                RfidAssignment.rfid_id
                == "0C0E000000000000000000B6"))
            check("RFID side still settled despite the hiccup",
                  a is not None, a)
    r = cl.post("/api/returns/process",
                json={"epc": SOLD2, "action": "used"})
    check("no restock flag = restock is null",
          r.status_code == 200 and r.json().get("restock") is None,
          r.text[:250])

    # ---- the sold-order arithmetic (Nick, 2026-09-16) ------------------
    # A returned tag whose retirement consumed a sale: restock SUCCESS
    # keeps that sale consumed (the return resolves it - no more
    # "expecting N + 1 sold" review noise); no/failed restock hands it
    # back like the plain undo always did.
    L1 = "0C0E000000000000000000B7"
    L2 = "0C0E000000000000000000B8"
    with Session(get_engine()) as s:
        s.add(SoldRecord(order_id="o1", order_name="#1", sku="RET-1",
                         quantity=2, retired=2))
        s.add(RetiredTag(rfid_id=L1, sku="RET-1", product_title="Ret Cam",
                         shopify_variant_id="t:R1", kind="presumed-sold",
                         ledger_consumed=1))
        s.add(RetiredTag(rfid_id=L2, sku="RET-1", product_title="Ret Cam",
                         shopify_variant_id="t:R1", kind="presumed-sold",
                         ledger_consumed=1))
        s.commit()
    with patch("app.main.shopify.get_on_hand", return_value=5), \
         patch("app.main.shopify.set_on_hand", return_value=5):
        r = cl.post("/api/returns/process",
                    json={"epc": L1, "action": "as-new",
                          "restock": True})
        d = r.json()
        check("restocked return keeps the sale consumed",
              r.status_code == 200
              and "stays settled by this return" in d["message"],
              r.text[:350])
    with Session(get_engine()) as s:
        row = s.scalar(select(SoldRecord).where(
            SoldRecord.order_id == "o1"))
        check("SoldRecord.retired untouched after restocked return",
              row.retired == 2, row.retired)
    r = cl.post("/api/returns/process",
                json={"epc": L2, "action": "as-new"})
    check("un-restocked return still hands the sale back",
          r.status_code == 200, r.text[:250])
    with Session(get_engine()) as s:
        row = s.scalar(select(SoldRecord).where(
            SoldRecord.order_id == "o1"))
        check("SoldRecord.retired handed back without a restock",
              row.retired == 1, row.retired)

    # ---- history got the umbrella event --------------------------------
    hist = cl.get("/api/history?limit=50").json()["events"]
    n = sum(1 for e in hist if e.get("type") == "return-processed")
    check("return-processed events logged", n >= 4, n)

print()
print(f"{'FAIL' if fails else 'OK'}  {len(fails)} failing")
sys.exit(1 if fails else 0)
