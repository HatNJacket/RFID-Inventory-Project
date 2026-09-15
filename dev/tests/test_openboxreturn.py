"""Open-box returns (Nick, 2026-09-15): a sold, RFID-tagged product
comes back opened. Set as Open Box files a return watch (+ Review
task), resolves or drafts the -O twin, and sweeps hearing the
original's presumed-sold tags ask "is this box the open-box unit?" -
YES adopts the old tag for the twin (or says peel it, when a fresh -O
label already paired), NO stops asking about that EPC.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_obxreturn_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

# OBX2-O has a hand-made twin listing; OBX1-O has none (draft flow).
def fake_find_sku_listing(sku):
    if sku.strip().upper() == "OBX2-O":
        return {"shopify_variant_id": "gid://v/obx2o",
                "shopify_product_id": "gid://p/obx2o",
                "product_title": "Widget Two - Open Box",
                "status": "DRAFT", "sku": "OBX2-O", "barcode": None}
    return None

def fake_create_draft(title, sku, barcode, bin_value):
    return {"product_gid": "gid://p/new", "variant_gid": "gid://v/new",
            "sku": sku}

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.find_sku_listing",
           side_effect=fake_find_sku_listing), \
     patch("app.shopify.create_draft_listing",
           side_effect=fake_create_draft), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_on_hand", return_value=None), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.shopify.get_quantity_pairs_by_skus", return_value={}), \
     patch("app.main._maybe_refresh_bin_map", return_value=False):
  with TestClient(app) as cl:
    from sqlalchemy import select, func
    from sqlalchemy.orm import Session as S
    from app.database import get_engine
    from app.models import (BarcodeChange, BinMapEntry, OpenboxReturn,
                            RetiredTag, ReviewTask, RfidAssignment)

    EPC1 = "0BX0000000000000000000A1"
    EPC2 = "0BX0000000000000000000B2"
    EPC3 = "0BX0000000000000000000C3"
    with S(get_engine()) as s:
        s.add(BinMapEntry(sku="OBX1", barcode="7100001",
                          product_title="Widget One", bin="B1-1", qty=2,
                          shopify_variant_id="gid://v/obx1"))
        for epc, sku, title in ((EPC1, "OBX1", "Widget One"),
                                (EPC2, "OBX2", "Widget Two"),
                                (EPC3, "OBX3", "Widget Three")):
            s.add(RetiredTag(rfid_id=epc, sku=sku, product_title=title,
                             shopify_variant_id=f"gid://v/{sku.lower()}",
                             bin_location="B1-1",
                             kind="presumed-sold", retired_by="test"))
        s.commit()

    # ---- info + filing -------------------------------------------------
    r = cl.get("/api/products/openbox-info/OBX1")
    d = r.json()
    check("info: no twin listing, retired tag listed",
          r.status_code == 200 and d["listing"] is None
          and [t["rfid_id"] for t in d["retired"]] == [EPC1], d)
    r = cl.get("/api/products/openbox-info/OBX1-O")
    check("info refuses a -O SKU", r.status_code == 422, r.text[:200])

    r = cl.post("/api/openbox-returns", json={
        "sku": "OBX1", "product_title": "Widget One",
        "barcode": "7100001", "bin_location": "B1-1",
        "create_draft": False, "watch": True, "created_by": "Nick"})
    check("no twin + no draft confirm = 409", r.status_code == 409,
          r.text[:200])
    r = cl.post("/api/openbox-returns", json={
        "sku": "OBX1", "product_title": "Widget One",
        "barcode": "7100001", "bin_location": "B1-1",
        "create_draft": True, "watch": True, "epc": EPC1,
        "created_by": "Nick"})
    d = r.json()
    check("filed with a fresh draft twin",
          r.status_code == 201 and d["created_listing"] is True
          and d["openbox"]["sku"] == "OBX1-O"
          and d["return"]["openbox_sku"] == "OBX1-O", d)
    ret1 = d["return"]["id"]
    with S(get_engine()) as s:
        task = s.scalar(select(ReviewTask).where(
            ReviewTask.category == "openbox-return"))
        check("review task opened", task is not None
              and task.sku == "OBX1" and task.status == "open",
              task.as_dict() if task else None)

    # ---- sweep decoration ---------------------------------------------
    r = cl.post("/api/bins/B1-1/check", json={"epcs": [EPC1]})
    rep = r.json()
    flagged = []
    for it in rep.get("items", []):
        flagged += [g for g in (it.get("ghosts") or [])
                    if g.get("openbox_return_id")]
    flagged += [g for g in (rep.get("stray_ghosts") or [])
                if g.get("openbox_return_id")]
    check("heard old tag carries the open-box prompt",
          len(flagged) == 1 and flagged[0]["openbox_return_id"] == ret1
          and flagged[0]["openbox_sku"] == "OBX1-O",
          {"items": len(rep.get("items", [])),
           "strays": rep.get("stray_ghosts")})

    # ---- NO suppresses that EPC ---------------------------------------
    r = cl.post(f"/api/openbox-returns/{ret1}/resolve", json={
        "answer": "no", "epc": EPC1, "resolved_by": "Nick"})
    check("NO recorded", r.status_code == 200
          and EPC1 in (r.json()["return"]["not_epcs"] or ""), r.text[:300])
    r = cl.post("/api/bins/B1-1/check", json={"epcs": [EPC1]})
    rep = r.json()
    flagged = [g for it in rep.get("items", [])
               for g in (it.get("ghosts") or [])
               if g.get("openbox_return_id")]
    flagged += [g for g in (rep.get("stray_ghosts") or [])
                if g.get("openbox_return_id")]
    check("NO-answered EPC stops prompting", flagged == [], flagged)

    # ---- YES with no fresh label = ADOPT ------------------------------
    r = cl.post(f"/api/openbox-returns/{ret1}/resolve", json={
        "answer": "yes", "epc": EPC1, "bin_location": "B1-1",
        "resolved_by": "Nick"})
    d = r.json()
    check("YES adopts the old tag for the twin",
          r.status_code == 200 and d["adopted"] is True, d)
    with S(get_engine()) as s:
        a = s.scalar(select(RfidAssignment).where(
            RfidAssignment.rfid_id == EPC1))
        check("old EPC is now the -O product's live tag",
              a is not None and a.sku == "OBX1-O"
              and a.bin_location == "B1-1"
              and "Open Box" in (a.product_title or ""),
              a.as_dict() if a else None)
        check("retired row consumed",
              s.scalar(select(RetiredTag).where(
                  RetiredTag.rfid_id == EPC1)) is None, "")
        ret = s.get(OpenboxReturn, ret1)
        check("watch closed as adopted", ret.status == "done"
              and ret.resolution == "adopted", ret.as_dict())
        task = s.get(ReviewTask, ret.task_id)
        check("task resolved with it", task.status == "resolved",
              task.as_dict())

    # ---- YES with a fresh -O label = PEEL -----------------------------
    r = cl.post("/api/openbox-returns", json={
        "sku": "OBX2", "product_title": "Widget Two",
        "create_draft": False, "watch": True, "created_by": "Nick"})
    d = r.json()
    check("existing hand-made twin adopted without a draft",
          r.status_code == 201 and d["created_listing"] is False
          and d["openbox"]["shopify_variant_id"] == "gid://v/obx2o", d)
    ret2 = d["return"]["id"]
    with S(get_engine()) as s:
        # The fresh open-box label paired after filing.
        s.add(RfidAssignment(rfid_id="0BX00000000000000000FRESH",
                             shopify_variant_id="gid://v/obx2o",
                             sku="OBX2-O",
                             product_title="Widget Two - Open Box",
                             bin_location="B1-1"))
        s.commit()
    r = cl.post(f"/api/openbox-returns/{ret2}/resolve", json={
        "answer": "yes", "epc": EPC2, "resolved_by": "Nick"})
    d = r.json()
    check("YES with fresh label says peel, never double-counts",
          r.status_code == 200 and d["adopted"] is False
          and "PEEL" in d["message"].upper(), d)
    with S(get_engine()) as s:
        rt = s.scalar(select(RetiredTag).where(
            RetiredTag.rfid_id == EPC2))
        check("old tag flips to replaced", rt is not None
              and rt.kind == "replaced", rt.as_dict() if rt else None)
        check("only the fresh tag counts for OBX2-O",
              len(s.scalars(select(RfidAssignment).where(
                  func.upper(RfidAssignment.sku) == "OBX2-O"
              )).all()) == 1, "")

    # ---- manual "peeled" from the Review tab --------------------------
    r = cl.post("/api/openbox-returns", json={
        "sku": "OBX3", "product_title": "Widget Three",
        "create_draft": True, "watch": True, "epc": EPC3,
        "created_by": "Nick"})
    ret3 = r.json()["return"]["id"]
    r = cl.post(f"/api/openbox-returns/{ret3}/resolve", json={
        "answer": "peeled", "resolved_by": "Nick"})
    check("peeled closes the watch", r.status_code == 200
          and r.json()["return"]["resolution"] == "peeled", r.text[:300])
    with S(get_engine()) as s:
        rt = s.scalar(select(RetiredTag).where(
            RetiredTag.rfid_id == EPC3))
        check("known EPC flips to replaced on peel",
              rt is not None and rt.kind == "replaced",
              rt.as_dict() if rt else None)
    r = cl.post(f"/api/openbox-returns/{ret3}/resolve", json={
        "answer": "dismiss"})
    check("resolving a closed watch = 409", r.status_code == 409,
          r.text[:200])

    # ---- the label itself (Nick, 2026-09-15, second pass) --------------
    # SKU line = BASE SKU (the -O belongs to the barcode), bin line
    # carries "OPEN BOX", records never keep the note.
    from app.models import PrintJob
    r = cl.post("/api/print-jobs", json={
        "shopify_variant_id": "gid://v/new", "product_title":
        "Widget One - Open Box", "sku": "OBX1-O",
        "barcode": "7100001-O", "bin_location": "B1-1"})
    j = r.json()["jobs"][0]
    check("open-box label: OPEN BOX rides the bin line",
          j["bin_location"] == "B1-1, OPEN BOX", j)
    check("open-box label: SKU line prints the base SKU",
          j["label_sku"] == "OBX1", j)
    r = cl.post("/api/print-jobs", json={
        "shopify_variant_id": "gid://v/new", "product_title":
        "Widget One - Open Box", "sku": "OBX1-O",
        "barcode": "7100001-O", "bin_location": "B1-1, OPEN BOX"})
    check("note never stacks",
          r.json()["jobs"][0]["bin_location"] == "B1-1, OPEN BOX",
          r.text[:200])
    r = cl.post("/api/print-jobs", json={
        "shopify_variant_id": "gid://v/new", "product_title":
        "Widget One - Open Box", "sku": "OBX1-O", "barcode": "7100001-O",
        "bin_location": "B1-1", "label_sku": "CUSTOM LINE"})
    check("a custom SKU line is never overwritten",
          r.json()["jobs"][0]["label_sku"] == "CUSTOM LINE",
          r.text[:200])
    r = cl.post(f"/api/print-jobs/{j['id']}/complete")
    check("printed: assignment record keeps the CLEAN bin",
          r.status_code == 200
          and r.json()["assignment"]["bin_location"] == "B1-1",
          r.text[:300])

    # ---- history trail -------------------------------------------------
    with S(get_engine()) as s:
        n = len(s.scalars(select(BarcodeChange).where(
            BarcodeChange.changed_field == "openbox")).all())
        check("history rows for the whole trail", n >= 5, n)

print()
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES'}")
sys.exit(1 if fails else 0)
