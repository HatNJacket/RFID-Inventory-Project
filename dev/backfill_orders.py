"""One-off sold-ledger backfill (Nick, 2026-08-24).

The orders sync started 2026-08-18 with a 7-day lookback, so the ledger
has almost no sales before mid-August while tag pools go back to July.
This pages fulfilled orders over the FULL window the read_orders scope
allows (the last 60 days; older needs read_all_orders) and inserts them
through the same dedup as the sync, so re-running is a no-op.

Run it with prod credentials in the environment:

    set DATABASE_URL=<prod mssql url>
    set SHOPIFY_STORE=... SHOPIFY_CLIENT_ID=... SHOPIFY_CLIENT_SECRET=...
    py dev/backfill_orders.py [YYYY-MM-DD]

The optional argument is the window start (default: 59 days ago, just
inside the API limit). Only SKUs with tags on file are recorded (same
rule as the sync). Finishes with a mismatch-task refresh so the new
sales are reasoned about immediately.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import orders_sync, shopify  # noqa: E402
from app.database import get_engine  # noqa: E402
from app.models import SoldRecord  # noqa: E402

start = (
    sys.argv[1]
    if len(sys.argv) > 1
    else (datetime.now(timezone.utc) - timedelta(days=59))
    .strftime("%Y-%m-%d")
)
search = f"updated_at:>={start} fulfillment_status:shipped"
print(f"Backfilling fulfilled orders updated since {start} ...")

orders = []
cursor = None
pages = 0
while pages < 200:  # generous; the sync's 10-page cap is too small here
    data = shopify.query_shopify(
        shopify._ORDERS_QUERY, {"search": search, "cursor": cursor}
    )
    block = data["orders"]
    for node in block["nodes"]:
        if node.get("displayFulfillmentStatus") != "FULFILLED":
            continue
        fulfilled_at = None
        for f in node.get("fulfillments") or []:
            if f.get("createdAt"):
                fulfilled_at = max(fulfilled_at or "", f["createdAt"])
        lines = [
            {"sku": li["sku"], "qty": li["quantity"]}
            for li in node["lineItems"]["nodes"]
            if li.get("sku") and (li.get("quantity") or 0) > 0
        ]
        if lines:
            orders.append({
                "order_id": node["id"],
                "name": node["name"],
                "fulfilled_at": fulfilled_at,
                "lines": lines,
            })
    pages += 1
    if not block["pageInfo"]["hasNextPage"]:
        break
    cursor = block["pageInfo"]["endCursor"]
print(f"  {len(orders)} fulfilled orders fetched over {pages} page(s).")
if pages >= 200:
    print("  WARNING: page cap hit, the window was NOT fully covered.")

added = 0
skipped = 0
untracked = 0
with Session(get_engine()) as s:
    tracked = orders_sync.tracked_skus(s)
    for order in orders:
        for line in order["lines"]:
            sku = (line["sku"] or "").strip()
            if sku.upper() not in tracked:
                untracked += 1
                continue
            row = s.scalars(
                select(SoldRecord).where(
                    SoldRecord.order_id == order["order_id"],
                    func.upper(SoldRecord.sku) == sku.upper(),
                )
            ).first()
            if row is not None:
                skipped += 1
                continue
            s.add(SoldRecord(
                order_id=order["order_id"],
                order_name=(order.get("name") or "")[:32] or None,
                sku=sku,
                quantity=line["qty"],
                fulfilled_at=orders_sync._parse_iso(order["fulfilled_at"]),
            ))
            added += 1
    s.commit()
    print(f"  {added} ledger row(s) added, {skipped} already present, "
          f"{untracked} line(s) on untracked SKUs ignored.")
    res = orders_sync.refresh_mismatch_tasks(s)
    s.commit()
    print(f"  mismatch tasks: {res}")
print("Done.")
