"""One-off prod cleanup (Nick, 2026-09-15): the store-wide sweep that
also read the blank, unprinted RFID roll polluted the unpaired lists.

 1. The UNLINKED-TAGS locate stash (the "orphaned tags" hunt list) is
    emptied: every EPC in it gets a permanent write-off (LabelDismissal,
    same mechanism as the ignore-heard button) - EXCEPT EPCs that belong
    to PRINT JOBS, because a written-off label EPC would silently count
    as "accounted for" on the receiving lists Nick still wants to walk.
 2. Every receiving batch product still owing labels is checked for
    pairs made AFTER its labels printed with OTHER tags (the ALP-T-2
    shape: printed strip mislaid, boxes hand-tagged minutes later).
    Those items get the pairing credit the Sept-15 credit system would
    have granted, which removes them from the unpaired-labels list AND
    settles their receiving task. Batches with nothing left owed close.
 3. What remains owed is real: Nick finds and pairs those labels.

Run against prod:

    set DATABASE_URL=<prod mssql url>
    py dev/cleanup_unpaired.py           (dry run)
    py dev/cleanup_unpaired.py --apply

RUN --apply ONCE ONLY (done 2026-09-15). The credit formula reads the
CURRENT paired_count as its baseline, so a second apply would let the
same hand-paired tags cover labels they already covered.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SHOPIFY_STORE", "t.myshopify.com")
os.environ.setdefault("SHOPIFY_CLIENT_ID", "x")
os.environ.setdefault("SHOPIFY_CLIENT_SECRET", "x")
os.environ.setdefault("ORDERS_SYNC_DISABLE", "1")

from sqlalchemy import select, func  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.database import get_engine  # noqa: E402
from app.models import (  # noqa: E402
    BarcodeChange, Batch, BatchItem, LabelDismissal, LocateQueueEntry,
    PrintJob, RfidAssignment,
)
from app import main as appmain  # noqa: E402  (helpers reused below)

APPLY = "--apply" in sys.argv
MARKER = ("ignore-sweep " + datetime.utcnow().strftime("%Y%m%d%H%M%S")
          + " blank-roll cleanup")[:100]

with Session(get_engine()) as s:
    # ---- 1. empty the orphaned-tags stash --------------------------------
    entry = s.scalar(select(LocateQueueEntry).where(
        func.upper(LocateQueueEntry.sku) == "UNLINKED-TAGS"))
    stash = entry.epc_list() if entry is not None else []
    stash_u = sorted({(e or "").strip().upper() for e in stash if e})
    print(f"orphaned-tags stash: {len(stash_u)} EPC(s)")
    protected = set()
    if stash_u:
        protected = {
            (e or "").upper()
            for e in s.scalars(select(PrintJob.epc).where(
                func.upper(PrintJob.epc).in_(stash_u)))
        }
    fresh = appmain._still_unlinked(s, stash_u)
    to_dismiss = sorted(set(fresh) - protected)
    print(f"  {len(protected)} are printed-label EPCs - kept (they stay "
          "owed on the receiving lists)")
    print(f"  {len(set(stash_u) - set(fresh))} already owned/retired/"
          "dismissed - nothing to do")
    print(f"  {len(to_dismiss)} blank/foreign sticker(s) to write off")
    if APPLY and to_dismiss:
        for epc in to_dismiss:
            s.add(LabelDismissal(epc=epc, dismissed_by=MARKER))
        s.add(BarcodeChange(
            sku=None,
            product_title=(f"{len(to_dismiss)} unpaired sticker(s) "
                           "written off")[:255],
            changed_field="unpaired-ignored",
            old_barcode="blank-roll store sweep"[:64],
            new_barcode=MARKER[:64],
            changed_by="Nick",
        ))
    if APPLY and entry is not None:
        keep = [e for e in stash if e.strip().upper() in protected]
        if keep:
            entry.epcs = "\n".join(keep)
        else:
            s.delete(entry)

    # ---- 2. retro-credit labels replaced by later hand pairs -------------
    cutoff = datetime.utcnow() - timedelta(days=60)
    print("receiving batches still owing labels:")
    credited_any = False
    for b in s.scalars(select(Batch).where(Batch.kind == "receiving")
                       .order_by(Batch.id)):
        created = b.created_at
        if created is not None and created.tzinfo is not None:
            created = created.astimezone(timezone.utc).replace(tzinfo=None)
        if created is not None and created < cutoff:
            continue
        net = appmain._receiving_unpaired_net(s, b)
        if not net:
            continue
        jobs = s.scalars(select(PrintJob).where(
            PrintJob.batch_id == b.id,
            PrintJob.status.in_(("pending", "printing", "done")),
        )).all()
        job_epcs = {(j.epc or "").upper() for j in jobs}
        first_print: dict[str, object] = {}
        for j in jobs:
            k = (j.sku or "").strip().upper()
            t = j.created_at
            if k and t is not None and (
                    k not in first_print or t < first_print[k]):
                first_print[k] = t
        items = {
            (i.sku or "").strip().upper(): i
            for i in s.scalars(select(BatchItem).where(
                BatchItem.batch_id == b.id))
        }
        for u in net:
            key = (u["sku"] or "").strip().upper()
            owed = u["count"]
            item = items.get(key)
            if item is None or owed <= 0:
                continue
            since = first_print.get(key)
            later = [
                t for t in s.scalars(select(RfidAssignment))
                if (t.sku or "").strip().upper() == key
                and t.rfid_id.upper() not in job_epcs
                and (since is None or (
                    (t.assigned_at.astimezone(timezone.utc)
                     .replace(tzinfo=None))
                    if t.assigned_at is not None
                    and t.assigned_at.tzinfo is not None
                    else t.assigned_at) >= (
                    since.astimezone(timezone.utc).replace(tzinfo=None)
                    if since.tzinfo is not None else since))
            ]
            credit = min(owed, len(later))
            print(f"  batch {b.id} · {u['sku']}: owes {owed}, "
                  f"{len(later)} other tag(s) paired after its labels "
                  f"printed -> credit {credit}")
            if credit > 0:
                credited_any = True
                if APPLY:
                    item.paired_count += credit
                    s.add(BarcodeChange(
                        sku=item.sku,
                        product_title=item.product_title,
                        changed_field="locate-list",
                        old_barcode=(f"batch {b.id}: {owed} label(s) "
                                     "owed")[:64],
                        new_barcode=(f"{credit} covered by later hand "
                                     "pairs")[:64],
                        changed_by="Nick",
                    ))
        if APPLY:
            s.flush()
            appmain._maybe_close_receiving(s, b)
    if not credited_any:
        print("  (nothing creditable)")

    if APPLY:
        s.flush()
        appmain._check_unpaired_label_tasks(s)
        s.commit()
        print("APPLIED.")
    else:
        s.rollback()
        print("Dry run only - re-run with --apply.")
