"""One-off prod migration: the Inventory Check merger (Nick, 2026-09-02).

1. Creates rfid_onhand_log (the on-hand observation memory).
2. Folds open "tag-onhand-mismatch" tasks into "inventory-check":
   - no open inventory-check for the SKU -> the task is re-categorized
     in place (history intact);
   - an open inventory-check exists -> the arithmetic task resolves
     with a pointer note (one task per SKU from now on).
3. Closes every open "pairing-incomplete" task - the category is
   retired (printed-unused labels are normal; receiving leftovers live
   on held strips).

Run ONCE against prod before deploying:

    set DATABASE_URL=<prod mssql url, from the app's Azure settings>
    py dev/migrate_review_merge.py

Safe to re-run: create() checks first; the task sweeps only touch
open rows in the old categories.
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from app.database import get_engine  # noqa: E402
from app.models import OnhandLog, ReviewTask  # noqa: E402

engine = get_engine()
OnhandLog.__table__.create(engine, checkfirst=True)
print("rfid_onhand_log is present.")

now = datetime.now(timezone.utc)
with Session(engine) as s:
    open_inv = {
        (t.sku or "").strip().upper(): t
        for t in s.scalars(select(ReviewTask).where(
            ReviewTask.category == "inventory-check",
            ReviewTask.status == "open",
        ))
        if t.sku
    }
    moved = merged = 0
    for t in s.scalars(select(ReviewTask).where(
        ReviewTask.category == "tag-onhand-mismatch",
        ReviewTask.status == "open",
    )).all():
        key = (t.sku or "").strip().upper()
        keeper = open_inv.get(key)
        if keeper is not None:
            t.status = "resolved"
            t.resolved_by = "orders-sync"
            t.resolved_at = now
            t.resolution_note = (
                f"Merged into Inventory Check #{keeper.id} - one check "
                "per SKU from now on."
            )[:255]
            merged += 1
        else:
            t.category = "inventory-check"
            if key:
                open_inv[key] = t
            moved += 1
    retired = 0
    for t in s.scalars(select(ReviewTask).where(
        ReviewTask.category == "pairing-incomplete",
        ReviewTask.status == "open",
    )).all():
        t.status = "resolved"
        t.resolved_by = "category-retired"
        t.resolved_at = now
        t.resolution_note = (
            "Category retired (Nick, 2026-09-02): printed-but-unused "
            "labels are normal; receiving leftovers live on held vendor "
            "strips."
        )[:255]
        retired += 1
    s.commit()
print(f"tag-onhand-mismatch: {moved} re-categorized, {merged} merged.")
print(f"pairing-incomplete: {retired} closed (category retired).")
print("Done.")
