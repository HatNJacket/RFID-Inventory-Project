"""One-off (2026-10-07): give box photos read before layouts were kept
their reader layout (every line and word with its position and Azure's
confidence), so they can feed SKU-line training crops and the
layout-aware matching checks.

Each photo is fetched from the server, read again by Azure (paced to
the free tier: 20 calls a minute, polls included, so one read every
15 s leaves room for live uploads) and only ocr_layout is written; the
stored text, status and labels are untouched. Safe to re-run: photos
that already have a layout are skipped.

Needs DATABASE_URL, VISION_ENDPOINT, VISION_KEY and STATION_KEY in the
environment (pull them from az without printing them):
    py dev/backfill_layout.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SHOPIFY_STORE", "backfill.invalid")
os.environ.setdefault("SHOPIFY_CLIENT_ID", "x")
os.environ.setdefault("SHOPIFY_CLIENT_SECRET", "x")
os.environ.setdefault("ORDERS_SYNC_DISABLE", "1")

import requests  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import boxphotos as bp  # noqa: E402
from app.database import get_engine  # noqa: E402
from app.models import BoxPhoto  # noqa: E402

BASE = os.getenv("RFID_BASE", "https://telcan-rfid.azurewebsites.net")
HEAD = {"X-Station-Key": os.environ["STATION_KEY"]}

with Session(get_engine()) as s:
    ids = s.scalars(select(BoxPhoto.id).where(BoxPhoto.ocr_layout.is_(None))
                    .order_by(BoxPhoto.id)).all()
print(f"{len(ids)} photos need a layout")
done = failed = 0
for pid in ids:
    started = time.time()
    try:
        img = requests.get(f"{BASE}/api/boxphotos/{pid}/image", headers=HEAD,
                           timeout=60)
        if img.status_code != 200:
            failed += 1
            continue
        read = bp.azure_read(img.content)
        if read[1] or len(read) < 3:
            print(f"  {pid}: {read[1]}")
            failed += 1
        else:
            with Session(get_engine()) as s:
                row = s.get(BoxPhoto, pid)
                row.ocr_layout = json.dumps(read[2])[:200000]
                s.commit()
            done += 1
    except Exception as error:  # noqa: BLE001
        print(f"  {pid}: {error}")
        failed += 1
    # ~3 calls a read against 20 a minute, leaving room for live uploads.
    time.sleep(max(0.0, 15.0 - (time.time() - started)))
print(f"{done} layouts written, {failed} failed")
