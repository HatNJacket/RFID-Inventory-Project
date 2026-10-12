"""One batch per Sort a shipment (Steve, 2026-10-07).

A Svbony delivery sorted on the web became two receiving batches (SO
965, SO 976) plus a loose run of no-order labels with no batch at all -
three places to pair from, and the no-order boxes never reached Shopify
on-hand. Now a sort owns ONE batch:

  POST /api/sorter/batch            {batch_id?, orders: ["SO 965", ...],
                                     vendor, worker}
      Claims the pile's sort batch (creates it, or adds new orders to its
      name). It is an ordinary planner receiving batch named
      "TC-Planner · SO 965, SO 976 · Svbony", so each order's page in the
      planner (Save, Print labels, per-line Print) books and prints into
      it through /api/receiving/sync - the intake picks the newest open
      batch naming the SO. bin_name SORT marks it.

  POST /api/sorter/batch/{id}/no-order {items: [{sku, quantity}], worker}
      Books the boxes no open order explains on the same batch (so_units
      "") and queues their labels.

  GET  /api/sorter/batch/{id}/finish
  POST /api/sorter/batch/{id}/finish {confirmed, worker}
      The gun's confirm (Steve: each paired no-order box is +1, but the
      on-hand goes to Shopify only when the task is confirmed): one
      raise per product for its paired no-order boxes, each a History
      "on-hand" row with the usual Undo, gated by SHOPIFY_WRITE_MODE.
      Then the batch closes if every box is paired. A sort batch never
      closes by itself - the next order's print must still find it.

Each row records units per order (BatchItem.so_units), so the planner
sync counts only its own order's share of a shared row.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import main as M
from app.auth import actor_name
from app.models import Batch, BatchItem, BinMapEntry

app = M.app

SORT_BIN = "SORT"
_SO_RE = re.compile(r"^SO\s*(\d+)$", re.I)
# The History title an on-hand raise from a sort carries; the undo
# reads it back to free the units for another raise.
_RAISE_TAG = re.compile(r"\(no order, sort batch #(\d+)\)$")


def is_sort_batch(batch: Batch | None) -> bool:
    return bool(batch is not None and batch.kind == "receiving"
                and batch.bin_name == SORT_BIN)


def _so_list(batch: Batch) -> tuple[list[str], str]:
    """(["SO 965", "SO 976"], "Svbony") from the batch name."""
    parts = [p.strip() for p in (batch.created_by or "").split("·")]
    sos = [s.strip().upper() for s in (parts[1] if len(parts) > 1 else "").split(",")
           if _SO_RE.match(s.strip())]
    vendor = parts[2] if len(parts) > 2 else ""
    return sos, vendor


def _name(sos: list[str], vendor: str) -> str:
    return " · ".join(x for x in (
        "TC-Planner", ", ".join(sos) or "No order", vendor) if x)


def _open_sort(session: Session, batch_id: int) -> Batch:
    batch = session.get(Batch, batch_id)
    if not is_sort_batch(batch):
        raise HTTPException(404, "No sort batch with that number.")
    if batch.status in ("done", "abandoned"):
        raise HTTPException(409, f"Sort batch #{batch.id} is already finished.")
    return batch


class SortBatchIn(BaseModel):
    batch_id: int | None = None
    orders: list[str] = Field(default_factory=list, max_length=20)
    vendor: str | None = Field(default=None, max_length=60)
    worker: str | None = Field(default=None, max_length=100)


@app.post("/api/sorter/batch", dependencies=[Depends(M.require_user)])
def claim_sort_batch(payload: SortBatchIn, request: Request,
                     session: Session = Depends(M.get_session)):
    """The pile's one batch: reuse it (adding any new orders to its
    name), or start it."""
    want = []
    for o in payload.orders:
        m = _SO_RE.match((o or "").strip())
        if not m:
            raise HTTPException(422, f"'{o}' isn't a stock order number.")
        so = f"SO {int(m.group(1))}"
        if so not in want:
            want.append(so)
    by = actor_name(request, payload.worker)
    batch = None
    if payload.batch_id:
        cand = session.get(Batch, payload.batch_id)
        if is_sort_batch(cand) and cand.status not in ("done", "abandoned"):
            batch = cand
    if batch is not None:
        sos, vendor = _so_list(batch)
        added = [s for s in want if s not in sos]
        if added:
            name = _name(sos + added, vendor or (payload.vendor or "").strip())
            if len(name) > 100:
                raise HTTPException(
                    422, "Too many stock orders for one sort batch - finish "
                         "this one first.")
            batch.created_by = name
            M._log_change(session, field="sort-batch", title=name,
                          new=f"Sort batch #{batch.id} now takes "
                              + ", ".join(added), by=by)
            session.commit()
        return {"batch": batch.as_dict(), "created": False}
    name = _name(want, (payload.vendor or "").strip())
    if len(name) > 100:
        raise HTTPException(422, "Too many stock orders for one sort batch.")
    batch = Batch(bin_name=SORT_BIN, kind="receiving", created_by=name,
                  status="pairing")
    session.add(batch)
    session.flush()
    M._log_change(session, field="sort-batch", title=name,
                  new=f"Sort batch #{batch.id} started", by=by)
    session.commit()
    session.refresh(batch)
    return {"batch": batch.as_dict(), "created": True}


class NoOrderIn(BaseModel):
    items: list[M.ReceivingPrintItemIn] = Field(min_length=1, max_length=200)
    worker: str | None = Field(default=None, max_length=100)


@app.post("/api/sorter/batch/{batch_id}/no-order",
          dependencies=[Depends(M.require_user)])
def print_no_order(batch_id: int, payload: NoOrderIn, request: Request,
                   session: Session = Depends(M.get_session)):
    """Boxes no open order explains: booked on the sort batch as
    no-order units and their labels queued there, one strip."""
    batch = _open_sort(session, batch_id)
    by = actor_name(request, payload.worker)
    out = M._receiving_intake(
        session, M.ReceivingPrintsIn(items=payload.items, requested_by=by),
        queue_labels=False, into=batch, so_key="")
    session.flush()
    skus = {M._up(a["sku"]) for a in out["added"]}
    jobs, no_bin, _ = M._build_receiving_label_jobs(session, batch, by or batch.created_by)
    keep = [j for j in jobs if M._up(j.sku) in skus]
    session.add_all(keep)
    titles = {i.product_title for i in M._batch_items(session, batch.id)
              if M._up(i.sku) in skus}
    no_bin = [t for t in no_bin if t in titles]
    for a in out["added"]:
        M._log_change(
            session, field="sorter-leftover", sku=a["sku"], title=a["sku"],
            new=(f"{M._count(a['quantity'], 'label', 'labels')} on sort batch "
                 f"#{batch.id}, no open stock order"), by=by)
    session.commit()
    return {
        "batch": batch.as_dict(),
        "queued": len(keep),
        "added": out["added"],
        "skipped_unknown": out["skipped_unknown"],
        "skipped_non_taggable": out["skipped_non_taggable"],
        "skipped_no_bin": no_bin,
        "message": (
            f"{M._count(len(keep), 'label', 'labels')} queued on sort batch "
            f"#{batch.id}"
            + (f"; held for a bin: {', '.join(no_bin[:4])}" if no_bin else "")
            + (f"; {M._count(len(out['skipped_unknown']), 'unknown SKU', 'unknown SKUs')}"
               if out["skipped_unknown"] else "")
            + "."),
    }


def no_order_due(row: BatchItem) -> dict | None:
    """A row's no-order boxes that are paired and not yet raised. Paired
    boxes fill the orders' share first, so a half-paired row never
    raises on-hand for boxes still waiting."""
    n = row.no_order_units()
    if n <= 0 or not row.sku or row.skip_reason:
        return None
    slots = max(1, M._item_box_slots(row))
    total = (row.expected_qty or 0) // slots
    paired_units = (row.paired_count or 0) // slots
    paired_no = min(n, max(0, paired_units - max(0, total - n)))
    return {
        "item_id": row.id,
        "sku": row.sku,
        "title": row.product_title,
        "no_order": n,
        "paired": paired_no,
        "raised": row.onhand_added or 0,
        "due": max(0, paired_no - (row.onhand_added or 0)),
    }


def _untagged(session: Session, batch: Batch) -> int:
    left = 0
    for i in M._batch_items(session, batch.id):
        if not i.resolved or i.skip_reason or i.skipped or i.kind == "bundle":
            continue
        left += max(0, (i.qty_scanned or 0) + (i.case_count or 0)
                    - (i.paired_count or 0))
    return left


@app.get("/api/sorter/batch/{batch_id}/finish",
         dependencies=[Depends(M.require_user)])
def finish_preview(batch_id: int, session: Session = Depends(M.get_session)):
    """What CONFIRM will do: each product's on-hand raise (live Shopify
    number now and after) and whether the batch closes."""
    batch = session.get(Batch, batch_id)
    if not is_sort_batch(batch):
        raise HTTPException(404, "No sort batch with that number.")
    raises = []
    for row in M._batch_items(session, batch.id):
        d = no_order_due(row)
        if not d or d["due"] <= 0:
            continue
        now = None
        try:
            if not M.config.check_shopify_env():
                now = M.shopify.get_on_hand(row.sku)
        except Exception:  # noqa: BLE001 - the preview still answers
            now = None
        if now is None:
            snap = session.execute(
                select(BinMapEntry.qty).where(BinMapEntry.sku == row.sku)
            ).scalars().all()
            now = sum(q or 0 for q in snap) if snap else None
        d["on_hand_now"] = now
        d["on_hand_after"] = None if now is None else now + d["due"]
        raises.append(d)
    untagged = _untagged(session, batch)
    sos, vendor = _so_list(batch)
    return {
        "batch": batch.as_dict(),
        "orders": sos,
        "vendor": vendor,
        "raises": raises,
        "untagged": untagged,
        "closes": untagged == 0,
        "writes_enabled": M.shopify_write_enabled("sort_onhand"),
    }


class FinishIn(BaseModel):
    confirmed: bool = False
    worker: str | None = Field(default=None, max_length=100)


@app.post("/api/sorter/batch/{batch_id}/finish",
          dependencies=[Depends(M.require_user)])
def finish_sort(batch_id: int, payload: FinishIn, request: Request,
                session: Session = Depends(M.get_session)):
    """The operator confirmed: raise Shopify on-hand once per product for
    its paired no-order boxes (History + Undo), then close the batch when
    every box is paired. Never lowers anything."""
    batch = _open_sort(session, batch_id)
    by = actor_name(request, payload.worker)
    rows = [(r, no_order_due(r)) for r in M._batch_items(session, batch.id)]
    rows = [(r, d) for r, d in rows if d and d["due"] > 0]
    raised, failed = [], []
    if rows:
        M.require_shopify_write("sort_onhand")
        M._require_shopify_env()
        if not payload.confirmed:
            raise HTTPException(
                409, "This raises Shopify on-hand - confirm it first.")
        for row, d in rows:
            try:
                live = M.shopify.get_on_hand(row.sku)
                if live is None:
                    raise RuntimeError("no Shopify product for this SKU")
                new = live + d["due"]
                before = M.shopify.set_on_hand(row.sku, new)
            except Exception as error:  # noqa: BLE001 - the rest still go
                failed.append({"sku": row.sku, "error": str(error)[:160]})
                continue
            M._log_change(
                session, field="on-hand", sku=row.sku,
                title=f"{row.product_title or row.sku} "
                      f"(no order, sort batch #{batch.id})",
                variant_id=row.shopify_variant_id,
                old=str(before), new=str(new), by=by)
            row.onhand_added = (row.onhand_added or 0) + d["due"]
            M._refresh_binmap_onhand(session, row.sku, new)
            # Each write is its own Shopify change - record it now so a
            # later failure can't lose the History row (or the undo).
            session.commit()
            raised.append({"sku": row.sku, "before": before, "after": new,
                           "added": d["due"]})
    closed = False
    if not failed and _untagged(session, batch) == 0:
        batch.status = "done"
        batch.completed_at = datetime.now(timezone.utc)
        M._audit_q_invalidate()
        closed = True
    session.commit()
    msg = []
    if raised:
        msg.append("On-hand raised: " + ", ".join(
            f"{r['sku']} {r['before']} → {r['after']}" for r in raised))
    if failed:
        msg.append("Not raised: " + ", ".join(
            f"{f['sku']} ({f['error']})" for f in failed))
    msg.append(f"Sort batch #{batch.id} finished." if closed
               else f"Sort batch #{batch.id} stays open"
               + (" - untagged boxes left." if not failed else " - try again."))
    return {"raised": raised, "failed": failed, "closed": closed,
            "message": " ".join(msg)}


def undo_hook(session: Session, change) -> None:
    """An on-hand undo of a sort raise frees those units, so the sort can
    raise them again if the undo was a mistake."""
    m = _RAISE_TAG.search(change.product_title or "")
    if not m or not change.sku:
        return
    try:
        units = int(change.new_barcode or 0) - int(change.old_barcode or 0)
    except ValueError:
        return
    for row in M._batch_items(session, int(m.group(1))):
        if M._up(row.sku) == M._up(change.sku):
            row.onhand_added = max(0, (row.onhand_added or 0) - max(0, units))
            break
