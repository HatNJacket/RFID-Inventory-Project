"""One-off prod cleanup (Nick, 2026-09-15): multi-box sets are
SCRAPPED. Every box is its own individual product from here on; a
product sold as one unit made of N boxes is modelled as a multi-product
BUNDLE of its box products instead.

What this does, in order:
 1. Each registered set's MASTER becomes a bundle: ProductKind
    kind="bundle" + one BundleContent row per box (qty 1). The
    mismatch checker then skips the master like every other bundle.
 2. Each box's DRAFT listing gets its registered part barcode written
    to Shopify (if it isn't there already), so scans keep resolving
    the box now that the registry is gone. Needs SHOPIFY_* env; skips
    with a warning when absent.
 3. BoxSetPart rows deleted (the registry).
 4. MultiboxProduct rows deleted (the same-SKU companion system).
 5. All "Part of a set" marks cleared from batch items.
 6. " - Box N of M" stripped from live tag titles and open-batch item
    titles; ", Box N of M" stripped from PENDING label bin lines.
 7. History rows record the conversions.

Run ONCE against prod (idempotent - reruns find nothing to do):

    set DATABASE_URL=<prod mssql url>
    (plus SHOPIFY_STORE / SHOPIFY_CLIENT_ID / SHOPIFY_CLIENT_SECRET
     for the barcode step)
    py dev/unlink_box_sets.py --apply
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.database import get_engine  # noqa: E402
from app.models import (  # noqa: E402
    BarcodeChange, Batch, BatchItem, BinMapEntry, BoxSetPart,
    BundleContent, MultiboxProduct, PrintJob, ProductKind,
    RfidAssignment,
)

APPLY = "--apply" in sys.argv
BOX_TITLE_RE = re.compile(r"\s*-\s*Box \d+ of \d+\s*$", re.IGNORECASE)
BOX_BIN_RE = re.compile(r",?\s*Box \d+ of \d+\s*$", re.IGNORECASE)

with Session(get_engine()) as s:
    parts = s.scalars(
        select(BoxSetPart).order_by(BoxSetPart.set_sku, BoxSetPart.box_no)
    ).all()
    sets: dict[str, list[BoxSetPart]] = {}
    for p in parts:
        sets.setdefault(p.set_sku, []).append(p)
    print(f"{len(sets)} registered set(s): "
          + ", ".join(f"{k} ({len(v)} boxes)" for k, v in sets.items()))

    # 1. masters -> bundles
    for set_sku, plist in sets.items():
        pk = s.get(ProductKind, set_sku)
        if pk is None:
            pk = ProductKind(sku=set_sku, kind="bundle")
            s.add(pk)
        else:
            pk.kind = "bundle"
        existing = {
            (b.component_sku or "").strip().upper()
            for b in s.scalars(select(BundleContent).where(
                func.upper(BundleContent.bundle_sku) == set_sku.upper()
            ))
        }
        added = 0
        for p in plist:
            if p.part_sku.strip().upper() not in existing:
                s.add(BundleContent(bundle_sku=set_sku,
                                    component_sku=p.part_sku, qty=1))
                added += 1
        s.add(BarcodeChange(
            sku=set_sku, product_title=plist[0].set_title,
            changed_field="bundle-contents",
            old_barcode="was a multi-box set"[:64],
            new_barcode=(f"now a bundle of {len(plist)} box "
                         "product(s)")[:64],
            changed_by="set-teardown",
        ))
        print(f"  {set_sku}: bundle kind set, {added} content row(s) added")

    # 2. part barcodes onto the draft listings
    have_shopify = all(os.environ.get(k) for k in (
        "SHOPIFY_STORE", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET"))
    if have_shopify:
        from app import shopify
        for p in parts:
            if not (p.part_barcode or "").strip():
                continue
            try:
                listing = shopify.find_sku_listing(p.part_sku)
            except Exception as error:  # noqa: BLE001
                print(f"  ! probe failed for {p.part_sku}: {error}")
                continue
            if listing is None:
                # The box's identity lived ONLY in the registry (the
                # S11830 boxes): it still needs an individual product
                # to point to, so a draft listing is created for it -
                # title, SKU, its barcode, the master's bin.
                bin_row = s.scalar(
                    select(BinMapEntry).where(
                        func.upper(BinMapEntry.sku)
                        == p.set_sku.upper()
                    )
                )
                title = f"{p.set_title or p.set_sku} - {p.part_sku}"
                if APPLY:
                    try:
                        made = shopify.create_draft_listing(
                            title, p.part_sku, p.part_barcode.strip(),
                            bin_row.bin if bin_row else None,
                        )
                        print(f"  {p.part_sku}: draft listing created ✓ "
                              f"({made.get('variant_gid')})")
                    except Exception as error:  # noqa: BLE001
                        print(f"  ! draft create failed for "
                              f"{p.part_sku}: {error}")
                else:
                    print(f"  {p.part_sku}: WOULD create a draft "
                          f"listing '{title}' with barcode "
                          f"{p.part_barcode}")
                continue
            if ((listing.get("barcode") or "").strip().upper()
                    == p.part_barcode.strip().upper()):
                print(f"  {p.part_sku}: listing already carries "
                      f"{p.part_barcode}")
                continue
            if APPLY:
                try:
                    shopify.update_variant_barcode(
                        listing["shopify_product_id"],
                        listing["shopify_variant_id"],
                        p.part_barcode.strip(),
                    )
                    print(f"  {p.part_sku}: barcode {p.part_barcode} "
                          "written to its listing ✓")
                except Exception as error:  # noqa: BLE001
                    print(f"  ! barcode write failed for {p.part_sku}: "
                          f"{error}")
            else:
                print(f"  {p.part_sku}: WOULD write barcode "
                      f"{p.part_barcode} to its listing")
    else:
        print("  ! SHOPIFY_* env missing - part-barcode step skipped")

    # 3 + 4. registries
    for p in parts:
        s.delete(p)
    mb = s.scalars(select(MultiboxProduct)).all()
    for m in mb:
        s.delete(m)
    print(f"  {len(parts)} registry row(s), {len(mb)} multibox row(s) "
          "deleted")

    # 5. marks
    marked = s.scalars(select(BatchItem).where(
        BatchItem.set_mark_master.isnot(None)
    )).all()
    for it in marked:
        it.set_mark_master = None
        it.set_mark_box = None
        it.set_mark_total = None
    print(f"  {len(marked)} set mark(s) cleared")

    # 6. titles and bin lines
    fixed_tags = 0
    for t in s.scalars(select(RfidAssignment)):
        new = BOX_TITLE_RE.sub("", t.product_title or "")
        if new != (t.product_title or ""):
            t.product_title = new
            fixed_tags += 1
    open_ids = [b.id for b in s.scalars(select(Batch).where(
        Batch.status.notin_(("done", "abandoned"))))]
    fixed_items = 0
    if open_ids:
        for it in s.scalars(select(BatchItem).where(
                BatchItem.batch_id.in_(open_ids))):
            new = BOX_TITLE_RE.sub("", it.product_title or "")
            if new != (it.product_title or ""):
                it.product_title = new
                fixed_items += 1
    fixed_jobs = 0
    for j in s.scalars(select(PrintJob).where(
            PrintJob.status == "pending")):
        new = BOX_BIN_RE.sub("", j.bin_location or "").strip() or None
        if new != j.bin_location:
            j.bin_location = new
            fixed_jobs += 1
    print(f"  titles cleaned: {fixed_tags} tag(s), {fixed_items} open "
          f"batch item(s); {fixed_jobs} pending label bin line(s)")

    if APPLY:
        s.commit()
        print("APPLIED.")
    else:
        s.rollback()
        print("Dry run only - nothing written. Re-run with --apply.")
