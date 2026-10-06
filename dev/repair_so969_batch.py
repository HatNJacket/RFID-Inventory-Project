"""One-off (2026-10-06): SO 969's first receive lived on two duplicate
batches (#356 and #357, both from the overlapping-sync bug) and BOTH got
abandoned during cleanup. Abandoned batches no longer count as booked,
so without this the planner's next Save would re-book those 69 units
and offer their labels again. #357 printed the labels that went on the
boxes, so it comes back as the finished record. Idempotent; logs to
History as "receiving-restored". DATABASE_URL is read from the prod app
settings and never printed.

    py dev/repair_so969_batch.py          # dry run
    py dev/repair_so969_batch.py --apply
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

raw = subprocess.run(
    ["az", "webapp", "config", "appsettings", "list", "-n", "telcan-rfid",
     "-g", "shopify-automation-rg", "-o", "json"],
    capture_output=True, text=True, shell=True, check=True,
).stdout
for row in json.loads(raw.lstrip("﻿")):
    if row["name"] == "DATABASE_URL":
        os.environ["DATABASE_URL"] = row["value"]
os.environ.setdefault("SHOPIFY_STORE", "x")
os.environ.setdefault("SHOPIFY_CLIENT_ID", "x")
os.environ.setdefault("SHOPIFY_CLIENT_SECRET", "x")

from sqlalchemy.orm import Session  # noqa: E402

from app.database import get_engine  # noqa: E402
from app.models import BarcodeChange, Batch  # noqa: E402

APPLY = "--apply" in sys.argv
BATCH_ID = 357

with Session(get_engine()) as s:
    b = s.get(Batch, BATCH_ID)
    if b is None:
        sys.exit(f"batch {BATCH_ID} not found")
    print(f"batch {b.id}: status={b.status} created_by={b.created_by!r}")
    if b.status != "abandoned":
        print("nothing to do")
        sys.exit(0)
    if not APPLY:
        print("dry run - rerun with --apply to mark it done")
        sys.exit(0)
    b.status = "done"
    b.completed_at = datetime.now(timezone.utc)
    s.add(BarcodeChange(
        sku=None, product_title=b.created_by, changed_field="receiving-restored",
        old_barcode="abandoned", new_barcode=(
            f"Receiving #{b.id} restored as done - it holds SO 969's first "
            "receive (69 units) and the labels on those boxes"),
        changed_by="Claude (SO 969 repair)",
    ))
    s.commit()
    print(f"batch {b.id} is now done")
