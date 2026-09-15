"""Backfill the sold ledger with SHIPPED lines of partially-fulfilled
orders (Nick, 2026-09-15: order #50260's 8h0045 line shipped Sept 11
while a sibling line stayed backordered, and the old whole-order gate
kept the sale out of the ledger - expected counts undercounted and
Review tasks fired).

Walks every order updated since the ledger's first lookback, with the
line-level counting now in shopify.get_fulfilled_orders, and applies
the SAME upsert the sync uses (tracked SKUs only, per-order-per-SKU
rows, quantity raised when more of a line ships). Then one mismatch
refresh so agreeing tasks close.

    py dev/backfill_partial_orders.py            (dry: shows plan)
    py dev/backfill_partial_orders.py --apply

Needs %TEMP%/dburl.txt and SHOPIFY_* env vars.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

if not os.environ.get("DATABASE_URL"):
    with open(os.path.join(os.environ["TEMP"], "dburl.txt"),
              encoding="utf-8-sig") as f:
        os.environ["DATABASE_URL"] = f.read().strip().replace(
            "mssql://", "mssql+pymssql://", 1)
os.environ.setdefault("ORDERS_SYNC_DISABLE", "1")

from sqlalchemy import select, func  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.database import get_engine  # noqa: E402
from app.models import SoldRecord  # noqa: E402
from app import orders_sync, shopify  # noqa: E402

APPLY = "--apply" in sys.argv
SINCE = "2026-07-20T00:00:00Z"  # ledger's first lookback era

orders = shopify.get_fulfilled_orders(SINCE, max_pages=200)
print(f"{len(orders)} order(s) with shipped units since {SINCE}")

with Session(get_engine()) as s:
    tracked = orders_sync.tracked_skus(s)
    adds = []
    bumps = []
    for order in orders:
        for line in order["lines"]:
            sku = (line["sku"] or "").strip()
            if sku.upper() not in tracked:
                continue
            row = s.scalars(
                select(SoldRecord).where(
                    SoldRecord.order_id == order["order_id"],
                    func.upper(SoldRecord.sku) == sku.upper(),
                )
            ).first()
            if row is None:
                adds.append((order, sku, line["qty"]))
            elif row.quantity != line["qty"]:
                bumps.append((order, sku, row.quantity, line["qty"]))
    for order, sku, qty in adds:
        print(f"  ADD  {order.get('name')}  {sku} x{qty} "
              f"(fulfilled {order.get('fulfilled_at')})")
    for order, sku, old, new in bumps:
        print(f"  BUMP {order.get('name')}  {sku} {old} -> {new}")
    print(f"{len(adds)} missing sold record(s), {len(bumps)} quantity "
          "update(s).")
    if not APPLY:
        print("Dry run - nothing written. Re-run with --apply.")
        sys.exit(0)
    for order, sku, qty in adds:
        s.add(SoldRecord(
            order_id=order["order_id"],
            order_name=(order.get("name") or "")[:32] or None,
            sku=sku,
            quantity=qty,
            fulfilled_at=orders_sync._parse_iso(
                order.get("fulfilled_at")),
        ))
    for order, sku, old, new in bumps:
        row = s.scalars(
            select(SoldRecord).where(
                SoldRecord.order_id == order["order_id"],
                func.upper(SoldRecord.sku) == sku.upper(),
            )
        ).first()
        row.quantity = new
    s.flush()
    res = orders_sync.refresh_mismatch_tasks(s)
    s.commit()
    print(f"written. mismatch refresh: {res}")
