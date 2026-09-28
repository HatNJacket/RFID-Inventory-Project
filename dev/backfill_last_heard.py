"""One-off: seed rfid_assignments.last_heard_at from the sweep history
already on file (EpcCapture rows), taking each tag's NEWEST capture.
Tags no capture ever heard stay NULL - consumers fall back to
assigned_at. Idempotent: re-running only ever moves stamps forward.

Run against whatever DATABASE_URL points at:
    py dev/backfill_last_heard.py            (local/dev sqlite)
Against prod, run it with prod's DATABASE_URL in the environment
(pull it from az into the env var without printing it).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SHOPIFY_STORE", "backfill.invalid")
os.environ.setdefault("SHOPIFY_CLIENT_ID", "x")
os.environ.setdefault("SHOPIFY_CLIENT_SECRET", "x")
os.environ.setdefault("ORDERS_SYNC_DISABLE", "1")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.database import get_engine, init_db  # noqa: E402
from app.models import EpcCapture, RfidAssignment  # noqa: E402

init_db()  # makes sure the column exists before we write it

newest: dict[str, object] = {}
with Session(get_engine()) as s:
    for cap in s.scalars(select(EpcCapture).order_by(EpcCapture.id)):
        when = cap.created_at
        if when is None:
            continue
        for e in (cap.epcs or "").split("\n"):
            e = e.strip().upper()
            if e:
                prev = newest.get(e)
                if prev is None or when > prev:
                    newest[e] = when
    stamped = 0
    for a in s.scalars(select(RfidAssignment)):
        when = newest.get((a.rfid_id or "").strip().upper())
        if when is None:
            continue
        cur = a.last_heard_at
        if cur is None or (
            when.replace(tzinfo=None) if when.tzinfo else when
        ) > (cur.replace(tzinfo=None) if cur.tzinfo else cur):
            a.last_heard_at = when
            stamped += 1
    s.commit()
print(f"OK  {stamped} tag(s) stamped from {len(newest)} heard EPC(s)")
