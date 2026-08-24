"""Windowed sales math + ledger consumption (Nick's 2026-08-24 field
cases, timestamps verbatim from prod):

CASE A (AIRPLUS-256G): 3 tags paired Aug 19 20:56; sweep hears 1; 7
unretired sales but only the 2 fulfilled AFTER the pairing (Aug 21,
Aug 24) may explain the 2 silent tags. The old code said "beyond what
recorded sales explain"; the truth is sales explain everything.

CASE B (EAF PRO): 10 tag records from July; sales history only starts
Aug 8 (2 sales); on-hand 6; sweep hears 5. Sales explain 2, three stay
unexplained, and the reason must say all of it (incl. the history gap
and the on-hand cross-check) instead of blaming sales.

Plus: interleave (sales can never explain more silent tags than exist),
windowed-first ledger consumption, unretire round-trip conservation.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["SHOPIFY_WRITE_MODE"]=(
    "scan_station_only,verify_onhand,verify_onhand_lower")
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_ledgerflow_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from datetime import datetime, timezone
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

UTC = timezone.utc
def dt(*a): return datetime(*a, tzinfo=UTC)

PRODUCTS = {
    "111": {"sku": "CASE-A", "title": "ZWO ASIAIR Plus 256"},
    "222": {"sku": "CASE-B", "title": "ZWO EAF PRO"},
    "333": {"sku": "CASE-C", "title": "Interleave Widget"},
    "444": {"sku": "CASE-D", "title": "Overheard Gadget"},
}
ONHAND = {"CASE-A": 1, "CASE-B": 6, "CASE-C": 3, "CASE-D": 2}

def fake_lookup(t):
    p = PRODUCTS.get(t)
    if p is None:
        for bc, q in PRODUCTS.items():
            if q["sku"].upper() == str(t).upper():
                p, t = q, bc
                break
    if p is None: return None
    return {"shopify_variant_id": "gid:" + p["sku"],
            "shopify_product_id": "gid:p" + p["sku"],
            "product_title": p["title"], "variant_title": None,
            "sku": p["sku"], "barcode": t, "bin_location": "F1-2"}

with patch("app.shopify.lookup_barcode", side_effect=fake_lookup), \
     patch("app.shopify.lookup_barcode_all",
           side_effect=lambda t: [fake_lookup(t)] if fake_lookup(t) else []), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_on_hand",
           side_effect=lambda sku: ONHAND.get(str(sku).upper())), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus",
           side_effect=lambda skus: {
               s: ONHAND[s.upper()] for s in skus if s.upper() in ONHAND
           }), \
     patch("app.shopify.set_on_hand",
           side_effect=lambda sku, qty: ONHAND.get(str(sku).upper())), \
     patch("app.main._kick_orders_sync_soon"):
  with TestClient(app) as cl:
    from sqlalchemy import select
    from sqlalchemy.orm import Session as S
    from app.database import get_engine
    from app.models import (
        Batch, RetiredTag, RfidAssignment, SoldRecord,
    )

    # ---- seed ---------------------------------------------------------
    with S(get_engine()) as s:
        s.add(Batch(bin_name="F1-2", status="done", created_by="Nick",
                    completed_at=dt(2026, 8, 19, 21, 15)))
        # CASE A: 3 live tags paired Aug 19 20:56.
        for i in range(3):
            s.add(RfidAssignment(
                rfid_id=f"A200{i:020d}", shopify_variant_id="gid:CASE-A",
                product_title="ZWO ASIAIR Plus 256", sku="CASE-A",
                bin_location="F1-2",
                assigned_at=dt(2026, 8, 19, 20, 56, 12 + i)))
        # Prod debris: 4 already-retired tags with no ledger consumption.
        for i in range(4):
            s.add(RetiredTag(
                rfid_id=f"A20099{i:018d}", sku="CASE-A",
                product_title="ZWO ASIAIR Plus 256", kind="presumed-sold",
                bin_location="F1-2", retired_by="Nick"))
        # 7 unretired sales; only Aug 21 + Aug 24 fall after the pairing.
        for oid, f in (("o1", dt(2026, 8, 12, 14, 21)),
                       ("o2", dt(2026, 8, 17, 17, 10)),
                       ("o3", dt(2026, 8, 17, 17, 11)),
                       ("o4", dt(2026, 8, 17, 17, 44)),
                       ("o5", dt(2026, 8, 19, 15, 11)),
                       ("o6", dt(2026, 8, 21, 13, 58)),
                       ("o7", dt(2026, 8, 24, 15, 49))):
            s.add(SoldRecord(order_id="gid:A" + oid, order_name="#" + oid,
                             sku="CASE-A", quantity=1, retired=0,
                             fulfilled_at=f))
        # CASE B: 10 tags from July; ledger starts Aug 8 (2 sales).
        for i in range(10):
            s.add(RfidAssignment(
                rfid_id=f"B200{i:020d}", shopify_variant_id="gid:CASE-B",
                product_title="ZWO EAF PRO", sku="CASE-B",
                bin_location="F1-2",
                assigned_at=dt(2026, 7, 27, 16, 12, i + 1)))
        for oid, f in (("p1", dt(2026, 8, 8, 14, 44)),
                       ("p2", dt(2026, 8, 8, 16, 15))):
            s.add(SoldRecord(order_id="gid:B" + oid, order_name="#" + oid,
                             sku="CASE-B", quantity=1, retired=0,
                             fulfilled_at=f))
        # CASE C interleave: 3 tags Aug 10; one PRE-baseline sale Aug 5
        # and two post-baseline sales Aug 15 - more sales than silent
        # tags, sales must never explain more than exist.
        for i in range(3):
            s.add(RfidAssignment(
                rfid_id=f"C200{i:020d}", shopify_variant_id="gid:CASE-C",
                product_title="Interleave Widget", sku="CASE-C",
                bin_location="F1-2",
                assigned_at=dt(2026, 8, 10, 12, 0, i)))
        for oid, f in (("q1", dt(2026, 8, 5, 9, 0)),
                       ("q2", dt(2026, 8, 15, 9, 0)),
                       ("q3", dt(2026, 8, 15, 10, 0))):
            s.add(SoldRecord(order_id="gid:C" + oid, order_name="#" + oid,
                             sku="CASE-C", quantity=1, retired=0,
                             fulfilled_at=f))
        # CASE D: 2 tags on record; the operator will collect only ONE
        # box but the sweep hears both (neighboring shelf) - the split
        # must cap at the collected count, never raise it.
        for i in range(2):
            s.add(RfidAssignment(
                rfid_id=f"D200{i:020d}", shopify_variant_id="gid:CASE-D",
                product_title="Overheard Gadget", sku="CASE-D",
                bin_location="F1-2",
                assigned_at=dt(2026, 8, 1, 12, 0, i)))
        s.commit()

    # ---- the re-tag batch: collect + sweep ----------------------------
    bid = cl.post("/api/batches",
                  json={"bin": "F1-2", "created_by": "Nick"}).json()["id"]
    # Nick collects 1 CASE-A box, 5 CASE-B, 2 CASE-C, 1 CASE-D.
    for code, times in (("111", 1), ("222", 5), ("333", 2), ("444", 1)):
        for _ in range(times):
            cl.post(f"/api/batches/{bid}/scan", json={"code": code})

    with S(get_engine()) as s:
        a_heard = [s.scalars(select(RfidAssignment).where(
            RfidAssignment.sku == "CASE-A")).all()[0].rfid_id]
        b_heard = [t.rfid_id for t in s.scalars(select(RfidAssignment)
                   .where(RfidAssignment.sku == "CASE-B")).all()[:5]]
        c_heard = [t.rfid_id for t in s.scalars(select(RfidAssignment)
                   .where(RfidAssignment.sku == "CASE-C")).all()[:2]]
        d_heard = [t.rfid_id for t in s.scalars(select(RfidAssignment)
                   .where(RfidAssignment.sku == "CASE-D")).all()]
    swept = a_heard + b_heard + c_heard + d_heard

    r = cl.post(f"/api/batches/{bid}/shelf-sweep",
                json={"epcs": swept, "device": "C72",
                      "apply": False}).json()
    st = {x["sku"]: x for x in r["items"]}

    # ---- CASE A: sales fully explain ----------------------------------
    a = st["CASE-A"]
    check("A: only post-pairing sales count (sales_since 2 of 7)",
          a["sales_since"] == 2, a)
    check("A: 2 silent, explained 2, unexplained 0",
          a["explained"] == 2 and a["unexplained"] == 0, a)
    check("A: expected 1 (3 on file - 2 windowed sales) = heard -> match",
          a["expected"] == 1 and a["state"] == "match", a)
    check("A: no coverage gap (ledger starts Aug 12 < baseline Aug 19)",
          a["sales_gap"] is False, a)
    check("A: on-hand reported alongside, never pre-empted",
          a["on_hand"] == 1, a)

    # ---- CASE B: partial, history gap, on-hand cross-check -------------
    b = st["CASE-B"]
    check("B: 5 silent, sales explain 2, 3 unexplained",
          b["explained"] == 2 and b["unexplained"] == 3, b)
    check("B: coverage gap flagged (ledger starts after July tagging)",
          b["sales_gap"] is True, b)
    check("B: presumption stays sales-backed only (2, not 5)",
          b["presumed_sold"] == 2, b)

    # ---- CASE C: interleave cap ---------------------------------------
    c = st["CASE-C"]
    check("C: sales (2 windowed) never explain more than the 1 silent",
          c["explained"] == 1 and c["unexplained"] == 0, c)

    # ---- state ladder agrees with the verify tri-state -----------------
    check("state: A green (sales fully explain)",
          st["CASE-A"]["state"] == "match", st.get("CASE-A"))
    check("state: B yellow (3 unexplained)",
          st["CASE-B"]["state"] == "unheard", st.get("CASE-B"))
    check("state: C green despite heard != expected (all explained)",
          st["CASE-C"]["state"] == "match", st.get("CASE-C"))
    check("state: D green with over_heard noted, never yellow",
          st["CASE-D"]["state"] == "match", st.get("CASE-D"))

    # ---- CASE D: sweep hears MORE than collected -----------------------
    d = st["CASE-D"]
    check("D: over_heard reported (collected 1, heard 2)",
          d["over_heard"] == 1 and d["boxes"] == 1, d)
    r2 = cl.post(f"/api/batches/{bid}/shelf-sweep",
                 json={"epcs": swept, "device": "C72",
                       "apply": True}).json()
    di = next(i for i in cl.get(f"/api/batches/{bid}").json()["items"]
              if i["sku"] == "CASE-D")
    check("D: split caps at the collected count (1, not 2)",
          di["tagged_before"] == 1 and di["qty_scanned"] == 0, di)
    check("D: apply result still carries over_heard",
          next(x for x in r2["items"] if x["sku"] == "CASE-D")
          ["over_heard"] == 1, r2)

    # ---- verify wording -----------------------------------------------
    v = cl.post(f"/api/batches/{bid}/verify", json={"epcs": swept}).json()
    vst = {x["sku"]: x for x in v["items"] if x["sku"]}
    check("verify A: ok, 'recorded sales account for all 2'",
          vst["CASE-A"]["state"] == "ok"
          and "account for all 2" in vst["CASE-A"]["reason"],
          vst.get("CASE-A"))
    check("verify: the old blame string is gone",
          all("beyond what recorded" not in (x["reason"] or "")
              for x in v["items"]), [x["reason"] for x in v["items"]])
    br = vst["CASE-B"]
    check("verify B: yellow prior-silent",
          br["state"] == "prior-silent", br)
    check("verify B: reason carries the sales split",
          "only account for 2" in br["reason"], br["reason"])
    check("verify B: reason carries the on-hand cross-check",
          "on-hand 6" in br["reason"], br["reason"])
    check("verify B: reason names the history gap",
          "sales history only starts Aug 08" in br["reason"],
          br["reason"])
    check("verify B: no em dash in the reason",
          "\u2014" not in br["reason"], br["reason"])

    check("can_lower: C's 1-unit drop is sales-backed -> offered",
          vst["CASE-C"]["can_lower"] is True, vst.get("CASE-C"))
    check("can_lower: B's 1-unit drop (6 -> 5) is also sales-backed",
          vst["CASE-B"]["can_lower"] is True, vst.get("CASE-B"))
    check("can_lower: A has no drop -> not offered",
          vst["CASE-A"]["can_lower"] is False, vst.get("CASE-A"))

    # ---- ledger consumption: windowed rows first, oldest first ---------
    a_silent = [e for e in st["CASE-A"]["unheard_epcs"]]
    check("A retire offer = exactly the 2 silent EPCs",
          len(a_silent) == 2, a_silent)
    r = cl.post("/api/assignments/retire",
                json={"epcs": a_silent, "kind": "presumed-sold",
                      "changed_by": "Nick", "note": "test"})
    check("retire succeeds", r.status_code == 200, r.text)
    with S(get_engine()) as s:
        rows = {x.order_name: (x.retired or 0) for x in s.scalars(
            select(SoldRecord).where(SoldRecord.sku == "CASE-A"))}
        consumed = [x.ledger_consumed for x in s.scalars(
            select(RetiredTag).where(
                RetiredTag.rfid_id.in_(a_silent)))]
    check("windowed sales consumed first (Aug 21 + Aug 24), oldest first",
          rows["#o6"] == 1 and rows["#o7"] == 1
          and all(rows[k] == 0 for k in
                  ("#o1", "#o2", "#o3", "#o4", "#o5")), rows)
    check("each retirement recorded its consumption",
          sorted(consumed) == [1, 1], consumed)

    # ---- unretire restores the ledger exactly -------------------------
    r = cl.post("/api/assignments/unretire",
                json={"epcs": a_silent, "changed_by": "Nick"})
    check("unretire succeeds", r.status_code == 200, r.text)
    with S(get_engine()) as s:
        total = sum((x.retired or 0) for x in s.scalars(
            select(SoldRecord).where(SoldRecord.sku == "CASE-A")))
        back = s.scalars(select(RfidAssignment).where(
            RfidAssignment.rfid_id.in_(a_silent))).all()
    check("ledger fully restored (retired sum back to 0)",
          total == 0, total)
    check("tags are live again", len(back) == 2, back)

    # ---- replaced/dead never touch the ledger -------------------------
    cl.post("/api/assignments/retire",
            json={"epcs": [a_silent[0]], "kind": "dead",
                  "changed_by": "Nick"})
    with S(get_engine()) as s:
        total = sum((x.retired or 0) for x in s.scalars(
            select(SoldRecord).where(SoldRecord.sku == "CASE-A")))
        rt = s.scalar(select(RetiredTag).where(
            RetiredTag.rfid_id == a_silent[0]))
    check("dead retire consumes no ledger units",
          total == 0 and rt.ledger_consumed == 0, (total, rt))

    # ---- lower on-hand: gate, one-click resolution, undo ---------------
    c_silent = [e for e in st["CASE-C"]["unheard_epcs"]]

    r = cl.post("/api/onhand-updates/lower", json={
        "sku": "CASE-B", "bin_name": "F1-2", "new_qty": 3,
        "epcs": [], "changed_by": "Nick", "confirmed": True})
    check("gate refuses a drop sales can't cover (B: drop 3 > sales 2)",
          r.status_code == 422 and "only account for 2" in r.text,
          (r.status_code, r.text))

    r = cl.post("/api/onhand-updates/lower", json={
        "sku": "CASE-C", "bin_name": "F1-2", "new_qty": 2,
        "epcs": [c_heard[0]], "changed_by": "Nick",
        "confirmed": True, "batch_id": bid})
    check("gate refuses retiring a tag the sweep HEARD",
          r.status_code == 422 and "answered the shelf sweep" in r.text,
          (r.status_code, r.text))

    r = cl.post("/api/onhand-updates/lower", json={
        "sku": "CASE-C", "bin_name": "F1-2", "new_qty": 2,
        "epcs": c_silent + c_heard, "changed_by": "Nick",
        "confirmed": True})
    check("gate refuses more EPCs than the drop",
          r.status_code == 422 and "only drops by" in r.text,
          (r.status_code, r.text))

    r = cl.post("/api/onhand-updates/lower", json={
        "sku": "CASE-C", "bin_name": "F1-2", "new_qty": 2,
        "epcs": c_silent, "changed_by": "Nick", "confirmed": False})
    check("unconfirmed lower answers 409 with the consequences",
          r.status_code == 409 and "retires 1 silent tag(s)" in r.text,
          (r.status_code, r.text))

    r = cl.post("/api/onhand-updates/lower", json={
        "sku": "CASE-C", "bin_name": "F1-2", "new_qty": 2,
        "epcs": c_silent, "changed_by": "Nick", "confirmed": True,
        "batch_id": bid})
    check("confirmed lower succeeds (C: 3 -> 2, 1 tag retired)",
          r.status_code == 201 and r.json()["retired"] == c_silent,
          (r.status_code, r.text))
    low_id = r.json()["change_id"]
    with S(get_engine()) as s:
        rt = s.scalar(select(RetiredTag).where(
            RetiredTag.rfid_id == c_silent[0]))
        rows = {x.order_name: (x.retired or 0) for x in s.scalars(
            select(SoldRecord).where(SoldRecord.sku == "CASE-C"))}
    check("lower retired the tag with the undo marker note",
          rt is not None and rt.kind == "presumed-sold"
          and rt.note == f"onhand-lower #{low_id}", rt and rt.note)
    check("lower consumed the windowed sale first (Aug 15, not Aug 5)",
          rows["#q2"] == 1 and rows["#q1"] == 0, rows)

    r = cl.post(f"/api/onhand-updates/{low_id}/undo-lower",
                json={"changed_by": "Nick", "confirmed": False})
    check("unconfirmed undo-lower answers 409",
          r.status_code == 409 and "restores 1 retired tag(s)" in r.text,
          (r.status_code, r.text))
    r = cl.post(f"/api/onhand-updates/{low_id}/undo-lower",
                json={"changed_by": "Nick", "confirmed": True})
    check("undo-lower succeeds", r.status_code == 200, r.text)
    with S(get_engine()) as s:
        live_again = s.scalar(select(RfidAssignment).where(
            RfidAssignment.rfid_id == c_silent[0]))
        total = sum((x.retired or 0) for x in s.scalars(
            select(SoldRecord).where(SoldRecord.sku == "CASE-C")))
    check("undo-lower restores the tag and the ledger",
          live_again is not None and total == 0, (live_again, total))

    # ---- increase path regression --------------------------------------
    r = cl.post("/api/onhand-updates", json={
        "sku": "CASE-C", "new_qty": 2, "changed_by": "Nick",
        "confirmed": True})
    check("increase endpoint still refuses lowering (422)",
          r.status_code == 422 and "only RAISES" in r.text,
          (r.status_code, r.text))

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
