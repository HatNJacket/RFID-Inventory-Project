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
from app.database import ci_in
from app.models import (
    AppSetting,
    BarcodeChange,
    Batch,
    BatchItem,
    BinMapEntry,
    BundleContent,
    OnhandLog,
    OrderReceipt,
    RefreshLog,
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
        # Index-friendly on a case-insensitive database (2026-09-29).
        return stmt.where(ci_in(column, sorted(uppers)))
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


# ------------------------------------------------------- bundle sales -------
def bundle_defs(session: Session) -> dict[str, list[tuple[str, int]]]:
    """Upper bundle SKU -> [(component_sku, per-unit qty), ...]. One
    query; the whole table is a few hundred rows."""
    out: dict[str, list[tuple[str, int]]] = {}
    for bc in session.scalars(
        select(BundleContent).order_by(BundleContent.id)
    ):
        out.setdefault(
            (bc.bundle_sku or "").strip().upper(), []
        ).append((bc.component_sku, bc.qty or 1))
    return out


def explode_bundle_items(defs: dict, items: list[dict]) -> list[dict]:
    """A sold bundle is components leaving the shelf: expand bundle
    line items into component line items (one level - a bundle listed
    inside a bundle stays as itself) so the ledger only ever records
    things that physically carry tags. Non-bundle items pass through
    untouched."""
    if not defs:
        return items
    out: list[dict] = []
    for it in items:
        key = (it.get("sku") or "").strip().upper()
        contents = defs.get(key)
        if not contents:
            out.append(it)
            continue
        for comp, per in contents:
            out.append({**it, "sku": comp,
                        "qty": (it.get("qty") or 0) * per})
    return out


def explode_ledger_bundles(session: Session) -> dict:
    """One-time repair for rows recorded BEFORE their bundle was
    defined: re-book each bundle-SKU ledger row as its component rows
    (quantities and per-shipment maps scaled), then settle any that
    predate the component's tag-pool baseline - exactly what the live
    explosion would have written. Rows something already retired
    against are left alone (that history is spent)."""
    defs = bundle_defs(session)
    stats = {"rows_exploded": 0, "component_rows": 0, "settled": 0}
    if not defs:
        return stats
    rows = [
        r for r in session.scalars(select(SoldRecord))
        if (r.sku or "").strip().upper() in defs
        and (r.quantity or 0) > 0 and not (r.retired or 0)
    ]
    if not rows:
        return stats
    made: list[SoldRecord] = []
    for r in rows:
        for comp, per in defs[(r.sku or "").strip().upper()]:
            ship_map = {}
            try:
                parsed = json.loads(r.ss_shipments or "{}")
                if isinstance(parsed, dict):
                    ship_map = {k: int(v) * per for k, v in parsed.items()}
            except (ValueError, TypeError):
                ship_map = {}
            # Merge into an existing row for the same (order, component)
            # rather than double-recording a mixed order.
            existing = session.scalars(
                select(SoldRecord).where(
                    SoldRecord.order_id == r.order_id,
                    func.upper(SoldRecord.sku) == comp.strip().upper(),
                )
            ).first()
            if existing is not None:
                existing.quantity = (
                    (existing.quantity or 0) + (r.quantity or 0) * per
                )
                continue
            row = SoldRecord(
                order_id=r.order_id,
                order_name=r.order_name,
                sku=comp,
                quantity=(r.quantity or 0) * per,
                retired=0,
                fulfilled_at=r.fulfilled_at,
                source=r.source,
                ss_order_id=r.ss_order_id,
                ss_shipments=(
                    json.dumps(ship_map)[:2000] if ship_map else None
                ),
                ss_line_qty=(
                    r.ss_line_qty * per
                    if r.ss_line_qty is not None else None
                ),
            )
            session.add(row)
            made.append(row)
            stats["component_rows"] += 1
        session.delete(r)
        stats["rows_exploded"] += 1
    session.flush()
    # Sales older than the component's baseline can't expect tags -
    # settle them exactly like the first-run backfill does.
    if made:
        uppers = {(m.sku or "").strip().upper() for m in made}
        baselines = _sku_baselines(session, uppers)
        for m in made:
            cut = _as_utc(baselines.get((m.sku or "").strip().upper()))
            f = _as_utc(m.fulfilled_at)
            if cut is not None and f is not None and f <= cut:
                m.retired = m.quantity
                stats["settled"] += 1
    return stats


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
    # A shipped bundle is components leaving the shelf: its line items
    # are exploded into component lines BEFORE the ledger sees them, so
    # audits can explain the components' silence (round 12).
    defs = bundle_defs(session)
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
        for it in explode_bundle_items(defs, sh["items"]):
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
        # The forward feed exploded bundle lines, so the void must
        # unwind the same component rows.
        for it in explode_bundle_items(defs, sh["items"]):
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
        # Same bundle explosion as the ShipStation feed, so the two
        # sources keep recording the SAME (order, component) lines and
        # the dedupe between them still matches up.
        b_defs = bundle_defs(session)
        for order in orders:
            order_no = _norm_order_no(order.get("name"))
            for line in explode_bundle_items(b_defs, order["lines"]):
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
                "The Shopify app doesn't have the read_orders scope yet - "
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
        # A working ShipStation pull with the Shopify orders scope
        # missing is still a good run (the whole point of two sources).
        # Mismatches surface through the audit queue now - no task
        # refresh here since the 2026-09-28 scope reset.
        if "ss_shipments_seen" in status and not status.get("ok"):
            status["ok"] = True
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
