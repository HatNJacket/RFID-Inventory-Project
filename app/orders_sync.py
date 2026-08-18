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


def retire_units(session: Session, sku: str, units: int) -> int:
    """An audit marked `units` tags of this SKU sold: retire them against
    the OLDEST unretired sales first. Returns how many actually landed
    (never more than the ledger holds)."""
    landed = 0
    rows = session.scalars(
        select(SoldRecord)
        .where(func.upper(SoldRecord.sku) == sku.strip().upper())
        .order_by(SoldRecord.fulfilled_at, SoldRecord.id)
    ).all()
    for row in rows:
        if landed >= units:
            break
        room = max(0, (row.quantity or 0) - (row.retired or 0))
        take = min(room, units - landed)
        if take:
            row.retired = (row.retired or 0) + take
            landed += take
    return landed


# ------------------------------------------------------- mismatch tasks -----
def refresh_mismatch_tasks(session: Session) -> dict:
    """One open tag-onhand-mismatch task per SKU whose tags ≠ on-hand +
    sold-unretired; auto-closed when the numbers agree again. DISTINCT
    from inventory-check (a human counted the shelf and disagreed) — this
    category is arithmetic, so the system may both open and close it."""
    sold = sold_unretired_map(session)
    open_tasks = {
        (t.sku or "").strip().upper(): t
        for t in session.scalars(
            select(ReviewTask).where(
                ReviewTask.category == CATEGORY,
                ReviewTask.status == "open",
            )
        ).all()
    }
    skus = sorted(set(sold) | set(open_tasks))
    if not skus:
        return {"tasks_opened": 0, "tasks_closed": 0}
    try:
        on_hand = shopify.get_on_hand_by_skus(skus)
    except Exception as error:
        logger.warning("mismatch check skipped (on-hand fetch): %s", error)
        return {"tasks_opened": 0, "tasks_closed": 0}
    on_hand_ci = {
        (k or "").strip().upper(): v for k, v in (on_hand or {}).items()
    }

    opened = closed = 0
    for sku in skus:
        oh = on_hand_ci.get(sku)
        if oh is None:
            continue
        tags = tag_units(session, sku)
        expected = oh + sold.get(sku, 0)
        task = open_tasks.get(sku)
        if tags != expected:
            detail = (
                f"RFID tags stand for {tags} unit(s) but the expected count "
                f"is {expected} (Shopify on-hand {oh}"
                + (
                    f" + {sold[sku]} sold since the last audit"
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


def _edit_close(a: str, b: str) -> bool:
    """Edit distance ≤ 2 with early exit — catches transposed letters
    (AISAIR vs ASIAIR) and single typos without a fuzzy dependency."""
    if abs(len(a) - len(b)) > 2:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(
                prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)
            ))
        if min(cur) > 2:
            return False
        prev = cur
    return prev[-1] <= 2


def refresh_duplicate_tasks(session: Session) -> dict:
    """One open duplicate-product review task per suspicious SKU pair.

    Runs ONLY inside sync runs (the daily 8 AM pass and the Review tab's
    manual button) over the SKUs that actually hold tags — a few hundred
    strings compared once a day, never work done per scan (Nick's
    overhead worry). A pair is suspicious when the normalized SKUs are
    identical (case/punctuation noise) or within edit distance 2 with
    the same first character (the ZWO AISAIR/ASIAIR misspelling).
    A DISMISSED pair stays dismissed — the check files each pair once,
    ever. Open tasks close themselves when a side loses its tags
    (merged or cleaned up)."""
    info: dict[str, dict] = {}
    for a in session.scalars(select(RfidAssignment)).all():
        sku = (a.sku or "").strip()
        if not sku:
            continue
        side = info.setdefault(sku.upper(), {
            "sku": sku, "titles": set(), "units": 0,
        })
        side["units"] += a.case_units or 1
        if a.product_title:
            side["titles"].add(a.product_title)

    keys = sorted(info)
    pairs: list[tuple[str, str]] = []
    for i, a in enumerate(keys):
        na = _norm_sku(a)
        if len(na) < 6:
            continue
        for b in keys[i + 1:]:
            nb = _norm_sku(b)
            if len(nb) < 6 or na[0] != nb[0]:
                continue
            if na == nb or _edit_close(na, nb):
                pairs.append((a, b))

    existing = session.scalars(
        select(ReviewTask).where(ReviewTask.category == DUP_CATEGORY)
    ).all()
    opened = closed = 0
    for a, b in pairs:
        key = (f"Possible duplicate products: "
               f"{info[a]['sku']} ⇄ {info[b]['sku']}")
        if any((t.detail or "").startswith(key) for t in existing):
            continue
        title = next(iter(info[a]["titles"]), None)
        session.add(ReviewTask(
            category=DUP_CATEGORY,
            sku=info[a]["sku"],
            product_title=title,
            detail=(f"{key} — {info[a]['units']} and {info[b]['units']} "
                    f"tag unit(s) on file. Resolve to merge the tags into "
                    f"one product (you pick the SKU and name), or dismiss "
                    f"if they really are two products."),
            created_by="dupe-check",
        ))
        opened += 1

    # A side lost its tags -> the pair was merged/cleaned; close the task.
    for t in existing:
        if t.status != "open":
            continue
        m = re.match(r"Possible duplicate products: (.+) ⇄ (.+?) —",
                     t.detail or "")
        if not m:
            continue
        if (m.group(1).strip().upper() not in info
                or m.group(2).strip().upper() not in info):
            t.status = "resolved"
            t.resolved_by = "dupe-check"
            t.resolved_at = datetime.utcnow()
            t.resolution_note = (
                "One side no longer holds tags — merged or cleaned up."
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
