"""One-off prod migration for the 2026-09-08 flag upgrades:

1. rfid_non_taggable gains a `kind` column ("non-taggable" |
   "unlabelable-box"), and every EXISTING row is switched to
   "unlabelable-box" — Nick's call: the thumbscrew-style products get a
   box label + location and show on-hand again; the plain non-taggable
   flag stays available for anything truly outside the system.
2. rfid_mislabel_flags gains `alt_skus` (the products a mis-printed
   label might actually be), and the known EXOS pair is cross-linked
   both ways so the picker works day one.

Run ONCE against prod before deploying:

    set DATABASE_URL=<prod mssql url, from the app's Azure settings>
    py dev/alter_add_flag_kinds.py

Safe to re-run: each step checks before altering.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text  # noqa: E402

from app.database import get_engine  # noqa: E402

engine = get_engine()
insp = inspect(engine)

with engine.begin() as conn:
    cols = {c["name"] for c in insp.get_columns("rfid_non_taggable")}
    if "kind" not in cols:
        conn.execute(text(
            "ALTER TABLE rfid_non_taggable ADD kind NVARCHAR(20) "
            "NOT NULL DEFAULT 'non-taggable'"
        ))
        print("rfid_non_taggable.kind added.")
    else:
        print("rfid_non_taggable.kind already present.")

with engine.begin() as conn:
    n = conn.execute(text(
        "UPDATE rfid_non_taggable SET kind = 'unlabelable-box' "
        "WHERE kind = 'non-taggable'"
    )).rowcount
    print(f"{n} existing non-taggable row(s) switched to unlabelable-box.")

with engine.begin() as conn:
    cols = {c["name"] for c in insp.get_columns("rfid_mislabel_flags")}
    if "alt_skus" not in cols:
        conn.execute(text(
            "ALTER TABLE rfid_mislabel_flags ADD alt_skus NVARCHAR(MAX)"
        ))
        print("rfid_mislabel_flags.alt_skus added.")
    else:
        print("rfid_mislabel_flags.alt_skus already present.")

# Cross-link the known EXOS pair (both flags already exist on prod);
# only fills empty lists, never overwrites an operator's edits.
with engine.begin() as conn:
    for sku, alt in (("EXOS2CW", "EXOS2CWB5"), ("EXOS2CWB5", "EXOS2CW")):
        n = conn.execute(
            text(
                "UPDATE rfid_mislabel_flags SET alt_skus = :alt "
                "WHERE sku = :sku AND (alt_skus IS NULL OR alt_skus = '')"
            ),
            {"alt": alt, "sku": sku},
        ).rowcount
        if n:
            print(f"{sku}: alternate {alt} linked.")

print("Done.")
