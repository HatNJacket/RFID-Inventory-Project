"""The Inventory Check merger + drift guards (Nick, 2026-09-02): one
task per SKU from either trigger, arithmetic closes only its own
filings, receives-in-flight are skipped, unavailable stock counts as
agreement, the verdict computes retire/surplus/shortfall, retire-sold
N works sales-guarded, bin audits resolve bin-check tasks, and the
self-clear offers for could-not-scan / unresolved-barcode answer."""
import os, sys, tempfile
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_invcheck_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app import orders_sync
from app.database import get_engine
from app.models import (Batch, BatchItem, BinMapEntry, EpcCapture,
                        HeldLabelItem, HeldLabelList, OnhandLog,
                        OrderReceipt, RetiredTag, ReviewTask,
                        RfidAssignment, SoldRecord)
from sqlalchemy import select
from sqlalchemy.orm import Session
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

STOCK = {"MERGE-1": 2, "FLIGHT-1": 5, "UNAV-1": 1, "SHORT-1": 3}
UNAVAIL = {"UNAV-1": 1}
def fake_stock_info(skus):
    return {s: {"on_hand": STOCK[s], "unavailable": UNAVAIL.get(s, 0),
                "bin": "V1-1"}
            for s in skus if s in STOCK}

NOW = datetime.now(timezone.utc)
def tag(s, epc, sku, when=None):
    s.add(RfidAssignment(rfid_id=epc, shopify_variant_id="t:1",
        product_title=f"{sku} product", sku=sku, bin_location="V1-1",
        assigned_at=when or NOW - timedelta(days=2)))

def look(t):
    if t in ("XR-77", "REAL-1"):
        return {"shopify_variant_id":"t:R1","shopify_product_id":"g:1",
                "product_title":"Real One","variant_title":None,
                "sku":"REAL-1","barcode":"XR-77","bin_location":"V1-1"}
    return None

with patch("app.shopify.lookup_barcode", side_effect=look), \
     patch("app.shopify.lookup_barcode_all",
           side_effect=lambda t:([look(t)] if look(t) else [])), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus",
           side_effect=fake_stock_info), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.shopify.get_shelf_on_hand", return_value=0), \
     patch("app.main._maybe_refresh_bin_map", return_value=False), \
     patch("app.main._kick_orders_sync_soon"):
  with TestClient(app) as cl:
    # ---- the merger: arithmetic files inventory-check ----------------
    with Session(get_engine()) as s:
        for e in ("C100000000000000000000A1", "C100000000000000000000A2",
                  "C100000000000000000000A3"):
            tag(s, e, "MERGE-1")
        s.add(SoldRecord(order_id="o1", order_name="#1", sku="MERGE-1",
                         quantity=1, fulfilled_at=NOW - timedelta(days=1)))
        s.commit()
        # tags 3, on-hand 2, sold-covered 1 -> expected 3: agree.
        orders_sync.refresh_mismatch_tasks(s); s.commit()
        open_now = s.scalars(select(ReviewTask).where(
            ReviewTask.status == "open")).all()
        check("agreement files nothing", open_now == [], open_now)
        # A tag goes missing from the record side: on-hand drops to 1
        # without a sale -> tags 3 vs expected 2 -> ONE inventory-check.
        STOCK["MERGE-1"] = 1
        orders_sync.refresh_mismatch_tasks(s); s.commit()
        t1 = s.scalar(select(ReviewTask).where(
            ReviewTask.status == "open"))
        check("the arithmetic files an inventory-check",
              t1 is not None and t1.category == "inventory-check"
              and t1.created_by == "orders-sync", t1)
        # A human-filed check for the same SKU never doubles up: the
        # sync updates its own; a second run adds nothing.
        orders_sync.refresh_mismatch_tasks(s); s.commit()
        n = len(s.scalars(select(ReviewTask).where(
            ReviewTask.status == "open")).all())
        check("one open check per SKU, ever", n == 1, n)
        # A human-filed check is NOT auto-closed by the arithmetic.
        t1.created_by = "Nick"
        STOCK["MERGE-1"] = 2
        orders_sync.refresh_mismatch_tasks(s); s.commit()
        s.refresh(t1)
        check("agreement never closes a human's filing",
              t1.status == "open", t1.status)
        t1.created_by = "orders-sync"
        orders_sync.refresh_mismatch_tasks(s); s.commit()
        s.refresh(t1)
        check("agreement closes the arithmetic's own filing",
              t1.status == "resolved"
              and t1.resolved_by == "orders-sync", t1.status)
        # The on-hand observation log recorded the moves.
        obs = s.scalars(select(OnhandLog).where(
            OnhandLog.sku == "MERGE-1")).all()
        check("on-hand changes land in the observation log",
              [o.on_hand for o in obs] == [2, 1, 2],
              [o.on_hand for o in obs])

    # ---- drift guard 1: mid-receive skip -----------------------------
    with Session(get_engine()) as s:
        tag(s, "C100000000000000000000B1", "FLIGHT-1")
        s.add(SoldRecord(order_id="o2", order_name="#2", sku="FLIGHT-1",
                         quantity=2, fulfilled_at=NOW - timedelta(days=1)))
        b = Batch(bin_name="RECEIVING", kind="receiving",
                  created_by="TC-Planner · SO 5 · V")
        s.add(b); s.flush()
        s.add(BatchItem(batch_id=b.id, scanned_code="FLIGHT-1",
                        sku="FLIGHT-1", resolved=True, qty_scanned=4,
                        product_title="Flight product",
                        shopify_variant_id="t:F"))
        s.commit()
        # tags 1 vs expected 7 would scream - but a receive is open.
        orders_sync.refresh_mismatch_tasks(s); s.commit()
        flight = s.scalars(select(ReviewTask).where(
            ReviewTask.sku == "FLIGHT-1",
            ReviewTask.status == "open")).all()
        check("a mid-flight receive files NO check", flight == [], flight)
        b.status = "done"
        s.commit()
        orders_sync.refresh_mismatch_tasks(s); s.commit()
        flight = s.scalar(select(ReviewTask).where(
            ReviewTask.sku == "FLIGHT-1", ReviewTask.status == "open"))
        check("the check fires once the receive settles",
              flight is not None, flight)

    # ---- drift guard 4: unavailable counts as agreement --------------
    with Session(get_engine()) as s:
        for e in ("C100000000000000000000U1", "C100000000000000000000U2"):
            tag(s, e, "UNAV-1")
        s.add(SoldRecord(order_id="o3", order_name="#3", sku="UNAV-1",
                         quantity=0, fulfilled_at=NOW))
        s.commit()
        # tags 2, on-hand 1 + unavailable 1 -> surplus of exactly the
        # unavailable bucket: no task.
        orders_sync.refresh_mismatch_tasks(s); s.commit()
        u = s.scalars(select(ReviewTask).where(
            ReviewTask.sku == "UNAV-1",
            ReviewTask.status == "open")).all()
        check("a surplus within Unavailable files nothing", u == [], u)

    # ---- the verdict + retire-sold N ---------------------------------
    with Session(get_engine()) as s:
        flight = s.scalar(select(ReviewTask).where(
            ReviewTask.sku == "FLIGHT-1", ReviewTask.status == "open"))
        fid = flight.id
    r = cl.get(f"/api/review-tasks/{fid}/context")
    ctx = r.json()
    check("context carries tiles and terms",
          ctx["units_on_file"] == 1 and ctx["live_on_hand"] == 5
          and ctx["terms"]["sold_unretired"] == 2, str(ctx)[:300])
    check("shortfall verdict for missing tags",
          ctx["verdict"]["kind"] == "shortfall"
          and ctx["verdict"]["units"] == 6, ctx["verdict"])

    # MERGE-1 retire case: tags 3, on-hand drops to 0, sales cover 2 -
    # expected 0+2 = 2 vs 3 tags, and the surplus is sales-covered.
    with Session(get_engine()) as s:
        s.add(SoldRecord(order_id="o4", order_name="#4", sku="MERGE-1",
                         quantity=1, fulfilled_at=NOW))
        STOCK["MERGE-1"] = 0
        orders_sync.refresh_mismatch_tasks(s); s.commit()
        m = s.scalar(select(ReviewTask).where(
            ReviewTask.sku == "MERGE-1", ReviewTask.status == "open"))
        mid_ = m.id
        # A sweep heard two of the three tags - the third is silent.
        s.add(EpcCapture(device="C72", note="AUDIT V1-1", epc_count=2,
                         epcs="C100000000000000000000A1\n"
                              "C100000000000000000000A2"))
        s.commit()
    ctx = cl.get(f"/api/review-tasks/{mid_}/context").json()
    check("retire verdict when sales cover the surplus",
          ctx["verdict"]["kind"] == "retire"
          and ctx["verdict"]["units"] == 1, ctx["verdict"])
    check("last heard rides the tiles",
          ctx["last_heard"]["heard"] == 2
          and ctx["last_heard"]["total"] == 3, ctx["last_heard"])
    check("movement hover names the change",
          "from 2 to 0" in
          (ctx.get("onhand_movement") or {}).get("text", ""),
          ctx.get("onhand_movement"))
    r = cl.post(f"/api/review-tasks/{mid_}/retire-sold",
                json={"units": 1, "changed_by": "Nick",
                      "confirmed": True})
    check("retire-sold N retires and resolves", r.status_code == 200
          and r.json()["retired"] == 1, r.text[:300])
    with Session(get_engine()) as s:
        rt = s.scalars(select(RetiredTag).where(
            RetiredTag.sku == "MERGE-1")).all()
        check("the UNHEARD tag was the one retired",
              len(rt) == 1
              and rt[0].rfid_id == "C100000000000000000000A3",
              [x.rfid_id for x in rt])
        m = s.get(ReviewTask, mid_)
        check("the check resolved with the retirement",
              m.status == "resolved", m.status)
    r = cl.post(f"/api/review-tasks/{mid_}/retire-sold",
                json={"units": 1, "confirmed": True})
    check("a resolved task refuses another retire", r.status_code == 409,
          r.status_code)

    # ---- bin-check resolves on a covering audit ----------------------
    with Session(get_engine()) as s:
        s.add(ReviewTask(category="bin-check",
                         detail="Bin V1-1: 3 box(es) shelved from "
                                "receiving #9. RFID walk-scan the shelf.",
                         created_by="Nick"))
        s.add(BinMapEntry(sku="MERGE-1", product_title="Merge product",
                          bin="V1-1", qty=2, shopify_variant_id="t:M"))
        s.commit()
    r = cl.post("/api/bins/V1-1/check",
                json={"epcs": ["C100000000000000000000A1"]})
    check("bin check answers", r.status_code == 200, r.text[:200])
    with Session(get_engine()) as s:
        t = s.scalar(select(ReviewTask).where(
            ReviewTask.category == "bin-check"))
        check("the audit resolved the bin-check task",
              t.status == "resolved" and t.resolved_by == "bin-audit"
              and "1 tag(s) heard" in (t.resolution_note or ""),
              (t.status, t.resolution_note))
        # An EMPTY check (a bare LOAD) never resolves one.
        s.add(ReviewTask(category="bin-check",
                         detail="Bin V1-1: 1 box(es) shelved from "
                                "receiving #9.", created_by="Nick"))
        s.commit()
    cl.post("/api/bins/V1-1/check", json={"epcs": []})
    with Session(get_engine()) as s:
        t2 = s.scalars(select(ReviewTask).where(
            ReviewTask.category == "bin-check",
            ReviewTask.status == "open")).all()
        check("an empty sweep leaves the reminder open", len(t2) == 1,
              t2)

    # ---- could-not-scan: tags-added-since offer ----------------------
    with Session(get_engine()) as s:
        s.add(ReviewTask(category="could-not-scan", sku="MERGE-1",
                         product_title="Merge product",
                         detail="Bin V1-1: skipped", created_by="Nick",
                         created_at=NOW - timedelta(days=3)))
        s.commit()
        cid = s.scalar(select(ReviewTask).where(
            ReviewTask.category == "could-not-scan")).id
    ctx = cl.get(f"/api/review-tasks/{cid}/context").json()
    check("could-not-scan counts the tags added since filing",
          ctx.get("tags_added_since", 0) >= 2, ctx.get("tags_added_since"))

    # ---- unresolved-barcode: re-lookup + link material ---------------
    with Session(get_engine()) as s:
        s.add(ReviewTask(category="unresolved-barcode",
                         detail="Barcode ZZZ-UNKNOWN was scanned 2x "
                                "while receiving (#9) but never resolved "
                                "to a product.", created_by="Nick"))
        s.add(ReviewTask(category="unresolved-barcode",
                         detail="Barcode XR-77 was scanned 1x while "
                                "receiving (#9) but never resolved to a "
                                "product.", created_by="Nick"))
        s.commit()
        ids = [t.id for t in s.scalars(select(ReviewTask).where(
            ReviewTask.category == "unresolved-barcode"))]
    ctx = cl.get(f"/api/review-tasks/{ids[0]}/context").json()
    check("a still-unknown code carries itself, resolves nothing",
          ctx["code"] == "ZZZ-UNKNOWN" and ctx["resolves_to"] is None,
          str(ctx)[:200])
    ctx = cl.get(f"/api/review-tasks/{ids[1]}/context").json()
    check("a now-resolving code offers its product",
          (ctx["resolves_to"] or {}).get("sku") == "REAL-1",
          str(ctx)[:200])

    # ---- held-lists window -------------------------------------------
    with Session(get_engine()) as s:
        hl = HeldLabelList(batch_id=1, stock_order_id=9,
                           reference="SO 9", vendor="ZWO",
                           created_by="Nick", epcs="H1\nH2")
        s.add(hl); s.flush()
        s.add(HeldLabelItem(list_id=hl.id, sku="STRIP-1",
                            product_title="Strip product", count=2))
        empty = HeldLabelList(batch_id=2, stock_order_id=10,
                              reference="SO 10", vendor="ZWO", epcs="")
        s.add(empty); s.flush()
        s.add(HeldLabelItem(list_id=empty.id, sku="GONE-1",
                            product_title="Gone", count=0))
        s.commit()
    r = cl.get("/api/held-lists")
    d = r.json()
    check("held strips list what is still waiting",
          d["count"] == 1 and d["lists"][0]["remaining"] == 2
          and d["lists"][0]["items"][0]["sku"] == "STRIP-1",
          r.text[:300])

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
