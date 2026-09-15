"""One-off ALTER for prod (run BEFORE deploying the 2026-09-15
multi-box redo): rfid_batch_items gains the "Part of a set" mark
columns (master SKU + Box X of Y, taken at collect, resolved on the
web during verification).

    py dev/alter_add_setmarks.py          (dry: shows columns)
    py dev/alter_add_setmarks.py --apply
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
            "WHERE TABLE_NAME='rfid_batch_items'"))
    }
    print("existing columns:", sorted(cols))
    todo = []
    if "set_mark_master" not in cols:
        todo.append("ALTER TABLE rfid_batch_items "
                    "ADD set_mark_master NVARCHAR(100) NULL")
    if "set_mark_box" not in cols:
        todo.append("ALTER TABLE rfid_batch_items "
                    "ADD set_mark_box INT NULL")
    if "set_mark_total" not in cols:
        todo.append("ALTER TABLE rfid_batch_items "
                    "ADD set_mark_total INT NULL")
    if not todo:
        print("nothing to do - all three columns exist.")
        sys.exit(0)
    for stmt in todo:
        print(("APPLYING: " if APPLY else "WOULD RUN: ") + stmt)
    if not APPLY:
        sys.exit(0)

with eng.begin() as c:
    for stmt in todo:
        c.execute(text(stmt))
print("done.")
