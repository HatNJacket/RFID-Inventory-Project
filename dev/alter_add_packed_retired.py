"""One-off ALTER for prod (run BEFORE deploying the 2026-09-14 packed-
orders rework): rfid_epc_captures gains the spent-sweep stamp.

    py dev/alter_add_packed_retired.py          (dry: shows columns)
    py dev/alter_add_packed_retired.py --apply
"""
import os
import sys

with open(os.path.join(os.environ["TEMP"], "dburl.txt"),
          encoding="utf-8-sig") as f:
    url = f.read().strip().replace("mssql://", "mssql+pymssql://", 1)
from sqlalchemy import create_engine, text  # noqa: E402

eng = create_engine(url)
APPLY = "--apply" in sys.argv

with eng.connect() as c:
    cols = {
        r.COLUMN_NAME.lower()
        for r in c.execute(text(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME='rfid_epc_captures'"))
    }
    print("existing columns:", sorted(cols))
    todo = []
    if "packed_retired_at" not in cols:
        todo.append("ALTER TABLE rfid_epc_captures "
                    "ADD packed_retired_at DATETIMEOFFSET NULL")
    if "packed_retired_by" not in cols:
        todo.append("ALTER TABLE rfid_epc_captures "
                    "ADD packed_retired_by NVARCHAR(100) NULL")
    if not todo:
        print("nothing to do - both columns exist.")
        sys.exit(0)
    for stmt in todo:
        print(("APPLYING: " if APPLY else "WOULD RUN: ") + stmt)
    if not APPLY:
        sys.exit(0)

with eng.begin() as c:
    for stmt in todo:
        c.execute(text(stmt))
print("done.")
