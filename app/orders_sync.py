"""Fulfilled-order sync: teaches the RFID system that stock has SOLD.

A shipped box leaves the building with its RFID tag still on file, so
from the moment an order fulfills, "tags on file" legitimately exceeds
Shopify's on-hand by the sold count. This module keeps that ledger
(rfid_sold_ledger):

    expected tags for a SKU  =  live Shopify on-hand  +  sold-unretired

READ-ONLY against Shopify, and it needs the read_orders access scope —
until that's granted on the custom app, every run reports
waiting_scope and touches nothing. Runs daily at 8 AM Toronto time
plus on the Review tab's manual button. Each successful run also
re-checks the ledger'd SKUs and keeps ONE open "tag-onhand-mismatch"
review task per SKU whose numbers don't add up — and closes it again
by itself when the world catches up (that's a local record, so
auto-closing is safe). Audits remain the only thing that CONFIRMS a
count; this module only moves expectations.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import shopify
from app.models import (
    AppSetting,
    BarcodeChange,
    RefreshLog,
    ReviewTask,
    RfidAssignment,
    SoldRecord,
)

logger = logging.getLogger("rfid.orders")

KIND = "orders-sync"            # refresh-stats kind (button + auto share it)
CATEGORY = "tag-onhand-mismatch"  # the review-task type this module owns
STATUS_KEY = "orders_sync_status"
CURSOR_KEY = "orders_sync_cursor"
DAILY_KEY = "orders_sync_last_daily"
SYNC_HOUR = 8                   # 8 AM, America/Toronto
FIRST_LOOKBACK_DAYS = 7


# ------------------------------------------------------------ settings io ---
def _get(session: Session, key: str) -> str | None:
    row = session.get(AppSetting, key)
    return row.value if row else None


def _set(session: Session, key: str, value: str) -> None:
    row = session.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key)
        session.add(row)
    row.value = value[:500]


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


def sold_unretired_map(
    session: Session, skus: list[str] | None = None
) -> dict[str, int]:
    """Upper-cased SKU -> units sold (fulfilled) whose tag is still on
    file. This is the number that explains missing tags in audits."""
    stmt = select(SoldRecord)
    if skus is not None:
        uppers = [s.strip().upper() for s in skus]
        stmt = stmt.where(func.upper(SoldRecord.sku).in_(uppers))
    out: dict[str, int] = {}
    for row in session.scalars(stmt).all():
        left = max(0, (row.quantity or 0) - (row.retired or 0))
        if left:
            key = row.sku.strip().upper()
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
    uppers = [s.strip().upper() for s in skus]
    out: dict[str, int] = {}
    if not uppers:
        return out
    for row in session.scalars(
        select(SoldRecord).where(func.upper(SoldRecord.sku).in_(uppers))
    ).all():
        left = max(0, (row.quantity or 0) - (row.retired or 0))
        if not left:
            continue
        key = row.sku.strip().upper()
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
    uppers = [s.strip().upper() for s in skus]
    out: dict[str, object] = {}
    if not uppers:
        return out
    for row in session.scalars(
        select(SoldRecord).where(func.upper(SoldRecord.sku).in_(uppers))
    ).all():
        f = _as_utc(row.fulfilled_at)
        if f is None:
            continue
        key = row.sku.strip().upper()
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
    """Upper SKU -> the tag pool's baseline: newest live pairing (any
    bin), or a newer confirmed on-hand write, whichever is later. Sales
    fulfilled before it cannot be expected to have tags."""
    uppers = [s.strip().upper() for s in skus]
    out: dict[str, object] = {}
    if not uppers:
        return out
    for t in session.scalars(
        select(RfidAssignment).where(
            func.upper(RfidAssignment.sku).in_(uppers)
        )
    ):
        k = t.sku.strip().upper()
        ts = _as_utc(t.assigned_at)
        if ts is not None and (k not in out or ts > out[k]):
            out[k] = ts
    for bc in session.scalars(
        select(BarcodeChange).where(
            func.upper(BarcodeChange.sku).in_(uppers),
            BarcodeChange.changed_field.in_((
                "on-hand", "on-hand-undo",
                "on-hand-lower", "on-hand-lower-undo",
            )),
        )
    ):
        k = (bc.sku or "").strip().upper()
        ts = _as_utc(bc.changed_at)
        if ts is not None and (k not in out or ts > out[k]):
            out[k] = ts
    return out


# ------------------------------------------------------- mismatch tasks -----
def refresh_mismatch_tasks(session: Session) -> dict:
    """One open tag-onhand-mismatch task per SKU whose tags ≠ on-hand +
    sold-unretired; auto-closed when the numbers agree again. DISTINCT
    from inventory-check (a human counted the shelf and disagreed) — this
    category is arithmetic, so the system may both open and close it.

    Sales are WINDOWED to each SKU's tag-pool baseline (same rule as the
    shelf reconcile): a unit sold before tagging never had a tag, so it
    belongs in neither side of the expectation. Without this, the 60-day
    ledger backfill false-flagged every SKU with pre-tagging sales
    (Nick, 2026-08-24)."""
    sold_all = sold_unretired_map(session)
    open_tasks = {
        (t.sku or "").strip().upper(): t
        for t in session.scalars(
            select(ReviewTask).where(
                ReviewTask.category == CATEGORY,
                ReviewTask.status == "open",
            )
        ).all()
    }
    skus = sorted(set(sold_all) | set(open_tasks))
    if not skus:
        return {"tasks_opened": 0, "tasks_closed": 0}
    baselines = _sku_baselines(session, skus)
    sold = sold_unretired_since_map(session, skus, baselines)
    try:
        on_hand = shopify.get_on_hand_by_skus(skus)
    except Exception as error:
        logger.warning("mismatch check skipped (on-hand fetch): %s", error)
        return {"tasks_opened": 0, "tasks_closed": 0}
    on_hand_ci = {
        (k or "").strip().upper(): v for k, v in (on_hand or {}).items()
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

    opened = closed = 0
    for sku in skus:
        if sku in no_tag:
            task = open_tasks.get(sku)
            if task is not None:
                task.status = "resolved"
                task.resolved_by = "orders-sync"
                task.resolved_at = datetime.utcnow()
                task.resolution_note = (
                    "Product marked non-taggable — it sits outside the "
                    "RFID system, so the tag arithmetic no longer applies."
                )
                closed += 1
            continue
        oh = on_hand_ci.get(sku)
        if oh is None:
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
            if task is not None:
                task.status = "resolved"
                task.resolved_by = "orders-sync"
                task.resolved_at = datetime.utcnow()
                task.resolution_note = (
                    "No live tags on file - there is no tag arithmetic to "
                    "check. Untagged stock shows in the Audit tab instead."
                )
                closed += 1
            continue
        expected = oh + sold.get(sku, 0)
        task = open_tasks.get(sku)
        if tags != expected:
            base = baselines.get(sku)
            since = (
                base.strftime("%b %d") if base is not None else "ever"
            )
            detail = (
                f"RFID tags stand for {tags} unit(s) but the expected count "
                f"is {expected} (Shopify on-hand {oh}"
                + (
                    f" + {sold[sku]} sold since tagging ({since})"
                    if sold.get(sku)
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
            elif task.detail != detail[:500]:
                task.detail = detail[:500]
        elif task is not None:
            task.status = "resolved"
            task.resolved_by = "orders-sync"
            task.resolved_at = datetime.utcnow()
            task.resolution_note = (
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
    # Duplicate-SKU detection rides every sync run and doesn't depend on
    # the orders scope — it must work while read_orders is still pending.
    try:
        status.update(refresh_duplicate_tasks(session))
    except Exception:  # noqa: BLE001 — never let the dup check kill a sync
        logger.exception("duplicate check failed")
    try:
        cursor = _get(session, CURSOR_KEY) or (
            datetime.utcnow() - timedelta(days=FIRST_LOOKBACK_DAYS)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        orders = shopify.get_fulfilled_orders(cursor)
        tracked = tracked_skus(session)
        recorded = 0
        for order in orders:
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
                if row is None:
                    session.add(SoldRecord(
                        order_id=order["order_id"],
                        order_name=(order.get("name") or "")[:32] or None,
                        sku=sku,
                        quantity=line["qty"],
                        fulfilled_at=_parse_iso(order.get("fulfilled_at")),
                    ))
                    recorded += 1
                elif row.quantity != line["qty"]:
                    row.quantity = line["qty"]  # order was edited
        session.flush()
        # Overlap the next window by an hour so an order fulfilling
        # mid-run can't slip between two syncs. Upserts absorb the dupes.
        _set(session, CURSOR_KEY, (
            datetime.utcnow() - timedelta(hours=1)
        ).strftime("%Y-%m-%dT%H:%M:%SZ"))
        status.update(ok=True, orders=len(orders), recorded=recorded)
        status.update(refresh_mismatch_tasks(session))
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
        _set(session, STATUS_KEY, json.dumps(status)[:500])
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


def _seconds_until_daily() -> float:
    tz = ZoneInfo("America/Toronto")
    now = datetime.now(tz)
    target = now.replace(hour=SYNC_HOUR, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(60.0, (target - now).total_seconds())


def _daily_loop() -> None:
    from app.database import get_engine

    while True:
        try:
            time.sleep(_seconds_until_daily())
            with Session(get_engine()) as session:
                # One run per (Toronto) day even with several workers.
                today = datetime.now(
                    ZoneInfo("America/Toronto")
                ).strftime("%Y-%m-%d")
                if _get(session, DAILY_KEY) == today:
                    continue
                _set(session, DAILY_KEY, today)
                session.commit()
                run(session, source="auto")
        except Exception:  # noqa: BLE001 — the loop must survive anything
            logger.exception("orders sync daily loop")
            time.sleep(300)


def start_daily_thread() -> None:
    """Called once from app startup. No-op when disabled (tests set
    ORDERS_SYNC_DISABLE=1) or already started."""
    global _thread_started
    if _thread_started or os.getenv("ORDERS_SYNC_DISABLE") == "1":
        return
    _thread_started = True
    threading.Thread(
        target=_daily_loop, name="orders-sync-daily", daemon=True
    ).start()
