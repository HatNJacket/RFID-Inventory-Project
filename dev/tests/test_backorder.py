"""Backorders vs the tag arithmetic (Nick, 2026-08-26, AirGradient):
a delivery landing on NEGATIVE Shopify on-hand loses units to the
oversell hole (on-hand -1 + 48 received = 47 against 48 tagged boxes),
so the Tags vs On-hand check false-flagged forever.
- A receiving close notes the gap as backorder debt, capped at the
  units Shopify shows COMMITTED (a plain mistag can never hide here);
- refresh_mismatch_tasks adds uncleared debt to the expected count and
  names it in the task detail;
- an operator on-hand write supersedes (auto-clears) the note; the
  History event's undo clears one by hand;
- the resolve window's sold-out shortcut retires ALL tags presumed-sold
  when live on-hand is 0 (re-checked LIVE), resolves the task, and the
  grouped History event restores the whole set in one undo."""
import os, sys, tempfile
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_backorder_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app import orders_sync
from app.database import get_engine
from app.models import (BackorderDebt, BarcodeChange, BinMapEntry,
                        RetiredTag, ReviewTask, RfidAssignment, SoldRecord)
from sqlalchemy.orm import Session
from sqlalchemy import select, func
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

KIT = "AG ONE-KIT"
# Mutable Shopify state the mocks read; tests move these numbers to
# play out the story.
STOCK = {KIT: 2, "AG PLAIN": 2, "AG SHORT": 1}
COMMITTED = {KIT: 1, "AG PLAIN": 0, "AG SHORT": 0}

def fake_on_hand_by_skus(skus):
    return {s: STOCK[s] for s in skus if s in STOCK}
def fake_on_hand(sku):
    return STOCK.get(sku)
def fake_breakdown(sku):
    if sku not in STOCK: return None
    return {"available": STOCK[sku] - COMMITTED.get(sku, 0),
            "committed": COMMITTED.get(sku, 0),
            "on_hand": STOCK[sku], "unavailable": 0}

def receive_and_pair(cl, sku, qty, ref, epcs):
    r = cl.post("/api/receiving/prints", json={
        "items": [{"sku": sku, "quantity": qty}],
        "requested_by": "Nick", "reference": ref})
    bid = r.json()["batch"]["id"]
    item = next(i for i in cl.get(f"/api/batches/{bid}").json()["items"]
                if i["sku"] == sku)
    last = None
    for epc in epcs:
        last = cl.post(f"/api/batches/{bid}/pair", json={
            "epc": epc, "item_id": item["id"], "created_by": "Nick"})
    return bid, last

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.shopify.get_on_hand_by_skus",
           side_effect=fake_on_hand_by_skus), \
     patch("app.shopify.get_on_hand", side_effect=fake_on_hand), \
     patch("app.shopify.get_quantity_breakdown",
           side_effect=fake_breakdown), \
     patch("app.main._kick_orders_sync_soon"):
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku=KIT, product_title="AirGradient One KIT",
                          bin="I5-1", qty=2, barcode="801",
                          shopify_variant_id="t:KIT"))
        s.add(BinMapEntry(sku="AG PLAIN", product_title="AirGradient Plain",
                          bin="I5-1", qty=2, barcode="802",
                          shopify_variant_id="t:PLAIN"))
        s.add(BinMapEntry(sku="AG SHORT", product_title="AirGradient Short",
                          bin="I5-1", qty=1, barcode="803",
                          shopify_variant_id="t:SHORT"))
        s.commit()

    # --- A) a receiving close notes the oversell gap -------------------
    # Story: on-hand sat at -1 (one unit committed on backorder), the
    # planner received +3, so Shopify says 2 while 3 tagged boxes land.
    bid, last = receive_and_pair(cl, KIT, 3, "SO 900 · AG", [
        "AA000000000000000000AA01", "AA000000000000000000AA02",
        "AA000000000000000000AA03"])
    check("the receiving closes on the last pair",
          last.json().get("receiving_done") is True, last.text[:200])
    with Session(get_engine()) as s:
        rows = s.scalars(select(BackorderDebt).where(
            BackorderDebt.sku == KIT)).all()
        check("the close notes 1 unit of backorder debt",
              len(rows) == 1 and rows[0].units == 1
              and rows[0].cleared_at is None,
              [r.as_dict() for r in rows])
        check("the note names its receiving batch",
              "SO 900" in (rows[0].source or ""), rows[0].source)
        debt_id = rows[0].id

    hist = cl.get("/api/history").json()["events"]
    noted = next((e for e in hist if e["type"] == "backorder-noted"), None)
    check("History carries the Backorder Noted event with its undo",
          noted is not None and "1 unit(s) behind" in noted["detail"]
          and (noted.get("undo") or {}).get("kind") == "backorder-debt"
          and (noted.get("undo") or {}).get("debt_id") == debt_id, noted)

    # --- B) the committed cap keeps honest gaps visible ----------------
    # A healthy delivery (numbers agree) notes nothing.
    receive_and_pair(cl, "AG PLAIN", 2, "SO 900 · AG", [
        "AA000000000000000000AB01", "AA000000000000000000AB02"])
    # A shortfall with NOTHING committed is a real error, not a
    # backorder - it must stay visible to the daily check.
    receive_and_pair(cl, "AG SHORT", 2, "SO 900 · AG", [
        "AA000000000000000000AC01", "AA000000000000000000AC02"])
    with Session(get_engine()) as s:
        others = s.scalars(select(BackorderDebt).where(
            BackorderDebt.sku != KIT)).all()
        check("no debt is noted without committed units to justify it",
              others == [], [r.as_dict() for r in others])

    # --- C) expected count carries the debt ----------------------------
    with Session(get_engine()) as s:
        out = orders_sync.refresh_mismatch_tasks(s); s.commit()
        open_kit = s.scalars(select(ReviewTask).where(
            ReviewTask.sku == KIT, ReviewTask.status == "open")).all()
        check("tags 3 = on-hand 2 + debt 1: no task opens",
              open_kit == [], [t.detail for t in open_kit])

    # One unit sells and ships: on-hand 1, one sold-unretired. Books
    # still balance THROUGH the debt.
    STOCK[KIT] = 1
    with Session(get_engine()) as s:
        s.add(SoldRecord(order_id="o1", order_name="#1", sku=KIT,
                         quantity=1, fulfilled_at=datetime.now(timezone.utc)
                         + timedelta(minutes=2)))
        s.commit()
        orders_sync.refresh_mismatch_tasks(s); s.commit()
        open_kit = s.scalars(select(ReviewTask).where(
            ReviewTask.sku == KIT, ReviewTask.status == "open")).all()
        check("tags 3 = on-hand 1 + 1 sold + debt 1: still no task",
              open_kit == [], [t.detail for t in open_kit])

    # A REAL extra loss on top does flag - and the detail names the
    # backorder unit as part of the expectation.
    STOCK[KIT] = 0
    with Session(get_engine()) as s:
        orders_sync.refresh_mismatch_tasks(s); s.commit()
        task = s.scalars(select(ReviewTask).where(
            ReviewTask.sku == KIT, ReviewTask.status == "open")).first()
        check("a real shortfall beyond the debt still opens a task",
              task is not None
              and "on customer backorder when received" in task.detail,
              task.detail if task else None)
    STOCK[KIT] = 1
    with Session(get_engine()) as s:
        orders_sync.refresh_mismatch_tasks(s); s.commit()
        task = s.scalars(select(ReviewTask).where(
            ReviewTask.sku == KIT, ReviewTask.status == "open")).first()
        check("the task closes itself when the numbers agree again",
              task is None, task.detail if task else None)

    # --- D) an operator on-hand write supersedes the note --------------
    with Session(get_engine()) as s:
        s.add(BarcodeChange(sku=KIT, changed_field="on-hand",
                            old_barcode="1", new_barcode="1",
                            changed_by="Nick",
                            changed_at=datetime.now(timezone.utc)
                            + timedelta(minutes=5)))
        s.commit()
        orders_sync.refresh_mismatch_tasks(s); s.commit()
        row = s.get(BackorderDebt, debt_id)
        check("an on-hand write auto-clears the note",
              row.cleared_at is not None
              and row.cleared_by == "on-hand write", row.as_dict())
        task = s.scalars(select(ReviewTask).where(
            ReviewTask.sku == KIT, ReviewTask.status == "open")).first()
        check("with the note cleared, the naked gap flags again",
              task is not None
              and "backorder" not in task.detail,
              task.detail if task else None)
        task_id = task.id

    hist = cl.get("/api/history").json()["events"]
    noted = next((e for e in hist if e["type"] == "backorder-noted"), None)
    check("a cleared note no longer offers its undo",
          noted is not None and "undo" not in noted, noted)

    # --- E) the sold-out shortcut in the resolve window ----------------
    ctx = cl.get(f"/api/review-tasks/{task_id}/context").json()
    check("the resolve window context carries live on-hand and tags",
          ctx["live_on_hand"] == 1 and ctx["units_on_file"] == 3
          and ctx["tag_count"] == 3, ctx)

    r = cl.post(f"/api/review-tasks/{task_id}/retire-all-sold",
                json={"changed_by": "Nick", "confirmed": True})
    check("the shortcut refuses while Shopify still expects boxes",
          r.status_code == 422 and "still" in r.text, r.text[:200])

    STOCK[KIT] = 0
    r = cl.post(f"/api/review-tasks/{task_id}/retire-all-sold",
                json={"changed_by": "Nick"})
    check("unconfirmed call describes what will happen (409)",
          r.status_code == 409 and "3 tag(s)" in r.text, r.text[:250])
    r = cl.post(f"/api/review-tasks/{task_id}/retire-all-sold",
                json={"changed_by": "Nick", "confirmed": True})
    check("confirmed: all tags retire and the task resolves",
          r.status_code == 200 and len(r.json()["retired"]) == 3,
          r.text[:250])
    with Session(get_engine()) as s:
        live = s.scalars(select(RfidAssignment).where(
            func.upper(RfidAssignment.sku) == KIT)).all()
        retired = s.scalars(select(RetiredTag).where(
            RetiredTag.sku == KIT)).all()
        check("no live tags remain; 3 presumed-sold tombstones exist",
              live == [] and len(retired) == 3
              and all(t.kind == "presumed-sold" for t in retired),
              (len(live), len(retired)))
        check("the tombstones name their review task",
              all(f"#{task_id}" in (t.note or "") for t in retired),
              [t.note for t in retired])
        sold = s.scalars(select(SoldRecord).where(
            SoldRecord.sku == KIT)).first()
        check("the sold ledger absorbed its matching unit",
              sold.retired == 1, sold.retired)
        task = s.get(ReviewTask, task_id)
        check("the task resolved itself with the sold-out note",
              task.status == "resolved"
              and "presumed sold" in (task.resolution_note or ""),
              (task.status, task.resolution_note))

    hist = cl.get("/api/history").json()["events"]
    fold = next((e for e in hist if e["type"] == "tag-retired"
                 and e.get("epcs")), None)
    check("History folds the pass into one event with a grouped undo",
          fold is not None and len(fold["epcs"]) == 3
          and (fold.get("undo") or {}).get("epcs") == fold["epcs"], fold)

    r = cl.post("/api/assignments/unretire", json={
        "epcs": fold["epcs"], "changed_by": "Nick"})
    check("the grouped undo restores every tag",
          r.status_code == 200 and len(r.json()["restored"]) == 3,
          r.text[:200])
    with Session(get_engine()) as s:
        sold = s.scalars(select(SoldRecord).where(
            SoldRecord.sku == KIT)).first()
        check("undo hands the consumed ledger unit back",
              sold.retired == 0, sold.retired)

    # --- F) clearing a note by hand (the History undo's endpoint) ------
    STOCK[KIT] = 2; COMMITTED[KIT] = 2
    receive_and_pair(cl, KIT, 1, "SO 901 · AG", [
        "AA000000000000000000AA04"])
    with Session(get_engine()) as s:
        row = s.scalars(select(BackorderDebt).where(
            BackorderDebt.sku == KIT,
            BackorderDebt.cleared_at.is_(None))).first()
        check("a later receive notes fresh debt (capped at committed 2)",
              row is not None and row.units == 2,
              row.as_dict() if row else None)
        new_id = row.id
    r = cl.post(f"/api/backorder-debts/{new_id}/clear",
                json={"changed_by": "Nick"})
    check("the manual clear works and says what changes",
          r.status_code == 200 and "may" in r.json()["message"],
          r.text[:250])
    r = cl.post(f"/api/backorder-debts/{new_id}/clear",
                json={"changed_by": "Nick"})
    check("clearing twice is refused", r.status_code == 409, r.text[:150])
    hist = cl.get("/api/history").json()["events"]
    check("History shows the Backorder Cleared event",
          any(e["type"] == "backorder-cleared" for e in hist),
          [e["type"] for e in hist][:20])

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
