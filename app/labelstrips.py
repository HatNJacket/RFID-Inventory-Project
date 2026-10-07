"""Which strip a label came off, and where on it (Steve, 2026-10-07).

Receiving prints long strips - three batches can leave three strips of
labels on the bench, and finding the label for the box in hand meant
reading down a list. Every print job now carries a strip id and its
position: all jobs queued in one request (one PRINT press, one planner
print, one whole-strip run with its side trips) share a strip, numbered
in the order they were added, which is the order the agent prints them
(it claims jobs by id).

The gun's FIND A LABEL scans a label's barcode (the product's barcode,
or its SKU when it has none) and asks /api/labels/locate: which batch,
how many labels on that strip, which positions are this product's, how
many are paired - then opens the batch with the product selected.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import event, func, or_, select
from sqlalchemy.orm import Session

from app.auth import require_user
from app.database import get_session
from app.models import Batch, BatchItem, BinMapEntry, PrintJob

router = APIRouter(prefix="/api/labels", dependencies=[Depends(require_user)])

# Jobs that are (or will be) on the strip; failed ones never printed.
ON_STRIP = ("pending", "printing", "done")


def _new_strip() -> str:
    return "s" + secrets.token_hex(8)


@event.listens_for(Session, "before_flush")
def _stamp_strips(session: Session, flush_context, instances) -> None:
    """Give new print jobs this transaction's strip, numbered in the
    order they were added."""
    fresh = [o for o in session.new
             if isinstance(o, PrintJob) and not o.strip_id]
    if not fresh:
        return
    info = session.info.setdefault("label_strip", {})
    if "id" not in info:
        info["id"] = _new_strip()
        info["n"] = 0
    for job in fresh:
        info["n"] += 1
        job.strip_id = info["id"]
        job.strip_pos = info["n"]


@event.listens_for(Session, "after_commit")
@event.listens_for(Session, "after_rollback")
def _end_strip(session: Session) -> None:
    session.info.pop("label_strip", None)


def _codes_for(session: Session, code: str) -> tuple[set[str], set[str]]:
    """The barcodes and SKUs a scanned label code can stand for."""
    up = code.strip().upper()
    barcodes, skus = {code.strip()}, {up}
    for row in session.execute(
        select(BinMapEntry.sku, BinMapEntry.barcode).where(
            or_(BinMapEntry.barcode == code.strip(),
                func.upper(BinMapEntry.sku) == up))
    ).all():
        if row.sku:
            skus.add(row.sku.upper())
        if row.barcode:
            barcodes.add(row.barcode)
    return barcodes, skus


def _ranges(nums: list[int]) -> str:
    """[7, 8, 9, 12] -> "7-9, 12"."""
    out, start, prev = [], None, None
    for n in sorted(nums):
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}-{prev}" if prev != start else str(start))
            start = prev = n
    if start is not None:
        out.append(f"{start}-{prev}" if prev != start else str(start))
    return ", ".join(out)


@router.get("/locate")
def locate_label(code: str, days: int = 30,
                 session: Session = Depends(get_session)):
    """Where the labels for a scanned barcode sit: per strip (newest,
    unfinished batches first), the batch, the strip's label count, this
    product's positions on it, and the batch item to select."""
    code = (code or "").strip()
    if not code:
        raise HTTPException(400, "Scan a label's barcode.")
    barcodes, skus = _codes_for(session, code)
    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))
    mine = session.scalars(
        select(PrintJob).where(
            PrintJob.status.in_(ON_STRIP),
            PrintJob.created_at >= since,
            or_(PrintJob.barcode.in_(barcodes), func.upper(PrintJob.sku).in_(skus)),
        ).order_by(PrintJob.id)
    ).all()
    strips: dict[str, list[PrintJob]] = {}
    for job in mine:
        strips.setdefault(job.strip_id or f"job{job.id}", []).append(job)
    results = []
    for sid, jobs in strips.items():
        if sid.startswith("job"):
            whole = jobs
        else:
            whole = session.scalars(
                select(PrintJob).where(PrintJob.strip_id == sid,
                                       PrintJob.status.in_(ON_STRIP))
                .order_by(PrintJob.strip_pos, PrintJob.id)
            ).all()
        order = [j.id for j in whole]
        positions = [order.index(j.id) + 1 for j in jobs if j.id in order]
        batch_ids = {j.batch_id for j in whole if j.batch_id}
        first = jobs[0]
        batch = session.get(Batch, first.batch_id) if first.batch_id else None
        item = None
        if batch is not None:
            item = session.scalar(
                select(BatchItem).where(
                    BatchItem.batch_id == batch.id,
                    or_(func.upper(BatchItem.sku).in_(skus),
                        BatchItem.barcode.in_(barcodes)))
                .order_by(BatchItem.id).limit(1)
            )
        printed_at = max((j.printed_at or j.created_at) for j in whole)
        results.append({
            "strip_id": sid,
            "strip_labels": len(whole),
            "positions": positions,
            "positions_text": _ranges(positions),
            "strip_batches": sorted(batch_ids),
            "printed_at": printed_at.isoformat() if printed_at else None,
            "pending": sum(1 for j in whole if j.status != "done"),
            "sku": first.sku,
            "title": first.product_title,
            "batch": ({"id": batch.id, "label": batch.bin_name,
                       "status": batch.status, "kind": batch.kind}
                      if batch else None),
            "item": ({"id": item.id, "paired": item.paired_count}
                     if item else None),
        })
    def open_first(r):
        st = (r["batch"] or {}).get("status")
        return (st in ("done", "abandoned"), r["printed_at"] or "")
    results.sort(key=lambda r: open_first(r)[1], reverse=True)
    results.sort(key=lambda r: open_first(r)[0])
    return {"code": code, "results": results[:10]}
