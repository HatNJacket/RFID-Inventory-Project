"""One batch per Sort a shipment (Steve, 2026-10-07). The sorter claims
one batch; each order's planner page prints into it through the
receiving sync; the no-order boxes print into it too. Each row keeps
units per order, so a repeat sync never re-books and so-labels reports
only that order's share. Pairing never closes it; the confirm raises
Shopify on-hand once per product for the paired no-order boxes (History
+ Undo, behind the write switch), then closes it."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"] = "t.myshopify.com"
os.environ["SHOPIFY_CLIENT_ID"] = "x"
os.environ["SHOPIFY_CLIENT_SECRET"] = "x"
os.environ["ORDERS_SYNC_DISABLE"] = "1"
os.environ["SHOPIFY_WRITE_MODE"] = "on"
os.environ.pop("STATION_KEY", None)
os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_sortbatch_test.db")
if os.path.exists(db):
    os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\", "/")
import json
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.main import app
from app import config
from app.database import get_engine
from app.models import Batch, BatchItem, BarcodeChange, BinMapEntry, PrintJob

fails = []


def check(label, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + label + ("" if cond else f"  <- {extra}"))
    if not cond:
        fails.append(label)


def jobs(bid, sku=None):
    with Session(get_engine()) as s:
        q = select(func.count()).select_from(PrintJob).where(PrintJob.batch_id == bid)
        if sku:
            q = q.where(PrintJob.sku == sku)
        return s.scalar(q) or 0


def row(bid, sku):
    with Session(get_engine()) as s:
        r = s.scalar(select(BatchItem).where(BatchItem.batch_id == bid,
                                             BatchItem.sku == sku))
        return r.as_dict() if r else None


SHOP = {"F9152A": 5, "F9184D": 2, "F9363B": 0}
writes = []


def get_on_hand(sku):
    return SHOP.get(sku)


def set_on_hand(sku, qty):
    before = SHOP[sku]
    SHOP[sku] = qty
    writes.append((sku, before, qty))
    return before


epc_n = [0]


def pair(cl, bid, sku):
    epc_n[0] += 1
    r = row(bid, sku)
    return cl.post(f"/api/batches/{bid}/pair", json={
        "epc": f"E2{epc_n[0]:022d}", "item_id": r["id"], "created_by": "C72"}).json()


with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.shopify.get_on_hand", side_effect=get_on_hand), \
     patch("app.shopify.set_on_hand", side_effect=set_on_hand), \
     patch("app.main._maybe_refresh_bin_map", return_value=False), \
     patch("app.main._kick_orders_sync_soon"):
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        for i, (sku, b) in enumerate([("F9152A", "I1-2"), ("F9184D", "I1-4"),
                                      ("F9363B", "K1-1")]):
            s.add(BinMapEntry(sku=sku, product_title=f"Svbony {sku}", bin=b,
                              qty=SHOP[sku], barcode=f"3601717704{i}",
                              shopify_variant_id=f"t:{sku}", vendor="Svbony"))
        s.commit()

    # ---- the sorter claims one batch, then adds the second order ------
    r = cl.post("/api/sorter/batch", json={"orders": ["SO 965"], "vendor": "Svbony"}).json()
    bid = r["batch"]["id"]
    check("the sorter's first claim starts a sort batch",
          r["created"] and r["batch"]["created_by"] == "TC-Planner · SO 965 · Svbony", r)
    r = cl.post("/api/sorter/batch", json={"batch_id": bid, "orders": ["so965", "SO 976"],
                                           "vendor": "Svbony"}).json()
    check("a later claim adds the new order to the same batch",
          not r["created"] and r["batch"]["id"] == bid
          and r["batch"]["created_by"] == "TC-Planner · SO 965, SO 976 · Svbony", r)
    check("a bad order number is refused",
          cl.post("/api/sorter/batch", json={"orders": ["965x"]}).status_code == 422)

    # ---- each order's planner page prints into it ----------------------
    out = cl.post("/api/receiving/sync", json={
        "reference": "SO 965 · Svbony", "print": True,
        "items": [{"sku": "F9152A", "barcode": "36017177040", "received_total": 3}]}).json()
    check("SO 965's planner print lands on the sort batch",
          out["queued"] == 3 and jobs(bid, "F9152A") == 3, out)
    out = cl.post("/api/receiving/sync", json={
        "reference": "SO 976 · Svbony", "print": True,
        "items": [{"sku": "F9363B", "barcode": "36017177042", "received_total": 2}]}).json()
    check("SO 976's planner print lands on the same batch",
          out["queued"] == 2 and jobs(bid, "F9363B") == 2, out)
    with Session(get_engine()) as s:
        n = s.scalar(select(func.count()).select_from(Batch).where(Batch.kind == "receiving"))
    check("  ...no second batch was made", n == 1, n)

    # ---- the no-order boxes print into it too --------------------------
    out = cl.post(f"/api/sorter/batch/{bid}/no-order", json={
        "items": [{"sku": "F9152A", "quantity": 8}, {"sku": "F9184D", "quantity": 1}]}).json()
    check("no-order labels queue on the sort batch",
          out["queued"] == 9 and jobs(bid, "F9152A") == 11 and jobs(bid, "F9184D") == 1, out)
    f = row(bid, "F9152A")
    check("the shared row keeps each order's units apart",
          json.loads(f["so_units"]) == {"SO 965": 3, "": 8} and f["no_order_units"] == 8, f)
    with Session(get_engine()) as s:
        h = s.scalar(select(BarcodeChange).where(BarcodeChange.changed_field == "sorter-leftover"))
    check("  ...and History names the sort batch", h and f"#{bid}" in h.new_barcode, h and h.new_barcode)

    # ---- the planner only ever sees its own order's share --------------
    out = cl.post("/api/receiving/sync", json={
        "reference": "SO 965 · Svbony", "print": True,
        "items": [{"sku": "F9152A", "barcode": "36017177040", "received_total": 3}]}).json()
    check("a repeat SO 965 sync books and prints nothing (the 8 aren't its)",
          out["booked"] == [] and out["queued"] == 0, out)
    st = cl.get("/api/receiving/so-labels", params={"reference": "SO 965"}).json()
    a = next(x for x in st["skus"] if x["sku"] == "F9152A")
    check("so-labels shows SO 965's 3, not the row's 11",
          a["booked_units"] == 3 and a["labels_queued"] == 3 and a["labels_owed"] == 0, a)

    # ---- pairing never closes it; the confirm does ---------------------
    for _ in range(5):
        pair(cl, bid, "F9152A")
    pv = cl.get(f"/api/sorter/batch/{bid}/finish").json()
    d = {x["sku"]: x for x in pv["raises"]}
    check("paired boxes fill the order's share first (5 paired = 2 no-order)",
          d["F9152A"]["due"] == 2 and "F9184D" not in d, pv["raises"])
    for _ in range(6):
        last = pair(cl, bid, "F9152A")
    pair(cl, bid, "F9184D")
    pair(cl, bid, "F9363B")
    last = pair(cl, bid, "F9363B")
    with Session(get_engine()) as s:
        status = s.get(Batch, bid).status
    check("a fully paired sort batch stays open for the confirm",
          last.get("receiving_done") and status != "done", (last.get("receiving_done"), status))
    pv = cl.get(f"/api/sorter/batch/{bid}/finish").json()
    d = {x["sku"]: x for x in pv["raises"]}
    check("the confirm preview lists one raise per product with live numbers",
          d["F9152A"]["due"] == 8 and d["F9152A"]["on_hand_now"] == 5
          and d["F9152A"]["on_hand_after"] == 13 and d["F9184D"]["due"] == 1
          and pv["closes"] and pv["untagged"] == 0, pv)
    check("nothing was written to Shopify while pairing", writes == [], writes)

    r = cl.post(f"/api/sorter/batch/{bid}/finish", json={})
    check("finishing without the confirm is refused", r.status_code == 409, r.status_code)
    config.SHOPIFY_WRITE_MODE = "disabled"
    r = cl.post(f"/api/sorter/batch/{bid}/finish", json={"confirmed": True})
    check("the write switch off refuses the raise", r.status_code == 403, r.status_code)
    config.SHOPIFY_WRITE_MODE = "on"
    out = cl.post(f"/api/sorter/batch/{bid}/finish", json={"confirmed": True, "worker": "Steve"}).json()
    check("confirm raises once per product",
          sorted(writes) == [("F9152A", 5, 13), ("F9184D", 2, 3)] and out["closed"], (writes, out))
    with Session(get_engine()) as s:
        hs = s.scalars(select(BarcodeChange).where(BarcodeChange.changed_field == "on-hand")).all()
        status = s.get(Batch, bid).status
    check("  ...each raise is a History on-hand row (so Undo works)",
          len(hs) == 2 and all("(no order, sort batch" in h.product_title for h in hs), [h.product_title for h in hs])
    check("  ...and the batch is finished", status == "done", status)
    check("  ...and the rows remember what was raised",
          row(bid, "F9152A")["onhand_added"] == 8 and row(bid, "F9184D")["onhand_added"] == 1)

    # ---- Undo frees the units ------------------------------------------
    hid = next(h.id for h in hs if h.sku == "F9152A")
    r = cl.post(f"/api/onhand-updates/{hid}/undo", json={"confirmed": True})
    check("undoing a sort raise puts on-hand back and frees the units",
          r.status_code == 200 and SHOP["F9152A"] == 5 and row(bid, "F9152A")["onhand_added"] == 0,
          (r.status_code, SHOP["F9152A"], row(bid, "F9152A")))

    # ---- a finished sort batch takes no more -----------------------------
    r = cl.post(f"/api/sorter/batch/{bid}/no-order", json={"items": [{"sku": "F9184D", "quantity": 1}]})
    check("a finished sort batch takes no more no-order boxes", r.status_code == 409, r.status_code)
    r = cl.post("/api/sorter/batch", json={"batch_id": bid, "orders": ["SO 965"]}).json()
    check("claiming with a finished batch starts a fresh one", r["created"] and r["batch"]["id"] != bid, r)

print()
print(f"{len(fails)} failed" if fails else "all passed")
sys.exit(1 if fails else 0)
