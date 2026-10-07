"""Box photos collector (Steve, 2026-10-07; plan in ROADMAP "Camera SKU
reader on the C72").

Svbony boxes carry no barcode, only the SKU in plain text. The gun's
developer-mode camera photographs each box; this module stores the
photo, has Azure AI Vision read it, matches what it read against our
SKUs (the open batch's products first) and files the photo into a SKU
folder: "auto" when sure, "ask" with the top guesses when not, "none"
when nothing looked like a SKU. Auto filings only count as training
labels once a person confirms them.

When the shipment is done the gun sends its confirmed boxes to the web
sorter (Batch tab > Sort a shipment) as a hand-off, because Svbony
rarely ships what the stock order says.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app import auth as _auth
from app import config
from app.auth import require_user
from app.database import get_session
from app.models import (BarcodeChange, BatchItem, BinMapEntry, Batch,
                        BoxPhoto, BoxPhotoHandoff)

log = logging.getLogger("rfid.boxphotos")

router = APIRouter(prefix="/api/boxphotos", dependencies=[Depends(require_user)])

MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_THUMB_BYTES = 300 * 1024


def _now() -> datetime:
    return datetime.now(timezone.utc)


def photo_dir() -> Path:
    if config.BOX_PHOTO_DIR:
        root = Path(config.BOX_PHOTO_DIR)
    elif Path("/home/site").exists():
        # App Service: /home survives restarts and deploys.
        root = Path("/home/data/boxphotos")
    else:
        root = Path(tempfile.gettempdir()) / "rfid_boxphotos"
    root.mkdir(parents=True, exist_ok=True)
    return root


# ---- reading the box --------------------------------------------------------

def azure_read(image: bytes) -> tuple[list[str], str | None]:
    """Azure AI Vision Read 3.2: the text lines on the photo, top to
    bottom. Returns (lines, error); never raises."""
    if not (config.VISION_ENDPOINT and config.VISION_KEY):
        return [], "The text reader isn't set up on the server."
    headers = {"Ocp-Apim-Subscription-Key": config.VISION_KEY}
    try:
        r = requests.post(
            f"{config.VISION_ENDPOINT}/vision/v3.2/read/analyze",
            headers={**headers, "Content-Type": "application/octet-stream"},
            data=image, timeout=20,
        )
        if r.status_code == 429:
            return [], "The text reader is busy (free tier limit). Try again in a minute."
        if r.status_code != 202:
            return [], f"The text reader answered {r.status_code}."
        loc = r.headers.get("Operation-Location")
        deadline = time.time() + 15
        while loc and time.time() < deadline:
            time.sleep(0.5)
            g = requests.get(loc, headers=headers, timeout=20).json()
            state = g.get("status")
            if state == "succeeded":
                lines = []
                for page in (g.get("analyzeResult") or {}).get("readResults") or []:
                    for line in page.get("lines") or []:
                        if line.get("text"):
                            lines.append(line["text"])
                return lines, None
            if state == "failed":
                return [], "The text reader couldn't read this photo."
        return [], "The text reader took too long."
    except Exception as error:  # noqa: BLE001
        log.warning("vision read failed: %s", error)
        return [], "The text reader couldn't be reached."


# ---- matching what was read to a SKU ---------------------------------------

def _norm(s: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


# Letters and digits OCR mixes up, folded to one form on both sides.
_FOLD = str.maketrans("OQILSBZ", "0011582")


def _fold(s: str) -> str:
    return s.translate(_FOLD)


def _one_edit(a: str, b: str) -> bool:
    """True when a and b differ by exactly one substitution, insertion
    or deletion."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def ocr_tokens(lines: list[str]) -> list[str]:
    """Candidate SKU strings in what was read: each word, each pair of
    neighbouring words run together ("F9172 B"), and each short line
    with its spaces squeezed out."""
    out: list[str] = []
    for line in lines:
        words = [_norm(w) for w in re.split(r"[^A-Za-z0-9]+", line or "")]
        words = [w for w in words if w]
        out.extend(words)
        out.extend(a + b for a, b in zip(words, words[1:]))
        whole = _norm(line)
        if 5 <= len(whole) <= 12:
            out.append(whole)
    seen, keep = set(), []
    for t in out:
        if t not in seen and len(t) >= 4 and re.search(r"\d", t) and re.search(r"[A-Z]", t):
            seen.add(t)
            keep.append(t)
    return keep


def match_skus(lines: list[str], catalog: list[dict],
               batch_skus: set[str] | None = None) -> dict:
    """Score catalog SKUs against what was read.

    Returns {status, sku, guesses}: "auto" with the SKU when one match is
    clearly best, "ask" with up to three guesses when it's close or only
    a product family matched (F9301A -> F9301AA/AB/AC), "none" when
    nothing looked like one of our SKUs."""
    batch_skus = {s.upper() for s in (batch_skus or set())}
    by_norm: dict[str, list[dict]] = {}
    by_fold: dict[str, list[dict]] = {}
    for p in catalog:
        n = _norm(p.get("sku"))
        if len(n) < 4:
            continue
        by_norm.setdefault(n, []).append(p)
        by_fold.setdefault(_fold(n), []).append(p)
    words = {_norm(w) for line in lines for w in re.split(r"\s+", line or "")}
    words.discard("")
    scores: dict[str, float] = {}
    info: dict[str, dict] = {}

    def offer(p: dict, score: float) -> None:
        key = (p.get("sku") or "").upper()
        if score > scores.get(key, 0):
            scores[key] = score
            info[key] = p

    for tok in ocr_tokens(lines):
        for p in by_norm.get(tok, []):
            offer(p, 1.0)
        for p in by_fold.get(_fold(tok), []):
            offer(p, 0.92)
        if len(tok) >= 5:
            family = [p for n, ps in by_norm.items()
                      if n.startswith(tok) and 1 <= len(n) - len(tok) <= 2
                      for p in ps]
            for p in family:
                offer(p, 0.85 if len(family) == 1 else 0.6)
        if len(tok) >= 6:
            ft = _fold(tok)
            for n, ps in by_fold.items():
                # A longer SKU that STARTS with the read is a family
                # member, scored above - one extra letter isn't a typo.
                if n.startswith(ft) or ft.startswith(n):
                    continue
                if abs(len(n) - len(ft)) <= 1 and _one_edit(ft, n):
                    for p in ps:
                        offer(p, 0.7)
    for key, p in info.items():
        variant = _norm(p.get("variant_title"))
        if variant and variant != "DEFAULTTITLE" and variant in words:
            scores[key] += 0.25
        if key in batch_skus:
            scores[key] += 0.08
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    guesses = [
        {"sku": info[k].get("sku"), "title": _title(info[k]),
         "score": round(min(s, 1.0), 2)}
        for k, s in ranked[:3]
    ]
    if not ranked or ranked[0][1] < 0.5:
        return {"status": "none", "sku": None, "guesses": guesses}
    top = ranked[0][1]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if top >= 0.9 and top - second >= 0.15:
        return {"status": "auto", "sku": info[ranked[0][0]].get("sku"),
                "guesses": guesses}
    return {"status": "ask", "sku": None, "guesses": guesses}


def _title(p: dict) -> str:
    title = p.get("product_title") or p.get("sku") or ""
    variant = p.get("variant_title")
    if variant and variant.lower() != "default title":
        title = f"{title} · {variant}"
    return title


# ---- catalog ----------------------------------------------------------------

_catalog_lock = threading.Lock()
_catalog_cache: dict = {"at": 0.0, "rows": []}


def catalog(session: Session) -> list[dict]:
    """Every SKU in the bin map (one row per SKU), cached ten minutes."""
    with _catalog_lock:
        if time.time() - _catalog_cache["at"] < 600 and _catalog_cache["rows"]:
            return _catalog_cache["rows"]
    rows = session.execute(
        select(BinMapEntry.sku, BinMapEntry.product_title,
               BinMapEntry.variant_title, BinMapEntry.bin)
        .where(BinMapEntry.sku.is_not(None))
    ).all()
    seen: dict[str, dict] = {}
    for r in rows:
        key = (r.sku or "").strip().upper()
        if key and key not in seen:
            seen[key] = {"sku": r.sku.strip(), "product_title": r.product_title,
                         "variant_title": r.variant_title, "bin": r.bin}
    out = list(seen.values())
    with _catalog_lock:
        _catalog_cache.update(at=time.time(), rows=out)
    return out


def _catalog_map(session: Session) -> dict[str, dict]:
    return {p["sku"].upper(): p for p in catalog(session)}


def batch_products(session: Session, batch_id: int | None) -> list[dict]:
    if not batch_id:
        return []
    rows = session.execute(
        select(BatchItem.sku, BatchItem.product_title, BatchItem.variant_title)
        .where(BatchItem.batch_id == batch_id, BatchItem.sku.is_not(None))
    ).all()
    seen: dict[str, dict] = {}
    for r in rows:
        key = r.sku.strip().upper()
        if key and key not in seen:
            seen[key] = {"sku": r.sku.strip(), "product_title": r.product_title,
                         "variant_title": r.variant_title}
    return list(seen.values())


# ---- records ----------------------------------------------------------------

def _photo_dict(row: BoxPhoto, cmap: dict[str, dict]) -> dict:
    p = cmap.get((row.sku or "").upper())
    try:
        guesses = json.loads(row.guesses or "[]")
    except ValueError:
        guesses = []
    return {
        "id": row.id,
        "uid": row.uid,
        "status": row.status,
        "sku": row.sku,
        "title": _title(p) if p else None,
        "guesses": guesses,
        "ocr_text": row.ocr_text or "",
        "ocr_error": row.ocr_error,
        "new_box": bool(row.new_box),
        "worker": row.worker,
        "batch_id": row.batch_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "focus_diopters": row.focus_diopters,
        "af_ms": row.af_ms,
        "af_result": row.af_result,
        "box_uid": row.box_uid,
        "quality": row.quality,
    }


def _history(session: Session, field: str, new: str, by: str | None,
             sku: str | None = None, title: str | None = None) -> None:
    session.add(BarcodeChange(
        sku=sku, product_title=None if title is None else title[:255],
        changed_field=field, new_barcode=new[:255],
        changed_by=None if by is None else by[:100],
    ))


def _get_photo(session: Session, photo_id: int) -> BoxPhoto:
    row = session.get(BoxPhoto, photo_id)
    if row is None:
        raise HTTPException(404, "That photo isn't on the server.")
    return row


def _decode(b64: str, cap: int, what: str) -> bytes:
    try:
        data = base64.b64decode(b64, validate=False)
    except (binascii.Error, ValueError):
        raise HTTPException(400, f"The {what} didn't arrive intact. Take it again.")
    if not data or len(data) > cap:
        raise HTTPException(413, f"The {what} is too large.")
    if not data.startswith(b"\xff\xd8"):
        raise HTTPException(400, f"The {what} isn't a JPEG.")
    return data


class BoxPhotoIn(BaseModel):
    uid: str = Field(min_length=8, max_length=40, pattern=r"^[A-Za-z0-9-]+$")
    image_b64: str = Field(min_length=100)
    thumb_b64: str = Field(min_length=100)
    batch_id: int | None = None
    new_box: bool = True
    # "Keep in SKU" mode: file straight into this folder, still read.
    pin_sku: str | None = Field(default=None, max_length=100)
    # The pin was inferred (another photo of the same box was read), not
    # chosen by a person: file it as "auto" until someone confirms.
    pin_auto: bool = False
    worker: str | None = Field(default=None, max_length=100)
    focus_diopters: float | None = Field(default=None, ge=0, le=100)
    af_ms: int | None = Field(default=None, ge=0, le=120000)
    af_result: str | None = Field(default=None, max_length=16)
    focus_mode: str | None = Field(default=None, max_length=16)
    box_uid: str | None = Field(default=None, max_length=40)


@router.post("")
def upload_photo(payload: BoxPhotoIn, request: Request,
                 session: Session = Depends(get_session)):
    cmap = _catalog_map(session)
    existing = session.scalar(select(BoxPhoto).where(BoxPhoto.uid == payload.uid))
    if existing is not None:
        return {"photo": _photo_dict(existing, cmap)}
    image = _decode(payload.image_b64, MAX_IMAGE_BYTES, "photo")
    thumb = _decode(payload.thumb_b64, MAX_THUMB_BYTES, "thumbnail")
    folder = _now().strftime("%Y-%m")
    (photo_dir() / folder).mkdir(parents=True, exist_ok=True)
    name = f"{folder}/{payload.uid}.jpg"
    (photo_dir() / name).write_bytes(image)
    (photo_dir() / f"{folder}/{payload.uid}_t.jpg").write_bytes(thumb)

    lines, error = azure_read(image)
    by = _auth.actor_name(request, payload.worker)
    row = BoxPhoto(uid=payload.uid, worker=by, batch_id=payload.batch_id,
                   file_name=name, new_box=payload.new_box,
                   ocr_text="\n".join(lines)[:4000], ocr_error=error,
                   focus_diopters=payload.focus_diopters, af_ms=payload.af_ms,
                   af_result=payload.af_result, focus_mode=payload.focus_mode,
                   box_uid=payload.box_uid)
    pin = (payload.pin_sku or "").strip()
    if pin:
        p = cmap.get(pin.upper())
        row.sku = p["sku"] if p else pin
        if payload.pin_auto:
            row.status = "auto"
        else:
            row.status = "confirmed"
            row.confirmed_at = _now()
            row.confirmed_by = by
        row.guesses = "[]"
    else:
        skus = {p["sku"] for p in batch_products(session, payload.batch_id)}
        verdict = match_skus(lines, catalog(session), skus)
        row.status = verdict["status"]
        row.sku = verdict["sku"]
        row.guesses = json.dumps(verdict["guesses"])
    session.add(row)
    session.commit()
    return {"photo": _photo_dict(row, cmap)}


@router.get("/current")
def current_photos(batch_id: int | None = None,
                   session: Session = Depends(get_session)):
    """The shipment in progress: every photo not yet sent to the sorter,
    newest first, plus the open batch's products for the folder list."""
    cmap = _catalog_map(session)
    rows = session.scalars(
        select(BoxPhoto)
        .where(BoxPhoto.handoff_id.is_(None), BoxPhoto.status != "deleted")
        .order_by(BoxPhoto.id.desc())
        .limit(1000)
    ).all()
    batch = session.get(Batch, batch_id) if batch_id else None
    products = [
        {"sku": p["sku"], "title": _title(cmap.get(p["sku"].upper()) or p)}
        for p in batch_products(session, batch_id)
    ]
    pending = session.scalars(
        select(BoxPhotoHandoff.id).where(
            BoxPhotoHandoff.loaded_at.is_(None),
            BoxPhotoHandoff.dismissed_at.is_(None))
    ).all()
    return {
        "photos": [_photo_dict(r, cmap) for r in rows],
        "batch": ({"id": batch.id, "label": batch.bin_name, "products": products}
                  if batch else None),
        "reader_ready": bool(config.VISION_ENDPOINT and config.VISION_KEY),
        "handoffs_pending": len(pending),
        "focus": focus_summary(session, 60),
    }


def _median(xs: list) -> float | None:
    xs = sorted(xs)
    if not xs:
        return None
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2


def focus_summary(session: Session, last: int = 200) -> dict:
    """Where the recent photos focused and how fast: the gun parks its
    lens at the median so focusing starts close to right."""
    rows = session.execute(
        select(BoxPhoto.focus_diopters, BoxPhoto.af_ms, BoxPhoto.af_result)
        .where(BoxPhoto.focus_diopters.is_not(None))
        .order_by(BoxPhoto.id.desc()).limit(last)
    ).all()
    locked = [r.focus_diopters for r in rows
              if r.af_result in ("locked", "continuous") and r.focus_diopters]
    times = sorted(r.af_ms for r in rows if r.af_ms is not None)
    med = _median(locked)
    results: dict[str, int] = {}
    for r in rows:
        results[r.af_result or "?"] = results.get(r.af_result or "?", 0) + 1
    return {
        "n": len(rows),
        "median_diopters": round(med, 2) if med else None,
        "median_cm": round(100 / med) if med else None,
        "af_ms_median": int(_median(times)) if times else None,
        "af_ms_p90": times[int(len(times) * 0.9)] if times else None,
        "results": results,
    }


@router.get("/focus")
def focus_stats(last: int = 200, session: Session = Depends(get_session)):
    return focus_summary(session, max(1, min(last, 2000)))


class WorkerIn(BaseModel):
    worker: str | None = Field(default=None, max_length=100)


class FileIn(WorkerIn):
    sku: str = Field(min_length=1, max_length=100)
    # Filed by inference (a box-mate's read), not by a person.
    auto: bool = False


@router.post("/{photo_id}/file")
def file_photo(photo_id: int, payload: FileIn, request: Request,
               session: Session = Depends(get_session)):
    """A person put this photo in a folder: that's a confirmed label."""
    row = _get_photo(session, photo_id)
    cmap = _catalog_map(session)
    p = cmap.get(payload.sku.strip().upper())
    if p is None:
        raise HTTPException(404, f"{payload.sku} isn't in the catalog.")
    row.sku = p["sku"]
    if payload.auto:
        row.status = "auto"
        row.confirmed_at = None
        row.confirmed_by = None
    else:
        row.status = "confirmed"
        row.confirmed_at = _now()
        row.confirmed_by = _auth.actor_name(request, payload.worker)
    session.commit()
    return {"photo": _photo_dict(row, cmap)}


@router.post("/{photo_id}/unfile")
def unfile_photo(photo_id: int, session: Session = Depends(get_session)):
    """Undo a filing: back to Incoming with the reader's guesses."""
    row = _get_photo(session, photo_id)
    try:
        has_guesses = bool(json.loads(row.guesses or "[]"))
    except ValueError:
        has_guesses = False
    row.sku = None
    row.status = "ask" if has_guesses else "none"
    row.confirmed_at = None
    row.confirmed_by = None
    session.commit()
    return {"photo": _photo_dict(row, _catalog_map(session))}


class QualityIn(BaseModel):
    # None clears the tag.
    quality: Literal["good", "blurry", "angle"] | None = None


@router.post("/{photo_id}/quality")
def tag_quality(photo_id: int, payload: QualityIn,
                session: Session = Depends(get_session)):
    """The operator's verdict on one photo: the sharp one, a blurry
    one, or an angle without the sticker."""
    row = _get_photo(session, photo_id)
    row.quality = payload.quality
    session.commit()
    return {"photo": _photo_dict(row, _catalog_map(session))}


@router.post("/{photo_id}/delete")
def delete_photo(photo_id: int, session: Session = Depends(get_session)):
    row = _get_photo(session, photo_id)
    row.status = "deleted"
    session.commit()
    return {"ok": True}


@router.post("/{photo_id}/restore")
def restore_photo(photo_id: int, session: Session = Depends(get_session)):
    row = _get_photo(session, photo_id)
    if row.status == "deleted":
        if row.sku:
            row.status = "confirmed" if row.confirmed_at else "auto"
        else:
            try:
                row.status = "ask" if json.loads(row.guesses or "[]") else "none"
            except ValueError:
                row.status = "none"
        session.commit()
    return {"photo": _photo_dict(row, _catalog_map(session))}


class ConfirmIn(WorkerIn):
    ids: list[int] = Field(min_length=1, max_length=500)


@router.post("/confirm")
def confirm_photos(payload: ConfirmIn, request: Request,
                   session: Session = Depends(get_session)):
    """"Looks right": the reader's auto filings become real labels."""
    by = _auth.actor_name(request, payload.worker)
    rows = session.scalars(
        select(BoxPhoto).where(BoxPhoto.id.in_(payload.ids),
                               BoxPhoto.status == "auto")
    ).all()
    for row in rows:
        row.status = "confirmed"
        row.confirmed_at = _now()
        row.confirmed_by = by
    session.commit()
    return {"confirmed": len(rows)}


def _file_response(row: BoxPhoto, thumb: bool) -> FileResponse:
    name = row.file_name
    if thumb:
        name = name[:-4] + "_t.jpg"
    path = photo_dir() / name
    if not path.is_file():
        raise HTTPException(404, "The photo file is missing.")
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=86400"})


@router.get("/{photo_id}/thumb")
def photo_thumb(photo_id: int, session: Session = Depends(get_session)):
    return _file_response(_get_photo(session, photo_id), True)


@router.get("/{photo_id}/image")
def photo_image(photo_id: int, session: Session = Depends(get_session)):
    return _file_response(_get_photo(session, photo_id), False)


@router.get("/search")
def search_skus(q: str = "", batch_id: int | None = None,
                session: Session = Depends(get_session)):
    """Folder picker: the batch's products first, then catalog hits on
    SKU or name. A scanned barcode matches too."""
    term = q.strip()
    first = [{"sku": p["sku"], "title": _title(p), "in_batch": True}
             for p in batch_products(session, batch_id)]
    if not term:
        return {"results": first[:40]}
    n = _norm(term)
    words = [w for w in term.lower().split() if w]
    hits = []
    for p in catalog(session):
        sku_n = _norm(p["sku"])
        title = (_title(p) or "").lower()
        if (n and n in sku_n) or (words and all(w in title for w in words)):
            hits.append(p)
    if not hits and n:
        row = session.scalar(select(BinMapEntry).where(BinMapEntry.barcode == term))
        if row is not None and row.sku:
            hits.append({"sku": row.sku, "product_title": row.product_title,
                         "variant_title": row.variant_title})
    batch_keys = {p["sku"].upper() for p in first}
    hits.sort(key=lambda p: (p["sku"].upper() not in batch_keys,
                             not _norm(p["sku"]).startswith(n), p["sku"]))
    return {"results": [
        {"sku": p["sku"], "title": _title(p),
         "in_batch": p["sku"].upper() in batch_keys}
        for p in hits[:40]
    ]}


# ---- hand-off to the web sorter --------------------------------------------

class SendItem(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    title: str | None = Field(default=None, max_length=255)
    qty: int = Field(ge=1, le=500)


class SendIn(WorkerIn):
    items: list[SendItem] = Field(min_length=1, max_length=300)
    photo_ids: list[int] = Field(default_factory=list, max_length=2000)
    batch_id: int | None = None


@router.post("/send")
def send_to_sorter(payload: SendIn, request: Request,
                   session: Session = Depends(get_session)):
    by = _auth.actor_name(request, payload.worker)
    boxes = sum(i.qty for i in payload.items)
    row = BoxPhotoHandoff(
        worker=by, batch_id=payload.batch_id, boxes=boxes,
        items=json.dumps([i.model_dump() for i in payload.items]),
    )
    session.add(row)
    session.flush()
    if payload.photo_ids:
        for photo in session.scalars(
            select(BoxPhoto).where(BoxPhoto.id.in_(payload.photo_ids),
                                   BoxPhoto.handoff_id.is_(None))
        ).all():
            photo.handoff_id = row.id
    products = len(payload.items)
    _history(session, "box-photos-sent",
             f"{boxes} {'box' if boxes == 1 else 'boxes'} of "
             f"{products} {'product' if products == 1 else 'products'} "
             "sent to the shipment sorter", by)
    session.commit()
    return {"handoff": _handoff_dict(row)}


def _handoff_dict(row: BoxPhotoHandoff) -> dict:
    try:
        items = json.loads(row.items or "[]")
    except ValueError:
        items = []
    return {
        "id": row.id,
        "worker": row.worker,
        "batch_id": row.batch_id,
        "boxes": row.boxes,
        "items": items,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "loaded_at": row.loaded_at.isoformat() if row.loaded_at else None,
    }


@router.get("/handoffs")
def list_handoffs(session: Session = Depends(get_session)):
    """Sent lists the web sorter hasn't loaded or dismissed yet."""
    rows = session.scalars(
        select(BoxPhotoHandoff)
        .where(BoxPhotoHandoff.loaded_at.is_(None),
               BoxPhotoHandoff.dismissed_at.is_(None))
        .order_by(BoxPhotoHandoff.id.desc())
        .limit(20)
    ).all()
    return {"handoffs": [_handoff_dict(r) for r in rows]}


@router.post("/handoffs/{handoff_id}/load")
def load_handoff(handoff_id: int, payload: WorkerIn, request: Request,
                 session: Session = Depends(get_session)):
    """Claim the list for one sorter, so two terminals can't load it
    twice."""
    row = session.get(BoxPhotoHandoff, handoff_id)
    if row is None:
        raise HTTPException(404, "That list isn't on the server.")
    if row.loaded_at is not None:
        raise HTTPException(409, "That list was already loaded into a sorter.")
    row.loaded_at = _now()
    row.loaded_by = _auth.actor_name(request, payload.worker)
    session.commit()
    return {"handoff": _handoff_dict(row)}


@router.post("/handoffs/{handoff_id}/dismiss")
def dismiss_handoff(handoff_id: int, session: Session = Depends(get_session)):
    row = session.get(BoxPhotoHandoff, handoff_id)
    if row is None:
        raise HTTPException(404, "That list isn't on the server.")
    row.dismissed_at = _now()
    session.commit()
    return {"ok": True}


@router.get("/export")
def export_photos(session: Session = Depends(get_session)):
    """Every photo's record, for building the reader off the gun."""
    cmap = _catalog_map(session)
    rows = session.scalars(select(BoxPhoto).order_by(BoxPhoto.id)).all()
    return {"photos": [{**_photo_dict(r, cmap), "file": r.file_name,
                        "handoff_id": r.handoff_id,
                        "confirmed_by": r.confirmed_by} for r in rows]}
