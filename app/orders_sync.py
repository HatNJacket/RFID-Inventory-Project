"""Sold/shipped sync: teaches the RFID system that stock has LEFT.

A shipped box leaves the building with its RFID tag still on file, so
from the moment it ships, "tags on file" legitimately exceeds
Shopify's on-hand by the sold count. This module keeps that ledger
(rfid_sold_ledger):

    expected tags for a SKU  =  live Shopify on-hand  +  sold-unretired

TWO read-only feeds fill it (Nick, 2026-09-23 - "plan for
inconsistencies between these two sources"):

1. ShipStation shipments (PRIMARY when configured): a label created and
   not voided is a box that physically left - the ground truth. Covers
   manual (non-Shopify) orders too. Multi-parcel orders merge into one
   row per (order, SKU) with the label ids recorded, capped at the
   order line's units so overlapping parcel item lists (label reprints)
   never double-count; voided labels are subtracted by id. Shopify
   ON-HAND stays the stock source everywhere - ShipStation only ever
   supplies the outflow term.
2. The Shopify fulfilled-orders feed (the original source) stays on as
   the FALLBACK and gap-filler: an order fulfilled without a
   ShipStation label (pickup, outside carrier) still lands. It never
   duplicates a line the ShipStation feed already recorded, and never
   overrides a ShipStation quantity - disagreements are counted in the
   run status instead (qty_conflicts).

Both feeds are READ-ONLY against their sources. Runs HOURLY (the old
once-daily pass left the ledger up to 24h behind the adjustment
history, which mis-windowed sales; the 8 AM run additionally does the
daily housekeeping) plus on the Review tab's manual button. Each run
re-checks the ledger'd SKUs and keeps ONE open Inventory Check per SKU
whose numbers don't add up - closing it again itself when the world
catches up. Audits remain the only thing that CONFIRMS a count; this
module only moves expectations.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import shipstation, shopify
from app.models import (
    AppSetting,
    BackorderDebt,
    BarcodeChange,
    Batch,
    BatchItem,
    BinMapEntry,
    OnhandLog,
    OrderReceipt,
    RefreshLog,
    ReviewTask,
    RfidAssignment,
    SoldRecord,
)

logger = logging.getLogger("rfid.orders")

KIND = "orders-sync"            # refresh-stats kind (button + auto share it)
# One category for every count disagreement (Nick, 2026-09-02): the
# tag arithmetic and human counts file the SAME Inventory Check per
# SKU. "tag-onhand-mismatch" is retired; old resolved tasks keep it.
CATEGORY = "inventory-check"
STATUS_KEY = "orders_sync_status"
CURSOR_KEY = "orders_sync_cursor"
DAILY_KEY = "orders_sync_last_daily"
HOURLY_KEY = "orders_sync_last_hourly"
SS_CURSOR_KEY = "shipstation_sync_cursor"
SS_VOID_KEY = "shipstation_void_cursor"
SYNC_HOUR = 8                   # daily housekeeping hour, America/Toronto
FIRST_LOOKBACK_DAYS = 7
# First ShipStation run backfills from the OLDEST live pairing (a
# shipment can only explain a tag that existed) minus a pad, but never
# further back than this - the ledger explains tag silence, not store
# history.
SS_BACKFILL_CAP_DAYS = 400
SS_BACKFILL_PAD_DAYS = 7


# ------------------------------------------------------------ settings io ---
def _get(session: Session, key: str) -> str | None:
    row = session.get(AppSetting, key)
    return row.value if row else None


def _set(session: Session, key: str, value: str) -> None:
    row = session.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key)
        session.add(row)
    row.value = value[:2000]


def _mark_running(session: Session) -> None:
    _set(session, f"refresh_running:{KIND}", datetime.utcnow().isoformat())


def _clear_running(session: Session, ms: int, source: str) -> None:
    row = session.get(AppSetting, f"refresh_running:{KIND}")
    if row is not None:
        session.delete(row)
    session.add(RefreshLog(kind=KIND, source=source, ms=ms))


# ---------------------------------------------------------------- queries ---
def tracked_skus(session: Session) -> set[str]:
    """Upper-cased SKUs the RFID system actually holds tags for — the
    ledger only records sales of those; the rest of the store isn't our
    problem yet."""
    return {
        (sku or "").strip().upper()
        for sku in session.scalars(
            select(RfidAssignment.sku).distinct()
        ).all()
        if sku and sku.strip()
    }


def tag_units(session: Session, sku: str) -> int:
    """Units the SKU's tags stand for (a sealed-case tag counts its
    case_units, everything else counts 1) — same arithmetic as audits."""
    rows = session.scalars(
        select(RfidAssignment).where(
            func.upper(RfidAssignment.sku) == sku.strip().upper()
        )
    ).all()
    return sum((r.case_units or 1) for r in rows)


# A giant IN(<thousands of SKUs>) is what actually melts the Basic
# tier: SQL Server burns its tiny compile-memory grant on the parameter
# list and EVERY other query queues behind it (2026-09-02: two wedged
# store-wide audit SELECTs held DTU at 100% while Nick scanned). These
# app-owned tables are all small - above this size, scan them WITHOUT
# the IN and match SKUs in Python instead.
_IN_LIMIT = 300


def _sku_in(stmt, column, uppers):
    """Attach an upper-SKU IN() only when the list is small enough to
    compile cheaply; large lists filter in Python (caller checks)."""
    if uppers is not None and len(uppers) <= _IN_LIMIT:
        return stmt.where(func.upper(column).in_(sorted(uppers)))
    return stmt


def sold_unretired_map(
    session: Session, skus: list[str] | None = None
) -> dict[str, int]:
    """Upper-cased SKU -> units sold (fulfilled) whose tag is still on
    file. This is the number that explains missing tags in audits."""
    uppers = (
        {s.strip().upper() for s in skus} if skus is not None else None
    )
    stmt = _sku_in(select(SoldRecord), SoldRecord.sku, uppers)
    out: dict[str, int] = {}
    for row in session.scalars(stmt).all():
        left = max(0, (row.quantity or 0) - (row.retired or 0))
        if left:
            key = row.sku.strip().upper()
            if uppers is not None and key not in uppers:
                continue
            out[key] = out.get(key, 0) + left
    return out


def _as_utc(dt):
    """Timestamps arrive tz-aware from Azure SQL and naive from the
    sqlite test databases; comparisons need one flavor."""
    if dt is None:
        return None
    from datetime import timezone as _tz
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=_tz.utc)


def sold_unretired_since_map(
    session: Session,
    skus: list[str],
    since_by_sku: dict[str, object],
) -> dict[str, int]:
    """Upper SKU -> unretired units sold strictly AFTER that SKU's tag-
    pool baseline. A sale fulfilled before a tag was paired cannot
    explain that tag's later silence (Nick's AIRPLUS case: the 3 PM sale
    predates the 8:56 PM pairing). A SKU with no baseline (no live tags)
    falls back to the full unwindowed sum. Rows with no fulfilled_at are
    excluded from windows; they surface through ledger_covers_from_map
    instead of being silently blamed."""
    uppers = {s.strip().upper() for s in skus}
    out: dict[str, int] = {}
    if not uppers:
        return out
    for row in session.scalars(
        _sku_in(select(SoldRecord), SoldRecord.sku, uppers)
    ).all():
        left = max(0, (row.quantity or 0) - (row.retired or 0))
        if not left:
            continue
        key = row.sku.strip().upper()
        if key not in uppers:
            continue
        since = _as_utc(since_by_sku.get(key))
        if since is not None:
            f = _as_utc(row.fulfilled_at)
            if f is None or f <= since:
                continue
        out[key] = out.get(key, 0) + left
    return out


def ledger_covers_from_map(
    session: Session, skus: list[str]
) -> dict[str, object]:
    """Upper SKU -> earliest dated sale on record (None entries absent).
    Lets callers say "sales history only starts <date>" instead of
    claiming older disappearances are unexplained by sales."""
    uppers = {s.strip().upper() for s in skus}
    out: dict[str, object] = {}
    if not uppers:
        return out
    for row in session.scalars(
        _sku_in(select(SoldRecord), SoldRecord.sku, uppers)
    ).all():
        f = _as_utc(row.fulfilled_at)
        if f is None:
            continue
        key = row.sku.strip().upper()
        if key not in uppers:
            continue
        if key not in out or f < out[key]:
            out[key] = f
    return out


def retire_units(
    session: Session, sku: str, units: int, since=None
) -> int:
    """`units` tags of this SKU were resolved as sold: retire them
    against the OLDEST unretired sales first. With `since`, sales inside
    the window (fulfilled after it) are consumed first, then older rows
    take any remainder, so totals stay conserved even when the window
    guessed wrong. Returns how many actually landed (never more than the
    ledger holds)."""
    landed = 0
    rows = session.scalars(
        select(SoldRecord)
        .where(func.upper(SoldRecord.sku) == sku.strip().upper())
        .order_by(SoldRecord.fulfilled_at, SoldRecord.id)
    ).all()
    since = _as_utc(since)

    def _in_window(row):
        f = _as_utc(row.fulfilled_at)
        return since is not None and f is not None and f > since

    ordered = (
        [r for r in rows if _in_window(r)]
        + [r for r in rows if not _in_window(r)]
        if since is not None else rows
    )
    for row in ordered:
        if landed >= units:
            break
        room = max(0, (row.quantity or 0) - (row.retired or 0))
        take = min(room, units - landed)
        if take:
            row.retired = (row.retired or 0) + take
            landed += take
    return landed


def unretire_units(session: Session, sku: str, units: int) -> int:
    """Inverse of retire_units for undo paths: hand `units` back to the
    ledger, NEWEST retired sales first, never below zero. Returns how
    many were actually restored."""
    restored = 0
    rows = session.scalars(
        select(SoldRecord)
        .where(func.upper(SoldRecord.sku) == sku.strip().upper())
        .order_by(SoldRecord.fulfilled_at.desc(), SoldRecord.id.desc())
    ).all()
    for row in rows:
        if restored >= units:
            break
        take = min(row.retired or 0, units - restored)
        if take:
            row.retired = (row.retired or 0) - take
            restored += take
    return restored


def backorder_debt_map(
    session: Session, skus: list[str] | None = None
) -> dict[str, int]:
    """Upper SKU -> units Shopify's on-hand runs behind the shelf
    (uncleared backorder-debt rows, noted at receiving close). These
    boxes are physically present and tagged, but Shopify absorbed them
    into a pre-delivery negative — expected-tag math must add them or
    every such SKU false-flags forever (Nick, 2026-08-26)."""
    stmt = select(BackorderDebt).where(BackorderDebt.cleared_at.is_(None))
    if skus is not None:
        uppers = [s.strip().upper() for s in skus]
        stmt = stmt.where(func.upper(BackorderDebt.sku).in_(uppers))
    out: dict[str, int] = {}
    for row in session.scalars(stmt).all():
        if row.units:
            key = row.sku.strip().upper()
            out[key] = out.get(key, 0) + row.units
    return out


def _clear_superseded_debts(session: Session, skus: list[str]) -> int:
    """An operator on-hand write is fresh shelf truth: any backorder
    debt noted BEFORE it is stale (the write already counted those
    boxes). Clears such rows and returns how many."""
    uppers = {s.strip().upper() for s in skus}
    if not uppers:
        return 0
    write_ts: dict[str, object] = {}
    for bc in session.scalars(
        _sku_in(
            select(BarcodeChange).where(
                BarcodeChange.changed_field.in_((
                    "on-hand", "on-hand-undo",
                    "on-hand-lower", "on-hand-lower-undo",
                )),
            ),
            BarcodeChange.sku, uppers,
        )
    ):
        k = (bc.sku or "").strip().upper()
        if k not in uppers:
            continue
        ts = _as_utc(bc.changed_at)
        if ts is not None and (k not in write_ts or ts > write_ts[k]):
            write_ts[k] = ts
    if not write_ts:
        return 0
    cleared = 0
    for row in session.scalars(
        select(BackorderDebt).where(
            BackorderDebt.cleared_at.is_(None),
            func.upper(BackorderDebt.sku).in_(sorted(write_ts)),
        )
    ).all():
        ts = write_ts.get(row.sku.strip().upper())
        noted = _as_utc(row.noted_at)
        if ts is not None and noted is not None and ts > noted:
            row.cleared_at = datetime.utcnow()
            row.cleared_by = "on-hand write"
            cleared += 1
    return cleared


def _sku_baselines(session: Session, skus) -> dict[str, object]:
    """Upper SKU -> the tag pool's baseline: OLDEST live pairing (when
    the pool began), or a newer confirmed on-hand write, whichever is
    later. Sales fulfilled before it cannot be expected to have tags.

    Oldest, not newest (Nick, 2026-09-02 drift guard 2): sell one of
    three tagged boxes Monday, receive and pair two more Tuesday - the
    NEWEST pairing threw Monday's sale out of the window even though
    its box absolutely had a tag, so the expected count dropped and a
    silent tag lost its explanation. A sale after the pool EXISTED can
    always be explained by a tag. The original false-positive fix
    survives: a sale before the first-ever pairing stays outside."""
    uppers = {s.strip().upper() for s in skus}
    out: dict[str, object] = {}
    if not uppers:
        return out
    for t in session.scalars(
        _sku_in(select(RfidAssignment), RfidAssignment.sku, uppers)
    ):
        k = t.sku.strip().upper() if t.sku else ""
        if k not in uppers:
            continue
        ts = _as_utc(t.assigned_at)
        if ts is not None and (k not in out or ts < out[k]):
            out[k] = ts
    for bc in session.scalars(
        _sku_in(
            select(BarcodeChange).where(
                BarcodeChange.changed_field.in_((
                    "on-hand", "on-hand-undo",
                    "on-hand-lower", "on-hand-lower-undo",
                )),
            ),
            BarcodeChange.sku, uppers,
        )
    ):
        k = (bc.sku or "").strip().upper()
        if k not in uppers:
            continue
        ts = _as_utc(bc.changed_at)
        if ts is not None and (k not in out or ts > out[k]):
            out[k] = ts
    return out


# ----------------------------------------------------- ShipStation feed -----
def _norm_order_no(value: str | None) -> str:
    """'#50930' and '50930' are the same order to both feeds."""
    return (value or "").strip().lstrip("#").upper()


def _ss_map(row: SoldRecord) -> dict:
    try:
        parsed = json.loads(row.ss_shipments or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except ValueError:
        return {}


def _ss_recompute(row: SoldRecord, keep_floor: int | None = None) -> None:
    """quantity = the labels' units, capped at the order line (when
    known), never below what audits already retired. `keep_floor`
    additionally holds a quantity another source proved (forward feed
    only - the void path must be allowed to lower)."""
    q = sum(int(v) for v in _ss_map(row).values())
    if row.ss_line_qty is not None:
        q = min(q, row.ss_line_qty)
    if keep_floor is not None:
        q = max(q, keep_floor)
    row.quantity = max(q, row.retired or 0)


def _ss_rows_for_order(session: Session, ss_order_id) -> dict[str, SoldRecord]:
    return {
        (r.sku or "").strip().upper(): r
        for r in session.scalars(
            select(SoldRecord).where(
                SoldRecord.ss_order_id == str(ss_order_id)
            )
        )
    }


def sync_shipstation(session: Session) -> dict:
    """Pull shipments (and voids) into the sold ledger. One row per
    (order, SKU) whatever the parcel count; the Shopify feed's rows for
    the same line are ADOPTED, keeping their retired count, so the two
    sources can never double-record a sale. Fail-soft: unconfigured is
    a state, and any API error leaves the ledger exactly as it was."""
    if not shipstation.configured():
        return {"shipstation": "not configured"}
    shopify_ids, excluded_ids = shipstation.store_kinds()
    raw_cursor = _get(session, SS_CURSOR_KEY)
    first_run = raw_cursor is None
    now_utc = datetime.now(timezone.utc)
    if first_run:
        # Backfill window: a shipment can only explain a tag that
        # existed, so start at the oldest live pairing (padded).
        oldest = _as_utc(session.scalar(
            select(func.min(RfidAssignment.assigned_at))
        ))
        start = (
            oldest - timedelta(days=SS_BACKFILL_PAD_DAYS)
            if oldest is not None
            else now_utc - timedelta(days=FIRST_LOOKBACK_DAYS)
        )
        start = max(start, now_utc - timedelta(days=SS_BACKFILL_CAP_DAYS))
    else:
        start = (
            _parse_iso(raw_cursor) or (now_utc - timedelta(days=1))
        ).replace(tzinfo=timezone.utc) - timedelta(hours=2)
    # Coverage BEFORE this run: backfill rows older than what the
    # ledger already knew arrive pre-settled (they explain nothing new;
    # the audits of that era already retired their tags against
    # whatever the ledger held then), and so do rows older than the
    # SKU's tag-pool baseline. Only genuinely-new, tag-era sales land
    # live. Computed up front so this run's own inserts don't count.
    prior_covers: dict[str, object] = {}
    if first_run:
        for r in session.scalars(select(SoldRecord)):
            f = _as_utc(r.fulfilled_at)
            if f is None:
                continue
            k = (r.sku or "").strip().upper()
            if k not in prior_covers or f < prior_covers[k]:
                prior_covers[k] = f

    shipments = shipstation.get_shipments_since(start)
    stats = {
        "ss_new": 0, "ss_updated": 0, "ss_adopted": 0,
        "ss_voided_skipped": 0, "ss_no_sku_units": 0,
        "ss_manual": 0, "ss_qty_conflicts": 0, "ss_voids_applied": 0,
    }
    order_lines_cache: dict = {}
    new_rows: list[SoldRecord] = []
    for sh in shipments:
        if sh["voided"]:
            stats["ss_voided_skipped"] += 1
            continue
        if sh["store_id"] in excluded_ids:
            continue
        manual = sh["store_id"] not in shopify_ids
        num = _norm_order_no(sh["order_number"])
        by_sku: dict[str, dict] = {}
        for it in sh["items"]:
            if not it["sku"]:
                stats["ss_no_sku_units"] += it["qty"]
                continue
            g = by_sku.setdefault(
                it["sku"].upper(), {"sku": it["sku"], "qty": 0}
            )
            g["qty"] += it["qty"]
        if not by_sku:
            continue
        rows = _ss_rows_for_order(session, sh["ss_order_id"])
        for key, g in by_sku.items():
            row = rows.get(key)
            adopted_qty = None
            if row is None and not manual and num:
                # A Shopify-feed row for the same order line: adopt it
                # instead of double-recording the sale.
                row = session.scalars(
                    select(SoldRecord).where(
                        SoldRecord.ss_order_id.is_(None),
                        func.upper(SoldRecord.sku) == key,
                        SoldRecord.order_name.in_((num, f"#{num}")),
                    )
                ).first()
                if row is not None:
                    adopted_qty = row.quantity
                    row.ss_order_id = str(sh["ss_order_id"])
                    row.source = "shipstation"
                    rows[key] = row
                    stats["ss_adopted"] += 1
            if row is None:
                row = SoldRecord(
                    order_id=f"ss:{sh['ss_order_id']}"[:64],
                    order_name=num[:32] if num else None,
                    sku=g["sku"],
                    quantity=0,
                    retired=0,
                    fulfilled_at=sh["created_at"],
                    source="ss-manual" if manual else "shipstation",
                    ss_order_id=str(sh["ss_order_id"]),
                    ss_shipments="{}",
                )
                session.add(row)
                rows[key] = row
                new_rows.append(row)
                stats["ss_new"] += 1
                if manual:
                    stats["ss_manual"] += 1
            ship_map = _ss_map(row)
            sid = str(sh["shipment_id"])
            already = ship_map.get(sid) == g["qty"]
            ship_map[sid] = g["qty"]
            row.ss_shipments = json.dumps(ship_map)[:2000]
            # Second parcel for the same line: fetch the order's line
            # units ONCE and cap - overlapping item lists (a label
            # reprinted with the full order on it) must not double.
            if len(ship_map) > 1 and row.ss_line_qty is None:
                oid = sh["ss_order_id"]
                if oid not in order_lines_cache:
                    try:
                        order_lines_cache[oid] = (
                            shipstation.get_order_line_quantities(oid)
                        )
                    except Exception as error:  # noqa: BLE001 — cap is best-effort
                        logger.warning(
                            "ss order %s line fetch failed: %s", oid, error
                        )
                        order_lines_cache[oid] = None
                lines = order_lines_cache[oid]
                if lines and key in lines:
                    row.ss_line_qty = lines[key]
            _ss_recompute(row, keep_floor=adopted_qty)
            if adopted_qty is not None and row.quantity != adopted_qty:
                stats["ss_qty_conflicts"] += 1
            if not already and row not in new_rows:
                stats["ss_updated"] += 1
            f = sh["created_at"]
            if f is not None:
                cur = _as_utc(row.fulfilled_at)
                if cur is None or f < cur:
                    row.fulfilled_at = f
        session.flush()

    # Voided-after-recording labels: remove exactly those label ids and
    # recompute (the void path may LOWER, unlike the forward feed).
    raw_void = _get(session, SS_VOID_KEY)
    void_since = (
        start if first_run else
        ((_parse_iso(raw_void) or (now_utc - timedelta(days=1)))
         .replace(tzinfo=timezone.utc) - timedelta(hours=2))
    )
    for sh in shipstation.get_voids_since(void_since):
        rows = _ss_rows_for_order(session, sh["ss_order_id"])
        if not rows:
            continue
        sid = str(sh["shipment_id"])
        touched = False
        for it in sh["items"]:
            row = rows.get((it["sku"] or "").strip().upper())
            if row is None:
                continue
            ship_map = _ss_map(row)
            if sid not in ship_map:
                continue
            del ship_map[sid]
            row.ss_shipments = json.dumps(ship_map)[:2000]
            _ss_recompute(row)
            touched = True
            if (row.quantity == 0 and (row.retired or 0) == 0
                    and not ship_map):
                session.delete(row)
        if touched:
            stats["ss_voids_applied"] += 1
        session.flush()

    # First-run backfill: settle what history already accounted for.
    if first_run and new_rows:
        uppers = {(r.sku or "").strip().upper() for r in new_rows}
        baselines = _sku_baselines(session, uppers)
        settled = 0
        for r in new_rows:
            k = (r.sku or "").strip().upper()
            cut = baselines.get(k)
            cov = prior_covers.get(k)
            if cov is not None and (cut is None or cov > cut):
                cut = cov
            f = _as_utc(r.fulfilled_at)
            if cut is not None and f is not None and f <= cut:
                r.retired = r.quantity
                settled += 1
        stats["ss_backfilled_settled"] = settled

    stamp = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    _set(session, SS_CURSOR_KEY, stamp)
    _set(session, SS_VOID_KEY, stamp)
    stats["ss_shipments_seen"] = len(shipments)
    return stats


# ------------------------------------------------------- mismatch tasks -----
def _receiving_in_flight_skus(session: Session) -> set[str]:
    """Upper SKUs whose numbers are MOVING right now (Nick, 2026-09-02
    drift guard 1): an open receiving batch is still pairing tags, or a
    full-shipment receipt is settled but TC-Planner hasn't saved the
    stock yet - the two sides of the arithmetic move at different
    moments, so any comparison inside the window is noise."""
    out: set[str] = set()
    open_batches = session.scalars(
        select(Batch).where(
            Batch.kind == "receiving",
            Batch.status.notin_(("done", "abandoned")),
        )
    ).all()
    pending_receipts = session.scalars(
        select(OrderReceipt).where(
            OrderReceipt.settled_at.is_not(None),
            OrderReceipt.stock_updated_at.is_(None),
        )
    ).all()
    batch_ids = {b.id for b in open_batches} | {
        r.batch_id for r in pending_receipts if r.batch_id
    }
    if not batch_ids:
        return out
    for item in session.scalars(
        select(BatchItem).where(BatchItem.batch_id.in_(sorted(batch_ids)))
    ):
        if item.sku:
            out.add(item.sku.strip().upper())
    return out


def _tagging_in_flight_skus(session: Session) -> set[str]:
    """Drift guard 5 (Nick, 2026-09-14): during BIN batch tagging the
    tag count jumps at pairing but on-hand only catches up at the
    verify step's raise - and the batch-open kick runs this check right
    inside that window (3 Inventory Checks landed a minute after a
    collect, against counts the operator had literally just verified).
    Same story for bin-audit finds: labels print and pair before the
    raise. In flight: every SKU on an OPEN batch of any kind, plus any
    SKU whose newest pairing is under an hour old."""
    out: set[str] = set()
    open_ids = [
        b.id for b in session.scalars(
            select(Batch).where(
                Batch.status.notin_(("done", "abandoned"))
            )
        )
    ]
    if open_ids:
        for item in session.scalars(
            select(BatchItem).where(BatchItem.batch_id.in_(open_ids))
        ):
            if item.sku:
                out.add(item.sku.strip().upper())
    cutoff = datetime.utcnow().replace(tzinfo=timezone.utc) - timedelta(
        hours=1
    )
    # Newest pairings first; stop at the first one older than the
    # window (id order tracks assignment order).
    for a in session.scalars(
        select(RfidAssignment).order_by(RfidAssignment.id.desc())
        .limit(500)
    ):
        ts = _as_utc(a.assigned_at)
        if ts is not None and ts < cutoff:
            break
        if a.sku:
            out.add(a.sku.strip().upper())
    return out


def _log_onhand(
    session: Session, sku: str, value: int, source: str = "orders-sync"
) -> None:
    """Append an observation when the value CHANGED - the memory behind
    the Inventory Check window's movement hover."""
    last = session.scalar(
        select(OnhandLog).where(func.upper(OnhandLog.sku) == sku.upper())
        .order_by(OnhandLog.id.desc())
    )
    if last is None or last.on_hand != value:
        session.add(OnhandLog(sku=sku, on_hand=value, source=source))


def refresh_mismatch_tasks(session: Session) -> dict:
    """One open Inventory Check per SKU whose tags ≠ on-hand +
    sold-unretired (+ backorder debt, within the unavailable bucket).
    The arithmetic files into the SAME category human counts use (Nick,
    2026-09-02) - one task per SKU, whoever noticed first - but it only
    auto-CLOSES tasks it created itself: a human's standing count
    dispute is not the system's to wave away.

    Sales are WINDOWED to each SKU's tag-pool baseline (oldest live
    pairing, or a newer confirmed on-hand write): a unit sold before
    the pool existed never had a tag. SKUs with a receive mid-flight
    are skipped (guard 1); a surplus within Shopify's Unavailable
    bucket counts as agreement (guard 4)."""
    sold_all = sold_unretired_map(session)
    open_tasks = {
        (t.sku or "").strip().upper(): t
        for t in session.scalars(
            select(ReviewTask).where(
                ReviewTask.category.in_((CATEGORY, "tag-onhand-mismatch")),
                ReviewTask.status == "open",
            )
        ).all()
        if t.sku
    }
    debt_all = backorder_debt_map(session)
    skus = sorted(set(sold_all) | set(open_tasks) | set(debt_all))
    if not skus:
        return {"tasks_opened": 0, "tasks_closed": 0}
    _clear_superseded_debts(session, skus)
    debts = backorder_debt_map(session, skus)
    baselines = _sku_baselines(session, skus)
    sold = sold_unretired_since_map(session, skus, baselines)
    in_flight = (
        _receiving_in_flight_skus(session)
        | _tagging_in_flight_skus(session)
    )
    try:
        stock = shopify.get_stock_info_by_skus(skus)
    except Exception as error:
        logger.warning("mismatch check skipped (on-hand fetch): %s", error)
        return {"tasks_opened": 0, "tasks_closed": 0}
    on_hand_ci = {
        (k or "").strip().upper(): (v or {}).get("on_hand")
        for k, v in (stock or {}).items()
    }
    unavail_ci = {
        (k or "").strip().upper(): (v or {}).get("unavailable") or 0
        for k, v in (stock or {}).items()
    }

    # Non-taggable products (bins of loose thumbscrews) are outside the
    # RFID system by decision — their arithmetic is meaningless. Any open
    # task for one is auto-closed by the tags != expected branch never
    # firing... which it would keep doing, so skip AND close explicitly.
    from app.models import NonTaggable
    no_tag = {
        (r.sku or "").strip().upper()
        for r in session.scalars(select(NonTaggable))
    }
    # Bundles have no box of their own - the components carry the tags
    # (Nick, 2026-09-15, the DSLR Buddy couplers: bundle listings that
    # hold inventory and deliberately zero tags). Skip AND close, like
    # non-taggables.
    from app.models import ProductKind
    bundles = {
        (r.sku or "").strip().upper()
        for r in session.scalars(select(ProductKind))
        if r.kind == "bundle"
    }

    def _own(task) -> bool:
        # The arithmetic may only close its OWN filings; a human-filed
        # check (batch completion) waits for human or batch evidence.
        return (task.created_by or "") == "orders-sync"

    opened = closed = 0
    for sku in skus:
        if sku in no_tag:
            task = open_tasks.get(sku)
            if task is not None and _own(task):
                task.status = "resolved"
                task.resolved_by = "orders-sync"
                task.resolved_at = datetime.utcnow()
                task.resolution_note = (
                    "Product marked non-taggable — it sits outside the "
                    "RFID system, so the tag arithmetic no longer applies."
                )
                closed += 1
            continue
        if sku in bundles:
            task = open_tasks.get(sku)
            if task is not None and _own(task):
                task.status = "resolved"
                task.resolved_by = "orders-sync"
                task.resolved_at = datetime.utcnow()
                task.resolution_note = (
                    "Marked as a bundle - its components carry the "
                    "tags, so the listing holds inventory with no RFID "
                    "arithmetic of its own."
                )
                closed += 1
            continue
        oh = on_hand_ci.get(sku)
        if oh is None:
            continue
        _log_onhand(session, sku, oh)
        # Mid-receive: tags jump at pairing, on-hand at the planner's
        # save - comparing inside the window files noise. Skip filing
        # and closing; re-word an open check so nobody chases it.
        if sku in in_flight:
            task = open_tasks.get(sku)
            if task is not None and task.status == "open":
                marker = "A shipment is being received right now"
                if marker not in (task.detail or ""):
                    task.detail = (
                        f"{marker} - tag and stock numbers move until "
                        "TC-Planner saves the order. Re-check after."
                    )[:500]
            continue
        tags = tag_units(session, sku)
        # No live tags = no RFID claim to check (Nick, 2026-08-26, the
        # ZWO ANTI-DEW case: 0 tags, on-hand 0, one old ledger row filed
        # "0 units but expected 1"). With nothing tagged, the sold-ledger
        # arithmetic has no tag pool to reconcile against, and the task's
        # own remedy - a sweep that hears the remaining tags - cannot
        # work. Untagged-stock gaps are the Audit tab's job instead.
        if tags == 0:
            task = open_tasks.get(sku)
            if task is not None and _own(task):
                task.status = "resolved"
                task.resolved_by = "orders-sync"
                task.resolved_at = datetime.utcnow()
                task.resolution_note = (
                    "No live tags on file - there is no tag arithmetic to "
                    "check. Untagged stock shows in the Audit tab instead."
                )
                closed += 1
            continue
        # Backordered units absorbed at receiving (Shopify on-hand went
        # negative before the delivery landed) are real tagged boxes
        # Shopify's number doesn't count — expected must carry them or
        # the SKU false-flags forever (Nick, 2026-08-26, AirGradient).
        expected = oh + sold.get(sku, 0) + debts.get(sku, 0)
        unavail = unavail_ci.get(sku, 0)
        task = open_tasks.get(sku)
        diff = tags - expected
        # A surplus that fits inside Shopify's Unavailable bucket is
        # agreement (drift guard 4): those units were tagged at
        # receiving, then reserved/damaged out of the shelf number.
        within_unavail = 0 < diff <= unavail
        if diff != 0 and not within_unavail:
            base = baselines.get(sku)
            since = (
                base.strftime("%b %d") if base is not None else "ever"
            )
            detail = (
                f"RFID tags stand for {tags} unit(s) but the expected count "
                f"is {expected} (Shopify on-hand {oh}"
                + (
                    f" + {sold[sku]} sold or shipped since the tag pool "
                    f"began ({since})"
                    if sold.get(sku)
                    else ""
                )
                + (
                    f" + {debts[sku]} on customer backorder when received"
                    if debts.get(sku)
                    else ""
                )
                + (
                    f"; {unavail} more sit in Shopify's Unavailable bucket"
                    if unavail
                    else ""
                )
                + "). Recommend a bin audit — a sweep that hears the "
                "remaining tags can mark the sold ones."
            )
            if task is None:
                title_row = session.scalars(
                    select(RfidAssignment).where(
                        func.upper(RfidAssignment.sku) == sku
                    )
                ).first()
                session.add(ReviewTask(
                    category=CATEGORY,
                    sku=title_row.sku if title_row else sku,
                    product_title=(
                        title_row.product_title if title_row else None
                    ),
                    detail=detail[:500],
                    created_by="orders-sync",
                ))
                opened += 1
            elif _own(task) and task.detail != detail[:500]:
                task.detail = detail[:500]
        elif task is not None and _own(task):
            task.status = "resolved"
            task.resolved_by = "orders-sync"
            task.resolved_at = datetime.utcnow()
            task.resolution_note = (
                "Tags exceed the shelf number by the Unavailable bucket "
                "- matches its unavailable stock."
                if within_unavail else
                "Tags, on-hand and the sold ledger agree again."
            )
            closed += 1
    return {"tasks_opened": opened, "tasks_closed": closed}


# ------------------------------------------------- duplicate products -------
DUP_CATEGORY = "duplicate-product"


def _norm_sku(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def dup_pair_of(detail: str | None):
    """The (upper, upper) SKU pair a duplicate task names, as a
    frozenset — tolerant of every separator this feature has written:
    the ASCII "<->", the original "⇄", and the "?" SQL Server's VARCHAR
    turned that arrow into."""
    m = re.match(
        r"Possible duplicate products: (.+?) (?:<->|⇄|\?) (.+?) —",
        detail or "",
    )
    if not m:
        return None
    return frozenset((
        m.group(1).strip().upper(), m.group(2).strip().upper()
    ))


def _is_open_box(sku: str, titles: set[str]) -> bool:
    """Open-box listings legitimately share the new product's barcode —
    they must never be flagged as duplicates of it."""
    hay = " ".join([sku, *titles]).upper()
    if re.search(r"OPEN[\s\-–]?BOX", hay):
        return True
    return bool(re.search(r"[-_ ]OB\d*$", sku.upper()))


def refresh_duplicate_tasks(session: Session) -> dict:
    """One open duplicate-product review task per suspicious SKU pair.

    Runs ONLY inside sync runs (the daily 8 AM pass and the Review tab's
    manual button) over the SKUs that actually hold tags — never work
    done per scan. EXACT evidence only (the fuzzy edit-distance match
    drowned Review — the catalog is full of SKUs one character apart;
    Nick, 2026-08-18): a pair is flagged when two different SKUs share
    the SAME saved barcode, or the SKUs are the same string up to
    case/punctuation. Open-box products are ignored entirely. A
    DISMISSED pair stays dismissed — each pair is filed once, ever.
    Open tasks close themselves when a side loses its tags or the pair
    no longer qualifies under the current rules."""
    info: dict[str, dict] = {}
    for a in session.scalars(select(RfidAssignment)).all():
        sku = (a.sku or "").strip()
        if not sku:
            continue
        side = info.setdefault(sku.upper(), {
            "sku": sku, "titles": set(), "units": 0, "barcodes": set(),
        })
        side["units"] += a.case_units or 1
        if a.product_title:
            side["titles"].add(a.product_title)
        if a.barcode and a.barcode.strip():
            side["barcodes"].add(a.barcode.strip().upper())

    eligible = {
        k for k, v in info.items()
        if not _is_open_box(v["sku"], v["titles"])
    }

    pair_reasons: dict[tuple[str, str], str] = {}
    # Same saved barcode, different SKU.
    by_barcode: dict[str, set[str]] = {}
    for k in eligible:
        for bc in info[k]["barcodes"]:
            by_barcode.setdefault(bc, set()).add(k)
    for bc, skus in by_barcode.items():
        if len(skus) < 2:
            continue
        ordered = sorted(skus)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                pair_reasons.setdefault(
                    (a, b), f"both carry barcode {bc}"
                )
    # Same SKU up to case/punctuation.
    by_norm: dict[str, set[str]] = {}
    for k in eligible:
        nk = _norm_sku(k)
        if len(nk) >= 4:
            by_norm.setdefault(nk, set()).add(k)
    for nk, skus in by_norm.items():
        if len(skus) < 2:
            continue
        ordered = sorted(skus)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                pair_reasons.setdefault(
                    (a, b), "the same SKU written differently"
                )
    # One adjacent-swap apart (the ASIAIR/AISAIR class, Nick,
    # 2026-09-14: the transposed twin hid the bracket's existing tags
    # and the shelf got re-stickered). Deliberately NOT general edit
    # distance - that drowned Review (2026-08-18): substitutions are
    # how real SKU families differ (EAF-5V vs EAF-5C), but two
    # tag-holding SKUs that are the same letters with two neighbours
    # swapped are a typo.
    def _adjacent_swap(a: str, b: str) -> bool:
        if len(a) != len(b) or a == b:
            return False
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return (
            len(diff) == 2 and diff[1] == diff[0] + 1
            and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]
        )

    by_len: dict[int, list[str]] = {}
    for k in eligible:
        nk = _norm_sku(k)
        if len(nk) >= 6:
            by_len.setdefault(len(nk), []).append(k)
    for keys in by_len.values():
        keys.sort()
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                if _adjacent_swap(_norm_sku(a), _norm_sku(b)):
                    pair_reasons.setdefault(
                        (a, b),
                        "the same letters with two neighbours swapped "
                        "- a likely typo twin",
                    )

    # Both sides living in the live catalog = two REAL Shopify
    # listings, not a duplicate (Nick, 2026-09-14: pairs of existing
    # products flooded Review). A pair only qualifies when at least
    # one side is NOT linked to a Shopify product - the ASIAIR shape,
    # where the catalog knows one spelling and orphan tags wear the
    # other. Filtered pairs' open tasks close themselves below.
    if pair_reasons:
        all_skus = {s for pair in pair_reasons for s in pair}
        in_catalog = {
            (e.sku or "").strip().upper()
            for e in session.scalars(
                select(BinMapEntry).where(
                    func.upper(BinMapEntry.sku).in_(sorted(all_skus))
                )
            )
        }
        pair_reasons = {
            (a, b): reason
            for (a, b), reason in pair_reasons.items()
            if a not in in_catalog or b not in in_catalog
        }

    existing = session.scalars(
        select(ReviewTask).where(ReviewTask.category == DUP_CATEGORY)
    ).all()
    # Match by the PAIR, not the exact string: SQL Server's VARCHAR
    # mangled the original "⇄" separator into a literal "?", so string
    # comparison silently never matched (8k ghost tasks in prod). The
    # key is pure ASCII now ("<->") and the parser accepts every format
    # this feature has ever written.
    existing_pairs: set = set()
    for t in existing:
        p = dup_pair_of(t.detail)
        if p:
            existing_pairs.add(p)
    opened = closed = 0
    current_pairs = set()
    for (a, b), reason in sorted(pair_reasons.items()):
        fs = frozenset((a, b))
        current_pairs.add(fs)
        if fs in existing_pairs:
            continue  # filed once, ever — dismissed pairs stay dismissed
        title = next(iter(info[a]["titles"]), None)
        key = (f"Possible duplicate products: "
               f"{info[a]['sku']} <-> {info[b]['sku']}")
        session.add(ReviewTask(
            category=DUP_CATEGORY,
            sku=info[a]["sku"],
            product_title=title,
            detail=(f"{key} — {reason}; {info[a]['units']} and "
                    f"{info[b]['units']} tag unit(s) on file. Resolve to "
                    f"merge the tags into one product (you pick the SKU "
                    f"and name), or dismiss if they really are two "
                    f"products."),
            created_by="dupe-check",
        ))
        opened += 1

    # Close open tasks whose pair no longer qualifies: a side was merged
    # or cleaned up, or the detection rules tightened out from under it.
    for t in existing:
        if t.status != "open":
            continue
        p = dup_pair_of(t.detail)
        if p is None or p in current_pairs:
            continue
        t.status = "resolved"
        t.resolved_by = "dupe-check"
        t.resolved_at = datetime.utcnow()
        t.resolution_note = (
            "No longer flagged: a side was merged/cleaned up, or the "
            "detection rules tightened (exact barcode/SKU evidence only)."
        )
        closed += 1
    return {"dupes_opened": opened, "dupes_closed": closed}


# ------------------------------------------------------------------ sync ----
def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    except ValueError:
        return None


def run(session: Session, source: str = "manual") -> dict:
    """One sync pass. Fail-soft: every outcome lands in the status
    setting; a missing scope is a state, not an exception."""
    t0 = time.time()
    _mark_running(session)
    session.commit()
    status: dict = {
        "ok": False,
        "at": datetime.utcnow().isoformat(timespec="seconds"),
        "source": source,
    }
    # Duplicate-SKU detection rides the daily and manual passes; the
    # hourly ledger pull skips it (it walks every assignment row, and
    # nothing about duplicates changes hour to hour).
    if source != "hourly":
        try:
            status.update(refresh_duplicate_tasks(session))
        except Exception:  # noqa: BLE001 — never let the dup check kill a sync
            logger.exception("duplicate check failed")
    # ShipStation FIRST, so the Shopify feed's dedupe below sees fresh
    # rows. Its failure never blocks the fallback feed.
    try:
        status.update(sync_shipstation(session))
        session.flush()
    except Exception as error:  # noqa: BLE001 — fallback feed still runs
        status["ss_error"] = str(error)[:300]
        logger.exception("shipstation sync failed")
    try:
        cursor = _get(session, CURSOR_KEY) or (
            datetime.utcnow() - timedelta(days=FIRST_LOOKBACK_DAYS)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        orders = shopify.get_fulfilled_orders(cursor)
        tracked = tracked_skus(session)
        recorded = 0
        skipped_ss = qty_conflicts = 0
        for order in orders:
            order_no = _norm_order_no(order.get("name"))
            for line in order["lines"]:
                sku = (line["sku"] or "").strip()
                if sku.upper() not in tracked:
                    continue
                row = session.scalars(
                    select(SoldRecord).where(
                        SoldRecord.order_id == order["order_id"],
                        func.upper(SoldRecord.sku) == sku.upper(),
                    )
                ).first()
                if row is None and order_no:
                    # The ShipStation feed already holds this line (its
                    # rows key on ShipStation's order id, so the
                    # order_id match above can't see them): the label
                    # is the physical truth, don't double-record.
                    ss_row = session.scalars(
                        select(SoldRecord).where(
                            SoldRecord.ss_order_id.is_not(None),
                            func.upper(SoldRecord.sku) == sku.upper(),
                            SoldRecord.order_name.in_(
                                (order_no, f"#{order_no}")
                            ),
                        )
                    ).first()
                    if ss_row is not None:
                        skipped_ss += 1
                        if ss_row.quantity != line["qty"]:
                            qty_conflicts += 1
                        continue
                if row is None:
                    session.add(SoldRecord(
                        order_id=order["order_id"],
                        order_name=(order.get("name") or "")[:32] or None,
                        sku=sku,
                        quantity=line["qty"],
                        fulfilled_at=_parse_iso(order.get("fulfilled_at")),
                        source="shopify",
                    ))
                    recorded += 1
                elif row.ss_order_id is not None:
                    # Adopted by ShipStation: the labels own the
                    # quantity; a disagreement is surfaced, not applied.
                    if row.quantity != line["qty"]:
                        qty_conflicts += 1
                elif row.quantity != line["qty"]:
                    row.quantity = line["qty"]  # order was edited
        session.flush()
        # Overlap the next window by an hour so an order fulfilling
        # mid-run can't slip between two syncs. Upserts absorb the dupes.
        _set(session, CURSOR_KEY, (
            datetime.utcnow() - timedelta(hours=1)
        ).strftime("%Y-%m-%dT%H:%M:%SZ"))
        status.update(ok=True, orders=len(orders), recorded=recorded)
        if skipped_ss:
            status["shopify_lines_covered_by_ss"] = skipped_ss
        if qty_conflicts:
            status["qty_conflicts"] = qty_conflicts
    except RuntimeError as error:
        if "ACCESS_DENIED" in str(error) or "Access denied" in str(error):
            status["waiting_scope"] = True
            status["error"] = (
                "The Shopify app doesn't have the read_orders scope yet — "
                "add it under Develop apps → Configuration, and the next "
                "run picks it up."
            )
        else:
            status["error"] = str(error)[:300]
        logger.warning("orders sync: %s", status["error"])
    except Exception as error:  # noqa: BLE001 — sync must never take the app down
        status["error"] = str(error)[:300]
        logger.exception("orders sync failed")
    finally:
        # The mismatch re-check runs when EITHER feed moved the ledger:
        # a working ShipStation pull with the Shopify orders scope
        # missing is still a good run (the whole point of two sources).
        if "ss_shipments_seen" in status and not status.get("ok"):
            status["ok"] = True
        if status.get("ok"):
            try:
                status.update(refresh_mismatch_tasks(session))
            except Exception:  # noqa: BLE001 — the ledger pull still stands
                logger.exception("mismatch refresh failed")
        _set(session, STATUS_KEY, json.dumps(status)[:2000])
        _clear_running(session, int((time.time() - t0) * 1000), source)
        session.commit()
    return status


def current_status(session: Session) -> dict:
    raw = _get(session, STATUS_KEY)
    try:
        parsed = json.loads(raw) if raw else None
    except ValueError:
        parsed = None
    return {
        "configured": True,
        "last_run": parsed,
        "cursor": _get(session, CURSOR_KEY),
        "running": _get(session, f"refresh_running:{KIND}") is not None,
    }


# ------------------------------------------------------------- scheduler ----
_thread_started = False


def _seconds_until_hourly() -> float:
    """Next :07 past the hour - off the top of the hour so the pull
    never collides with other on-the-hour jobs."""
    now = datetime.now(timezone.utc)
    target = now.replace(minute=7, second=0, microsecond=0)
    if target <= now:
        target += timedelta(hours=1)
    return max(60.0, (target - now).total_seconds())


def _sync_loop() -> None:
    """HOURLY ledger pull (Nick, 2026-09-23: the once-daily pass left
    the ledger up to 24h behind the adjustment history, so sales kept
    falling on the wrong side of freshly-moved baselines); the first
    pass at/after SYNC_HOUR Toronto additionally runs the daily
    housekeeping (duplicate detection)."""
    from app.database import get_engine

    while True:
        try:
            time.sleep(_seconds_until_hourly())
            with Session(get_engine()) as session:
                # One run per hour even with several workers.
                stamp = datetime.utcnow().strftime("%Y-%m-%dT%H")
                if _get(session, HOURLY_KEY) == stamp:
                    continue
                _set(session, HOURLY_KEY, stamp)
                session.commit()
                tor = datetime.now(ZoneInfo("America/Toronto"))
                daily = (
                    tor.hour >= SYNC_HOUR
                    and _get(session, DAILY_KEY) != tor.strftime("%Y-%m-%d")
                )
                if daily:
                    _set(session, DAILY_KEY, tor.strftime("%Y-%m-%d"))
                    session.commit()
                run(session, source="auto" if daily else "hourly")
        except Exception:  # noqa: BLE001 — the loop must survive anything
            logger.exception("orders sync loop")
            time.sleep(300)


def start_daily_thread() -> None:
    """Called once from app startup (name kept from the daily era).
    No-op when disabled (tests set ORDERS_SYNC_DISABLE=1) or already
    started."""
    global _thread_started
    if _thread_started or os.getenv("ORDERS_SYNC_DISABLE") == "1":
        return
    _thread_started = True
    threading.Thread(
        target=_sync_loop, name="orders-sync-loop", daemon=True
    ).start()
