"""One-off prod migration (Nick, 2026-09-15): performance indexes plus
column widenings. All metadata-only operations - no data is touched.

 1. Index rfid_bin_map.barcode - the barcode equality test is the FIRST
    query of every scan-station lookup and had no index.
 2. Widen the columns that were the app's tightest storage caps (we use
    ~20% of the database tier, so there is room to stop truncating):
      rfid_barcode_changes.old_barcode / new_barcode  64  -> 255
      rfid_print_jobs.error                          500  -> 1000
      rfid_openbox_returns.not_epcs                  500  -> 2000
      rfid_review_notes.note                         500  -> 1000
      rfid_review_tasks.detail                       500  -> 1000
      rfid_app_settings.value                        500  -> 2000
      other_bins (print_jobs / batch_items / bin_map) 255 -> 500
      image_url  (batch_items / bin_map)             500  -> 1000
    new_barcode is indexed, so its index is dropped and recreated
    around the ALTER.

Idempotent: already-wide columns and existing indexes are skipped.

Run against prod:

    set DATABASE_URL=<prod mssql url>
    py dev/alter_perf_and_widen.py           (report only)
    py dev/alter_perf_and_widen.py --apply
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SHOPIFY_STORE", "t.myshopify.com")
os.environ.setdefault("SHOPIFY_CLIENT_ID", "x")
os.environ.setdefault("SHOPIFY_CLIENT_SECRET", "x")

from sqlalchemy import text  # noqa: E402

from app.database import get_engine  # noqa: E402

APPLY = "--apply" in sys.argv

WIDEN = [
    # (table, column, new length, drop+recreate this index around it)
    ("rfid_barcode_changes", "old_barcode", 255, None),
    ("rfid_barcode_changes", "new_barcode", 255,
     "ix_rfid_barcode_changes_new_barcode"),
    ("rfid_print_jobs", "error", 1000, None),
    ("rfid_print_jobs", "other_bins", 500, None),
    ("rfid_openbox_returns", "not_epcs", 2000, None),
    ("rfid_review_notes", "note", 1000, None),
    ("rfid_review_tasks", "detail", 1000, None),
    ("rfid_app_settings", "value", 2000, None),
    ("rfid_batch_items", "other_bins", 500, None),
    ("rfid_batch_items", "image_url", 1000, None),
    ("rfid_bin_map", "other_bins", 500, None),
    ("rfid_bin_map", "image_url", 1000, None),
]

NEW_INDEXES = [
    ("rfid_bin_map", "barcode", "ix_rfid_bin_map_barcode"),
]

engine = get_engine()
if engine.dialect.name != "mssql":
    print(f"Refusing to run against dialect {engine.dialect.name!r} - "
          "this script is for the Azure SQL prod database.")
    sys.exit(1)

with engine.connect() as conn:
    def col_info(table, column):
        row = conn.execute(text(
            "SELECT DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = :t AND COLUMN_NAME = :c"
        ), {"t": table, "c": column}).first()
        return row

    def index_exists(name):
        return conn.execute(text(
            "SELECT 1 FROM sys.indexes WHERE name = :n"), {"n": name}
        ).first() is not None

    for table, column, newlen, guarded_index in WIDEN:
        info = col_info(table, column)
        if info is None:
            print(f"SKIP  {table}.{column}: column not found")
            continue
        dtype, curlen, nullable = info
        if dtype not in ("varchar", "nvarchar"):
            print(f"SKIP  {table}.{column}: unexpected type {dtype}")
            continue
        if curlen is not None and (curlen < 0 or curlen >= newlen):
            print(f"OK    {table}.{column}: already {dtype}({curlen})")
            continue
        null_sql = "NULL" if nullable == "YES" else "NOT NULL"
        print(f"WIDEN {table}.{column}: {dtype}({curlen}) -> "
              f"{dtype}({newlen}) {null_sql}")
        if APPLY:
            if guarded_index and index_exists(guarded_index):
                conn.execute(text(
                    f"DROP INDEX {guarded_index} ON {table}"))
            conn.execute(text(
                f"ALTER TABLE {table} ALTER COLUMN {column} "
                f"{dtype}({newlen}) {null_sql}"))
            if guarded_index:
                conn.execute(text(
                    f"CREATE INDEX {guarded_index} ON {table} ({column})"))

    for table, column, name in NEW_INDEXES:
        if index_exists(name):
            print(f"OK    index {name} already exists")
            continue
        print(f"INDEX {name} ON {table}({column})")
        if APPLY:
            conn.execute(text(f"CREATE INDEX {name} ON {table} ({column})"))

    if APPLY:
        conn.commit()
        print("APPLIED.")
    else:
        print("Report only - re-run with --apply.")
