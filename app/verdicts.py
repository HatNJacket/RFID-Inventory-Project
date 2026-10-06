"""Audit verdicts (Nick, 2026-10-06): the numbers, the ladder, the copy.

The plan of record is the "Expected + verdict ladder spec" in
ROADMAP.md. In short: the LAST LOGGED COUNT is the anchor and the
CURRENT SWEEP is the evidence; Shopify is a reference line that must
agree, never the thing the verdict is computed from. Per product on a
shelf:

    Expected now = Last count - Sold since + Received since
    Heard here   = tags recorded on this shelf that answered this sweep

and the ladder turns the difference into ONE summary line for the card
("3 to resolve: retired tag answered · 1 shipped · 1 unavailable") plus
a list of problems, each with its title, description and action, for
the Resolve window.

Two halves, deliberately split:
- rack_model(session, loc): everything the ladder needs for every
  product of a bin or rack, in ONE fetch. The gun downloads this once
  and runs the ladder locally on every sweep (instant re-sweeps, no
  server round trip); the web calls the endpoint that runs both.
- judge(product, heard, now): the pure ladder. No database, no clock
  of its own - the same function the gun ports line for line.

Never writes anything. Resolutions are the existing endpoints.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Batch, BatchItem, BinAudit, BinMapEntry, NonTaggable, OrderReceipt,
    RetiredTag, RfidAssignment, RfidIncompatible, SoldRecord,
)

# The ladder, worst first. Colour drives the card edge; rows stack.
RED, YELLOW, GREEN = "red", "yellow", "green"


def _up(s) -> str:
    return (s or "").strip().upper()


def _utc(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt):
    dt = _utc(dt)
    return dt.isoformat() if dt else None


def _day(dt) -> str:
    """'Sep 30' for copy; '' when unknown."""
    dt = _utc(dt)
    return f"{dt.strftime('%b')} {dt.day}" if dt and hasattr(dt, "strftime") else ""


def _when(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return _utc(datetime.fromisoformat(iso.replace("Z", "+00:00")))
    except ValueError:
        return None


def _tail(epc: str) -> str:
    return "…" + (epc or "")[-6:]


# ---------------------------------------------------------------- model --

def rack_model(session: Session, loc: str) -> dict:
    """One fetch for a bin ("F9-2") or a whole rack ("F9"): the covered
    bins and, per product, every number and record the ladder reads.
    Shopify figures come from the bin-map snapshot (on-hand INCLUDING
    the unavailable units, the way Shopify counts) with their age; the
    caller may true them up first."""
    loc = (loc or "").strip()
    bin_keys = [loc.lower()]
    rack = False
    if loc and "-" not in loc:
        prefixed = sorted({
            (b or "").strip().lower()
            for (b,) in session.execute(
                select(BinMapEntry.bin).where(
                    func.lower(BinMapEntry.bin).like(loc.lower() + "-%")
                )
            ) if b
        })
        if prefixed:
            rack, bin_keys = True, prefixed
    bin_set = set(bin_keys)
    rows = session.scalars(
        select(BinMapEntry)
        .where(func.lower(BinMapEntry.bin).in_(bin_keys))
        .order_by(BinMapEntry.product_title)
    ).all()
    skus = sorted({_up(e.sku) for e in rows if e.sku})
    if not skus:
        return {"loc": loc, "rack": rack, "bins": bin_keys, "products": [],
                "fetched_at": _iso(datetime.now(timezone.utc))}

    # Anchors: the newest Log per bin, its per-SKU counts and time.
    anchors: dict[str, BinAudit] = {}
    for a in session.scalars(
        select(BinAudit).where(func.lower(BinAudit.bin).in_(bin_keys))
        .order_by(BinAudit.audited_at, BinAudit.id)
    ):
        k = (a.bin or "").strip().lower()
        prev = anchors.get(k)
        if prev is None or (a.audited_at and prev.audited_at
                            and a.audited_at > prev.audited_at):
            anchors[k] = a
    # A bin never logged anchors on its batch tagging (completed_at).
    batch_done: dict[str, datetime] = {}
    for b in session.scalars(
        select(Batch).where(
            func.lower(Batch.bin_name).in_(bin_keys), Batch.status == "done",
            Batch.parent_batch_id.is_(None),
        )
    ):
        k = (b.bin_name or "").strip().lower()
        t = _utc(b.completed_at)
        if t and (k not in batch_done or t > batch_done[k]):
            batch_done[k] = t

    tags_by_sku: dict[str, list[RfidAssignment]] = {}
    for t in session.scalars(
        select(RfidAssignment).where(
            func.upper(RfidAssignment.sku).in_(skus)
        )
    ):
        tags_by_sku.setdefault(_up(t.sku), []).append(t)
    retired_by_sku: dict[str, list[RetiredTag]] = {}
    for r in session.scalars(
        select(RetiredTag).where(func.upper(RetiredTag.sku).in_(skus))
    ):
        retired_by_sku.setdefault(_up(r.sku), []).append(r)
    sales_by_sku: dict[str, list[SoldRecord]] = {}
    for s in session.scalars(
        select(SoldRecord).where(func.upper(SoldRecord.sku).in_(skus))
        .order_by(SoldRecord.fulfilled_at, SoldRecord.id)
    ):
        sales_by_sku.setdefault(_up(s.sku), []).append(s)
    # Receipts: receiving batches that have a stock-order receipt record
    # (Nick: no record, no claim), with their units and pairings.
    receipts_by_sku: dict[str, list[dict]] = {}
    rec_batches = {
        r.batch_id: r for r in session.scalars(select(OrderReceipt))
    }
    if rec_batches:
        for b in session.scalars(
            select(Batch).where(
                Batch.id.in_(list(rec_batches)), Batch.status == "done"
            )
        ):
            rec = rec_batches[b.id]
            for it in session.scalars(
                select(BatchItem).where(BatchItem.batch_id == b.id)
            ):
                k = _up(it.sku)
                if k not in skus:
                    continue
                units = (it.qty_scanned or 0) + (
                    (it.case_count or 0) * (it.case_units or 1)
                )
                receipts_by_sku.setdefault(k, []).append({
                    "stock_order": rec.stock_order_id,
                    "reference": rec.reference,
                    "when": _iso(b.completed_at or rec.printed_at),
                    "units": units,
                    "unpaired": max(0, units - (it.paired_count or 0)),
                })
    noscan = {_up(s) for s in session.scalars(select(RfidIncompatible.sku))}
    nontag = {_up(s) for s in session.scalars(select(NonTaggable.sku))}

    merged: dict[str, dict] = {}
    order: list[str] = []
    for e in rows:
        k = _up(e.sku)
        if not k:
            continue
        g = merged.get(k)
        if g is None:
            g = {"entry": e, "bins": [], "qty": None, "unavail": 0}
            merged[k] = g
            order.append(k)
        if e.qty is not None:
            g["qty"] = (g["qty"] or 0) + e.qty
        g["unavail"] += e.unavailable or 0
        bk = (e.bin or "").strip().lower()
        if bk and bk not in g["bins"]:
            g["bins"].append(bk)

    products = []
    for k in order:
        g, e = merged[k], merged[k]["entry"]
        tags = tags_by_sku.get(k, [])
        here = [t for t in tags
                if (t.bin_location or "").strip().lower() in bin_set]
        # Last count: sum of the newest Log per covered bin; a bin never
        # logged contributes its tags recorded here (so Expected now
        # reduces to "tags here - sold since + received since" there,
        # which the first Log then anchors properly).
        last_qty, last_at, src = 0, None, "log"
        for bk in g["bins"]:
            a = anchors.get(bk)
            if a is not None:
                last_qty += a.baseline_map().get(k, 0)
                t = _utc(a.audited_at)
                if t and (last_at is None or t < last_at):
                    last_at = t
            else:
                src = "batch"
                last_qty += sum((t.case_units or 1) for t in here
                                if (t.bin_location or "").strip().lower() == bk)
                t = batch_done.get(bk)
                if t and (last_at is None or t < last_at):
                    last_at = t
        if last_at is None and here:
            last_at = min((_utc(t.assigned_at) for t in here
                           if t.assigned_at), default=None)
        sold = []
        for s in sales_by_sku.get(k, []):
            left = max(0, (s.quantity or 0) - (s.retired or 0))
            f = _utc(s.fulfilled_at)
            if not left or f is None:
                continue
            if last_at is not None and f <= last_at:
                continue
            sold.append({"order": s.order_name or s.order_id, "when": _iso(f),
                         "units": left, "row": s.id})
        received = [
            r for r in receipts_by_sku.get(k, [])
            if last_at is None or (_when(r["when"]) or last_at) > last_at
        ]
        snap_qty = g["qty"]
        products.append({
            "sku": e.sku, "title": e.product_title,
            "variant": e.variant_title, "image": e.image_url,
            "bins": g["bins"],
            "last_count": {"qty": last_qty, "at": _iso(last_at), "source": src},
            "sold_since": sold,
            "received_since": received,
            "tags": [
                {
                    "epc": t.rfid_id, "bin": (t.bin_location or "").strip(),
                    "here": (t.bin_location or "").strip().lower() in bin_set,
                    "units": t.case_units or 1,
                    "last_heard_at": _iso(t.last_heard_at or t.assigned_at),
                    "last_heard_ctx": getattr(t, "last_heard_ctx", None),
                }
                for t in tags
            ],
            "retired": [
                {"epc": r.rfid_id, "kind": r.kind, "at": _iso(r.retired_at),
                 "units": r.case_units or 1}
                for r in retired_by_sku.get(k, [])
            ],
            # Shopify as Shopify counts it: on-hand includes unavailable.
            "shopify": {
                "on_hand": (None if snap_qty is None
                            else snap_qty + (g["unavail"] or 0)),
                "unavailable": g["unavail"] or 0,
                "available": None, "committed": None,
                "staff_comment": None,
                "as_of": _iso(e.updated_at),
            },
            "flags": {"incompatible": k in noscan, "non_taggable": k in nontag},
        })
    return {"loc": loc, "rack": rack, "bins": bin_keys, "products": products,
            "fetched_at": _iso(datetime.now(timezone.utc))}


# --------------------------------------------------------------- ladder --

def _n(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def judge(p: dict, heard: set[str], now: datetime | None = None) -> dict:
    """The ladder for one product. `heard` = EPCs this sweep answered
    (upper-cased). Returns numbers, problems (ladder order), colour and
    the card's summary line. Pure: port this to the gun as is."""
    now = _utc(now) or datetime.now(timezone.utc)
    heard = {_up(e) for e in heard}
    flags = p.get("flags") or {}
    shop = p.get("shopify") or {}
    last = p.get("last_count") or {}
    last_at = _when(last.get("at"))
    tags = p.get("tags") or []
    here = [t for t in tags if t.get("here")]
    heard_here = [t for t in here if _up(t["epc"]) in heard]
    heard_elsewhere = [t for t in tags if not t.get("here")
                       and _up(t["epc"]) in heard]
    silent = sorted(
        (t for t in here if _up(t["epc"]) not in heard),
        key=lambda t: _when(t.get("last_heard_at")) or datetime.min.replace(
            tzinfo=timezone.utc),
    )
    ghosts = [r for r in (p.get("retired") or []) if _up(r["epc"]) in heard]
    sold = list(p.get("sold_since") or [])
    received = list(p.get("received_since") or [])
    sold_units = sum(s.get("units", 0) for s in sold)
    recv_units = sum(r.get("units", 0) for r in received)
    recv_unpaired = sum(r.get("unpaired", 0) for r in received)
    expected = max(0, int(last.get("qty") or 0) - sold_units + recv_units)
    heard_units = sum(t.get("units", 1) for t in heard_here)
    ghost_units = sum(g.get("units", 1) for g in ghosts)
    on_hand = shop.get("on_hand")
    unavail = int(shop.get("unavailable") or 0)

    out = {
        "sku": p.get("sku"),
        "expected": expected, "heard": heard_units,
        "tags_here": sum(t.get("units", 1) for t in here),
        "shopify": shop,
        "heard_elsewhere": [t["epc"] for t in heard_elsewhere],
        "problems": [], "colour": GREEN, "summary": "", "resolve_all": [],
    }

    # Row 10: hand-count products skip the tag arithmetic entirely.
    if flags.get("non_taggable") or flags.get("incompatible"):
        out["mode"] = "hand"
        out["colour"] = YELLOW
        out["summary"] = "count by hand"
        out["problems"].append({
            "kind": "hand", "colour": YELLOW, "fragment": "count by hand",
            "title": "Count by hand",
            "text": (f"{'Non-taggable' if flags.get('non_taggable') else 'RFID-incompatible'}: "
                     f"{on_hand if on_hand is not None else '?'} on hand in Shopify"
                     + (f", {heard_units} tags heard." if flags.get("incompatible") else ".")),
            "action": "Confirm count", "n": on_hand or 0,
        })
        return out

    probs = out["problems"]
    # 1. Retired tags answering.
    if ghosts:
        g0 = ghosts[0]
        probs.append({
            "kind": "ghost", "colour": RED, "n": len(ghosts),
            "epcs": [g["epc"] for g in ghosts],
            "fragment": "retired tag answered" if len(ghosts) == 1
            else f"{len(ghosts)} retired tags answered",
            "title": "Retired tag answered" if len(ghosts) == 1
            else f"{len(ghosts)} retired tags answered",
            "text": (f"{_tail(g0['epc'])} was retired as {g0.get('kind') or 'sold'}"
                     f"{(' on ' + _day(_when(g0.get('at')))) if g0.get('at') else ''}, "
                     "but it answered this sweep. The box never left."),
            "action": "Un-retire",
        })
    # 2. More heard than expected.
    if heard_units > expected:
        probs.append({
            "kind": "over", "colour": YELLOW, "heard": heard_units,
            "expected": expected,
            "fragment": f"heard {heard_units}, expected {expected}",
            "title": "More heard than expected",
            "text": (f"Expected {expected} from the last count, {heard_units} "
                     f"{'tag' if heard_units == 1 else 'tags'} answered here. "
                     "Check for a tag on the wrong product or a box that was "
                     "never counted."),
            "action": "Count and set", "n": heard_units,
        })
    # 3. Silent tags paired with sales since the last count.
    remaining = list(silent)
    shipped = []
    pool = sorted(sold, key=lambda s: s.get("when") or "")
    for t in list(remaining):
        lh = _when(t.get("last_heard_at"))
        for s in pool:
            if s.get("units", 0) <= 0:
                continue
            sw = _when(s.get("when"))
            if lh is None or sw is None or lh <= sw:
                s["units"] -= 1
                shipped.append((t, s))
                remaining.remove(t)
                break
    if shipped:
        t0, s0 = shipped[0]
        orders = sorted({s.get("order") or "?" for _, s in shipped})
        probs.append({
            "kind": "shipped", "colour": GREEN, "n": len(shipped),
            "tags": [{"epc": t["epc"], "order": s.get("order"),
                      "when": s.get("when"), "row": s.get("row")}
                     for t, s in shipped],
            "fragment": _n(len(shipped), "shipped", "shipped"),
            "title": _n(len(shipped), "silent tag, shipped", "silent tags, shipped"),
            "text": (f"{_tail(t0['epc'])} was last heard {_day(_when(t0.get('last_heard_at')))}"
                     f"{' during the ' + t0['last_heard_ctx'] if t0.get('last_heard_ctx') else ''}. "
                     f"Order {'#' + str(s0.get('order')) if s0.get('order') else '?'} shipped on "
                     f"{_day(_when(s0.get('when')))}."
                     + (f" Also {', '.join('#' + o for o in orders[1:])}." if len(orders) > 1 else "")),
            "action": "Mark sold", "confirm": False,
        })
    # 4. Unavailable covers the rest.
    cover = min(len(remaining), unavail) if remaining else 0
    if cover:
        covered, remaining = remaining[:cover], remaining[cover:]
        t0 = covered[0]
        comment = shop.get("staff_comment")
        probs.append({
            "kind": "unavailable", "colour": YELLOW, "n": cover,
            "epcs": [t["epc"] for t in covered],
            "fragment": _n(cover, "unavailable", "unavailable"),
            "title": _n(cover, "silent tag, set as unavailable",
                        "silent tags, set as unavailable"),
            "text": (f"{_tail(t0['epc'])} was last heard {_day(_when(t0.get('last_heard_at')))}"
                     f"{' during the ' + t0['last_heard_ctx'] if t0.get('last_heard_ctx') else ''}. "
                     f"Shopify holds {_n(unavail, 'unit', 'units')} as unavailable."
                     + (f' Staff comment: "{comment}".' if comment else "")),
            "action": "Clear unavailable", "comment": comment,
        })
    # 5. Received, not shelved (only with a stock-order receipt record).
    if recv_unpaired > 0:
        r0 = received[0]
        probs.append({
            "kind": "received", "colour": YELLOW, "n": recv_unpaired,
            "fragment": f"{recv_unpaired} received, not shelved",
            "title": "Received, not shelved",
            "text": (f"Stock order #{r0.get('stock_order') or '?'} received "
                     f"{_day(_when(r0.get('when')))}: {_n(recv_units, 'unit', 'units')}, "
                     f"{recv_unpaired} with no label paired yet."),
            "action": f"Print {_n(recv_unpaired, 'label', 'labels')}",
        })
        # Received units never had tags: they explain Expected being
        # above the tags on file, not a silent tag's silence.
    # 6. Silent tags heard somewhere else since the last count.
    elsewhere = []
    for t in list(remaining):
        lh = _when(t.get("last_heard_at"))
        ctx = t.get("last_heard_ctx") or ""
        if lh and (last_at is None or lh > last_at) and ctx and not any(
                b.upper() in ctx.upper() for b in p.get("bins") or []):
            elsewhere.append(t)
            remaining.remove(t)
    if elsewhere:
        t0 = elsewhere[0]
        probs.append({
            "kind": "elsewhere", "colour": YELLOW, "n": len(elsewhere),
            "epcs": [t["epc"] for t in elsewhere],
            "fragment": _n(len(elsewhere), "heard elsewhere", "heard elsewhere"),
            "title": _n(len(elsewhere), "silent tag, heard elsewhere",
                        "silent tags, heard elsewhere"),
            "text": (f"{_tail(t0['epc'])} was last heard "
                     f"{_day(_when(t0.get('last_heard_at')))} during the "
                     f"{t0.get('last_heard_ctx')}."),
            "action": "Locate",
        })
    # 7. Nothing explains it.
    if remaining:
        t0 = remaining[0]
        since = _day(_when(t0.get("last_heard_at"))) or _day(last_at)
        probs.append({
            "kind": "missing", "colour": RED, "n": len(remaining),
            "epcs": [t["epc"] for t in remaining],
            "fragment": _n(len(remaining), "missing", "missing"),
            "title": f"{_n(len(remaining), 'tag', 'tags')} missing since {since}",
            "text": (f"{_tail(t0['epc'])} was last heard {since}"
                     f"{' during the ' + t0['last_heard_ctx'] if t0.get('last_heard_ctx') else ''}. "
                     "No sale, no receipt, not heard anywhere since."),
            "action": "Count and set", "n_preset": heard_units + ghost_units,
        })
    # 8. Shopify disagrees with the settled shelf (after everything above).
    settled = heard_units + ghost_units
    if on_hand is not None and on_hand != settled:
        avail = shop.get("available")
        probs.append({
            "kind": "shopify", "colour": YELLOW, "shelf": settled,
            "on_hand": on_hand,
            "fragment": f"shelf {settled}, Shopify {on_hand}",
            "title": "Shopify disagrees",
            "text": (f"The shelf is settled at {settled}. Shopify shows on-hand "
                     f"{on_hand}"
                     + (f" (available {avail}, unavailable {unavail})"
                        if avail is not None else
                        (f" ({unavail} unavailable)" if unavail else ""))
                     + "."),
            "action": f"Set on-hand to {settled}", "n": settled,
        })
        if avail is not None and avail < 0:
            probs.append({
                "kind": "oversold", "colour": YELLOW, "fragment": "oversold",
                "title": "Oversold in Shopify",
                "text": (f"Available is {avail}: Shopify has sold more than it "
                         "holds. Setting on-hand will clear it."),
                "action": None,
            })

    rank = {RED: 0, YELLOW: 1, GREEN: 2}
    colour = GREEN
    for pr in probs:
        if rank[pr["colour"]] < rank[colour]:
            colour = pr["colour"]
    out["colour"] = colour
    actionable = [pr for pr in probs if pr.get("action")]
    if actionable:
        out["summary"] = (f"{len(actionable)} to resolve: "
                          + " · ".join(pr["fragment"] for pr in actionable))
        out["resolve_all"] = [pr["action"] for pr in actionable]
    else:
        out["summary"] = "Match"
    return out


def judge_all(model: dict, heard, now: datetime | None = None) -> dict:
    """The ladder over a whole rack model. Adds the roll-up the list
    header shows (products / all match / to resolve)."""
    heard = {_up(e) for e in heard}
    items = [judge(p, heard, now) for p in model.get("products") or []]
    return {
        "loc": model.get("loc"), "bins": model.get("bins"),
        "items": items,
        "count": len(items),
        "all_match": sum(1 for i in items if i["colour"] == GREEN),
        "to_resolve": sum(1 for i in items if i["colour"] != GREEN
                          and i.get("mode") != "hand"),
        "by_hand": sum(1 for i in items if i.get("mode") == "hand"),
    }
