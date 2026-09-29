"""TC-Planner receiving sync (Nick, 2026-09-29).

The planner's receiving used to reach this app only on two paths - its
Print labels button (/api/receiving/prints) and, when stock was pushed
without printing, the no-labels relay (/api/receiving/unprinted). SO 964
(Celestron, 45 units) proved the gap: the push relayed nothing, the
Review task that used to catch it is gone, and the order left no record
here at all.

This module makes the bridge SELF-HEALING instead of event-driven:

  POST /api/receiving/sync   {reference, items: [{sku, barcode,
                              received_total}], print, print_skus}
      The planner sends each line's CUMULATIVE received count (since the
      bridge went live). Whatever this app hasn't booked yet for that
      stock order - across every receiving batch the SO ever had, open
      or closed - is booked now (no labels), so a repeat call never
      double-counts and a missed call is repaired by the next one (Save,
      Push and Print all call it). With print=true it then queues the
      owed labels, only for print_skus when given (the planner's
      per-line Print buttons).

  GET  /api/receiving/so-labels?reference=SO 964
      Per-SKU booked units, labels queued and labels still owed, for the
      planner's Push-to-Shopify window and its after-push Print button.

An order received through "Receive entire shipment" already printed and
paired everything here; both calls answer full_shipment and do nothing.
Shopify is never touched from this module.
"""
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import main as M
from app.models import Batch, BatchItem, OrderReceipt, PrintJob

app = M.app


class ReceivingSyncItemIn(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    barcode: str | None = Field(default=None, max_length=64)
    # The planner's cumulative received units for this line (since the
    # bridge existed) - never a delta, so repeats are harmless.
    received_total: int = Field(ge=0, le=100000)


class ReceivingSyncIn(BaseModel):
    reference: str = Field(min_length=3, max_length=60)
    requested_by: str | None = Field(default=None, max_length=100)
    items: list[ReceivingSyncItemIn] = Field(default_factory=list,
                                             max_length=300)
    # Queue the owed labels after booking...
    print: bool = False
    # ...for just these SKUs (the planner's per-line buttons). None = all
    # the lines in this call.
    print_skus: list[str] | None = Field(default=None, max_length=300)
    # The planner's Undo receive (2026-09-29, SO 977): a total BELOW
    # what's booked lowers the booking to match (open batches only) and
    # takes the labels with it. Only the undo path sends it - a plain
    # sync never lowers.
    allow_lower: bool = False


def _so_ref(reference: str) -> tuple[str, str]:
    """(normalized reference, "SO 964") - the SO number is the unit of
    work, same rule as the intake."""
    ref = (reference or "").strip()
    try:
        ref = M._normalize_so_reference(ref)
    except Exception:  # noqa: BLE001 - decoration, never blocks
        pass
    so_part = ref.split("·")[0].strip().upper()
    return ref, so_part


def _full_shipment(session: Session, so_part: str) -> bool:
    so_num = so_part.removeprefix("SO").strip()
    if not so_num.isdigit():
        return False
    return session.scalar(
        select(OrderReceipt.id).where(
            (OrderReceipt.stock_order_id == int(so_num))
            | (OrderReceipt.reference == f"SO {so_num}")
        ).limit(1)
    ) is not None


def _so_batches(session: Session, so_part: str) -> list[Batch]:
    """Every planner receiving batch this SO ever had, any status - the
    booked total must count closed batches too, or a receive after the
    first batch closed would re-book the whole order."""
    out = []
    for b in session.scalars(
        select(Batch).where(
            Batch.kind == "receiving",
            Batch.created_by.like("TC-Planner ·%"),
        ).order_by(Batch.id)
    ):
        parts = [p.strip() for p in (b.created_by or "").split("·")]
        if len(parts) < 2:
            continue
        sos = {s.strip().upper() for s in parts[1].split(",") if s.strip()}
        if so_part in sos:
            out.append(b)
    return out


def _row_matches(row: BatchItem, sku: str, barcode: str | None) -> bool:
    keys = {M._up(sku)} | ({M._up(barcode)} if barcode else set())
    keys.discard("")
    return bool(
        (row.sku and M._up(row.sku) in keys)
        or (row.barcode and M._up(row.barcode) in keys)
        or (row.scanned_code and M._up(row.scanned_code) in keys)
    )


def _row_units(row: BatchItem) -> int:
    """Units the planner booked on this row (expected_qty holds the
    planner's number in boxes; a multi-box product books slots per
    unit)."""
    slots = 1 if row.skip_reason else M._item_box_slots(row)
    return (row.expected_qty or 0) // max(1, slots)


def _rows(session: Session, batches: list[Batch]) -> list[BatchItem]:
    rows: list[BatchItem] = []
    for b in batches:
        rows.extend(M._batch_items(session, b.id))
    return rows


def _so_status(session: Session, batches: list[Batch]) -> list[dict]:
    """Per SKU: units booked, labels queued, labels owed (open batches
    only - a closed batch owes nothing), and why a label can't print."""
    per: dict[str, dict] = {}
    for b in batches:
        open_batch = b.status not in ("done", "abandoned")
        have_by_variant: dict[str, int] = {}
        if open_batch:
            for vid, n in session.execute(
                select(PrintJob.shopify_variant_id, func.count())
                .where(
                    PrintJob.batch_id == b.id,
                    PrintJob.status.in_(("pending", "printing", "done")),
                )
                .group_by(PrintJob.shopify_variant_id)
            ):
                have_by_variant[vid or ""] = n
        for r in M._batch_items(session, b.id):
            key = M._up(r.sku) or M._up(r.scanned_code)
            d = per.setdefault(key, {
                "sku": r.sku or r.scanned_code,
                "product_title": r.product_title,
                "booked_units": 0,
                "labels_queued": 0,
                "labels_owed": 0,
                "problem": None,
            })
            d["booked_units"] += _row_units(r)
            if r.skip_reason:
                d["problem"] = r.skip_reason
                continue
            if not open_batch or not r.shopify_variant_id:
                continue
            want = (r.qty_scanned or 0) + (r.case_count or 0)
            have = have_by_variant.get(r.shopify_variant_id, 0)
            d["labels_queued"] += min(want, have)
            owed = max(0, want - have)
            d["labels_owed"] += owed
            bin_ = (r.bin_location or "").strip()
            if owed and (not bin_ or bin_.lower() == "no bin assigned"):
                d["problem"] = ("No bin assigned yet - set one, then "
                                "print again.")
    return list(per.values())


def _lower_bookings(session: Session, payload: ReceivingSyncIn,
                    batches: list[Batch], rows: list[BatchItem],
                    by: str | None) -> tuple[list[dict], int]:
    """The undo half of the sync: for each line whose planner total is
    now BELOW what's booked, take the difference off the OPEN batches'
    rows (newest batch first), never below the boxes already paired to
    a tag. Labels go with the units: waiting ones are canceled, printed
    ones voided (the sticker is spare - discard it). A row left with
    nothing on it is removed. Returns (unbooked lines, labels voided)."""
    open_ids = [b.id for b in batches if b.status not in ("done", "abandoned")]
    unbooked: list[dict] = []
    voided_total = 0
    for it in payload.items:
        mine = [r for r in rows if _row_matches(r, it.sku, it.barcode)]
        excess = sum(_row_units(r) for r in mine) - it.received_total
        if excess <= 0:
            continue
        took_line = 0
        for r in sorted((r for r in mine if r.batch_id in open_ids),
                        key=lambda r: (r.batch_id, r.id), reverse=True):
            if excess <= 0:
                break
            slots = 1 if r.skip_reason else M._item_box_slots(r)
            tagged = 0
            if r.shopify_variant_id:
                tagged = session.scalar(
                    select(func.count()).select_from(M.RfidAssignment).where(
                        M.RfidAssignment.batch_id == r.batch_id,
                        M.RfidAssignment.shopify_variant_id
                        == r.shopify_variant_id,
                    )
                ) or 0
            # Boxes already tagged stay booked - they physically exist.
            floor_units = -(-tagged // max(1, slots))
            can = max(0, _row_units(r) - floor_units)
            take = min(can, excess)
            if take <= 0:
                continue
            boxes = take * slots
            r.expected_qty = max(0, (r.expected_qty or 0) - boxes)
            r.qty_scanned = max(0, (r.qty_scanned or 0) - boxes)
            excess -= take
            took_line += take
            # Labels beyond what the row still wants: waiting ones are
            # canceled first, then printed ones voided, newest first.
            if r.shopify_variant_id and not r.skip_reason:
                want = (r.qty_scanned or 0) + (r.case_count or 0)
                live = session.scalars(
                    select(PrintJob).where(
                        PrintJob.batch_id == r.batch_id,
                        PrintJob.shopify_variant_id == r.shopify_variant_id,
                        PrintJob.status.in_(("pending", "done")),
                    ).order_by(PrintJob.id.desc())
                ).all()
                over = len(live) - want
                for j in sorted(live, key=lambda j: j.status != "pending"):
                    if over <= 0:
                        break
                    if j.status == "done":
                        voided_total += 1
                    j.status = "canceled" if j.status == "pending" else "voided"
                    over -= 1
            session.flush()   # the label changes above must count
            has_jobs = session.scalar(
                select(func.count()).select_from(PrintJob).where(
                    PrintJob.batch_id == r.batch_id,
                    PrintJob.shopify_variant_id == r.shopify_variant_id,
                    PrintJob.status.in_(("pending", "printing", "done")),
                )
            ) if r.shopify_variant_id else 0
            if (r.qty_scanned or 0) == 0 and (r.expected_qty or 0) == 0 \
                    and not (r.case_count or 0) and not has_jobs and not tagged:
                session.delete(r)
        if took_line:
            unbooked.append({"sku": it.sku, "quantity": took_line})
    if unbooked:
        units = sum(u["quantity"] for u in unbooked)
        M._log_change(
            session,
            sku=None,
            title=next((b.created_by for b in batches if b.id in open_ids),
                       payload.reference),
            field="receiving-unbooked",
            old=None,
            new=(f"{M._count(units, 'unit', 'units')} undone in TC-Planner"
                 + (f"; {M._count(voided_total, 'printed label', 'printed labels')} "
                    "voided" if voided_total else "")),
            by=by,
        )
        session.flush()
    return unbooked, voided_total


@app.post(
    "/api/receiving/sync",
    dependencies=[Depends(M.require_user)],
)
def receiving_sync(
    payload: ReceivingSyncIn, session: Session = Depends(M.get_session)
):
    ref, so_part = _so_ref(payload.reference)
    if not so_part.startswith("SO"):
        raise HTTPException(422, "The reference must start with the SO number.")
    if _full_shipment(session, so_part):
        return {
            "full_shipment": True,
            "booked": [],
            "queued": 0,
            "skus": [],
            "message": (f"{so_part} was received via Receive entire shipment - "
                        "its labels are already printed and paired."),
        }
    by = (payload.requested_by or "").strip() or None
    batches = _so_batches(session, so_part)
    rows = _rows(session, batches)

    # 1) Book whatever the planner has received that isn't booked yet.
    deltas: list[M.ReceivingPrintItemIn] = []
    for it in payload.items:
        have = sum(_row_units(r) for r in rows
                   if _row_matches(r, it.sku, it.barcode))
        need = it.received_total - have
        while need > 0:   # the intake caps one line at 500 units
            take = min(need, 500)
            deltas.append(M.ReceivingPrintItemIn(
                sku=it.sku, quantity=take, barcode=it.barcode))
            need -= take
    booked: list[dict] = []
    if deltas:
        out = M._receiving_intake(
            session,
            M.ReceivingPrintsIn(items=deltas, requested_by=by, reference=ref),
            queue_labels=False,
        )
        booked = out["added"]
        units = sum(a["quantity"] for a in booked)
        M._log_change(
            session,
            sku=None,
            title=out["tag"],
            field="receiving-booked",
            old=None,
            new=(f"{M._count(units, 'unit', 'units')} booked from TC-Planner "
                 f"on receiving batch #{out['batch'].id}"),
            by=by,
        )
        session.flush()
        batches = _so_batches(session, so_part)
        rows = _rows(session, batches)

    # 1b) Undo: lower the booking to the planner's total and take the
    # labels with it.
    unbooked, voided = _lower_bookings(session, payload, batches, rows, by) \
        if payload.allow_lower else ([], 0)
    if unbooked:
        rows = _rows(session, batches)

    # 2) Queue the owed labels - only for the asked lines.
    queued: list = []
    no_bin: list[str] = []
    if payload.print:
        asked = payload.print_skus
        lines = [it for it in payload.items
                 if asked is None or M._up(it.sku) in {M._up(s) for s in asked}]
        if asked is not None and not payload.items:
            lines = [ReceivingSyncItemIn(sku=s, received_total=0) for s in asked]
        row_skus = {
            M._up(r.sku) for r in rows
            for it in lines if r.sku and _row_matches(r, it.sku, it.barcode)
        }
        titles = {
            r.product_title for r in rows
            if r.sku and M._up(r.sku) in row_skus
        }
        # A bin added AFTER the line was booked (the planner now makes the
        # user add one before printing, 2026-09-29) lives only in Shopify
        # so far - the booked row still says "no bin". Re-read the live
        # bin for just those rows so the label carries it.
        live_bins: dict[str, str] = {}
        for r in rows:
            if not r.sku or M._up(r.sku) not in row_skus or r.skip_reason:
                continue
            cur = (r.bin_location or "").strip()
            if cur and cur.lower() != "no bin assigned":
                continue
            key = M._up(r.sku)
            if key not in live_bins:
                live_bins[key] = ""
                try:
                    p = M.shopify.lookup_barcode(r.sku) or (
                        M.shopify.lookup_barcode(r.barcode) if r.barcode else None)
                    fresh = ((p or {}).get("bin_location") or "").strip()
                    if fresh and fresh.lower() != "no bin assigned":
                        live_bins[key] = fresh[:100]
                except Exception:  # noqa: BLE001 - stays held, named below
                    pass
            if live_bins[key]:
                r.bin_location = live_bins[key]
        session.flush()
        for b in batches:
            if b.status in ("done", "abandoned"):
                continue
            jobs, nb, _ = M._build_receiving_label_jobs(session, b, by or b.created_by)
            keep = [j for j in jobs if M._up(j.sku) in row_skus]
            session.add_all(keep)
            queued.extend(keep)
            no_bin.extend(t for t in nb if t in titles)
    session.commit()
    status = _so_status(session, _so_batches(session, so_part))
    units_booked = sum(a["quantity"] for a in booked)
    msg = []
    if units_booked:
        msg.append(f"{M._count(units_booked, 'unit', 'units')} booked on "
                   f"{so_part}'s receiving batch")
    if unbooked:
        n_un = sum(u["quantity"] for u in unbooked)
        msg.append(f"{M._count(n_un, 'unit', 'units')} taken off {so_part}'s "
                   "receiving batch with the undo"
                   + (f" - {M._count(voided, 'printed label', 'printed labels')} "
                      "voided, discard "
                      + ("it" if voided == 1 else "them") if voided else ""))
    if payload.print:
        msg.append(f"{M._count(len(queued), 'label', 'labels')} queued")
    if no_bin:
        msg.append("held for a bin: " + ", ".join(no_bin[:4])
                   + (" and more" if len(no_bin) > 4 else ""))
    return {
        "full_shipment": False,
        "booked": booked,
        "unbooked": unbooked,
        "labels_voided": voided,
        "queued": len(queued),
        "skipped_no_bin": no_bin,
        "skus": status,
        "labels_owed": sum(s["labels_owed"] for s in status),
        "message": ("; ".join(msg) + ".") if msg
        else f"{so_part} is up to date here - nothing new to book or print.",
    }


@app.get(
    "/api/receiving/so-labels",
    dependencies=[Depends(M.require_user)],
)
def receiving_so_labels(
    reference: str, session: Session = Depends(M.get_session)
):
    ref, so_part = _so_ref(reference)
    if _full_shipment(session, so_part):
        return {"full_shipment": True, "skus": [], "labels_owed": 0}
    status = _so_status(session, _so_batches(session, so_part))
    return {
        "full_shipment": False,
        "reference": so_part,
        "skus": status,
        "labels_owed": sum(s["labels_owed"] for s in status),
    }
