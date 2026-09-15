"""Import a bundle-groups CSV export (Nick, 2026-09-15: the DSLR
Buddy V2 couplers showed as missing inventory - they are bundles from
a bundle app whose relationships the native Shopify Bundles query
can't see, so Nick exported them).

CSV shape: Group Name, Product Title, Product ID, Variant Title,
Variant ID, Quantity, Role - one "master" row per group (the sellable
bundle variant) and its "component" rows with quantities.

Per resolvable group this writes, via the same helper the web bundle
editor uses (_write_bundle_contents): the BundleContent recipe, the
ProductKind kind="bundle" row, and the History receipt. Every surface
then treats the master as a bundle - inventory without tags, no
checks, never batched or labelled.

    py dev/import_bundle_groups.py <export.csv>           (dry run)
    py dev/import_bundle_groups.py <export.csv> --apply

Needs %TEMP%/dburl.txt (prod) or DATABASE_URL already set. Variant
ids resolve through the bin map; misses fall back to live Shopify
when SHOPIFY_* env is present. Re-running with a fresh export
re-imports (contents are replaced wholesale per master).
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

if not os.environ.get("DATABASE_URL"):
    with open(os.path.join(os.environ["TEMP"], "dburl.txt"),
              encoding="utf-8-sig") as f:
        os.environ["DATABASE_URL"] = f.read().strip().replace(
            "mssql://", "mssql+pymssql://", 1)
os.environ.setdefault("ORDERS_SYNC_DISABLE", "1")
os.environ.setdefault("SHOPIFY_STORE", "")
os.environ.setdefault("SHOPIFY_CLIENT_ID", "")
os.environ.setdefault("SHOPIFY_CLIENT_SECRET", "")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

APPLY = "--apply" in sys.argv
args = [a for a in sys.argv[1:] if a != "--apply"]
if not args:
    print(__doc__)
    sys.exit(2)
path = args[0]

groups: dict[str, dict] = {}
with open(path, encoding="utf-8-sig", newline="") as f:
    for row in csv.DictReader(f):
        name = (row.get("Group Name") or "").strip()
        if not name:
            continue
        g = groups.setdefault(name, {"master": None, "components": []})
        entry = {
            "vid": (row.get("Variant ID") or "").strip(),
            "title": (row.get("Product Title") or "").strip(),
            "variant": (row.get("Variant Title") or "").strip(),
            "qty": int((row.get("Quantity") or "0").strip() or 0),
        }
        if (row.get("Role") or "").strip().lower() == "master":
            g["master"] = entry
        else:
            g["components"].append(entry)
print(f"{len(groups)} group(s) in {os.path.basename(path)}")

from app.database import get_engine  # noqa: E402
from app.models import BinMapEntry  # noqa: E402
from app import shopify  # noqa: E402

# Variant id -> SKU: the bin map first (one load), live Shopify for
# the rest (bundle masters without bins never make the bin map).
by_vid: dict[str, str] = {}
with Session(get_engine()) as s:
    for sku, vid in s.execute(
        select(BinMapEntry.sku, BinMapEntry.shopify_variant_id)
    ):
        v = (vid or "").strip()
        if v and sku:
            by_vid[v.rsplit("/", 1)[-1]] = sku.strip()


def resolve(vid: str) -> str | None:
    if vid in by_vid:
        return by_vid[vid]
    if not os.environ.get("SHOPIFY_CLIENT_SECRET"):
        return None
    try:
        hit = shopify.lookup_variant_by_gid(
            f"gid://shopify/ProductVariant/{vid}")
    except Exception as error:  # noqa: BLE001
        print(f"  ! live lookup failed for variant {vid}: {error}")
        return None
    if hit and (hit.get("sku") or "").strip():
        by_vid[vid] = hit["sku"].strip()
        return by_vid[vid]
    return None


plans = []
skipped = []
for name, g in sorted(groups.items()):
    m = g["master"]
    if m is None:
        skipped.append((name, "no master row"))
        continue
    master_sku = resolve(m["vid"])
    if not master_sku:
        skipped.append((name, f"master variant {m['vid']} has no SKU "
                              "anywhere"))
        continue
    comps = []
    missing = []
    for c in g["components"]:
        c_sku = resolve(c["vid"])
        if c_sku and c["qty"] >= 1:
            comps.append((c_sku, c["qty"]))
        else:
            missing.append(c["vid"])
    if not comps:
        skipped.append((name, f"master {master_sku}: no resolvable "
                              "components"))
        continue
    plans.append((name, master_sku, comps, missing))

for name, master_sku, comps, missing in plans:
    line = ", ".join(f"{q}x {c}" for c, q in comps)
    note = f"  (unresolved components: {', '.join(missing)})" \
        if missing else ""
    print(f"  {master_sku}  <-  {line}   [{name}]{note}")
for name, why in skipped:
    print(f"  SKIPPED {name}: {why}")
print(f"{len(plans)} bundle(s) ready, {len(skipped)} skipped.")

if not APPLY:
    print("Dry run - nothing written. Re-run with --apply.")
    sys.exit(0)

from app.main import _write_bundle_contents  # noqa: E402

done = 0
with Session(get_engine()) as s:
    for name, master_sku, comps, _missing in plans:
        _write_bundle_contents(
            s, master_sku, comps, "Claude (bundle-groups import)")
        done += 1
print(f"done - {done} bundle(s) written (contents + kind=bundle + "
      "History receipts).")
