"""One-off (2026-10-07): give print jobs queued before strips were kept
a strip id and position, so FIND A LABEL works on labels already on the
bench.

Jobs are walked in id order (the order the agent printed them). A job
joins the previous job's strip when it was queued within 2 seconds of it
and both belong to a batch (or both to none) - one PRINT press, one
planner print, or a whole-strip run with its side trips. Already-stamped
jobs are left alone. Safe to re-run.

    py dev/backfill_print_strips.py
(against prod: pull DATABASE_URL from az into the env without printing it)
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
from app.models import PrintJob  # noqa: E402

init_db()
with Session(get_engine()) as s:
    rows = s.scalars(select(PrintJob).where(PrintJob.strip_id.is_(None))
                     .order_by(PrintJob.id)).all()
    prev = None
    strips = 0
    for job in rows:
        same = (prev is not None and job.created_at and prev.created_at
                and job.created_at - prev.created_at <= timedelta(seconds=2)
                and bool(job.batch_id) == bool(prev.batch_id))
        if same:
            job.strip_id = prev.strip_id
            job.strip_pos = (prev.strip_pos or 0) + 1
        else:
            job.strip_id = f"legacy{job.id}"
            job.strip_pos = 1
            strips += 1
        prev = job
    s.commit()
    print(f"{len(rows)} jobs stamped into {strips} strips")
