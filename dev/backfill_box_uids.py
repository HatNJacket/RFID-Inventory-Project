"""One-off (2026-10-07): give box photos taken before box tagging a box.

Photos are walked in the order they were taken. A photo that started a
box (new_box) opens a new box; any other photo joins the box most
recently opened for the same SKU, if that was within 10 minutes; a
photo with nothing to join starts its own. Already-tagged photos are
left alone. Safe to re-run.

Run against whatever DATABASE_URL points at (prod: pull it from az into
the env var without printing it):
    py dev/backfill_box_uids.py
"""
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SHOPIFY_STORE", "backfill.invalid")
os.environ.setdefault("SHOPIFY_CLIENT_ID", "x")
os.environ.setdefault("SHOPIFY_CLIENT_SECRET", "x")
os.environ.setdefault("ORDERS_SYNC_DISABLE", "1")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.database import get_engine, init_db  # noqa: E402
from app.models import BoxPhoto  # noqa: E402

init_db()
with Session(get_engine()) as s:
    rows = s.scalars(select(BoxPhoto).order_by(BoxPhoto.id)).all()
    last_box: dict[str, tuple[str, object]] = {}  # sku -> (box_uid, when)
    made = joined = 0
    for r in rows:
        key = (r.sku or "").upper()
        if r.box_uid:
            last_box[key] = (r.box_uid, r.created_at)
            continue
        prev = last_box.get(key)
        if (not r.new_box and prev and key and r.created_at and prev[1]
                and r.created_at - prev[1] <= timedelta(minutes=10)):
            r.box_uid = prev[0]
            joined += 1
        else:
            r.box_uid = f"legacy-{r.id}"
            made += 1
        last_box[key] = (r.box_uid, r.created_at)
    s.commit()
    print(f"{made} boxes made, {joined} photos joined a box, "
          f"{len(rows)} photos in all")
