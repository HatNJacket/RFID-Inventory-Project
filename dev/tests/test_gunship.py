"""Gun-only receiving (Nick, 2026-09-02): the sort-match endpoint that
places a no-order-number pallet into open stock orders (consolidation
prefers ONE order that contains everything; overflow doesn't break it),
scan-order label printing so the stack matches the pallet walk, and the
print-progress poll the gun's check screen waits on."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_gunship_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_engine
from app.models import BinMapEntry, PrintJob
from sqlalchemy.orm import Session
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

# SO 962 wants A(4), B(2), C(3). SO 951 wants B(5) only. SO 958 wants D(2).
ORDERS_LINES = {
    "configured": True, "ok": True, "orders": [
        {"order_id": 62, "reference_number": "962", "vendor": "Svbony",
         "items": [
             {"sku": "SORT-A", "barcode": "601", "title": "Sort A",
              "remaining": 4},
             {"sku": "SORT-B", "barcode": "602", "title": "Sort B",
              "remaining": 2},
             {"sku": "SORT-C", "barcode": "603", "title": "Sort C",
              "remaining": 3},
             {"sku": "SORT-E", "barcode": "605", "title": "Sort E",
              "remaining": 1},
         ]},
        {"order_id": 51, "reference_number": "951", "vendor": "Svbony",
         "items": [
             {"sku": "SORT-B", "barcode": "602", "title": "Sort B",
              "remaining": 5},
         ]},
        {"order_id": 58, "reference_number": "958", "vendor": "Antares",
         "items": [
             {"sku": "SORT-D", "barcode": "604", "title": "Sort D",
              "remaining": 2},
         ]},
    ],
}

ORDER962 = {"order_id": 62, "reference_number": "962", "vendor": "Svbony",
            "status": "open", "expected_date": None}
LINES962 = [
    {"sku": "SORT-A", "barcode": "601", "title": "Sort A",
     "ordered": 4, "received": 0, "remaining": 4},
    {"sku": "SORT-B", "barcode": "602", "title": "Sort B",
     "ordered": 2, "received": 0, "remaining": 2},
    {"sku": "SORT-C", "barcode": "603", "title": "Sort C",
     "ordered": 3, "received": 0, "remaining": 3},
]

def fake_order_lines(ref, operator=None):
    key = (ref or "").strip().upper().replace("SO", "").strip()
    if key == "962":
        return {"configured": True, "ok": True, "order": ORDER962,
                "items": [dict(x) for x in LINES962]}
    return {"configured": True, "ok": True, "order": None, "items": []}

# Vendor scoping (Nick, 2026-09-02 round 3): the matcher must hand the
# scanned products' vendor(s) to the planner walk so only that vendor's
# orders get the slow detail fetches. VENDOR_BLIND simulates the SO 941
# failure (round 4): planner vendor spellings past containment, so the
# scoped walk answers empty and the matcher must retry unfiltered.
seen_vendors = []
VENDOR_BLIND = []
def fake_orders_lines(operator=None, vendors=None):
    seen_vendors.append(vendors)
    if vendors and VENDOR_BLIND:
        return {"configured": True, "ok": True, "orders": []}
    return ORDERS_LINES

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_shelf_on_hand", return_value=0), \
     patch("app.planner.open_orders_lines",
           side_effect=fake_orders_lines), \
     patch("app.planner.order_lines", side_effect=fake_order_lines), \
     patch("app.main._maybe_refresh_bin_map", return_value=False):
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="SORT-A", barcode="601",
                          product_title="Sort A", bin="D2-4", qty=0,
                          vendor="Svbony", shopify_variant_id="t:SA"))
        s.add(BinMapEntry(sku="SORT-B", barcode="602",
                          product_title="Sort B", bin="C1-2", qty=0,
                          vendor="Svbony", shopify_variant_id="t:SB"))
        s.add(BinMapEntry(sku="SORT-C", barcode="603",
                          product_title="Sort C", bin="C1-3", qty=0,
                          shopify_variant_id="t:SC"))
        s.add(BinMapEntry(sku="SORT-D", barcode="604",
                          product_title="Sort D", bin="B4-1", qty=0,
                          shopify_variant_id="t:SD"))
        s.add(BinMapEntry(sku="SORT-E", barcode="605",
                          product_title="Sort E", bin="C1-4", qty=0,
                          vendor="Svbony", shopify_variant_id="t:SE"))
        s.commit()

    # ---- consolidation: one order contains everything ---------------
    # B is on TWO orders (962 wants 2, 951 wants 5) but A pins the
    # pallet to 962, which contains both -> consolidated.
    r = cl.post("/api/receiving/sort-match", json={"counts": [
        {"code": "601", "count": 3}, {"code": "602", "count": 2}]})
    body = r.json()
    v = body["verdict"]
    check("consolidates onto the one covering order",
          r.status_code == 200 and v["consolidated"] is True
          and len(v["orders"]) == 1
          and v["orders"][0]["reference_number"] == "962", r.text[:400])
    wanted = {p["sku"]: p["wanted_by"] for p in body["products"]}
    check("products report every order that wants them",
          wanted["SORT-A"] == ["962"]
          and sorted(wanted["SORT-B"]) == ["951", "962"], wanted)
    check("the planner walk was scoped to the scanned vendor",
          seen_vendors and seen_vendors[-1] == {"Svbony"}, seen_vendors)

    # ---- tie between two covering orders -> the most recent wins ----
    # Only B is scanned; SO 962 (id 62) and SO 951 (id 51) both cover
    # it and both absorb the full 2 -> the newer 962 takes it.
    r = cl.post("/api/receiving/sort-match", json={"counts": [
        {"code": "602", "count": 2}]})
    v = r.json()["verdict"]
    check("tied covering orders go to the most recent",
          v["consolidated"] is True
          and v["orders"][0]["reference_number"] == "962", str(v)[:300])

    # ---- overflow doesn't break consolidation (Nick round 2, Q1) ----
    r = cl.post("/api/receiving/sort-match", json={"counts": [
        {"code": "601", "count": 6}, {"code": "603", "count": 1}]})
    v = r.json()["verdict"]
    over = {p["sku"]: p["overflow"] for p in v["orders"][0]["products"]}
    check("overflow keeps the order, flags the extras",
          v["consolidated"] is True and over["SORT-A"] == 2
          and over["SORT-C"] == 0, str(v)[:400])

    # ---- split + unmatched ------------------------------------------
    r = cl.post("/api/receiving/sort-match", json={"counts": [
        {"code": "601", "count": 2}, {"code": "604", "count": 1},
        {"code": "999", "count": 2}]})
    v = r.json()["verdict"]
    refs = sorted(o["reference_number"] for o in v["orders"])
    check("no covering order -> a two-order split",
          v["consolidated"] is False and refs == ["958", "962"],
          str(v)[:400])
    check("unknown code lands in unmatched",
          len(v["unmatched"]) == 1
          and v["unmatched"][0]["code"] == "999", str(v)[:300])

    # ---- no vendor on record -> the full unscoped walk still runs ---
    r = cl.post("/api/receiving/sort-match", json={"counts": [
        {"code": "604", "count": 1}]})
    check("vendorless products fall back to the full order walk",
          r.status_code == 200 and seen_vendors[-1] is None,
          seen_vendors[-1])

    # ---- SKU codes match too (scan pass can hand SKUs through) ------
    r = cl.post("/api/receiving/sort-match", json={"counts": [
        {"code": "sort-a", "count": 1}]})
    v = r.json()["verdict"]
    check("a SKU (case-insensitive) matches like a barcode",
          v["consolidated"] is True
          and v["orders"][0]["reference_number"] == "962", str(v)[:300])

    # ---- soft consolidation (round 4: "all but one in SO 941") ------
    # A, B, C, E sit on SO 962; D belongs to SO 958 only. 4 of 5
    # matched products (80%) fit one order -> consolidate on it and
    # SKIP the stray with a note, instead of a two-order split.
    r = cl.post("/api/receiving/sort-match", json={"counts": [
        {"code": "601", "count": 1}, {"code": "602", "count": 1},
        {"code": "603", "count": 1}, {"code": "605", "count": 1},
        {"code": "604", "count": 1}]})
    v = r.json()["verdict"]
    check("80% in one order -> soft consolidation",
          v["consolidated"] is True
          and v["orders"][0]["reference_number"] == "962", str(v)[:400])
    check("the stray is skipped with its own order named",
          len(v["skipped"]) == 1 and v["skipped"][0]["sku"] == "SORT-D"
          and v["skipped"][0]["wanted_by"] == ["958"], str(v)[:400])

    # ---- vendor-blind planner -> unfiltered retry (the SO 941 bug) --
    VENDOR_BLIND.append(1)
    seen_vendors.clear()
    r = cl.post("/api/receiving/sort-match", json={"counts": [
        {"code": "601", "count": 2}]})
    v = r.json()["verdict"]
    check("scoped walk empty -> retries unfiltered and still matches",
          v["consolidated"] is True
          and v["orders"][0]["reference_number"] == "962"
          and seen_vendors == [{"Svbony"}, None], (str(v)[:200],
                                                   seen_vendors))
    VENDOR_BLIND.clear()

    # ---- C72 -> web sort hand-off -----------------------------------
    r = cl.post("/api/receiving/sort-handoff", json={
        "counts": [{"code": "XR-100", "count": 3},
                   {"code": "605", "count": 1}],
        "created_by": "C72-Nick"})
    h1 = r.json()["handoff"]
    check("hand-off stored with box math", r.status_code == 201
          and h1["boxes"] == 4 and h1["products"] == 2, r.text[:300])
    r = cl.get("/api/receiving/sort-handoff/pending")
    check("pending answers the newest pass",
          (r.json()["handoff"] or {}).get("id") == h1["id"],
          r.text[:200])
    r = cl.post("/api/receiving/sort-handoff", json={
        "counts": [{"code": "XR-200", "count": 1}]})
    h2 = r.json()["handoff"]
    r = cl.get("/api/receiving/sort-handoff/pending")
    check("a new pass supersedes the old one",
          (r.json()["handoff"] or {}).get("id") == h2["id"], r.text[:200])
    r = cl.post(f"/api/receiving/sort-handoff/{h2['id']}/consume",
                json={"consumed_by": "Nick"})
    check("consume stamps the pass", r.status_code == 200
          and r.json()["handoff"]["consumed_at"] is not None,
          r.text[:200])
    r = cl.get("/api/receiving/sort-handoff/pending")
    check("consumed pass leaves pending empty",
          r.json()["handoff"] is None, r.text[:200])

    # ---- scan-order printing ----------------------------------------
    # The pallet was scanned C, A, B -> the label jobs must queue in
    # that order, not the order-line order (A, B, C).
    r = cl.post("/api/receiving/full-shipment",
                json={"order": "962", "requested_by": "Nick",
                      "scan_order": ["603", "601", "602"]})
    body = r.json()
    check("full shipment accepts the scan order", r.status_code == 201
          and body["queued"] == 9, r.text[:300])
    bid = body["batch"]["id"]
    with Session(get_engine()) as s:
        jobs = [j.sku for j in s.query(PrintJob)
                .filter(PrintJob.batch_id == bid)
                .order_by(PrintJob.id).all()]
    check("labels print in scan order (C then A then B)",
          jobs == ["SORT-C"] * 3 + ["SORT-A"] * 4 + ["SORT-B"] * 2, jobs)

    # ---- print-progress poll ----------------------------------------
    r = cl.get(f"/api/batches/{bid}/print-progress")
    p = r.json()
    check("progress: 9 pending, not finished", r.status_code == 200
          and p["total"] == 9 and p["pending"] == 9
          and p["finished"] is False, r.text[:200])
    with Session(get_engine()) as s:
        for j in s.query(PrintJob).filter(PrintJob.batch_id == bid):
            j.status = "done"
        s.commit()
    p = cl.get(f"/api/batches/{bid}/print-progress").json()
    check("progress: all done -> finished true",
          p["done"] == 9 and p["finished"] is True, p)

    # ---- planner bridge off fails soft with a clear 502 -------------
    with patch("app.planner.open_orders_lines",
               return_value={"configured": False, "ok": False,
                             "orders": []}):
        r = cl.post("/api/receiving/sort-match", json={"counts": [
            {"code": "601", "count": 1}]})
        check("bridge off -> 502, not a crash", r.status_code == 502,
              r.status_code)

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
