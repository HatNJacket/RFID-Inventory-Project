"""Bridge to the 1-left dashboard (Inventory Verification Function App).

That system watches Shopify inventory webhooks and queues every product
that drops to 1 on hand for a human to physically verify — the queue
behind the Ops Dashboard's "Stock Checks" number. The RFID system keeps
discovering physical stock all day (tags paired, shelves swept, bins
batch-tagged), so for many queued products the walk has effectively
already happened. This module joins their pending queue against RFID
evidence and, when the evidence proves at least the claimed stock exists,
confirms the check the same way their own UI's Verify button does.

What this module may call on their side, and NOTHING else:
  GET  /pending      — read the queue (their UI does this on every load)
  POST /bulk-confirm — confirm checks (their UI's select-all + Verify)
  POST /confirm      — confirm one check (their UI's per-row Verify)
  POST /import-skus  — re-queue one SKU: the undo for a confirm

Never called: update-stock, update-bin, update-barcode, report-issue,
close-issue — this bridge must not be able to change a stock number,
a bin, or a barcode anywhere but the RFID system, no matter what bugs
it grows. Their function app also runs a live two-store inventory sync;
its code is never touched from here.

Everything is gated by config.ONELEFT_MODE ("off" default / "read" /
"confirm") and fails SOFT — a dashboard outage can never break a scan,
a sweep, or a batch.

Evidence rules (what counts as "a user discovered stock"):
  · a tag paired to the SKU AFTER the check was detected (someone held
    the physical box);
  · a C72 sweep AFTER detection that heard one of the SKU's tags — even
    a tag paired long before (the box was physically on a shelf just
    now);
  · a batch STARTED after detection that counted the SKU — collect
    scans, sealed cases, and already-tagged/baseline boxes all count
    (bins recorded as tagged from an audit sweep are excluded: those
    rows are copied from tags already on file, and the sweep behind
    them already counts).
Evidence sources overlap on the same physical boxes, so the item's
evidence_units is the MAX across sources, never the sum. Auto-confirm
requires evidence_units >= Shopify's current claim and a claim >= 1;
a claim of 0 is never auto-confirmed (nothing physical can prove an
absence here — and evidence AGAINST a zero is flagged for a human).
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.models import (
    AppSetting,
    Batch,
    BatchItem,
    BinMapEntry,
    EpcCapture,
    OneLeftCheck,
    RfidAssignment,
)

logger = logging.getLogger("rfid.oneleft")

_TIMEOUT = 20
# Their confirm endpoint validates against this fixed list (recovered from
# their deployed source, 2026-08-17). A name outside it gets a clean 400,
# never a bad write — but keep it current if their app changes.
VALID_EMPLOYEES = {"Danielle", "Evie", "Matt", "Noor", "Steve"}

# The pending queue barely moves minute to minute and every board build
# starts with it; a short cache keeps kicks from hammering their app.
_PENDING_TTL = 60
_pending_cache: dict = {"at": 0.0, "data": None}

# Auto-scan throttle: interaction hooks (pairs, sweeps, batch completes)
# fire kick() freely; at most one background scan runs per window.
_SCAN_EVERY = 45
_scan_lock = threading.Lock()
_last_scan = 0.0

AUTO_SETTING_KEY = "oneleft_auto"


# ------------------------------------------------------------- HTTP layer ---
def configured() -> bool:
    return config.ONELEFT_MODE in ("read", "confirm") and bool(
        config.ONELEFT_URL
    )


def can_confirm() -> bool:
    return config.ONELEFT_MODE == "confirm"


def _get(path: str) -> dict:
    response = requests.get(f"{config.ONELEFT_URL}{path}", timeout=_TIMEOUT)
    response.raise_for_status()
    return response.json()


def _post(path: str, body: dict) -> dict:
    response = requests.post(
        f"{config.ONELEFT_URL}{path}", json=body, timeout=_TIMEOUT
    )
    # Their errors come back as JSON with a message — surface it instead of
    # a bare status code.
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400:
        raise RuntimeError(
            str(data.get("error") or f"HTTP {response.status_code}")[:200]
        )
    return data


def get_pending(force: bool = False) -> dict:
    """Their pending queue, cached briefly. Fail-soft shape:
    {configured, ok, count, items: [...], error?}."""
    base = {"configured": configured(), "ok": False, "count": 0, "items": []}
    if not configured():
        return base
    now = time.monotonic()
    if (not force and _pending_cache["data"] is not None
            and now - _pending_cache["at"] < _PENDING_TTL):
        return _pending_cache["data"]
    try:
        raw = _get("/pending")
        result = {
            **base,
            "ok": bool(raw.get("success")),
            "count": int(raw.get("count") or 0),
            "items": raw.get("items") or [],
        }
    except Exception as exc:  # noqa: BLE001 — bridge fails soft, always
        result = {**base, "error": str(exc)[:200]}
    _pending_cache["at"] = now
    _pending_cache["data"] = result
    return result


def invalidate_pending_cache() -> None:
    _pending_cache["data"] = None


def employee_for(operator: str | None) -> str:
    """The name their confirm endpoint will accept: the RFID operator when
    they're on the dashboard's employee list, else the configured default.
    (The true actor is always recorded in OUR receipt row.)"""
    name = (operator or "").strip().title()
    if name in VALID_EMPLOYEES:
        return name
    return config.ONELEFT_EMPLOYEE or "Steve"


def confirm(sku: str, employee: str) -> dict:
    return _post("/confirm", {"sku": sku, "employee": employee})


def bulk_confirm(skus: list[str], employee: str) -> dict:
    return _post("/bulk-confirm", {"skus": skus, "employee": employee})


def requeue(sku: str) -> dict:
    """Put one SKU back on their pending queue — the undo for a confirm.
    Their import endpoint looks the product up in Shopify itself."""
    return _post("/import-skus", {"csv_content": f"sku\n{sku}"})


# -------------------------------------------------------- evidence engine ---
def _naive_utc(dt: datetime | None) -> datetime | None:
    """DB timestamps come back naive-UTC (app-wide convention); their API
    sends +00:00-suffixed ISO. Compare everything as naive UTC."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _parse_detected(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return _naive_utc(datetime.fromisoformat(raw))
    except ValueError:
        return None


def _claimed_of(item: dict) -> int | None:
    """Shopify's current stock claim from their pending payload. Their
    endpoint attaches {available, committed, on_hand, pickups} — or the
    string '?' when its own live fetch failed."""
    stock = item.get("current_stock")
    if isinstance(stock, dict):
        for key in ("on_hand", "available"):
            value = stock.get(key)
            if isinstance(value, (int, float)):
                return int(value)
    return None


def build_board(session: Session, pending: list[dict]) -> list[dict]:
    """Join their pending queue against RFID evidence. Pure read — no
    writes to anything, theirs or ours."""
    rows: list[dict] = []
    sku_keys = {
        (i.get("sku") or "").strip().upper()
        for i in pending if (i.get("sku") or "").strip()
    }
    if not sku_keys:
        return rows

    # Every tag on file for a pending SKU (any age — an old tag seen by a
    # NEW sweep is evidence too).
    tags_by_sku: dict[str, list[RfidAssignment]] = {}
    for a in session.scalars(
        select(RfidAssignment).where(
            func.upper(RfidAssignment.sku).in_(sorted(sku_keys))
        )
    ):
        tags_by_sku.setdefault(a.sku.strip().upper(), []).append(a)

    detecteds = [
        d for d in (_parse_detected(i.get("detected_date")) for i in pending)
        if d is not None
    ]
    oldest = min(detecteds) if detecteds else None

    # EPC -> newest sweep that heard it, across every capture since the
    # oldest pending detection. One pass over the capture text, shared by
    # all items.
    epc_last_heard: dict[str, datetime] = {}
    if oldest is not None and tags_by_sku:
        for cap in session.scalars(
            select(EpcCapture).where(EpcCapture.created_at > oldest)
            .order_by(EpcCapture.id.desc()).limit(300)
        ):
            at = _naive_utc(cap.created_at)
            if at is None:
                continue
            for epc in (cap.epcs or "").split("\n"):
                epc = epc.strip().upper()
                if epc and (epc not in epc_last_heard
                            or epc_last_heard[epc] < at):
                    epc_last_heard[epc] = at

    # Batch counts per SKU. Audit-recorded bins are excluded — their rows
    # are copies of tags already on file, not a fresh physical count.
    batch_counts: dict[str, list[tuple[datetime, int, str]]] = {}
    for item, batch in session.execute(
        select(BatchItem, Batch)
        .join(Batch, Batch.id == BatchItem.batch_id)
        .where(
            func.upper(BatchItem.sku).in_(sorted(sku_keys)),
            Batch.status != "abandoned",
        )
    ):
        if batch.ui_step == "audit-complete":
            continue
        at = _naive_utc(batch.created_at)
        units = (item.qty_scanned
                 + item.case_count * (item.case_units or 0)
                 + item.tagged_before)
        if at is not None and units > 0:
            batch_counts.setdefault(item.sku.strip().upper(), []).append(
                (at, units, f"#{batch.id} ({batch.bin_name})")
            )

    # Bin fallback for items whose dashboard row has no bin recorded.
    map_bins: dict[str, str] = {}
    for sku, bin_name in session.execute(
        select(BinMapEntry.sku, BinMapEntry.bin).where(
            func.upper(BinMapEntry.sku).in_(sorted(sku_keys))
        )
    ):
        if sku and bin_name:
            map_bins.setdefault(sku.strip().upper(), bin_name)

    # A re-queue is an operator saying "no, walk this one". Without this,
    # the evidence that cleared it the first time would clear it again on
    # the very next auto pass — so a re-queued check stays pinned for a
    # human until evidence NEWER than the re-queue shows up.
    requeued_at: dict[str, datetime] = {}
    for oc in session.scalars(
        select(OneLeftCheck).where(
            func.upper(OneLeftCheck.sku).in_(sorted(sku_keys)),
            OneLeftCheck.action == "requeue",
            # == not .is_(): SQL Server has no boolean literal, and
            # .is_(True) renders as "ok IS 1" — a syntax error there.
            OneLeftCheck.ok == True,  # noqa: E712
        )
    ):
        at = _naive_utc(oc.created_at)
        key = oc.sku.strip().upper()
        if at is not None and (key not in requeued_at
                               or requeued_at[key] < at):
            requeued_at[key] = at

    for item in pending:
        sku = (item.get("sku") or "").strip()
        if not sku:
            continue
        key = sku.upper()
        detected = _parse_detected(item.get("detected_date"))
        claimed = _claimed_of(item)
        # Unknown claim: the queue only ever admits products at 1 on hand,
        # so "prove at least 1 exists" is the honest bar.
        claimed_eff = claimed if claimed is not None else 1
        tags = tags_by_sku.get(key, [])

        details: list[str] = []
        paired_units = 0
        paired_last = None
        seen_units = 0
        seen_last = None
        for a in tags:
            at = _naive_utc(a.assigned_at)
            units = a.case_units or 1
            if detected is not None and at is not None and at > detected:
                paired_units += units
                if paired_last is None or at > paired_last:
                    paired_last = at
            heard = epc_last_heard.get((a.rfid_id or "").strip().upper())
            if detected is not None and heard is not None and heard > detected:
                seen_units += units
                if seen_last is None or heard > seen_last:
                    seen_last = heard
        if paired_units:
            details.append(
                f"{paired_units} tag(s) paired since detection"
                + (f" (last {paired_last:%b %d %H:%M})" if paired_last else "")
            )
        if seen_units:
            details.append(
                f"{seen_units} tagged box(es) heard by a sweep since"
                + (f" (last {seen_last:%b %d %H:%M})" if seen_last else "")
            )
        batch_units = 0
        batch_last = None
        for at, units, label in batch_counts.get(key, []):
            if detected is not None and at > detected:
                if units > batch_units:
                    batch_units = units
                    batch_label = label
                if batch_last is None or at > batch_last:
                    batch_last = at
        if batch_units:
            details.append(
                f"{batch_units} unit(s) counted in batch {batch_label}"
            )

        evidence_units = max(paired_units, seen_units, batch_units)
        evidence_last = max(
            (t for t in (paired_last, seen_last, batch_last)
             if t is not None),
            default=None,
        )
        if claimed is not None and claimed <= 0:
            verdict = "discrepancy" if evidence_units > 0 else "zero-claim"
        elif evidence_units >= claimed_eff:
            verdict = "confirmable"
        else:
            verdict = "needs-walk"
        pin = requeued_at.get(key)
        if (verdict == "confirmable" and pin is not None
                and (evidence_last is None or evidence_last <= pin)):
            verdict = "requeued"
        # Old evidence can't clear a check: a tag paired weeks ago says
        # nothing about whether the box has sold since. Only a discovery
        # inside the freshness window counts as "answered".
        if verdict == "confirmable":
            cutoff = (datetime.now(timezone.utc).replace(tzinfo=None)
                      - timedelta(hours=config.ONELEFT_FRESH_HOURS))
            if evidence_last is None or evidence_last < cutoff:
                verdict = "stale-evidence"

        rows.append({
            "sku": sku,
            "product_title": item.get("product_title"),
            "variant_title": item.get("variant_title"),
            "vendor": item.get("vendor"),
            "bin": (item.get("stock_bin") or "").strip()
                   or map_bins.get(key) or "",
            "barcode": item.get("barcode"),
            "detected_date": item.get("detected_date"),
            "claimed": claimed,
            "tag_count": len(tags),
            "evidence_units": evidence_units,
            "evidence": details,
            "verdict": verdict,
        })
    return rows


# ------------------------------------------------------------- auto scans ---
def auto_enabled(session: Session) -> bool:
    row = session.get(AppSetting, AUTO_SETTING_KEY)
    return row is None or row.value != "off"


def scan_and_confirm(trigger: str, operator: str | None = None) -> dict:
    """One auto pass: read their queue, keep only checks the evidence
    fully answers, confirm those in one bulk call, and write a receipt
    row per SKU (History renders them). Returns what happened."""
    from app.database import get_engine  # late: avoids import cycles

    if not can_confirm():
        return {"ran": False, "reason": "mode"}
    with Session(get_engine()) as session:
        if not auto_enabled(session):
            return {"ran": False, "reason": "auto-off"}
        pending = get_pending()
        if not pending["ok"]:
            return {"ran": False, "reason": pending.get("error", "no data")}
        board = build_board(session, pending["items"])
        targets = [r for r in board if r["verdict"] == "confirmable"]
        if not targets:
            return {"ran": True, "confirmed": [], "trigger": trigger}
        employee = employee_for(operator)
        skus = [r["sku"] for r in targets]
        try:
            result = bulk_confirm(skus, employee)
            confirmed = set(result.get("confirmed_skus") or [])
            error = None
        except Exception as exc:  # noqa: BLE001 — record, never raise
            confirmed = set()
            error = str(exc)[:300]
        invalidate_pending_cache()
        for row in targets:
            session.add(OneLeftCheck(
                sku=row["sku"],
                product_title=(row.get("product_title") or "")[:255] or None,
                vendor=(row.get("vendor") or "")[:150] or None,
                claimed=row["claimed"],
                evidence_units=row["evidence_units"],
                evidence=("; ".join(row["evidence"])
                          + f" · trigger: {trigger}")[:500],
                action="auto",
                employee=employee,
                operator=(operator or "").strip()[:100] or None,
                ok=row["sku"] in confirmed,
                error=(None if row["sku"] in confirmed
                       else (error or "not on their pending list")[:300]),
            ))
        session.commit()
        done = [r["sku"] for r in targets if r["sku"] in confirmed]
        logger.info("1-left auto pass (%s): confirmed %s", trigger, done)
        return {"ran": True, "confirmed": done, "failed": error,
                "trigger": trigger}


def kick(trigger: str, operator: str | None = None) -> None:
    """Fire-and-forget: run an auto pass soon if one hasn't run recently.
    Called after stock-discovering actions (tag pairs, sweeps, batch
    completions). Never blocks or raises into the caller."""
    global _last_scan
    if not can_confirm():
        return
    now = time.monotonic()
    with _scan_lock:
        if now - _last_scan < _SCAN_EVERY:
            return
        _last_scan = now

    def _run():
        try:
            scan_and_confirm(trigger, operator)
        except Exception:  # noqa: BLE001 — background, log only
            logger.exception("1-left auto pass failed (%s)", trigger)

    threading.Thread(target=_run, daemon=True).start()
