"""Multi-box units (Nick, 2026-09-02, the S11740): one sellable unit,
several cartons, ONE counting tag. The durable mark makes labels print
"BOX X OF Y" with each box's own bin; boxes 2..N are companion labels -
live tags registered in the companion registry, recognized everywhere,
counted nowhere - and the audit find flow refuses to double-count an
untagged second carton without an explicit override."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_multibox_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_engine
from app.models import BinMapEntry, CompanionTag, PrintJob, RfidAssignment
from sqlalchemy.orm import Session
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

P = {"shopify_variant_id":"t:MB","shopify_product_id":"gid://p/MB",
     "product_title":"Big Scope S11740","variant_title":None,
     "sku":"S11740","barcode":"117","bin_location":"B11-1"}
def look(t):
    return dict(P) if t in ("117","S11740") else None

with patch("app.shopify.lookup_barcode", side_effect=look), \
     patch("app.shopify.lookup_barcode_all",
           side_effect=lambda t:([look(t)] if look(t) else [])), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_on_hand", return_value=1), \
     patch("app.shopify.get_shelf_on_hand", return_value=1), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.shopify.get_quantity_pairs_by_skus", return_value={}), \
     patch("app.main._maybe_refresh_bin_map", return_value=False):
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="S11740", barcode="117",
                          product_title="Big Scope S11740", bin="B11-1",
                          qty=1, shopify_variant_id="t:MB"))
        s.commit()

    # ---- the durable mark -------------------------------------------
    r = cl.put("/api/multibox/S11740", json={
        "boxes_per_unit": 2, "bins": ["B11-1", "B11-1"],
        "updated_by": "Nick"})
    check("mark set: 1 unit = 2 boxes", r.status_code == 200
          and r.json()["multibox"]["boxes_per_unit"] == 2, r.text[:200])
    r = cl.get("/api/multibox/s11740")
    check("mark reads back case-insensitively",
          (r.json()["multibox"] or {}).get("bins") == ["B11-1", "B11-1"],
          r.text[:200])

    # ---- label expansion --------------------------------------------
    bid = cl.post("/api/batches",
                  json={"bin":"B11-1","created_by":"Nick"}).json()["id"]
    cl.post(f"/api/batches/{bid}/scan", json={"code":"117"})
    r = cl.post(f"/api/batches/{bid}/queue-labels", json={})
    check("1 unit queues 2 physical labels", r.json()["count"] == 2,
          r.text[:200])
    with Session(get_engine()) as s:
        jobs = s.query(PrintJob).filter(
            PrintJob.batch_id == bid).order_by(PrintJob.id).all()
    # Nick's round 2: the header stays Telescopes Canada / the saved
    # name; the box note rides the BIN line instead.
    check("box 1 = counting label, box note on the BIN line",
          jobs[0].kind is None and jobs[0].label_name is None
          and jobs[0].bin_location == "B11-1, Box 1 of 2",
          [(j.kind, j.label_name, j.bin_location) for j in jobs])
    check("box 2 = companion label right behind it",
          jobs[1].kind == "companion"
          and jobs[1].bin_location == "B11-1, Box 2 of 2",
          [(j.kind, j.bin_location) for j in jobs])

    # ---- printing: assignment for box 1, registry for box 2 ---------
    for j in jobs:
        r = cl.post(f"/api/print-jobs/{j.id}/complete")
        check(f"job {j.id} completes", r.status_code == 200, r.text[:200])
    with Session(get_engine()) as s:
        ties = s.query(RfidAssignment).filter(
            RfidAssignment.sku == "S11740").all()
        comps = s.query(CompanionTag).all()
    check("ONE counting assignment only, with the CLEAN bin",
          len(ties) == 1 and ties[0].rfid_id == jobs[0].epc
          and ties[0].bin_location == "B11-1",
          [(t.rfid_id, t.bin_location) for t in ties])
    check("companion registered with box math and clean bin",
          len(comps) == 1
          and comps[0].epc == jobs[1].epc and comps[0].box_no == 2
          and comps[0].box_count == 2
          and comps[0].bin_location == "B11-1",
          [(c.epc, c.box_no, c.box_count, c.bin_location)
           for c in comps])

    # ---- counting stays 1 everywhere --------------------------------
    it = cl.get(f"/api/batches/{bid}").json()["items"][0]
    check("tracker counts 1 printed label, not 2",
          it["printed_count"] == 1 and it["boxes_per_unit"] == 2, it)

    # ---- sweeps recognize the companion, count nothing --------------
    r = cl.post("/api/bins/B11-1/check",
                json={"epcs": [jobs[0].epc, jobs[1].epc]})
    rep = r.json()
    row = next(i for i in rep["items"] if i["sku"] == "S11740")
    check("bin audit: only box 1's tag counts",
          row["detected"] == 1 and row["boxes_per_unit"] == 2, row)
    check("companion heard by name, not unknown, not owed",
          len(rep["companions_heard"]) == 1
          and rep["companions_heard"][0]["box_no"] == 2
          and rep["unknown_epcs"] == []
          and rep["printed_labels_heard"] == [],
          {k: rep[k] for k in ("companions_heard", "unknown_epcs",
                               "printed_labels_heard")})

    # ---- tag-info names the companion -------------------------------
    r = cl.get(f"/api/tag-info/{jobs[1].epc}")
    check("tag-info explains the companion sticker",
          r.json().get("companion", {}).get("box_no") == 2
          and "counts nowhere" in " ".join(r.json()["notes"]),
          r.text[:300])

    # ---- audit find guard -------------------------------------------
    r = cl.post("/api/audit/finds", json={"code": "117", "by": "Nick"})
    check("untagged second carton refused with the multibox warning",
          r.status_code == 409
          and r.json()["detail"].startswith("MULTIBOX:"), r.text[:300])
    r = cl.post("/api/audit/finds", json={"code": "117", "by": "Nick",
                                          "multibox_ok": True})
    check("explicit override still notes a real separate unit",
          r.status_code == 201, r.text[:200])

    # ---- pairing the dual labels (Nick round 3) ---------------------
    # The companion sticker trigger-reads like any label: it must
    # CONFIRM, never count - and a hand-applied counting tag on a
    # marked product answers with the "now the other boxes" guidance.
    r = cl.post(f"/api/batches/{bid}/pair",
                json={"epc": jobs[1].epc, "item_id": it["id"],
                      "created_by": "Nick"})
    d = r.json()
    check("companion pair = confirmed, not counted",
          r.status_code == 201 and d["assignment"] is None
          and d["companion"]["box_no"] == 2
          and "counts by Box 1" in d["message"], r.text[:300])
    it2 = next(i for i in cl.get(f"/api/batches/{bid}").json()["items"]
               if i["id"] == it["id"])
    check("paired count untouched by the companion confirm",
          it2["paired_count"] == it["paired_count"], it2["paired_count"])
    r = cl.post(f"/api/batches/{bid}/pair",
                json={"epc": "AAAA00000000000000000MB1",
                      "item_id": it["id"], "created_by": "Nick"})
    check("counting pair answers with the next-boxes guidance",
          r.status_code == 201
          and "Box 1 of 2 paired" in (r.json().get("message") or ""),
          r.text[:300])

    # Wrong product: the companion sticker is on somebody else's box.
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="OTHER-1", barcode="909",
                          product_title="Other Thing", bin="B11-1",
                          qty=1, shopify_variant_id="t:OT"))
        s.commit()
    def look2(t):
        if t in ("909","OTHER-1"):
            return {"shopify_variant_id":"t:OT",
                    "shopify_product_id":"gid://p/OT",
                    "product_title":"Other Thing","variant_title":None,
                    "sku":"OTHER-1","barcode":"909",
                    "bin_location":"B11-1"}
        return look(t)
    with patch("app.shopify.lookup_barcode", side_effect=look2), \
         patch("app.shopify.lookup_barcode_all",
               side_effect=lambda t:([look2(t)] if look2(t) else [])):
        cl.post(f"/api/batches/{bid}/scan", json={"code":"909"})
        other = next(i for i in cl.get(f"/api/batches/{bid}")
                     .json()["items"] if i["barcode"] == "909")
        r = cl.post(f"/api/batches/{bid}/pair",
                    json={"epc": jobs[1].epc, "item_id": other["id"],
                          "created_by": "Nick"})
        check("companion on the wrong product refuses loudly",
              r.status_code == 409
              and "DIFFERENT product" in r.json()["detail"],
              r.text[:300])

    # Station link + sweep-assign can't tie a companion either.
    r = cl.post("/api/rfid-assignments", json={
        "rfid_id": jobs[1].epc, "shopify_variant_id": "t:MB",
        "product_title": "Big Scope S11740", "sku": "S11740",
        "assigned_by": "Nick"})
    check("station link of a companion refuses", r.status_code == 409
          and "counts nowhere" in r.json()["detail"], r.text[:300])
    r = cl.post("/api/rfid-assignments/sweep", json={
        "epcs": [jobs[1].epc, "AAAA00000000000000000MB2"],
        "shopify_variant_id": "t:MB",
        "product_title": "Big Scope S11740", "sku": "S11740",
        "bin_location": "B11-1", "assigned_by": "Nick"})
    d = r.json()
    check("sweep-assign skips the companion by name",
          d["count"] == 1
          and len(d["companions_skipped"]) == 1
          and d["companions_skipped"][0]["box_no"] == 2, r.text[:300])

    # ---- clearing the mark ------------------------------------------
    r = cl.put("/api/multibox/S11740", json={"boxes_per_unit": 1,
                                             "updated_by": "Nick"})
    check("boxes_per_unit 1 clears the mark", r.status_code == 200
          and r.json()["multibox"] is None, r.text[:200])
    r = cl.get("/api/multibox/S11740")
    check("cleared mark reads back empty",
          r.json()["multibox"] is None, r.text[:200])

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
