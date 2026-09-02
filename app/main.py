"""FastAPI application: pages + JSON API.

Request flow (mirrors what runs on Azure):
  Browser scan -> JS fetch -> FastAPI route -> shopify.py / database -> JSON

No terminal input anywhere. The scanner types into browser fields exactly
as it would type into Notepad, and JavaScript forwards each scan here.
"""
import json
import logging
import os
import re
import secrets
import time
import unicodedata
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import AliasChoices, BaseModel, Field, field_validator
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.requests import Request

from app import config, oneleft, orders_sync, planner, shopify
from app.auth import require_user
from app.database import (
    DatabaseNotConfigured,
    database_configured,
    get_engine,
    get_session,
    init_db,
)
from app.models import (
    AppSetting,
    AuditFind,
    AuditSession,
    AuditSessionItem,
    BackorderDebt,
    BarcodeAlias,
    BarcodeChange,
    Batch,
    BatchItem,
    BinMapEntry,
    BundleContent,
    C72Command,
    CompanionTag,
    C72DebugEvent,
    C72Tuning,
    CaseCode,
    EpcCapture,
    FlaggedBin,
    HeldLabelItem,
    HeldLabelList,
    HiddenBin,
    LabelDismissal,
    LabelName,
    LinkScan,
    LocateQueueEntry,
    MismatchDismissal,
    MultiboxProduct,
    NonTaggable,
    OneLeftCheck,
    OrderReceipt,
    Printer,
    PrintJob,
    ProductKind,
    RefreshLog,
    ReleasedTag,
    RetiredTag,
    ReviewNote,
    ReviewTask,
    RfidAssignment,
    RfidIncompatible,
    ScanNote,
    SerialPrefix,
    SoldRecord,
    SortHandoff,
)

logger = logging.getLogger("rfid")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Cache-buster for static assets: changes on every app start (i.e. every
# deploy), so browsers stop serving stale JS/CSS after updates.
ASSET_VERSION = str(int(time.time()))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup only when a database is configured. Locally,
    # before you provision PostgreSQL, the app still boots and does lookups.
    if database_configured():
        init_db()
        # Warm the bin map (Shopify metafield walk) in the background; the
        # persisted table keeps answering while a refresh runs.
        if not config.check_shopify_env():
            _maybe_refresh_bin_map()
        # Daily fulfilled-order sync (8 AM Toronto) — read-only, fail-soft.
        orders_sync.start_daily_thread()
    yield


app = FastAPI(title="RFID Inventory", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.middleware("http")
async def frame_ancestors_for_shopify(request: Request, call_next):
    """Allow the page to be iframed by Shopify admin (embedded app) and
    nothing else."""
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Content-Security-Policy"] = (
            "frame-ancestors https://admin.shopify.com https://*.myshopify.com"
        )
        # The page must never be cached: it carries the version-stamped
        # asset URLs, so a cached page pins stale JS/CSS across deploys
        # (the "feature didn't reach the warehouse browser" bug, twice).
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(DatabaseNotConfigured)
def _db_not_configured(request: Request, exc: DatabaseNotConfigured):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=503,
        content={"detail": "Database not configured. Set DATABASE_URL to "
                           "enable saving and listing assignments."},
    )


def require_shopify_write(feature: str = "scan_station") -> None:
    """Server-side Shopify write gate (config.SHOPIFY_WRITE_MODE). Every
    write endpoint calls this with its own feature name. The mode is
    either "disabled", "production" (everything confirmed), or a comma
    list of enabled features — "scan_station_only" enables the Scan
    Station flows, and specific features can be promoted one at a time
    ("scan_station_only,verify_onhand") without opening the floodgates."""
    mode = config.SHOPIFY_WRITE_MODE
    if mode == "disabled":
        raise HTTPException(
            403, "Shopify writes are disabled (SHOPIFY_WRITE_MODE=disabled)."
        )
    parts = {p.strip() for p in mode.split(",") if p.strip()}
    if "production" in parts:
        return
    if feature == "scan_station" and "scan_station_only" in parts:
        return
    if feature in parts:
        return
    raise HTTPException(
        403,
        f"Shopify write '{feature}' is not enabled yet "
        f"(SHOPIFY_WRITE_MODE={mode}). Add '{feature}' to the mode (or "
        f"promote to 'production') to turn it on.",
    )


def shopify_write_enabled(feature: str) -> bool:
    """Non-raising twin of require_shopify_write, for read endpoints
    that tell the client which buttons to draw."""
    try:
        require_shopify_write(feature)
        return True
    except HTTPException:
        return False


# ---------------------------------------------------------------- schemas ---
class AssignmentIn(BaseModel):
    # max_length values mirror the column sizes in models.py so bad input
    # fails as a clear 422 here, not a SQL Server truncation error.
    rfid_id: str = Field(max_length=128)
    shopify_variant_id: str = Field(max_length=64)
    shopify_product_id: str | None = Field(default=None, max_length=300)
    product_title: str = Field(max_length=255)
    variant_title: str | None = Field(default=None, max_length=255)
    sku: str | None = Field(default=None, max_length=100)
    barcode: str | None = Field(default=None, max_length=64)
    bin_location: str | None = Field(default=None, max_length=100)
    assigned_by: str | None = Field(default=None, max_length=100)

    @field_validator("rfid_id", "shopify_variant_id", "product_title")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


# ------------------------------------------------------------------ pages ---
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    missing = config.check_shopify_env()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "shopify_ready": not missing,
            "missing_env": missing,
            "db_ready": database_configured(),
            "allow_remote_print": config.ALLOW_REMOTE_PRINT,
            "operators": config.OPERATORS,
            "asset_version": ASSET_VERSION,
            # For "open in Shopify admin" links: the store's handle, i.e.
            # "telcan" out of "telcan.myshopify.com".
            "shop_handle": (config.SHOPIFY_STORE or "").split(".")[0],
            # App Bridge only when loaded inside Shopify admin (it adds a
            # 'host' query param); the script is inert/broken outside it.
            "app_bridge_key": (
                config.SHOPIFY_CLIENT_ID
                if request.query_params.get("host")
                else None
            ),
        },
    )


@app.get("/health")
def health():
    return {
        "status": "running",
        "shopify_env_ok": not config.check_shopify_env(),
        "database_configured": database_configured(),
    }


# -------------------------------------------------------------- lookup API ---
def _lookup_api(barcode: str) -> dict | None:
    product = shopify.lookup_barcode(barcode)
    if product is not None:
        product["source"] = "shopify"
    return product


def _binmap_product(row: BinMapEntry) -> dict:
    """A bin-map row in the flat shape every lookup source returns. Its ids
    are real Shopify gids (the walk built them), not the mirror's
    `telcan:` surrogates."""
    return {
        "image_url": row.image_url,
        "shopify_variant_id": row.shopify_variant_id,
        "shopify_product_id": row.shopify_product_id,
        "product_title": row.product_title or "(unknown)",
        "variant_title": row.variant_title,
        "sku": row.sku,
        "barcode": row.barcode,
        "bin_location": (row.bin or "").strip() or "No bin assigned",
        "other_bins": row.other_bins,
        "vendor": row.vendor,
        "source": "binmap",
    }


def _lookup_bin_map(term: str) -> dict | None:
    """Live-sourced catalog lookup, the first stop for every barcode/SKU.

    The bin map is rebuilt from the Shopify API every few hours, so when it
    knows a barcode its SKU, title and ids are current. Anything it doesn't
    know goes straight to the live API — the TELCAN mirror that used to sit
    between them was REMOVED 2026-08-07 (sync dead since Dec 2025; it
    stamped renamed SKUs and cross-wired handles onto tags).

    Barcode wins over SKU, and when several listings share a barcode the
    primary one is preferred, so an OPEN BOX twin never shadows the main
    listing."""
    rows = _lookup_bin_map_all(term)
    return rows[0] if rows else None


def _lookup_bin_map_all(term: str) -> list[dict]:
    """Every live listing for a barcode/SKU, primary first — one indexed
    query, so the Check step can use it in its per-item loop."""
    from app.database import get_engine

    wanted = (term or "").strip()
    if not wanted:
        return []
    with Session(get_engine()) as session:
        rows = session.scalars(
            select(BinMapEntry)
            .where(BinMapEntry.barcode == wanted)
            .order_by(BinMapEntry.id)
        ).all()
        if not rows:
            rows = session.scalars(
                select(BinMapEntry)
                .where(func.upper(BinMapEntry.sku) == wanted.upper())
                .order_by(BinMapEntry.id)
            ).all()
        if not rows:
            return []
        # One row per bin for split-shelf products: collapse to one
        # candidate per SKU before ranking.
        seen: dict[str, BinMapEntry] = {}
        for r in rows:
            seen.setdefault((r.sku or "").strip().upper(), r)
        return sorted(
            (_binmap_product(r) for r in seen.values()), key=_candidate_rank
        )


MISSING_BIN_VALUES = (None, "", "No bin assigned")


def _case_payload(session: Session, code: str) -> dict | None:
    """The case behind a scanned code, with the product it contains resolved
    fresh. Returned by every scan path so the warning follows the BARCODE
    rather than being re-implemented per tab."""
    row = session.get(CaseCode, code.strip())
    if row is None:
        return None
    product = None
    try:
        product = product_by_barcode(row.sku)
    except HTTPException:
        product = None
    return {
        "barcode": row.barcode,
        "sku": row.sku,
        "units": row.units,
        "scan_note": row.scan_note,
        "product_title": (
            (product or {}).get("product_title") or row.product_title
        ),
        "product": product,
        # Ready-made one-liner so the C72 and the web never drift apart.
        "summary": (
            f"{row.units} x {row.sku}"
            + (f" · {(product or {}).get('product_title')}" if product else "")
            + (f" -> {(product or {}).get('bin_location')}"
               if product and product.get("bin_location") else "")
        ),
    }


def _case_for(session: Session, code: str | None) -> dict | None:
    if not code:
        return None
    try:
        return _case_payload(session, code)
    except Exception as error:  # never let a case lookup break a scan
        logger.warning("case lookup failed for %s: %s", code, error)
        return None


@app.get("/api/cases/{barcode}", dependencies=[Depends(require_user)])
def get_case(barcode: str, session: Session = Depends(get_session)):
    """Is this scanned code a case? Used by the C72's Find Bin and by any
    client that needs the answer on its own."""
    case = _case_payload(session, barcode)
    if case is None:
        raise HTTPException(404, "That barcode isn't a known case.")
    return case


class CaseIn(BaseModel):
    barcode: str = Field(max_length=64)
    # What one case contains. Always N of a single product by design.
    sku: str = Field(max_length=100)
    units: int = Field(ge=2, le=500)
    scan_note: str | None = Field(default=None, max_length=255)
    created_by: str | None = Field(default=None, max_length=100)

    @field_validator("barcode", "sku")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


@app.post("/api/cases", status_code=201, dependencies=[Depends(require_user)])
def upsert_case(payload: CaseIn, session: Session = Depends(get_session)):
    """Record that a barcode is a case of N units of one product. Local
    only — nothing about a case is written to Shopify."""
    code = payload.barcode.strip()
    # Refuse to shadow a real listing: if the code already resolves, calling
    # it a case would quietly change what an existing barcode means.
    existing_product = None
    try:
        existing_product = product_by_barcode(code)
    except HTTPException as error:
        if error.status_code != 404:
            raise
    if existing_product is not None:
        raise HTTPException(
            409,
            f"{code} is already a real product "
            f"({existing_product.get('sku')}) — a case code has to be a "
            f"barcode Shopify doesn't know.",
        )

    product = None
    try:
        product = product_by_barcode(payload.sku)
    except HTTPException as error:
        if error.status_code != 404:
            raise
    if product is None:
        raise HTTPException(
            404, f"No product found for {payload.sku} — check the SKU."
        )

    row = session.get(CaseCode, code)
    if row is None:
        row = CaseCode(barcode=code, sku=product.get("sku") or payload.sku)
        session.add(row)
    row.sku = product.get("sku") or payload.sku
    row.units = payload.units
    row.scan_note = (payload.scan_note or "").strip() or None
    row.product_title = (product.get("product_title") or "")[:255] or None
    row.created_by = row.created_by or payload.created_by
    row.updated_at = datetime.now(timezone.utc)
    session.commit()
    return {
        "case": row.as_dict(),
        "message": (
            f"{code} recorded as {row.units} x {row.sku} "
            f"({row.product_title or 'unnamed'})."
        ),
    }


@app.delete("/api/cases/{barcode}", dependencies=[Depends(require_user)])
def delete_case(barcode: str, session: Session = Depends(get_session)):
    row = session.get(CaseCode, barcode.strip())
    if row is None:
        raise HTTPException(404, "No such case code.")
    session.delete(row)
    session.commit()
    return {"deleted": barcode.strip()}


def _live_barcode_map(session: Session) -> dict[str, str]:
    """sku -> barcode per the live-sourced bin map, first row wins. The
    Check step passes this in so a 50-item batch reads the table once, not
    once per item — the per-item reads were what timed the C72 out."""
    live: dict[str, str] = {}
    for sku, bc in session.execute(
        select(BinMapEntry.sku, BinMapEntry.barcode)
        .where(BinMapEntry.sku.isnot(None))
    ):
        if sku and sku not in live:
            live[sku] = (bc or "").strip()
    return live


@app.get(
    "/api/products/by-barcode/{barcode}",
    dependencies=[Depends(require_user)],
)
def product_by_barcode(barcode: str):
    """Barcode-or-SKU -> product. The per-product scan note rides every
    successful lookup, so BOTH scanning surfaces (Scan Station card, C72)
    can show it without a second call."""
    product = _product_lookup(barcode)
    sku = (product.get("sku") or "").strip() if product else ""
    if sku and database_configured():
        try:
            with Session(get_engine()) as session:
                sn = session.scalar(
                    select(ScanNote).where(
                        func.upper(ScanNote.sku) == sku.upper()
                    )
                )
            if sn is not None:
                product["scan_note"] = sn.note
        except Exception:  # noqa: BLE001 — the note is decoration
            pass
    return product


def _product_lookup(barcode: str):
    """Barcode-or-SKU -> product (bad/missing barcodes happen, so the same
    field accepts a typed SKU). Source order is config.BARCODE_LOOKUP:
    auto = live bin map, then the Shopify API; or force 'db' (bin map
    only) / 'api'. The TELCAN mirror was REMOVED 2026-08-07 — its dead
    sync stamped renamed SKUs and cross-wired handles onto tags (G3M662C
    for the live G3M662C-L; ATR585M linked to the ATR294M page). The
    repair that cleaned those records: dev/repair_mirror_records.py."""
    barcode = barcode.strip()
    mode = config.BARCODE_LOOKUP
    db_ok = database_configured()
    api_ok = not config.check_shopify_env()
    errors: list[str] = []

    if mode in ("auto", "db") and db_ok:
        try:
            product = _lookup_bin_map(barcode)
            if product is not None:
                return product
        except Exception as error:
            logger.warning("bin-map lookup failed: %s", error)
            if mode == "db":
                raise HTTPException(502, f"bin-map lookup failed: {error}")

    if mode in ("auto", "api") and api_ok:
        try:
            product = _lookup_api(barcode)
            if product is not None:
                return product
        # Broad on purpose: requests raises HTTPError (not RuntimeError)
        # when the token call itself fails, which escaped as a 500 and
        # took the product window down with it. A Shopify outage is a
        # bad-gateway, not a crash.
        except Exception as error:
            errors.append(f"Shopify lookup failed: {error}")
            raise HTTPException(502, errors[-1])

    # Not a real barcode/SKU — maybe an operator-linked alias (a foreign
    # barcode, e.g. the manufacturer's, confirmed to mean one of our
    # products). Resolves normally but flagged so the UI can confirm.
    if db_ok:
        from app.database import get_engine

        with Session(get_engine()) as session:
            # Case-insensitive: label-line aliases are TYPED, not scanned
            # ("zwo softbag small" should find the bag), and real
            # identities were already tried above so nothing is shadowed.
            alias = session.scalar(
                select(BarcodeAlias).where(
                    func.upper(BarcodeAlias.alias_barcode)
                    == barcode.strip().upper()
                )
            )
        if alias is not None:
            product = _resolve(alias.sku or alias.barcode, mode, db_ok, api_ok)
            if product is not None:
                product["alias_barcode"] = alias.alias_barcode
                product["alias_warning"] = True
                return product

        # Or a brand serial number whose leading digits identify the
        # product (Astronomik barcodes each unit's serial; the first 4
        # digits are the item). Length-bounded so ordinary UPC/EAN-13/14
        # retail barcodes never fall in here.
        if barcode.isdigit() and 5 <= len(barcode) <= 12:
            with Session(get_engine()) as session:
                sp = session.get(SerialPrefix, barcode[:4])
            if sp is not None:
                product = _resolve(sp.sku, mode, db_ok, api_ok)
                if product is not None:
                    product["serial_brand"] = sp.brand
                    product["serial_prefix"] = sp.prefix
                    product["serial_number"] = barcode
                    product["serial_item_name"] = sp.item_name
                    product["serial_label"] = (
                        sp.label_name or _default_serial_label(sp.item_name)
                    )
                    # True only when an operator has saved the name — the
                    # UI's auto-print trusts confirmed names, not defaults.
                    product["serial_label_saved"] = sp.label_name is not None
                    if sp.scan_note:
                        product["serial_note"] = sp.scan_note
                    return product
                # Structured detail: the UI prefills its SKU-update flow
                # with the manufacturer's current SKU for this prefix.
                raise HTTPException(
                    404,
                    {
                        "message": (
                            f"Recognized an {sp.brand} serial number "
                            f"(prefix {sp.prefix} = {sp.item_name}), but no "
                            f"product with SKU {sp.sku} exists in the "
                            f"catalog — the store's SKU may be outdated."
                        ),
                        "suggested_sku": sp.sku,
                        "serial_prefix": sp.prefix,
                        "brand": sp.brand,
                    },
                )

    # Broken-character rescue (Nick, 2026-08-25 - ZWO ships SKUs with the
    # single unicode char 'Ⅱ' for II, which VARCHAR stores as '?'):
    # 1) NFKC folds compatibility characters to plain ASCII (Ⅱ -> II), so
    #    an old label carrying the REAL character finds a record that was
    #    since fixed to the proper text.
    # 2) Folding the scan's non-ASCII to '?' finds a record the database
    #    mangled and still holds that way.
    # Each fires only when the term actually contains such characters and
    # re-enters the FULL chain (aliases included); the changed-term guard
    # makes recursion terminate (all folds are idempotent).
    # 3) '+' -> ' ' (2026-08-26): C72 builds before 3.63 form-encoded URL
    #    path segments, so the gun asked for "ZWO+D25AR" when the operator
    #    typed "ZWO D25AR". LAST in the chain on purpose - real SKUs
    #    contain '+' ("22451+81037+93575") and must win untouched first.
    for fixed in dict.fromkeys((
        unicodedata.normalize("NFKC", barcode),
        re.sub(r"[^\x00-\x7e]", "?", barcode),
        barcode.replace("+", " "),
    )):
        if fixed == barcode or not fixed.strip():
            continue
        try:
            product = _product_lookup(fixed)
        except HTTPException as error:
            if error.status_code == 404:
                continue
            raise
        if product is not None:
            # The UI can tell the operator what the scan REALLY said —
            # groundwork for a future one-tap "recommended fix".
            product.setdefault("charfold_from", barcode)
            return product

    if not db_ok and not api_ok:
        raise HTTPException(
            500, "Neither the database nor Shopify credentials are configured."
        )
    raise HTTPException(404, "No product found for that barcode or SKU.")


def _default_serial_label(item_name: str | None) -> str:
    """Sensible label default from the manufacturer's item name: drop the
    ', Made in Germany' tail, cut at the first parenthesis, drop the leading
    brand word. (Their sizes use decimal commas — '1,25"' — so cutting at
    the first comma would mangle most names.) Operators overwrite this with
    whatever the physical product label actually says."""
    if not item_name:
        return ""
    name = re.sub(r",?\s*made in germany\s*$", "", item_name, flags=re.I)
    name = name.split("(")[0]
    name = re.sub(r"^\s*astronomik\s+", "", name, flags=re.I)
    return name.strip(" ,")


def _resolve(term: str, mode: str, db_ok: bool, api_ok: bool) -> dict | None:
    """Resolve a barcode or SKU without alias/serial handling."""

    if not term:
        return None

    if mode in ("auto", "db") and db_ok:
        try:
            product = _lookup_bin_map(term)
            if product is not None:
                return product
        except Exception as error:
            logger.warning("bin-map lookup failed: %s", error)

    if mode in ("auto", "api") and api_ok:
        try:
            return _lookup_api(term)
        # Broad for the same reason as product_by_barcode: a failing token
        # call raises HTTPError, and an unresolvable term must come back
        # as None, never as a crash.
        except Exception as error:
            logger.warning("Shopify lookup failed: %s", error)

    return None


# Titles that mark secondary listings — the primary listing should be the
# default pick when one barcode matches several products.
_SECONDARY_TITLE = re.compile(r"open[\s-]?box|used|demo|refurb", re.I)


def _candidate_rank(p: dict) -> tuple:
    title = f"{p.get('product_title') or ''} {p.get('variant_title') or ''}"
    return (1 if _SECONDARY_TITLE.search(title) else 0,)


def products_by_barcode_all(
    code: str, live_barcodes: dict[str, str] | None = None
) -> list[dict]:
    """All catalog matches for a barcode, primary listing first. Falls back
    to the single-product resolver chain (alias/serial) when the direct
    barcode search finds nothing. `live_barcodes` lets a caller that loops
    over many items (the Check step) pay for the bin-map read once."""
    code = code.strip()
    mode = config.BARCODE_LOOKUP
    db_ok = database_configured()
    api_ok = not config.check_shopify_env()
    candidates: list[dict] = []
    if mode in ("auto", "db") and db_ok:
        try:
            candidates = _lookup_bin_map_all(code)
        except Exception as error:
            logger.warning("bin-map multi-lookup failed: %s", error)
    if not candidates and mode in ("auto", "api") and api_ok:
        try:
            candidates = shopify.lookup_barcode_all(code)
        except Exception as error:
            logger.warning("Shopify multi-lookup failed: %s", error)
    if not candidates:
        try:
            single = product_by_barcode(code)
            if single is not None:
                candidates = [single]
        except HTTPException:
            candidates = []
    # De-dup by case-insensitive SKU first (mirror + API can both
    # contribute, and the dead mirror's SKU casing drifts — same-SKU-
    # different-case IS the same product), variant id when there's no SKU.
    seen: set = set()
    unique = []
    for p in candidates:
        key = (p.get("sku") or "").strip().upper() \
            or p.get("shopify_variant_id")
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    unique.sort(key=_candidate_rank)
    return unique


@app.get(
    "/api/products/candidates", dependencies=[Depends(require_user)]
)
def product_candidates(barcode: str):
    items = products_by_barcode_all(barcode)
    return {"count": len(items), "candidates": items}


@app.get("/api/products/tags", dependencies=[Depends(require_user)])
def tags_for_product(
    sku: str | None = None,
    barcode: str | None = None,
    session: Session = Depends(get_session),
):
    """All RFID tags on file for a product, matched by exact SKU or barcode.
    (Anchored on SKU/barcode because TELCAN and the Shopify API identify
    variants differently; these two fields both sources agree on.)"""
    if not sku and not barcode:
        raise HTTPException(422, "Provide sku or barcode.")
    conditions = []
    if sku:
        conditions.append(RfidAssignment.sku == sku.strip())
    if barcode:
        conditions.append(RfidAssignment.barcode == barcode.strip())
    rows = session.scalars(
        select(RfidAssignment)
        .where(or_(*conditions))
        .order_by(RfidAssignment.assigned_at.desc())
    ).all()
    # Piggybacked flag: both scan stations already make this call right
    # after a product lookup, so the "won't scan" chip needs no extra trip.
    look = (sku or "").strip() or next(
        ((r.sku or "").strip() for r in rows if r.sku), ""
    )
    # Saved label lines + on-hand ride along: the Scan Station card makes
    # this call right after every lookup, so its label preview and the
    # "Shopify onhand" line need no extra round trips.
    custom = session.get(LabelName, look) if look else None
    if custom is None and look:
        custom = session.scalar(
            select(LabelName).where(func.upper(LabelName.sku) == look.upper())
        )
    on_hand = None
    if look:
        try:
            on_hand = _expected_qty(session, look)
        except Exception:  # noqa: BLE001 — decoration, never blocks a scan
            on_hand = None
    return {
        "count": len(rows),
        "assignments": [r.as_dict() for r in rows],
        "rfid_incompatible": bool(
            look and session.get(RfidIncompatible, look) is not None
        ),
        "on_hand": on_hand,
        "label_name": custom.label_name if custom else None,
        "label_placement": (custom.placement or "header") if custom else None,
        "label_sku_text": custom.sku_text if custom else None,
    }


# ---------------------------------------------------------- assignment API ---
@app.post(
    "/api/rfid-assignments",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def create_assignment(
    payload: AssignmentIn, session: Session = Depends(get_session)
):
    # A companion label (box 2..N of a multi-box unit) never becomes a
    # counting tie - not even by a deliberate Scan Station link.
    comp = session.scalar(
        select(CompanionTag).where(
            func.upper(CompanionTag.epc)
            == (payload.rfid_id or "").strip().upper()
        )
    )
    if comp is not None:
        raise HTTPException(
            409,
            f"That tag is the companion label for Box "
            f"{comp.box_no or '?'} of {comp.box_count or '?'} of "
            f"{comp.product_title or comp.sku or '?'} - it counts "
            "nowhere by design (the unit's tag is on Box 1). Nothing "
            "to link.",
        )
    assignment = RfidAssignment(**payload.model_dump())
    # Every real tag is a 96-bit EPC = 24 hex chars. Anything else is
    # probably a mangled read (e.g. Bluetooth relay dropping characters):
    # save it anyway, but flag it for a re-scan.
    assignment.suspect = (
        re.fullmatch(r"[0-9A-Fa-f]{24}", payload.rfid_id) is None
    )
    session.add(assignment)
    # A pairing answers the oldest open audit find for this SKU (the
    # tagless-box work queue from the C72 AUDIT tab), and an EPC off a
    # vendor strip retires its held-label note (the label found its box).
    _consume_audit_find(session, payload.sku)
    _consume_held_label(session, payload.rfid_id, payload.sku)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            409,
            f"RFID tag {payload.rfid_id} is already assigned. Unassign it "
            f"first to reassign.",
        )
    session.refresh(assignment)
    return assignment.as_dict()


class SweepAssignIn(BaseModel):
    """Bulk-scan: one sweep's worth of EPCs tied to ONE product. Same
    product fields as a single assignment; the EPC list replaces rfid_id."""

    epcs: list[str] = Field(min_length=1, max_length=200)
    shopify_variant_id: str = Field(max_length=64)
    shopify_product_id: str | None = Field(default=None, max_length=300)
    product_title: str = Field(max_length=255)
    variant_title: str | None = Field(default=None, max_length=255)
    sku: str | None = Field(default=None, max_length=100)
    barcode: str | None = Field(default=None, max_length=64)
    bin_location: str | None = Field(default=None, max_length=100)
    assigned_by: str | None = Field(default=None, max_length=100)


@app.post(
    "/api/rfid-assignments/sweep",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def sweep_assign(
    payload: SweepAssignIn, session: Session = Depends(get_session)
):
    """Assign every NEW tag a sweep heard to the loaded product, in one
    write. Already-assigned EPCs are skipped and named (the sweep will
    always hear neighbours), never stolen. All rows share one timestamp,
    which is what lets History fold the sweep into a single expandable
    event instead of N identical rows."""
    now = datetime.now(timezone.utc)
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in payload.epcs:
        epc = (raw or "").strip()
        if epc and epc.upper() not in seen:
            seen.add(epc.upper())
            cleaned.append(epc)
    if not cleaned:
        raise HTTPException(422, "No usable EPCs in that sweep.")
    existing = {
        r.rfid_id.upper(): r
        for r in session.scalars(
            select(RfidAssignment).where(
                func.upper(RfidAssignment.rfid_id).in_(
                    [e.upper() for e in cleaned]
                )
            )
        )
    }
    own_sku = (payload.sku or "").strip().upper()
    # Companion labels answer sweeps like any sticker; they're excluded
    # by name, never silently tied.
    _comp_rows, comp_epcs = _companions_heard(session, cleaned)
    assigned: list[RfidAssignment] = []
    duplicates: list[dict] = []
    companions_skipped: list[dict] = []
    for epc in cleaned:
        if epc.upper() in comp_epcs:
            companions_skipped.append(next(
                c for c in _comp_rows
                if (c["epc"] or "").upper() == epc.upper()
            ))
            continue
        row = existing.get(epc.upper())
        if row is not None:
            duplicates.append({
                "epc": epc,
                "sku": row.sku,
                "product_title": row.product_title,
                # Its own earlier tag answering ≠ someone else's box.
                "own": bool(own_sku
                            and (row.sku or "").strip().upper() == own_sku),
            })
            continue
        a = RfidAssignment(
            rfid_id=epc,
            shopify_variant_id=payload.shopify_variant_id,
            shopify_product_id=payload.shopify_product_id,
            product_title=payload.product_title,
            variant_title=payload.variant_title,
            sku=payload.sku,
            barcode=payload.barcode,
            bin_location=payload.bin_location,
            assigned_by=payload.assigned_by,
            assigned_at=now,
        )
        a.suspect = re.fullmatch(r"[0-9A-Fa-f]{24}", epc) is None
        session.add(a)
        assigned.append(a)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            409, "A tag in that sweep was assigned by someone else "
                 "mid-write — pull the sweep again.",
        )
    for a in assigned:
        session.refresh(a)
    return {
        "count": len(assigned),
        "assigned": [a.as_dict() for a in assigned],
        "duplicates": duplicates,
        "companions_skipped": companions_skipped,
    }


class SweepUndoIn(BaseModel):
    """Roll back one sweep's assignments — the blank-label rescue."""

    epcs: list[str] = Field(min_length=1, max_length=200)
    # Safety rail: only unlink tags that belong to THIS product.
    sku: str | None = Field(default=None, max_length=100)
    by: str | None = Field(default=None, max_length=100)


@app.post(
    "/api/rfid-assignments/sweep/undo",
    dependencies=[Depends(require_user)],
)
def sweep_undo(payload: SweepUndoIn, session: Session = Depends(get_session)):
    """Unlink the tags one sweep just assigned. Each unlink leaves the
    usual History receipt; sharing one timestamp folds them into a single
    expandable event, mirroring the sweep that made them."""
    now = datetime.now(timezone.utc)
    wanted = {(e or "").strip().upper() for e in payload.epcs if e}
    guard = (payload.sku or "").strip().upper()
    rows = session.scalars(
        select(RfidAssignment).where(
            func.upper(RfidAssignment.rfid_id).in_(sorted(wanted))
        )
    ).all()
    removed: list[str] = []
    skipped: list[str] = []
    for row in rows:
        if guard and (row.sku or "").strip().upper() != guard:
            skipped.append(row.rfid_id)
            continue
        session.add(BarcodeChange(
            sku=row.sku,
            product_title=row.product_title,
            shopify_variant_id=row.shopify_variant_id,
            changed_field="tag-unlinked",
            old_barcode=(row.rfid_id or "")[:64] or None,
            new_barcode=(row.bin_location or "")[:64] or None,
            changed_by=(payload.by or "").strip()[:100] or None,
            changed_at=now,
        ))
        removed.append(row.rfid_id)
        session.delete(row)
    session.commit()
    return {"count": len(removed), "epcs": removed, "skipped": skipped}


class TagChainIn(BaseModel):
    """Release/re-apply from History's Assigned Tag undo chain."""

    epcs: list[str] = Field(min_length=1, max_length=200)
    # Safety rail: only touch tags that belong to THIS product.
    sku: str | None = Field(default=None, max_length=100)
    by: str | None = Field(default=None, max_length=100)


@app.post("/api/tags/release", dependencies=[Depends(require_user)])
def tags_release(payload: TagChainIn, session: Session = Depends(get_session)):
    """History's Assigned Tag undo (Nick, 2026-08-25): release the tags,
    keeping a FULL snapshot of each assignment so the release itself can
    be undone (re-apply restores every field, original pairing date
    included). Each press is logged; release and re-apply may loop
    forever - both are manual, so there's no way to spin unattended."""
    now = datetime.now(timezone.utc)
    wanted = {(e or "").strip().upper() for e in payload.epcs if e}
    guard = (payload.sku or "").strip().upper()
    by = (payload.by or "").strip()[:100] or None
    rows = session.scalars(
        select(RfidAssignment).where(
            func.upper(RfidAssignment.rfid_id).in_(sorted(wanted))
        )
    ).all()
    released: list[str] = []
    skipped: list[str] = []
    for row in rows:
        if guard and (row.sku or "").strip().upper() != guard:
            skipped.append(row.rfid_id)
            continue
        # A stale snapshot for the same EPC (released, then re-paired by
        # hand instead of re-applied) yields to the fresh one.
        stale = session.scalar(
            select(ReleasedTag).where(
                func.upper(ReleasedTag.rfid_id)
                == (row.rfid_id or "").strip().upper()
            )
        )
        if stale is not None:
            session.delete(stale)
            session.flush()
        session.add(ReleasedTag(
            rfid_id=row.rfid_id,
            shopify_variant_id=row.shopify_variant_id,
            shopify_product_id=row.shopify_product_id,
            product_title=row.product_title,
            variant_title=row.variant_title,
            sku=row.sku,
            barcode=row.barcode,
            bin_location=row.bin_location,
            case_units=row.case_units,
            suspect=row.suspect,
            batch_id=row.batch_id,
            assigned_at=row.assigned_at,
            assigned_by=row.assigned_by,
            released_at=now,
            released_by=by,
        ))
        # One row per EPC, all sharing one timestamp - History folds them
        # into a single expandable event, mirroring the sweep that paired
        # them.
        session.add(BarcodeChange(
            sku=row.sku,
            product_title=row.product_title,
            shopify_variant_id=row.shopify_variant_id,
            changed_field="tag-released",
            old_barcode=(row.rfid_id or "")[:64] or None,
            new_barcode=(row.bin_location or "")[:64] or None,
            changed_by=by,
            changed_at=now,
        ))
        released.append(row.rfid_id)
        session.delete(row)
    if not released:
        raise HTTPException(
            422,
            "None of those tags are currently assigned"
            + (" to that product" if guard else "")
            + " - nothing to release.",
        )
    session.commit()
    return {
        "count": len(released),
        "epcs": released,
        "skipped": skipped,
        "message": (
            f"{len(released)} tag(s) released"
            + (f" ({len(skipped)} skipped - they belong to another product)"
               if skipped else "")
            + ". Undo lives in History: Released Tag > Undo re-applies them."
        ),
    }


@app.post("/api/tags/reapply", dependencies=[Depends(require_user)])
def tags_reapply(payload: TagChainIn, session: Session = Depends(get_session)):
    """Undo a release: re-create each assignment exactly from its stored
    snapshot - product, bin, case units, suspect flag, batch and the
    ORIGINAL pairing date/operator all come back, so counts and baselines
    read as if the release never happened. Logged per EPC (shared
    timestamp) as tag-reapplied, which History offers to undo again."""
    now = datetime.now(timezone.utc)
    wanted = {(e or "").strip().upper() for e in payload.epcs if e}
    guard = (payload.sku or "").strip().upper()
    by = (payload.by or "").strip()[:100] or None
    rows = session.scalars(
        select(ReleasedTag).where(
            func.upper(ReleasedTag.rfid_id).in_(sorted(wanted))
        )
    ).all()
    live = {
        (r.rfid_id or "").strip().upper()
        for r in session.scalars(
            select(RfidAssignment).where(
                func.upper(RfidAssignment.rfid_id).in_(sorted(wanted))
            )
        )
    }
    reapplied: list[str] = []
    skipped: list[str] = []
    for row in rows:
        key = (row.rfid_id or "").strip().upper()
        if guard and (row.sku or "").strip().upper() != guard:
            skipped.append(row.rfid_id)
            continue
        # The physical tag was claimed by something else while released -
        # never steal it back.
        if key in live:
            skipped.append(row.rfid_id)
            continue
        session.add(RfidAssignment(
            rfid_id=row.rfid_id,
            shopify_variant_id=row.shopify_variant_id,
            shopify_product_id=row.shopify_product_id,
            product_title=row.product_title,
            variant_title=row.variant_title,
            sku=row.sku,
            barcode=row.barcode,
            bin_location=row.bin_location,
            case_units=row.case_units,
            suspect=row.suspect,
            batch_id=row.batch_id,
            assigned_at=row.assigned_at or now,
            assigned_by=row.assigned_by,
        ))
        session.add(BarcodeChange(
            sku=row.sku,
            product_title=row.product_title,
            shopify_variant_id=row.shopify_variant_id,
            changed_field="tag-reapplied",
            old_barcode=(row.rfid_id or "")[:64] or None,
            new_barcode=(row.bin_location or "")[:64] or None,
            changed_by=by,
            changed_at=now,
        ))
        reapplied.append(row.rfid_id)
        session.delete(row)
    if not reapplied:
        raise HTTPException(
            422,
            "None of those tags are waiting to be re-applied - they were "
            "already re-applied, or the tag was since paired to something "
            "else.",
        )
    session.commit()
    return {
        "count": len(reapplied),
        "epcs": reapplied,
        "skipped": skipped,
        "message": (
            f"{len(reapplied)} tag(s) re-applied with their original "
            "pairing dates"
            + (f" ({len(skipped)} skipped - re-applied already or claimed "
               "by another product)" if skipped else "")
            + ". Undo lives in History: Assigned Tag > Undo releases them."
        ),
    }


@app.get("/api/rfid-assignments", dependencies=[Depends(require_user)])
def list_assignments(
    q: str | None = None,
    limit: int = 100,
    session: Session = Depends(get_session),
):
    """List assignments, optionally filtered by a free-text query that
    matches EPC, barcode, SKU, or product title."""
    stmt = select(RfidAssignment).order_by(RfidAssignment.assigned_at.desc())
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                RfidAssignment.rfid_id.ilike(like),
                RfidAssignment.barcode.ilike(like),
                RfidAssignment.sku.ilike(like),
                RfidAssignment.product_title.ilike(like),
            )
        )
    stmt = stmt.limit(min(limit, 500))
    rows = session.scalars(stmt).all()
    return {"count": len(rows), "assignments": [r.as_dict() for r in rows]}


@app.get(
    "/api/rfid-assignments/{rfid_id}", dependencies=[Depends(require_user)]
)
def get_assignment(rfid_id: str, session: Session = Depends(get_session)):
    row = session.scalar(
        select(RfidAssignment).where(RfidAssignment.rfid_id == rfid_id.strip())
    )
    if row is None:
        raise HTTPException(404, "No assignment for that RFID tag.")
    return row.as_dict()


@app.get(
    "/api/tag-info/{rfid_id}", dependencies=[Depends(require_user)]
)
def tag_info(rfid_id: str, session: Session = Depends(get_session)):
    """Everything worth knowing about ONE tag, for "what is this sticker?"
    on the gun: its product, how many tags that product has and how many
    sit in this bin, whether the live catalog still knows the SKU, and
    whether the recorded bin still agrees with Shopify.

    A tag nobody owns is answered, not 404'd — an unpaired printed label
    is a real thing to find on a box, and saying so beats "not found"."""
    epc = (rfid_id or "").strip()
    if not epc:
        raise HTTPException(422, "Which tag?")
    row = session.scalar(
        select(RfidAssignment).where(
            func.upper(RfidAssignment.rfid_id) == epc.upper()
        )
    )
    notes: list[str] = []
    if row is None:
        # A companion tag (box 2..N of a multi-box unit) is a known
        # sticker with a name - it counts nowhere on purpose.
        comp = session.scalar(
            select(CompanionTag).where(
                func.upper(CompanionTag.epc) == epc.upper()
            )
        )
        if comp is not None:
            notes.append(
                f"Companion label: box {comp.box_no or '?'} of "
                f"{comp.box_count or '?'} of "
                f"{comp.product_title or comp.sku or '?'}"
                + (f", lives in {comp.bin_location}"
                   if comp.bin_location else "")
                + ". The unit's counting tag is on box 1 - this one "
                "counts nowhere, by design."
            )
            return {
                "found": False, "printed_only": False, "epc": epc,
                "companion": comp.as_dict(), "print_job": None,
                "notes": notes,
            }
        # Printed but never paired: the label exists, the tie doesn't.
        job = session.scalar(
            select(PrintJob).where(func.upper(PrintJob.epc) == epc.upper())
        )
        if job is not None:
            notes.append(
                f"Printed for {job.sku or job.product_title or '?'} "
                f"(job #{job.id}, {job.status}) but never paired — stick it "
                f"on that box and pair it, or void the label."
            )
            return {
                "found": False, "printed_only": True, "epc": epc,
                "print_job": job.as_dict(), "notes": notes,
            }
        notes.append(
            "This tag isn't in the system at all — a blank sticker, or one "
            "from another store. Pairing it in a batch will claim it."
        )
        return {"found": False, "printed_only": False, "epc": epc,
                "print_job": None, "notes": notes}

    sku_key = (row.sku or "").strip().upper()
    bin_key = (row.bin_location or "").strip().lower()
    siblings = session.scalars(
        select(RfidAssignment).where(
            func.upper(RfidAssignment.sku) == sku_key
        )
    ).all() if sku_key else [row]
    tags_here = sum(
        1 for t in siblings
        if (t.bin_location or "").strip().lower() == bin_key
    ) if bin_key else 0

    # Does the LIVE catalog still know this SKU? The dead mirror let a
    # renamed product get tagged under its old SKU (DB24010501 for what
    # Shopify now calls F9394B), and those tags are orphans.
    live = session.scalars(
        select(BinMapEntry).where(func.upper(BinMapEntry.sku) == sku_key)
    ).all() if sku_key else []
    live_bins = sorted({(e.bin or "").strip() for e in live if e.bin})
    if sku_key and not live:
        notes.append(
            f"Shopify has no product with SKU {row.sku} any more — this "
            f"tag is an orphan (usually an old SKU that was renamed). "
            f"Unlinking it is normally the right move."
        )
    elif live_bins and bin_key and not any(
        b.lower() == bin_key for b in live_bins
    ):
        notes.append(
            f"This tag says bin {row.bin_location}, but Shopify now puts "
            f"{row.sku} in {', '.join(live_bins)}."
        )
    if row.suspect:
        notes.append("Flagged as a SUSPECT read when it was paired — the "
                     "EPC doesn't look like a normal tag.")
    if (row.case_units or 0) > 1:
        notes.append(f"Sealed case: this ONE tag counts as "
                     f"{row.case_units} units.")
    if sku_key and sku_key in _noscan_skus(session):
        notes.append("Product is flagged \"won't RFID scan on box\" — "
                     "sweeps don't expect it to answer.")

    batch = session.get(Batch, row.batch_id) if row.batch_id else None
    entry = live[0] if live else None
    return {
        "found": True,
        "printed_only": False,
        "epc": row.rfid_id,
        "assignment": row.as_dict(),
        "tags_total": len(siblings),
        "tags_here": tags_here,
        "live_sku_exists": bool(live),
        "live_bins": live_bins,
        "image_url": entry.image_url if entry else None,
        "expected_qty": entry.qty if entry else None,
        "batch": (
            {"id": batch.id, "bin_name": batch.bin_name,
             "status": batch.status}
            if batch else None
        ),
        "notes": notes,
    }


@app.delete(
    "/api/rfid-assignments/{rfid_id}",
    status_code=204,
    dependencies=[Depends(require_user)],
)
def unassign(
    rfid_id: str,
    by: str | None = None,
    session: Session = Depends(get_session),
):
    row = session.scalar(
        select(RfidAssignment).where(RfidAssignment.rfid_id == rfid_id.strip())
    )
    if row is None:
        raise HTTPException(404, "No assignment for that RFID tag.")
    # The tie IS the record — deleting it used to erase the fact that it
    # ever existed, so an unlink left no trace anywhere. History keeps the
    # receipt: which tag, which product, who pulled it.
    session.add(BarcodeChange(
        sku=row.sku,
        product_title=row.product_title,
        shopify_variant_id=row.shopify_variant_id,
        changed_field="tag-unlinked",
        old_barcode=(row.rfid_id or "")[:64] or None,
        new_barcode=(row.bin_location or "")[:64] or None,
        changed_by=(by or "").strip()[:100] or None,
    ))
    session.delete(row)
    session.commit()


# ------------------------------------------------------------ print queue ---
# Any device queues jobs; print_agent.py on the printer laptop claims them,
# drives the Zebra (print + RFID encode in one pass), and reports back.
# Success auto-creates the RfidAssignment — printed labels need no tag scan.

def require_agent_key(x_agent_key: str | None = Header(default=None)):
    """Protects agent endpoints when PRINT_AGENT_KEY is configured."""
    if config.PRINT_AGENT_KEY and x_agent_key != config.PRINT_AGENT_KEY:
        raise HTTPException(401, "Missing or wrong X-Agent-Key header.")


def _new_epc() -> str:
    """Random 96-bit EPC as 24 uppercase hex chars. Uniqueness is enforced
    by the DB; the collision odds on random 96 bits are negligible."""
    return secrets.token_hex(12).upper()


# ------------------------------------------------------- multi-box units ---
# One sellable unit, several cartons (Nick, 2026-09-02, the S11740).
# The invariant everything counts on is ONE tag per unit, so box 1 gets
# the counting label and boxes 2..N get COMPANION labels: printed with
# "BOX X OF Y" and that box's own bin, registered as CompanionTags
# (recognized by sweeps, counted nowhere), never RfidAssignments.

def _multibox_map(
    session: Session, skus: list[str | None]
) -> dict[str, MultiboxProduct]:
    wanted = sorted({
        (s or "").strip().upper() for s in skus if (s or "").strip()
    })
    if not wanted:
        return {}
    return {
        (r.sku or "").strip().upper(): r
        for r in session.scalars(
            select(MultiboxProduct).where(
                func.upper(MultiboxProduct.sku).in_(wanted)
            )
        )
    }


# The box note rides the BIN line ("BIN: B11-1, Box 1 of 2" - Nick,
# 2026-09-02: the header stays Telescopes Canada / the saved name).
# It must never leak into recorded bins, so everything that copies a
# job's bin into a record strips it first.
_BOX_NOTE_RE = re.compile(r",?\s*Box (\d+) of (\d+)\s*$", re.IGNORECASE)


def _strip_box_note(bin_location: str | None) -> str | None:
    return _BOX_NOTE_RE.sub("", bin_location or "").strip() or None


def _expand_multibox(
    session: Session, jobs: list[PrintJob]
) -> list[PrintJob]:
    """Give every counting label of a multi-box product its per-box bin
    line ("BIN: B11-1, Box 1 of 2" - the header line stays untouched),
    and append the companion labels for boxes 2..Y right behind it
    (label order = sticking order). Jobs that are already companions,
    or case labels, pass through."""
    marks = _multibox_map(session, [j.sku for j in jobs])
    if not marks:
        return jobs
    out: list[PrintJob] = []
    for job in jobs:
        out.append(job)
        mark = marks.get((job.sku or "").strip().upper())
        if (mark is None or mark.boxes_per_unit <= 1
                or job.kind == "companion" or job.case_units):
            continue
        bins = mark.bin_list()

        def box_bin(i: int) -> str:
            b = bins[i - 1] if len(bins) >= i else None
            return (b or "").strip() or (job.bin_location or "").strip()

        total = mark.boxes_per_unit
        job.bin_location = (
            f"{box_bin(1)}, Box 1 of {total}"[:100]
        )
        for i in range(2, total + 1):
            out.append(PrintJob(
                epc=_new_epc(),
                status="pending",
                kind="companion",
                barcode=job.barcode,
                sku=job.sku,
                product_title=job.product_title,
                variant_title=job.variant_title,
                bin_location=f"{box_bin(i)}, Box {i} of {total}"[:100],
                other_bins=job.other_bins,
                shopify_variant_id=job.shopify_variant_id,
                shopify_product_id=job.shopify_product_id,
                label_name=job.label_name,
                label_placement=job.label_placement,
                label_sku=job.label_sku,
                requested_by=job.requested_by,
                batch_id=job.batch_id,
                printer=job.printer,
                print_session=job.print_session,
            ))
    return out


# Filter for every count of a batch's printed labels: companion labels
# are physical stickers but never counting units.
_NOT_COMPANION = (PrintJob.kind.is_(None)) | (PrintJob.kind != "companion")


def _companions_heard(
    session: Session, epcs: list[str]
) -> tuple[list[dict], set[str]]:
    """Which of these swept EPCs are companion tags: display info plus
    the EPC set to drop from every unknown list."""
    if not epcs:
        return [], set()
    rows = session.scalars(
        select(CompanionTag).where(
            func.upper(CompanionTag.epc).in_(sorted(
                {(e or "").upper() for e in epcs}
            ))
        )
    ).all()
    return ([r.as_dict() for r in rows],
            {(r.epc or "").upper() for r in rows})


@app.get("/api/multibox/{sku}", dependencies=[Depends(require_user)])
def get_multibox(sku: str, session: Session = Depends(get_session)):
    row = session.scalar(
        select(MultiboxProduct).where(
            func.upper(MultiboxProduct.sku) == sku.strip().upper()
        )
    )
    return {"multibox": row.as_dict() if row else None}


class MultiboxIn(BaseModel):
    boxes_per_unit: int = Field(ge=1, le=20)
    # Per-box bins, index 0 = box 1; null/empty entries = not known yet
    # (they fall back to the product's own bin on labels).
    bins: list[str | None] | None = Field(default=None, max_length=20)
    updated_by: str | None = Field(default=None, max_length=100)


@app.put("/api/multibox/{sku}", dependencies=[Depends(require_user)])
def set_multibox(
    sku: str, payload: MultiboxIn, session: Session = Depends(get_session)
):
    """Mark a product as one-unit-several-cartons (or clear it with
    boxes_per_unit 1). Durable and store-wide: labels, audits and
    receiving all read it from here on."""
    key = sku.strip()
    if not key:
        raise HTTPException(422, "A SKU is required.")
    row = session.scalar(
        select(MultiboxProduct).where(
            func.upper(MultiboxProduct.sku) == key.upper()
        )
    )
    old = row.boxes_per_unit if row else 1
    if payload.boxes_per_unit <= 1:
        if row is not None:
            session.delete(row)
    else:
        bins = [
            ((b or "").strip() or None)
            for b in (payload.bins or [])
        ][:payload.boxes_per_unit]
        if row is None:
            row = MultiboxProduct(sku=key)
            session.add(row)
        row.boxes_per_unit = payload.boxes_per_unit
        row.bins = json.dumps(bins) if any(bins) else None
        row.updated_by = payload.updated_by
        row.updated_at = datetime.now(timezone.utc)
    if old != payload.boxes_per_unit:
        session.add(BarcodeChange(
            sku=key,
            product_title=f"{key} (multi-box mark)",
            changed_field="multibox",
            old_barcode=f"{old} box(es) per unit"[:64],
            new_barcode=f"{payload.boxes_per_unit} box(es) per unit"[:64],
            changed_by=(payload.updated_by or "").strip()[:100] or None,
        ))
    session.commit()
    return {
        "multibox": row.as_dict()
        if payload.boxes_per_unit > 1 else None,
        "message": (
            f"{key}: 1 unit = {payload.boxes_per_unit} boxes - box 1 "
            "carries the counting label, the rest print companion "
            "labels."
            if payload.boxes_per_unit > 1
            else f"{key}: back to one box per unit."
        ),
    }


class PrintJobIn(BaseModel):
    quantity: int = Field(default=1, ge=1, le=100)
    shopify_variant_id: str = Field(max_length=64)
    shopify_product_id: str | None = Field(default=None, max_length=300)
    product_title: str = Field(max_length=255)
    variant_title: str | None = Field(default=None, max_length=255)
    sku: str | None = Field(default=None, max_length=100)
    barcode: str | None = Field(default=None, max_length=64)
    bin_location: str | None = Field(default=None, max_length=100)
    other_bins: str | None = Field(default=None, max_length=255)
    label_name: str | None = Field(default=None, max_length=255)
    label_placement: str | None = Field(
        default=None, pattern="^(header|sku|both)$"
    )
    label_sku: str | None = Field(default=None, max_length=56)
    requested_by: str | None = Field(default=None, max_length=100)
    # Target printer (rfid_printers.name); omitted = any agent prints it.
    printer: str | None = Field(default=None, max_length=100)
    # One token per product LOAD at the Scan Station: every print pressed
    # before the next barcode reset shares it, and the Queue tab groups
    # a product's loose jobs by it.
    print_session: str | None = Field(default=None, max_length=24)

    @field_validator("shopify_variant_id", "product_title")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


@app.post(
    "/api/print-jobs", status_code=201, dependencies=[Depends(require_user)]
)
def create_print_jobs(
    payload: PrintJobIn, session: Session = Depends(get_session)
):
    """Queue N labels for one product; each gets its own EPC.

    Label content: an explicit label_name in the payload wins (the serial
    flow sends the operator-confirmed name). Otherwise the SAVED label
    lines apply — the batch flows always consulted the store but this
    endpoint trusted the client, so a Scan Station print quietly ignored
    an updated SKU line (Nick, 2026-08-25, ZWO Softbag1)."""
    fields = payload.model_dump(exclude={"quantity"})
    if not (fields.get("label_name") or "").strip() and (
        payload.sku or ""
    ).strip():
        custom = session.get(LabelName, payload.sku.strip()) or session.scalar(
            select(LabelName).where(
                func.upper(LabelName.sku) == payload.sku.strip().upper()
            )
        )
        if custom is not None:
            fields["label_name"] = custom.label_name
            fields["label_placement"] = custom.placement or "header"
            fields["label_sku"] = custom.sku_text
    jobs = [
        PrintJob(epc=_new_epc(), status="pending", **fields)
        for _ in range(payload.quantity)
    ]
    # Multi-box units: per-box labels ride along ("BOX 2 OF 2", its own
    # bin) as companion jobs - physical stickers, counting nowhere.
    jobs = _expand_multibox(session, jobs)
    session.add_all(jobs)
    session.commit()
    for job in jobs:
        session.refresh(job)
    return {"count": len(jobs), "jobs": [j.as_dict() for j in jobs]}


@app.get("/api/print-jobs", dependencies=[Depends(require_user)])
def list_print_jobs(
    status: str | None = None,
    ids: str | None = None,
    batch_id: int | None = None,
    limit: int = 50,
    session: Session = Depends(get_session),
):
    stmt = select(PrintJob).order_by(PrintJob.id.desc())
    if status:
        stmt = stmt.where(PrintJob.status == status.strip())
    if batch_id is not None:
        stmt = stmt.where(PrintJob.batch_id == batch_id)
    if ids:
        try:
            id_list = [int(i) for i in ids.split(",") if i.strip()]
        except ValueError:
            raise HTTPException(422, "ids must be comma-separated integers.")
        stmt = stmt.where(PrintJob.id.in_(id_list))
    rows = session.scalars(stmt.limit(min(limit, 200))).all()
    # The Queue tab groups jobs under their batch; the batch's bin, kind
    # and status ride along so the group headers need no extra calls.
    batch_ids = {j.batch_id for j in rows if j.batch_id}
    batches = {
        b.id: {"bin_name": b.bin_name, "kind": b.kind, "status": b.status,
               "created_by": b.created_by}
        for b in session.scalars(
            select(Batch).where(Batch.id.in_(batch_ids))
        )
    } if batch_ids else {}
    return {
        "count": len(rows),
        "jobs": [j.as_dict() for j in rows],
        "batches": batches,
        # Enables the Resume button independent of the listing's limit.
        "resumable_stopped": len(_resumable_stopped_jobs(session)),
    }


def _resumable_stopped_jobs(session: Session) -> list[PrintJob]:
    """Jobs a Stop press canceled that can simply run again: they never
    printed, their EPCs were never used, and their batch (if any) is
    still alive. Ordered by id - the original print order."""
    rows = session.scalars(
        select(PrintJob).where(
            PrintJob.status == "canceled",
            PrintJob.error == "stopped by operator",
        ).order_by(PrintJob.id)
    ).all()
    alive: dict[int, bool] = {}
    out = []
    for job in rows:
        if job.batch_id:
            if job.batch_id not in alive:
                b = session.get(Batch, job.batch_id)
                alive[job.batch_id] = (
                    b is not None and b.status not in ("done", "abandoned")
                )
            if not alive[job.batch_id]:
                continue
        out.append(job)
    return out


# Print-agent heartbeat: the agent polls claim every ~10 s, so a recent
# claim means the printer PC is up. In-memory is fine — after an app
# restart the next poll repopulates it within seconds.
_agent_last_seen: float | None = None

# What a claim from an agent too old to send --printer-id registers as.
# Keeps the single-printer warehouse on the picker without touching it.
DEFAULT_PRINTER = "warehouse-zebra"
PRINTER_ONLINE_SECONDS = 120  # agent polls every ~3 s; be generous


# --- printer commands (re-align etc.) ---------------------------------------
# Transient, in-memory: a command is a one-shot nudge ("feed to the next
# label's home") the NEXT agent poll picks up. Lost on app restart by
# design — the operator just presses the button again. Old agents never
# see this endpoint, so nothing changes until the agent is updated AND
# someone presses the button (Nick, 2026-08-25: no tags may be wasted
# without a human asking for it).
_printer_commands: dict[str, list[dict]] = {}


class PrinterCommandIn(BaseModel):
    printer: str | None = Field(default=None, max_length=100)
    # feed = re-align to the next label's home; purge = delete every
    # job stuck in the warehouse PC's WINDOWS print queue (the wedge
    # remedy - server-side jobs are already done, reprints cover any
    # label that never came out).
    kind: Literal["feed", "purge"] = "feed"
    requested_by: str | None = Field(default=None, max_length=100)


@app.post(
    "/api/printer-commands",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def queue_printer_command(payload: PrinterCommandIn):
    """Queue a one-shot printer command. 'feed' = slew to the next
    label's home position (ZPL ~PH): re-registers the media after a rip
    pulled the liner forward, at the cost of the one already-disturbed
    label instead of two misprints plus a blank."""
    name = (payload.printer or DEFAULT_PRINTER).strip()
    _printer_commands.setdefault(name, []).append({
        "kind": payload.kind,
        "requested_by": payload.requested_by,
        "at": time.time(),
    })
    return {"queued": payload.kind, "printer": name}


# Only re-align-capable agents (print_agent v2+) poll the commands
# endpoint, so a recent poll IS the capability proof — the UI can say
# outright whether the warehouse PC has been restarted on the new code
# instead of everyone guessing (Nick, 2026-08-25: "still not
# re-aligning" and no way to tell why).
_commands_last_polled: dict[str, float] = {}
_agent_versions: dict[str, str] = {}
# v4 agents report their WINDOWS queue's health on every poll: jobs
# waiting + the oldest one's age. The server marks a job done the
# moment it reaches Windows, so this is the only wedge detector
# (Nick, 2026-09-01: the ZD220 sat "online" all morning while 16
# labels piled behind a Printing-Retained head).
_printer_win_queue: dict[str, dict] = {}
# A Windows job older than this while the queue is non-empty = wedged.
# The head normally clears in seconds; 90s absorbs a long burst.
PRINTER_WEDGE_SECONDS = 90


@app.post(
    "/api/printer-commands/claim", dependencies=[Depends(require_agent_key)]
)
def claim_printer_commands(
    printer: str | None = None,
    agent_version: str | None = None,
    win_jobs: int | None = None,
    win_oldest_s: int | None = None,
):
    """Agent: take (and clear) any pending commands for this printer.
    Commands older than 10 minutes are dropped - a stale re-align from a
    forgotten press must not move media out of the blue."""
    name = (printer or DEFAULT_PRINTER).strip()
    now = time.time()
    _commands_last_polled[name] = now
    if agent_version:
        _agent_versions[name] = agent_version.strip()[:20]
    if win_jobs is not None:
        _printer_win_queue[name] = {
            "jobs": max(0, win_jobs),
            "oldest_s": max(0, win_oldest_s or 0),
            "at": now,
        }
    cmds = [
        c for c in _printer_commands.pop(name, [])
        if now - c["at"] < 600
    ]
    return {"count": len(cmds), "commands": cmds}


@app.get("/api/print-agent/status", dependencies=[Depends(require_user)])
def print_agent_status():
    seen = _agent_last_seen
    cmd_seen = max(_commands_last_polled.values(), default=None)
    version = (
        next(iter(_agent_versions.values()), None)
        if _agent_versions else None
    )
    # Freshest Windows-queue report (v4 agents; None until one polls).
    win = None
    for w in _printer_win_queue.values():
        if win is None or w["at"] > win["at"]:
            win = w
    win_fresh = win is not None and time.time() - win["at"] < 35
    return {
        "online": seen is not None and time.time() - seen < 35,
        "last_seen_seconds": (
            None if seen is None else int(time.time() - seen)
        ),
        # True only while an updated agent is actively polling for
        # commands — the re-align button and the ~JSB backfeed fix do
        # nothing until this is true.
        "realign_capable": (
            cmd_seen is not None and time.time() - cmd_seen < 35
        ),
        "agent_version": version,
        # Purge (clear the Windows queue) needs a v4+ agent polling now.
        "purge_capable": (
            win_fresh or (
                cmd_seen is not None and time.time() - cmd_seen < 35
                and (version or "").isdigit() and int(version) >= 4
            )
        ),
        "win_jobs": win["jobs"] if win_fresh else None,
        "win_oldest_seconds": win["oldest_s"] if win_fresh else None,
        # Wedged = labels sitting in the WINDOWS queue far longer than a
        # burst takes: the physical printer stopped taking data while
        # everything server-side reads "done". Power-cycle the printer,
        # or clear the stuck jobs and reprint.
        "wedged": (
            win_fresh and win["jobs"] > 0
            and win["oldest_s"] > PRINTER_WEDGE_SECONDS
        ),
    }


@app.get("/api/print-agent/script", dependencies=[Depends(require_user)])
def print_agent_script():
    """The CURRENT print_agent.py, served by the app itself so updating
    the warehouse PC never involves hunting for the repo: download this
    (station link works in a browser), replace the file next to the
    scheduled task, restart the task."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "print_agent.py",
    )
    if not os.path.isfile(path):
        raise HTTPException(404, "print_agent.py isn't in this deployment.")
    return FileResponse(
        path, media_type="text/x-python", filename="print_agent.py"
    )


@app.get("/api/printers", dependencies=[Depends(require_user)])
def list_printers(session: Session = Depends(get_session)):
    """Every printer whose agent has ever checked in, with liveness.

    Rows are created by agent claims, never by hand — "detected" printers
    in the picker's sense. online = a claim within the last 2 minutes."""
    now = datetime.utcnow()
    printers = []
    for p in session.scalars(select(Printer).order_by(Printer.name)).all():
        seen = p.last_seen
        if seen is not None and seen.tzinfo is not None:
            seen = seen.astimezone(timezone.utc).replace(tzinfo=None)
        age = None if seen is None else (now - seen).total_seconds()
        printers.append({
            **p.as_dict(),
            "online": age is not None and age < PRINTER_ONLINE_SECONDS,
            "last_seen_seconds": None if age is None else int(age),
        })
    return {"count": len(printers), "printers": printers}


def _touch_printer(session: Session, name: str, kind: str | None) -> None:
    """Upsert the claiming agent's printer row (detection + liveness).

    Throttled (2026-08-26): the agent claims every ~3s around the clock,
    and stamping last_seen on every poll meant a write transaction
    every 3 seconds against a 5-DTU database - a real slice of the DTU
    saturation that made the C72 crawl. Liveness reads a 120s window
    (PRINTER_ONLINE_SECONDS), so refreshing the stamp every 45s loses
    nothing."""
    row = session.scalars(
        select(Printer).where(Printer.name == name)
    ).first()
    if row is None:
        row = Printer(name=name)
        session.add(row)
    if kind and row.kind != kind[:100]:
        row.kind = kind[:100]
    now = datetime.utcnow()
    # Azure SQL hands last_seen back tz-AWARE; naive-minus-aware raises
    # and 500'd every claim after the first stamp (the printer showed
    # offline while the agent ran fine - Nick, 2026-08-26). Same
    # normalization list_printers uses. sqlite (tests) stays naive.
    seen = row.last_seen
    if seen is not None and seen.tzinfo is not None:
        seen = seen.astimezone(timezone.utc).replace(tzinfo=None)
    if seen is None or (now - seen).total_seconds() > 45:
        row.last_seen = now


@app.post("/api/print-jobs/claim", dependencies=[Depends(require_agent_key)])
def claim_print_jobs(
    limit: int = 5,
    printer: str | None = None,
    kind: str | None = None,
    session: Session = Depends(get_session),
):
    """Agent: take the oldest pending jobs and mark them printing.

    An agent that names its printer claims only jobs aimed at it (or at
    no printer in particular). A legacy agent names nothing: it registers
    as DEFAULT_PRINTER and claims EVERYTHING, exactly as before the
    picker existed — right for a warehouse with one physical printer."""
    global _agent_last_seen
    _agent_last_seen = time.time()
    printer = (printer or "").strip()[:100] or None
    _touch_printer(session, printer or DEFAULT_PRINTER, kind)
    stmt = (
        select(PrintJob)
        .where(PrintJob.status == "pending")
        .order_by(PrintJob.id)
        .limit(min(limit, 20))
    )
    if printer:
        stmt = stmt.where(
            (PrintJob.printer.is_(None)) | (PrintJob.printer == printer)
        )
    rows = session.scalars(stmt).all()
    for job in rows:
        job.status = "printing"
    session.commit()
    return {"count": len(rows), "jobs": [j.as_dict() for j in rows]}


class StopPrintingIn(BaseModel):
    requested_by: str | None = Field(default=None, max_length=100)


@app.post("/api/print-jobs/stop", dependencies=[Depends(require_user)])
def stop_printing(
    payload: StopPrintingIn, session: Session = Depends(get_session)
):
    """The Queue tab's Stop printing button (Nick, 2026-08-25): the
    printer is spewing (wax out, wrong labels, jam) and the run must
    halt NOW. Cancels every job still waiting - the agent's next claim
    (it polls every few seconds and takes at most 5 labels per burst)
    then comes back empty, so at most the burst in flight finishes.
    Canceled labels reprint from the normal Queue/batch reprint flows;
    nothing already printed is touched."""
    rows = session.scalars(
        select(PrintJob).where(PrintJob.status == "pending")
    ).all()
    in_flight = session.scalar(
        select(func.count()).where(PrintJob.status == "printing")
    ) or 0
    if not rows and not in_flight:
        raise HTTPException(422, "Nothing is printing or waiting to print.")
    for job in rows:
        job.status = "canceled"
        job.error = "stopped by operator"
    session.add(BarcodeChange(
        product_title="Print queue",
        changed_field="print-stop",
        old_barcode=f"{len(rows)} job(s) canceled"[:64],
        new_barcode=(f"{in_flight} in flight finished"
                     if in_flight else "queue was drained")[:64],
        changed_by=(payload.requested_by or "").strip()[:100] or None,
    ))
    session.commit()
    return {
        "canceled": len(rows),
        "in_flight": in_flight,
        "message": (
            f"{len(rows)} queued label(s) canceled."
            + (f" Up to {in_flight} label(s) already claimed by the "
               "printer may still come out." if in_flight else "")
            + " Resume printing brings the whole stopped run back in "
              "order, or reprint single labels from the Queue."
        ),
    }


@app.post("/api/print-jobs/resume", dependencies=[Depends(require_user)])
def resume_printing(
    payload: StopPrintingIn, session: Session = Depends(get_session)
):
    """The Stop button's inverse (Nick, 2026-08-26: stopped a run while
    the printer looked broken, then had no way back but label-by-label).
    A stopped job never printed and its EPC was never used, so it simply
    returns to pending - the SAME rows, so the original ids keep the
    original print order, and the agent takes them ahead of anything
    queued since. Jobs whose batch has since finished or been abandoned
    stay canceled. Stop and Resume can loop forever; both are manual."""
    rows = _resumable_stopped_jobs(session)
    if not rows:
        raise HTTPException(
            422, "No stopped labels to resume - nothing was stopped, or "
                 "their batches have since finished."
        )
    for job in rows:
        job.status = "pending"
        job.error = None
    session.add(BarcodeChange(
        product_title="Print queue",
        changed_field="print-resume",
        old_barcode=f"{len(rows)} job(s) resumed"[:64],
        new_barcode="original order kept"[:64],
        changed_by=(payload.requested_by or "").strip()[:100] or None,
    ))
    session.commit()
    return {
        "resumed": len(rows),
        "message": (
            f"{len(rows)} stopped label(s) are back in the queue in "
            f"their original order - the printer picks them up on its "
            f"next pass."
        ),
    }


# --- Refresh timing ---------------------------------------------------------
# Every refresh button on the site (manual or automatic) reports how long
# it took; the stats endpoint serves a recent median per kind so buttons
# can show "Estimated N seconds" with a fill that means something.
# Server-side autos mark themselves running in rfid_app_settings
# ("refresh_running:<kind>") so a page that loads mid-refresh picks the
# animation up at the right fill level instead of at zero.

class RefreshLogIn(BaseModel):
    kind: str = Field(max_length=60)
    source: str = Field(default="manual", pattern="^(manual|auto)$")
    ms: int = Field(ge=0, le=3_600_000)


@app.post(
    "/api/refresh-log", status_code=201, dependencies=[Depends(require_user)]
)
def log_refresh(payload: RefreshLogIn, session: Session = Depends(get_session)):
    session.add(RefreshLog(
        kind=payload.kind.strip(), source=payload.source, ms=payload.ms
    ))
    # Keep ~50 rows per kind — the estimate only ever reads the newest 7.
    old_ids = session.scalars(
        select(RefreshLog.id)
        .where(RefreshLog.kind == payload.kind.strip())
        .order_by(RefreshLog.id.desc())
        .offset(50)
    ).all()
    if old_ids:
        session.execute(delete(RefreshLog).where(RefreshLog.id.in_(old_ids)))
    session.commit()
    return {"ok": True}


def _mark_refresh_running(session: Session, kind: str) -> None:
    """Server-side auto refresh started — visible to every open page."""
    key = f"refresh_running:{kind}"
    row = session.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key)
        session.add(row)
    row.value = datetime.utcnow().isoformat()


def _clear_refresh_running(session: Session, kind: str, ms: int) -> None:
    """Auto refresh finished: clear the marker and log the duration."""
    row = session.get(AppSetting, f"refresh_running:{kind}")
    if row is not None:
        session.delete(row)
    session.add(RefreshLog(kind=kind, source="auto", ms=ms))


@app.get("/api/refresh-stats", dependencies=[Depends(require_user)])
def refresh_stats(session: Session = Depends(get_session)):
    """Median duration per refresh kind + which autos are running NOW."""
    stats: dict[str, int] = {}
    for kind in session.scalars(select(RefreshLog.kind).distinct()).all():
        recent = session.scalars(
            select(RefreshLog.ms)
            .where(RefreshLog.kind == kind)
            .order_by(RefreshLog.id.desc())
            .limit(7)
        ).all()
        if recent:
            stats[kind] = sorted(recent)[len(recent) // 2]
    running: dict[str, str] = {}
    for row in session.scalars(
        select(AppSetting).where(AppSetting.key.like("refresh_running:%"))
    ).all():
        started = None
        try:
            started = datetime.fromisoformat(row.value or "")
        except ValueError:
            pass
        # A marker older than 30 min is a crashed run, not a live one.
        if started and (datetime.utcnow() - started).total_seconds() < 1800:
            running[row.key.split(":", 1)[1]] = row.value
    return {"stats": stats, "running": running}


# --- Orders sync (sold detection) -------------------------------------------
# Read-only against Shopify; the daily 8 AM run lives in app/orders_sync.
# The manual button on the Review tab calls run; both paths share the
# refresh-stats kind "orders-sync" so the button animates either way.

@app.post("/api/orders-sync/run", dependencies=[Depends(require_user)])
def orders_sync_run(session: Session = Depends(get_session)):
    return orders_sync.run(session, source="manual")


@app.get("/api/orders-sync/status", dependencies=[Depends(require_user)])
def orders_sync_status(session: Session = Depends(get_session)):
    return orders_sync.current_status(session)


class MarkSoldIn(BaseModel):
    sku: str = Field(max_length=100)
    epcs: list[str] = Field(min_length=1, max_length=200)
    changed_by: str | None = Field(default=None, max_length=100)


@app.post(
    "/api/assignments/mark-sold", dependencies=[Depends(require_user)]
)
def mark_assignments_sold(
    payload: MarkSoldIn, session: Session = Depends(get_session)
):
    """An audit's verdict: these tags' boxes SHIPPED. Removes the
    assignments (History-logged as tag-sold, one row per tag) and retires
    the matching units from the sold ledger, oldest sales first. Local
    records only — Shopify is never touched; its on-hand already dropped
    when the orders fulfilled."""
    sku = payload.sku.strip()
    uppers = {(e or "").strip().upper() for e in payload.epcs if e}
    rows = session.scalars(
        select(RfidAssignment).where(
            func.upper(RfidAssignment.rfid_id).in_(sorted(uppers))
        )
    ).all()
    if not rows:
        raise HTTPException(404, "None of those tags are on file.")
    wrong = [r.rfid_id for r in rows
             if (r.sku or "").strip().upper() != sku.upper()]
    if wrong:
        raise HTTPException(
            409,
            f"{len(wrong)} of those tags belong to a different product "
            f"({wrong[0]} …) — refusing to mark them sold as {sku}.",
        )
    units = 0
    for r in rows:
        units += r.case_units or 1
        session.add(BarcodeChange(
            sku=r.sku,
            product_title=r.product_title,
            shopify_variant_id=r.shopify_variant_id,
            changed_field="tag-sold",
            old_barcode=(r.rfid_id or "")[:64] or None,
            new_barcode=(r.bin_location or "")[:64] or None,
            changed_by=(payload.changed_by or "").strip()[:100] or None,
        ))
        session.delete(r)
    retired = orders_sync.retire_units(session, sku, units)
    session.commit()
    return {
        "removed_tags": len(rows),
        "units": units,
        "retired_against_orders": retired,
    }


class SplitSideIn(BaseModel):
    sku: str = Field(min_length=1, max_length=100)      # current SKU
    new_sku: str = Field(min_length=1, max_length=100)
    new_barcode: str | None = Field(default=None, max_length=64)


class SplitProductsIn(BaseModel):
    sides: list[SplitSideIn] = Field(min_length=2, max_length=2)
    changed_by: str | None = Field(default=None, max_length=100)


def _norm_id(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


@app.post("/api/products/split", dependencies=[Depends(require_user)])
def split_products(
    payload: SplitProductsIn, session: Session = Depends(get_session)
):
    """Declare a flagged pair genuinely TWO products, each with a
    distinct identity. A side that lives in the catalog writes its new
    SKU/barcode to Shopify (audited, History-logged); an RFID-only side
    (the usual misspelling) updates its tag records locally. A barcode
    equal to the side's OWN new SKU is allowed — the no-manufacturer-
    barcode convention. The duplicate flag closes as dismissed-forever."""
    a, b = payload.sides
    # The two identities must stop interacting.
    if _norm_id(a.new_sku) == _norm_id(b.new_sku):
        raise HTTPException(422, "The two SKUs are still the same.")
    for x, y in ((a, b), (b, a)):
        bc = _norm_id(x.new_barcode)
        if not bc:
            continue
        if bc == _norm_id(y.new_barcode):
            raise HTTPException(422, "The two barcodes are still the same.")
        if bc == _norm_id(y.new_sku):
            raise HTTPException(
                422,
                f"{x.sku}'s barcode equals the other product's SKU — "
                f"they'd still collide.",
            )
    by = (payload.changed_by or "").strip()[:100] or None
    summary = []
    for side in payload.sides:
        cur = side.sku.strip()
        new_sku = side.new_sku.strip()
        new_bc = (side.new_barcode or "").strip() or None
        tags = session.scalars(
            select(RfidAssignment).where(
                func.upper(RfidAssignment.sku) == cur.upper()
            )
        ).all()
        bm = session.scalar(
            select(BinMapEntry).where(
                func.upper(BinMapEntry.sku) == cur.upper()
            )
        )
        old_bc = (
            (bm.barcode if bm is not None else None)
            or (tags[0].barcode if tags else None)
        )
        sku_changed = new_sku != cur
        bc_changed = new_bc is not None and new_bc != (old_bc or "")
        wrote_shopify = False
        if bm is not None and (sku_changed or bc_changed):
            # Catalog product: the identity change is a real Shopify
            # write, through the same mutations the edit window uses.
            require_shopify_write("scan_station")
            try:
                product = _lookup_api(cur)
            except RuntimeError as error:
                raise HTTPException(502, f"Shopify lookup failed: {error}")
            if product is None:
                raise HTTPException(
                    404, f"{cur} is in the bin map but not in Shopify."
                )
            try:
                if sku_changed:
                    shopify.update_variant_sku(
                        product["shopify_product_id"],
                        product["shopify_variant_id"], new_sku,
                    )
                if bc_changed:
                    shopify.update_variant_barcode(
                        product["shopify_product_id"],
                        product["shopify_variant_id"], new_bc,
                    )
            except RuntimeError as error:
                raise HTTPException(502, f"Shopify update failed: {error}")
            wrote_shopify = True
        # RFID records follow either way.
        for t in tags:
            t.sku = new_sku
            if new_bc is not None:
                t.barcode = new_bc[:64]
        if sku_changed:
            session.add(BarcodeChange(
                sku=new_sku, product_title=tags[0].product_title if tags
                else None,
                changed_field="sku",
                old_barcode=cur[:64], new_barcode=new_sku[:64],
                changed_by=by,
            ))
        if bc_changed:
            session.add(BarcodeChange(
                sku=new_sku, product_title=tags[0].product_title if tags
                else None,
                changed_field="barcode",
                old_barcode=(old_bc or "")[:64] or None,
                new_barcode=new_bc[:64],
                changed_by=by,
            ))
        summary.append({
            "sku": new_sku, "barcode": new_bc,
            "shopify": wrote_shopify, "tags": len(tags),
        })
    # The pair is two products by declaration: close its flag for good
    # (the any-status pair match means it is never re-filed).
    closed = 0
    for t in session.scalars(
        select(ReviewTask).where(
            ReviewTask.category == orders_sync.DUP_CATEGORY,
            ReviewTask.status == "open",
        )
    ).all():
        pair = orders_sync.dup_pair_of(t.detail)
        if pair == frozenset((a.sku.strip().upper(), b.sku.strip().upper())):
            t.status = "resolved"
            t.resolved_by = by or "split"
            t.resolved_at = datetime.utcnow()
            t.resolution_note = (
                f"Split into two distinct products: {a.new_sku} and "
                f"{b.new_sku}."
            )
            closed += 1
    session.commit()
    return {"sides": summary, "tasks_closed": closed}


class MergeProductsIn(BaseModel):
    from_sku: str = Field(min_length=1, max_length=100)
    into_sku: str = Field(min_length=1, max_length=100)
    # The name the merged product keeps (Nick: the duplicates carried
    # different recorded names). Empty = the survivor's catalog title.
    title: str | None = Field(default=None, max_length=255)
    changed_by: str | None = Field(default=None, max_length=100)


@app.post("/api/products/merge", dependencies=[Depends(require_user)])
def merge_products(
    payload: MergeProductsIn, session: Session = Depends(get_session)
):
    """Merge a duplicate's RFID records into the surviving product: every
    tag under from_sku moves to into_sku (identity refreshed from the
    live catalog when it knows the survivor), both sides take the chosen
    name, the duplicate review task closes, and an inventory check is
    filed for the merged product. LOCAL records only — Shopify never
    changes here."""
    frm = payload.from_sku.strip()
    into = payload.into_sku.strip()
    if not frm or not into or frm.upper() == into.upper():
        raise HTTPException(422, "Pick two different SKUs to merge.")
    from_tags = session.scalars(
        select(RfidAssignment).where(
            func.upper(RfidAssignment.sku) == frm.upper()
        )
    ).all()
    into_tags = session.scalars(
        select(RfidAssignment).where(
            func.upper(RfidAssignment.sku) == into.upper()
        )
    ).all()
    if not from_tags:
        raise HTTPException(404, f"No tags on file under {frm}.")
    bm = session.scalar(
        select(BinMapEntry).where(
            func.upper(BinMapEntry.sku) == into.upper()
        )
    )
    canonical = bm.sku if bm is not None else (
        into_tags[0].sku if into_tags else into
    )
    title = (payload.title or "").strip() or (
        bm.product_title if bm is not None else None
    )
    # BOTH sides come out wearing the survivor's identity: the moved tags
    # need it, and the survivor's own rows may carry stale ids too.
    for t in from_tags + into_tags:
        t.sku = canonical
        if title:
            t.product_title = title
        if bm is not None:
            t.barcode = bm.barcode or t.barcode
            t.shopify_variant_id = (
                bm.shopify_variant_id or t.shopify_variant_id
            )
            t.shopify_product_id = (
                bm.shopify_product_id or t.shopify_product_id
            )
    by = (payload.changed_by or "").strip()[:100] or None
    session.add(BarcodeChange(
        sku=canonical,
        product_title=title,
        changed_field="product-merged",
        old_barcode=frm[:64],
        new_barcode=canonical[:64],
        changed_by=by,
    ))
    # Close every open duplicate task naming this pair.
    closed = 0
    for t in session.scalars(
        select(ReviewTask).where(
            ReviewTask.category == orders_sync.DUP_CATEGORY,
            ReviewTask.status == "open",
        )
    ).all():
        d = (t.detail or "").upper()
        if frm.upper() in d and into.upper() in d:
            t.status = "resolved"
            t.resolved_by = by or "merge"
            t.resolved_at = datetime.utcnow()
            t.resolution_note = f"Merged {frm} into {canonical}."
            closed += 1
    # The merged product's count just changed shape — ask for a walk.
    session.add(ReviewTask(
        category="inventory-check",
        sku=canonical,
        product_title=title,
        detail=(f"Merged duplicate {frm} into {canonical} — "
                f"{len(from_tags)} tag(s) moved over. Recommend counting "
                f"the shelf to confirm the combined total."),
        created_by=by or "merge",
    ))
    session.commit()
    return {
        "moved_tags": len(from_tags),
        "into_sku": canonical,
        "title": title,
        "tasks_closed": closed,
    }


@app.post(
    "/api/print-jobs/{job_id}/complete",
    dependencies=[Depends(require_agent_key)],
)
def complete_print_job(
    job_id: int,
    create_assignment: bool = True,
    session: Session = Depends(get_session),
):
    """Agent: label printed OK. With an RFID-encoding printer the EPC was
    written to the tag, so the assignment is auto-created. Non-RFID printers
    (agent --no-rfid) pass create_assignment=false — the label is just a
    barcode, and the tag gets linked later via the normal two-scan flow."""
    job = session.get(PrintJob, job_id)
    if job is None:
        raise HTTPException(404, "No such print job.")
    if job.status not in ("printing", "pending"):
        raise HTTPException(409, f"Job is already {job.status}.")

    job.status = "done"
    job.printed_at = datetime.now(timezone.utc)
    if not create_assignment:
        session.commit()
        return {"job": job.as_dict(), "assignment": None}
    if job.kind == "companion":
        # Box 2..N of a multi-box unit: the tag is live (the printer
        # encoded it) but it must count NOWHERE - register it in the
        # companion registry instead of assignments. The box math rides
        # the bin line's note (label_name covered older jobs).
        m = (_BOX_NOTE_RE.search(job.bin_location or "")
             or re.search(r"BOX (\d+) OF (\d+)", job.label_name or ""))
        session.add(CompanionTag(
            epc=job.epc,
            sku=job.sku,
            product_title=job.product_title,
            box_no=int(m.group(1)) if m else None,
            box_count=int(m.group(2)) if m else None,
            bin_location=_strip_box_note(job.bin_location),
            created_by=job.requested_by or "printer",
        ))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            job = session.get(PrintJob, job_id)
            job.status = "done"
            job.printed_at = datetime.now(timezone.utc)
            session.commit()
        return {"job": job.as_dict(), "assignment": None}
    assignment = RfidAssignment(
        rfid_id=job.epc,
        shopify_variant_id=job.shopify_variant_id,
        shopify_product_id=job.shopify_product_id,
        product_title=job.product_title,
        variant_title=job.variant_title,
        sku=job.sku,
        barcode=job.barcode,
        # A multi-box unit's label bin carries ", Box 1 of 2" for the
        # sticker - the RECORD keeps the clean bin.
        bin_location=_strip_box_note(job.bin_location),
        assigned_by=job.requested_by or "printer",
    )
    session.add(assignment)
    try:
        session.commit()
    except IntegrityError:
        # EPC already assigned (e.g. a re-run after a crash) — keep the job
        # done; the tag <-> product link already exists.
        session.rollback()
        job = session.get(PrintJob, job_id)
        job.status = "done"
        job.printed_at = datetime.now(timezone.utc)
        session.commit()
        return {"job": job.as_dict(), "assignment": None}
    session.refresh(job)
    session.refresh(assignment)
    return {"job": job.as_dict(), "assignment": assignment.as_dict()}


class PrintJobFail(BaseModel):
    error: str = Field(max_length=500)


@app.post(
    "/api/print-jobs/{job_id}/fail",
    dependencies=[Depends(require_agent_key)],
)
def fail_print_job(
    job_id: int, payload: PrintJobFail, session: Session = Depends(get_session)
):
    job = session.get(PrintJob, job_id)
    if job is None:
        raise HTTPException(404, "No such print job.")
    job.status = "error"
    job.error = payload.error
    session.commit()
    return job.as_dict()


# --------------------------------------------------------- barcode aliases ---
class AliasIn(BaseModel):
    alias_barcode: str = Field(max_length=64)
    target: str = Field(max_length=100)  # the known/internal barcode or SKU
    created_by: str | None = Field(default=None, max_length=100)
    # "nickname" (Nick, 2026-09-01): what the VENDOR calls the product -
    # a box label like "Collimating Eyepiece For Newtonian" on a product
    # whose SKU is "RN". Shown bracketed + highlighted on receiving
    # lists, resolves as a lookup alias like any other, and one per
    # product: saving a new one replaces the old.
    kind: Literal["manual", "nickname"] = "manual"

    @field_validator("alias_barcode", "target")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


@app.post(
    "/api/barcode-aliases",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def create_alias(payload: AliasIn, session: Session = Depends(get_session)):
    """Link a foreign barcode to a known product (identified by its real
    barcode or SKU). Returns the alias and the resolved product."""
    db_ok = database_configured()
    api_ok = not config.check_shopify_env()
    mode = config.BARCODE_LOOKUP

    # The alias must not itself be a real barcode/SKU of some product.
    if _resolve(payload.alias_barcode, mode, db_ok, api_ok) is not None:
        raise HTTPException(
            409,
            "That scanned code already matches a real product — it can't "
            "be linked as an alias.",
        )

    product = _resolve(payload.target, mode, db_ok, api_ok)
    if product is None:
        raise HTTPException(404, "No product found for that barcode or SKU.")

    # One vendor nickname per product: saving a new one replaces the
    # old (also frees the text if the SAME product's nickname is being
    # re-typed with different casing).
    if payload.kind == "nickname" and (product.get("sku") or "").strip():
        for old in session.scalars(
            select(BarcodeAlias).where(
                BarcodeAlias.kind == "nickname",
                func.upper(BarcodeAlias.sku)
                == product["sku"].strip().upper(),
            )
        ):
            session.delete(old)
        session.flush()

    # Alias resolution is case-insensitive, so a case-variant duplicate
    # would be unreachable dead weight — refuse it like an exact one.
    if session.scalar(
        select(BarcodeAlias).where(
            func.upper(BarcodeAlias.alias_barcode)
            == payload.alias_barcode.upper()
        )
    ) is not None:
        raise HTTPException(
            409, "That scanned code is already linked to a product."
        )

    alias = BarcodeAlias(
        alias_barcode=payload.alias_barcode,
        sku=product.get("sku"),
        barcode=product.get("barcode"),
        product_title=product.get("product_title"),
        created_by=payload.created_by,
        kind=payload.kind,
    )
    session.add(alias)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            409, "That scanned code is already linked to a product."
        )
    session.refresh(alias)
    product["alias_barcode"] = alias.alias_barcode
    return {"alias": alias.as_dict(), "product": product}


@app.delete(
    "/api/barcode-aliases/{alias_barcode}",
    status_code=204,
    dependencies=[Depends(require_user)],
)
def delete_alias(alias_barcode: str, session: Session = Depends(get_session)):
    row = session.scalar(
        select(BarcodeAlias).where(
            BarcodeAlias.alias_barcode == alias_barcode.strip()
        )
    )
    if row is None:
        raise HTTPException(404, "No such linked barcode.")
    session.delete(row)
    session.commit()


# ------------------------------------------------------- serial prefixes ---
class SerialPrefixIn(BaseModel):
    """Register a new 4-digit Astronomik serial prefix -> product link,
    for items missing from the loaded manufacturer sheet."""

    prefix: str = Field(min_length=4, max_length=4)
    target: str = Field(max_length=100)  # known barcode or SKU
    scan_note: str | None = Field(default=None, max_length=255)
    created_by: str | None = Field(default=None, max_length=100)

    @field_validator("prefix")
    @classmethod
    def four_digits(cls, v: str) -> str:
        v = v.strip()
        if not (len(v) == 4 and v.isdigit()):
            raise ValueError("must be exactly 4 digits")
        return v

    @field_validator("target")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


@app.get(
    "/api/serial-prefixes/{prefix}",
    dependencies=[Depends(require_user)],
)
def get_serial_prefix(prefix: str, session: Session = Depends(get_session)):
    """Peek at a prefix — the UI uses the manufacturer sheet's SKU as a
    recommendation when operators fix products."""
    row = session.get(SerialPrefix, prefix.strip())
    if row is None:
        raise HTTPException(404, "No such serial prefix.")
    return row.as_dict()


@app.post(
    "/api/serial-prefixes",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def create_serial_prefix(
    payload: SerialPrefixIn, session: Session = Depends(get_session)
):
    db_ok = database_configured()
    api_ok = not config.check_shopify_env()
    product = _resolve(payload.target, config.BARCODE_LOOKUP, db_ok, api_ok)
    if product is None:
        raise HTTPException(404, "No product found for that barcode or SKU.")

    name = product.get("product_title") or ""
    if product.get("variant_title"):
        name += f" ({product['variant_title']})"
    row = session.get(SerialPrefix, payload.prefix)
    if row is None:
        row = SerialPrefix(prefix=payload.prefix, brand="Astronomik")
        session.add(row)
    row.sku = product.get("sku")
    row.item_name = name[:255]  # label_name untouched if one was saved
    row.scan_note = (payload.scan_note or "").strip() or None
    session.commit()
    return {"serial_prefix": row.as_dict(), "product": product}


# ---------------------------------------------------------- filter sets ---
_FILTER_SET_SQL = text(
    """
    SELECT v.Variant_SKU, v.Variant_Barcode, v.Option1_Value,
           p.Title AS Product_Title
    FROM dbo.Shopify_Variants v
    JOIN dbo.Shopify_Products p ON p.Handle_ID = v.Handle_ID
    WHERE p.Title LIKE '%Astronomik%'
      AND (p.Title LIKE '%RGB%' OR p.Title LIKE '%set%'
           OR p.Title LIKE '%LRGB%' OR p.Title LIKE '%SHO%')
    ORDER BY p.Title, v.Option1_Value
    """
)


@app.get("/api/filter-sets", dependencies=[Depends(require_user)])
def list_filter_sets(session: Session = Depends(get_session)):
    """Candidate multi-filter set products, for the set-registration window
    ("which set might these three filters belong to?")."""
    try:
        rows = session.execute(_FILTER_SET_SQL).all()
    except Exception as error:
        logger.warning("filter-set candidates failed: %s", error)
        return {"count": 0, "sets": []}
    sets = [
        {
            "sku": r.Variant_SKU,
            "barcode": r.Variant_Barcode,
            "variant": r.Option1_Value,
            "title": r.Product_Title,
        }
        for r in rows
    ]
    return {"count": len(sets), "sets": sets}


class FilterSetIn(BaseModel):
    """Register a 3-box filter set: three component serials (Red, Green,
    Blue order) all mapped to one set product."""

    serials: list[str] = Field(min_length=3, max_length=3)
    target: str = Field(max_length=100)  # the set's SKU or barcode
    created_by: str | None = Field(default=None, max_length=100)

    @field_validator("serials")
    @classmethod
    def serial_shaped(cls, v: list[str]) -> list[str]:
        v = [s.strip() for s in v]
        for s in v:
            if not (s.isdigit() and 5 <= len(s) <= 12):
                raise ValueError(f"'{s}' doesn't look like a serial number")
        if len({s[:4] for s in v}) != 3:
            raise ValueError(
                "the three serials must have three different prefixes — "
                "was the same filter scanned twice?"
            )
        return v

    @field_validator("target")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


@app.post(
    "/api/filter-sets/register",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def register_filter_set(
    payload: FilterSetIn, session: Session = Depends(get_session)
):
    db_ok = database_configured()
    api_ok = not config.check_shopify_env()
    product = _resolve(payload.target, config.BARCODE_LOOKUP, db_ok, api_ok)
    if product is None:
        raise HTTPException(404, "No product found for that barcode or SKU.")

    name = product.get("product_title") or ""
    if product.get("variant_title"):
        name += f" ({product['variant_title']})"
    prefixes = [s[:4] for s in payload.serials]
    note = (
        f"Part of the SET: {name} — 3 boxes "
        f"(R={prefixes[0]}, G={prefixes[1]}, B={prefixes[2]}). "
        f"Apply ONE tag to the set, not one per filter."
    )[:255]
    for prefix, color in zip(prefixes, ("Red", "Green", "Blue")):
        row = session.get(SerialPrefix, prefix)
        if row is None:
            row = SerialPrefix(prefix=prefix, brand="Astronomik")
            session.add(row)
        row.sku = product.get("sku")
        row.item_name = f"{name} ({color} component)"[:255]
        row.scan_note = note
    session.commit()
    return {"product": product, "prefixes": prefixes}


# -------------------------------------------------------- serial labels ---
class SerialLabelIn(BaseModel):
    label_name: str = Field(max_length=255)

    @field_validator("label_name")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


@app.put(
    "/api/serial-prefixes/{prefix}/label",
    dependencies=[Depends(require_user)],
)
def set_serial_label(
    prefix: str, payload: SerialLabelIn, session: Session = Depends(get_session)
):
    """Save the operator's preferred label name for a serial prefix (what
    prints at the top of that product's labels). The name doubles as an
    ephemeral lookup alias, like the two-line label editor's lines."""
    row = session.get(SerialPrefix, prefix.strip())
    if row is None:
        raise HTTPException(404, "No such serial prefix.")
    row.label_name = payload.label_name
    name = (payload.label_name or "").strip()
    _sync_label_aliases(
        session, row.sku,
        [name if name and name != STORE_HEADER else None], None,
    )
    session.commit()
    return row.as_dict()


# ------------------------------------------------------ barcode overwrite ---
class OverwriteIn(BaseModel):
    """Adopt a scanned (manufacturer) barcode as the product's REAL barcode,
    replacing the one in Shopify."""

    new_barcode: str = Field(max_length=64)
    target: str = Field(max_length=100)  # current barcode or SKU
    changed_by: str | None = Field(default=None, max_length=100)
    confirmed: bool = False  # the UI checkbox; server refuses without it

    @field_validator("new_barcode", "target")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


def _mojibake_value(v: str | None) -> bool:
    """True when a SKU/barcode carries a literal '?' or a non-ASCII char
    (ZWO's unicode 'Ⅱ') — the same heuristic the C72 uses. Such a value
    can't be a real retail barcode, so keeping it linked never collides."""
    return bool(v) and any(c == "?" or ord(c) > 126 for c in v)


def _refresh_item_idents(
    session: Session,
    product: dict,
    sku: str | None = None,
    barcode: str | None = None,
) -> int:
    """Push a just-written SKU/barcode into the rows of OPEN batches, so
    the web Check list and the C72's next pull show the fix immediately
    (Nick, 2026-08-26: a corrected barcode stayed invisible on both
    until the nightly rebuild). The live tag records' barcode follows
    too; finished batches keep telling their story as it was."""
    vid = (product.get("shopify_variant_id") or "").strip()
    if not vid:
        return 0
    touched = 0
    open_ids = [
        b.id for b in session.scalars(
            select(Batch).where(
                Batch.status.notin_(("done", "abandoned"))
            )
        )
    ]
    if open_ids:
        for it in session.scalars(
            select(BatchItem).where(
                BatchItem.batch_id.in_(open_ids),
                BatchItem.shopify_variant_id == vid,
            )
        ):
            if sku is not None:
                it.sku = sku
            if barcode is not None:
                it.barcode = barcode
            touched += 1
    if barcode is not None:
        for t in session.scalars(
            select(RfidAssignment).where(
                RfidAssignment.shopify_variant_id == vid
            )
        ):
            t.barcode = barcode
    return touched


def _link_replaced_value(
    session: Session, old: str | None, product: dict,
    changed_by: str | None,
) -> bool:
    """When an overwrite replaces a BROKEN value (mojibake), keep the old
    string linked as an alias so already-printed labels carrying it still
    scan (Nick, 2026-08-25: fixing ZWO Nikon-T2-Ⅱ's barcode orphaned
    every existing label). Only broken values are auto-linked - a clean
    replaced barcode might legitimately belong elsewhere."""
    old = (old or "").strip()
    if not old or len(old) > 64 or not _mojibake_value(old):
        return False
    if session.scalar(
        select(BarcodeAlias).where(
            func.upper(BarcodeAlias.alias_barcode) == old.upper()
        )
    ) is not None:
        return False
    session.add(BarcodeAlias(
        alias_barcode=old,
        sku=product.get("sku"),
        barcode=product.get("barcode"),
        product_title=product.get("product_title"),
        created_by=changed_by,
        kind="legacy",
    ))
    return True


@app.post(
    "/api/barcode-overwrites",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def overwrite_barcode(
    payload: OverwriteIn, session: Session = Depends(get_session)
):
    """Replace a product's barcode in Shopify with the scanned one, and log
    who did it and when. The bin map picks the change up on its next
    rebuild; until then the Shopify-API lookup resolves the new barcode."""
    if not payload.confirmed:
        raise HTTPException(
            422, "Confirmation checkbox is required for barcode replacement."
        )
    require_shopify_write("scan_station")
    if config.check_shopify_env():
        raise HTTPException(500, "Shopify credentials are not configured.")

    # Must resolve via the Shopify API: the mutation needs real Shopify ids.
    try:
        product = _lookup_api(payload.target)
    except RuntimeError as error:
        raise HTTPException(502, f"Shopify lookup failed: {error}")
    if product is None:
        raise HTTPException(
            404, "No product found in Shopify for that barcode or SKU."
        )

    db_ok = database_configured()
    existing = _resolve(payload.new_barcode, config.BARCODE_LOOKUP, db_ok, True)
    if existing:
        # A code that already belongs to a DIFFERENT product is refused.
        # The SAME product is fine: setting barcode = its own SKU is the
        # house convention for brands that ship no barcode (Svbony) —
        # both codes resolve to one product, nothing collides (Nick,
        # 2026-08-18).
        same = (
            existing.get("shopify_variant_id")
            == product.get("shopify_variant_id")
        ) or (
            (existing.get("sku") or "").strip().upper()
            == (product.get("sku") or "").strip().upper()
            != ""
        )
        if not same:
            raise HTTPException(
                409,
                "That scanned code already belongs to a product — it can't "
                "replace another product's barcode.",
            )

    try:
        shopify.update_variant_barcode(
            product["shopify_product_id"],
            product["shopify_variant_id"],
            payload.new_barcode,
        )
    except RuntimeError as error:
        raise HTTPException(502, f"Shopify barcode update failed: {error}")

    change = BarcodeChange(
        sku=product.get("sku"),
        product_title=product.get("product_title"),
        shopify_variant_id=product.get("shopify_variant_id"),
        old_barcode=product.get("barcode"),
        new_barcode=payload.new_barcode,
        changed_by=payload.changed_by,
    )
    session.add(change)
    # The bin map answers lookups FIRST; leaving the old barcode in it
    # would keep serving stale data until the nightly rebuild (Nick hit
    # this in the field, 2026-08-24). Update the live rows now.
    crit = [BinMapEntry.shopify_variant_id
            == product.get("shopify_variant_id")]
    if (product.get("sku") or "").strip():
        crit.append(func.upper(BinMapEntry.sku)
                    == product["sku"].strip().upper())
    for bm in session.scalars(select(BinMapEntry).where(or_(*crit))):
        bm.barcode = payload.new_barcode
    # Rows of OPEN batches and the live tag records show the barcode
    # too - without this, the Check list and the C72 kept displaying the
    # old value after a fix (Nick, 2026-08-26). Finished batches keep
    # telling their story as it was.
    _refresh_item_idents(
        session, product, barcode=payload.new_barcode
    )
    # If this code was previously linked as an alias, the link is now
    # redundant (and would shadow nothing, but keep the table honest).
    stale_alias = session.scalar(
        select(BarcodeAlias).where(
            BarcodeAlias.alias_barcode == payload.new_barcode
        )
    )
    if stale_alias is not None:
        session.delete(stale_alias)
    # A broken old value (mojibake) stays linked: labels already printed
    # with it keep scanning to this product.
    legacy_linked = _link_replaced_value(
        session, product.get("barcode"),
        {**product, "barcode": payload.new_barcode}, payload.changed_by,
    )
    session.commit()
    session.refresh(change)

    product["barcode"] = payload.new_barcode
    return {
        "change": change.as_dict(),
        "product": product,
        "legacy_linked": legacy_linked,
    }


class BinUpdateIn(BaseModel):
    """Set a product's bin location: the variant's stock.bin metafield AND
    the product's my_fields.bin_location (what EasyScan reads), so the two
    can't drift apart."""

    target: str = Field(max_length=100)  # barcode or SKU
    # `new_bin` is accepted as an alias: the scanner and older builds send
    # that name, and a rejected bin move at the shelf is worse than a
    # slightly permissive schema.
    bin: str = Field(max_length=100, validation_alias=AliasChoices(
        "bin", "new_bin"
    ))
    changed_by: str | None = Field(default=None, max_length=100)

    model_config = {"populate_by_name": True}

    @field_validator("target", "bin")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


@app.post(
    "/api/bin-updates",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def update_bin(payload: BinUpdateIn, session: Session = Depends(get_session)):
    require_shopify_write("scan_station")
    if config.check_shopify_env():
        raise HTTPException(500, "Shopify credentials are not configured.")
    # Shopify API resolution: the metafield write needs the variant GID.
    try:
        product = _lookup_api(payload.target)
    except RuntimeError as error:
        raise HTTPException(502, f"Shopify lookup failed: {error}")
    if product is None:
        raise HTTPException(
            404, "No product found in Shopify for that barcode or SKU."
        )
    try:
        shopify.set_variant_bin(product["shopify_variant_id"], payload.bin)
    except RuntimeError as error:
        raise HTTPException(502, f"Shopify bin update failed: {error}")

    # Keep EasyScan's product-level bin in step, so the two sources can't
    # disagree. Only when it's unambiguous: a single-variant product, or a
    # value that already exists (otherwise a multi-variant product's other
    # variants would inherit a bin they don't belong to).
    easyscan_updated = False
    product_gid = product.get("shopify_product_id")
    if product_gid and str(product_gid).startswith("gid://"):
        try:
            info = shopify.product_bin_info(product_gid)
            if info["variant_count"] <= 1 or info["easy_bin"]:
                shopify.set_product_bin(product_gid, payload.bin)
                easyscan_updated = True
        except RuntimeError as error:
            # The variant write already landed; say so rather than failing.
            logger.warning("EasyScan bin update failed for %s: %s",
                           product.get("sku"), error)

    session.add(BarcodeChange(
        sku=product.get("sku"),
        product_title=product.get("product_title"),
        shopify_variant_id=product.get("shopify_variant_id"),
        changed_field="bin",
        old_barcode=(product.get("bin_location") or "")[:64] or None,
        new_barcode=payload.bin[:64],
        changed_by=payload.changed_by,
    ))

    # The LOCAL records move with it, immediately — otherwise the RFID
    # system keeps the OLD shelf until the next full bin-map refresh:
    # batch pre-seeds, bin checks and sweep reports would all disagree
    # with the move that was just confirmed.
    sku = (product.get("sku") or "").strip()
    if sku:
        map_rows = session.scalars(
            select(BinMapEntry).where(
                func.upper(BinMapEntry.sku) == sku.upper()
            )
        ).all()
        if map_rows:
            # The Shopify write replaced the whole bin value, so one local
            # row with the new bin mirrors it; extra rows from an old
            # split-across-shelves value are collapsed.
            keep = map_rows[0]
            keep.bin = payload.bin
            keep.other_bins = None
            for extra in map_rows[1:]:
                session.delete(extra)
        else:
            # First bin this product has ever had: it was never in the map
            # (the map only holds binned variants), so CREATE its row —
            # otherwise batch pre-seeds and the Inventory tab keep saying
            # "no Shopify bin" until the next full map refresh, and the
            # write looks like it did nothing (the bin-backfill lesson).
            session.add(BinMapEntry(
                sku=product.get("sku"),
                barcode=product.get("barcode"),
                product_title=product.get("product_title"),
                variant_title=product.get("variant_title"),
                shopify_variant_id=product.get("shopify_variant_id"),
                shopify_product_id=product.get("shopify_product_id"),
                bin=payload.bin,
            ))
        for tag in session.scalars(
            select(RfidAssignment).where(
                func.upper(RfidAssignment.sku) == sku.upper()
            )
        ):
            tag.bin_location = payload.bin
        # Open batches carry a per-item bin SNAPSHOT taken at scan time,
        # and the Check step raises "wrong-bin" by comparing it to the bin
        # being walked. Leaving it stale is what made "Move product to this
        # bin" look like it did nothing: Shopify, the map and the tags all
        # moved, but the flag came straight back, so the operator pressed
        # again — W9159A collected four no-op J1-4 -> J1-4 changes that way.
        # CLOSED batches keep their snapshot: it's the honest record of
        # what was true when that shelf was walked.
        open_batches = {
            b.id for b in session.scalars(
                select(Batch).where(
                    Batch.status.notin_(("done", "abandoned"))
                )
            )
        }
        if open_batches:
            for item in session.scalars(
                select(BatchItem).where(
                    func.upper(BatchItem.sku) == sku.upper(),
                    BatchItem.batch_id.in_(open_batches),
                )
            ):
                item.bin_location = payload.bin
                # The Shopify write replaced the whole bin value, so the
                # product is no longer split across shelves.
                item.other_bins = None
    session.commit()

    product["bin_location"] = payload.bin
    return {"product": product, "easyscan_updated": easyscan_updated}


class RebinTagsIn(BaseModel):
    """Mismatch resolution, "Shopify is right" direction: the boxes moved
    (or are moving) to Shopify's shelf, so the TAG RECORDS follow it."""

    sku: str = Field(min_length=1, max_length=100)
    bin: str = Field(min_length=1, max_length=100)
    changed_by: str | None = Field(default=None, max_length=100)


@app.post(
    "/api/assignments/rebin",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def rebin_tags(payload: RebinTagsIn, session: Session = Depends(get_session)):
    """LOCAL-ONLY counterpart of /api/bin-updates: Shopify already holds
    this bin, only the RFID records disagree — so the tags (and open-batch
    snapshots) move to match, with a History receipt. Nothing in Shopify
    is touched."""
    sku = payload.sku.strip()
    new_bin = payload.bin.strip()
    tags = session.scalars(
        select(RfidAssignment).where(
            func.upper(RfidAssignment.sku) == sku.upper()
        )
    ).all()
    if not tags:
        raise HTTPException(404, f"No tags on file for SKU {sku}.")
    old_bins = sorted({(t.bin_location or "").strip() for t in tags
                       if (t.bin_location or "").strip()})
    for t in tags:
        t.bin_location = new_bin
    open_batches = {
        b.id for b in session.scalars(
            select(Batch).where(Batch.status.notin_(("done", "abandoned"))))
    }
    if open_batches:
        for item in session.scalars(
            select(BatchItem).where(
                func.upper(BatchItem.sku) == sku.upper(),
                BatchItem.batch_id.in_(open_batches),
            )
        ):
            item.bin_location = new_bin
            item.other_bins = None
    session.add(BarcodeChange(
        sku=sku,
        product_title=tags[0].product_title,
        shopify_variant_id=tags[0].shopify_variant_id,
        changed_field="bin-local",
        old_barcode=(", ".join(old_bins))[:64] or None,
        new_barcode=new_bin[:64],
        changed_by=payload.changed_by,
    ))
    session.commit()
    return {"sku": sku, "bin": new_bin, "tags_moved": len(tags)}


# ---- C72 live tuning + telemetry (diagnostic plumbing) -------------------
# Not inventory data: no History rows, no undo. The gun polls tuning and
# streams locate telemetry so field tuning happens without APK builds.

class C72TuningIn(BaseModel):
    values: dict
    merge: bool = True
    worker: str | None = Field(default=None, max_length=100)


class C72DebugIn(BaseModel):
    device: str | None = Field(default=None, max_length=100)
    lines: list[str]


def _tuning_row(session: Session) -> C72Tuning:
    row = session.scalar(select(C72Tuning).limit(1))
    if row is None:
        row = C72Tuning(values="{}")
        session.add(row)
        session.flush()
    return row


@app.get("/api/c72/tuning", dependencies=[Depends(require_user)])
def get_c72_tuning(
    device: str | None = None,
    tab: str | None = None,
    session: Session = Depends(get_session),
):
    # The gun's ~2s tuning poll doubles as its presence heartbeat: newer
    # APKs identify themselves and their current tab here (see the LINK
    # presence block). Old APKs send nothing and simply stay invisible
    # until their first LINK scan.
    if device:
        _stamp_gun(device, tab)
    row = session.scalar(select(C72Tuning).limit(1))
    try:
        values = json.loads(row.values) if row else {}
    except Exception:
        values = {}
    return {
        "values": values,
        "updated_by": row.updated_by if row else None,
        "updated_at": (row.updated_at.isoformat()
                       if row and row.updated_at else None),
    }


@app.post("/api/c72/tuning", dependencies=[Depends(require_user)])
def set_c72_tuning(
    payload: C72TuningIn, session: Session = Depends(get_session)
):
    """Set (merge by default) the gun's live parameters. A key set to
    null deletes it, so the gun falls back to its built-in default."""
    row = _tuning_row(session)
    try:
        current = json.loads(row.values or "{}")
    except Exception:
        current = {}
    if payload.merge:
        for k, v in payload.values.items():
            if v is None:
                current.pop(k, None)
            else:
                current[k] = v
    else:
        current = {k: v for k, v in payload.values.items()
                   if v is not None}
    row.values = json.dumps(current)[:2000]
    row.updated_by = payload.worker
    session.commit()
    return {"values": current}


@app.post(
    "/api/c72/debug-log",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def post_c72_debug(
    payload: C72DebugIn, session: Session = Depends(get_session)
):
    for line in payload.lines[:100]:
        session.add(C72DebugEvent(device=payload.device,
                                  line=str(line)[:400]))
    # Ring prune: keep the newest ~2000 rows. Flush first so the batch
    # just added counts toward the cap.
    session.flush()
    max_id = session.scalar(select(func.max(C72DebugEvent.id))) or 0
    if max_id > 2000:
        session.execute(delete(C72DebugEvent).where(
            C72DebugEvent.id <= max_id - 2000
        ))
    session.commit()
    return {"ok": True, "stored": min(len(payload.lines), 100)}


class C72CommandIn(BaseModel):
    command: str = Field(min_length=1, max_length=50)
    arg: str | None = Field(default=None, max_length=500)
    worker: str | None = Field(default=None, max_length=100)


class C72CommandDoneIn(BaseModel):
    result: str | None = Field(default=None, max_length=400)
    device: str | None = Field(default=None, max_length=100)


@app.post(
    "/api/c72/commands",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def create_c72_command(
    payload: C72CommandIn, session: Session = Depends(get_session)
):
    cmd = C72Command(command=payload.command.strip(), arg=payload.arg,
                     created_by=payload.worker)
    session.add(cmd)
    session.commit()
    return {"id": cmd.id}


@app.get("/api/c72/commands/pending", dependencies=[Depends(require_user)])
def pending_c72_commands(session: Session = Depends(get_session)):
    rows = session.scalars(
        select(C72Command).where(C72Command.done_at.is_(None))
        .order_by(C72Command.id).limit(20)
    ).all()
    return {"commands": [
        {"id": r.id, "command": r.command, "arg": r.arg} for r in rows
    ]}


@app.post(
    "/api/c72/commands/{command_id}/done",
    dependencies=[Depends(require_user)],
)
def ack_c72_command(
    command_id: int,
    payload: C72CommandDoneIn,
    session: Session = Depends(get_session),
):
    cmd = session.get(C72Command, command_id)
    if cmd is None:
        raise HTTPException(404, "No such command.")
    cmd.done_at = datetime.now(timezone.utc)
    cmd.done_by = payload.device
    cmd.result = payload.result
    session.commit()
    return {"ok": True}


@app.get("/api/c72/commands", dependencies=[Depends(require_user)])
def list_c72_commands(
    limit: int = 30, session: Session = Depends(get_session)
):
    rows = session.scalars(
        select(C72Command).order_by(C72Command.id.desc())
        .limit(max(1, min(limit, 200)))
    ).all()
    return {"commands": [
        {
            "id": r.id, "command": r.command, "arg": r.arg,
            "created_by": r.created_by,
            "done": r.done_at is not None,
            "done_by": r.done_by, "result": r.result,
        } for r in rows
    ]}


@app.get("/api/c72/debug-log", dependencies=[Depends(require_user)])
def get_c72_debug(
    limit: int = 200, session: Session = Depends(get_session)
):
    rows = session.scalars(
        select(C72DebugEvent).order_by(C72DebugEvent.id.desc())
        .limit(max(1, min(limit, 1000)))
    ).all()
    return {"lines": [
        {
            "at": r.created_at.isoformat() if r.created_at else None,
            "device": r.device,
            "line": r.line,
        } for r in rows
    ]}


class LocateQueueIn(BaseModel):
    """Queue a product for a physical tag hunt on the C72."""

    sku: str = Field(min_length=1, max_length=100)
    label: str | None = Field(default=None, max_length=255)
    worker: str | None = Field(default=None, max_length=100)


@app.get("/api/locate-queue", dependencies=[Depends(require_user)])
def list_locate_queue(session: Session = Depends(get_session)):
    """The shared to-hunt list, newest first, with live tag context so the
    C72 can show where the tags THINK they are before the walk starts."""
    entries = []
    for e in session.scalars(
        select(LocateQueueEntry).order_by(LocateQueueEntry.id.desc())
    ):
        tags = session.scalars(
            select(RfidAssignment).where(
                func.upper(RfidAssignment.sku) == e.sku.upper()
            )
        ).all()
        bins = sorted({(t.bin_location or "").strip() for t in tags
                       if (t.bin_location or "").strip()})
        # Image + title fallback from the live bin map, so the C72's list
        # shows the same preview card a loaded product does.
        map_row = session.scalar(
            select(BinMapEntry).where(
                func.upper(BinMapEntry.sku) == e.sku.upper()
            )
        )
        entries.append({
            "id": e.id,
            "sku": e.sku,
            "label": e.label or (map_row.product_title if map_row else None),
            "image_url": map_row.image_url if map_row else None,
            "added_by": e.added_by,
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "tag_count": len(tags),
            "bins": bins,
        })
    return {"entries": entries}


@app.post(
    "/api/locate-queue",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def add_locate_queue(
    payload: LocateQueueIn, session: Session = Depends(get_session)
):
    """Add a product to the locate list (idempotent per SKU): re-queuing
    an already-listed product is a no-op, not a duplicate."""
    sku = payload.sku.strip()
    existing = session.scalar(
        select(LocateQueueEntry).where(
            func.upper(LocateQueueEntry.sku) == sku.upper()
        )
    )
    if existing is not None:
        return {"id": existing.id, "sku": existing.sku, "already": True}
    entry = LocateQueueEntry(
        sku=sku, label=(payload.label or "").strip()[:255] or None,
        added_by=payload.worker,
    )
    session.add(entry)
    session.add(BarcodeChange(
        sku=sku,
        product_title=entry.label,
        changed_field="locate-list",
        old_barcode=None,
        new_barcode="on the locate list",
        changed_by=payload.worker,
    ))
    session.commit()
    return {"id": entry.id, "sku": entry.sku, "already": False}


@app.delete(
    "/api/locate-queue/{entry_id}", dependencies=[Depends(require_user)]
)
def remove_locate_queue(
    entry_id: int,
    worker: str | None = None,
    session: Session = Depends(get_session),
):
    """Take a product off the locate list — from the web terminal or the
    gun, whichever finishes (or abandons) the hunt."""
    entry = session.get(LocateQueueEntry, entry_id)
    if entry is None:
        raise HTTPException(404, "Not on the locate list (already removed?).")
    session.add(BarcodeChange(
        sku=entry.sku,
        product_title=entry.label,
        changed_field="locate-list",
        old_barcode="on the locate list",
        new_barcode="removed",
        changed_by=worker,
    ))
    session.delete(entry)
    session.commit()
    return {"ok": True, "sku": entry.sku}


class OnHandUpdateIn(BaseModel):
    """Verify-step count correction: raise Shopify's on-hand to what the
    shelf walk physically found."""

    sku: str = Field(max_length=100)
    new_qty: int = Field(ge=1, le=100000)
    changed_by: str | None = Field(default=None, max_length=100)
    # The operator answered the confirmation dialog.
    confirmed: bool = False
    # When sent, this batch item's expected-count snapshot is updated too,
    # so the Verify table agrees with the store right after the write.
    batch_id: int | None = None
    item_id: int | None = None

    @field_validator("sku")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


@app.post(
    "/api/onhand-updates",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def update_on_hand(
    payload: OnHandUpdateIn, session: Session = Depends(get_session)
):
    """Set Shopify ON-HAND to the count the shelf walk found. INCREASES
    ONLY: scanning more boxes than Shopify knew about is physical proof
    they exist; scanning fewer proves nothing (the rest may sit mis-binned
    on another shelf — this month's whole problem). Confirmed by the
    operator, logged, and undoable from History."""
    require_shopify_write("verify_onhand")
    if config.check_shopify_env():
        raise HTTPException(500, "Shopify credentials are not configured.")
    if not payload.confirmed:
        raise HTTPException(
            409, "This writes a stock number to Shopify — confirm it first."
        )
    live = shopify.get_on_hand(payload.sku)
    if live is None:
        raise HTTPException(404, f"No Shopify product for SKU {payload.sku}.")
    if payload.new_qty <= live:
        raise HTTPException(
            422,
            f"Shopify already shows {live} on hand for {payload.sku}; "
            f"this button only RAISES a count to match boxes physically "
            f"found. A count can be lowered from a bin audit or batch "
            f"verify when recorded sales account for the missing units.",
        )
    try:
        before = shopify.set_on_hand(payload.sku, payload.new_qty)
    except RuntimeError as error:
        raise HTTPException(502, f"Shopify on-hand write failed: {error}")
    change = BarcodeChange(
        sku=payload.sku,
        changed_field="on-hand",
        old_barcode=str(before),
        new_barcode=str(payload.new_qty),
        changed_by=payload.changed_by,
    )
    session.add(change)
    # Keep the open batch's snapshot honest so the Verify table agrees
    # with the store the moment it re-renders.
    if payload.batch_id and payload.item_id:
        item = session.get(BatchItem, payload.item_id)
        if item is not None and item.batch_id == payload.batch_id:
            item.expected_qty = payload.new_qty
    session.commit()
    session.refresh(change)
    return {
        "sku": payload.sku,
        "before": before,
        "after": payload.new_qty,
        "change_id": change.id,
        "message": (
            f"Shopify on-hand for {payload.sku}: {before} → "
            f"{payload.new_qty} ✓ (undo from History)"
        ),
    }


class OnHandUndoIn(BaseModel):
    changed_by: str | None = Field(default=None, max_length=100)
    confirmed: bool = False


@app.post(
    "/api/onhand-updates/{change_id}/undo",
    dependencies=[Depends(require_user)],
)
def undo_on_hand(
    change_id: int,
    payload: OnHandUndoIn,
    session: Session = Depends(get_session),
):
    """Put the on-hand number back where it was before an update made
    here. The unconfirmed call answers 409 with exactly what would happen
    (including the CURRENT live value, in case something else moved it
    since) — the client shows that as the confirmation text."""
    require_shopify_write("verify_onhand")
    row = session.get(BarcodeChange, change_id)
    if row is None or row.changed_field != "on-hand" or not row.sku:
        raise HTTPException(404, "No on-hand update with that id.")
    old = int(row.old_barcode or 0)
    if not payload.confirmed:
        live = shopify.get_on_hand(row.sku)
        raise HTTPException(
            409,
            f"Undo sets Shopify on-hand for {row.sku} back to {old} "
            f"(currently {live}"
            + (
                f" — note: something else changed it since this update "
                f"wrote {row.new_barcode}"
                if live is not None and str(live) != (row.new_barcode or "")
                else ""
            )
            + "). Confirm to write it.",
        )
    try:
        before = shopify.set_on_hand(row.sku, old)
    except RuntimeError as error:
        raise HTTPException(502, f"Shopify on-hand write failed: {error}")
    session.add(BarcodeChange(
        sku=row.sku,
        changed_field="on-hand-undo",
        old_barcode=str(before),
        new_barcode=str(old),
        changed_by=payload.changed_by,
    ))
    session.commit()
    return {
        "sku": row.sku,
        "before": before,
        "after": old,
        "message": f"Undone — {row.sku} on-hand back to {old}.",
    }


class OnHandLowerIn(BaseModel):
    """Audit count correction DOWNWARD: allowed only when recorded sales
    since the tag pool's baseline fully account for the drop (Nick,
    2026-08-24). One confirmed click lowers on-hand, retires the listed
    silent tags presumed-sold, and consumes the matching ledger units;
    one undo reverses all three."""

    sku: str = Field(max_length=100)
    bin_name: str = Field(max_length=100)
    new_qty: int = Field(ge=0, le=100000)
    # Silent tags to retire with the write. May be SHORTER than the drop
    # (untagged units sold too), never longer.
    epcs: list[str] = Field(default_factory=list, max_length=1000)
    changed_by: str | None = Field(default=None, max_length=100)
    confirmed: bool = False
    batch_id: int | None = None
    item_id: int | None = None

    @field_validator("sku", "bin_name")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


def _pool_baseline(session: Session, sku: str, bin_name: str):
    """The sales-window anchor for a SKU's tag pool in a bin: newest
    pairing among its live tags there, or a newer confirmed on-hand
    write, whichever is later (same rule as _shelf_reconcile)."""
    key = sku.strip().upper()
    base = None
    for t in session.scalars(
        select(RfidAssignment).where(func.upper(RfidAssignment.sku) == key)
    ):
        if not bin_contains(t.bin_location, bin_name):
            continue
        ts = orders_sync._as_utc(t.assigned_at)
        if ts is not None and (base is None or ts > base):
            base = ts
    for bc in session.scalars(
        select(BarcodeChange).where(
            func.upper(BarcodeChange.sku) == key,
            BarcodeChange.changed_field.in_((
                "on-hand", "on-hand-undo",
                "on-hand-lower", "on-hand-lower-undo",
            )),
        )
    ):
        ts = orders_sync._as_utc(bc.changed_at)
        if ts is not None and (base is None or ts > base):
            base = ts
    return base


@app.post(
    "/api/onhand-updates/lower",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def lower_on_hand(
    payload: OnHandLowerIn, session: Session = Depends(get_session)
):
    require_shopify_write("verify_onhand_lower")
    if config.check_shopify_env():
        raise HTTPException(500, "Shopify credentials are not configured.")
    live = shopify.get_on_hand(payload.sku)
    if live is None:
        raise HTTPException(404, f"No Shopify product for SKU {payload.sku}.")
    drop = live - payload.new_qty
    if drop < 1:
        raise HTTPException(
            422,
            f"Shopify shows {live} on hand for {payload.sku}; this path "
            f"only LOWERS a count. Use the raise button for increases.",
        )
    key = payload.sku.strip().upper()
    baseline = _pool_baseline(session, payload.sku, payload.bin_name)
    allowed = orders_sync.sold_unretired_since_map(
        session, [payload.sku], {key: baseline}
    ).get(key, 0)
    if drop > allowed:
        wf = _short_date(baseline.isoformat()) if baseline else "ever"
        raise HTTPException(
            422,
            f"Recorded sales since {wf} only account for {allowed} "
            f"missing unit(s); lowering {payload.sku} by {drop} is not "
            f"backed by orders. Recount, or fix it in Shopify admin.",
        )
    # Every EPC must be a live, unheard tag of THIS SKU in THIS bin.
    tags: list[RfidAssignment] = []
    for epc in payload.epcs:
        t = session.scalar(
            select(RfidAssignment).where(
                func.upper(RfidAssignment.rfid_id) == epc.strip().upper()
            )
        )
        if t is None or (t.sku or "").strip().upper() != key:
            raise HTTPException(
                422, f"{epc} is not a live tag of {payload.sku}."
            )
        if not bin_contains(t.bin_location, payload.bin_name):
            raise HTTPException(
                422, f"{epc} is not recorded in bin {payload.bin_name}."
            )
        tags.append(t)
    if len(tags) > drop:
        raise HTTPException(
            422,
            f"{len(tags)} tag(s) listed but the count only drops by "
            f"{drop}.",
        )
    if payload.batch_id:
        cap = _latest_shelf_sweep(session, payload.batch_id)
        if cap is not None and cap.epcs:
            heard = {e.strip().upper() for e in cap.epcs.split("\n")}
            for t in tags:
                if t.rfid_id.strip().upper() in heard:
                    raise HTTPException(
                        422,
                        f"{t.rfid_id} answered the shelf sweep; a heard "
                        f"tag is a box on the shelf, not a sale.",
                    )
    if not payload.confirmed:
        raise HTTPException(
            409,
            f"This lowers Shopify on-hand for {payload.sku} from {live} "
            f"to {payload.new_qty}, retires {len(tags)} silent tag(s) "
            f"as presumed-sold, and consumes {drop} recorded sale(s). "
            f"Undoable from History. Confirm to proceed.",
        )
    # Local rows first (uncommitted), the external write last: a Shopify
    # failure aborts everything.
    moved_rows: list[tuple[RetiredTag, RfidAssignment]] = []
    for t in tags:
        rt = RetiredTag(
            rfid_id=t.rfid_id,
            sku=t.sku,
            product_title=t.product_title,
            shopify_variant_id=t.shopify_variant_id,
            bin_location=t.bin_location,
            case_units=t.case_units,
            kind="presumed-sold",
            retired_by=payload.changed_by,
        )
        session.add(rt)
        session.add(BarcodeChange(
            sku=t.sku,
            product_title=t.product_title,
            shopify_variant_id=t.shopify_variant_id,
            changed_field="tag-retired",
            old_barcode=t.rfid_id,
            new_barcode="presumed-sold",
            changed_by=payload.changed_by,
        ))
        session.delete(t)
        moved_rows.append((rt, t))
    if moved_rows:
        _consume_ledger_for_retirements(session, moved_rows)
    # The drop may exceed the retired tags (untagged units sold): consume
    # the remainder from the ledger too, so the books stay conserved.
    tagged_units = sum((t.case_units or 1) for _, t in moved_rows)
    if drop > tagged_units:
        orders_sync.retire_units(
            session, payload.sku, drop - tagged_units, since=baseline
        )
    try:
        before = shopify.set_on_hand(payload.sku, payload.new_qty)
    except RuntimeError as error:
        raise HTTPException(502, f"Shopify on-hand write failed: {error}")
    change = BarcodeChange(
        sku=payload.sku,
        changed_field="on-hand-lower",
        old_barcode=str(before),
        new_barcode=str(payload.new_qty),
        changed_by=payload.changed_by,
    )
    session.add(change)
    session.flush()
    for rt, _ in moved_rows:
        rt.note = f"onhand-lower #{change.id}"
    if payload.batch_id and payload.item_id:
        item = session.get(BatchItem, payload.item_id)
        if item is not None and item.batch_id == payload.batch_id:
            item.expected_qty = payload.new_qty
    session.commit()
    session.refresh(change)
    return {
        "sku": payload.sku,
        "before": before,
        "after": payload.new_qty,
        "retired": [t.rfid_id for _, t in moved_rows],
        "change_id": change.id,
        "message": (
            f"Shopify on-hand for {payload.sku}: {before} to "
            f"{payload.new_qty}, {len(moved_rows)} tag(s) retired "
            f"presumed-sold ✓ (undo from History)"
        ),
    }


@app.post(
    "/api/onhand-updates/{change_id}/undo-lower",
    dependencies=[Depends(require_user)],
)
def undo_lower_on_hand(
    change_id: int,
    payload: OnHandUndoIn,
    session: Session = Depends(get_session),
):
    """Reverses a lower_on_hand in full: on-hand back up, the retired
    tags live again, the consumed ledger units restored."""
    require_shopify_write("verify_onhand_lower")
    row = session.get(BarcodeChange, change_id)
    if row is None or row.changed_field != "on-hand-lower" or not row.sku:
        raise HTTPException(404, "No on-hand lowering with that id.")
    old = int(row.old_barcode or 0)
    marker = f"onhand-lower #{change_id}"
    retired_rows = session.scalars(
        select(RetiredTag).where(RetiredTag.note == marker)
    ).all()
    if not payload.confirmed:
        live = shopify.get_on_hand(row.sku)
        raise HTTPException(
            409,
            f"Undo sets Shopify on-hand for {row.sku} back to {old} "
            f"(currently {live}) and restores {len(retired_rows)} "
            f"retired tag(s) and their consumed sales. Confirm to "
            f"write it.",
        )
    try:
        before = shopify.set_on_hand(row.sku, old)
    except RuntimeError as error:
        raise HTTPException(502, f"Shopify on-hand write failed: {error}")
    restored = []
    for r in retired_rows:
        if session.scalar(
            select(RfidAssignment).where(
                func.upper(RfidAssignment.rfid_id)
                == r.rfid_id.strip().upper()
            )
        ) is not None:
            continue  # EPC re-used on a new box since; leave it alone
        session.add(RfidAssignment(
            rfid_id=r.rfid_id,
            shopify_variant_id=r.shopify_variant_id or "",
            product_title=r.product_title or r.sku or "(unknown)",
            sku=r.sku,
            bin_location=r.bin_location,
            case_units=r.case_units,
            assigned_by=payload.changed_by,
        ))
        session.add(BarcodeChange(
            sku=r.sku,
            product_title=r.product_title,
            shopify_variant_id=r.shopify_variant_id,
            changed_field="tag-unretired",
            old_barcode=r.rfid_id,
            new_barcode=r.kind,
            changed_by=payload.changed_by,
        ))
        if r.sku and (r.ledger_consumed or 0) > 0:
            orders_sync.unretire_units(session, r.sku, r.ledger_consumed)
        session.delete(r)
        restored.append(r.rfid_id)
    # Units consumed beyond the tagged ones (untagged sales) come back
    # too: the total drop minus what the tags stood for.
    drop_units = old - int(row.new_barcode or 0)
    untagged = drop_units - sum(
        (r.case_units or 1) for r in retired_rows
    )
    if untagged > 0:
        orders_sync.unretire_units(session, row.sku, untagged)
    session.add(BarcodeChange(
        sku=row.sku,
        changed_field="on-hand-lower-undo",
        old_barcode=str(before),
        new_barcode=str(old),
        changed_by=payload.changed_by,
    ))
    session.commit()
    return {
        "sku": row.sku,
        "before": before,
        "after": old,
        "restored": restored,
        "message": (
            f"Undone. {row.sku} on-hand back to {old}, "
            f"{len(restored)} tag(s) live again."
        ),
    }


class VendorOverwriteIn(BaseModel):
    """Replace a PRODUCT's vendor (brand) in Shopify - the product
    options window's Change vendor button (Nick, 2026-08-26)."""

    new_vendor: str = Field(max_length=150)
    target: str = Field(max_length=100)  # current barcode or SKU
    changed_by: str | None = Field(default=None, max_length=100)
    confirmed: bool = False

    @field_validator("new_vendor", "target")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


@app.post(
    "/api/vendor-overwrites",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def overwrite_vendor(
    payload: VendorOverwriteIn, session: Session = Depends(get_session)
):
    """Write a new vendor to the product in Shopify - audited like the
    SKU/barcode overwrites (History row, no undo endpoint: run it again
    with the old name to reverse). Vendor is PRODUCT-level, so every
    variant of the product changes brand together."""
    if not payload.confirmed:
        raise HTTPException(
            422, "Confirmation is required for a vendor change."
        )
    require_shopify_write("scan_station")
    if config.check_shopify_env():
        raise HTTPException(500, "Shopify credentials are not configured.")
    try:
        product = _lookup_api(payload.target)
    except RuntimeError as error:
        raise HTTPException(502, f"Shopify lookup failed: {error}")
    if product is None:
        raise HTTPException(
            404, "No product found in Shopify for that barcode or SKU."
        )
    pid = product.get("shopify_product_id") or ""
    if not pid.startswith("gid://"):
        raise HTTPException(
            502, "The live lookup returned no usable product id."
        )
    old_vendor = None
    rows = session.scalars(
        select(BinMapEntry).where(
            BinMapEntry.shopify_product_id == pid
        )
    ).all()
    if rows:
        old_vendor = rows[0].vendor
    try:
        shopify.update_product_vendor(pid, payload.new_vendor)
    except RuntimeError as error:
        raise HTTPException(502, f"Shopify vendor update failed: {error}")
    # The bin map serves the site's vendor columns until its next full
    # rebuild - follow the product now.
    for row in rows:
        row.vendor = payload.new_vendor
    session.add(BarcodeChange(
        sku=product.get("sku"),
        product_title=product.get("product_title"),
        shopify_variant_id=product.get("shopify_variant_id"),
        changed_field="vendor",
        old_barcode=(old_vendor or "")[:64] or None,
        new_barcode=payload.new_vendor[:64],
        changed_by=payload.changed_by,
    ))
    session.commit()
    return {
        "vendor": payload.new_vendor,
        "old_vendor": old_vendor,
        "message": (
            f"Vendor set to {payload.new_vendor} in Shopify"
            + (f" (was {old_vendor})" if old_vendor else "")
            + " - every variant of the product follows. Logged to "
            "History; run it again with the old name to reverse."
        ),
    }


class SkuOverwriteIn(BaseModel):
    """Replace a product's SKU in Shopify (e.g. store SKU is outdated vs
    the manufacturer's current item number)."""

    new_sku: str = Field(max_length=100)
    target: str = Field(max_length=100)  # current barcode or SKU
    changed_by: str | None = Field(default=None, max_length=100)
    confirmed: bool = False

    @field_validator("new_sku", "target")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


@app.post(
    "/api/sku-overwrites",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def overwrite_sku(
    payload: SkuOverwriteIn, session: Session = Depends(get_session)
):
    if not payload.confirmed:
        raise HTTPException(
            422, "Confirmation checkbox is required for SKU replacement."
        )
    require_shopify_write("scan_station")
    if config.check_shopify_env():
        raise HTTPException(500, "Shopify credentials are not configured.")

    db_ok = database_configured()
    if _resolve(payload.new_sku, config.BARCODE_LOOKUP, db_ok, True):
        raise HTTPException(
            409,
            "That SKU already belongs to a product — it can't replace "
            "another product's SKU.",
        )

    try:
        product = _lookup_api(payload.target)
    except RuntimeError as error:
        raise HTTPException(502, f"Shopify lookup failed: {error}")
    if product is None:
        raise HTTPException(
            404, "No product found in Shopify for that barcode or SKU."
        )

    try:
        shopify.update_variant_sku(
            product["shopify_product_id"],
            product["shopify_variant_id"],
            payload.new_sku,
        )
    except RuntimeError as error:
        raise HTTPException(502, f"Shopify SKU update failed: {error}")

    old_sku = product.get("sku")
    session.add(BarcodeChange(
        sku=payload.new_sku,
        product_title=product.get("product_title"),
        shopify_variant_id=product.get("shopify_variant_id"),
        changed_field="sku",
        old_barcode=old_sku,
        new_barcode=payload.new_sku,
        changed_by=payload.changed_by,
    ))
    # Serial prefixes that pointed at the old SKU follow the product.
    if old_sku:
        for row in session.scalars(
            select(SerialPrefix).where(SerialPrefix.sku == old_sku)
        ):
            row.sku = payload.new_sku
    # Same staleness rule as the barcode overwrite: the bin map serves
    # lookups first, and paired tags carry the SKU too. Both follow the
    # product now, not at the next rebuild.
    crit = [BinMapEntry.shopify_variant_id
            == product.get("shopify_variant_id")]
    if (old_sku or "").strip():
        crit.append(func.upper(BinMapEntry.sku)
                    == old_sku.strip().upper())
    for bm in session.scalars(select(BinMapEntry).where(or_(*crit))):
        bm.sku = payload.new_sku
    # Rows of OPEN batches follow too - the Check list and the C72 pull
    # display values from them, and both kept showing the stale SKU
    # after a fix (Nick, 2026-08-26).
    _refresh_item_idents(session, product, sku=payload.new_sku)
    if old_sku:
        for tag in session.scalars(
            select(RfidAssignment).where(
                func.upper(RfidAssignment.sku) == old_sku.strip().upper()
            )
        ):
            tag.sku = payload.new_sku
        # Aliases anchored to the old SKU resolve through it — they
        # follow the product too, or every link would quietly die here.
        for al in session.scalars(
            select(BarcodeAlias).where(
                func.upper(BarcodeAlias.sku) == old_sku.strip().upper()
            )
        ):
            al.sku = payload.new_sku
    # A broken old SKU (mojibake — ZWO's 'Ⅱ' stored as '?') stays linked:
    # labels printed with it keep scanning to this product.
    legacy_linked = _link_replaced_value(
        session, old_sku,
        {**product, "sku": payload.new_sku}, payload.changed_by,
    )
    session.commit()

    product["sku"] = payload.new_sku
    return {"product": product, "legacy_linked": legacy_linked}


@app.get("/api/barcode-overwrites", dependencies=[Depends(require_user)])
def list_barcode_overwrites(
    limit: int = 100, session: Session = Depends(get_session)
):
    rows = session.scalars(
        select(BarcodeChange)
        .order_by(BarcodeChange.id.desc())
        .limit(min(limit, 500))
    ).all()
    return {"count": len(rows), "changes": [c.as_dict() for c in rows]}


# -------------------------------------------------------- inventory view ---
# Live-quantity cache: refreshing on every tab visit is the useful moment,
# but scan sessions reload the tab constantly — cache briefly.
_qty_cache: dict = {"key": None, "at": 0.0, "data": {}}
_QTY_CACHE_TTL = 120  # seconds


def _live_quantities(skus: list[str]) -> dict[str, int]:
    key = tuple(sorted(skus))
    now = time.time()
    if _qty_cache["key"] == key and now - _qty_cache["at"] < _QTY_CACHE_TTL:
        return _qty_cache["data"]
    data = shopify.get_quantities_by_skus(skus)
    _qty_cache.update(key=key, at=now, data=data)
    return data


@app.get("/api/inventory/summary", dependencies=[Depends(require_user)])
def inventory_summary(session: Session = Depends(get_session)):
    """One row per product in the RFID system: identity, bin, tag count,
    newest tag date — plus current LIVE Shopify on-hand, so tag counts can
    be eyeballed against stock levels."""
    rows = session.execute(
        select(
            RfidAssignment.sku,
            RfidAssignment.barcode,
            func.max(RfidAssignment.product_title).label("product_title"),
            func.max(RfidAssignment.variant_title).label("variant_title"),
            func.max(RfidAssignment.bin_location).label("bin_location"),
            func.count().label("tag_count"),
            func.max(RfidAssignment.assigned_at).label("last_assigned_at"),
        ).group_by(RfidAssignment.sku, RfidAssignment.barcode)
    ).all()

    # A tag on a sealed case stands for several units, so tags no longer
    # equal units. Both numbers are reported: the total, and how it splits
    # ("2 + 8x1" = two loose, plus one case of eight).
    case_tags: dict = {}
    for sku, barcode, units, n in session.execute(
        select(
            RfidAssignment.sku, RfidAssignment.barcode,
            RfidAssignment.case_units, func.count().label("n"),
        )
        .where(RfidAssignment.case_units.isnot(None))
        .group_by(RfidAssignment.sku, RfidAssignment.barcode,
                  RfidAssignment.case_units)
    ):
        case_tags.setdefault((sku, barcode), []).append((units or 0, n))

    def _units_for(r) -> dict:
        cases = case_tags.get((r.sku, r.barcode), [])
        if not cases:
            return {"unit_count": r.tag_count, "unit_breakdown": None}
        packed = sum(units * n for units, n in cases)
        case_tag_count = sum(n for _, n in cases)
        loose = r.tag_count - case_tag_count
        parts = [str(loose)] + [f"{units}x{n}" for units, n in cases]
        return {
            "unit_count": loose + packed,
            "unit_breakdown": " + ".join(parts),
        }

    noscan = _noscan_skus(session)
    products = [
        {
            "sku": r.sku,
            "barcode": r.barcode,
            "product_title": r.product_title,
            "variant_title": r.variant_title,
            "bin_location": r.bin_location,
            "tag_count": r.tag_count,
            **_units_for(r),
            "last_assigned_at": (
                r.last_assigned_at.isoformat() if r.last_assigned_at else None
            ),
            "shopify_qty": None,
            "vendor": None,
            "rfid_incompatible": (r.sku or "").strip().upper() in noscan,
        }
        for r in rows
    ]
    products.sort(key=lambda p: p["last_assigned_at"] or "", reverse=True)

    # Vendor (the brand) for filtering and sorting. The bin map holds it
    # live from Shopify. Some products genuinely have no vendor set —
    # those stay blank. (The TELCAN mirror used to fall back here for
    # unbinned products — removed 2026-08-07 with the rest of the mirror.)
    vendor_by_sku: dict = {}
    try:
        for sku, vendor in session.execute(
            select(BinMapEntry.sku, BinMapEntry.vendor)
            .where(BinMapEntry.vendor.isnot(None))
        ):
            if sku:
                vendor_by_sku.setdefault(sku, vendor)
    except Exception as error:
        logger.warning("vendor lookup (bin map) failed: %s", error)

    skus = [p["sku"] for p in products if p["sku"]]

    # Live Shopify quantities; a product the API can't answer for shows
    # no number rather than a stale one.
    if skus and not config.check_shopify_env():
        try:
            live = _live_quantities(skus)
            for p in products:
                if p["sku"] in live:
                    p["shopify_qty"] = live[p["sku"]]
        except Exception as error:
            # Broad on purpose: a Shopify hiccup (auth, network, HTTP —
            # requests raises its own types, not RuntimeError) must degrade
            # to the mirror numbers, never 500 the whole Inventory tab.
            logger.warning("live quantity fetch failed: %s", error)

    # Product GID for "open in Shopify admin" links — the live bin map is
    # the reliable source (historical assignments carried surrogate ids no
    # admin URL can be built from). Its bin also rides along so each row
    # can compare Shopify's shelf against where the tags actually are.
    gid_by_sku: dict = {}
    shopify_bin_by_sku: dict = {}
    try:
        for sku, pid, bin_, other in session.execute(
            select(
                BinMapEntry.sku,
                BinMapEntry.shopify_product_id,
                BinMapEntry.bin,
                BinMapEntry.other_bins,
            )
        ):
            key = (sku or "").strip().upper()
            if not key:
                continue
            if pid and str(pid).startswith("gid://"):
                gid_by_sku.setdefault(key, pid)
            full = ", ".join(x for x in ((bin_ or "").strip(), other) if x)
            if full:
                shopify_bin_by_sku.setdefault(key, full)
    except Exception as error:
        logger.warning("gid lookup failed: %s", error)

    for p in products:
        key = (p["sku"] or "").strip().upper()
        p["vendor"] = vendor_by_sku.get(p["sku"])
        p["shopify_product_id"] = gid_by_sku.get(key)
        p["shopify_bin"] = shopify_bin_by_sku.get(key)
        # The tags were placed by hand at a real shelf — when Shopify's
        # bin disagrees (or is missing), the row offers to write the
        # tags' bin to Shopify via the existing audited update.
        rfid_bin = (p["bin_location"] or "").strip()
        p["bin_differs"] = bool(
            rfid_bin
            and rfid_bin not in MISSING_BIN_VALUES
            and p["sku"]
            and not bin_contains(p["shopify_bin"] or "", rfid_bin)
        )

    return {
        "count": len(products),
        "products": products,
        # Everything the filters can offer, so the UI doesn't have to
        # derive them and can show them sorted.
        "bins": sorted(
            {
                p["bin_location"] for p in products
                if p["bin_location"] and p["bin_location"] not in
                MISSING_BIN_VALUES
            },
            key=lambda b: b.lower(),
        ),
        "vendors": sorted(
            {p["vendor"] for p in products if p["vendor"]},
            key=lambda v: v.lower(),
        ),
    }


@app.post(
    "/api/print-jobs/{job_id}/cancel", dependencies=[Depends(require_user)]
)
def cancel_print_job(job_id: int, session: Session = Depends(get_session)):
    job = session.get(PrintJob, job_id)
    if job is None:
        raise HTTPException(404, "No such print job.")
    if job.status != "pending":
        raise HTTPException(409, f"Only pending jobs can be canceled "
                                 f"(job is {job.status}).")
    job.status = "canceled"
    session.commit()
    return job.as_dict()


# ------------------------------------------------------------ bin batches ---
# The warehouse walk-around workflow (Batch Tagging tab):
#   collect (scan every box at a bin) -> prepare labels -> print (queue)
#   -> pair (barcode selects product, EPC scans attach) -> verify -> done.
# Batches only OBSERVE — no Shopify writes anywhere in this flow. Count and
# bin mismatches become ReviewTasks at completion, never live edits.

# --- Bin map: which bin each variant lives in (Shopify metafields) --------
# Rebuilt by a daemon thread (full catalog walk, ~1 min); the table itself
# persists across restarts so reads never wait on the walk.
import threading

_BIN_MAP_TTL = 6 * 60 * 60  # refresh when older than 6 hours
_bin_map_state = {"checked_at": 0.0, "running": False}
_bin_map_lock = threading.Lock()


def _rebuild_bin_map() -> None:
    from app.database import get_engine

    try:
        # Entries carry LIVE on-hand straight from Shopify inventory levels
        # (never the TELCAN mirror's quantities — its sync can stall for
        # months and it burned us once with 8-month-old numbers).
        entries = shopify.fetch_all_variant_bins()
        with Session(get_engine()) as session:
            # Serialize rewrites across gunicorn workers: without this,
            # two workers booting onto a stale map both insert and the
            # table doubles.
            if session.get_bind().dialect.name == "mssql":
                session.execute(text(
                    "EXEC sp_getapplock @Resource='rfid_bin_map_rebuild', "
                    "@LockMode='Exclusive', @LockOwner='Transaction', "
                    "@LockTimeout=120000"
                ))
            session.query(BinMapEntry).delete()
            rows = []
            for e in entries:
                # A product split across shelves ("G2-1 & B17") belongs to
                # BOTH bins — one row each, each naming the others.
                bins = parse_bins(e["bin"]) or [e["bin"]]
                for name in bins:
                    # From this shelf's point of view — keeps repeats, so
                    # two boxes on one shelf read as two.
                    others = bins_other_than(e["bin"], name)
                    rows.append(BinMapEntry(
                        sku=e["sku"],
                        barcode=e["barcode"],
                        product_title=(e["product_title"] or "")[:255] or None,
                        variant_title=(e["variant_title"] or "")[:255] or None,
                        shopify_variant_id=e["shopify_variant_id"],
                        shopify_product_id=e["shopify_product_id"],
                        bin=name[:100],
                        other_bins=(", ".join(others))[:255] or None,
                        qty=e["qty"],
                        unavailable=e.get("unavailable") or 0,
                        image_url=(e.get("image_url") or "")[:500] or None,
                        vendor=(e.get("vendor") or "")[:150] or None,
                    ))
            session.add_all(rows)
            session.commit()
        logger.info("bin map rebuilt: %d binned variants -> %d bin rows",
                    len(entries), len(rows))
    except Exception as error:
        logger.warning("bin map rebuild failed: %s", error)
    finally:
        with _bin_map_lock:
            _bin_map_state["running"] = False


def _bin_map_age(session: Session) -> float | None:
    """Seconds since the newest entry; None when the table is empty."""
    newest = session.scalar(select(func.max(BinMapEntry.updated_at)))
    if newest is None:
        return None
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - newest).total_seconds()


def _maybe_refresh_bin_map(force: bool = False,
                           max_age: float | None = None) -> bool:
    """Kick a background rebuild when the map is stale/empty. Returns True
    if a rebuild is running after the call. `max_age` overrides the normal
    TTL — batch start uses a short one so bin edits land quickly."""
    if config.check_shopify_env() or not database_configured():
        return False
    with _bin_map_lock:
        if _bin_map_state["running"]:
            return True
        try:
            from app.database import get_engine

            with Session(get_engine()) as session:
                age = _bin_map_age(session)
        except Exception as error:
            logger.warning("bin map age check failed: %s", error)
            return False
        ttl = _BIN_MAP_TTL if max_age is None else max_age
        if not force and age is not None and age < ttl:
            return False
        _bin_map_state["running"] = True
    threading.Thread(target=_rebuild_bin_map, daemon=True).start()
    return True


# Some products are one sellable item split across shelves, and the bin
# field says so: "G2-1 & B17", "MOUNT: B18-1, BATTERY: G1-4",
# "SCOPE: B17-2, TOOL: G3-2, KIT: G3-2". Each of those is a real bin the
# product legitimately lives in.
_BIN_SPLIT_RE = re.compile(r"\s*(?:[&,;/+]|\band\b)\s*", re.I)


def parse_bin_parts(value: str | None) -> list[str]:
    """Every box this product is stored as, in order — duplicates KEPT.
    Two boxes on the same shelf are two entries, because "Other: G3-2,
    G3-2" tells a picker there are two of them there. Part labels
    ("MOUNT: B18-1") are dropped; the shelf code is what matters."""
    if not value:
        return []
    parts: list[str] = []
    for part in _BIN_SPLIT_RE.split(str(value)):
        part = part.strip()
        if not part:
            continue
        if ":" in part:  # "MOUNT: B18-1" -> "B18-1"
            part = part.rsplit(":", 1)[-1].strip()
        if not part or part.lower() == "no bin assigned":
            continue
        parts.append(part)
    return parts


# A listing that occupies several box slots is either one product shipped in
# several boxes or a bundle of separate products. The catalog almost always
# says which: bundles are titled "BUNDLE: ..." and their SKUs are composites
# of the component SKUs ("91519+93973", "91523-BUNDLE-SkyPortal").
_BUNDLE_SKU_RE = re.compile(r"\+|-BUNDLE-", re.I)


def guess_product_kind(
    product_title: str | None, sku: str | None, bin_value: str | None
) -> str | None:
    """'bundle' | 'multi_box' | None. None means there is nothing to decide:
    the product occupies a single box slot, so it is just a normal product.

    A guess, not a verdict — the operator can override it per SKU, because
    nothing stops someone creating a bundle that skips the convention."""
    if len(parse_bin_parts(bin_value)) < 2:
        return None
    title = (product_title or "").strip()
    if title.upper().startswith("BUNDLE:") or "BUNDLE:" in title.upper():
        return "bundle"
    if sku and _BUNDLE_SKU_RE.search(sku):
        return "bundle"
    return "multi_box"


def resolve_product_kind(
    session: Session,
    product_title: str | None,
    sku: str | None,
    bin_value: str | None,
) -> tuple[str | None, bool]:
    """The effective (kind, excluded) for a product: the operator's saved
    answer wins over the guess, since they have the box in their hands."""
    guess = guess_product_kind(product_title, sku, bin_value)
    if not sku:
        return guess, False
    saved = session.get(ProductKind, sku)
    if saved is None:
        return guess, False
    # A saved answer applies even when the bin metafield has since changed
    # to a single slot — the operator saw the physical goods.
    return saved.kind, bool(saved.excluded)


def parse_bins(value: str | None) -> list[str]:
    """The distinct shelves a product lives on — for bin membership and
    for listing a bin's contents once each."""
    bins: list[str] = []
    seen: set = set()
    for part in parse_bin_parts(value):
        if part.lower() not in seen:
            seen.add(part.lower())
            bins.append(part)
    return bins


def bin_contains(value: str | None, wanted: str) -> bool:
    """Is `wanted` one of the bins this product lives in?"""
    target = (wanted or "").strip().lower()
    return any(b.lower() == target for b in parse_bins(value))


def bins_other_than(value: str | None, wanted: str) -> list[str]:
    """The product's OTHER boxes, from the point of view of one bin: drop
    a single occurrence of `wanted` (the box in your hand) and keep the
    rest — including repeats of the same shelf."""
    target = (wanted or "").strip().lower()
    parts = parse_bin_parts(value)
    for i, part in enumerate(parts):
        if part.lower() == target:
            return parts[:i] + parts[i + 1:]
    return parts


# A well-formed bin is one letter + 1-99, a dash, then 1-99 (D2-2, E14-3).
# Anything else — extra letters like "B19B-2", missing parts, stray text —
# usually means one product's stock is split across shelves, which needs
# sorting out in Shopify before that bin can be tagged cleanly.
_BIN_NAME_RE = re.compile(r"[A-Za-z](?:[1-9]|[1-9][0-9])-(?:[1-9]|[1-9][0-9])")


@app.get("/api/bins/overview", dependencies=[Depends(require_user)])
def bins_overview(recent: int = 8, session: Session = Depends(get_session)):
    """Every bin in the store (from the Shopify bin map) split into
    still-to-do and recently finished, so Batch Tagging can offer a work
    list instead of an empty box."""
    counts = session.execute(
        select(BinMapEntry.bin, func.count())
        .where(BinMapEntry.bin.isnot(None))
        .group_by(BinMapEntry.bin)
    ).all()

    last_done: dict = {}
    open_by_bin: dict = {}
    done_batches: list = []
    for b in session.scalars(select(Batch).order_by(Batch.id)):
        key = (b.bin_name or "").strip().lower()
        # Receiving batches have no bin of their own — they must neither
        # count any bin as done nor appear as a bin's continue-batch.
        if b.kind == "receiving":
            continue
        side = b.parent_batch_id is not None
        if b.status == "done":
            # A side trip only ever covered the few boxes carried over —
            # it shows in Recently done (labelled), but it must NOT count
            # the bin as checked: the rest of that shelf was never touched.
            if not side:
                last_done[key] = b
            done_batches.append(b)
        elif b.status != "abandoned" and not side:
            # An open side trip isn't "this bin is in progress" either —
            # offering it as the bin's continue-batch would drop whoever
            # clicks it into a trip that covers two boxes, not the shelf.
            open_by_bin[key] = b

    # Box/tag totals for the recent list.
    recent_batches = sorted(
        done_batches,
        key=lambda b: (_aware(b.completed_at) or datetime.min.replace(
            tzinfo=timezone.utc)),
        reverse=True,
    )[: max(1, min(recent, 20))]
    totals: dict = {}
    if recent_batches:
        for r in session.execute(
            select(
                BatchItem.batch_id,
                func.count().label("products"),
                func.sum(BatchItem.qty_scanned).label("boxes"),
                func.sum(BatchItem.paired_count).label("tags"),
            )
            .where(BatchItem.batch_id.in_([b.id for b in recent_batches]))
            .group_by(BatchItem.batch_id)
        ):
            totals[r.batch_id] = r

    hidden = {
        (h.bin or "").strip().lower()
        for h in session.scalars(select(HiddenBin))
    }
    # "Ask first" flags: bin -> optional note.
    flagged = {
        (f.bin or "").strip().lower(): f.note
        for f in session.scalars(select(FlaggedBin))
    }

    todo = []
    done = []
    done_bins = 0
    malformed_total = 0
    for name, products in counts:
        key = (name or "").strip().lower()
        if not key:
            continue
        odd_name = _BIN_NAME_RE.fullmatch((name or "").strip()) is None
        if odd_name:
            malformed_total += 1
        if key in last_done:
            done_bins += 1
            db_ = last_done[key]
            done.append({
                "bin": name,
                "products": products,
                "batch_id": db_.id,
                "by": db_.created_by,
                "completed_at": (
                    db_.completed_at.isoformat()
                    if db_.completed_at else None
                ),
            })
            continue
        openb = open_by_bin.get(key)
        todo.append({
            "bin": name,
            "products": products,
            "open_batch_id": openb.id if openb else None,
            "hidden": key in hidden,
            "malformed": odd_name,
            "flagged": key in flagged,
            "flag_note": flagged.get(key),
        })
    # Bins already in progress first, then the biggest jobs.
    todo.sort(key=lambda b: (b["open_batch_id"] is None, -b["products"],
                             b["bin"]))
    done.sort(key=lambda b: b["completed_at"] or "", reverse=True)

    return {
        "total_bins": len(counts),
        "done_bins": done_bins,
        "done": done,
        "todo_count": sum(1 for b in todo if not b["hidden"]),
        "hidden_count": sum(1 for b in todo if b["hidden"]),
        "malformed_count": malformed_total,
        "flagged_count": sum(1 for b in todo if b["flagged"]),
        "todo": todo,
        "recent": [
            {
                "batch_id": b.id,
                "bin": b.bin_name,
                "completed_at": (
                    b.completed_at.isoformat() if b.completed_at else None
                ),
                "by": b.created_by,
                "side_trip": b.parent_batch_id is not None,
                "products": totals[b.id].products if b.id in totals else 0,
                "boxes": int(totals[b.id].boxes or 0) if b.id in totals else 0,
                "tags": int(totals[b.id].tags or 0) if b.id in totals else 0,
            }
            for b in recent_batches
        ],
    }


class HideBinIn(BaseModel):
    hidden: bool = True
    hidden_by: str | None = Field(default=None, max_length=100)


@app.put(
    "/api/bins/{bin_name}/hidden", dependencies=[Depends(require_user)]
)
def set_bin_hidden(
    bin_name: str,
    payload: HideBinIn,
    session: Session = Depends(get_session),
):
    """Tick a bin off the work list (or put it back). Local only — the bin
    and its products are untouched, it just stops nagging."""
    name = bin_name.strip()
    if not name:
        raise HTTPException(422, "Bin required.")
    row = session.get(HiddenBin, name)
    if payload.hidden:
        if row is None:
            session.add(HiddenBin(bin=name, hidden_by=payload.hidden_by))
    elif row is not None:
        session.delete(row)
    session.commit()
    return {"bin": name, "hidden": payload.hidden}


class FlagBinIn(BaseModel):
    flagged: bool = True
    note: str | None = Field(default=None, max_length=255)
    flagged_by: str | None = Field(default=None, max_length=100)


@app.put(
    "/api/bins/{bin_name}/flagged", dependencies=[Depends(require_user)]
)
def set_bin_flagged(
    bin_name: str,
    payload: FlagBinIn,
    session: Session = Depends(get_session),
):
    """Mark a bin "ask first" (or clear it): the operator wants a word with
    someone who knows the inventory before scanning it. A warning on the
    work list only — nothing is hidden or blocked, and re-flagging updates
    the note."""
    name = bin_name.strip()
    if not name:
        raise HTTPException(422, "Bin required.")
    row = session.get(FlaggedBin, name)
    note = (payload.note or "").strip() or None
    if payload.flagged:
        if row is None:
            row = FlaggedBin(bin=name)
            session.add(row)
        row.note = note
        row.flagged_by = payload.flagged_by
    elif row is not None:
        session.delete(row)
    session.commit()
    return {"bin": name, "flagged": payload.flagged, "note": note}


def _fold_plain(value: str | None) -> str:
    """NFKC-fold and uppercase: lookalike unicode reads as its plain
    counterpart (Roman numeral Ⅱ -> II, fullwidth digits, etc.)."""
    return unicodedata.normalize("NFKC", value or "").strip().upper()


def _fold_seps(value: str | None) -> str:
    """On top of the plain fold, separator runs (space, hyphen,
    underscore, dot, slash) vanish - "ZWO-T2-Tilter-II" and
    "ZWO T2-Tilter II" become one key. Used for RECOMMENDING a match,
    never for silently resolving one."""
    return re.sub(r"[\s\-_./]+", "", _fold_plain(value))


@app.get(
    "/api/products/label-match/{code:path}",
    dependencies=[Depends(require_user)],
)
def product_label_match(code: str, session: Session = Depends(get_session)):
    """The shipment sorter's second try (Nick, 2026-08-31, the Buckeye
    box labels): a vendor's under-barcode string is USUALLY our SKU but
    sometimes differs by separators (EAF-FTF30 vs sku EAF-FTF-30) or is
    really the VARIANT name (ZWO-Slider-Gen2 vs variant 'ZWO Slider
    Gen2' on sku ZWO-SliderCase-Gen2). Separator folds only - never
    edit distance (house rule) - across sku, barcode, then variant
    title; only a UNIQUE product answers, ambiguity returns candidates
    for a human. Read-only."""
    term = (code or "").strip()
    fold = _fold_seps(term)
    if len(fold) < 4:
        raise HTTPException(
            404, "Label string too short to match safely."
        )
    hits: dict[str, tuple[str, str, BinMapEntry]] = {}
    for row in session.scalars(select(BinMapEntry)):
        for field, value in (
            ("sku", row.sku),
            ("barcode", row.barcode),
            ("variant name", row.variant_title),
        ):
            if value and _fold_seps(value) == fold:
                key = row.shopify_variant_id or row.sku or value
                hits.setdefault(key, (field, value, row))
                break
    if not hits:
        # Tier 3, SUGGESTION only (Nick, 2026-08-31 - RigelQF-Synta vs
        # Synta-RigelQuikfinder): every token of the scan must be
        # accounted for by a candidate token - exactly, or by a shared
        # prefix of 5+ characters (word order free, abbreviated tails
        # allowed). Enough to ASK a human, never to resolve: the sorter
        # shows it with link/overwrite buttons. Short tokens must match
        # exactly, so Tilter-I never suggests Tilter-II.
        scan_tokens = [t for t in re.split(
            r"[\s\-_./]+", _fold_plain(term)) if t]

        def _tok_match(t: str, c: str) -> bool:
            if t == c:
                return True
            if len(t) < 5 or len(c) < 5:
                return False
            n = 0
            for a, b in zip(t, c):
                if a != b:
                    break
                n += 1
            return n >= 5

        suggestions: dict[str, BinMapEntry] = {}
        if scan_tokens:
            for row in session.scalars(select(BinMapEntry)):
                cand_tokens = set()
                for value in (row.sku, row.variant_title):
                    for t in re.split(r"[\s\-_./]+", _fold_plain(value)):
                        if t:
                            cand_tokens.add(t)
                if all(any(_tok_match(t, c) for c in cand_tokens)
                       for t in scan_tokens):
                    key = row.shopify_variant_id or row.sku or ""
                    suggestions.setdefault(key, row)
                if len(suggestions) > 3:
                    break
        if len(suggestions) == 1:
            row = next(iter(suggestions.values()))
            return {
                "ok": False,
                "suggestion": {
                    "sku": row.sku,
                    "barcode": row.barcode,
                    "product_title": row.product_title,
                    "variant_title": row.variant_title,
                    "bin_location": row.bin,
                    "image_url": row.image_url,
                },
                "why": (
                    "every part of the scanned label matches this "
                    "product's SKU or variant name"
                ),
            }
        raise HTTPException(404, "No catalog match for that label.")
    if len(hits) > 1:
        return {
            "ok": False,
            "ambiguous": True,
            "candidates": [
                {"sku": r.sku, "product_title": r.product_title,
                 "matched_by": f, "matched_value": v}
                for f, v, r in list(hits.values())[:5]
            ],
        }
    field, value, row = next(iter(hits.values()))
    return {
        "ok": True,
        "matched_by": field,
        "matched_value": value,
        "product": {
            "sku": row.sku,
            "barcode": row.barcode,
            "product_title": row.product_title,
            "variant_title": row.variant_title,
            "shopify_variant_id": row.shopify_variant_id,
            "shopify_product_id": row.shopify_product_id,
            "bin_location": row.bin,
            "image_url": row.image_url,
        },
    }


@app.get(
    "/api/bins/{bin_name}/odd-barcodes", dependencies=[Depends(require_user)]
)
def bin_odd_barcodes(
    bin_name: str,
    scanned: str | None = None,
    session: Session = Depends(get_session),
):
    """Products in a bin whose Shopify barcode isn't a real 13-digit code —
    the usual reason a box scans as unresolved (the barcode field was left
    as the SKU or a placeholder). Prime suspects first: barcode identical
    to the SKU, then blank, then other odd lengths.

    `scanned` (the code that wouldn't resolve) also returns `recommended`:
    a product in this bin whose SKU or barcode matches it - exactly, or
    after SPECIFIC folds (Nick, 2026-08-26, the ZWO T2-Tilter-II case:
    SKU carried a Roman-numeral Ⅱ, barcode swapped its space for a
    hyphen). Lookalike characters are read plainly (NFKC: Ⅱ -> II) and
    separator runs (space/hyphen/underscore/dot/slash) are treated as
    interchangeable. Deliberately NEVER edit distance: one character off
    is how many GENUINE neighboring SKUs differ."""
    rows = session.scalars(
        select(BinMapEntry)
        .where(func.lower(BinMapEntry.bin) == bin_name.strip().lower())
        .order_by(BinMapEntry.product_title)
    ).all()

    def odd(entry) -> bool:
        bc = (entry.barcode or "").strip()
        return not (len(bc) == 13 and bc.isdigit())

    def rank(entry) -> tuple:
        bc = (entry.barcode or "").strip()
        sku = (entry.sku or "").strip()
        if bc and sku and bc.lower() == sku.lower():
            return (0,)  # barcode field holds the SKU — classic placeholder
        if not bc:
            return (1,)
        return (2,)

    candidates = sorted([e for e in rows if odd(e)], key=rank)
    payload = [
        {
            "shopify_variant_id": e.shopify_variant_id,
            "shopify_product_id": e.shopify_product_id,
            "product_title": e.product_title,
            "variant_title": e.variant_title,
            "sku": e.sku,
            "barcode": e.barcode,
            "bin_location": e.bin,
            "image_url": e.image_url,
            "reason": (
                "barcode is the SKU" if rank(e) == (0,)
                else "no barcode set" if rank(e) == (1,)
                else "barcode isn't 13 digits"
            ),
        }
        for e in candidates
    ]
    recommended = None
    if scanned and scanned.strip():
        term = scanned.strip()
        # Tier 0 is the old exact match; tier 1 reads lookalike unicode
        # plainly; tier 2 additionally treats separator runs as one and
        # the same. Lower tier wins, SKU beats barcode within a tier.
        # The whole BIN's rows are searched (not just the odd-barcode
        # candidates): a printed SKU label can miss a product whose real
        # barcode is perfectly fine.
        tiers = [
            (lambda v: (v or "").strip().lower(),
             "matches the scanned code exactly"),
            (_fold_plain,
             "matches the scan once lookalike characters are read "
             "plainly (Roman numeral II and friends)"),
            (_fold_seps,
             "matches the scan once spaces, hyphens, underscores and "
             "dots are treated the same"),
        ]
        keys = [fold(term) for fold, _why in tiers]

        def match_rank(entry):
            for t, (fold, why) in enumerate(tiers):
                key = keys[t]
                # A separator-only key ("A-1" -> "A1") is too little
                # signal to hang a recommendation on.
                if not key or (t == 2 and len(key) < 3):
                    continue
                for fp, (fname, value) in enumerate(
                    (("SKU", entry.sku), ("barcode", entry.barcode))
                ):
                    if value and fold(value) == key:
                        return (t, fp, fname, why)
            return None

        best = None
        for e in rows:
            r = match_rank(e)
            if r is not None and (best is None or r[:2] < best[0][:2]):
                best = (r, e)
        if best is not None:
            (_t, _fp, fname, why), e = best
            recommended = {
                "shopify_variant_id": e.shopify_variant_id,
                "shopify_product_id": e.shopify_product_id,
                "product_title": e.product_title,
                "variant_title": e.variant_title,
                "sku": e.sku,
                "barcode": e.barcode,
                "bin_location": e.bin,
                "image_url": e.image_url,
                "reason": f"{fname} {why}",
            }
    return {
        "count": len(payload),
        "candidates": payload,
        "recommended": recommended,
    }


class BinCheckIn(BaseModel):
    epcs: list[str] = Field(default_factory=list, max_length=5000)
    # Extra SKUs to report on beyond the bin map — the C72 sends its
    # batch's SKUs, because a batch legitimately holds products the map
    # doesn't put in this bin (open-box twins, strays kept here, map
    # lag) and their tags deserve real counts, not a blank row.
    skus: list[str] = Field(default_factory=list, max_length=500)


@app.post("/api/bins/{bin_name}/check", dependencies=[Depends(require_user)])
def bin_check(
    bin_name: str,
    payload: BinCheckIn,
    session: Session = Depends(get_session),
):
    """What a sweep says about ANY bin: for every product Shopify expects
    there, how many of its tags on file were detected. Read-only.

    A dash-less location ("F1") is a RACK: every bin on that shelf is
    audited as ONE zone (Nick, 2026-09-01 - the C72's read field can't
    localize below a rack anyway; bins are just levels of one shelf)."""
    swept = {(e or "").strip().upper() for e in payload.epcs if e}
    loc = bin_name.strip()
    bin_keys = [loc.lower()]
    rack = False
    if "-" not in loc and loc:
        prefixed = sorted({
            (b or "").strip().lower()
            for (b,) in session.execute(
                select(BinMapEntry.bin).where(
                    func.lower(BinMapEntry.bin).like(loc.lower() + "-%")
                )
            )
            if b
        })
        if prefixed:
            rack = True
            bin_keys = prefixed
    bin_key_set = set(bin_keys)
    rows = session.scalars(
        select(BinMapEntry)
        .where(func.lower(BinMapEntry.bin).in_(bin_keys))
        .order_by(BinMapEntry.product_title)
    ).all()
    # One query for every tag on file for the bin's products PLUS any
    # caller-requested SKUs — this used to be one query per product,
    # which timed real bins out. Upper-matched: sqlite compares IN()
    # case-sensitively, and tag casing has never been guaranteed.
    wanted = {e.sku.strip().upper() for e in rows if e.sku}
    extra = {
        s.strip().upper() for s in payload.skus if s and s.strip()
    } - wanted
    tags_by_sku: dict[str, list[RfidAssignment]] = {}
    if wanted or extra:
        for t in session.scalars(
            select(RfidAssignment).where(
                func.upper(RfidAssignment.sku).in_(sorted(wanted | extra))
            )
        ):
            tags_by_sku.setdefault((t.sku or "").upper(), []).append(t)
    noscan = _noscan_skus(session)
    # Sold-but-unretired units per SKU: the audit's licence to explain a
    # silent tag as "that box shipped" and offer MARK SOLD.
    sold_map = orders_sync.sold_unretired_map(
        session, sorted(wanted | extra)
    )
    # Uncleared backorder debt RAISES what the shelf should hold: those
    # boxes arrived and stand here, but Shopify's on-hand ran behind.
    debt_map: dict[str, int] = {}
    if wanted or extra:
        for d in session.scalars(
            select(BackorderDebt).where(
                BackorderDebt.cleared_at.is_(None),
                func.upper(BackorderDebt.sku).in_(sorted(wanted | extra)),
            )
        ):
            k = (d.sku or "").strip().upper()
            debt_map[k] = debt_map.get(k, 0) + (d.units or 0)
    # Open audit finds (tagless boxes barcode-scanned on a walk): shown
    # as owed work per product, never counted as evidence.
    finds_map: dict[str, dict] = {}
    for f in _active_audit_finds(session):
        k = (f.sku or "").strip().upper()
        if k not in wanted and k not in extra:
            continue
        d = finds_map.setdefault(k, {"open": 0, "printed": 0})
        d[f.status] = d.get(f.status, 0) + 1
    # Retired tags that ANSWER the sweep are ghosts - the box never left.
    # Marked per product for covered SKUs; the check screen treats them
    # as one more scan in the end (Nick, 2026-09-01).
    retired_heard: dict[str, RetiredTag] = {}
    if swept:
        retired_heard = {
            r.rfid_id.upper(): r
            for r in session.scalars(
                select(RetiredTag).where(
                    func.upper(RetiredTag.rfid_id).in_(sorted(swept))
                )
            )
        }
    ghost_map: dict[str, list[dict]] = {}
    stray_ghosts: list[dict] = []
    for epc, r in sorted(retired_heard.items()):
        g = {
            "epc": r.rfid_id,
            "sku": r.sku,
            "product_title": r.product_title,
            "kind": r.kind,
            "retired_at": (
                r.retired_at.isoformat() if r.retired_at else None
            ),
            "retired_by": r.retired_by,
        }
        k = (r.sku or "").strip().upper()
        if k in wanted or k in extra:
            ghost_map.setdefault(k, []).append(g)
        else:
            stray_ghosts.append(g)
    report = []

    def _units(tags) -> int:
        # A sealed-case tag stands for its case_units — the audit compares
        # against Shopify, which counts units, not boxes.
        return sum((t.case_units or 1) for t in tags)

    def _tag_counts(sku_upper: str) -> dict:
        tags = tags_by_sku.get(sku_upper, [])
        # Only the tags recorded as living in the covered bin(s) — what
        # a sweep of this shelf can fairly be expected to hear. Split-
        # shelf stock elsewhere doesn't count against the sweep.
        here = [t for t in tags
                if (t.bin_location or "").strip().lower() in bin_key_set]
        det = [t for t in tags if t.rfid_id.upper() in swept]
        return {
            "tags_on_file": len(tags),
            "tags_here": len(here),
            "detected": len(det),
            "units_here": _units(here),
            "detected_units": _units(det),
            # The tags a sweep of this shelf did NOT hear — the mark-sold
            # candidates when sales explain the silence.
            "silent_epcs": [
                t.rfid_id for t in here if t.rfid_id.upper() not in swept
            ],
            "sold_unretired": sold_map.get(sku_upper, 0),
            "backorder_debt": debt_map.get(sku_upper, 0),
            "finds_open": finds_map.get(sku_upper, {}).get("open", 0),
            "finds_printed": finds_map.get(sku_upper, {}).get("printed", 0),
            "ghosts": ghost_map.get(sku_upper, []),
        }

    # A rack audit can list one SKU from several of its bins — merged
    # into ONE row (quantities summed, bins named) because the rack is
    # a single zone.
    merged: dict[str, dict] = {}
    merged_order: list[str] = []
    for e in rows:
        if not e.sku:
            continue
        key = e.sku.strip().upper()
        g = merged.get(key)
        if g is None:
            g = {"entry": e, "qty": None, "bins": [], "unavail": 0}
            merged[key] = g
            merged_order.append(key)
        if e.qty is not None:
            g["qty"] = (g["qty"] or 0) + e.qty
        g["unavail"] += e.unavailable or 0
        if e.bin and e.bin not in g["bins"]:
            g["bins"].append(e.bin)
    # Multi-box units: the audit's find flow must know "this product is
    # N cartons per unit" so an untagged second box never becomes a
    # double-counting find.
    mb_map = _multibox_map(session, list(merged_order) + sorted(extra))
    for key in merged_order:
        g = merged[key]
        e = g["entry"]
        mb = mb_map.get(key)
        report.append({
            "sku": e.sku,
            "product_title": e.product_title,
            "variant_title": e.variant_title,
            "image_url": e.image_url,
            "expected_qty": g["qty"],
            "unavailable": g["unavail"],
            "bins": g["bins"],
            "rfid_incompatible": key in noscan,
            "in_bin_map": True,
            "boxes_per_unit": mb.boxes_per_unit if mb else None,
            **_tag_counts(key),
        })
    # Requested SKUs the bin map doesn't put here (open-box twins, kept
    # strays, map lag): same counts, named from their newest tag, no
    # expected quantity to claim.
    for key in sorted(extra):
        tags = tags_by_sku.get(key, [])
        newest = max(tags, key=lambda t: t.id) if tags else None
        report.append({
            "sku": newest.sku if newest else key,
            "product_title": newest.product_title if newest else None,
            "variant_title": newest.variant_title if newest else None,
            "image_url": None,
            "expected_qty": None,
            "unavailable": 0,
            "bins": [],
            "rfid_incompatible": key in noscan,
            "in_bin_map": False,
            "boxes_per_unit": (
                mb_map[key].boxes_per_unit if key in mb_map else None
            ),
            **_tag_counts(key),
        })
    # The rest of the sweep's story — tags of OTHER products heard on
    # this shelf, and EPCs nobody owns. This is what turns a bin check
    # into a bin AUDIT: the strays are usually the answer to "why is the
    # count wrong".
    foreign, unknown = [], []
    printed_labels_heard: list[dict] = []
    companions_heard: list[dict] = []
    if swept:
        covered = wanted | extra
        owners = {
            a.rfid_id.upper(): a
            for a in session.scalars(
                select(RfidAssignment).where(
                    func.upper(RfidAssignment.rfid_id).in_(sorted(swept))
                )
            )
        }
        for epc in sorted(swept):
            a = owners.get(epc)
            if a is None:
                # A ghost (retired tag heard) is already reported per
                # product above, not as an anonymous unknown.
                if epc not in retired_heard:
                    unknown.append(epc)
            elif (a.sku or "").strip().upper() not in covered:
                foreign.append({
                    "epc": a.rfid_id,
                    "sku": a.sku,
                    "product_title": a.product_title,
                    "bin_location": a.bin_location,
                })
        # An "unknown" EPC that matches a print job is a PRINTED LABEL
        # that was never paired - the strongest owed-pairing signal
        # (Nick, 2026-09-01: applied but unpaired labels answer sweeps
        # as productless tags). Dismissed EPCs (the operator said the
        # label is accounted for) vanish entirely - neither warned nor
        # listed as unknown.
        if unknown:
            dismissed = {
                (e or "").upper()
                for e in session.scalars(
                    select(LabelDismissal.epc).where(
                        func.upper(LabelDismissal.epc).in_(sorted(unknown))
                    )
                )
            }
            unknown = [e for e in unknown if e not in dismissed]
        # Companion tags (box 2..N of a multi-box unit) answer sweeps
        # like any sticker; they're known by name and count nowhere.
        companions_heard, _comp_epcs = _companions_heard(session, unknown)
        if _comp_epcs:
            unknown = [e for e in unknown if e.upper() not in _comp_epcs]
        if unknown:
            label_jobs = {
                (j.epc or "").upper(): j
                for j in session.scalars(
                    select(PrintJob).where(
                        func.upper(PrintJob.epc).in_(sorted(unknown))
                    )
                )
            }
            still_unknown = []
            for epc in unknown:
                j = label_jobs.get(epc)
                if j is None:
                    still_unknown.append(epc)
                else:
                    printed_labels_heard.append({
                        "epc": epc,
                        "sku": j.sku,
                        "product_title": j.product_title,
                        "job_id": j.id,
                        "job_status": j.status,
                    })
            unknown = still_unknown
    # Does this shelf already count as batch tagged? The audit offers to
    # record it when it doesn't (the abandoned-after-pairing case), and
    # the guarded on-hand LOWER is only offered for previously
    # batch-tagged bins (Nick, 2026-09-01).
    done_batches = session.scalars(
        select(Batch).where(
            func.lower(Batch.bin_name).in_(bin_keys),
            Batch.status == "done",
            Batch.parent_batch_id.is_(None),
        ).order_by(Batch.id)
    ).all()
    done_bin_keys = {
        (b.bin_name or "").strip().lower() for b in done_batches
    }
    done_batch = done_batches[0] if done_batches else None
    return {
        "bin": loc,
        "rack": rack,
        "bins_covered": bin_keys,
        "bins_batch_done": sorted(done_bin_keys),
        "swept": len(swept),
        "count": len(report),
        "items": report,
        "foreign": foreign,
        "unknown_epcs": unknown,
        "printed_labels_heard": printed_labels_heard,
        "companions_heard": companions_heard,
        "stray_ghosts": stray_ghosts,
        "batch_done": bool(bin_key_set) and bin_key_set <= done_bin_keys,
        "batch_done_id": done_batch.id if done_batch else None,
        "batch_done_at": (
            done_batch.completed_at.isoformat()
            if done_batch is not None and done_batch.completed_at else None
        ),
        # Named so the audit can say WHY it isn't offering to record the
        # bin: an earlier abandoned attempt is the usual reason someone
        # believes a finished shelf was never recorded.
        "abandoned_batches": [
            b.id for b in session.scalars(
                select(Batch).where(
                    func.lower(Batch.bin_name).in_(bin_keys),
                    Batch.status == "abandoned",
                ).order_by(Batch.id)
            )
        ],
    }


# ------------------------------------------------------------ audit tab ----
# The C72's AUDIT tab (Nick, 2026-09-01): a walk mode (sweep + barcode
# the tagless boxes) and a directed location mode (bin or rack vs a
# sweep). The FINAL SWEEP is the only counter of physical presence -
# barcode finds are work items (label -> pair -> the tag answers the
# next sweep), never audit evidence.

def _bin_sort_key(name: str):
    """Natural bin order: F2-1 before F10-1, racks alphabetical.
    Non-standard names sort after the real bins, alphabetically."""
    m = re.match(r"^([A-Za-z]+)(\d+)-(\d+)$", (name or "").strip())
    if m:
        return (0, m.group(1).upper(), int(m.group(2)), int(m.group(3)),
                name.upper())
    return (1, (name or "").upper(), 0, 0, (name or "").upper())


@app.get("/api/bins/names", dependencies=[Depends(require_user)])
def bins_names(session: Session = Depends(get_session)):
    """Every distinct bin in the bin map, naturally sorted - the
    prev/next arrows on bin pickers walk this list."""
    names = sorted(
        {
            (b or "").strip()
            for (b,) in session.execute(
                select(BinMapEntry.bin)
                .where(BinMapEntry.bin.isnot(None)).distinct()
            )
            if b and (b or "").strip()
        },
        key=_bin_sort_key,
    )
    return {"count": len(names), "bins": names}


def _consume_audit_find(session: Session, sku: str | None) -> None:
    """A pairing anywhere (Scan Station, batch pair) answers the oldest
    active find for that SKU. The warehouse ZD220t can't RFID-encode,
    so a paired sticker carries its FACTORY EPC - the queued label EPC
    never matches, and the SKU is the only link. Printed finds consume
    before open ones (the printed label is the box in hand). Fail-soft:
    an audit bookkeeping miss must never break a pairing."""
    if not sku or not (sku or "").strip():
        return
    try:
        row = session.scalars(
            select(AuditFind).where(
                AuditFind.status.in_(["open", "printed"]),
                func.upper(AuditFind.sku) == sku.strip().upper(),
            ).order_by(
                # 'printed' sorts after 'open' - desc puts printed first.
                AuditFind.status.desc(), AuditFind.id
            )
        ).first()
        if row is not None:
            row.status = "resolved"
            row.resolved_at = datetime.utcnow()
            row.resolved_by = "pairing"
    except Exception:  # noqa: BLE001
        logger.exception("audit-find consume failed (%s)", sku)


def _active_audit_finds(session: Session) -> list[AuditFind]:
    """Open + printed finds after the lazy maintenance pass: a printed
    find resolves the moment an assignment with its label's EPC exists
    (pairing ANYWHERE answers it - audit tab, scan station, batch), and
    a never-printed find quietly expires after an hour (Nick,
    2026-09-01: it's easy to tag outside audit mode anyway)."""
    rows = session.scalars(
        select(AuditFind)
        .where(AuditFind.status.in_(["open", "printed"]))
        .order_by(AuditFind.id)
    ).all()
    if not rows:
        return []
    now = datetime.utcnow()
    epcs = sorted({f.print_epc.upper() for f in rows if f.print_epc})
    paired: set[str] = set()
    if epcs:
        paired = {
            (e or "").upper()
            for e in session.scalars(
                select(RfidAssignment.rfid_id).where(
                    func.upper(RfidAssignment.rfid_id).in_(epcs)
                )
            )
        }
    out: list[AuditFind] = []
    dirty = False
    for f in rows:
        if (
            f.status == "printed"
            and f.print_epc
            and f.print_epc.upper() in paired
        ):
            f.status = "resolved"
            f.resolved_at = now
            f.resolved_by = "pairing"
            dirty = True
            continue
        if f.status == "open":
            created = f.created_at
            # Azure SQL hands timestamps back tz-aware; sqlite naive.
            if created is not None and created.tzinfo is not None:
                created = created.astimezone(
                    timezone.utc
                ).replace(tzinfo=None)
            if created is not None and (
                (now - created).total_seconds() > 3600
            ):
                f.status = "expired"
                dirty = True
                continue
        out.append(f)
    if dirty:
        session.commit()
    return out


class AuditFindIn(BaseModel):
    code: str = Field(min_length=1, max_length=200)
    by: str | None = Field(default=None, max_length=100)
    auto_print: bool = False
    printer: str | None = Field(default=None, max_length=100)
    # Multi-box guard override: the operator confirmed this really is a
    # separate untagged UNIT, not box 2..N of a unit already tagged.
    multibox_ok: bool = False


@app.post(
    "/api/audit/finds", status_code=201,
    dependencies=[Depends(require_user)],
)
def create_audit_find(
    payload: AuditFindIn, session: Session = Depends(get_session)
):
    """A tagless box found on an audit walk. Resolves the code through
    the full rescue chain, remembers the product's HOME bin (that's
    what the label will say), and - in auto-print mode - queues the
    label right away. A product with no home bin never auto-prints:
    the gun raises the no-bin alert instead."""
    product = _product_lookup(payload.code)
    sku = (product.get("sku") or "").strip()
    if not sku:
        raise HTTPException(
            422, "That product has no SKU - fix it in Shopify first."
        )
    if sku.upper() in _non_taggable_skus(session):
        raise HTTPException(
            422,
            f"{sku} is flagged non-taggable - it never gets labels. "
            "A hand-paired marker tag is the only tag it carries.",
        )
    kind, excluded = resolve_product_kind(
        session, product.get("product_title"), sku,
        product.get("bin_location"),
    )
    if kind == "bundle" or excluded:
        raise HTTPException(
            422,
            f"{sku} is a bundle - its components carry the tags. "
            "Scan a component box instead.",
        )
    # Multi-box guard (Nick, 2026-09-02, the S11740): an untagged
    # second carton of a multi-box unit is NOT untagged stock - noting
    # it would double-count the unit through the front door. The gun
    # asks and retries with multibox_ok when the operator is sure.
    if not payload.multibox_ok:
        mb = _multibox_map(session, [sku]).get(sku.upper())
        if mb is not None and mb.boxes_per_unit > 1:
            raise HTTPException(
                409,
                f"MULTIBOX: 1 unit of {sku} is {mb.boxes_per_unit} "
                f"boxes and its counting tag rides box 1. If this is "
                f"box 2..{mb.boxes_per_unit} of a unit that's already "
                "tagged, do NOT note it - stick a companion label on "
                "instead (reprint from the product window).",
            )
    bin_location = (product.get("bin_location") or "").strip()
    if bin_location.lower() == "no bin assigned":
        bin_location = ""
    find = AuditFind(
        sku=sku,
        product_title=product.get("product_title"),
        variant_title=product.get("variant_title"),
        shopify_variant_id=product.get("shopify_variant_id"),
        shopify_product_id=product.get("shopify_product_id"),
        barcode=product.get("barcode"),
        scanned_code=payload.code.strip()[:200],
        bin_location=bin_location or None,
        created_by=payload.by,
    )
    session.add(find)
    session.flush()
    printed = False
    print_error = None
    if payload.auto_print:
        if not bin_location:
            print_error = "no home bin - assign one, then print"
        elif not (product.get("shopify_variant_id") or "").strip():
            print_error = "no Shopify variant id - can't print"
        else:
            job = _queue_find_label(session, find, payload.by,
                                    payload.printer)
            printed = job is not None
            if not printed:
                print_error = "label queue failed - print from the list"
    session.commit()
    session.refresh(find)
    return {
        "find": find.as_dict(),
        "product": product,
        "no_home_bin": not bin_location,
        "printed": printed,
        "print_error": print_error,
        # How many boxes of this product the walk has found so far.
        "open_for_sku": len([
            f for f in _active_audit_finds(session)
            if f.sku.strip().upper() == sku.upper()
        ]),
    }


def _queue_find_label(
    session: Session, find: AuditFind, by: str | None,
    printer: str | None,
) -> dict | None:
    """One label for one found box, through the same door Scan Station
    prints use (saved label lines consulted, home bin on the label)."""
    try:
        res = create_print_jobs(PrintJobIn(
            quantity=1,
            shopify_variant_id=find.shopify_variant_id,
            shopify_product_id=find.shopify_product_id,
            product_title=find.product_title or find.sku,
            variant_title=find.variant_title,
            sku=find.sku,
            barcode=find.barcode,
            bin_location=find.bin_location,
            requested_by=by,
            printer=printer,
        ), session)
    except Exception:  # noqa: BLE001 - a bad row must not eat the walk
        logger.exception("audit find label queue failed (%s)", find.sku)
        return None
    job = res["jobs"][0]
    find.status = "printed"
    find.print_job_id = job["id"]
    find.print_epc = job.get("epc")
    return job


@app.get("/api/audit/finds", dependencies=[Depends(require_user)])
def list_audit_finds(session: Session = Depends(get_session)):
    rows = _active_audit_finds(session)
    return {"count": len(rows), "finds": [f.as_dict() for f in rows]}


class AuditFindsActIn(BaseModel):
    by: str | None = Field(default=None, max_length=100)
    printer: str | None = Field(default=None, max_length=100)


@app.post(
    "/api/audit/finds/print-all", dependencies=[Depends(require_user)]
)
def audit_finds_print_all(
    payload: AuditFindsActIn, session: Session = Depends(get_session)
):
    """Batch-print mode: one label per still-open find (home bin on
    the label). Finds with no bin or no variant id are held out and
    named, like receiving's held-out rows - fix, then print again."""
    rows = [f for f in _active_audit_finds(session) if f.status == "open"]
    queued = 0
    skipped: list[str] = []
    for f in rows:
        if not f.bin_location:
            skipped.append(f"{f.sku} - no home bin")
            continue
        if not (f.shopify_variant_id or "").strip():
            skipped.append(f"{f.sku} - no Shopify variant id")
            continue
        job = _queue_find_label(session, f, payload.by, payload.printer)
        if job is None:
            skipped.append(f"{f.sku} - label queue failed")
        else:
            queued += 1
    session.commit()
    return {
        "queued": queued,
        "skipped": skipped,
        "finds": [f.as_dict() for f in _active_audit_finds(session)],
    }


@app.post(
    "/api/audit/finds/{find_id}/dismiss",
    dependencies=[Depends(require_user)],
)
def dismiss_audit_find(
    find_id: int,
    payload: AuditFindsActIn,
    session: Session = Depends(get_session),
):
    f = session.get(AuditFind, find_id)
    if f is None:
        raise HTTPException(404, "No such find.")
    if f.status not in ("open", "printed"):
        raise HTTPException(409, f"This find is already {f.status}.")
    f.status = "dismissed"
    f.resolved_at = datetime.utcnow()
    f.resolved_by = payload.by
    session.commit()
    return f.as_dict()


class LabelDismissIn(BaseModel):
    epcs: list[str] = Field(min_length=1, max_length=500)
    by: str | None = Field(default=None, max_length=100)


@app.post(
    "/api/audit/dismiss-labels", dependencies=[Depends(require_user)]
)
def audit_dismiss_labels(
    payload: LabelDismissIn, session: Session = Depends(get_session)
):
    """Dismiss "printed label answered but never paired" warnings for
    good (Nick, 2026-09-01): the operator says these labels are
    accounted for. The EPCs vanish from future audit reports - neither
    warned about nor listed as unknown."""
    added = 0
    for raw in payload.epcs:
        epc = (raw or "").strip().upper()
        if not epc:
            continue
        exists = session.scalar(
            select(LabelDismissal).where(
                func.upper(LabelDismissal.epc) == epc
            )
        )
        if exists is None:
            session.add(LabelDismissal(
                epc=epc, dismissed_by=payload.by
            ))
            added += 1
    session.commit()
    return {"dismissed": added,
            "message": f"{added} label warning(s) dismissed for good."}


class AuditCompleteIn(BaseModel):
    location: str = Field(min_length=1, max_length=100)
    by: str | None = Field(default=None, max_length=100)
    summary: str = Field(default="", max_length=200)


@app.post(
    "/api/audit/complete", status_code=201,
    dependencies=[Depends(require_user)],
)
def audit_complete(
    payload: AuditCompleteIn, session: Session = Depends(get_session)
):
    """The completed-audits log (Nick, 2026-09-01: a log, not a live
    link): one History row per finished audit naming the location and
    a one-line summary of what the check screen showed."""
    session.add(BarcodeChange(
        sku=None,
        product_title=f"Audit of {payload.location.strip()}",
        changed_field="bin-audited",
        old_barcode=None,
        new_barcode=(payload.summary or "").strip()[:64] or "audited",
        changed_by=payload.by,
    ))
    session.commit()
    return {"ok": True}


class MarkTaggedIn(BaseModel):
    created_by: str | None = Field(default=None, max_length=100)
    confirmed: bool = False


@app.post(
    "/api/bins/{bin_name}/mark-tagged",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def mark_bin_tagged(
    bin_name: str,
    payload: MarkTaggedIn,
    session: Session = Depends(get_session),
):
    """Record a shelf as batch tagged WITHOUT walking a batch — the rescue
    for a batch abandoned after every tag was already paired, where the
    work is done but no completed batch says so.

    Deliberately narrow: it refuses when the bin already counts as tagged,
    and when no tags are recorded there at all (nothing to justify the
    claim). The batch it writes carries the tags as `tagged_before` — they
    were paired in an earlier session, not by this record — and is marked
    as an audit completion so History never passes it off as a shelf walk.
    """
    name = bin_name.strip()
    key = name.lower()
    if not name:
        raise HTTPException(422, "Which bin?")
    existing = session.scalars(
        select(Batch).where(
            func.lower(Batch.bin_name) == key,
            Batch.status == "done",
            Batch.parent_batch_id.is_(None),
        )
    ).first()
    if existing is not None:
        raise HTTPException(
            409,
            f"{name} already counts as batch tagged (batch #{existing.id}).",
        )
    tags = session.scalars(
        select(RfidAssignment).where(
            func.lower(RfidAssignment.bin_location) == key
        )
    ).all()
    if not tags:
        raise HTTPException(
            422,
            f"No RFID tags are recorded in {name}, so there's nothing to "
            f"mark as tagged. Tag the shelf with a batch instead.",
        )
    by_sku: dict[str, list[RfidAssignment]] = {}
    for t in tags:
        by_sku.setdefault((t.sku or "").strip().upper(), []).append(t)
    if not payload.confirmed:
        raise HTTPException(
            409,
            f"Mark {name} as batch tagged? {len(tags)} tag(s) across "
            f"{len(by_sku)} product(s) are recorded there. This records "
            f"the shelf as done — it does NOT tag anything, print "
            f"anything, or touch Shopify. Confirm to record it.",
        )

    # Shopify's expected count per SKU, so the recorded batch reads like
    # any other in History and Review.
    expected: dict[str, BinMapEntry] = {}
    for e in session.scalars(
        select(BinMapEntry).where(func.lower(BinMapEntry.bin) == key)
    ):
        if e.sku:
            expected.setdefault(e.sku.strip().upper(), e)

    now = datetime.now(timezone.utc)
    batch = Batch(
        bin_name=name,
        status="done",
        created_by=payload.created_by,
        completed_at=now,
        verified_at=now,
        # No spare column for provenance, and ui_step is dead data on a
        # closed batch — so it carries the marker History reads.
        ui_step="audit-complete",
    )
    session.add(batch)
    session.flush()
    for key_sku, rows in sorted(by_sku.items()):
        newest = max(rows, key=lambda t: t.id)
        ent = expected.get(key_sku)
        session.add(BatchItem(
            batch_id=batch.id,
            scanned_code=(newest.barcode or newest.sku or "")[:64],
            resolved=True,
            shopify_variant_id=newest.shopify_variant_id,
            shopify_product_id=newest.shopify_product_id,
            product_title=newest.product_title,
            variant_title=newest.variant_title,
            sku=newest.sku,
            barcode=newest.barcode,
            bin_location=name,
            image_url=ent.image_url if ent else None,
            qty_scanned=0,
            paired_count=0,
            # Tagged in an earlier session — units on the shelf, but this
            # record neither scanned nor paired them.
            tagged_before=sum((t.case_units or 1) for t in rows),
            expected_qty=ent.qty if ent else None,
        ))
    session.commit()
    return {
        "batch": batch.as_dict(),
        "products": len(by_sku),
        "tags": len(tags),
        "message": (
            f"{name} recorded as batch tagged ✓ — {len(tags)} tag(s) "
            f"across {len(by_sku)} product(s), from the tags already on "
            f"file. Nothing was tagged, printed or written to Shopify."
        ),
    }


@app.get("/api/audit/bins", dependencies=[Depends(require_user)])
def audit_bins(session: Session = Depends(get_session)):
    """Shopify on-hand vs RFID units on file, per product, grouped by the
    product's bin and scored by the sum of ABSOLUTE differences — the
    received-but-nowhere-to-be-found detector. On-hand comes from the
    live-sourced bin map (its age is reported so a stale map is visible);
    RFID units count each tag as 1 except sealed-case tags, which count
    their case_units. Read-only."""
    # Units on file per SKU, store-wide. A tag's home bin can lag a move,
    # so the comparison is per PRODUCT (like Steve's worked example), with
    # products grouped under the bin the live catalog says they live in.
    tags = session.scalars(select(RfidAssignment)).all()
    units: dict[str, int] = {}
    for t in tags:
        key = (t.sku or "").strip().upper()
        if not key:
            continue
        units[key] = units.get(key, 0) + (t.case_units or 1)

    entries = session.scalars(
        select(BinMapEntry).where(BinMapEntry.sku.isnot(None))
    ).all()

    # Sold-but-unretired units RAISE the expected tag count: a fulfilled
    # order's box left with its tag on file, so those tags aren't drift.
    # WINDOWED to each SKU's tag-pool baseline like everything else — the
    # unwindowed sum blamed sales that predate tagging and rendered
    # "3 in Shopify, 4 tags, difference of -19" (Nick, 2026-08-25). A SKU
    # with no live tags gets no sold adjustment at all: with nothing on
    # file, sales can't explain tags that don't exist.
    all_skus = list({e.sku.strip().upper() for e in entries} | set(units))
    baselines = orders_sync._sku_baselines(session, all_skus)
    sold = orders_sync.sold_unretired_since_map(session, all_skus, baselines)

    noscan = _noscan_skus(session)
    no_tag = _non_taggable_skus(session)
    skipped_bundles = 0
    skipped_non_taggable = 0
    seen_skus: set[str] = set()
    bins: dict[str, dict] = {}

    def _bucket(name: str) -> dict:
        return bins.setdefault(name, {"bin": name, "products": []})

    for e in entries:
        key = e.sku.strip().upper()
        # Bundles have no boxes of their own (their components carry the
        # tags) and dropped/non-taggable products aren't in the RFID
        # system at all — comparing any of them is guaranteed phantom
        # drift, so they leave the audit instead of scoring it.
        if key in no_tag:
            skipped_non_taggable += 1
            seen_skus.add(key)
            continue
        kind, excluded = resolve_product_kind(
            session, e.product_title, e.sku, e.bin
        )
        if kind == "bundle" or excluded:
            skipped_bundles += 1
            seen_skus.add(key)
            continue
        on_hand = e.qty or 0
        sold_n = sold.get(key, 0) if units.get(key, 0) > 0 else 0
        have = units.get(key, 0)
        seen_skus.add(key)
        _bucket((e.bin or "").strip() or "(no bin)")["products"].append({
            "sku": e.sku,
            "product_title": e.product_title,
            "on_hand": on_hand,
            "sold_unretired": sold_n,
            "rfid_units": have,
            "diff": have - (on_hand + sold_n),
            "rfid_incompatible": key in noscan,
        })

    # Tags for products the live catalog doesn't bin at all — they exist
    # in the RFID system, Shopify says nowhere. Grouped by the bin the
    # tags themselves claim, so someone can go look.
    orphans: dict[tuple, dict] = {}
    for t in tags:
        key = (t.sku or "").strip().upper()
        # Non-taggable products' hand-paired bag markers are Locate
        # helpers, not inventory — never orphan-flag them.
        if not key or key in seen_skus or key in no_tag:
            continue
        o = orphans.setdefault(
            ((t.bin_location or "").strip() or "(no bin)", key),
            {
                "sku": t.sku,
                "product_title": t.product_title,
                "on_hand": None,   # not in the live bin map
                "rfid_units": 0,
                "diff": 0,
                "rfid_incompatible": key in noscan,
            },
        )
        o["rfid_units"] += t.case_units or 1
        o["diff"] = o["rfid_units"]
    for (bin_name, _key), row in orphans.items():
        _bucket(f"{bin_name} · not in the bin map")["products"].append(row)

    # "Done" means the bin itself went through batch tagging to completion.
    # A lone tag from the Scan Station (or a stray carried in) must NOT
    # promote an otherwise-untouched bin into the default view — one
    # desk-tagged product out of 42 made E6-1 top the list with a score
    # that was 41 parts never-tagged noise. Side trips don't count either:
    # they tag a few carried boxes, never the whole shelf.
    done_bins = {
        (b.bin_name or "").strip().lower()
        for b in session.scalars(
            select(Batch).where(
                Batch.status == "done",
                Batch.parent_batch_id.is_(None),
            )
        )
    }

    payload = []
    for b in bins.values():
        b["products"].sort(key=lambda p: (-abs(p["diff"]), p["sku"] or ""))
        b["score"] = sum(abs(p["diff"]) for p in b["products"])
        b["tagged_products"] = sum(
            1 for p in b["products"] if p["rfid_units"] > 0
        )
        b["tagged"] = b["tagged_products"] > 0
        b["batch_done"] = b["bin"].strip().lower() in done_bins
        b["product_count"] = len(b["products"])
        b["mismatched_count"] = sum(1 for p in b["products"] if p["diff"])
        payload.append(b)
    # Biggest total difference first — a zero-diff bin reads as audited ✓.
    payload.sort(key=lambda b: (-b["score"], b["bin"]))

    age = _bin_map_age(session)
    return {
        "bins": payload,
        "bin_count": len(payload),
        "done_bin_count": sum(1 for b in payload if b["batch_done"]),
        "tagged_bin_count": sum(1 for b in payload if b["tagged"]),
        "skipped_bundles": skipped_bundles,
        "skipped_non_taggable": skipped_non_taggable,
        "onhand_age_minutes": None if age is None else int(age / 60),
        "refreshing": _bin_map_state["running"],
    }


@app.post("/api/bin-map/refresh", dependencies=[Depends(require_user)])
def bin_map_refresh():
    """Force a full re-read of every product's bin from Shopify. Takes
    ~a minute in the background; poll /api/bin-map/status for progress."""
    started = _maybe_refresh_bin_map(force=True)
    return {"refreshing": started}


@app.get("/api/bin-map/status", dependencies=[Depends(require_user)])
def bin_map_status(session: Session = Depends(get_session)):
    age = _bin_map_age(session)
    return {
        "entries": session.scalar(
            select(func.count()).select_from(BinMapEntry)
        ),
        "age_minutes": None if age is None else int(age / 60),
        "refreshing": _bin_map_state["running"],
    }


def _get_batch(session: Session, batch_id: int) -> Batch:
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "No such batch.")
    return batch


def _batch_items(session: Session, batch_id: int) -> list[BatchItem]:
    return session.scalars(
        select(BatchItem)
        .where(BatchItem.batch_id == batch_id)
        .order_by(BatchItem.id)
    ).all()


def _expected_qty(session: Session, sku: str | None) -> int | None:
    """Expected shelf count for one SKU: LIVE Shopify on-hand MINUS the
    Unavailable bucket (Nick, 2026-09-01) first, the bin map's
    live-sourced snapshot (already effective, rebuilt every few hours)
    when the API is unreachable. The TELCAN mirror used to be the
    fallback here — removed 2026-08-07, stale by months."""
    if not sku:
        return None
    if not config.check_shopify_env():
        try:
            live = shopify.get_shelf_on_hand(sku)
            if live is not None:
                return live
        except Exception as error:
            logger.warning("live on-hand failed for %s: %s", sku, error)
    try:
        total = session.execute(
            select(func.sum(BinMapEntry.qty))
            .where(func.upper(BinMapEntry.sku) == sku.strip().upper())
        ).scalar()
        return int(total) if total is not None else None
    except Exception as error:
        logger.warning("bin-map qty lookup failed for %s: %s", sku, error)
        return None


@lru_cache(maxsize=16384)
def _sku_root(sku: str | None) -> str | None:
    """The part of a SKU that identifies the PRODUCT, with open-box wording
    stripped: "OPEN BOX- 08891" and "08891" share the root "08891".

    Short roots are refused — a two-character root would tie half the
    catalog together, and a wrong candidate list is worse than none.
    Cached: the sibling scan calls this for every bin-map row for every
    item it checks, and SKUs never change meaning mid-process."""
    if not sku:
        return None
    root = re.sub(r"open[\s-]*box", " ", sku, flags=re.I)
    root = re.sub(r"[^0-9A-Za-z]+", "", root).strip()
    return root.upper() if len(root) >= 4 else None


def _merge_siblings(
    session: Session,
    item: BatchItem,
    candidates: list[dict],
    bin_rows: list[BinMapEntry] | None = None,
) -> list[dict]:
    """Add listings that are plainly the same product in another condition.

    A barcode search finds twins that SHARE a barcode, but an open-box
    listing often has none at all, so it can only be reached through its
    SKU. The bin map already holds every binned variant locally, which
    makes this a cheap local scan rather than more Shopify calls."""
    root = _sku_root(item.sku or item.scanned_code)
    if not root:
        return candidates
    # Dedupe on SKU, not variant id. The two sources disagree on ids —
    # TELCAN hands back "handle:<handle>" while the bin map stores Shopify's
    # gid — so an id comparison never matches and the SAME product gets
    # listed twice, once from each source. SKU is the key both agree on.
    seen = {
        c.get("shopify_variant_id")
        for c in candidates if c.get("shopify_variant_id")
    }
    seen_skus = {
        (c.get("sku") or "").strip().upper()
        for c in candidates if c.get("sku")
    }
    merged = list(candidates)
    try:
        # A caller checking many items (the Check step) hands the rows in
        # so the table is read once, not once per item.
        rows = bin_rows if bin_rows is not None else session.execute(
            select(BinMapEntry).where(BinMapEntry.sku.isnot(None))
        ).scalars()
        for row in rows:
            sku_key = (row.sku or "").strip().upper()
            if row.shopify_variant_id in seen or sku_key in seen_skus:
                continue
            if _sku_root(row.sku) != root:
                continue
            seen.add(row.shopify_variant_id)
            seen_skus.add(sku_key)
            merged.append({
                "shopify_variant_id": row.shopify_variant_id,
                "shopify_product_id": row.shopify_product_id,
                "product_title": row.product_title,
                "variant_title": row.variant_title,
                "sku": row.sku,
                "barcode": row.barcode,
                "bin_location": row.bin,
                "image_url": row.image_url,
            })
    except Exception as error:
        logger.warning("sibling lookup failed for %s: %s", item.sku, error)
        return candidates
    # Whatever the row is currently pointing at stays first, so the arrows
    # open on the listing the operator is looking at.
    merged.sort(key=lambda c: c.get("shopify_variant_id") != item.shopify_variant_id)
    return merged


def _units_on_shelf(item: BatchItem) -> int:
    """Stock this row represents. Loose boxes are one unit each; a sealed
    case is one box but `case_units` units; boxes a baseline sweep found
    already tagged are physically on the shelf too. Shopify counts units,
    so this is what any count comparison must use."""
    return (
        item.qty_scanned
        + item.case_count * (item.case_units or 0)
        + item.tagged_before
    )


def _units_breakdown(item: BatchItem) -> str | None:
    """"2 + 8x1" — loose units, then units-per-case times cases. Only when a
    case is involved; otherwise the single total says everything."""
    if not item.case_count or not item.case_units:
        return None
    return f"{item.qty_scanned} + {item.case_units}x{item.case_count}"


def _apply_product_to_item(
    session: Session, item: BatchItem, product: dict, batch: Batch
) -> None:
    """Copy a resolved product onto a batch row. Shared by the scan path (a
    brand-new row) and the Check step's re-check (a row that stayed
    unresolved until the operator set the barcode in Shopify), so both end
    up with identical snapshots."""
    item.resolved = True
    item.shopify_variant_id = product.get("shopify_variant_id")
    item.shopify_product_id = product.get("shopify_product_id")
    item.product_title = product.get("product_title")
    item.variant_title = product.get("variant_title")
    item.sku = product.get("sku")
    item.barcode = product.get("barcode")
    item.bin_location = product.get("bin_location")
    # "Other bins" only means something when this bin is genuinely one of
    # the product's — otherwise it's simply on the wrong shelf, and calling
    # that a split would be a lie.
    saved = product.get("bin_location")
    if bin_contains(saved, batch.bin_name):
        others = bins_other_than(saved, batch.bin_name)
        item.other_bins = (", ".join(others))[:255] if others else None
    else:
        item.other_bins = None
    # These three only overwrite when the lookup actually carried a value:
    # a re-check must never wipe a learned serial prefix, a cached image or
    # a known count just because one live call came back thin.
    if product.get("serial_prefix"):
        item.serial_prefix = product["serial_prefix"]
    image = (product.get("image_url") or "")[:500]
    if image:
        item.image_url = image
    # Multi-box product or bundle? Only meaningful when the listing occupies
    # more than one box slot; the operator's saved answer wins over the guess.
    item.kind, _ = resolve_product_kind(
        session, item.product_title, item.sku, saved
    )
    qty = _expected_qty(session, item.sku)
    if qty is not None:
        item.expected_qty = qty


class BatchIn(BaseModel):
    bin: str | None = Field(default=None, max_length=100)
    created_by: str | None = Field(default=None, max_length=100)
    # "receiving": a shipment batch — no home bin, everything comes from
    # scans, printing repeats per pass, labels carry each item's own bin.
    kind: Literal["receiving"] | None = None

    @field_validator("bin")
    @classmethod
    def strip_bin(cls, v: str | None) -> str | None:
        return v.strip() if v and v.strip() else None


RECEIVING_BIN = "RECEIVING"


def _is_receiving(batch: Batch) -> bool:
    return batch.kind == "receiving"


def _maybe_close_receiving(session: Session, batch: Batch) -> bool:
    """Receiving is entirely planner-driven (Nick, 2026-08-25): no Finish
    button. The shipment closes ITSELF the moment every received box is
    tagged - checked after each pair, count update and problem-row fix.
    Flagged informational rows (non-taggable) never block; an UNFIXED
    unknown row does, because its boxes are real and untagged - fix it
    with the card's Link button or abandon the batch. Runs inside the
    caller's transaction; History derives the receiving-completed event
    from the closed batch row."""
    if not _is_receiving(batch) or batch.status in ("done", "abandoned"):
        return False
    total = 0
    for i in _batch_items(session, batch.id):
        if not i.resolved:
            return False
        if i.skip_reason or i.skipped or i.kind == "bundle":
            continue
        want = i.qty_scanned + i.case_count
        if want <= 0:
            continue
        if (i.paired_count or 0) < want:
            return False
        total += want
    if total <= 0:
        return False
    batch.status = "done"
    batch.completed_at = datetime.now(timezone.utc)
    try:
        _note_backorder_debt(session, batch)
    except Exception:  # noqa: BLE001 — bookkeeping must never block a close
        logger.exception("backorder-debt check failed (batch %s)", batch.id)
    # A FULLY-arrived full-shipment receive auto-closes before anyone
    # can press the settle button (Nick, 2026-09-01, SO 946): pairing
    # is complete, so the 1-hour Shopify-update clock starts HERE - the
    # watchdog and the Continue-to-TC-Planner path both key off it.
    try:
        receipt = session.scalar(
            select(OrderReceipt)
            .where(OrderReceipt.batch_id == batch.id)
            .order_by(OrderReceipt.id.desc())
        )
        if receipt is not None and receipt.settled_at is None:
            receipt.settled_at = datetime.now(timezone.utc)
    except Exception:  # noqa: BLE001
        logger.exception("auto-settle failed (batch %s)", batch.id)
    return True


def _note_backorder_debt(session: Session, batch: Batch) -> None:
    """A delivery that lands on NEGATIVE Shopify on-hand loses units to
    the oversell hole: the stock write brings on-hand to (negative + N)
    while N tagged boxes hit the shelf, and the daily tag arithmetic
    would flag the difference forever (Nick, 2026-08-26 — AirGradient:
    on-hand -1 met 48 received boxes, so Shopify counts 47 against 48
    tags). When a receiving batch closes, note that gap per SKU, capped
    at the units Shopify still shows COMMITTED to customers — the cap is
    what keeps a plain mistag from hiding in here. The expected-tag math
    carries uncleared notes; an operator on-hand write supersedes them."""
    skus = sorted({
        (i.sku or "").strip()
        for i in _batch_items(session, batch.id)
        if i.sku and i.sku.strip() and not i.skip_reason and not i.skipped
        and i.kind != "bundle" and (i.qty_scanned + i.case_count) > 0
    })
    if not skus:
        return
    # One batched call finds the (rare) SKUs worth a closer look; the
    # per-SKU breakdown call runs only for those.
    on_hand_ci = {
        (k or "").strip().upper(): v
        for k, v in (shopify.get_on_hand_by_skus(skus) or {}).items()
    }
    baselines = orders_sync._sku_baselines(session, skus)
    sold = orders_sync.sold_unretired_since_map(session, skus, baselines)
    existing = orders_sync.backorder_debt_map(session, skus)
    for sku in skus:
        key = sku.upper()
        oh = on_hand_ci.get(key)
        if oh is None:
            continue
        tags = orders_sync.tag_units(session, sku)
        gap = tags - oh - sold.get(key, 0) - existing.get(key, 0)
        if gap <= 0:
            continue
        try:
            live = shopify.get_quantity_breakdown(sku)
        except Exception:  # noqa: BLE001 — skip quietly, next receive retries
            continue
        debt = min(gap, (live or {}).get("committed") or 0)
        if debt <= 0:
            continue
        row = BackorderDebt(
            sku=sku,
            units=debt,
            source=(f"receiving · {batch.created_by}"
                    if batch.created_by else f"receiving batch #{batch.id}"
                    )[:120],
        )
        session.add(row)
        session.flush()
        title = next(
            (i.product_title for i in _batch_items(session, batch.id)
             if (i.sku or "").strip().upper() == key and i.product_title),
            None,
        )
        # old_barcode carries the row id so History can offer "clear
        # this note"; new_barcode carries the unit count for display.
        session.add(BarcodeChange(
            sku=sku,
            product_title=title,
            changed_field="backorder-debt",
            old_barcode=str(row.id),
            new_barcode=str(debt),
            changed_by=batch.created_by,
        ))


@app.post(
    "/api/batches", status_code=201, dependencies=[Depends(require_user)]
)
def create_batch(payload: BatchIn, session: Session = Depends(get_session)):
    """Start a bin batch pre-seeded with everything Shopify expects in that
    bin (0/N tickers before the first scan). Scanning products not on the
    list still adds them — the seed is a head start, not a wall.

    kind="receiving" starts a shipment batch instead: no bin, no pre-seed —
    the collect → print → pair loop runs as many passes as the pallet
    takes, and finishing files per-bin inventory checks."""
    if payload.kind == "receiving":
        batch = Batch(
            bin_name=RECEIVING_BIN,
            kind="receiving",
            created_by=payload.created_by,
        )
        session.add(batch)
        session.commit()
        session.refresh(batch)
        result = batch.as_dict()
        result["items"] = []
        return result
    if not payload.bin:
        raise HTTPException(422, "Which bin? (bin must not be blank)")
    batch = Batch(bin_name=payload.bin, created_by=payload.created_by)
    session.add(batch)
    session.flush()

    # Expected products come from the bin map (Shopify metafields cache —
    # the mirror's Bin_Name column is empty store-wide).
    _maybe_refresh_bin_map()  # background top-up when stale; reads go on
    expected: list[dict] = []
    try:
        rows = session.scalars(
            select(BinMapEntry)
            .where(func.lower(BinMapEntry.bin) == payload.bin.lower())
            .order_by(BinMapEntry.product_title, BinMapEntry.sku)
        ).all()
        seen_variants: set = set()
        for r in rows:
            key = r.sku or r.barcode or r.shopify_variant_id
            if key in seen_variants:  # belt-and-braces vs duplicate rows
                continue
            seen_variants.add(key)
            expected.append({
                "shopify_variant_id": r.shopify_variant_id,
                "shopify_product_id": r.shopify_product_id,
                "product_title": r.product_title,
                "variant_title": r.variant_title,
                "sku": r.sku,
                "barcode": r.barcode,
                "bin_location": r.bin,
                "other_bins": r.other_bins,
                "expected_qty": r.qty,
                "image_url": r.image_url,
            })
    except Exception as error:
        logger.warning("bin pre-seed failed for %s: %s", payload.bin, error)

    # The bin map is a cache; starting a batch is exactly when it must be
    # THIS minute's truth. Re-check every seeded product live: refresh its
    # count, and drop it if its bin has since changed in Shopify.
    #
    # (Products that moved INTO this bin can't be found this way — Shopify
    # can't search variants by metafield value — so a background rebuild is
    # kicked off too, and scanning such a box adds it correctly anyway.)
    if expected and not config.check_shopify_env():
        try:
            live = shopify.get_stock_info_by_skus(
                [p["sku"] for p in expected if p.get("sku")]
            )
            wanted = payload.bin.strip().lower()
            fresh = []
            moved = []
            for p in expected:
                info = live.get(p.get("sku") or "")
                if info is None:
                    fresh.append(p)
                    continue
                p["expected_qty"] = info["on_hand"]
                actual = (info["bin"] or "").strip()
                # Multi-bin products legitimately live here AND elsewhere.
                if actual and not bin_contains(actual, wanted):
                    moved.append(f"{p.get('sku')}→{actual}")
                    continue
                if actual and bin_contains(actual, wanted):
                    others = bins_other_than(actual, wanted)
                    p["other_bins"] = ", ".join(others) if others else None
                fresh.append(p)
            if moved:
                logger.info("bin %s: %d product(s) moved since the map was "
                            "built: %s", payload.bin, len(moved),
                            ", ".join(moved[:10]))
            expected = fresh
        except Exception as error:
            logger.warning("live bin/stock refresh failed for bin %s: %s",
                           payload.bin, error)
    # Keep the map itself moving so newly-arrived products show up soon.
    _maybe_refresh_bin_map(max_age=900)

    # Serialized brands print their operator-confirmed name; grab any
    # prefix rows for the seeded SKUs in one query.
    sp_by_sku: dict[str, SerialPrefix] = {}
    skus = [p["sku"] for p in expected if p.get("sku")]
    if skus:
        for sp in session.scalars(
            select(SerialPrefix).where(SerialPrefix.sku.in_(skus))
        ):
            sp_by_sku.setdefault(sp.sku, sp)

    # Bundles with DEFINED contents aren't countable products: their boxes
    # ARE the component's boxes (63 W9184B on the shelf covers the
    # bundle-of-10 and bundle-of-5 listings by arithmetic). They're held
    # out of the seed and reported as covered instead of demanding scans.
    bundle_map: dict[str, list[dict]] = {}
    seed_skus = [p["sku"] for p in expected if p.get("sku")]
    if seed_skus:
        for bc in session.scalars(
            select(BundleContent).where(
                func.upper(BundleContent.bundle_sku).in_(
                    [s.upper() for s in seed_skus]
                )
            ).order_by(BundleContent.id)
        ):
            bundle_map.setdefault(
                bc.bundle_sku.upper(), []
            ).append(bc.as_dict())

    items = []
    dropped: list[str] = []
    covered: list[dict] = []
    no_tag = _non_taggable_skus(session)
    for p in expected:
        sp = sp_by_sku.get(p.get("sku") or "")
        # The whole bin metafield, not just this shelf: counting box slots
        # is what tells a multi-box product from a bundle.
        full_bin = ", ".join(
            x for x in (p.get("bin_location"), p.get("other_bins")) if x
        )
        kind, excluded = resolve_product_kind(
            session, p.get("product_title"), p.get("sku"), full_bin
        )
        # Bundles the operator dropped from the RFID system have no physical
        # box to tag — seeding them would just re-raise a settled question.
        # Non-taggable products (bins of loose thumbscrews) likewise stay
        # out: nobody labels those individually, by decision.
        if excluded or (p.get("sku") or "").strip().upper() in no_tag:
            dropped.append(p.get("sku") or "")
            continue
        contents = bundle_map.get((p.get("sku") or "").upper())
        if contents:
            covered.append({
                "sku": p.get("sku"),
                "product_title": p.get("product_title"),
                "contents": contents,
            })
            continue
        items.append(BatchItem(
            batch_id=batch.id,
            scanned_code=(p.get("barcode") or p.get("sku") or "")[:64],
            resolved=True,
            shopify_variant_id=p.get("shopify_variant_id"),
            shopify_product_id=p.get("shopify_product_id"),
            product_title=p.get("product_title"),
            variant_title=p.get("variant_title"),
            sku=p.get("sku"),
            barcode=p.get("barcode"),
            bin_location=p.get("bin_location"),
            other_bins=(p.get("other_bins") or "")[:255] or None,
            serial_prefix=sp.prefix if sp else None,
            image_url=(p.get("image_url") or "")[:500] or None,
            # Batch labels use the standard store header + SKU; Astronomik
            # item names are set in Scan Station, not here.
            label_name=None,
            qty_scanned=0,
            expected_qty=p.get("expected_qty"),
            kind=kind,
        ))
    if dropped:
        logger.info("bin %s: skipped %d excluded bundle(s): %s",
                    payload.bin, len(dropped), ", ".join(dropped[:10]))
    session.add_all(items)
    session.commit()
    session.refresh(batch)
    for item in items:
        session.refresh(item)
    result = batch.as_dict()
    result["items"] = [i.as_dict() for i in items]
    result["covered_bundles"] = covered
    return result


# --------------------------------------------------------------------------
# LINK relay: the C72's LINK tab turns the gun into a networked input device
# for the web terminal — every BT barcode and trigger RFID read lands here,
# the terminal polls with an id cursor and acts on each scan through its
# normal input paths, then posts the outcome back so the gun can ding/buzz.
# No Bluetooth to the PC, ever. Rows are plumbing, not history.
#
# Presence is in-memory only (single container, same rationale as the print
# agent's _agent_last_seen): guns stamp themselves through the tuning poll
# (new APKs, any tab) and through every LINK scan POST (old APKs too), and
# each web terminal stamps a per-page-load tid through its scan poll. That
# lets the LINK toggle warn when another terminal is already listening —
# gun scans act on EVERY listening terminal, so two ON at once print twice.

LINK_PRESENCE_TTL = 15.0  # s; rides out poll stalls during print bursts
_link_guns: dict[str, dict] = {}       # device -> {"seen": monotonic, "tab"}
_link_terminals: dict[str, dict] = {}  # tid -> {"seen": monotonic, "operator"}


def _stamp_gun(device: str | None, tab: str | None) -> None:
    device = (device or "").strip()[:100]
    if device:
        _link_guns[device] = {
            "seen": time.monotonic(), "tab": (tab or "").strip()[:20]
        }


def _stamp_terminal(tid: str | None, operator: str | None) -> None:
    tid = (tid or "").strip()[:40]
    if tid:
        _link_terminals[tid] = {
            "seen": time.monotonic(), "operator": (operator or "").strip()[:100]
        }


def _live(entries: dict[str, dict], key_name: str) -> list[dict]:
    """Prune expired entries in place and return the live ones with ages."""
    now = time.monotonic()
    for k in [k for k, v in entries.items()
              if now - v["seen"] > LINK_PRESENCE_TTL]:
        entries.pop(k, None)
    return [
        {key_name: k, "seen_seconds": int(now - v["seen"]),
         **{f: v[f] for f in v if f != "seen"}}
        for k, v in sorted(entries.items())
    ]


class LinkScanIn(BaseModel):
    kind: Literal["barcode", "epc"]
    value: str = Field(min_length=1, max_length=200)
    rssi: str | None = Field(default=None, max_length=20)
    device: str | None = Field(default=None, max_length=100)


class LinkResultIn(BaseModel):
    ok: bool
    outcome: str = Field(default="", max_length=300)


@app.post("/api/link/scans", dependencies=[Depends(require_user)])
def link_scan_submit(
    payload: LinkScanIn, session: Session = Depends(get_session)
):
    # Sweep day-old plumbing on the way in so the table can't grow forever.
    session.execute(
        delete(LinkScan).where(
            LinkScan.created_at
            < datetime.now(timezone.utc) - timedelta(days=1)
        )
    )
    row = LinkScan(
        kind=payload.kind,
        value=payload.value.strip(),
        rssi=payload.rssi,
        device=(payload.device or "").strip() or None,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    # Any scan proves a gun is alive on its LINK tab — old APKs included.
    _stamp_gun(payload.device or "C72", "link")
    return {"scan": row.as_dict()}


@app.get("/api/link/scans", dependencies=[Depends(require_user)])
def link_scans_poll(
    after: int = -1,
    device: str | None = None,
    limit: int = 20,
    tid: str | None = None,
    op: str | None = None,
    session: Session = Depends(get_session),
):
    """Cursor poll for the web terminal. after=-1 returns just the current
    cursor (max id) with no rows — the terminal calls that when its LINK
    toggle turns ON, so scans fired before the toggle never replay. The
    seat call doubles as the in-use pre-check: the caller is stamped FIRST,
    then everyone else is snapshotted, so of two terminals toggling at
    once at least the later one sees the earlier."""
    if tid:
        _stamp_terminal(tid, op)
    elif after >= 0:
        # A polling terminal running pre-update app.js is still a listener;
        # make it visible to updated terminals during the mixed-fleet window.
        _stamp_terminal("legacy", "a pre-update terminal")
    if after < 0:
        latest = session.scalar(select(func.max(LinkScan.id))) or 0
        return {
            "cursor": latest,
            "scans": [],
            "listeners": [
                t for t in _live(_link_terminals, "tid")
                if t["tid"] != (tid or "").strip()[:40]
            ],
            "guns": _live(_link_guns, "device"),
        }
    stmt = (
        select(LinkScan)
        .where(LinkScan.id > after)
        .order_by(LinkScan.id)
    )
    if device:
        stmt = stmt.where(LinkScan.device == device.strip())
    rows = session.scalars(stmt.limit(min(max(limit, 1), 100))).all()
    others = len([
        t for t in _live(_link_terminals, "tid")
        if t["tid"] != (tid or "").strip()[:40]
    ])
    return {
        "cursor": rows[-1].id if rows else after,
        "scans": [r.as_dict() for r in rows],
        "others": others,
    }


@app.get("/api/link/status", dependencies=[Depends(require_user)])
def link_status():
    """Who's around: live guns (device, tab, age) and live listening
    terminals (tid, operator, age). Diagnostics now, auto-on later."""
    return {
        "guns": _live(_link_guns, "device"),
        "listeners": _live(_link_terminals, "tid"),
    }


class LinkReleaseIn(BaseModel):
    tid: str = Field(min_length=1, max_length=40)


@app.post("/api/link/presence/release", dependencies=[Depends(require_user)])
def link_presence_release(payload: LinkReleaseIn):
    """Toggle-OFF (and dialog-cancel, which has already stamped itself)
    drops the terminal immediately instead of waiting out the TTL."""
    _link_terminals.pop(payload.tid.strip()[:40], None)
    return {"ok": True}


@app.get("/api/link/scans/{scan_id}", dependencies=[Depends(require_user)])
def link_scan_get(scan_id: int, session: Session = Depends(get_session)):
    """The gun polls its own scan by id until an outcome appears."""
    row = session.get(LinkScan, scan_id)
    if row is None:
        raise HTTPException(404, "No such scan (it may have been swept).")
    return {"scan": row.as_dict()}


@app.post(
    "/api/link/scans/{scan_id}/result",
    dependencies=[Depends(require_user)],
)
def link_scan_result(
    scan_id: int,
    payload: LinkResultIn,
    session: Session = Depends(get_session),
):
    row = session.get(LinkScan, scan_id)
    if row is None:
        raise HTTPException(404, "No such scan (it may have been swept).")
    row.ok = payload.ok
    row.outcome = payload.outcome.strip()[:300] or None
    row.consumed_at = datetime.now(timezone.utc)
    session.commit()
    return {"scan": row.as_dict()}


@app.get("/api/batches", dependencies=[Depends(require_user)])
def list_batches(
    status: str | None = None,
    limit: int = 20,
    session: Session = Depends(get_session),
):
    stmt = select(Batch).order_by(Batch.id.desc())
    if status == "open":
        # Lazy mis-scan cleanup: untouched batches self-abandon at 4h.
        _expire_stale_batches(session)
        stmt = stmt.where(Batch.status.notin_(("done", "abandoned")))
    elif status:
        stmt = stmt.where(Batch.status == status.strip())
    rows = session.scalars(stmt.limit(min(limit, 100))).all()
    totals = {}
    if rows:
        for r in session.execute(
            select(
                BatchItem.batch_id,
                func.count().label("products"),
                func.sum(BatchItem.qty_scanned).label("boxes"),
                func.sum(BatchItem.paired_count).label("paired"),
            )
            .where(BatchItem.batch_id.in_([b.id for b in rows]))
            .group_by(BatchItem.batch_id)
        ).all():
            totals[r.batch_id] = r
    prev_done = _prev_done_map(session, [b.bin_name for b in rows])
    batches = []
    for b in rows:
        d = b.as_dict()
        t = totals.get(b.id)
        d["products"] = t.products if t else 0
        d["boxes"] = int(t.boxes or 0) if t else 0
        d["paired"] = int(t.paired or 0) if t else 0
        # A bin that already had a FULL tagging session: the C72 list
        # shows the yellow "Previous batch tagging: X ago" line, and the
        # batch itself runs the re-tag flow (quiet collect, shelf sweep
        # at Check). A done batch would match ITSELF — skip those rows.
        d["prev_done_at"] = (
            prev_done.get((b.bin_name or "").strip().upper())
            if b.status != "done"
            else None
        )
        batches.append(d)
    return {"count": len(batches), "batches": batches}


def _nickname_map(session: Session, skus: list) -> dict[str, str]:
    """SKU -> the vendor's name for the product ("nickname" aliases):
    what the box actually says when the vendor's labelling has nothing
    to do with our SKU or title (Nick, 2026-09-01)."""
    wanted = {(s or "").strip().upper() for s in skus if s and s.strip()}
    if not wanted:
        return {}
    out: dict[str, str] = {}
    for a in session.scalars(
        select(BarcodeAlias).where(
            BarcodeAlias.kind == "nickname",
            func.upper(BarcodeAlias.sku).in_(sorted(wanted)),
        ).order_by(BarcodeAlias.id)
    ):
        out[(a.sku or "").strip().upper()] = a.alias_barcode
    return out


@app.get("/api/batches/{batch_id}", dependencies=[Depends(require_user)])
def get_batch(batch_id: int, session: Session = Depends(get_session)):
    batch = _get_batch(session, batch_id)
    items = _batch_items(session, batch_id)
    nicknames = _nickname_map(session, [i.sku for i in items if i.sku])
    # How many labels this batch actually printed per product — the pair
    # step compares tags paired against labels printed, not boxes scanned.
    # Companion labels (box 2..N of a multi-box unit) are physical
    # stickers but never counting units, so they stay out of this math.
    printed: dict = {}
    for job in session.scalars(
        select(PrintJob).where(
            PrintJob.batch_id == batch_id,
            PrintJob.status.in_(("pending", "printing", "done")),
            _NOT_COMPANION,
        )
    ):
        if job.sku:
            printed[job.sku] = printed.get(job.sku, 0) + 1
    multibox = _multibox_map(session, [i.sku for i in items if i.sku])
    payload = []
    noscan = _noscan_skus(session)
    prior = _prior_tag_counts(
        session, batch_id, [i.sku for i in items if i.sku]
    )
    for item in items:
        d = item.as_dict()
        d["printed_count"] = printed.get(item.sku or "", 0)
        d["nickname"] = nicknames.get((item.sku or "").strip().upper())
        mb = multibox.get((item.sku or "").strip().upper())
        d["boxes_per_unit"] = mb.boxes_per_unit if mb else None
        d["rfid_incompatible"] = (
            (item.sku or "").strip().upper() in noscan
        )
        # Tags already in the system from BEFORE this batch (a side trip,
        # an earlier session) — the C72 warns on the first scan of such a
        # product so stickered boxes aren't labelled twice.
        d["prior_tags"] = prior.get((item.sku or "").strip().upper(), 0)
        payload.append(d)
    b = batch.as_dict()
    # A "Receive entire shipment" batch carries its order receipt, so
    # the web can show the settle / count-unused flow (Nick, 2026-09-01).
    receipt = session.scalar(
        select(OrderReceipt).where(OrderReceipt.batch_id == batch_id)
        .order_by(OrderReceipt.id.desc())
    )
    b["order_receipt"] = receipt.as_dict() if receipt else None
    if batch.status != "done":
        b["prev_done_at"] = _prev_done_map(
            session, [batch.bin_name]
        ).get((batch.bin_name or "").strip().upper())
        # Re-tagging a done bin leans on sales data: freshen the sold
        # ledger in the background (throttled; silently a no-op until
        # the read_orders scope exists).
        if b["prev_done_at"] and any(p.get("prior_tags") for p in payload):
            _kick_orders_sync_soon()
    else:
        b["prev_done_at"] = None
    cap = _latest_shelf_sweep(session, batch_id)
    b["shelf_swept_at"] = (
        cap.created_at.isoformat() if cap and cap.created_at else None
    )
    return {"batch": b, "items": payload}


def _prior_tag_counts(
    session: Session, batch_id: int, skus: list[str]
) -> dict[str, int]:
    """Per SKU (upper-cased), how many tags exist that did NOT come from
    this batch. Case-insensitive: tags applied before a SKU's casing was
    tidied in Shopify must still count."""
    wanted = {(s or "").strip().upper() for s in skus if s and s.strip()}
    if not wanted:
        return {}
    counts: dict[str, int] = {}
    for t in session.scalars(
        select(RfidAssignment).where(
            func.upper(RfidAssignment.sku).in_(sorted(wanted))
        )
    ):
        if t.batch_id == batch_id:
            continue
        key = (t.sku or "").strip().upper()
        counts[key] = counts.get(key, 0) + 1
    return counts


# ------------------------------------------------- re-tagging a done bin ----
# A bin that already had a FULL batch-tagging session (not a side trip)
# gets special treatment when tagged again (Nick, 2026-08-19): the
# already-tagged popup stays quiet during collect, and the Check step
# runs one bin-level SHELF SWEEP that sorts stickered boxes from new
# ones — reconciled against sales where possible.

def _prev_done_map(session: Session, bins: list[str]) -> dict[str, str]:
    """bin (upper) -> ISO time of its newest COMPLETED full batch.
    Side trips and receiving never count; abandoned never counts."""
    wanted = {(b or "").strip().upper() for b in bins if b and b.strip()}
    if not wanted:
        return {}
    out: dict[str, str] = {}
    for b in session.scalars(
        select(Batch).where(
            Batch.status == "done",
            Batch.parent_batch_id.is_(None),
            func.upper(Batch.bin_name).in_(sorted(wanted)),
        )
    ):
        key = b.bin_name.strip().upper()
        when = b.completed_at or b.created_at
        if when is None:
            continue
        prev = out.get(key)
        if prev is None or when.isoformat() > prev:
            out[key] = when.isoformat()
    return out


def _expire_stale_batches(session: Session) -> int:
    """Mis-scan protection for scan-creates-batch: an open batch nobody
    ever touched (zero scans, cases, pairs) quietly self-abandons after
    4 hours. Deliberate batches survive the moment one box is scanned."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
    expired = 0
    for b in session.scalars(
        select(Batch).where(
            Batch.status.notin_(("done", "abandoned")),
        )
    ):
        created = b.created_at
        if created is None:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created > cutoff:
            continue
        touched = any(
            (i.qty_scanned or 0) > 0
            or (i.case_count or 0) > 0
            or (i.paired_count or 0) > 0
            for i in _batch_items(session, b.id)
        )
        if not touched:
            b.status = "abandoned"
            expired += 1
    if expired:
        session.commit()
    return expired


def _latest_shelf_sweep(session: Session, batch_id: int) -> EpcCapture | None:
    return session.scalar(
        select(EpcCapture)
        .where(
            EpcCapture.batch_id == batch_id,
            EpcCapture.note == "shelf-sweep",
        )
        .order_by(EpcCapture.id.desc())
    )


def _shelf_reconcile(
    session: Session, batch: Batch, epcs: list[str]
) -> dict:
    """The shelf-sweep verdicts, per batch item with a SKU:
      heard     — sweep EPCs belonging to this SKU (any recorded bin —
                  a tag physically here counts, wherever its record says)
      on_file   — active tag records for the SKU in THIS bin
      expected  — on_file minus sold-since (real sales when the orders
                  ledger has them; else min(on_file, live on-hand), the
                  robust stand-in Nick approved until read_orders lands)
      state     — match / unheard (yellow) / silent (red) / none
    Plus: retired EPCs heard (peel-that-sticker warnings) and unknowns."""
    swept = {e.strip().upper() for e in epcs if e and e.strip()}
    items = [i for i in _batch_items(session, batch.id) if i.sku]
    skus_upper = {i.sku.strip().upper() for i in items}

    by_epc: dict[str, RfidAssignment] = {}
    per_sku: dict[str, list[RfidAssignment]] = {}
    if skus_upper:
        for t in session.scalars(
            select(RfidAssignment).where(
                func.upper(RfidAssignment.sku).in_(sorted(skus_upper))
            )
        ):
            by_epc[t.rfid_id.strip().upper()] = t
            per_sku.setdefault(t.sku.strip().upper(), []).append(t)

    on_hand: dict[str, int] = {}
    unavail_map: dict[str, int] = {}
    try:
        raw = shopify.get_quantity_pairs_by_skus(sorted(skus_upper))
        for k, pair in (raw or {}).items():
            key2 = (k or "").strip().upper()
            if pair[0] is not None:
                on_hand[key2] = pair[0]
                unavail_map[key2] = pair[1] or 0
    except Exception as error:
        logger.warning("shelf-sweep on-hand lookup failed: %s", error)
    # Offline fallback = the bin-map SNAPSHOT (the house rule), never
    # raw record counts. The first verify after a restart failed the
    # live lookup and expected jumped from min(records, on-hand) to ALL
    # records — Nick's 5/6 became 5/8 and stale sold tags were suddenly
    # "silent" again.
    snapshot: dict[str, int] = {}
    missing = {k for k in skus_upper if k not in on_hand}
    if missing:
        for bm in session.scalars(
            select(BinMapEntry).where(
                func.upper(BinMapEntry.sku).in_(sorted(missing))
            )
        ):
            if bm.qty is None:
                continue
            if not bin_contains(bm.bin, batch.bin_name):
                continue
            key = (bm.sku or "").strip().upper()
            snapshot[key] = snapshot.get(key, 0) + bm.qty
            unavail_map.setdefault(key, 0)
            unavail_map[key] += bm.unavailable or 0
    # "Won't RFID scan" products are EXPECTED silent: no red/yellow, and
    # apply must never zero their already-tagged count (that would print
    # doubles for stickered-but-mute Astronomik boxes).
    noscan = _noscan_skus(session)

    # First pass: per-item tag pools plus the sales-window baseline.
    # A sale fulfilled BEFORE the pool was last established cannot
    # explain a tag paired later (Nick's AIRPLUS case: the 3 PM sale
    # predates the 8:56 PM pairing), so only sales after the baseline
    # count. The baseline = newest pairing among the judged tags, or a
    # newer confirmed on-hand write for the SKU, whichever is later.
    onhand_marks: dict[str, object] = {}
    if skus_upper:
        for bc in session.scalars(
            select(BarcodeChange).where(
                func.upper(BarcodeChange.sku).in_(sorted(skus_upper)),
                BarcodeChange.changed_field.in_((
                    "on-hand", "on-hand-undo",
                    "on-hand-lower", "on-hand-lower-undo",
                )),
            )
        ):
            k = (bc.sku or "").strip().upper()
            t = orders_sync._as_utc(bc.changed_at)
            if t is not None and (
                k not in onhand_marks or t > onhand_marks[k]
            ):
                onhand_marks[k] = t

    prelim = []
    baselines: dict[str, object] = {}
    for item in items:
        key = item.sku.strip().upper()
        # Tags paired by THIS batch are not "earlier records" — they're
        # the work in progress. Counting them here made a freshly paired
        # tag look like a silent prior record at verify time.
        tags = [
            t for t in per_sku.get(key, [])
            if t.batch_id != batch.id
        ]
        in_bin = sorted(
            (t for t in tags
             if bin_contains(t.bin_location, batch.bin_name)),
            key=lambda t: (
                orders_sync._as_utc(t.assigned_at)
                or datetime.min.replace(tzinfo=timezone.utc)
            ),
        )
        heard_tags = [
            t for t in tags if t.rfid_id.strip().upper() in swept
        ]
        heard = len(heard_tags)
        on_file = len(in_bin)
        # Oldest first, so retire offers consume the longest-silent
        # records before newer ones.
        unheard_epcs = [
            t.rfid_id for t in in_bin
            if t.rfid_id.strip().upper() not in swept
        ]
        base = max(
            (orders_sync._as_utc(t.assigned_at)
             for t in in_bin if t.assigned_at),
            default=None,
        )
        om = onhand_marks.get(key)
        if om is not None and (base is None or om > base):
            base = om
        baselines[key] = base
        prelim.append((item, key, heard, on_file, unheard_epcs))

    sold_since = orders_sync.sold_unretired_since_map(
        session, sorted(skus_upper), baselines
    )
    covers = orders_sync.ledger_covers_from_map(
        session, sorted(skus_upper)
    )

    out_items = []
    for item, key, heard, on_file, unheard_epcs in prelim:
        silent = len(unheard_epcs)
        sales_since = sold_since.get(key, 0)
        # Sales can only explain tags that actually went silent, and
        # never more of them than there were sales in the window.
        explained = min(silent, sales_since)
        unexplained = silent - explained
        base = baselines.get(key)
        lf = covers.get(key)
        sales_gap = base is not None and (lf is None or lf > base)
        if sales_since > 0:
            expected = max(0, on_file - sales_since)
            basis = "sales"
        elif key in on_hand:
            expected = min(on_file, on_hand[key])
            basis = "on-hand"
        elif key in snapshot:
            expected = min(on_file, snapshot[key])
            basis = "snapshot"
        else:
            expected = on_file
            basis = "records"
        # State ladder, the single source of truth for BOTH the web
        # check list and the C72 shelf rows (they only render `state`).
        # With sales in the window, green means "sales fully explain
        # every silent record" (the verify tri-state's rule, so the gun
        # and the web can never disagree). With no windowed sales the
        # judgment falls to the on-hand-capped expected, as before. A
        # sweep that hears EVERY in-bin record is green regardless
        # (over-hearing a neighbor is the over_heard note's job, not a
        # yellow).
        if key in noscan:
            state = "noscan"
        elif on_file == 0 and heard == 0:
            state = "none"
        elif (
            silent == 0
            or (sales_since > 0 and unexplained == 0)
            or (sales_since == 0 and heard == expected)
        ):
            state = "match"
        elif heard == 0:
            state = "silent"
        else:
            state = "unheard"
        # Presumption ladder: with a sales ledger, presume ONLY what
        # windowed sales genuinely cover (the old max(0, on_file -
        # expected) presumed every gap sold). With no ledger rows at all
        # (pre-scope era, untracked product) the on-hand shortfall keeps
        # carrying the presumption as before; raw records presume
        # nothing.
        if lf is not None:
            presumed = explained
        elif basis in ("on-hand", "snapshot"):
            presumed = max(0, on_file - expected)
        else:
            presumed = 0
        boxes_over = max(
            0,
            ((item.qty_scanned or 0) + (item.tagged_before or 0))
            - expected,
        ) if basis in ("on-hand", "snapshot") else 0
        out_items.append({
            "item_id": item.id,
            "sku": item.sku,
            "heard": heard,
            "on_file": on_file,
            "expected": expected,
            "basis": basis,
            "presumed_sold": presumed,
            "sales_since": sales_since,
            "explained": explained,
            "unexplained": unexplained,
            "sales_window_from": (
                base.isoformat() if base is not None else None
            ),
            "sales_gap": sales_gap,
            "ledger_from": lf.isoformat() if lf is not None else None,
            "on_hand": on_hand.get(key),
            "state": state,
            "unheard_epcs": unheard_epcs,
            # Physical boxes counted at collect (qty + tagged survives
            # the apply split, so this is stable across re-applies).
            "boxes": (item.qty_scanned or 0) + (item.tagged_before or 0),
            # The sweep heard more of this SKU's tags than boxes were
            # collected: a neighboring shelf answering, or uncollected
            # stock. The apply split never lets this raise the count
            # (collection is fact; Nick, 2026-08-24).
            "over_heard": max(
                0,
                heard - (
                    (item.qty_scanned or 0) + (item.tagged_before or 0)
                ),
            ),
            # Shelf holds MORE than expected but Shopify's Unavailable
            # bucket accounts for it (Nick, 2026-09-01, W9160A): the
            # damaged/reserved unit turned out to be on the shelf after
            # all. Both UIs flag it as EXPLAINED, not an error.
            "unavailable": unavail_map.get(key, 0),
            "over_unavailable": (
                boxes_over
                if boxes_over > 0
                and boxes_over <= unavail_map.get(key, 0)
                else 0
            ),
        })

    known = set(by_epc.keys())
    strays = []
    unknown = 0
    for e in sorted(swept - known):
        r = session.scalar(
            select(RetiredTag).where(
                func.upper(RetiredTag.rfid_id) == e
            )
        )
        if r is not None:
            strays.append({
                "epc": r.rfid_id,
                "sku": r.sku,
                "kind": r.kind,
                "message": (
                    "replaced sticker still on a box — peel it off"
                    if r.kind in ("replaced", "dead")
                    else "retired tag heard — possible return; check the box"
                ),
            })
        else:
            unknown += 1
    return {"items": out_items, "strays": strays, "unknown": unknown}


def _short_date(iso) -> str:
    """'2026-08-19T20:56:12+00:00' -> 'Aug 19' for reason strings."""
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(str(iso)).strftime("%b %d")
    except ValueError:
        return str(iso)[:10]


class ShelfSweepIn(BaseModel):
    epcs: list[str] = Field(default_factory=list, max_length=20000)
    device: str | None = Field(default=None, max_length=100)
    # False = live preview while sweeping; True = commit: store the
    # capture (the web prompt watches for it), stamp baseline_at, and
    # write each item's already-tagged count from what was heard.
    apply: bool = False


@app.post(
    "/api/batches/{batch_id}/shelf-sweep",
    dependencies=[Depends(require_user)],
)
def batch_shelf_sweep(
    batch_id: int,
    payload: ShelfSweepIn,
    session: Session = Depends(get_session),
):
    batch = _get_batch(session, batch_id)
    result = _shelf_reconcile(session, batch, payload.epcs)
    if payload.apply:
        uniq = sorted({
            e.strip().upper() for e in payload.epcs if e and e.strip()
        })
        session.add(EpcCapture(
            device=payload.device,
            note="shelf-sweep",
            batch_id=batch_id,
            epc_count=len(uniq),
            epcs="\n".join(uniq),
        ))
        batch.baseline_at = datetime.now(timezone.utc)
        # Collect counts EVERY box on a re-tag bin (Nick: scan without
        # worrying about stickers); the sweep SPLITS that count into
        # already-tagged vs needs-a-label. Never additive — the first
        # cut set tagged_before on top of qty_scanned and every heard
        # box counted twice. The boxes-here baseline is qty + tagged,
        # which keeps a re-apply (KEEP SWEEPING → APPLY again)
        # idempotent. Noscan products sit out entirely — their stickers
        # can't answer, and zeroing a hand-set count prints doubles.
        by_item = {r["item_id"]: r for r in result["items"]}
        for item in _batch_items(session, batch_id):
            r = by_item.get(item.id)
            if r is None or r["state"] == "noscan":
                continue
            if r["heard"] > 0 or r["on_file"] > 0:
                baseline = (
                    (item.qty_scanned or 0) + (item.tagged_before or 0)
                )
                # Collection is fact: the sweep SPLITS the collected
                # count, it never raises it. Hearing more tags than
                # boxes (a neighboring shelf, uncollected stock) is
                # reported as over_heard instead of silently inflating
                # tagged_before past what the operator counted (Nick's
                # batch 159: collected 4, heard 5, stored 5).
                item.tagged_before = min(r["heard"], baseline)
                item.qty_scanned = baseline - item.tagged_before
        session.commit()
    result["applied"] = payload.apply
    return result


@app.get(
    "/api/batches/{batch_id}/shelf-sweep",
    dependencies=[Depends(require_user)],
)
def batch_shelf_sweep_state(
    batch_id: int, session: Session = Depends(get_session)
):
    """The stored shelf sweep, re-reconciled — what the web check/verify
    steps read (and how they notice the C72 already swept)."""
    batch = _get_batch(session, batch_id)
    cap = _latest_shelf_sweep(session, batch_id)
    if cap is None:
        return {"swept": False}
    result = _shelf_reconcile(
        session, batch, cap.epcs.split("\n") if cap.epcs else []
    )
    result["swept"] = True
    result["swept_at"] = (
        cap.created_at.isoformat() if cap.created_at else None
    )
    result["device"] = cap.device
    return result


class RetireTagsIn(BaseModel):
    epcs: list[str] = Field(min_length=1, max_length=1000)
    # presumed-sold (verify cleanup) | replaced (peeled, read off-box) |
    # dead (unreadable even off the box)
    kind: Literal["presumed-sold", "replaced", "dead"]
    changed_by: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=255)


@app.post(
    "/api/assignments/retire",
    dependencies=[Depends(require_user)],
)
def retire_tags(
    payload: RetireTagsIn, session: Session = Depends(get_session)
):
    """Move tag records to the retired table. Local records only —
    Shopify is never touched. Every EPC keeps a permanent, recognizable
    row (tombstone) so a future sweep hearing it can say WHAT it is
    instead of reporting an unknown tag."""
    moved = []
    moved_rows: list[tuple[RetiredTag, RfidAssignment]] = []
    for epc in payload.epcs:
        t = session.scalar(
            select(RfidAssignment).where(
                func.upper(RfidAssignment.rfid_id)
                == epc.strip().upper()
            )
        )
        if t is None:
            continue
        rt = RetiredTag(
            rfid_id=t.rfid_id,
            sku=t.sku,
            product_title=t.product_title,
            shopify_variant_id=t.shopify_variant_id,
            bin_location=t.bin_location,
            case_units=t.case_units,
            kind=payload.kind,
            retired_by=payload.changed_by,
            note=payload.note,
        )
        session.add(rt)
        session.add(BarcodeChange(
            sku=t.sku,
            product_title=t.product_title,
            shopify_variant_id=t.shopify_variant_id,
            changed_field="tag-retired",
            old_barcode=t.rfid_id,
            new_barcode=payload.kind,
            changed_by=payload.changed_by,
        ))
        session.delete(t)
        moved.append(t.rfid_id)
        moved_rows.append((rt, t))
    if not moved:
        raise HTTPException(404, "None of those EPCs are active tags.")
    if payload.kind == "presumed-sold":
        _consume_ledger_for_retirements(session, moved_rows)
    session.commit()
    return {"retired": moved, "kind": payload.kind}


def _consume_ledger_for_retirements(
    session: Session,
    moved_rows: list[tuple[RetiredTag, RfidAssignment]],
) -> None:
    """Presumed-sold retirements consume matching sold-ledger units so
    'sold-unretired' keeps meaning 'sales with no physical resolution
    yet' (Nick, 2026-08-24 — before this, sales stayed unconsumed
    forever and every later reconcile re-blamed them). Windowed rows
    (sold after the SKU's remaining tag pool baseline) go first, oldest
    first; each RetiredTag records what it consumed so unretire can hand
    exactly that many back."""
    session.flush()  # deletes must be visible to the baseline query
    by_sku: dict[str, list[tuple[RetiredTag, RfidAssignment]]] = {}
    for rt, t in moved_rows:
        if t.sku:
            by_sku.setdefault(t.sku.strip().upper(), []).append((rt, t))
    for key, rows in by_sku.items():
        bin_name = rows[0][1].bin_location
        remaining = session.scalars(
            select(RfidAssignment).where(
                func.upper(RfidAssignment.sku) == key
            )
        ).all()
        since = max(
            (orders_sync._as_utc(x.assigned_at) for x in remaining
             if x.assigned_at
             and bin_contains(x.bin_location, bin_name or "")),
            default=None,
        )
        units = sum((t.case_units or 1) for _, t in rows)
        landed = orders_sync.retire_units(
            session, rows[0][1].sku, units, since=since
        )
        for rt, t in rows:
            take = min(landed, t.case_units or 1)
            rt.ledger_consumed = take
            landed -= take


class UnretireTagsIn(BaseModel):
    epcs: list[str] = Field(min_length=1, max_length=1000)
    changed_by: str | None = Field(default=None, max_length=100)


@app.post(
    "/api/assignments/unretire",
    dependencies=[Depends(require_user)],
)
def unretire_tags(
    payload: UnretireTagsIn, session: Session = Depends(get_session)
):
    """Undo for retire_tags: the row moves back to the active table
    (a return, a mis-click, a sweep that lied)."""
    restored = []
    for epc in payload.epcs:
        r = session.scalar(
            select(RetiredTag).where(
                func.upper(RetiredTag.rfid_id) == epc.strip().upper()
            )
        )
        if r is None:
            continue
        if session.scalar(
            select(RfidAssignment).where(
                func.upper(RfidAssignment.rfid_id)
                == epc.strip().upper()
            )
        ) is not None:
            continue  # EPC re-used on a new box since — leave it alone
        session.add(RfidAssignment(
            rfid_id=r.rfid_id,
            shopify_variant_id=r.shopify_variant_id or "",
            product_title=r.product_title or r.sku or "(unknown)",
            sku=r.sku,
            bin_location=r.bin_location,
            case_units=r.case_units,
            assigned_by=payload.changed_by,
        ))
        session.add(BarcodeChange(
            sku=r.sku,
            product_title=r.product_title,
            shopify_variant_id=r.shopify_variant_id,
            changed_field="tag-unretired",
            old_barcode=r.rfid_id,
            new_barcode=r.kind,
            changed_by=payload.changed_by,
        ))
        # Hand back exactly the ledger units this retirement consumed
        # (newest-first inverse), so undo round-trips conserve the books.
        if r.sku and (r.ledger_consumed or 0) > 0:
            orders_sync.unretire_units(session, r.sku, r.ledger_consumed)
        session.delete(r)
        restored.append(r.rfid_id)
    if not restored:
        raise HTTPException(
            404,
            "None of those EPCs are in the retired list (or they were "
            "re-used on new boxes since).",
        )
    session.commit()
    return {"restored": restored}


class ReplaceTagIn(BaseModel):
    # The off-box read, when the peeled sticker still answered. Absent =
    # truly dead: the oldest unheard record for the SKU in this bin goes.
    epc: str | None = Field(default=None, max_length=128)
    changed_by: str | None = Field(default=None, max_length=100)


@app.post(
    "/api/batches/{batch_id}/items/{item_id}/replace-tag",
    dependencies=[Depends(require_user)],
)
def replace_dead_tag(
    batch_id: int,
    item_id: int,
    payload: ReplaceTagIn,
    session: Session = Depends(get_session),
):
    """The dead-tag last resort (after retries and one-by-one scanning):
    the sticker comes OFF the box. Read off-box -> that exact EPC is
    retired as 'replaced' (the product was blocking RF). Still silent ->
    the oldest unheard record for this SKU in this bin is retired as
    'dead'. Either way the box counts as untagged and gets a fresh
    label. Sticker is discarded on the floor; records logged here."""
    batch = _get_batch(session, batch_id)
    item = session.get(BatchItem, item_id)
    if item is None or item.batch_id != batch_id:
        raise HTTPException(404, "No such item in this batch.")
    if not item.sku:
        raise HTTPException(422, "This row has no SKU.")

    if payload.epc:
        target = session.scalar(
            select(RfidAssignment).where(
                func.upper(RfidAssignment.rfid_id)
                == payload.epc.strip().upper()
            )
        )
        if target is None:
            raise HTTPException(
                404,
                "That EPC isn't an active tag — it may already be "
                "retired, or the read was garbled.",
            )
        if (target.sku or "").strip().upper() \
                != item.sku.strip().upper():
            raise HTTPException(
                409,
                f"That tag belongs to {target.sku}, not {item.sku} — "
                "wrong sticker in hand?",
            )
        kind = "replaced"
    else:
        cap = _latest_shelf_sweep(session, batch_id)
        swept = set()
        if cap is not None and cap.epcs:
            swept = {e.strip().upper() for e in cap.epcs.split("\n")}
        target = next(
            (
                t for t in session.scalars(
                    select(RfidAssignment)
                    .where(
                        func.upper(RfidAssignment.sku)
                        == item.sku.strip().upper()
                    )
                    .order_by(RfidAssignment.id)
                )
                if bin_contains(t.bin_location, batch.bin_name)
                and t.rfid_id.strip().upper() not in swept
            ),
            None,
        )
        if target is None:
            raise HTTPException(
                404,
                "No unheard tag record is left for this product in "
                "this bin — nothing to drop.",
            )
        kind = "dead"

    session.add(RetiredTag(
        rfid_id=target.rfid_id,
        sku=target.sku,
        product_title=target.product_title,
        shopify_variant_id=target.shopify_variant_id,
        bin_location=target.bin_location,
        case_units=target.case_units,
        kind=kind,
        retired_by=payload.changed_by,
        note=f"batch {batch_id} · {batch.bin_name}",
    ))
    session.add(BarcodeChange(
        sku=target.sku,
        product_title=target.product_title,
        shopify_variant_id=target.shopify_variant_id,
        changed_field="tag-retired",
        old_barcode=target.rfid_id,
        new_barcode=kind,
        changed_by=payload.changed_by,
    ))
    retired_epc = target.rfid_id
    session.delete(target)
    # The box in hand is now untagged: it must NOT sit in the
    # already-tagged count or it gets no replacement label.
    if (item.tagged_before or 0) > 0:
        item.tagged_before -= 1
    session.commit()
    session.refresh(item)
    return {
        "retired_epc": retired_epc,
        "kind": kind,
        "item": item.as_dict(),
    }


# Throttle: opening the same flagged bin twice in a row shouldn't hammer
# the orders API. One kick per 10 minutes across all batches.
_orders_kick_at: dict[str, float] = {"t": 0.0}


def _kick_orders_sync_soon() -> None:
    now = time.time()
    if now - _orders_kick_at["t"] < 600:
        return
    _orders_kick_at["t"] = now

    def _run():
        try:
            with Session(get_engine()) as s:
                orders_sync.run(s, source="batch-open")
        except Exception as error:
            logger.warning("batch-open orders kick failed: %s", error)

    threading.Thread(target=_run, daemon=True).start()


class BatchScanIn(BaseModel):
    code: str = Field(max_length=64)
    # Only meaningful when the code is a known case. Absent = the operator
    # hasn't been asked yet, so the scan pauses and asks.
    case_action: Literal["open", "sealed"] | None = None

    @field_validator("code")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


@app.post(
    "/api/batches/{batch_id}/scan",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def batch_scan(
    batch_id: int, payload: BatchScanIn, session: Session = Depends(get_session)
):
    """One box scanned at the shelf. Resolves through the full Scan Station
    chain (bin map -> Shopify API -> alias -> serial prefix); repeated
    scans of the same product bump its count. Unknown barcodes are kept as unresolved
    rows so the physical count survives — they never block the batch."""
    batch = _get_batch(session, batch_id)
    if batch.status in ("done", "abandoned"):
        raise HTTPException(409, f"This batch is {batch.status}.")

    code = payload.code

    # A case code is not a product. Ask once whether the box is being opened,
    # because the answer changes the count, the labels and the tags — then
    # carry on as a scan of the product INSIDE.
    case = _case_for(session, code)
    if case is not None and payload.case_action is None:
        return {
            "needs_case_decision": True,
            "case": case,
            "item": None,
            "message": (
                f"{code} is a case of {case['units']} x {case['sku']}"
                + (f" — {case['scan_note']}" if case.get("scan_note") else "")
            ),
        }

    lookup = case["sku"] if case is not None else code
    product = None
    try:
        product = product_by_barcode(lookup)
    except HTTPException as error:
        if error.status_code != 404:
            raise

    items = _batch_items(session, batch_id)
    item = None
    if product is not None:
        sku = product.get("sku")
        sku_ci = (sku or "").strip().upper()
        barcode = product.get("barcode")
        for i in items:
            # SKU match is case-insensitive: the mirror's casing drifts
            # ('ZWO Anti-dew'), and an exact match here split one product
            # into two batch rows.
            if i.resolved and (
                (sku and (i.sku or "").strip().upper() == sku_ci)
                or (not sku and barcode and i.barcode == barcode)
            ):
                item = i
                break
    else:
        for i in items:
            if not i.resolved and i.scanned_code == code:
                item = i
                break

    if item is None:
        item = BatchItem(
            batch_id=batch.id,
            # Remember the product's own code, not the case's, so the row
            # re-checks and reprints against something Shopify recognises.
            scanned_code=(lookup if case is not None else code)[:64],
            qty_scanned=0,
        )
        if product is not None:
            _apply_product_to_item(session, item, product, batch)
            # Batch labels print the store header + SKU (Astronomik naming
            # lives in Scan Station), so no per-item label name here.
            item.label_name = None
        else:
            item.resolved = False
            item.product_title = f"Unresolved: {code}"
        session.add(item)

    # A pre-seeded row scanned via a brand serial learns its prefix on first
    # contact — the pair stage needs it to tell barcodes from EPCs. The label
    # name stays the store default; Astronomik naming is Scan Station only.
    if product is not None and product.get("serial_prefix"):
        if not item.serial_prefix:
            item.serial_prefix = product["serial_prefix"]

    if case is None:
        item.qty_scanned += 1
    elif payload.case_action == "open":
        # Opened: the units go on the shelf individually, so they behave
        # exactly like that many loose boxes.
        item.qty_scanned += case["units"]
    else:
        # Sealed: ONE box, one label, one tag — but worth `units` of stock.
        item.case_count += 1
        item.case_units = case["units"]
    # First physical contact stamps the walking order — labels queue in
    # this order so the printed stack matches the shelf walk.
    if item.first_scanned_at is None:
        item.first_scanned_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(item)

    # Bin mismatch is informational: the operator decides at the shelf
    # (keep saved bin / move it via the existing confirmed bin update).
    # Receiving has no shelf — EVERY product would "mismatch" its sentinel
    # bin and the keep-or-move prompt would fire on every box, so it's off.
    saved_bin = item.bin_location
    # A product split across shelves ("G2-1 & B17") is legitimately here as
    # long as this bin is one of the ones listed.
    bin_mismatch = bool(
        not _is_receiving(batch)
        and item.resolved
        and saved_bin
        and saved_bin not in MISSING_BIN_VALUES
        and not bin_contains(saved_bin, batch.bin_name)
    )
    item_dict = item.as_dict()
    item_dict["rfid_incompatible"] = bool(
        item.sku
        and session.get(RfidIncompatible, item.sku.strip()) is not None
    )
    item_dict["prior_tags"] = (
        _prior_tag_counts(session, batch_id, [item.sku]).get(
            item.sku.strip().upper(), 0
        )
        if item.sku else 0
    )
    return {
        "item": item_dict,
        "bin_mismatch": bin_mismatch,
        "serial_note": (product or {}).get("serial_note"),
        # Present whenever a case was scanned, so the note shows here too.
        "case": case,
        "case_action": payload.case_action if case is not None else None,
    }


def _mojibake(*vals: str | None) -> bool:
    """True when a SKU/barcode carries a literal '?' or any non-ASCII
    char. The database's VARCHAR columns store non-Latin chars AS '?', so
    neither form survives a round trip and record matching silently
    breaks (ZWO's SKUs used the single Unicode char 'Ⅱ' for II)."""
    return any(
        ch == "?" or ord(ch) > 126 for v in vals for ch in (v or "")
    )


def _bad_char_fields(item: BatchItem) -> dict:
    """WHICH field broke, with the offending character wrapped in [ ] so
    the operator can see it (Nick, 2026-08-26: the ZWO HA7nm1.25 flag
    just said "shows as ?", which names nothing). The real character
    only exists in live Shopify - our VARCHAR stored it as '?' - so one
    live fetch recovers it; if that fails, the stored '?' is bracketed
    as the honest fallback. Returns e.g. {"sku": 'ZWO-HA 7nm 1.25["]'}
    with only the broken field(s) present."""
    live = None
    vid = (item.shopify_variant_id or "").strip()
    if vid.startswith("gid://"):
        try:
            live = shopify.get_variant_idents(vid)
        except Exception:  # noqa: BLE001 — the fallback still names the spot
            live = None

    def _mark(v: str) -> str:
        return "".join(
            f"[{ch}]" if ch == "?" or ord(ch) > 126 else ch for ch in v
        )

    out: dict = {}
    for field in ("sku", "barcode"):
        stored = getattr(item, field)
        if not _mojibake_value(stored):
            continue
        live_v = (live or {}).get(field)
        use = live_v if live_v and _mojibake_value(live_v) else stored
        out[field] = _mark(use)
    return out


# Check-step list ordering (Nick, 2026-08-26): the biggest problems
# first. count-mismatch is explicitly the LEAST important - a count
# nudge should never bury a broken record or a wrong-shelf box. Shared
# by web and C72 (both render the server's order); stable sort keeps
# shelf order inside each tier.
_FLAG_RANK = {
    "unresolved": 9,
    "bad-chars": 8,
    "ambiguous": 7,
    "tags-silent": 6,
    "wrong-bin": 5,
    "tags-unheard": 4,
    "double-count": 3,
    "tagged-not-detected": 3,
    "skipped": 2,
    "bundle": 2,
    "unconfirmed-name": 2,
    "not-on-shelf": 1,
    "count-mismatch": 0,
}


def _flag_rank(entry: dict) -> int:
    return max(
        (_FLAG_RANK.get(f, 1) for f in entry.get("flags") or []), default=0
    )


@app.get(
    "/api/batches/{batch_id}/review", dependencies=[Depends(require_user)]
)
def batch_review(batch_id: int, session: Session = Depends(get_session)):
    """The Check step, shared by web and C72: which items need a human
    decision before labels print, and why. Flags: 'ambiguous' (barcode
    matches several listings — candidates included, primary first),
    'count-mismatch' (scanned != expected), 'unconfirmed-name' (serialized
    product whose label name was never operator-confirmed), 'unresolved'
    (barcode matched nothing), 'bad-chars' (SKU/barcode carries a literal
    '?' or a non-ASCII char that the database mangles)."""
    batch = _get_batch(session, batch_id)
    # The bin map is read ONCE here and handed to the per-item helpers.
    # Before this, the stale-barcode check and the sibling scan each read
    # the whole table for every touched item — ~2 full reads x 20 items on
    # a real shelf, which grew past the C72's 20s timeout as the map grew.
    bin_rows: list[BinMapEntry] | None = None
    live_bc: dict[str, str] | None = None

    def _catalog_once():
        nonlocal bin_rows, live_bc
        if bin_rows is None:
            bin_rows = session.scalars(
                select(BinMapEntry).where(BinMapEntry.sku.isnot(None))
            ).all()
            live_bc = {}
            for r in bin_rows:
                if r.sku and r.sku not in live_bc:
                    live_bc[r.sku] = (r.barcode or "").strip()
        return bin_rows, live_bc

    noscan = _noscan_skus(session)

    # Re-tag flow: once the bin-level shelf sweep ran, its per-item
    # verdicts ride the check list (yellow = fewer heard than expected,
    # red = expected but silent).
    shelf_by_item: dict[int, dict] = {}
    if batch.status != "done" and _prev_done_map(
        session, [batch.bin_name]
    ).get((batch.bin_name or "").strip().upper()):
        cap = _latest_shelf_sweep(session, batch_id)
        if cap is not None:
            rec = _shelf_reconcile(
                session, batch,
                cap.epcs.split("\n") if cap.epcs else [],
            )
            shelf_by_item = {r["item_id"]: r for r in rec["items"]}

    flagged = []
    for item in _batch_items(session, batch_id):
        # A skipped row is a decision already made, not a problem to solve.
        # Checked FIRST: a skipped product usually has nothing scanned, so
        # the untouched-rows shortcut below would otherwise hide it — and a
        # deliberate skip is exactly what should be visible before printing.
        if item.skipped:
            flagged.append({
                "item": item.as_dict(),
                "flags": ["skipped"],
                "candidates": [],
            })
            continue
        if (
            item.qty_scanned == 0
            and item.case_count == 0
            and item.paired_count == 0
        ):
            # Untouched pre-seeded rows need no checking — with one
            # exception. After a baseline sweep, a product with tags on
            # file FOR THIS SHELF that the sweep never read is exactly the
            # weak-RFID case (Astronomik): re-tagging it blind would put a
            # second tag on a box that already wears one, so it gets its
            # own flag and a human look instead.
            if (
                batch.baseline_at is not None
                and item.resolved
                and item.sku
                and item.tagged_before == 0
                # "Won't RFID scan" products are EXPECTED silent — flagging
                # every baseline sweep forever would train people to ignore
                # the flag on products where it means something.
                and item.sku.strip().upper() not in noscan
            ):
                tags_here = [
                    t for t in session.scalars(
                        select(RfidAssignment)
                        .where(RfidAssignment.sku == item.sku)
                    )
                    if bin_contains(t.bin_location, batch.bin_name)
                ]
                if tags_here:
                    flagged.append({
                        "item": item.as_dict(),
                        "flags": ["tagged-not-detected"],
                        "candidates": [],
                        "tags_on_file": len(tags_here),
                    })
                    continue
            # Shopify expects stock of this product HERE, yet the walk of
            # the shelf never met a single box of it: it's sitting in some
            # other bin (or the count is fiction). D1-1 was full of these
            # and the Check step showed a clean bill — the one thing the
            # operator wanted to hear about was the one thing hidden.
            # Informational only; guarded to rows whose saved bin really is
            # this shelf, so a scanned-then-zeroed stray doesn't nag too.
            saved = (item.bin_location or "").strip()
            if (
                item.resolved
                and (item.expected_qty or 0) > 0
                and item.tagged_before == 0
                and saved
                and saved.lower() != "no bin assigned"
                and bin_contains(saved, batch.bin_name)
            ):
                flagged.append({
                    "item": item.as_dict(),
                    "flags": ["not-on-shelf"],
                    "candidates": [],
                })
            continue
        flags = []
        candidates: list[dict] = []
        bad_chars = None
        if not item.resolved:
            flags.append("unresolved")
        else:
            # A bundle occupying box slots is a decision waiting to happen:
            # tag nothing, or drop it from the system for good.
            if item.kind == "bundle":
                flags.append("bundle")
            code = item.barcode or item.scanned_code
            if code:
                rows_once, live_once = _catalog_once()
                try:
                    candidates = products_by_barcode_all(
                        code, live_barcodes=live_once
                    )
                except Exception as error:
                    logger.warning("candidates failed for %s: %s",
                                   code, error)
                # Open-box twins often carry NO barcode of their own
                # ("OPEN BOX- 08891" against "08891"), so a barcode search
                # can never surface them and the operator is left holding a
                # box with no way to pick the right listing. Fold in
                # siblings found by SKU.
                candidates = _merge_siblings(
                    session, item, candidates, bin_rows=rows_once
                )
                # An explicit USE THIS LISTING choice (listing_locked)
                # settles the row: the twin listings keep existing, so
                # the candidate count alone could never clear the flag.
                # Candidates stay listed for a change of mind.
                if len(candidates) > 1 and not item.listing_locked:
                    flags.append("ambiguous")
                elif len(candidates) <= 1:
                    candidates = []
            # Receiving compares against nothing: expected_qty is the
            # SHELF count, and a shipment legitimately adds to it.
            if (
                not _is_receiving(batch)
                and item.expected_qty is not None
                and _units_on_shelf(item) != item.expected_qty
            ):
                flags.append("count-mismatch")
            if item.serial_prefix:
                sp = session.get(SerialPrefix, item.serial_prefix)
                if sp is not None and sp.label_name is None:
                    flags.append("unconfirmed-name")
            # Saved bin differs from the bin being walked: the boxes are on
            # the wrong shelf (or the record is). Never blocks — the
            # operator picks move / relabel here / ignore. A receiving
            # batch has no shelf, so EVERY product would "mismatch" — the
            # whole flag is meaningless there and stays off.
            saved = (item.bin_location or "").strip()
            if (
                not _is_receiving(batch)
                and saved
                and saved.lower() != "no bin assigned"
                and not bin_contains(
                    saved, _get_batch(session, batch_id).bin_name
                )
            ):
                flags.append("wrong-bin")
            # Scans AND an already-tagged count on the same row: if the
            # stickered boxes were among the scans, the units double up.
            # A reminder with a one-tap fix, never an automatic change —
            # "X new boxes plus Y tagged ones" is physically possible.
            # NOT on shelf-swept items: there the sweep SPLIT the
            # collected count, so qty + tagged together IS the box count.
            sh_dc = shelf_by_item.get(item.id)
            if (
                item.qty_scanned
                and item.tagged_before
                and not (
                    sh_dc is not None
                    and sh_dc["state"] in ("match", "unheard", "silent")
                )
            ):
                flags.append("double-count")
            # A literal '?' or any non-ASCII char in the SKU/barcode is a
            # record that can't round-trip: SQL Server's VARCHAR stores
            # such chars AS '?' (the Ⅱ-in-a-SKU ZWO case), so every later
            # lookup quietly misses. Flag it here, where the operator can
            # fix the SKU/barcode on the spot.
            if _mojibake(item.sku, item.barcode):
                flags.append("bad-chars")
                bad_chars = _bad_char_fields(item)
            sh = shelf_by_item.get(item.id)
            if sh is not None and sh["state"] == "unheard":
                flags.append("tags-unheard")
            elif sh is not None and sh["state"] == "silent":
                flags.append("tags-silent")
        if flags:
            entry = {
                "item": item.as_dict(),
                "flags": flags,
                "candidates": candidates,
                "shelf": shelf_by_item.get(item.id),
            }
            if bad_chars:
                entry["bad_chars"] = bad_chars
            flagged.append(entry)
    # Strays gathered by the shelf they actually belong on, so the Check step
    # can offer one trip per bin rather than one per product.
    strays: dict = {}
    for entry in flagged:
        if "wrong-bin" not in entry["flags"]:
            continue
        saved = entry["item"].get("bin_location")
        for name in parse_bins(saved):
            strays.setdefault(name, []).append(entry["item"].get("sku"))
    # Boxes ALREADY tagged and recorded at each stray's home bin: the
    # keep-or-move decision reads differently when the recommended shelf
    # provably holds stock — keeping the stray here drags those boxes'
    # records along with the bin update.
    wrong_skus = {
        (e["item"].get("sku") or "").strip().upper()
        for e in flagged
        if "wrong-bin" in e["flags"] and e["item"].get("sku")
    }
    if wrong_skus:
        tags_of: dict[str, list] = {}
        for t in session.scalars(
            select(RfidAssignment).where(
                func.upper(RfidAssignment.sku).in_(sorted(wrong_skus))
            )
        ):
            tags_of.setdefault((t.sku or "").strip().upper(), []).append(t)
        for entry in flagged:
            if "wrong-bin" not in entry["flags"]:
                continue
            key = (entry["item"].get("sku") or "").strip().upper()
            homes = {
                b.lower()
                for b in parse_bins(entry["item"].get("bin_location"))
            }
            entry["record_bin_tags"] = sum(
                1 for t in tags_of.get(key, [])
                if (t.bin_location or "").strip().lower() in homes
            )
    for entry in flagged:
        entry["item"]["rfid_incompatible"] = (
            (entry["item"].get("sku") or "").strip().upper() in noscan
        )
    # Biggest problems first; count-mismatch-only rows sink to the
    # bottom (Nick, 2026-08-26). Stable: shelf order holds within a tier.
    flagged.sort(key=lambda e: -_flag_rank(e))
    return {
        "count": len(flagged),
        "items": flagged,
        "stray_bins": [
            {"bin": name, "skus": skus, "count": len(skus)}
            for name, skus in sorted(strays.items())
        ],
    }


class ReassignIn(BaseModel):
    shopify_variant_id: str = Field(max_length=64)


@app.post(
    "/api/batches/{batch_id}/items/{item_id}/reassign",
    dependencies=[Depends(require_user)],
)
def batch_item_reassign(
    batch_id: int,
    item_id: int,
    payload: ReassignIn,
    session: Session = Depends(get_session),
):
    """Point an ambiguous item at a different listing sharing its barcode.
    The WHOLE scanned count moves (mixed shelves get fixed with -/+
    afterwards). If the target product is already in the batch, the counts
    merge into that row."""
    _get_batch(session, batch_id)
    item = session.get(BatchItem, item_id)
    if item is None or item.batch_id != batch_id:
        raise HTTPException(404, "No such item in this batch.")
    code = item.barcode or item.scanned_code
    # Same set the Check step offered, siblings included — an open-box twin
    # usually has no barcode at all, so a barcode-only test would refuse the
    # very listing the operator was just shown and asked to choose.
    choices = _merge_siblings(
        session, item, products_by_barcode_all(code or "")
    )
    match = next(
        (
            p for p in choices
            if p.get("shopify_variant_id") == payload.shopify_variant_id
        ),
        None,
    )
    if match is None:
        raise HTTPException(
            404, "That listing isn't one of the alternatives for this item."
        )

    existing = next(
        (
            i for i in _batch_items(session, batch_id)
            if i.id != item.id and i.resolved and i.sku
            and i.sku.strip().upper()
            == (match.get("sku") or "").strip().upper()
        ),
        None,
    )
    if existing is not None:
        existing.qty_scanned += item.qty_scanned
        existing.paired_count += item.paired_count
        # An explicit human choice: the ambiguous flag stops re-raising.
        existing.listing_locked = True
        # The merged row keeps the EARLIER walking-order stamp.
        stamps = [t for t in (existing.first_scanned_at,
                              item.first_scanned_at) if t is not None]
        if stamps:
            existing.first_scanned_at = min(stamps)
        session.delete(item)
        session.commit()
        session.refresh(existing)
        return {"item": existing.as_dict(), "merged": True}

    item.resolved = True
    item.listing_locked = True
    item.shopify_variant_id = match.get("shopify_variant_id")
    item.shopify_product_id = match.get("shopify_product_id")
    item.product_title = match.get("product_title")
    item.variant_title = match.get("variant_title")
    item.sku = match.get("sku")
    item.barcode = match.get("barcode")
    item.bin_location = match.get("bin_location")
    item.image_url = (match.get("image_url") or "")[:500] or None
    item.expected_qty = _expected_qty(session, item.sku)
    session.commit()
    session.refresh(item)
    return {"item": item.as_dict(), "merged": False}


@app.post(
    "/api/batches/{batch_id}/items/{item_id}/resolve",
    dependencies=[Depends(require_user)],
)
def batch_item_resolve(
    batch_id: int, item_id: int, session: Session = Depends(get_session)
):
    """Look this row up in Shopify again, right now. The Check step's answer
    to "the product had no barcode set, so I set it in Shopify — now what":
    an unresolved row turns into a real product without re-scanning the
    boxes, and an already-resolved row refreshes its title/bin/count.

    Read-only as far as the store is concerned — nothing is written to
    Shopify here, so this needs no write gate."""
    batch = _get_batch(session, batch_id)
    item = session.get(BatchItem, item_id)
    if item is None or item.batch_id != batch_id:
        raise HTTPException(404, "No such item in this batch.")
    code = (item.scanned_code or item.barcode or item.sku or "").strip()
    if not code:
        raise HTTPException(422, "This row has no barcode or SKU to look up.")

    was_resolved = bool(item.resolved)
    product = None
    try:
        product = product_by_barcode(code)
    except HTTPException as error:
        if error.status_code != 404:
            raise

    if product is None:
        # Not a failure — the operator asked a question and the answer is
        # "still nothing". Shopify's search index trails an edit by a few
        # seconds, so trying again shortly is genuinely worth suggesting.
        return {
            "resolved": False,
            "merged": False,
            "was_resolved": was_resolved,
            "item": item.as_dict(),
            "message": (
                f"Shopify still has no product with barcode or SKU {code}. "
                "If you just changed it there, give it a few seconds and try "
                "again — the store's search takes a moment to catch up."
            ),
        }

    # Its other boxes may already have scanned fine under the real product:
    # merge into that row instead of leaving two rows for one product.
    sku = product.get("sku")
    existing = next(
        (
            i for i in _batch_items(session, batch_id)
            if i.id != item.id and i.resolved and sku and i.sku == sku
        ),
        None,
    )
    title = product.get("product_title") or sku or code
    recv = _is_receiving(batch)
    if existing is not None:
        moved = item.qty_scanned
        existing.qty_scanned += item.qty_scanned
        existing.paired_count += item.paired_count
        if recv and item.expected_qty:
            # Receiving rows keep the PLANNER's number in expected_qty -
            # merging a fixed problem row folds its share in too.
            existing.expected_qty = (
                (existing.expected_qty or 0) + item.expected_qty
            )
        stamps = [t for t in (existing.first_scanned_at,
                              item.first_scanned_at) if t is not None]
        if stamps:
            existing.first_scanned_at = min(stamps)
        session.delete(item)
        queued = _queue_receiving_labels_after_fix(session, batch) if recv \
            else 0
        session.commit()
        session.refresh(existing)
        return {
            "resolved": True,
            "merged": True,
            "was_resolved": was_resolved,
            "queued": queued,
            "item": existing.as_dict(),
            "message": (
                f"Resolved to {title} — its {moved} box(es) merged into the "
                f"row already in this batch ({existing.qty_scanned} total)."
                + (f" {queued} label(s) queued." if queued else "")
            ),
        }

    planner_expected = item.expected_qty if recv else None
    _apply_product_to_item(session, item, product, batch)
    queued = 0
    if recv:
        # A fixed problem row rejoins the shipment as a normal card: the
        # flag clears, the planner's number survives the product refresh
        # (apply stores the Shopify on-hand snapshot), and its boxes get
        # their labels right away - printing stays planner-driven.
        item.expected_qty = planner_expected
        item.skipped = False
        item.skip_reason = None
        if item.first_scanned_at is None:
            item.first_scanned_at = datetime.now(timezone.utc)
        queued = _queue_receiving_labels_after_fix(session, batch)
    session.commit()
    session.refresh(item)
    return {
        "resolved": True,
        "merged": False,
        "was_resolved": was_resolved,
        "queued": queued,
        "item": item.as_dict(),
        "message": (
            (f"Refreshed from Shopify ✓ — {title}."
             if was_resolved
             else f"Resolved to {title} ✓ — {item.qty_scanned} box(es) "
                  f"kept.")
            + (f" {queued} label(s) queued." if queued else "")
        ),
    }


def _queue_receiving_labels_after_fix(
    session: Session, batch: Batch
) -> int:
    """Queue whatever a just-fixed receiving row now needs. The builder
    only ever prints unlabelled boxes, so this is safe to run batch-wide."""
    jobs, _no_bin, _held_notes = _build_receiving_label_jobs(
        session, batch, batch.created_by
    )
    session.add_all(jobs)
    return len(jobs)


class ProductKindIn(BaseModel):
    # SKUs contain "+" and can contain "/" ("22451+81037+93575"), so the SKU
    # travels in the body — a path segment would need escaping to survive.
    sku: str = Field(max_length=100)
    # None clears the override and hands the product back to auto-detection.
    kind: Literal["multi_box", "bundle"] | None = None
    excluded: bool = False
    updated_by: str | None = Field(default=None, max_length=100)


@app.post("/api/product-kinds", dependencies=[Depends(require_user)])
def set_product_kind(
    payload: ProductKindIn, session: Session = Depends(get_session)
):
    """Set (or clear) the multi-box/bundle answer for a product outside any
    batch — this is the undo behind a 'dropped from the RFID system' event,
    reachable from History and the product panel."""
    sku = payload.sku.strip()
    if not sku:
        raise HTTPException(422, "Provide a SKU.")
    row = session.get(ProductKind, sku)

    if payload.kind is None:
        if row is not None:
            session.delete(row)
            session.commit()
        return {
            "sku": sku, "kind": None, "excluded": False,
            "message": f"{sku} is back to automatic detection.",
        }

    if payload.excluded and payload.kind != "bundle":
        raise HTTPException(
            422, "Only a bundle can be dropped from the RFID system."
        )
    if row is None:
        row = ProductKind(sku=sku, kind=payload.kind)
        session.add(row)
    row.kind = payload.kind
    row.excluded = payload.excluded
    row.updated_by = payload.updated_by
    row.updated_at = datetime.now(timezone.utc)
    session.commit()
    if payload.excluded:
        message = f"{sku} dropped from the RFID system."
    else:
        message = (
            f"{sku} is back in the RFID system"
            + (" as a bundle — it still won't be labelled."
               if payload.kind == "bundle"
               else " as a multi-box product.")
        )
    return {
        "sku": sku, "kind": row.kind, "excluded": row.excluded,
        "message": message,
    }


def _bundle_contents(session: Session, sku: str | None) -> list[dict]:
    if not sku or not sku.strip():
        return []
    return [
        r.as_dict() for r in session.scalars(
            select(BundleContent).where(
                func.upper(BundleContent.bundle_sku) == sku.strip().upper()
            ).order_by(BundleContent.id)
        )
    ]


class BundleContentsIn(BaseModel):
    """Replace a bundle's contents wholesale. The SKU travels in the body
    (bundle SKUs contain '+', which path segments mangle). An empty
    contents list clears the record — the bundle becomes countable
    again."""

    bundle_sku: str = Field(min_length=1, max_length=100)
    contents: list[dict] = Field(default_factory=list, max_length=20)
    updated_by: str | None = Field(default=None, max_length=100)


@app.get("/api/bundle-contents", dependencies=[Depends(require_user)])
def get_bundle_contents(
    sku: str, session: Session = Depends(get_session)
):
    return {"bundle_sku": sku, "contents": _bundle_contents(session, sku)}


@app.post(
    "/api/bundle-contents",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def set_bundle_contents(
    payload: BundleContentsIn, session: Session = Depends(get_session)
):
    sku = payload.bundle_sku.strip()
    rows = []
    for c in payload.contents:
        comp = str(c.get("component_sku") or "").strip()
        try:
            qty = int(c.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        if not comp or qty < 1:
            raise HTTPException(
                422, "Each content line needs a component SKU and a "
                     "quantity of at least 1."
            )
        rows.append((comp, qty))
    return _write_bundle_contents(session, sku, rows, payload.updated_by)


def _write_bundle_contents(
    session: Session,
    sku: str,
    rows: list[tuple[str, int]],
    updated_by: str | None,
) -> dict:
    """Replace a bundle's contents (shared by manual entry and the
    Shopify import): rewrites the rows, settles the product kind, and
    leaves the History receipt."""
    before = _bundle_contents(session, sku)
    for old in session.scalars(
        select(BundleContent).where(
            func.upper(BundleContent.bundle_sku) == sku.upper()
        )
    ):
        session.delete(old)
    for comp, qty in rows:
        session.add(BundleContent(
            bundle_sku=sku, component_sku=comp, qty=qty
        ))
    # A defined bundle IS a bundle — settle the kind question too, so the
    # Check step stops asking (the operator can still override later).
    if rows:
        pk = session.get(ProductKind, sku)
        if pk is None:
            session.add(ProductKind(sku=sku, kind="bundle",
                                    updated_by=updated_by))
        elif pk.kind != "bundle":
            pk.kind = "bundle"
            pk.updated_by = updated_by
            pk.updated_at = datetime.now(timezone.utc)
    fmt = lambda items: ", ".join(  # noqa: E731
        f"{i['qty']}× {i['component_sku']}" for i in items) or "(none)"
    session.add(BarcodeChange(
        sku=sku,
        changed_field="bundle-contents",
        old_barcode=fmt(before)[:64] or None,
        new_barcode=fmt([{"component_sku": c, "qty": q}
                         for c, q in rows])[:64],
        changed_by=updated_by,
    ))
    session.commit()
    return {
        "bundle_sku": sku,
        "contents": _bundle_contents(session, sku),
        "message": (
            f"{sku} = {fmt([{'component_sku': c, 'qty': q} for c, q in rows])}"
            + " — batch collect now counts the components instead."
            if rows else f"{sku} contents cleared — countable again."
        ),
    }


class BundleImportIn(BaseModel):
    """Pull a bundle's components straight from Shopify (the Shopify
    Bundles / Bundles.app relationship) instead of typing them."""

    sku: str = Field(min_length=1, max_length=100)
    updated_by: str | None = Field(default=None, max_length=100)


@app.post(
    "/api/bundle-contents/import",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def import_bundle_contents(
    payload: BundleImportIn, session: Session = Depends(get_session)
):
    if config.check_shopify_env():
        raise HTTPException(500, "Shopify credentials are not configured.")
    sku = payload.sku.strip()
    try:
        product = _lookup_api(sku)
    except RuntimeError as error:
        raise HTTPException(502, f"Shopify lookup failed: {error}")
    if product is None or not product.get("shopify_variant_id"):
        raise HTTPException(404, f"No Shopify product found for {sku}.")
    try:
        components = shopify.get_bundle_components(
            product["shopify_variant_id"]
        )
    except RuntimeError as error:
        raise HTTPException(502, f"Bundle component fetch failed: {error}")
    if not components:
        raise HTTPException(
            404,
            f"Shopify holds no bundle components for {sku} — it isn't a "
            f"native bundle (or wasn't built with the Bundles app). "
            f"Define the contents by hand instead.",
        )
    return _write_bundle_contents(
        session,
        sku,
        [(c["component_sku"], c["qty"]) for c in components],
        payload.updated_by,
    )


class ItemKindIn(BaseModel):
    kind: Literal["multi_box", "bundle"]
    # Bundles only: drop this product out of the RFID system altogether.
    excluded: bool = False
    updated_by: str | None = Field(default=None, max_length=100)


@app.post(
    "/api/batches/{batch_id}/items/{item_id}/kind",
    dependencies=[Depends(require_user)],
)
def set_item_kind(
    batch_id: int,
    item_id: int,
    payload: ItemKindIn,
    session: Session = Depends(get_session),
):
    """Say whether a listing that fills several box slots is ONE product in
    several boxes or a BUNDLE of separate products. Saved against the SKU,
    so every later batch already knows.

    A bundle has no box of its own — its components are tagged as
    themselves — so it queues no labels. `excluded` goes further and keeps
    it out of future batches entirely."""
    batch = _get_batch(session, batch_id)
    item = session.get(BatchItem, item_id)
    if item is None or item.batch_id != batch_id:
        raise HTTPException(404, "No such item in this batch.")
    if payload.excluded and payload.kind != "bundle":
        raise HTTPException(
            422, "Only a bundle can be dropped from the RFID system."
        )
    if payload.kind == "bundle" and item.paired_count:
        raise HTTPException(
            409,
            f"{item.paired_count} RFID tag(s) are already paired to this "
            f"row. Unpair them first — marking it a bundle would leave "
            f"tags pointing at something with no box to be on.",
        )

    # Remembered per SKU; without one there is nothing to key on, so the
    # answer can only apply to this row.
    if item.sku:
        saved = session.get(ProductKind, item.sku)
        if saved is None:
            saved = ProductKind(sku=item.sku, kind=payload.kind)
            session.add(saved)
        saved.kind = payload.kind
        saved.excluded = payload.excluded
        saved.updated_by = payload.updated_by or batch.created_by
        saved.updated_at = datetime.now(timezone.utc)

    # Same product may have several rows in this batch (a rescued unresolved
    # scan, say) — they all describe the same physical thing.
    rows = [
        i for i in _batch_items(session, batch_id)
        if i.id == item.id or (item.sku and i.sku == item.sku)
    ]
    for row in rows:
        row.kind = payload.kind

    removed = False
    if payload.excluded:
        for row in rows:
            if not row.paired_count:
                session.delete(row)
                removed = True

    session.commit()
    name = item.product_title or item.sku or item.scanned_code
    if payload.excluded:
        message = (
            f"{name} dropped from the RFID system — it won't be seeded into "
            f"future batches or labelled. Undo it from the product's panel "
            f"in History."
        )
    elif payload.kind == "bundle":
        message = (
            f"{name} marked as a bundle — no labels will print for it; its "
            f"component products get tagged as themselves."
        )
    else:
        message = (
            f"{name} marked as a multi-box product — one label per box, as "
            f"scanned."
        )
    return {
        "kind": payload.kind,
        "excluded": payload.excluded,
        "removed": removed,
        "item": None if removed else item.as_dict(),
        "message": message,
    }


class SplitPartIn(BaseModel):
    shopify_variant_id: str = Field(max_length=64)
    qty: int = Field(ge=0, le=500)


class SplitIn(BaseModel):
    parts: list[SplitPartIn] = Field(min_length=2, max_length=10)


@app.post(
    "/api/batches/{batch_id}/items/{item_id}/split",
    dependencies=[Depends(require_user)],
)
def batch_item_split(
    batch_id: int,
    item_id: int,
    payload: SplitIn,
    session: Session = Depends(get_session),
):
    """One scanned pile, several listings: two 94216 boxes share a barcode
    but one is the open-box listing. Reassign moves the WHOLE count; this
    divides it — each candidate gets its share, and the shares must add up
    to exactly what was scanned, so a box can't be lost or invented in the
    shuffle.

    Refused once tags are paired: the tags were tied to ONE listing, and
    splitting under them would leave tags asserting the wrong product."""
    batch = _get_batch(session, batch_id)
    item = session.get(BatchItem, item_id)
    if item is None or item.batch_id != batch_id:
        raise HTTPException(404, "No such item in this batch.")
    if not item.resolved:
        raise HTTPException(422, "That row never resolved to a product.")
    if item.paired_count:
        raise HTTPException(
            409,
            f"{item.paired_count} tag(s) are already paired to this row — "
            f"undo the pairing first, then split.",
        )
    if item.case_count:
        raise HTTPException(
            409,
            "This row holds sealed cases. Open or re-scan them first — a "
            "case can't be split between listings.",
        )
    total = sum(p.qty for p in payload.parts)
    if total != item.qty_scanned:
        raise HTTPException(
            422,
            f"The split adds up to {total}, but {item.qty_scanned} box(es) "
            f"were scanned. Every box has to land somewhere.",
        )
    seen_variants = {p.shopify_variant_id for p in payload.parts}
    if len(seen_variants) != len(payload.parts):
        raise HTTPException(422, "The same listing appears twice.")

    code = item.barcode or item.scanned_code
    choices = _merge_siblings(
        session, item, products_by_barcode_all(code or "")
    )
    by_variant = {c.get("shopify_variant_id"): c for c in choices}
    for p in payload.parts:
        if p.shopify_variant_id not in by_variant:
            raise HTTPException(
                404,
                "One of those listings isn't an alternative for this item.",
            )

    rows = []
    for p in payload.parts:
        match = by_variant[p.shopify_variant_id]
        if p.shopify_variant_id == item.shopify_variant_id:
            # The original keeps its row (and its label-name override);
            # only the count changes. qty 0 is allowed — it then reads as
            # an untouched seeded row, which is exactly what it is.
            item.qty_scanned = p.qty
            rows.append(item)
            continue
        existing = next(
            (
                i for i in _batch_items(session, batch_id)
                if i.id != item.id and i.resolved and i.sku
                and i.sku == match.get("sku")
            ),
            None,
        )
        if existing is not None:
            existing.qty_scanned += p.qty
            rows.append(existing)
            continue
        if p.qty == 0:
            continue    # don't create empty rows for unpicked listings
        row = BatchItem(
            batch_id=batch.id,
            scanned_code=(match.get("barcode")
                          or match.get("sku") or "")[:64],
            resolved=True,
            shopify_variant_id=match.get("shopify_variant_id"),
            shopify_product_id=match.get("shopify_product_id"),
            product_title=match.get("product_title"),
            variant_title=match.get("variant_title"),
            sku=match.get("sku"),
            barcode=match.get("barcode"),
            bin_location=match.get("bin_location"),
            image_url=(match.get("image_url") or "")[:500] or None,
            qty_scanned=p.qty,
            expected_qty=_expected_qty(session, match.get("sku")),
            # Split rows share the original's walking-order slot.
            first_scanned_at=item.first_scanned_at,
        )
        session.add(row)
        rows.append(row)
    session.commit()
    for r in rows:
        session.refresh(r)
    summary = ", ".join(
        f"{r.qty_scanned} × {r.sku or r.product_title}" for r in rows
        if r.qty_scanned
    )
    return {
        "items": [r.as_dict() for r in rows],
        "message": f"Split ✓ — {summary}.",
    }


class ItemSkipIn(BaseModel):
    skipped: bool = True
    reason: str | None = Field(default=None, max_length=120)


@app.post(
    "/api/batches/{batch_id}/items/{item_id}/skip",
    dependencies=[Depends(require_user)],
)
def set_item_skipped(
    batch_id: int,
    item_id: int,
    payload: ItemSkipIn,
    session: Session = Depends(get_session),
):
    """Mark a product as one you can't do on this pass — no barcode, wrapped
    beyond identifying, damaged label. The row stays with its reason so the
    shelf's story survives; it just queues no label and holds nothing up.

    Nothing here writes a quantity. Not to Shopify, not locally: the scanned
    count is left exactly as found (usually zero, meaning 'not counted'),
    because 'I couldn't check this' and 'there are none' are different
    facts and only one of them is true. Completing the batch raises a
    review task instead, so it comes back to a human."""
    _get_batch(session, batch_id)
    item = session.get(BatchItem, item_id)
    if item is None or item.batch_id != batch_id:
        raise HTTPException(404, "No such item in this batch.")
    if payload.skipped and item.paired_count:
        raise HTTPException(
            409,
            f"{item.paired_count} tag(s) are already paired to this row, so "
            f"it isn't unfinished. Undo the pairing first if you really "
            f"mean to skip it.",
        )
    item.skipped = payload.skipped
    item.skip_reason = (
        ((payload.reason or "").strip() or None) if payload.skipped else None
    )
    session.commit()
    session.refresh(item)
    name = item.product_title or item.sku or item.scanned_code
    return {
        "item": item.as_dict(),
        "message": (
            f"{name} skipped — no label, and it won't hold up the batch. "
            f"Counts are untouched; it'll come back as a review task."
            if payload.skipped
            else f"{name} is back in the batch."
        ),
    }


class ItemQtyIn(BaseModel):
    qty: int = Field(ge=0, le=500)


@app.post(
    "/api/batches/{batch_id}/items/{item_id}/qty",
    dependencies=[Depends(require_user)],
)
def set_item_qty(
    batch_id: int,
    item_id: int,
    payload: ItemQtyIn,
    session: Session = Depends(get_session),
):
    batch = _get_batch(session, batch_id)
    item = session.get(BatchItem, item_id)
    if item is None or item.batch_id != batch_id:
        raise HTTPException(404, "No such item in this batch.")
    item.qty_scanned = payload.qty
    # A manual bump from zero counts as this row's first contact too.
    if payload.qty > 0 and item.first_scanned_at is None:
        item.first_scanned_at = datetime.now(timezone.utc)
    # A count lowered to what's actually tagged can be the last missing
    # piece of a receiving shipment - check, same as after a pair.
    session.flush()
    receiving_done = _maybe_close_receiving(session, batch)
    session.commit()
    d = item.as_dict()
    d["receiving_done"] = receiving_done
    return d


class ItemLabelIn(BaseModel):
    label_name: str = Field(max_length=255)

    @field_validator("label_name")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


@app.put(
    "/api/batches/{batch_id}/items/{item_id}/label",
    dependencies=[Depends(require_user)],
)
def set_item_label(
    batch_id: int,
    item_id: int,
    payload: ItemLabelIn,
    session: Session = Depends(get_session),
):
    _get_batch(session, batch_id)
    item = session.get(BatchItem, item_id)
    if item is None or item.batch_id != batch_id:
        raise HTTPException(404, "No such item in this batch.")
    item.label_name = payload.label_name
    # Serialized brands: saving here confirms the name exactly like the
    # Scan Station's Save button, so future scans auto-print with it.
    if item.serial_prefix:
        sp = session.get(SerialPrefix, item.serial_prefix)
        if sp is not None:
            sp.label_name = payload.label_name
    session.commit()
    return item.as_dict()


class BatchQueueIn(BaseModel):
    requested_by: str | None = Field(default=None, max_length=100)


def _label_name_for(session: Session, item: BatchItem) -> tuple:
    """(name, placement, sku_text) for one batch item, in order: the serial
    brand's confirmed name, the product's saved label name, then the item's
    own override. (None, "header", None) = store header + SKU. sku_text is
    only ever set from a saved name whose two lines were customized with
    DIFFERENT text — placement alone can't express that."""
    if item.serial_prefix:
        sp = session.get(SerialPrefix, item.serial_prefix)
        if sp is not None and sp.label_name:
            return sp.label_name, "header", None
    if item.sku:
        custom = session.get(LabelName, item.sku)
        if custom is not None:
            return (
                custom.label_name,
                custom.placement or "header",
                custom.sku_text,
            )
    if item.label_name and item.label_name != item.sku:
        return item.label_name, "header", None
    return None, "header", None


class ItemLabelsIn(BaseModel):
    quantity: int = Field(default=1, ge=1, le=50)
    requested_by: str | None = Field(default=None, max_length=100)


@app.post(
    "/api/batches/{batch_id}/items/{item_id}/labels",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def batch_item_labels(
    batch_id: int,
    item_id: int,
    payload: ItemLabelsIn,
    session: Session = Depends(get_session),
):
    """Print labels for ONE product in the batch — a damaged sticker or a
    box that turned up late shouldn't mean reprinting the whole bin. Same
    label content as the batch run; the batch's status is untouched."""
    batch = _get_batch(session, batch_id)
    item = session.get(BatchItem, item_id)
    if item is None or item.batch_id != batch_id:
        raise HTTPException(404, "No such item in this batch.")
    if not item.resolved or not item.shopify_variant_id:
        raise HTTPException(
            422, "That row never resolved to a product, so there's nothing "
                 "to put on a label."
        )
    if item.kind == "bundle":
        raise HTTPException(
            422,
            "This is marked as a bundle — it has no box of its own to put a "
            "tag on. Print the label from one of its component products, or "
            "switch it to 'multi-box product' if that's wrong.",
        )
    # Receiving labels are shelving instructions: they carry the ITEM's
    # home bin, never the RECEIVING sentinel (same rule as the batch-wide
    # receiving print pass). Flagged problem rows never print - the flag
    # says why.
    if _is_receiving(batch):
        if item.skip_reason:
            raise HTTPException(
                422, f"This row is flagged, no labels print for it: "
                     f"{item.skip_reason}",
            )
        label_bin = (item.bin_location or "").strip()
        if not label_bin or label_bin.lower() == "no bin assigned":
            raise HTTPException(
                422,
                "This product has no bin assigned yet, so its label can't "
                "say where the box goes. Set a bin first (product preview "
                "> bin chip), then print.",
            )
    else:
        label_bin = batch.bin_name
    label_name, placement, label_sku = _label_name_for(session, item)
    jobs = [
        PrintJob(
            epc=_new_epc(),
            status="pending",
            batch_id=batch.id,
            shopify_variant_id=item.shopify_variant_id,
            shopify_product_id=item.shopify_product_id,
            product_title=item.product_title or "",
            variant_title=item.variant_title,
            sku=item.sku,
            barcode=item.barcode,
            bin_location=label_bin,
            other_bins=item.other_bins,
            label_name=label_name,
            label_placement=placement,
            label_sku=label_sku,
            requested_by=payload.requested_by or batch.created_by,
        )
        for _ in range(payload.quantity)
    ]
    jobs = _expand_multibox(session, jobs)
    session.add_all(jobs)
    session.commit()
    return {"count": len(jobs), "item": item.as_dict()}


@app.post(
    "/api/batches/{batch_id}/queue-labels",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def batch_queue_labels(
    batch_id: int,
    payload: BatchQueueIn,
    session: Session = Depends(get_session),
):
    """One print job per scanned box. Labels carry the BATCH bin — that's
    where the boxes physically are. Only from 'collecting' so a double-click
    can't queue the whole batch twice; single reprints live in Print Queue.

    Receiving batches loop collect → PRINT → pair per pass instead: PRINT
    is repeatable and queues only boxes not yet labelled, each label carries
    the ITEM's home bin (that's where the box is going), and items with no
    bin are held out and named so the operator can assign one first."""
    batch = _get_batch(session, batch_id)
    if _is_receiving(batch):
        if batch.status in ("done", "abandoned"):
            raise HTTPException(409, f"This batch is {batch.status}.")
        jobs, skipped_no_bin, held_notes = _build_receiving_label_jobs(
            session, batch, payload.requested_by
        )
        if not jobs and not skipped_no_bin and not held_notes:
            raise HTTPException(
                422, "Nothing new to label — every scanned box already "
                     "has a label queued or printed.",
            )
        session.add_all(jobs)
        session.commit()
        return {
            "count": len(jobs),
            "batch": batch.as_dict(),
            "skipped_bundles": [],
            # Held out, not silently dropped: these need a bin before a
            # label can tell the shelver where the box goes.
            "skipped_no_bin": skipped_no_bin,
            # Products covered by unused labels on a vendor strip - take
            # those instead of printing (Nick, 2026-09-01).
            "held_notes": held_notes,
        }
    if batch.status != "collecting":
        raise HTTPException(
            409,
            f"Labels were already queued for this batch (status "
            f"{batch.status}). Reprint individual labels from Print Queue.",
        )
    jobs, skipped_bundles = _build_label_jobs(
        session, batch, payload.requested_by
    )
    if not jobs:
        if skipped_bundles:
            raise HTTPException(
                422,
                "Everything scanned here is marked as a bundle, and bundles "
                "aren't labelled — their component products are tagged "
                "instead. Switch one to 'multi-box product' if that's wrong.",
            )
        raise HTTPException(422, "No resolved products with boxes to label.")
    session.add_all(jobs)
    batch.status = "printing"
    session.commit()
    return {
        "count": len(jobs),
        "batch": batch.as_dict(),
        # Named, not silently dropped: skipping a label is exactly the kind
        # of thing that should never be a surprise at the printer.
        "skipped_bundles": skipped_bundles,
    }


def _items_in_scan_order(
    session: Session, batch_id: int
) -> list[BatchItem]:
    """Batch items in the operator's WALKING order: first-scanned first,
    never-scanned (pre-seeded, record-only) rows last in id order. The
    printed label stack then matches the shelf walk instead of the seeded
    alphabetical order (Nick, 2026-08-25: collect A..H, labels came out
    G, F, A, B, D, H, C, E)."""
    items = _batch_items(session, batch_id)
    return sorted(items, key=lambda i: (
        i.first_scanned_at is None,
        orders_sync._as_utc(i.first_scanned_at) if i.first_scanned_at
        else None,
        i.id,
    ))


class BatchReprintAllIn(BaseModel):
    requested_by: str | None = Field(default=None, max_length=100)
    # The operator confirmed the printed strip is binned - a voided
    # label left on a box would answer sweeps as an unknown tag.
    confirmed: bool = False


@app.post(
    "/api/batches/{batch_id}/reprint-all",
    dependencies=[Depends(require_user)],
)
def batch_reprint_all(
    batch_id: int,
    payload: BatchReprintAllIn,
    session: Session = Depends(get_session),
):
    """Void EVERY label queued for this batch and queue a fresh full set
    in the same order (Nick, 2026-08-25: the printer ran out of wax
    mid-run, printed 46 blanks, and believed all 46 succeeded - the
    per-label reprint would have meant 46 clicks). Completed jobs auto-
    created tag assignments for their EPCs; those die with the blank
    labels, so no ghost tags stay on file. Only at the Print step and
    only before any pairing - voiding a paired tag would break the pair."""
    batch = _get_batch(session, batch_id)
    if _is_receiving(batch):
        raise HTTPException(
            422,
            "Receiving batches re-queue unlabelled boxes with the normal "
            "PRINT button instead - it only prints what isn't labelled yet.",
        )
    if batch.status != "printing":
        raise HTTPException(
            409,
            f"This batch is at '{batch.status}', not the Print step - "
            "reprint individual labels from the Print queue instead.",
        )
    if any(i.paired_count for i in _batch_items(session, batch_id)):
        raise HTTPException(
            409,
            "Some tags are already paired - voiding their labels would "
            "break the pairs. Reprint individual labels from the Print "
            "queue instead.",
        )
    if not payload.confirmed:
        raise HTTPException(
            409,
            "Confirm first: the whole printed strip must go in the bin - "
            "a voided label applied to a box would answer sweeps as an "
            "unknown tag.",
        )

    old_jobs = session.scalars(
        select(PrintJob).where(
            PrintJob.batch_id == batch.id,
            PrintJob.status.in_(("pending", "printing", "done", "error")),
        ).order_by(PrintJob.id)
    ).all()
    if not old_jobs:
        raise HTTPException(422, "No labels were ever queued for this batch.")
    return _void_and_requeue(
        session, batch, old_jobs, payload.requested_by
    )


def _void_and_requeue(
    session: Session,
    batch: Batch,
    old_jobs: list[PrintJob],
    requested_by: str | None,
) -> dict:
    """Void the given jobs (their blank labels' auto-created tag records
    die with them) and queue fresh clones in the same order. Shared by
    the whole-batch reprint and the pick-specific-labels reprint."""
    fresh: list[PrintJob] = []
    unlinked = 0
    for job in old_jobs:
        job.status = "canceled" if job.status == "pending" else "voided"
        if job.kind == "companion":
            # A companion label's tag record lives in the companion
            # registry, not in assignments.
            c = session.scalar(
                select(CompanionTag).where(
                    func.upper(CompanionTag.epc)
                    == (job.epc or "").strip().upper()
                )
            )
            if c is not None:
                session.delete(c)
                unlinked += 1
        else:
            a = session.scalar(
                select(RfidAssignment).where(
                    func.upper(RfidAssignment.rfid_id)
                    == (job.epc or "").strip().upper()
                )
            )
            if a is not None:
                session.delete(a)
                unlinked += 1
        fresh.append(PrintJob(
            epc=_new_epc(),
            status="pending",
            batch_id=batch.id,
            shopify_variant_id=job.shopify_variant_id,
            shopify_product_id=job.shopify_product_id,
            product_title=job.product_title,
            variant_title=job.variant_title,
            sku=job.sku,
            barcode=job.barcode,
            bin_location=job.bin_location,
            other_bins=job.other_bins,
            label_name=job.label_name,
            label_placement=job.label_placement,
            label_sku=job.label_sku,
            case_units=job.case_units,
            kind=job.kind,
            printer=job.printer,
            requested_by=requested_by or job.requested_by,
        ))
    session.add_all(fresh)
    session.add(BarcodeChange(
        product_title=f"Batch {batch.id} · bin {batch.bin_name}",
        changed_field="batch-reprint",
        old_barcode=f"{len(old_jobs)} label(s) voided"[:64],
        new_barcode=f"{len(fresh)} reprinted"[:64],
        changed_by=requested_by,
    ))
    session.commit()
    return {
        "voided": len(old_jobs),
        "queued": len(fresh),
        "tags_unlinked": unlinked,
        "message": (
            f"{len(old_jobs)} label(s) voided"
            + (f", {unlinked} blank-label tag record(s) unlinked"
               if unlinked else "")
            + f" - {len(fresh)} fresh label(s) queued in the same order. "
            "Bin the old copies."
        ),
    }


class BatchReprintJobsIn(BaseModel):
    job_ids: list[int] = Field(min_length=1, max_length=500)
    requested_by: str | None = Field(default=None, max_length=100)
    confirmed: bool = False


@app.post(
    "/api/batches/{batch_id}/reprint-jobs",
    dependencies=[Depends(require_user)],
)
def batch_reprint_jobs(
    batch_id: int,
    payload: BatchReprintJobsIn,
    session: Session = Depends(get_session),
):
    """Reprint SPECIFIC labels from this batch's run (Nick, 2026-08-25:
    the printer ran out of labels mid-run, and separately debris on the
    stock ruined a few prints - neither calls for voiding all 46). Same
    void-and-requeue as reprint-all, but only for the picked jobs; the
    rest of the run is untouched."""
    batch = _get_batch(session, batch_id)
    if batch.status != "printing":
        raise HTTPException(
            409,
            f"This batch is at '{batch.status}', not the Print step - "
            "reprint individual labels from the Print queue instead.",
        )
    if any(i.paired_count for i in _batch_items(session, batch_id)):
        raise HTTPException(
            409,
            "Some tags are already paired - voiding their labels would "
            "break the pairs. Reprint individual labels from the Print "
            "queue instead.",
        )
    if not payload.confirmed:
        raise HTTPException(
            409,
            "Confirm first: the picked labels must go in the bin - a "
            "voided label applied to a box would answer sweeps as an "
            "unknown tag.",
        )
    jobs = session.scalars(
        select(PrintJob).where(
            PrintJob.batch_id == batch.id,
            PrintJob.id.in_(payload.job_ids),
            PrintJob.status.in_(("pending", "printing", "done", "error")),
        ).order_by(PrintJob.id)
    ).all()
    if not jobs:
        raise HTTPException(
            422,
            "None of those labels belong to this batch (or they were "
            "already voided).",
        )
    return _void_and_requeue(session, batch, jobs, payload.requested_by)


def _build_label_jobs(
    session: Session, batch: Batch, requested_by: str | None
) -> tuple[list[PrintJob], list[str]]:
    """Print jobs for every labelable box in a batch. Shared by the normal
    label run and by a side trip, so a stray carried to its real shelf gets
    exactly the label it would have got had it been found there."""
    jobs: list[PrintJob] = []
    skipped_bundles: list[str] = []
    for item in _items_in_scan_order(session, batch.id):
        if not item.resolved or not item.shopify_variant_id:
            continue
        # Couldn't be identified on this pass — there is nothing to put a
        # label on.
        if item.skipped:
            continue
        # A bundle is an inventory construct, not a box: its components are
        # tagged as themselves, so labelling it would put a second tag on a
        # box that already has one.
        if item.kind == "bundle":
            if item.qty_scanned:
                skipped_bundles.append(item.product_title or item.sku or "?")
            continue
        label_name, label_placement, label_sku = _label_name_for(
            session, item
        )
        # One label per loose box, plus one per sealed case. The case labels
        # carry their unit count so the sticker reads "8 x 93581" and nobody
        # mistakes the box for a single item.
        per_label_units = (
            [None] * item.qty_scanned
            + [item.case_units] * item.case_count
        )
        for units in per_label_units:
            jobs.append(
                PrintJob(
                    epc=_new_epc(),
                    status="pending",
                    case_units=units,
                    batch_id=batch.id,
                    shopify_variant_id=item.shopify_variant_id,
                    shopify_product_id=item.shopify_product_id,
                    product_title=item.product_title or "",
                    variant_title=item.variant_title,
                    sku=item.sku,
                    barcode=item.barcode,
                    bin_location=batch.bin_name,
                    # Split-shelf products print where their other boxes
                    # are, so a picker isn't left hunting.
                    other_bins=item.other_bins,
                    # Store header + SKU unless a preferred name exists:
                    # the batch item's own override first, else the
                    # product's saved label name (set in Check / History).
                    label_name=label_name,
                    label_placement=label_placement,
                    label_sku=label_sku,
                    requested_by=requested_by or batch.created_by,
                )
            )
    return _expand_multibox(session, jobs), skipped_bundles


def _held_available(session: Session, skus: list[str]) -> dict[str, dict]:
    """Unused held labels per SKU, across every vendor strip: label(s)
    printed for an order the product never actually shipped with (Nick,
    2026-09-01). {SKU: {"count": n, "where": "ZWO strip (SO 948)"}}."""
    wanted = {(s or "").strip().upper() for s in skus if s and s.strip()}
    if not wanted:
        return {}
    out: dict[str, dict] = {}
    lists = {
        hl.id: hl for hl in session.scalars(select(HeldLabelList))
    }
    for it in session.scalars(
        select(HeldLabelItem).where(
            HeldLabelItem.count > 0,
            func.upper(HeldLabelItem.sku).in_(sorted(wanted)),
        )
    ):
        key = it.sku.strip().upper()
        hl = lists.get(it.list_id)
        where = (
            f"{hl.vendor or 'vendor'} strip"
            + (f" ({hl.reference})" if hl and hl.reference else "")
        ) if hl else "held strip"
        d = out.setdefault(key, {"count": 0, "where": where})
        d["count"] += it.count
    return out


def _consume_held_label(session: Session, epc: str | None,
                        sku: str | None) -> None:
    """A pairing whose EPC came off a held strip: remove it from the
    pool and decrement its product's unused count - the label found its
    box at last. Fail-soft: bookkeeping must never break a pairing."""
    if not epc or not (epc or "").strip():
        return
    try:
        up = epc.strip().upper()
        for hl in session.scalars(select(HeldLabelList)):
            pool = hl.epc_set()
            if up not in pool:
                continue
            pool.discard(up)
            hl.epcs = "\n".join(sorted(pool))
            if sku:
                row = session.scalar(
                    select(HeldLabelItem).where(
                        HeldLabelItem.list_id == hl.id,
                        func.upper(HeldLabelItem.sku)
                        == sku.strip().upper(),
                        HeldLabelItem.count > 0,
                    )
                )
                if row is not None:
                    row.count -= 1
            return
    except Exception:  # noqa: BLE001
        logger.exception("held-label consume failed (%s)", epc)


def _item_box_slots(item: BatchItem) -> int:
    """Boxes per unit for a receiving row. A multi-box product's bin
    field lists ONE BIN PER BOX (Nick, 2026-09-02 - the 11740's
    "strange bin numbers"), so the slot count IS the carton count.
    Stays 1 until the per-box bins are actually set on the product."""
    if item.kind != "multi_box":
        return 1
    full = ", ".join(
        x for x in (item.bin_location, item.other_bins) if x
    )
    return max(1, len(parse_bin_parts(full)))


def _build_receiving_label_jobs(
    session: Session, batch: Batch, requested_by: str | None
) -> tuple[list[PrintJob], list[str], list[str]]:
    """Print jobs for a receiving pass: only boxes NOT yet labelled (the
    loop repeats PRINT per pass), each label carrying the ITEM's home bin —
    a received box's label is its shelving instruction. Items without a bin
    are held out and named so the operator can assign one and re-print;
    cancelled jobs free their box to be re-queued next pass.

    Held labels (Nick, 2026-09-01): a product with unused labels on a
    vendor strip prints that many FEWER - the third return value names
    each ("take 2 from the ZWO strip (SO 948)")."""
    jobs: list[PrintJob] = []
    skipped_no_bin: list[str] = []
    held_notes: list[str] = []
    items_all = _items_in_scan_order(session, batch.id)
    held = _held_available(session, [i.sku for i in items_all if i.sku])
    for item in items_all:
        if not item.resolved or not item.shopify_variant_id:
            continue
        if item.skipped or item.kind == "bundle":
            continue
        want = item.qty_scanned + item.case_count
        if want <= 0:
            continue
        have = session.scalar(
            select(func.count()).where(
                PrintJob.batch_id == batch.id,
                PrintJob.shopify_variant_id == item.shopify_variant_id,
                PrintJob.status.in_(("pending", "printing", "done")),
            )
        ) or 0
        delta = want - have
        if delta <= 0:
            continue
        h = held.get((item.sku or "").strip().upper())
        if h and h["count"] > 0:
            use = min(h["count"], delta)
            delta -= use
            # Reserve locally so two items of one SKU can't both claim
            # the same held label in one pass.
            h["count"] -= use
            held_notes.append(
                f"{item.sku}: take {use} label(s) from the {h['where']} "
                "instead of printing"
            )
            if delta <= 0:
                continue
        bin_ = (item.bin_location or "").strip()
        if not bin_ or bin_.lower() == "no bin assigned":
            skipped_no_bin.append(item.product_title or item.sku or "?")
            continue
        label_name, label_placement, label_sku = _label_name_for(
            session, item
        )
        # Multi-box products (the 11740): the row counts CARTONS, and
        # each carton's label names its own box and its own bin - "BOX
        # 2 OF 2" going to the second bin slot. Interleaved per unit so
        # the stack comes out unit by unit.
        slots = _item_box_slots(item)
        box_bins = parse_bins(", ".join(
            x for x in (item.bin_location, item.other_bins) if x
        )) if slots > 1 else []
        # Loose boxes label first, cases after — same order pairing walks.
        per_label_units = (
            [None] * item.qty_scanned + [item.case_units] * item.case_count
        )[have:]
        for idx, units in enumerate(per_label_units[:delta]):
            job_bin = bin_
            job_other = item.other_bins
            job_name, job_place, job_sku = (
                label_name, label_placement, label_sku
            )
            if slots > 1:
                box_no = ((have + idx) % slots) + 1
                job_name = f"BOX {box_no} OF {slots}"
                job_place = "header"
                job_sku = None
                if box_no - 1 < len(box_bins):
                    job_bin = box_bins[box_no - 1]
                job_other = None
            jobs.append(
                PrintJob(
                    epc=_new_epc(),
                    status="pending",
                    case_units=units,
                    batch_id=batch.id,
                    shopify_variant_id=item.shopify_variant_id,
                    shopify_product_id=item.shopify_product_id,
                    product_title=item.product_title or "",
                    variant_title=item.variant_title,
                    sku=item.sku,
                    barcode=item.barcode,
                    bin_location=job_bin,
                    other_bins=job_other,
                    label_name=job_name,
                    label_placement=job_place,
                    label_sku=job_sku,
                    requested_by=requested_by or batch.created_by,
                )
            )
    return _expand_multibox(session, jobs), skipped_no_bin, held_notes


class ReceivingPrintItemIn(BaseModel):
    sku: str = Field(max_length=100)
    quantity: int = Field(ge=1, le=500)
    barcode: str | None = Field(default=None, max_length=64)


class ReceivingPrintsIn(BaseModel):
    items: list[ReceivingPrintItemIn] = Field(min_length=1, max_length=200)
    requested_by: str | None = Field(default=None, max_length=100)
    # e.g. "SO 123" - shows on the batch and groups repeat saves of the
    # same stock order into the same receiving batch.
    reference: str | None = Field(default=None, max_length=60)


def _receiving_intake(
    session: Session, payload: ReceivingPrintsIn, queue_labels: bool,
    tag_prefix: str = "TC-Planner", merge_vendor: bool = True,
) -> dict:
    """Shared body of the planner bridge: create/reuse the stock order's
    receiving batch, add the received quantities to its rows (problem
    rows flagged, repeat saves folded) — and, when queue_labels, queue
    the not-yet-labelled boxes exactly like a receiving print pass. The
    no-labels path (the Update-stock safety net) books the SAME rows so
    the boxes are never lost; their labels queue later from the Review
    task. Caller commits.

    Full-shipment receives pass their own tag_prefix and merge_vendor
    False (Nick, 2026-09-01): their settle step reads the WHOLE batch as
    one order's story, so folding them into a same-vendor planner batch
    would count that batch's unpaired boxes as never-shipped."""
    ref = (payload.reference or "").strip()
    parts = [p.strip() for p in ref.split("·")] if ref else []
    vendor = parts[1] if len(parts) > 1 and parts[1] else None
    so_part = parts[0] if parts else ""
    batch = None
    if vendor and merge_vendor:
        # One receiving batch per VENDOR (Nick, 2026-08-31): same-vendor
        # stock orders arrive on one pallet, so their labels and pairing
        # belong together. The batch name keeps EVERY stock order it
        # covers - "TC-Planner · SO 1254, SO 1266 · ZWO".
        for cand in session.scalars(
            select(Batch).where(
                Batch.kind == "receiving",
                Batch.status.notin_(("done", "abandoned")),
                Batch.created_by.like(f"{tag_prefix} ·%"),
            ).order_by(Batch.id.desc())
        ):
            cparts = [p.strip() for p in (cand.created_by or "").split("·")]
            if (len(cparts) >= 3
                    and cparts[-1].upper() == vendor.upper()):
                batch = cand
                sos = [s.strip() for s in cparts[1].split(",")
                       if s.strip()]
                if so_part and so_part not in sos:
                    sos.append(so_part)
                    cand.created_by = (
                        f"{tag_prefix} · " + ", ".join(sos)
                        + " · " + cparts[-1]
                    )[:100]
                break
        tag = (batch.created_by if batch is not None
               else f"{tag_prefix} · " + ref)[:100]
    else:
        tag = (tag_prefix + (f" · {ref}" if ref else ""))[:100]
        batch = session.scalar(
            select(Batch).where(
                Batch.kind == "receiving",
                Batch.status.notin_(("done", "abandoned")),
                Batch.created_by == tag,
            ).order_by(Batch.id.desc())
        )
    if batch is None:
        batch = Batch(bin_name=RECEIVING_BIN, kind="receiving",
                      created_by=tag)
        session.add(batch)
        session.flush()

    no_tag = _non_taggable_skus(session)
    items = _batch_items(session, batch.id)
    added: list[dict] = []
    skipped_unknown: list[str] = []
    skipped_non_taggable: list[str] = []

    def problem_row(code: str, qty: int, reason: str,
                    product: dict | None = None) -> None:
        """Keep the failure ON the batch as a flagged row instead of only
        naming it in a response nobody re-reads (Nick, 2026-08-25: the
        receiving list must show what could not print, expandable to say
        why). Unknown items stay unresolved; known-but-unprintable ones
        keep their product info. Repeat saves reuse the row."""
        row = next(
            (i for i in items
             if (i.skip_reason or "")
             and (i.scanned_code or "").strip().upper()
             == (code or "").strip().upper()),
            None,
        )
        if row is None:
            row = BatchItem(
                batch_id=batch.id,
                scanned_code=(code or "?")[:64],
                resolved=False,
                qty_scanned=0,
            )
            if product is not None:
                _apply_product_to_item(session, row, product, batch)
                row.label_name = None
            # expected_qty tracks the PLANNER's number here, not the
            # Shopify on-hand snapshot _apply_product_to_item stores.
            row.expected_qty = 0
            session.add(row)
            items.append(row)
        row.qty_scanned += qty
        row.expected_qty = (row.expected_qty or 0) + qty
        row.skip_reason = reason[:120]
        row.skipped = True

    for entry in payload.items:
        product = None
        try:
            for term in (entry.sku, entry.barcode):
                if not (term or "").strip():
                    continue
                try:
                    product = product_by_barcode(term.strip())
                    break
                except HTTPException as error:
                    if error.status_code >= 500:
                        raise
            if product is None:
                skipped_unknown.append(entry.sku)
                problem_row(
                    entry.sku or entry.barcode or "?", entry.quantity,
                    "Not found: no product matches this SKU or barcode. "
                    "Fix it in Shopify or link the code at the Scan "
                    "Station, then reprint.",
                )
                continue
            sku_ci = (product.get("sku") or entry.sku).strip().upper()
            if sku_ci in no_tag:
                skipped_non_taggable.append(product.get("sku") or entry.sku)
                problem_row(
                    product.get("sku") or entry.sku, entry.quantity,
                    "Marked non-taggable: this product is kept out of the "
                    "RFID system, so no labels print for it.",
                    product,
                )
                continue
            item = next(
                (i for i in items if i.resolved and not i.skip_reason
                 and i.sku and i.sku.strip().upper() == sku_ci),
                None,
            )
            if item is None:
                item = BatchItem(
                    batch_id=batch.id,
                    scanned_code=(product.get("barcode")
                                  or product.get("sku") or "")[:64],
                    qty_scanned=0,
                )
                _apply_product_to_item(session, item, product, batch)
                # Receiving labels use the standard store header + SKU.
                item.label_name = None
                # The planner's number is the EXPECTED count, kept apart
                # from the received count so "Update count" can correct
                # the latter while the window still shows what the
                # planner said (Nick, 2026-08-25).
                item.expected_qty = 0
                session.add(item)
                items.append(item)
            elif item.expected_qty is None:
                item.expected_qty = 0
            # Multi-box products (Nick, 2026-09-02, the 11740): the bin
            # field lists one bin per CARTON, so re-resolve the kind
            # against the full bin string (the receiving row only kept
            # the first bin) and count cartons, not units - every box
            # gets its own label and tag, same as batch tagging.
            full_bin = ", ".join(
                x for x in (product.get("bin_location"),
                            product.get("other_bins")) if x
            )
            item.kind, _ = resolve_product_kind(
                session, item.product_title, item.sku, full_bin
            )
            if item.kind == "multi_box" and not item.other_bins:
                item.other_bins = (
                    (product.get("other_bins") or "")[:255] or None
                )
            boxes = entry.quantity * _item_box_slots(item)
            item.qty_scanned += boxes
            item.expected_qty += boxes
            if item.first_scanned_at is None:
                item.first_scanned_at = datetime.now(timezone.utc)
            added.append({
                "sku": item.sku,
                "quantity": entry.quantity,
                "boxes": boxes,
            })
        except HTTPException:
            raise
        except Exception as error:  # noqa: BLE001 - the row must survive
            # An unforeseen failure on ONE line must not eat the line (or
            # the save): it becomes a flagged row the list can explain.
            logger.exception("receiving item failed: %s", entry.sku)
            skipped_unknown.append(entry.sku)
            problem_row(
                entry.sku or entry.barcode or "?", entry.quantity,
                f"Could not process: {str(error)[:80]}",
            )
    session.flush()
    jobs: list[PrintJob] = []
    skipped_no_bin: list[str] = []
    held_notes: list[str] = []
    if queue_labels:
        jobs, skipped_no_bin, held_notes = _build_receiving_label_jobs(
            session, batch, payload.requested_by or tag
        )
        session.add_all(jobs)
    return {
        "batch": batch,
        "tag": tag,
        "added": added,
        "jobs": jobs,
        "skipped_no_bin": skipped_no_bin,
        "skipped_unknown": skipped_unknown,
        "skipped_non_taggable": skipped_non_taggable,
        "held_notes": held_notes,
    }


@app.post(
    "/api/receiving/prints",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def receiving_prints(
    payload: ReceivingPrintsIn, session: Session = Depends(get_session)
):
    """TC-Planner's "Print labels" button (Nick, 2026-08-25): the user
    marks stock-order items received over there and sends them here to
    print. Creates (or reuses, per reference) an open RECEIVING batch,
    adds the received quantities to its rows, and queues labels exactly
    like a receiving PRINT pass - only not-yet-labelled boxes, each label
    carrying the item's home bin, items without a bin held out and named.
    The warehouse then pairs tags in Batch tagging as usual. Nothing is
    written to Shopify here."""
    out = _receiving_intake(session, payload, queue_labels=True)
    batch, tag = out["batch"], out["tag"]
    jobs = out["jobs"]
    skipped_unknown = out["skipped_unknown"]
    skipped_non_taggable = out["skipped_non_taggable"]
    skipped_no_bin = out["skipped_no_bin"]
    session.commit()
    session.refresh(batch)
    return {
        "batch": batch.as_dict(),
        "added": out["added"],
        "queued": len(jobs),
        "skipped_no_bin": skipped_no_bin,
        "skipped_unknown": skipped_unknown,
        "skipped_non_taggable": skipped_non_taggable,
        "held_notes": out["held_notes"],
        "message": (
            f"{len(jobs)} label(s) queued on receiving batch {batch.id}"
            + (f" ({tag})" if payload.reference else "")
            + (f"; {len(skipped_unknown)} unknown SKU(s) skipped"
               if skipped_unknown else "")
            + (f"; {len(skipped_non_taggable)} non-taggable skipped"
               if skipped_non_taggable else "")
            + (f"; held for a bin: {', '.join(skipped_no_bin)}"
               if skipped_no_bin else "")
            + (f"; {'; '.join(out['held_notes'])}"
               if out["held_notes"] else "")
            + "."
        ),
    }


@app.post(
    "/api/receiving/unprinted",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def receiving_unprinted(
    payload: ReceivingPrintsIn, session: Session = Depends(get_session)
):
    """The Update-stock safety net (Nick, 2026-08-26): TC-Planner pushed
    stock to Shopify for these items WITHOUT printing their labels. The
    boxes still book into the stock order's receiving batch — same rows,
    same problem handling as Print labels — but no labels queue yet;
    instead ONE open Review task per batch tracks how many units are
    waiting, and resolving it queues the missing labels (mechanically
    identical to Print labels). Repeat pushes fold into the same task."""
    # An order received via Receive entire shipment already printed AND
    # paired everything - the planner's frontend just doesn't hold those
    # line-ids in its printed set, so it relays them as unprinted (Nick,
    # 2026-09-01, SO 946: spurious task + duplicate label-less batch).
    # The relay reference is "SO {planner order id} · vendor".
    so_part = (payload.reference or "").split("·")[0].strip()
    so_num = so_part.upper().removeprefix("SO").strip()
    if so_num.isdigit():
        receipt = session.scalar(
            select(OrderReceipt).where(
                OrderReceipt.stock_order_id == int(so_num)
            ).order_by(OrderReceipt.id.desc())
        )
        if receipt is not None:
            return {
                "batch": None,
                "added": [],
                "labels_waiting": 0,
                "task_id": None,
                "message": (
                    f"{so_part} was received via Receive entire "
                    "shipment - its labels are already printed and "
                    "paired, nothing is owed."
                ),
            }
    out = _receiving_intake(session, payload, queue_labels=False)
    batch, tag = out["batch"], out["tag"]
    session.flush()
    # What a print pass WOULD queue right now = the labels outstanding.
    would, would_no_bin, _would_held = _build_receiving_label_jobs(
        session, batch, payload.requested_by or tag
    )
    pushed_units = sum(a["quantity"] for a in out["added"])
    task = session.scalar(
        select(ReviewTask).where(
            ReviewTask.category == "labels-not-printed",
            ReviewTask.status == "open",
            ReviewTask.batch_id == batch.id,
        )
    )
    detail = (
        f"{tag}: stock was updated in Shopify without printing labels. "
        f"{len(would)} label(s) are waiting on receiving batch "
        f"#{batch.id}"
        + (f" (plus {len(would_no_bin)} product(s) held for a bin)"
           if would_no_bin else "")
        + (f"; {len(out['skipped_unknown'])} unknown SKU(s) flagged on "
           f"the batch" if out["skipped_unknown"] else "")
        + ". Resolve to queue them - the normal receiving print and "
        "pair flow takes over."
    )[:500]
    if task is None:
        task = ReviewTask(
            category="labels-not-printed",
            product_title=tag,
            detail=detail,
            batch_id=batch.id,
            created_by=payload.requested_by,
        )
        session.add(task)
    else:
        task.detail = detail
    session.flush()
    session.add(ReviewNote(
        task_key=str(task.id),
        note=(f"Stock push without labels: {pushed_units} unit(s) "
              f"across {len(out['added'])} product(s).")[:500],
        created_by=payload.requested_by,
    ))
    session.commit()
    session.refresh(batch)
    return {
        "batch": batch.as_dict(),
        "added": out["added"],
        "labels_waiting": len(would),
        "task_id": task.id,
        "skipped_unknown": out["skipped_unknown"],
        "skipped_non_taggable": out["skipped_non_taggable"],
        "message": (
            f"Booked {pushed_units} unit(s) on receiving batch "
            f"{batch.id} with NO labels queued - Review task #{task.id} "
            f"tracks the {len(would)} waiting label(s)."
        ),
    }


class QueueLabelsIn(BaseModel):
    changed_by: str | None = Field(default=None, max_length=100)


@app.post(
    "/api/review-tasks/{task_id}/queue-labels",
    dependencies=[Depends(require_user)],
)
def queue_missing_labels(
    task_id: int,
    payload: QueueLabelsIn,
    session: Session = Depends(get_session),
):
    """Resolution for the Update-stock safety net: queue every label the
    linked receiving batch is still owed - mechanically identical to the
    planner's Print labels pass (only unlabelled boxes, home bins on the
    labels, no-bin items held out and named) - then close the task."""
    task = session.get(ReviewTask, task_id)
    if task is None:
        raise HTTPException(404, "No such review task.")
    if task.category != "labels-not-printed":
        raise HTTPException(
            422, "Queue-labels is for the unprinted-stock safety net."
        )
    if task.status != "open":
        raise HTTPException(409, f"Task is already {task.status}.")
    batch = session.get(Batch, task.batch_id) if task.batch_id else None
    if batch is None or batch.status in ("done", "abandoned"):
        raise HTTPException(
            409,
            "The receiving batch behind this task is gone or closed - "
            "nothing left to print for it.",
        )
    by = (payload.changed_by or "").strip()[:100] or None
    jobs, skipped_no_bin, held_notes = _build_receiving_label_jobs(
        session, batch, by or batch.created_by
    )
    session.add_all(jobs)
    task.status = "resolved"
    task.resolved_by = by
    task.resolved_at = datetime.now(timezone.utc)
    task.resolution_note = (
        f"{len(jobs)} label(s) queued to receiving batch #{batch.id}."
        + (f" Held for a bin: {', '.join(skipped_no_bin)}."
           if skipped_no_bin else "")
        if jobs or skipped_no_bin
        else "Labels were already queued from the receiving list."
    )[:255]
    session.commit()
    return {
        "queued": len(jobs),
        "skipped_no_bin": skipped_no_bin,
        "batch_id": batch.id,
        "message": (
            f"{len(jobs)} label(s) queued on receiving batch {batch.id}"
            + (f"; held for a bin: {', '.join(skipped_no_bin)}"
               if skipped_no_bin else "")
            + ". The receiving list runs the normal print and pair flow."
            if jobs or skipped_no_bin
            else "Nothing was owed - labels were already queued; task "
                 "resolved."
        ),
    }


# --------------------------------------------- receive entire shipment ----
# Nick's 2026-09-01 flow: pull a whole TC-Planner stock order into ONE
# receiving batch, print every remaining label up front, pair what
# physically arrived, then sweep the strip of unused labels into a
# vendor container (a HELD list). The paired counts hand off to the
# planner pre-filled (Print labels grayed there - labels already
# exist); a Review task opens if Shopify stock isn't updated within an
# hour of the operator settling the batch.

@app.get("/api/receiving/orders", dependencies=[Depends(require_user)])
def receiving_open_orders(
    operator: str | None = None, session: Session = Depends(get_session)
):
    """Open stock orders for the picker, annotated with whether a
    full-shipment receive already printed them."""
    out = planner.open_orders(operator)
    if out.get("ok"):
        ids = [o["order_id"] for o in out["orders"] if o.get("order_id")]
        receipts = {
            r.stock_order_id: r
            for r in session.scalars(
                select(OrderReceipt).where(
                    OrderReceipt.stock_order_id.in_(ids or [0])
                )
            )
        }
        for o in out["orders"]:
            r = receipts.get(o.get("order_id"))
            o["already_printed"] = r is not None
            o["batch_id"] = r.batch_id if r else None
    return out


@app.get(
    "/api/receiving/orders/{order_ref}",
    dependencies=[Depends(require_user)],
)
def receiving_order_preview(
    order_ref: str,
    operator: str | None = None,
    session: Session = Depends(get_session),
):
    """One order's remaining lines, checked against the catalog: every
    line resolves to a product or gets a flag (unknown, non-taggable,
    bundle, no bin) - the confirm screen shows the whole story before a
    single label prints. Held labels per SKU ride along."""
    out = planner.order_lines(order_ref, operator)
    if not out.get("ok"):
        raise HTTPException(502, out.get("error")
                            or "The planner bridge isn't configured.")
    if not out.get("order"):
        raise HTTPException(
            404, f"No open stock order matches '{order_ref}'."
        )
    no_tag = _non_taggable_skus(session)
    held = _held_available(
        session, [i.get("sku") for i in out["items"]]
    )
    nicknames = _nickname_map(
        session, [i.get("sku") for i in out["items"]]
    )
    receipt = session.scalar(
        select(OrderReceipt).where(
            OrderReceipt.stock_order_id == out["order"]["order_id"]
        ).order_by(OrderReceipt.id.desc())
    )
    for line in out["items"]:
        line["flag"] = None
        line["nickname"] = nicknames.get(
            (line.get("sku") or "").strip().upper()
        )
        product = None
        for term in (line.get("sku"), line.get("barcode")):
            if not (term or "").strip():
                continue
            try:
                product = product_by_barcode(term.strip())
                break
            except HTTPException as error:
                if error.status_code >= 500:
                    raise
        if product is None:
            line["flag"] = "unknown - no product matches this SKU or " \
                           "barcode"
            continue
        sku_ci = (product.get("sku") or "").strip().upper()
        line["product_title"] = product.get("product_title")
        line["bin_location"] = product.get("bin_location")
        if sku_ci in no_tag:
            line["flag"] = "non-taggable - no labels print for it"
        else:
            kind, excluded = resolve_product_kind(
                session, product.get("product_title"),
                product.get("sku"), product.get("bin_location"),
            )
            if kind == "bundle" or excluded:
                line["flag"] = "bundle - components carry the tags"
            elif not (product.get("bin_location") or "").strip() or (
                product.get("bin_location") or ""
            ).strip().lower() == "no bin assigned":
                line["flag"] = "no home bin - assign one before printing"
        h = held.get(sku_ci)
        if h and h["count"] > 0:
            line["held"] = {"count": h["count"], "where": h["where"]}
    return {**out, "receipt": receipt.as_dict() if receipt else None}


class FullShipmentIn(BaseModel):
    order: str = Field(min_length=1, max_length=60)
    requested_by: str | None = Field(default=None, max_length=100)
    # Codes in the order the boxes were scanned (the shipment sorter's
    # pass): labels print in this order so the stack matches the pallet
    # walk (Nick, 2026-09-02). Lines not in the list print after, in
    # order-line order.
    scan_order: list[str] | None = Field(default=None, max_length=400)


@app.post(
    "/api/receiving/full-shipment",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def receiving_full_shipment(
    payload: FullShipmentIn, session: Session = Depends(get_session)
):
    """Load the whole stock order (remaining quantities) into a
    receiving batch and print every label up front. Re-running for the
    same order REUSES the live batch instead of creating a twin; a
    settled/done full-shipment receive refuses with its history."""
    out = planner.order_lines(payload.order, payload.requested_by)
    if not out.get("ok"):
        raise HTTPException(502, out.get("error")
                            or "The planner bridge isn't configured.")
    order = out.get("order")
    if not order:
        raise HTTPException(
            404, f"No open stock order matches '{payload.order}'."
        )
    receipt = session.scalar(
        select(OrderReceipt).where(
            OrderReceipt.stock_order_id == order["order_id"]
        ).order_by(OrderReceipt.id.desc())
    )
    if receipt is not None:
        prior = session.get(Batch, receipt.batch_id)
        if prior is not None and prior.status not in ("done", "abandoned"):
            return {
                "reused": True,
                "batch": prior.as_dict(),
                "receipt": receipt.as_dict(),
                "queued": 0,
                "held_notes": [],
                "skipped_no_bin": [],
                "skipped_unknown": [],
                "skipped_non_taggable": [],
                "message": (
                    f"SO {order['reference_number']} already has a live "
                    f"full-shipment batch (#{prior.id}) - picking it up "
                    "where it left off."
                ),
            }
        if prior is not None and prior.status == "done":
            raise HTTPException(
                409,
                f"SO {order['reference_number']} was already received "
                f"via Receive entire shipment (batch #{prior.id}, done). "
                "Use the planner's normal Print labels flow for anything "
                "extra.",
            )
    if not out["items"]:
        raise HTTPException(
            422,
            f"SO {order['reference_number']} has nothing left to "
            "receive - every line is already fully received.",
        )
    ref = f"SO {order['reference_number']}"
    lines = out["items"]
    if payload.scan_order:
        # Stable sort by first appearance in the scan pass; lines the
        # sorter never scanned keep their order-line order at the back.
        pos = {}
        for idx, code in enumerate(payload.scan_order):
            key = (code or "").strip().upper()
            if key and key not in pos:
                pos[key] = idx
        far = len(payload.scan_order)

        def scan_pos(line: dict) -> int:
            for term in (line.get("sku"), line.get("barcode")):
                p = pos.get((term or "").strip().upper())
                if p is not None:
                    return p
            return far

        lines = sorted(lines, key=scan_pos)
    intake = _receiving_intake(session, ReceivingPrintsIn(
        items=[ReceivingPrintItemIn(
            sku=(line.get("sku") or "?")[:100],
            quantity=max(1, min(500, line["remaining"])),
            barcode=(line.get("barcode") or None),
        ) for line in lines],
        requested_by=payload.requested_by,
        reference=f"{ref} · {order.get('vendor') or ''}".strip(" ·"),
    ), queue_labels=True, tag_prefix="Full shipment",
        merge_vendor=False)
    batch = intake["batch"]
    session.add(OrderReceipt(
        stock_order_id=order["order_id"],
        reference=ref,
        vendor=order.get("vendor"),
        batch_id=batch.id,
        created_by=payload.requested_by,
    ))
    session.commit()
    session.refresh(batch)
    jobs = intake["jobs"]
    return {
        "reused": False,
        "batch": batch.as_dict(),
        "queued": len(jobs),
        "held_notes": intake["held_notes"],
        "skipped_no_bin": intake["skipped_no_bin"],
        "skipped_unknown": intake["skipped_unknown"],
        "skipped_non_taggable": intake["skipped_non_taggable"],
        "message": (
            f"{ref}: {len(jobs)} label(s) queued on receiving batch "
            f"#{batch.id}"
            + (f"; {'; '.join(intake['held_notes'])}"
               if intake["held_notes"] else "")
            + (f"; held for a bin: {', '.join(intake['skipped_no_bin'])}"
               if intake["skipped_no_bin"] else "")
            + (f"; {len(intake['skipped_unknown'])} unknown SKU(s) "
               "flagged" if intake["skipped_unknown"] else "")
            + ". Label and pair the shipment, then press "
            "\"All boxes labelled\" to count what didn't arrive."
        ),
    }


@app.get(
    "/api/batches/{batch_id}/print-progress",
    dependencies=[Depends(require_user)],
)
def batch_print_progress(
    batch_id: int, session: Session = Depends(get_session)
):
    """How much of this batch's label burst is physically out of the
    printer - the gun's check screen waits on this before the operator
    walks away from the printer (Nick, 2026-09-02)."""
    _get_batch(session, batch_id)
    counts = {"pending": 0, "printing": 0, "done": 0}
    for job in session.scalars(
        select(PrintJob).where(
            PrintJob.batch_id == batch_id,
            PrintJob.status.in_(("pending", "printing", "done")),
        )
    ):
        counts[job.status] += 1
    total = sum(counts.values())
    return {**counts, "total": total,
            "finished": total > 0 and counts["done"] == total}


class SortCountIn(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    count: int = Field(ge=1, le=500)


class SortMatchIn(BaseModel):
    counts: list[SortCountIn] = Field(min_length=1, max_length=200)
    requested_by: str | None = Field(default=None, max_length=100)


@app.post(
    "/api/receiving/sort-match", dependencies=[Depends(require_user)]
)
def receiving_sort_match(
    payload: SortMatchIn, session: Session = Depends(get_session)
):
    """The shipment sorter (Nick, 2026-09-02): a pallet with no order
    number gets its boxes scanned first; this matches the scanned
    counts against every open stock order's remaining lines. One order
    containing EVERY matched product wins outright (consolidation -
    quantity overflow doesn't break it, the extras just get flagged
    for a manual Shopify update); otherwise a greedy split assigns
    each product to the fewest orders that cover the pallet. Read-only:
    nothing prints and nothing is written here."""
    # Resolve the scanned codes FIRST (folding codes that reach the
    # same product): the products name their vendor(s), and the planner
    # walk below then fetches only THAT vendor's open orders (Nick,
    # 2026-09-02 round 3: a pallet is almost always one vendor - the
    # first box names it, the rest just confirm).
    products: list[dict] = []
    by_key: dict[str, dict] = {}
    vendors: set[str] = set()
    for entry in payload.counts:
        code = entry.code.strip()
        product = None
        try:
            product = product_by_barcode(code)
        except HTTPException as error:
            if error.status_code >= 500:
                raise
        sku = (product or {}).get("sku")
        title = (product or {}).get("product_title")
        if ((product or {}).get("vendor") or "").strip():
            vendors.add(product["vendor"].strip())
        match_keys = {code.upper()}
        if product:
            for term in (sku, product.get("barcode")):
                if (term or "").strip():
                    match_keys.add(term.strip().upper())
        fold = (sku or code).strip().upper()
        row = by_key.get(fold)
        if row is None:
            row = {"code": code, "sku": sku, "title": title, "count": 0,
                   "keys": match_keys, "wanted_by": []}
            by_key[fold] = row
            products.append(row)
        row["count"] += entry.count
        row["keys"] |= match_keys

    # No product named a vendor -> the full order walk still runs.
    plan = planner.open_orders_lines(payload.requested_by,
                                     vendors=vendors or None)
    if not plan.get("ok"):
        raise HTTPException(502, plan.get("error")
                            or "The planner bridge isn't configured.")

    def order_tables(plan_out: dict) -> list[dict]:
        """Every remaining line under its SKU and barcode keys
        (case-insensitive, like all SKU compares)."""
        out = []
        for o in plan_out["orders"]:
            keys: dict[str, dict] = {}
            for line in o["items"]:
                for term in (line.get("sku"), line.get("barcode")):
                    key = (term or "").strip().upper()
                    if key:
                        keys.setdefault(key, line)
            out.append({**o, "keys": keys})
        return out

    def rematch(order_rows: list[dict]) -> None:
        for row in products:
            row["wanted_by"] = [
                o["reference_number"] for o in order_rows
                if any(k in o["keys"] for k in row["keys"])
            ]

    orders = order_tables(plan)
    rematch(orders)

    # The vendor-scoped walk found NOTHING: vendor spellings between
    # Shopify and the planner can diverge past containment ("SO 941",
    # 2026-09-02) - retry once against every open order before calling
    # the pallet unmatched.
    if vendors and not any(p["wanted_by"] for p in products):
        wide = planner.open_orders_lines(payload.requested_by)
        if wide.get("ok"):
            plan = wide
            orders = order_tables(plan)
            rematch(orders)

    matched = [p for p in products if p["wanted_by"]]
    unmatched = [{"code": p["code"], "sku": p["sku"],
                  "title": p["title"], "count": p["count"]}
                 for p in products if not p["wanted_by"]]

    def order_products(order: dict, rows: list[dict]) -> list[dict]:
        out = []
        for p in rows:
            line = next(
                (order["keys"][k] for k in p["keys"]
                 if k in order["keys"]), None,
            )
            remaining = int(line["remaining"]) if line else 0
            out.append({
                "code": p["code"], "sku": p["sku"],
                "title": p["title"] or (line or {}).get("title"),
                "count": p["count"], "remaining": remaining,
                "overflow": max(0, p["count"] - remaining),
            })
        return out

    def order_out(order: dict, rows: list[dict]) -> dict:
        return {
            "order_id": order["order_id"],
            "reference_number": order["reference_number"],
            "vendor": order["vendor"],
            "products": order_products(order, rows),
        }

    def split_verdict() -> dict:
        # Greedy split: repeatedly take the order covering the most
        # still-unassigned products (ties to the most boxes absorbed).
        left = list(matched)
        split = []
        while left:
            def coverage(o: dict) -> tuple[int, int]:
                rows = [p for p in left
                        if any(k in o["keys"] for k in p["keys"])]
                return (len(rows), sum(p["count"] for p in rows))

            best = max(orders, key=coverage, default=None)
            if best is None or coverage(best)[0] == 0:
                break
            rows = [p for p in left
                    if any(k in best["keys"] for k in p["keys"])]
            split.append(order_out(best, rows))
            left = [p for p in left if p not in rows]
        return {"consolidated": False, "skipped": [], "orders": split}

    verdict: dict
    covering = [
        o for o in orders
        if matched and all(
            any(k in o["keys"] for k in p["keys"]) for p in matched
        )
    ]
    if covering:
        # Best fit: the order whose remaining quantities absorb the
        # most of what was scanned; ties go to the most recent order
        # (Nick, 2026-09-02 round 3).
        def fit(o: dict) -> tuple[int, int]:
            got = sum(
                min(p["count"], int(next(
                    o["keys"][k] for k in p["keys"] if k in o["keys"]
                )["remaining"]))
                for p in matched
            )
            return (got, int(o.get("order_id") or 0))

        best = max(covering, key=fit)
        verdict = {"consolidated": True, "skipped": [],
                   "orders": [order_out(best, matched)]}
    elif matched and orders:
        # Soft consolidation (Nick, 2026-09-02 round 4): when one order
        # holds MOST of what matched (>= 80%), the pallet is that order
        # - the few strays are SKIPPED with a note naming where they
        # actually belong, instead of forcing a multi-order split.
        def soft_cover(o: dict) -> tuple[int, int, int]:
            rows = [p for p in matched
                    if any(k in o["keys"] for k in p["keys"])]
            return (len(rows), sum(p["count"] for p in rows),
                    int(o.get("order_id") or 0))

        best = max(orders, key=soft_cover)
        rows = [p for p in matched
                if any(k in best["keys"] for k in p["keys"])]
        if rows and len(rows) * 5 >= len(matched) * 4:
            strays = [p for p in matched if p not in rows]
            verdict = {
                "consolidated": True,
                "orders": [order_out(best, rows)],
                "skipped": [
                    {"code": p["code"], "sku": p["sku"],
                     "title": p["title"], "count": p["count"],
                     "wanted_by": p["wanted_by"]}
                    for p in strays
                ],
            }
        else:
            verdict = split_verdict()
    else:
        verdict = split_verdict()

    return {
        "ok": True,
        "products": [
            {"code": p["code"], "sku": p["sku"], "title": p["title"],
             "count": p["count"], "wanted_by": p["wanted_by"]}
            for p in products
        ],
        "verdict": {**verdict, "unmatched": unmatched},
    }


class SortHandoffIn(BaseModel):
    counts: list[SortCountIn] = Field(min_length=1, max_length=200)
    created_by: str | None = Field(default=None, max_length=100)


@app.post(
    "/api/receiving/sort-handoff",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def create_sort_handoff(
    payload: SortHandoffIn, session: Session = Depends(get_session)
):
    """The gun's sort pass, handed to the web terminal (Nick,
    2026-09-02): box labels the gun couldn't resolve get the web
    sorter's label-match tooling instead. One pending pass at a time -
    a new hand-off replaces any unconsumed older one."""
    for old in session.scalars(
        select(SortHandoff).where(SortHandoff.consumed_at.is_(None))
    ):
        old.consumed_at = datetime.now(timezone.utc)
        old.consumed_by = "superseded"
    row = SortHandoff(
        created_by=payload.created_by,
        payload=json.dumps([
            {"code": c.code.strip(), "count": c.count}
            for c in payload.counts
        ]),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return {"handoff": row.as_dict(),
            "message": "Sent - open Batch tab → Sort a shipment on the "
                       "web terminal to load it."}


@app.get(
    "/api/receiving/sort-handoff/pending",
    dependencies=[Depends(require_user)],
)
def pending_sort_handoff(session: Session = Depends(get_session)):
    """Newest unconsumed C72 sort pass (fresh passes only - a day-old
    one is stale pallet history, not work)."""
    row = session.scalar(
        select(SortHandoff).where(SortHandoff.consumed_at.is_(None))
        .order_by(SortHandoff.id.desc())
    )
    if row is not None and row.created_at is not None:
        age = datetime.now(timezone.utc) - (
            row.created_at if row.created_at.tzinfo
            else row.created_at.replace(tzinfo=timezone.utc)
        )
        if age.total_seconds() > 24 * 3600:
            row = None
    return {"handoff": row.as_dict() if row else None}


class SortHandoffConsumeIn(BaseModel):
    consumed_by: str | None = Field(default=None, max_length=100)


@app.post(
    "/api/receiving/sort-handoff/{handoff_id}/consume",
    dependencies=[Depends(require_user)],
)
def consume_sort_handoff(
    handoff_id: int,
    payload: SortHandoffConsumeIn,
    session: Session = Depends(get_session),
):
    row = session.get(SortHandoff, handoff_id)
    if row is None:
        raise HTTPException(404, "No such sort hand-off.")
    if row.consumed_at is None:
        row.consumed_at = datetime.now(timezone.utc)
        row.consumed_by = payload.consumed_by
        session.commit()
    return {"handoff": row.as_dict()}


def _planner_paired_items(session: Session, batch_id: int) -> list[dict]:
    """Paired counts for the planner hand-off, in UNITS: a multi-box
    product pairs one tag per CARTON, so its boxes divide back by the
    slot count before the planner hears a received quantity (a
    half-paired unit doesn't count until its last carton pairs)."""
    out = []
    for i in _batch_items(session, batch_id):
        if not i.sku:
            continue
        units = (i.paired_count or 0) // _item_box_slots(i)
        if units > 0:
            out.append({"sku": i.sku, "qty": units})
    return out


def _unpaired_label_counts(session: Session, batch: Batch) -> list[dict]:
    """Per product: labels printed for this batch minus tags paired -
    the physical leftover strip, by arithmetic (Nick, 2026-09-01)."""
    printed: dict[str, int] = {}
    for job in session.scalars(
        select(PrintJob).where(
            PrintJob.batch_id == batch.id,
            PrintJob.status.in_(("pending", "printing", "done")),
            _NOT_COMPANION,
        )
    ):
        key = (job.sku or "").strip().upper()
        if key:
            printed[key] = printed.get(key, 0) + 1
    out = []
    for item in _batch_items(session, batch.id):
        key = (item.sku or "").strip().upper()
        if not key:
            continue
        n = printed.get(key, 0) - (item.paired_count or 0)
        if n > 0:
            out.append({
                "sku": item.sku,
                "product_title": item.product_title,
                "count": n,
            })
    return out


class SettleShipmentIn(BaseModel):
    created_by: str | None = Field(default=None, max_length=100)


@app.post(
    "/api/batches/{batch_id}/settle-shipment",
    dependencies=[Depends(require_user)],
)
def settle_shipment(
    batch_id: int,
    payload: SettleShipmentIn,
    session: Session = Depends(get_session),
):
    """The operator says: every box that physically arrived is labelled
    and paired - now counting the unused labels. Starts the 1-hour
    Shopify-update clock (Nick, 2026-09-01: the clock starts HERE, even
    if they go back and fix labels) and answers with the per-product
    unpaired counts plus the planner hand-off payload."""
    batch = _get_batch(session, batch_id)
    receipt = session.scalar(
        select(OrderReceipt).where(OrderReceipt.batch_id == batch_id)
        .order_by(OrderReceipt.id.desc())
    )
    if receipt is None:
        raise HTTPException(
            422, "This batch didn't come from Receive entire shipment."
        )
    if receipt.settled_at is None:
        receipt.settled_at = datetime.now(timezone.utc)
        session.commit()
    unpaired = _unpaired_label_counts(session, batch)
    paired_items = _planner_paired_items(session, batch_id)
    return {
        "receipt": receipt.as_dict(),
        "unpaired": unpaired,
        "total_unpaired": sum(u["count"] for u in unpaired),
        "planner": {
            "order_id": receipt.stock_order_id,
            "items": paired_items,
        },
    }


class HeldListIn(BaseModel):
    epcs: list[str] = Field(default_factory=list, max_length=5000)
    created_by: str | None = Field(default=None, max_length=100)


@app.post(
    "/api/batches/{batch_id}/held-list",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def create_held_list(
    batch_id: int,
    payload: HeldListIn,
    session: Session = Depends(get_session),
):
    """Sweep of the unused-label strip: the leftover labels stay on the
    liner, go in a vendor container, and their EPCs become the strip's
    POOL (product accounting = printed minus paired, per SKU). Closes
    the batch - the shipment is settled; what didn't arrive waits on
    the strip. EPCs already assigned (shelf tags the sweep over-heard)
    or already on another strip are excluded, never stolen."""
    batch = _get_batch(session, batch_id)
    receipt = session.scalar(
        select(OrderReceipt).where(OrderReceipt.batch_id == batch_id)
        .order_by(OrderReceipt.id.desc())
    )
    if receipt is None:
        raise HTTPException(
            422, "This batch didn't come from Receive entire shipment."
        )
    if receipt.settled_at is None:
        receipt.settled_at = datetime.now(timezone.utc)
    unpaired = _unpaired_label_counts(session, batch)
    total = sum(u["count"] for u in unpaired)
    swept = {(e or "").strip().upper() for e in payload.epcs if e}
    excluded_assigned = 0
    excluded_held = 0
    pool: set[str] = set()
    if swept:
        owned = {
            (e or "").upper() for e in session.scalars(
                select(RfidAssignment.rfid_id).where(
                    func.upper(RfidAssignment.rfid_id).in_(sorted(swept))
                )
            )
        }
        other_pools: set[str] = set()
        for hl in session.scalars(select(HeldLabelList)):
            other_pools |= hl.epc_set()
        for epc in swept:
            if epc in owned:
                excluded_assigned += 1
            elif epc in other_pools:
                excluded_held += 1
            else:
                pool.add(epc)
    held_list = None
    if total > 0:
        held_list = HeldLabelList(
            batch_id=batch.id,
            stock_order_id=receipt.stock_order_id,
            reference=receipt.reference,
            vendor=receipt.vendor,
            created_by=payload.created_by,
            epcs="\n".join(sorted(pool)),
        )
        session.add(held_list)
        session.flush()
        for u in unpaired:
            session.add(HeldLabelItem(
                list_id=held_list.id,
                sku=u["sku"],
                product_title=u["product_title"],
                count=u["count"],
            ))
    # The shipment is settled: close the batch (a receiving batch with
    # unpaired labels would otherwise never auto-close).
    if batch.status not in ("done", "abandoned"):
        batch.status = "done"
        batch.completed_at = datetime.now(timezone.utc)
    session.commit()
    mismatch = pool and len(pool) != total
    return {
        "list": held_list.as_dict() if held_list else None,
        "items": unpaired,
        "total_unpaired": total,
        "pool_count": len(pool),
        "excluded_assigned": excluded_assigned,
        "excluded_held": excluded_held,
        "planner": {
            "order_id": receipt.stock_order_id,
            "items": _planner_paired_items(session, batch_id),
        },
        "message": (
            (f"{total} unused label(s) held on the "
             f"{receipt.vendor or 'vendor'} strip"
             + (f" - {len(pool)} tag(s) in its pool" if pool else
                " - no sweep captured, the strip still counts by SKU")
             + (f" ({excluded_assigned} swept tag(s) belonged to real "
                "boxes and were excluded)" if excluded_assigned else "")
             + (f" (⚠ pool size doesn't match the {total} unused "
                "label(s) - strays excluded or labels missed)"
                if mismatch else "")
             if total > 0 else
             "Every printed label found its box - nothing to hold")
            + f". Batch #{batch.id} closed. Save the receive in "
            "TC-Planner and update Shopify stock there."
        ),
    }


class StockUpdatedIn(BaseModel):
    stock_order_id: int
    updated_by: str | None = Field(default=None, max_length=100)


@app.post(
    "/api/receiving/stock-updated", dependencies=[Depends(require_user)]
)
def receiving_stock_updated(
    payload: StockUpdatedIn, session: Session = Depends(get_session)
):
    """TC-Planner reports a Shopify stock update for this stock order.
    Stamps the receipt and auto-resolves the stock-not-updated Review
    task if the watchdog already filed one."""
    receipts = session.scalars(
        select(OrderReceipt).where(
            OrderReceipt.stock_order_id == payload.stock_order_id
        )
    ).all()
    closed = 0
    for r in receipts:
        if r.stock_updated_at is None:
            r.stock_updated_at = datetime.now(timezone.utc)
        if r.review_task_id:
            task = session.get(ReviewTask, r.review_task_id)
            if task is not None and task.status == "open":
                task.status = "resolved"
                task.resolved_by = "planner-update"
                task.resolved_at = datetime.now(timezone.utc)
                closed += 1
    session.commit()
    return {"ok": True, "receipts": len(receipts),
            "tasks_closed": closed}


@app.get(
    "/api/receiving/order-status/{stock_order_id}",
    dependencies=[Depends(require_user)],
)
def receiving_order_status(
    stock_order_id: int, session: Session = Depends(get_session)
):
    """Lifecycle of a full-shipment receive, for the planner's gray-out
    (labels already printed) and the Review task's planner hand-off."""
    receipt = session.scalar(
        select(OrderReceipt).where(
            OrderReceipt.stock_order_id == stock_order_id
        ).order_by(OrderReceipt.id.desc())
    )
    if receipt is None:
        return {"printed": False}
    paired_items = _planner_paired_items(session, receipt.batch_id)
    return {
        "printed": True,
        "receipt": receipt.as_dict(),
        "planner": {
            "order_id": receipt.stock_order_id,
            "items": paired_items,
        },
    }


@app.get(
    "/api/receiving/batch-order-status/{batch_id}",
    dependencies=[Depends(require_user)],
)
def receiving_batch_order_status(
    batch_id: int, session: Session = Depends(get_session)
):
    """Same lifecycle payload, looked up by BATCH - the Review task only
    knows its batch_id."""
    receipt = session.scalar(
        select(OrderReceipt).where(OrderReceipt.batch_id == batch_id)
        .order_by(OrderReceipt.id.desc())
    )
    if receipt is None:
        return {"printed": False}
    return receiving_order_status(receipt.stock_order_id, session)


def _check_stock_update_tasks(session: Session) -> None:
    """The 1-hour watchdog (lazy, run when the Review inbox is read):
    a settled full-shipment receive whose Shopify stock never updated
    gets ONE open Review task; resolving it opens the pre-filled
    planner page. Fail-soft."""
    try:
        now = datetime.utcnow()
        dirty = False
        for r in session.scalars(
            select(OrderReceipt).where(
                OrderReceipt.settled_at.isnot(None),
                OrderReceipt.stock_updated_at.is_(None),
                OrderReceipt.review_task_id.is_(None),
            )
        ):
            settled = r.settled_at
            # Azure SQL hands timestamps back tz-aware; sqlite naive.
            if settled is not None and settled.tzinfo is not None:
                settled = settled.astimezone(
                    timezone.utc
                ).replace(tzinfo=None)
            if settled is None or (
                (now - settled).total_seconds() < 3600
            ):
                continue
            task = ReviewTask(
                category="stock-not-updated",
                product_title=(
                    f"{r.reference or 'Stock order'}"
                    + (f" · {r.vendor}" if r.vendor else "")
                )[:255],
                detail=(
                    f"{r.reference or 'A stock order'} was labelled and "
                    "paired over an hour ago, but Shopify stock was "
                    "never updated in TC-Planner. Resolve to open the "
                    "order pre-filled with what actually arrived - Save "
                    "the receive there, then Update stock."
                )[:500],
                batch_id=r.batch_id,
            )
            session.add(task)
            session.flush()
            r.review_task_id = task.id
            dirty = True
        if dirty:
            session.commit()
    except Exception:  # noqa: BLE001 — the inbox must still load
        logger.exception("stock-update watchdog failed")


class DivertIn(BaseModel):
    # The shelf these strays actually belong on.
    bin: str = Field(max_length=100)
    created_by: str | None = Field(default=None, max_length=100)


@app.post(
    "/api/batches/{batch_id}/divert",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def divert_to_bin(
    batch_id: int, payload: DivertIn, session: Session = Depends(get_session)
):
    """Boxes found on the wrong shelf, caught at the Check step before any
    label exists. Rather than rewriting the product's bin, carry them to
    where the rest of that product already lives: their rows move into a
    small side batch for that bin, whose labels — and therefore whose tags
    — say the RIGHT shelf. Nothing has been printed yet, so there is
    nothing to reprint, unpair or peel off.

    The parent batch is left exactly as it was, minus the strays."""
    parent = _get_batch(session, batch_id)
    if _is_receiving(parent):
        raise HTTPException(
            422,
            "Receiving doesn't need side trips — every label already "
            "carries its product's home bin.",
        )
    if parent.status not in ("collecting", "printing"):
        raise HTTPException(
            409,
            f"This batch is {parent.status} — side trips only run while "
            f"it's still being worked.",
        )
    wanted = payload.bin.strip()
    if not wanted:
        raise HTTPException(422, "Which bin are they going to?")
    if bin_contains(wanted, parent.bin_name):
        raise HTTPException(
            422, "That's the bin you're already working in."
        )

    # Everything in this batch whose saved home is that shelf, together —
    # one trip carries them all. tagged_before counts: a box tagged in an
    # earlier session still physically moves (Nick, 2026-09-01), it just
    # needs no label.
    movers = [
        i for i in _batch_items(session, batch_id)
        if i.resolved
        and (i.qty_scanned or i.case_count or i.tagged_before)
        and bin_contains(i.bin_location, wanted)
    ]
    if not movers:
        raise HTTPException(
            404,
            f"Nothing scanned here belongs in {wanted}.",
        )
    paired = [i for i in movers if i.paired_count]
    if paired:
        raise HTTPException(
            409,
            "Some of those already have tags paired, so they can't be moved "
            "as if they were untouched. Undo the pairing first.",
        )
    # The batch-status test above is deliberately loose (a side trip's own
    # labels print the moment it's created, so a stray scanned INSIDE one
    # sits in a 'printing' batch — the EFW-mask case). The real invariant
    # is per item: nothing moves that already has labels printed HERE,
    # because those labels name this bin.
    printed_here = {
        (j.sku or "").upper()
        for j in session.scalars(
            select(PrintJob).where(
                PrintJob.batch_id == batch_id,
                PrintJob.status.in_(("pending", "printing", "done")),
            )
        )
    }
    stuck = [i for i in movers if (i.sku or "").upper() in printed_here]
    if stuck:
        raise HTTPException(
            409,
            f"{len(stuck)} of those already have labels printed for THIS "
            f"bin — reprint/void those labels first, or pair them here.",
        )

    side = Batch(
        bin_name=wanted,
        created_by=payload.created_by or parent.created_by,
        parent_batch_id=parent.id,
        status="collecting",
    )
    session.add(side)
    session.flush()

    for item in movers:
        # The row moves wholesale — counts, cases, label name, the lot.
        item.batch_id = side.id
        # Its saved bin IS this batch's bin now, so it is no longer split
        # from the point of view of the shelf it's on.
        others = bins_other_than(item.bin_location, wanted)
        item.other_bins = (", ".join(others))[:255] if others else None

    session.flush()
    jobs, skipped = _build_label_jobs(
        session, side, payload.created_by or parent.created_by
    )
    # A trip with no labels is still a real trip when the boxes were
    # tagged in an earlier session (tagged_before): nothing prints,
    # nothing pairs - the operator just carries them over and confirms
    # (Nick, 2026-09-01). Bundles-only stays refused: there is no box.
    tagged_before_units = sum((i.tagged_before or 0) for i in movers)
    if not jobs and not tagged_before_units:
        session.rollback()
        raise HTTPException(
            422,
            "Nothing there can be labelled — bundles carry no labels of "
            "their own.",
        )
    session.add_all(jobs)
    side.status = "printing" if jobs else "pairing"
    side.ui_step = "pair"
    session.commit()
    session.refresh(side)
    return {
        "batch": side.as_dict(),
        "parent": parent.as_dict(),
        "moved": len(movers),
        "labels": len(jobs),
        "skipped_bundles": skipped,
        "message": (
            f"{len(movers)} product(s) moved to a side trip for {wanted} — "
            + (
                f"{len(jobs)} label(s) queued. Pair them, then close it"
                if jobs else
                "already tagged, so no labels are needed. Carry the "
                "box(es) over and close it"
            )
            + f" to get back to {parent.bin_name}."
        ),
    }


@app.post(
    "/api/batches/{batch_id}/close-divert",
    dependencies=[Depends(require_user)],
)
def close_divert(batch_id: int, session: Session = Depends(get_session)):
    """Finish a side trip and hand back to the batch it came from. No shelf
    verification is asked for: a side trip only ever covers the few boxes
    carried over, never the whole of its bin."""
    side = _get_batch(session, batch_id)
    if side.parent_batch_id is None:
        raise HTTPException(422, "That batch isn't a side trip.")
    items = _batch_items(session, batch_id)
    unpaired = [
        i for i in items
        if i.resolved and i.paired_count < i.qty_scanned + i.case_count
    ]
    side.status = "done"
    side.completed_at = datetime.now(timezone.utc)
    session.commit()
    parent = session.get(Batch, side.parent_batch_id)
    return {
        "batch": side.as_dict(),
        "parent": parent.as_dict() if parent else None,
        # Reported, not blocked — the operator may deliberately be leaving
        # one for later, and refusing to close would strand them here.
        "unpaired": [
            {"sku": i.sku, "paired": i.paired_count,
             "labels": i.qty_scanned + i.case_count}
            for i in unpaired
        ],
        "message": (
            f"Side trip to {side.bin_name} closed"
            + (f" — back to {parent.bin_name}." if parent else ".")
        ),
    }


class BaselineIn(BaseModel):
    epcs: list[str] = Field(default_factory=list, max_length=5000)


@app.post(
    "/api/batches/{batch_id}/baseline",
    dependencies=[Depends(require_user)],
)
def batch_baseline(
    batch_id: int, payload: BaselineIn, session: Session = Depends(get_session)
):
    """Reconcile a part-tagged shelf before collecting: sweep it, and every
    tag read is matched to its product so the batch starts knowing what was
    tagged in an earlier session. Those boxes count as units on the shelf
    but queue no labels — the work left is exactly the untagged remainder.

    Re-applying with a fresh sweep recomputes from scratch, so a second
    pass over a weak-reading shelf can only improve the picture."""
    batch = _get_batch(session, batch_id)
    if batch.status != "collecting":
        raise HTTPException(
            409,
            f"This batch is already {batch.status} — a baseline only makes "
            f"sense before labels are queued.",
        )
    swept = {e.strip().upper() for e in payload.epcs if e and e.strip()}
    if not swept:
        raise HTTPException(422, "That sweep contained no tags.")

    # One pass over the whole assignments table, matched in memory: the
    # table is thousands of rows at most, and EPC casing has never been
    # guaranteed, so normalising both sides here beats an IN() that would
    # quietly miss on case.
    detected_by_sku: dict = {}
    stray_rows: list = []
    matched = 0
    known_epcs: set = set()
    batch_skus = {
        i.sku for i in _batch_items(session, batch_id) if i.resolved and i.sku
    }
    for a in session.scalars(select(RfidAssignment)):
        epc = (a.rfid_id or "").strip().upper()
        known_epcs.add(epc)
        if epc not in swept:
            continue
        matched += 1
        if a.sku and a.sku in batch_skus:
            detected_by_sku[a.sku] = detected_by_sku.get(a.sku, 0) + 1
        else:
            # A tag on this shelf whose product isn't expected here: either
            # the box wandered, or the bin map is stale. Named, not counted.
            stray_rows.append({
                "sku": a.sku,
                "product_title": a.product_title,
                "recorded_bin": a.bin_location,
                "epc": a.rfid_id,
            })
    unknown = len(swept - known_epcs)

    done = 0
    tagged_products = 0
    for item in _batch_items(session, batch_id):
        item.tagged_before = detected_by_sku.get(item.sku or "", 0)
        if item.tagged_before:
            tagged_products += 1
            if (
                item.expected_qty is not None
                and item.tagged_before >= item.expected_qty
            ):
                done += 1
    batch.baseline_at = datetime.now(timezone.utc)
    session.commit()

    return {
        "batch": batch.as_dict(),
        "swept": len(swept),
        "matched": matched,
        "tagged_products": tagged_products,
        "done_products": done,
        "strays": stray_rows[:20],
        "unknown": unknown,
        "message": (
            f"Baseline applied ✓ — {len(swept)} tag(s) swept, {matched} "
            f"matched to products; {tagged_products} product(s) here "
            f"already carry tags"
            + (f", {done} fully done" if done else "")
            + (f". {len(stray_rows)} tag(s) belong to products not "
               f"expected in {batch.bin_name}" if stray_rows else "")
            + (f". {unknown} tag(s) aren't in the system — printed but "
               f"never paired, or foreign." if unknown else ".")
        ),
    }


class TaggedBeforeIn(BaseModel):
    # Boxes on this shelf that already wear an RFID sticker from an earlier
    # session (side trip, previous batch). 0 is a real answer: "those tags
    # belong to stock somewhere else".
    count: int = Field(ge=0, le=500)
    updated_by: str | None = Field(default=None, max_length=100)


@app.put(
    "/api/batches/{batch_id}/items/{item_id}/tagged-before",
    dependencies=[Depends(require_user)],
)
def set_tagged_before(
    batch_id: int,
    item_id: int,
    payload: TaggedBeforeIn,
    session: Session = Depends(get_session),
):
    """The per-product answer to "some of these boxes are already tagged":
    sets the same field a baseline sweep fills, so all the downstream math
    (units on the shelf, labels to print, pair tracker) is already right.
    Allowed right up to verification — the web verify table resolves
    "extra tags answered" rows by correcting exactly this count — but not
    on a closed batch."""
    batch = _get_batch(session, batch_id)
    if batch.status in ("done", "abandoned"):
        raise HTTPException(
            409,
            f"This batch is {batch.status} — its counts are settled.",
        )
    item = session.get(BatchItem, item_id)
    if item is None or item.batch_id != batch_id:
        raise HTTPException(404, "No such item in this batch.")
    if not item.resolved:
        raise HTTPException(422, "That item never resolved to a product.")
    old = item.tagged_before
    item.tagged_before = payload.count
    if old != payload.count:
        session.add(BarcodeChange(
            sku=item.sku,
            product_title=item.product_title,
            shopify_variant_id=item.shopify_variant_id,
            changed_field="tagged-before",
            old_barcode=str(old),
            new_barcode=str(payload.count),
            changed_by=payload.updated_by,
        ))
    session.commit()
    session.refresh(item)
    d = item.as_dict()
    d["prior_tags"] = (
        _prior_tag_counts(session, batch_id, [item.sku]).get(
            item.sku.strip().upper(), 0
        )
        if item.sku else 0
    )
    return {
        "item": d,
        "message": (
            f"{payload.count} box(es) counted as already tagged — no "
            f"labels will print for those."
            if payload.count else
            "Already-tagged count cleared — every scanned box gets a label."
        ),
    }


class PairIn(BaseModel):
    epc: str = Field(max_length=128)
    item_id: int
    created_by: str | None = Field(default=None, max_length=100)

    @field_validator("epc")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


@app.post(
    "/api/batches/{batch_id}/pair",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def batch_pair(
    batch_id: int, payload: PairIn, session: Session = Depends(get_session)
):
    """Attach one applied label's EPC to the active product. Duplicate EPCs
    are rejected with what they're already assigned to; odd-looking EPCs
    save but come back flagged suspect (same rules as the Scan Station)."""
    batch = _get_batch(session, batch_id)
    item = session.get(BatchItem, payload.item_id)
    if item is None or item.batch_id != batch_id:
        raise HTTPException(404, "No such item in this batch.")
    if not item.resolved:
        raise HTTPException(422, "That item never resolved to a product.")

    # A companion label (box 2..N of a multi-box unit) trigger-reads
    # like any sticker, but it must never become a counting tie (Nick,
    # 2026-09-02: "I need to pair 2 labels to one product"). Same
    # product -> confirmed and done, nothing counted; different
    # product -> the sticker is on the wrong box.
    comp = session.scalar(
        select(CompanionTag).where(
            func.upper(CompanionTag.epc)
            == (payload.epc or "").strip().upper()
        )
    )
    if comp is not None:
        if ((comp.sku or "").strip().upper()
                != (item.sku or "").strip().upper()):
            raise HTTPException(
                409,
                f"That sticker is Box {comp.box_no or '?'} of "
                f"{comp.box_count or '?'} of "
                f"{comp.product_title or comp.sku or '?'} - a companion "
                "label for a DIFFERENT product.",
            )
        return {
            "assignment": None,
            "companion": comp.as_dict(),
            "item": item.as_dict(),
            "receiving_done": False,
            "message": (
                f"Box {comp.box_no or '?'} of {comp.box_count or '?'} "
                f"confirmed ✓ - companion label, the unit counts by "
                "Box 1's tag."
            ),
        }

    # Labels were queued loose boxes first, then sealed cases; pairing walks
    # the same order, so once the loose ones are tied the remaining tags are
    # the case labels and each stands for `case_units` units.
    on_a_case = (
        item.case_count > 0 and item.paired_count >= item.qty_scanned
    )
    # Receiving has no bin of its own — the tag records the box's HOME bin
    # (where it's about to be shelved), matching the label on the box.
    tag_bin = (
        (item.bin_location or batch.bin_name)
        if _is_receiving(batch) else batch.bin_name
    )
    # A multi-box product's cartons each go to their OWN bin, and that
    # per-box bin was printed on the label whose EPC this is - inherit
    # it so the tag records the carton's real shelf (Nick, 2026-09-02,
    # the 11740). Falls back to the item bin for a hand-applied tag.
    # The label's ", Box 1 of 2" note is sticker text, never record.
    mb_marked = bool(_multibox_map(session, [item.sku]))
    if _is_receiving(batch) and (item.kind == "multi_box" or mb_marked):
        job = session.scalar(
            select(PrintJob).where(PrintJob.epc == payload.epc)
        )
        if job is not None and (job.bin_location or "").strip():
            tag_bin = _strip_box_note(job.bin_location) or tag_bin
    assignment = RfidAssignment(
        rfid_id=payload.epc,
        shopify_variant_id=item.shopify_variant_id,
        shopify_product_id=item.shopify_product_id,
        product_title=item.label_name or item.product_title or "",
        variant_title=item.variant_title,
        sku=item.sku,
        barcode=item.barcode,
        bin_location=tag_bin,
        case_units=item.case_units if on_a_case else None,
        assigned_by=payload.created_by,
        batch_id=batch.id,
    )
    assignment.suspect = (
        re.fullmatch(r"[0-9A-Fa-f]{24}", payload.epc) is None
    )
    session.add(assignment)
    # A pairing answers the oldest open audit find for this SKU (the
    # tagless-box work queue from the C72 AUDIT tab), and an EPC off a
    # vendor strip retires its held-label note (the label found its box).
    _consume_audit_find(session, item.sku)
    _consume_held_label(session, payload.epc, item.sku)
    item.paired_count += 1
    if batch.status == "printing":
        batch.status = "pairing"
    # The last box's tag closes a receiving shipment by itself - the
    # whole flow is planner-driven, no Finish ceremony.
    session.flush()
    receiving_done = _maybe_close_receiving(session, batch)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(RfidAssignment).where(
                RfidAssignment.rfid_id == payload.epc
            )
        )
        raise HTTPException(
            409,
            f"Duplicate EPC — already assigned to "
            f"{existing.product_title if existing else 'another product'}.",
        )
    session.refresh(assignment)
    session.refresh(item)
    # Multi-box guidance (Nick, 2026-09-02: read the labels in 1 by 1):
    # the counting tag just paired, so the remaining box labels are the
    # companions - name the next step right on the pair confirmation.
    message = None
    if mb_marked:
        mark = _multibox_map(session, [item.sku]).get(
            (item.sku or "").strip().upper()
        )
        if mark is not None and mark.boxes_per_unit > 1:
            message = (
                f"Box 1 of {mark.boxes_per_unit} paired ✓ - stick the "
                f"other {mark.boxes_per_unit - 1} box label(s) on and "
                "trigger each to confirm (they don't count, the unit "
                "already did)."
            )
    return {
        "assignment": assignment.as_dict(),
        "item": item.as_dict(),
        "receiving_done": receiving_done,
        "message": message,
    }


class PairUndoIn(BaseModel):
    epc: str = Field(max_length=128)
    item_id: int


@app.post(
    "/api/batches/{batch_id}/pair/undo",
    dependencies=[Depends(require_user)],
)
def batch_pair_undo(
    batch_id: int, payload: PairUndoIn, session: Session = Depends(get_session)
):
    _get_batch(session, batch_id)
    item = session.get(BatchItem, payload.item_id)
    if item is None or item.batch_id != batch_id:
        raise HTTPException(404, "No such item in this batch.")
    row = session.scalar(
        select(RfidAssignment).where(
            RfidAssignment.rfid_id == payload.epc.strip()
        )
    )
    if row is None:
        raise HTTPException(404, "No assignment for that EPC.")
    session.delete(row)
    item.paired_count = max(0, item.paired_count - 1)
    session.commit()
    return {"item": item.as_dict()}


STORE_HEADER = "Telescopes Canada"


def _sync_label_aliases(
    session: Session,
    sku: str,
    lines: list[str | None],
    updated_by: str | None,
) -> None:
    """Custom label lines double as lookup aliases while they are saved
    (Nick, 2026-08-25): typing what the sticker says finds the product.
    EPHEMERAL by design - when a line changes, its old alias goes with
    it, and the new line takes over. Safe by construction: the resolver
    consults aliases only after direct resolution misses, so a line that
    happens to equal a real SKU/barcode can never shadow that product.
    A line already linked elsewhere (manual alias, or another product's
    label line) is left alone - first come, first served."""
    sku = (sku or "").strip()
    if not sku:
        return
    want = {
        v.strip() for v in lines
        if v and v.strip() and len(v.strip()) <= 64
    }
    for row in session.scalars(
        select(BarcodeAlias).where(
            BarcodeAlias.kind == "label",
            func.upper(BarcodeAlias.sku) == sku.upper(),
        )
    ):
        if row.alias_barcode in want:
            want.discard(row.alias_barcode)
        else:
            session.delete(row)
    session.flush()
    for line in want:
        clash = session.scalar(
            select(BarcodeAlias).where(
                func.upper(BarcodeAlias.alias_barcode) == line.upper()
            )
        )
        if clash is not None:
            continue
        title = session.scalar(
            select(BinMapEntry.product_title).where(
                func.upper(BinMapEntry.sku) == sku.upper()
            )
        )
        session.add(BarcodeAlias(
            alias_barcode=line,
            sku=sku,
            product_title=title,
            created_by=updated_by,
            kind="label",
        ))


def _two_line_fields(
    sku: str | None, top_text: str | None, sku_line: str | None
) -> tuple | None:
    """The (label_name, placement, label_sku) the two typed lines ask
    for, or None when both sit at their defaults. Used to drive a print
    run DIRECTLY from the dialog - the store-wide save is keyed by SKU
    and silently skips SKU-less products, which made their reprints
    ignore the typed lines entirely (Nick, 2026-09-02: edited SKU line
    printed as "-")."""
    top = (top_text or "").strip()
    centre = (sku_line or "").strip()
    default_centre = (sku or "").strip()
    top_custom = top if top and top != STORE_HEADER else None
    centre_custom = (
        centre if centre and centre != default_centre else None
    )
    if not top_custom and not centre_custom:
        return None
    if top_custom and centre_custom:
        if top_custom == centre_custom:
            return top_custom, "both", None
        return top_custom, "header", centre_custom
    if top_custom:
        return top_custom, "header", None
    return centre_custom, "sku", None


def _save_two_line_label(
    session: Session,
    sku: str,
    top_text: str | None,
    sku_line: str | None,
    updated_by: str | None,
) -> None:
    """Map the two edited label lines onto the saved preferred name. Text
    equal to the defaults (store header on top, the SKU in the centre)
    means "standard"; both at default clears the saved name entirely.
    Custom lines double as ephemeral lookup aliases (_sync_label_aliases).

    SKU-less products can't save here (the store is keyed by SKU) - the
    reprint endpoints apply their typed lines straight to the print run
    via _two_line_fields instead."""
    sku = (sku or "").strip()
    if not sku:
        return
    top = (top_text or "").strip()
    centre = (sku_line or "").strip()
    top_custom = top if top and top != STORE_HEADER else None
    centre_custom = centre if centre and centre != sku else None
    _sync_label_aliases(session, sku, [top_custom, centre_custom], updated_by)
    row = session.get(LabelName, sku)
    if not top_custom and not centre_custom:
        if row is not None:
            session.delete(row)
        return
    if row is None:
        row = LabelName(sku=sku)
        session.add(row)
    if top_custom and centre_custom:
        if top_custom == centre_custom:
            row.label_name, row.placement = top_custom, "both"
            row.sku_text = None
        else:
            row.label_name, row.placement = top_custom, "header"
            row.sku_text = centre_custom
    elif top_custom:
        row.label_name, row.placement = top_custom, "header"
        row.sku_text = None
    else:
        row.label_name, row.placement = centre_custom, "sku"
        row.sku_text = None
    row.updated_by = updated_by


class ReprintLabelsIn(BaseModel):
    """Fix-and-reprint at the Pair step: the labels printed wrong (usually
    a preferred name replacing the wrong line). The operator edits the two
    label lines directly — top line and centre line — and text equal to
    the default (store header / the SKU) means "standard"."""

    count: int = Field(ge=1, le=200)
    top_text: str = Field(default=STORE_HEADER, max_length=76)
    sku_line: str = Field(default="", max_length=56)
    created_by: str | None = Field(default=None, max_length=100)
    # The operator confirmed the OLD stickers are off the boxes — an old
    # sticker left on would answer sweeps alongside the new one.
    old_stickers_removed: bool = False


@app.post(
    "/api/batches/{batch_id}/items/{item_id}/reprint-labels",
    dependencies=[Depends(require_user)],
)
def batch_item_reprint(
    batch_id: int,
    item_id: int,
    payload: ReprintLabelsIn,
    session: Session = Depends(get_session),
):
    """Void this product's labels in the batch, release any tags paired to
    them, optionally fix the saved label name, and queue fresh labels.
    The pair tracker returns to 0/N — N being the fresh count, not the
    old plus the new."""
    batch = _get_batch(session, batch_id)
    if batch.status in ("done", "abandoned"):
        raise HTTPException(409, f"This batch is {batch.status}.")
    item = session.get(BatchItem, item_id)
    if item is None or item.batch_id != batch_id:
        raise HTTPException(404, "No such item in this batch.")
    if not item.resolved:
        raise HTTPException(422, "That item never resolved to a product.")
    if item.kind == "bundle" or item.skipped:
        raise HTTPException(422, "This row carries no labels of its own.")
    if item.paired_count and not payload.old_stickers_removed:
        raise HTTPException(
            409,
            f"{item.paired_count} tag(s) are already paired to the old "
            f"labels. Peel those stickers OFF the boxes first — a leftover "
            f"sticker would answer sweeps alongside the new one — then "
            f"confirm and try again.",
        )

    sku_ci = (item.sku or "").strip().upper()

    # 1. The saved preferred name is the usual culprit (right name, wrong
    # line) — fix it store-wide so the NEXT print anywhere is right too.
    _save_two_line_label(
        session, item.sku or "", payload.top_text, payload.sku_line,
        payload.created_by,
    )

    # 2. Release the ties to the old stickers (they're coming off/binned).
    released = 0
    for tie in _batch_tie_rows(session, batch):
        if (tie.sku or "").strip().upper() == sku_ci:
            session.delete(tie)
            released += 1
    item.paired_count = 0

    # 3. Void the old labels so the tracker counts only the fresh run:
    # unprinted jobs are simply canceled; printed ones are marked voided.
    voided = 0
    for job in session.scalars(
        select(PrintJob).where(
            PrintJob.batch_id == batch.id,
            PrintJob.status.in_(("pending", "printing", "done")),
        )
    ):
        if (job.sku or "").strip().upper() == sku_ci:
            job.status = "canceled" if job.status == "pending" else "voided"
            voided += 1
            if job.kind == "companion":
                c = session.scalar(
                    select(CompanionTag).where(
                        func.upper(CompanionTag.epc)
                        == (job.epc or "").strip().upper()
                    )
                )
                if c is not None:
                    session.delete(c)
    session.flush()

    # 4. Fresh labels, picking up the corrected name. The typed lines
    # drive THIS run directly (Nick, 2026-09-02: a SKU-less product
    # can't save to the store, so reading the store back printed "-"
    # again); the store-wide save above still covers future prints for
    # products that CAN save. Sealed-case labels are rebuilt at the
    # tail, same order the original run used.
    typed = _two_line_fields(item.sku, payload.top_text, payload.sku_line)
    if typed is not None:
        label_name, label_placement, label_sku = typed
    else:
        label_name, label_placement, label_sku = _label_name_for(
            session, item
        )
    cases = min(item.case_count, payload.count) if item.case_units else 0
    per_label_units = (
        [None] * (payload.count - cases) + [item.case_units] * cases
    )
    jobs = [
        PrintJob(
            epc=_new_epc(),
            status="pending",
            case_units=units,
            batch_id=batch.id,
            shopify_variant_id=item.shopify_variant_id,
            shopify_product_id=item.shopify_product_id,
            product_title=item.product_title or "",
            variant_title=item.variant_title,
            sku=item.sku,
            barcode=item.barcode,
            bin_location=batch.bin_name,
            other_bins=item.other_bins,
            label_name=label_name,
            label_placement=label_placement,
            label_sku=label_sku,
            requested_by=payload.created_by or batch.created_by,
        )
        for units in per_label_units
    ]
    jobs = _expand_multibox(session, jobs)
    session.add_all(jobs)
    session.commit()
    session.refresh(item)
    return {
        "item": item.as_dict(),
        "queued": len(jobs),
        "voided": voided,
        "ties_released": released,
        "message": (
            f"{voided} old label(s) voided"
            + (f", {released} tag tie(s) released" if released else "")
            + f" — {len(jobs)} fresh label(s) queued."
        ),
    }


class VerifyIn(BaseModel):
    epcs: list[str] = Field(max_length=2000)


@app.post(
    "/api/batches/{batch_id}/verify", dependencies=[Depends(require_user)]
)
def batch_verify(
    batch_id: int, payload: VerifyIn, session: Session = Depends(get_session)
):
    """Final bin sweep: classify every detected EPC as ours-in-this-batch,
    a known tag from another product, or unknown; report per-product
    paired-vs-detected counts. Read-only — completion decides what becomes
    a ReviewTask."""
    batch = _get_batch(session, batch_id)
    if _is_receiving(batch):
        raise HTTPException(
            422,
            "Receiving batches don't verify against a shelf — finishing "
            "files a bin-check Review task for every bin that received "
            "stock instead.",
        )
    # Scanned-then-zeroed wrong-bin strays are non-events: a vendor
    # barcode that mis-resolved to a foreign product and was decremented
    # to 0 must not surface as "counted 0" anywhere (Nick, 2026-08-26).
    items = [
        i for i in _batch_items(session, batch_id)
        if i.resolved and not (
            _units_on_shelf(i) == 0
            and not i.paired_count
            and (i.bin_location or "").strip()
            and not bin_contains(i.bin_location, batch.bin_name)
        )
    ]
    epcs = {e.strip().upper() for e in payload.epcs if e and e.strip()}
    if epcs and batch.verified_at is None:
        batch.verified_at = datetime.now(timezone.utc)
        session.commit()

    # Keep the Expected snapshots honest at verify (Nick, 2026-09-02:
    # a batch scanned BEFORE the unavailable-stock change kept showing
    # the old raw number - "empty filter cell casing expects 2, but 1
    # is unavailable"). ONE batched lookup per verify press, never one
    # per product; fail-soft, the scan-time snapshot stands offline.
    if batch.status != "done":
        try:
            live = shopify.get_quantities_by_skus(
                sorted({i.sku for i in items if i.sku})
            )
            live_ci = {
                (k or "").strip().upper(): v
                for k, v in (live or {}).items()
            }
            dirty = False
            for i in items:
                key = (i.sku or "").strip().upper()
                if key in live_ci and i.expected_qty != live_ci[key]:
                    i.expected_qty = live_ci[key]
                    dirty = True
            if dirty:
                session.commit()
        except Exception as error:
            logger.warning("verify expected refresh skipped: %s", error)

    assignments = {}
    if epcs:
        rows = session.scalars(
            select(RfidAssignment).where(
                func.upper(RfidAssignment.rfid_id).in_(epcs)
            )
        ).all()
        assignments = {r.rfid_id.upper(): r for r in rows}

    # Tags are OURS by case-insensitive SKU alone. The old key was
    # (SKU, barcode), which called a product's own tags "foreign" the
    # moment its barcode had been replaced since tagging (or the tag was
    # made by a flow that never recorded one). Barcode only breaks the
    # tie for tags with no SKU at all.
    batch_skus = {(i.sku or "").upper() for i in items if i.sku}
    skuless_barcodes = {i.barcode for i in items if not i.sku and i.barcode}
    detected = {}  # upper-SKU (or ("", barcode)) -> count
    # Split by provenance: tags paired IN THIS BATCH are the ones a red
    # verdict is allowed to shout about; everything older only ever goes
    # yellow (sold/moved before this batch — Nick, 2026-08-19).
    detected_batch: dict = {}
    detected_other: dict = {}
    # Where each detected tag's RECORD says it lives — the expandable
    # verify row uses this to explain "1 tag answered, recorded at I1-5".
    detected_bins: dict = {}  # key -> {bin: count}
    foreign, unknown = [], []
    for epc in sorted(epcs):
        row = assignments.get(epc)
        if row is None:
            unknown.append(epc)
            continue
        rsku = (row.sku or "").upper()
        key = None
        if rsku and rsku in batch_skus:
            key = rsku
        elif not rsku and row.barcode in skuless_barcodes:
            key = ("", row.barcode)
        if key is not None:
            detected[key] = detected.get(key, 0) + 1
            if row.batch_id == batch_id:
                detected_batch[key] = detected_batch.get(key, 0) + 1
            else:
                detected_other[key] = detected_other.get(key, 0) + 1
            bkey = (row.bin_location or "").strip() or "(no bin)"
            detected_bins.setdefault(key, {})
            detected_bins[key][bkey] = detected_bins[key].get(bkey, 0) + 1
        else:
            foreign.append(
                {
                    "epc": row.rfid_id,
                    "product_title": row.product_title,
                    "sku": row.sku,
                    "bin_location": row.bin_location,
                }
            )

    # Companion tags (box 2..N of a multi-box unit) are known by name,
    # not strangers - and they never count.
    companions_heard, _comp_epcs = _companions_heard(session, unknown)
    if _comp_epcs:
        unknown = [e for e in unknown if e.upper() not in _comp_epcs]

    # Unknown EPCs that are actually TOMBSTONES get named instead of
    # shrugged at: a replaced/dead sticker still on a box means the peel
    # step was skipped; a presumed-sold tag answering again is probably
    # a return.
    retired_heard = []
    if unknown:
        still_unknown = []
        for epc in unknown:
            r = session.scalar(
                select(RetiredTag).where(
                    func.upper(RetiredTag.rfid_id) == epc
                )
            )
            if r is None:
                still_unknown.append(epc)
            else:
                retired_heard.append({
                    "epc": r.rfid_id,
                    "sku": r.sku,
                    "product_title": r.product_title,
                    "kind": r.kind,
                    "message": (
                        "replaced sticker still on a box — peel it off"
                        if r.kind in ("replaced", "dead")
                        else "retired tag heard — possible return; "
                             "check the box"
                    ),
                })
        unknown = still_unknown

    # Re-tag flow: the presumed-sold reconciliation rides each verify
    # row, so the web can offer "retire N unheard tags" right here.
    shelf_by_item: dict[int, dict] = {}
    if batch.status != "done" and _prev_done_map(
        session, [batch.bin_name]
    ).get((batch.bin_name or "").strip().upper()):
        rec = _shelf_reconcile(session, batch, sorted(epcs))
        shelf_by_item = {r["item_id"]: r for r in rec["items"]}

    # Labels this batch printed per SKU: the anchor of the verdict —
    # printed vs paired vs this-batch-tags-heard is the chain that must
    # hold; older records can only colour things yellow.
    printed: dict[str, int] = {}
    for job in session.scalars(
        select(PrintJob).where(
            PrintJob.batch_id == batch_id,
            PrintJob.status.in_(("pending", "printing", "done")),
            _NOT_COMPANION,
        )
    ):
        if job.sku:
            printed[job.sku.upper()] = printed.get(job.sku.upper(), 0) + 1

    noscan = _noscan_skus(session)
    report = [
        {
            "item_id": i.id,
            "sku": i.sku,
            "product_title": i.label_name or i.product_title,
            "qty_scanned": i.qty_scanned,
            # Boxes that already wore a sticker from an earlier session:
            # they were never scanned or paired HERE, but their tags are
            # on this shelf and the sweep is expected to hear them.
            "tagged_before": i.tagged_before,
            "paired_count": i.paired_count,
            "detected": detected.get(
                (i.sku or "").upper() if i.sku else ("", i.barcode), 0
            ),
            "detected_bins": [
                {"bin": b, "count": c}
                for b, c in sorted(detected_bins.get(
                    (i.sku or "").upper() if i.sku else ("", i.barcode), {}
                ).items())
            ],
            "image_url": i.image_url,
            # Expected silent: the tag never answers once it's on the box.
            "rfid_incompatible": (i.sku or "").strip().upper() in noscan,
            # A walked batch is a deep manual check of the shelf: boxes
            # were physically handled here, so a Shopify bin that says
            # otherwise (or says nothing) earns a one-tap fix offer.
            "bin_location": i.bin_location,
            "bin_differs": bool(
                (i.qty_scanned or i.paired_count or i.tagged_before)
                and (
                    not (i.bin_location or "").strip()
                    or (i.bin_location or "").strip().lower()
                       == "no bin assigned"
                    or not bin_contains(i.bin_location, batch.bin_name)
                )
            ),
            # What Shopify expected on this shelf (snapshot from scan
            # time) and the units actually found — display only, nothing
            # here writes a count anywhere.
            "expected_qty": i.expected_qty,
            "units_total": _units_on_shelf(i),
            # For the "open in Shopify admin" link on the product name.
            "shopify_product_id": i.shopify_product_id,
        }
        for i in items
    ]
    for r in report:
        r["shelf"] = shelf_by_item.get(r["item_id"])
        key = (
            (r["sku"] or "").upper()
            if r["sku"]
            else ("", next(
                (i.barcode for i in items if i.id == r["item_id"]), None
            ))
        )
        db = detected_batch.get(key, 0)
        do = detected_other.get(key, 0)
        r["detected_batch"] = db
        r["detected_other"] = do
        r["printed_count"] = printed.get((r["sku"] or "").upper(), 0)
        # The verdict chain (Nick, 2026-08-19): printed → paired →
        # this batch's tags heard must all agree, else RED. Older tags
        # going quiet is a different story — sold or moved before this
        # batch — and only ever YELLOW. Noscan products judge pairing
        # alone.
        sh = r["shelf"]
        prior_expected = r["tagged_before"]
        if sh is not None and sh["state"] != "noscan":
            prior_expected = max(prior_expected, sh["expected"])
        na = r["rfid_incompatible"]
        # The number the HEARD (EARLIER) chip must show as its target —
        # the chip used to show already-confirmed counts as their own
        # denominator ("5/5" on a row flagged for a silent 6th record).
        r["prior_expected"] = prior_expected
        if r["paired_count"] < r["printed_count"]:
            r["state"] = "pairing-short"
            r["reason"] = (
                f"{r['printed_count'] - r['paired_count']} printed "
                f"label(s) never got a tag paired — finish pairing"
            )
        elif not na and db < r["paired_count"]:
            r["state"] = "batch-silent"
            r["reason"] = (
                f"{r['paired_count'] - db} tag(s) paired in THIS batch "
                f"didn't answer — find those boxes"
            )
        elif not na and do < prior_expected:
            gap = prior_expected - do
            r["state"] = "prior-silent"
            # Say WHY it's yellow with the numbers, built from the sweep
            # decomposition (silent / sales-explained / unexplained),
            # never from the basis label alone: the old wording blamed
            # "beyond what recorded sales explain" whenever ANY sale
            # existed, even when sales explained everything (Nick's
            # AIRPLUS, 2026-08-24). On-hand and sales are both reported
            # when they disagree.
            if sh is not None and (sh.get("sales_since") or 0) > 0:
                silent = (
                    sh.get("explained", 0) + sh.get("unexplained", 0)
                )
                wf = _short_date(sh.get("sales_window_from")) or "tagging"
                r["reason"] = (
                    f"{silent} tag(s) silent, sales since {wf} "
                    f"only account for {sh.get('explained', 0)}"
                    if sh.get("unexplained", 0) > 0
                    else f"{silent} tag(s) silent, recorded sales "
                         f"account for {sh.get('explained', 0)}, but "
                         f"the sweep still came up short"
                )
            elif sh is not None and sh.get("sales_gap"):
                r["reason"] = (
                    f"{gap} earlier tag(s) silent, no recorded sales "
                    f"in the window"
                )
            else:
                r["reason"] = (
                    f"{gap} earlier tag(s) silent, likely sold or "
                    f"moved before this batch"
                )
            oh = (
                sh.get("on_hand") if sh is not None
                else r["expected_qty"]
            )
            if oh is None:
                oh = r["expected_qty"]
            if oh is not None and oh > do + r["paired_count"]:
                still = oh - do - r["paired_count"]
                r["reason"] += (
                    f"; on-hand {oh} still counts "
                    f"{still} of them, count may be off"
                )
            if sh is not None and sh.get("sales_gap"):
                lf = _short_date(sh.get("ledger_from"))
                r["reason"] += (
                    f"; sales history only starts {lf}, older sales "
                    f"are invisible" if lf
                    else "; no sales history exists for this product yet"
                )
            if sh is not None and sh.get("basis") == "records":
                r["reason"] += (
                    "; live count unavailable, comparing against raw "
                    "records, re-send the sweep to re-check"
                )
            r["reason"] += f" ({do} of {prior_expected} answered)"
        else:
            r["state"] = "ok"
            if sh is not None and sh.get("explained"):
                s = sh["explained"]
                r["reason"] = (
                    f"{s} earlier tag(s) silent, recorded sales "
                    f"account for all {s}"
                )
            elif sh is not None and sh.get("presumed_sold"):
                # No sales history for this product: the on-hand
                # shortfall carries the presumption, as before.
                r["reason"] = (
                    f"{sh['presumed_sold']} recorded tag(s) presumed sold"
                )
            else:
                r["reason"] = ""
        # Whether the count may be LOWERED from this row: only when the
        # whole drop is backed by windowed unretired sales AND the
        # feature is on. The endpoint re-checks everything; this flag
        # just tells the client which button to draw.
        found = r.get("units_total") or 0
        r["can_lower"] = bool(
            r["expected_qty"] is not None
            and sh is not None
            and 0 < (r["expected_qty"] - found) <= sh.get("sales_since", 0)
            and shopify_write_enabled("verify_onhand_lower")
        )
    ok = (
        not unknown
        and not foreign
        and not retired_heard
        and all(
            r["state"] not in ("pairing-short", "batch-silent")
            for r in report
        )
    )
    return {
        "bin": batch.bin_name,
        "scanned_epcs": len(epcs),
        "items": report,
        "foreign": foreign,
        "retired_heard": retired_heard,
        "unknown_epcs": unknown,
        "companions_heard": companions_heard,
        # Unresolved codes still in the batch: worth a heads-up at verify
        # (they were counted but match no product), never a blocker —
        # completion drops them without filing Review work (Nick's call,
        # 2026-08-08: the code is removed either way).
        "unresolved_codes": [
            i.scanned_code for i in _batch_items(session, batch_id)
            if not i.resolved and (i.qty_scanned or 0) > 0
        ],
        "ok": ok,
    }


class CompleteIn(BaseModel):
    created_by: str | None = Field(default=None, max_length=100)
    # Closing a bin is a deliberate sign-off made on a full screen, where
    # the counts and mismatches are readable. The web terminal sends this;
    # a scanner's "finish" hands the batch over instead — with one
    # exception: the C72's confirmed EMPTY-bin complete (nothing scanned,
    # nothing already-tagged), where there are no counts to check on any
    # screen and the honest record is "0 of everything, bin done".
    finalize: bool = False


# Resolver names that mean the SYSTEM closed a task — History wears a
# distinct Auto-Resolved tag for these, never a person's click (Nick,
# 2026-08-26).
AUTO_CLOSERS = {"orders-sync", "dupe-check", "batch-count",
                "planner-update"}


def _reconcile_inventory_checks(
    session: Session,
    batch: Batch,
    item: BatchItem,
    created_by: str | None,
) -> bool:
    """A fresh shelf count supersedes what an OPEN inventory check
    remembers (Nick, 2026-08-26 — ZWO M54-M54-7.5: a stale "0 counted
    vs 1" task outlived a newer batch that counted the 1). Every open
    check for the SKU gets its counted/on-hand figures rewritten to the
    new observation, with a note naming the batch; when the fresh
    numbers AGREE the task closes itself, tagged automatic. Returns
    True when an open task absorbed a new MISMATCH — the caller must
    not file a duplicate then."""
    if not item.sku or item.expected_qty is None:
        return False
    units = _units_on_shelf(item)
    expected = item.expected_qty
    open_tasks = session.scalars(
        select(ReviewTask).where(
            ReviewTask.category == "inventory-check",
            ReviewTask.status == "open",
            func.upper(ReviewTask.sku) == item.sku.strip().upper(),
        )
    ).all()
    absorbed = False
    for task in open_tasks:
        old = re.search(r"(\d+) unit\(s\)", task.detail or "")
        breakdown = _units_breakdown(item)
        detail = re.sub(
            r"\d+ unit\(s\)( \([^)]*\))? counted",
            f"{units} unit(s) "
            + (f"({breakdown}) " if breakdown else "")
            + "counted",
            task.detail or "",
            count=1,
        )
        detail = re.sub(
            r"on-hand is \d+", f"on-hand is {expected}", detail, count=1
        )
        if detail != task.detail:
            task.detail = detail[:500]
        session.add(ReviewNote(
            task_key=str(task.id),
            note=(
                f"Bin {batch.bin_name} recounted: {units} unit(s)"
                + (f" (task previously said {old.group(1)})"
                   if old and old.group(1) != str(units) else "")
                + f" — batch #{batch.id}."
            )[:500],
            created_by=created_by,
        ))
        if units == expected:
            task.status = "resolved"
            task.resolved_by = "batch-count"
            task.resolved_at = datetime.now(timezone.utc)
            task.resolution_note = (
                f"A newer count in bin {batch.bin_name} matches Shopify "
                f"({units}) — closed automatically."
            )[:255]
        else:
            task.batch_id = batch.id
            absorbed = True
    return absorbed


@app.post(
    "/api/batches/{batch_id}/complete", dependencies=[Depends(require_user)]
)
def batch_complete(
    batch_id: int,
    payload: CompleteIn,
    session: Session = Depends(get_session),
):
    """Close the batch. Mismatches become Review tasks — recommendations
    for a future product check, never automatic fixes.

    A finish from the shelf (no `finalize`) doesn't close anything: it
    parks the batch as awaiting-verify so the counts get checked on a web
    terminal first."""
    batch = _get_batch(session, batch_id)
    if batch.status in ("done", "abandoned"):
        raise HTTPException(409, f"This batch is already {batch.status}.")
    if _is_receiving(batch):
        return _complete_receiving(session, batch, payload)
    if not payload.finalize:
        if batch.status != "awaiting-verify":
            batch.status = "awaiting-verify"
            # Point every terminal at the step that has to happen next.
            batch.ui_step = "verify"
            session.commit()
        raise HTTPException(
            409,
            f"Bin {batch.bin_name} is ready to close, but bins are closed "
            f"from a web terminal so the counts can be checked on a full "
            f"screen. Open Batch tagging on the PC or iPad — this bin is "
            f"waiting under unfinished batches — run Verify, then Complete "
            f"batch.",
        )
    tasks = []
    for item in _batch_items(session, batch_id):
        name = item.label_name or item.product_title
        # Skipped: the one thing that MUST happen is that it doesn't vanish.
        # No count is asserted and nothing is written anywhere — it simply
        # comes back as work for a human, which is the honest record of
        # "nobody could check this one".
        if item.skipped:
            tasks.append(ReviewTask(
                category="could-not-scan",
                sku=item.sku,
                product_title=name,
                detail=(
                    f"Bin {batch.bin_name}: {name or item.scanned_code} was "
                    f"skipped during tagging"
                    + (f" ({item.skip_reason})" if item.skip_reason else "")
                    + ". It was NOT counted and no quantity was changed — "
                      "it still needs identifying and tagging."
                )[:500],
                batch_id=batch.id,
                created_by=payload.created_by,
            ))
            continue
        if not item.resolved:
            # No Review task (Nick, 2026-08-08): an unresolved code can't
            # resolve to a product from an inbox either — the verify step
            # already flagged it as a non-blocking note, and completing
            # drops the code exactly as removing it would have.
            continue
        # Bundles with defined contents never file count mismatches: their
        # count is ARITHMETIC on the component's count, not a shelf fact
        # of their own. Matters for batches seeded before the contents
        # were defined — those still carry the bundle as a 0-count row.
        if item.sku and _bundle_contents(session, item.sku):
            continue
        # A wrong-bin product scanned by accident and zeroed out asserts
        # NOTHING (Nick, 2026-08-26: vendor barcodes mis-resolved to
        # foreign products on F2-4, and the leftover 0-count rows filed
        # "counted 0" inventory checks against OTHER bins' stock). Home
        # bin elsewhere + zero units counted here = a non-event.
        if (
            _units_on_shelf(item) == 0
            and not item.paired_count
            and (item.bin_location or "").strip()
            and not bin_contains(item.bin_location, batch.bin_name)
        ):
            continue
        # This count is the NEWEST observation: older open checks for
        # the SKU update to it (and close themselves when it agrees
        # with Shopify); a still-standing mismatch lands on the
        # existing task instead of stacking a duplicate.
        absorbed = _reconcile_inventory_checks(
            session, batch, item, payload.created_by
        )
        if (
            item.expected_qty is not None
            and _units_on_shelf(item) != item.expected_qty
            and not absorbed
        ):
            tasks.append(ReviewTask(
                category="inventory-check",
                sku=item.sku,
                product_title=name,
                detail=(
                    f"Bin {batch.bin_name}: {_units_on_shelf(item)} unit(s) "
                    + (f"({_units_breakdown(item)}) "
                       if _units_breakdown(item) else "")
                    + f"counted but Shopify on-hand is {item.expected_qty}. "
                    f"Recommend a product-specific count."
                )[:500],
                batch_id=batch.id,
                created_by=payload.created_by,
            ))
        # Labels (and therefore tags) = loose boxes + sealed cases, which is
        # not the unit count once a case is involved.
        if item.paired_count < item.qty_scanned + item.case_count:
            tasks.append(ReviewTask(
                category="pairing-incomplete",
                sku=item.sku,
                product_title=name,
                detail=(
                    f"Bin {batch.bin_name}: only {item.paired_count} of "
                    f"{item.qty_scanned} RFID tags were paired. Finish "
                    f"pairing at the Scan Station or re-check the shelf."
                )[:500],
                batch_id=batch.id,
                created_by=payload.created_by,
            ))
    session.add_all(tasks)
    batch.status = "done"
    batch.completed_at = datetime.now(timezone.utc)
    session.commit()
    oneleft.kick("batch completed", payload.created_by)
    return {
        "batch": batch.as_dict(),
        "review_tasks": [t.as_dict() for t in tasks],
    }


def _complete_receiving(session: Session, batch: Batch, payload) -> dict:
    """Close a receiving batch. No verify step and no shelf-count checks —
    the boxes fan out to many bins that get audited later instead: one
    bin-check Review task per bin that received stock (the RFID walk-scan
    list), plus the usual unresolved/skipped/orphan-label flags."""
    tasks: list[ReviewTask] = []
    bins: dict[str, int] = {}
    for item in _batch_items(session, batch.id):
        name = item.label_name or item.product_title
        if item.skipped:
            tasks.append(ReviewTask(
                category="could-not-scan",
                sku=item.sku,
                product_title=name,
                detail=(
                    f"Receiving #{batch.id}: {name or item.scanned_code} "
                    f"was skipped"
                    + (f" ({item.skip_reason})" if item.skip_reason else "")
                    + ". It was NOT counted — it still needs identifying "
                      "and tagging."
                )[:500],
                batch_id=batch.id,
                created_by=payload.created_by,
            ))
            continue
        if not item.resolved:
            tasks.append(ReviewTask(
                category="unresolved-barcode",
                detail=(
                    f"Barcode {item.scanned_code} was scanned "
                    f"{item.qty_scanned}x while receiving (#{batch.id}) but "
                    f"never resolved to a product. Link or fix it at the "
                    f"Scan Station."
                )[:500],
                batch_id=batch.id,
                created_by=payload.created_by,
            ))
            continue
        boxes = item.qty_scanned + item.case_count
        if item.paired_count < boxes:
            tasks.append(ReviewTask(
                category="pairing-incomplete",
                sku=item.sku,
                product_title=name,
                detail=(
                    f"Receiving #{batch.id}: only {item.paired_count} of "
                    f"{boxes} labels were paired for {name or item.sku} — "
                    f"an unpaired label is an orphan sticker. Pair or "
                    f"reprint before shelving."
                )[:500],
                batch_id=batch.id,
                created_by=payload.created_by,
            ))
        bin_ = (item.bin_location or "").strip()
        if boxes > 0 and bin_ and bin_.lower() != "no bin assigned":
            bins[bin_] = bins.get(bin_, 0) + boxes
    for bin_, count in sorted(bins.items()):
        tasks.append(ReviewTask(
            category="bin-check",
            detail=(
                f"Bin {bin_}: {count} box(es) shelved from receiving "
                f"#{batch.id}. RFID walk-scan the shelf (Audits → bin "
                f"audit) to confirm the count."
            )[:500],
            batch_id=batch.id,
            created_by=payload.created_by,
        ))
    session.add_all(tasks)
    batch.status = "done"
    batch.completed_at = datetime.now(timezone.utc)
    session.commit()
    oneleft.kick("receiving completed", payload.created_by)
    return {
        "batch": batch.as_dict(),
        "review_tasks": [t.as_dict() for t in tasks],
        "bins_touched": bins,
    }


class BinChecksIn(BaseModel):
    """Manual "mark for inventory check": explicit bins, or a whole rack
    by prefix (every bin-map bin starting `rack`, e.g. rack=I1)."""

    bins: list[str] = Field(default_factory=list)
    rack: str | None = Field(default=None, max_length=100)
    created_by: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=200)


@app.post(
    "/api/review/bin-checks",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def file_bin_checks(
    payload: BinChecksIn, session: Session = Depends(get_session)
):
    wanted: list[str] = [b.strip() for b in payload.bins if b and b.strip()]
    if payload.rack and payload.rack.strip():
        prefix = payload.rack.strip().lower()
        rack_bins = session.scalars(
            select(BinMapEntry.bin)
            .where(BinMapEntry.bin.isnot(None))
            .distinct()
        ).all()
        wanted += [b for b in rack_bins
                   if b and b.strip().lower().startswith(prefix)]
    # De-dupe (CI) and skip bins that already have an OPEN bin-check.
    seen: set[str] = set()
    bins: list[str] = []
    for b in wanted:
        key = b.strip().lower()
        if key and key not in seen:
            seen.add(key)
            bins.append(b.strip())
    if not bins:
        raise HTTPException(422, "Which bins? (none given, rack matched none)")
    open_checks = {
        (t.detail.split(":", 1)[0].removeprefix("Bin ").strip().lower())
        for t in session.scalars(
            select(ReviewTask).where(
                ReviewTask.category == "bin-check",
                ReviewTask.status == "open",
            )
        )
    }
    tasks = []
    for b in bins:
        if b.lower() in open_checks:
            continue
        tasks.append(ReviewTask(
            category="bin-check",
            detail=(
                f"Bin {b}: marked for an inventory check"
                + (f" — {payload.note.strip()}"
                   if payload.note and payload.note.strip() else "")
                + ". RFID walk-scan the shelf (Audits → bin audit) to "
                  "confirm the count."
            )[:500],
            created_by=payload.created_by,
        ))
    session.add_all(tasks)
    session.commit()
    return {
        "count": len(tasks),
        "already_open": len(bins) - len(tasks),
        "tasks": [t.as_dict() for t in tasks],
    }


def _aware(dt: datetime | None) -> datetime | None:
    """Timestamps come back naive from some backends; compare in UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _batch_tie_rows(session: Session, batch: Batch) -> list[RfidAssignment]:
    """Every tag tie belonging to a batch.

    Ties made before rfid_assignments carried a batch_id are matched the
    only way left: the bin that batch walked, during the window it was
    open. Without this, old batches look untied but aren't."""
    rows = {
        r.id: r
        for r in session.scalars(
            select(RfidAssignment).where(RfidAssignment.batch_id == batch.id)
        )
    }
    bin_name = (batch.bin_name or "").strip().lower()
    start = _aware(batch.created_at)
    if not bin_name or start is None:
        return list(rows.values())
    end = _aware(batch.completed_at) or datetime.now(timezone.utc)
    for r in session.scalars(
        select(RfidAssignment).where(
            RfidAssignment.batch_id.is_(None),
            func.lower(func.coalesce(RfidAssignment.bin_location, ""))
            == bin_name,
        )
    ):
        at = _aware(r.assigned_at)
        if at is not None and start <= at <= end:
            rows[r.id] = r
    return list(rows.values())


def _unpair_batch(session: Session, batch: Batch) -> dict:
    """Remove every tag tie this batch created and zero its paired counts.
    Local records only — no Shopify, and the printed labels stay valid."""
    rows = _batch_tie_rows(session, batch)
    legacy = sum(1 for r in rows if r.batch_id is None)
    for row in rows:
        session.delete(row)
    for item in _batch_items(session, batch.id):
        item.paired_count = 0
    session.commit()
    return {"removed": len(rows), "legacy": legacy}


class AbandonIn(BaseModel):
    remove_ties: bool = True


@app.post(
    "/api/batches/{batch_id}/abandon", dependencies=[Depends(require_user)]
)
def batch_abandon(
    batch_id: int,
    payload: AbandonIn | None = None,
    session: Session = Depends(get_session),
):
    """Close a batch without completing it. By default the tag ties this
    batch created are removed too — an abandoned bin shouldn't leave
    products tied to labels that were never verified."""
    batch = _get_batch(session, batch_id)
    if batch.status == "done":
        raise HTTPException(409, "This batch is already done.")
    removed = 0
    if payload is None or payload.remove_ties:
        # Before flipping status: completed_at bounds the legacy window.
        removed = _unpair_batch(session, batch)["removed"]
    batch.status = "abandoned"
    batch.completed_at = datetime.now(timezone.utc)
    session.commit()
    result = batch.as_dict()
    result["ties_removed"] = removed
    return result


@app.post(
    "/api/batches/{batch_id}/unpair-all",
    dependencies=[Depends(require_user)],
)
def batch_unpair_all(batch_id: int, session: Session = Depends(get_session)):
    """Undo the pairing step: every tag this batch tied is released so the
    shelf can be re-scanned. Labels already printed stay valid."""
    batch = _get_batch(session, batch_id)
    return _unpair_batch(session, batch)


class StepIn(BaseModel):
    step: str = Field(pattern="^(collect|check|print|pair|verify)$")


@app.post(
    "/api/batches/{batch_id}/step", dependencies=[Depends(require_user)]
)
def batch_set_step(
    batch_id: int, payload: StepIn, session: Session = Depends(get_session)
):
    """Record which step the operator is on so other terminals watching
    this batch can follow. Purely a UI signal — nothing else reads it."""
    batch = _get_batch(session, batch_id)
    batch.ui_step = payload.step
    session.commit()
    return {"id": batch.id, "ui_step": batch.ui_step}


@app.post(
    "/api/batches/{batch_id}/skip-print",
    dependencies=[Depends(require_user)],
)
def batch_skip_print(batch_id: int, session: Session = Depends(get_session)):
    """Go straight to pairing without queueing labels — for bins whose
    labels are already printed and applied."""
    batch = _get_batch(session, batch_id)
    if batch.status in ("done", "abandoned"):
        raise HTTPException(409, f"This batch is {batch.status}.")
    batch.status = "pairing"
    session.commit()
    return batch.as_dict()


@app.delete(
    "/api/batches/{batch_id}/items/{item_id}",
    status_code=204,
    dependencies=[Depends(require_user)],
)
def batch_item_delete(
    batch_id: int, item_id: int, session: Session = Depends(get_session)
):
    """Drop a row from the batch (an unresolved barcode you don't want
    counted, or a product that belongs on another shelf). Any tags already
    tied to it are released with it."""
    _get_batch(session, batch_id)
    item = session.get(BatchItem, item_id)
    if item is None or item.batch_id != batch_id:
        raise HTTPException(404, "No such item in this batch.")
    if item.sku:
        for row in session.scalars(
            select(RfidAssignment).where(
                RfidAssignment.batch_id == batch_id,
                RfidAssignment.sku == item.sku,
            )
        ):
            session.delete(row)
    session.delete(item)
    session.commit()


class UnlinkedIn(BaseModel):
    epcs: list[str] = Field(min_length=1, max_length=2000)


@app.post(
    "/api/batches/{batch_id}/unlinked",
    dependencies=[Depends(require_user)],
)
def batch_unlinked(
    batch_id: int,
    payload: UnlinkedIn,
    session: Session = Depends(get_session),
):
    """Given a sweep, report which tags aren't tied to anything yet — the
    unreadable-label rescue: sweep the shelf, find the orphan, tie it."""
    _get_batch(session, batch_id)
    epcs = []
    seen: set = set()
    for raw in payload.epcs:
        epc = (raw or "").strip().upper()
        if epc and epc not in seen:
            seen.add(epc)
            epcs.append(epc)
    taken = {
        r.rfid_id.upper(): r
        for r in session.scalars(
            select(RfidAssignment).where(RfidAssignment.rfid_id.in_(epcs))
        )
    }
    unlinked = [e for e in epcs if e not in taken]
    return {
        "swept": len(epcs),
        "unlinked": unlinked,
        "linked": [
            {"epc": e, "product_title": taken[e].product_title,
             "sku": taken[e].sku}
            for e in epcs if e in taken
        ],
    }


# ------------------------------------------------------------ review tasks ---
def _bin_mismatch_entries(session: Session) -> list[dict]:
    """LIVE bin disagreements as synthetic Review entries: products whose
    tags sit at a shelf Shopify's bin value doesn't include. Computed
    fresh on every fetch and never stored — fixing the bin (writing the
    tags' shelf to Shopify, or moving the product) clears the entry by
    itself, so there is nothing to resolve, dismiss, or go stale."""
    shopify_bin_by_sku: dict = {}
    barcode_by_sku: dict = {}
    img_by_sku: dict = {}
    for sku, bin_, other, barcode, img in session.execute(
        select(BinMapEntry.sku, BinMapEntry.bin, BinMapEntry.other_bins,
               BinMapEntry.barcode, BinMapEntry.image_url)
    ):
        key = (sku or "").strip().upper()
        if not key:
            continue
        full = ", ".join(x for x in ((bin_ or "").strip(), other) if x)
        if full:
            shopify_bin_by_sku.setdefault(key, full)
        if barcode:
            barcode_by_sku.setdefault(key, barcode)
        if img:
            img_by_sku.setdefault(key, img)

    # Dismissed disagreements stay quiet while the exact (sku, tags' bin,
    # Shopify's bin) triple still holds; either shelf changing makes it a
    # new situation and it comes back.
    dismissed = {
        ((d.sku or "").strip().upper(),
         (d.tag_bin or "").strip().lower(),
         (d.shopify_bin or "").strip().lower())
        for d in session.scalars(select(MismatchDismissal))
    }

    entries: list[dict] = []
    for sku, title, tag_bin, n_tags, newest, barcode in session.execute(
        select(
            RfidAssignment.sku,
            func.max(RfidAssignment.product_title),
            func.max(RfidAssignment.bin_location),
            func.count(),
            func.max(RfidAssignment.assigned_at),
            func.max(RfidAssignment.barcode),
        )
        .where(RfidAssignment.sku.isnot(None))
        .group_by(RfidAssignment.sku)
    ):
        key = (sku or "").strip().upper()
        rfid_bin = (tag_bin or "").strip()
        saved = shopify_bin_by_sku.get(key)
        # Only a real DISAGREEMENT counts: both sides have a bin and
        # Shopify's listing doesn't include the tags' shelf. A missing
        # Shopify bin is the Inventory tab's "⇢ Shopify" case instead.
        if (
            not key
            or not rfid_bin
            or rfid_bin in MISSING_BIN_VALUES
            or not saved
            or bin_contains(saved, rfid_bin)
        ):
            continue
        if (key, rfid_bin.lower(), saved.strip().lower()) in dismissed:
            continue
        entries.append({
            "id": f"binmm:{key}",
            "synthetic": True,
            "category": "bin-mismatch",
            "status": "open",
            "sku": sku,
            "barcode": barcode or barcode_by_sku.get(key),
            "product_title": title,
            "detail": (
                f"{n_tags} tag(s) were placed at {rfid_bin}, but Shopify "
                f"bins this product at {saved}. Either write {rfid_bin} "
                f"to Shopify (the tags mark where the boxes really are) "
                f"or move the boxes to {saved}."
            ),
            "tag_bin": rfid_bin,
            "shopify_bin": saved,
            "batch_id": None,
            "created_at": newest.isoformat() if newest else None,
            "created_by": None,
            "image_url": img_by_sku.get(key),
        })
    entries.sort(key=lambda e: e["created_at"] or "", reverse=True)
    return entries


@app.get("/api/review-tasks", dependencies=[Depends(require_user)])
def list_review_tasks(
    status: str = "open",
    limit: int = 100,
    session: Session = Depends(get_session),
):
    # Lazy watchdog: settled full-shipment receives whose Shopify stock
    # never updated file their task the next time anyone reads the inbox.
    _check_stock_update_tasks(session)
    stmt = select(ReviewTask).order_by(ReviewTask.id.desc())
    if status != "all":
        stmt = stmt.where(ReviewTask.status == status.strip())
    rows = session.scalars(stmt.limit(min(limit, 500))).all()
    # Product images for the expandable preview, from the local bin map.
    imgs: dict = {}
    skus = {(t.sku or "").strip().upper() for t in rows if t.sku}
    if skus:
        for sku, img in session.execute(
            select(BinMapEntry.sku, BinMapEntry.image_url)
            .where(BinMapEntry.image_url.isnot(None))
        ):
            if sku and sku.strip().upper() in skus:
                imgs.setdefault(sku.strip().upper(), img)
    tasks = []
    for t in rows:
        d = t.as_dict()
        d["image_url"] = imgs.get((t.sku or "").strip().upper())
        tasks.append(d)
    # Live bin disagreements ride along with the open inbox (they have no
    # stored row — see _bin_mismatch_entries).
    if status in ("open", "all"):
        try:
            tasks = _bin_mismatch_entries(session) + tasks
        except Exception as error:
            logger.warning("bin-mismatch entries failed: %s", error)
    # Notes ride on every entry — stored tasks key by their id as text,
    # synthetic entries by their stable string id.
    keys = {str(t["id"]) for t in tasks}
    if keys:
        notes_by_key: dict = {}
        for n in session.scalars(
            select(ReviewNote).where(ReviewNote.task_key.in_(keys))
            .order_by(ReviewNote.id)
        ):
            notes_by_key.setdefault(n.task_key, []).append(n.as_dict())
        for t in tasks:
            t["notes"] = notes_by_key.get(str(t["id"]), [])
    return {"count": len(tasks), "tasks": tasks}


class ResolveIn(BaseModel):
    resolved_by: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=255)
    dismissed: bool = False


class RecountIn(BaseModel):
    count: int = Field(ge=0, le=5000)
    changed_by: str | None = Field(default=None, max_length=100)


@app.post(
    "/api/review-tasks/{task_id}/recount",
    dependencies=[Depends(require_user)],
)
def recount_review_task(
    task_id: int, payload: RecountIn, session: Session = Depends(get_session)
):
    """The Inventory Check window's manual -/+ recount (Nick, 2026-08-26):
    the operator KNOWS what's on the shelf and corrects the counted
    number without a walk. Rewrites the task's counted figure, corrects
    the source batch row when there is one, and logs a History row - the
    RFID side only. Any Shopify on-hand change rides the existing audited
    on-hand endpoints (the client chains them), never this one."""
    task = session.get(ReviewTask, task_id)
    if task is None:
        raise HTTPException(404, "No such review task.")
    if task.category != "inventory-check":
        raise HTTPException(422, "Manual recounts are for inventory checks.")
    if task.status != "open":
        raise HTTPException(409, f"Task is already {task.status}.")
    m = re.search(r"(\d+) unit\(s\) counted", task.detail or "")
    old_count = int(m.group(1)) if m else None
    if m:
        task.detail = (
            task.detail[:m.start(1)] + str(payload.count)
            + task.detail[m.end(1):]
        )[:500]
    by = (payload.changed_by or "").strip()[:100] or None
    # Correct the batch row the count came from, so every later view of
    # that batch tells the corrected story too.
    item = None
    if task.batch_id and task.sku:
        item = session.scalar(
            select(BatchItem).where(
                BatchItem.batch_id == task.batch_id,
                func.upper(BatchItem.sku) == task.sku.strip().upper(),
            )
        )
    if item is not None:
        delta = payload.count - _units_on_shelf(item)
        if delta >= 0:
            item.qty_scanned += delta
        else:
            take = min(item.qty_scanned, -delta)
            item.qty_scanned -= take
            item.tagged_before = max(0, item.tagged_before - (-delta - take))
    session.add(BarcodeChange(
        sku=task.sku,
        product_title=task.product_title,
        changed_field="recount",
        old_barcode=(str(old_count) if old_count is not None else None),
        new_barcode=str(payload.count),
        changed_by=by,
    ))
    session.add(ReviewNote(
        task_key=str(task.id),
        note=(
            f"Manual recount: "
            f"{old_count if old_count is not None else '?'} -> "
            f"{payload.count}."
        )[:500],
        created_by=by,
    ))
    session.commit()
    return {
        "task": task.as_dict(),
        "old_count": old_count,
        "count": payload.count,
        "message": (
            f"Counted set to {payload.count}"
            + (f" (was {old_count})" if old_count is not None else "")
            + " - logged. Shopify was not touched by this step."
        ),
    }


class SoldOutRetireIn(BaseModel):
    changed_by: str | None = Field(default=None, max_length=100)
    confirmed: bool = False


@app.post(
    "/api/review-tasks/{task_id}/retire-all-sold",
    dependencies=[Depends(require_user)],
)
def retire_all_sold(
    task_id: int,
    payload: SoldOutRetireIn,
    session: Session = Depends(get_session),
):
    """The Tags vs On-hand window's sold-out shortcut (Nick, 2026-08-26):
    when Shopify's live on-hand is 0, every remaining box has sold, so
    ALL the SKU's live tags retire presumed-sold in one click, the sold
    ledger absorbs them, and the task resolves itself. The on-hand==0
    guard re-checks LIVE here - a stale window can't fire this after
    stock came back. Local records only; Shopify is never written."""
    task = session.get(ReviewTask, task_id)
    if task is None:
        raise HTTPException(404, "No such review task.")
    if task.category != "tag-onhand-mismatch":
        raise HTTPException(
            422, "The sold-out shortcut is for Tags vs On-hand tasks."
        )
    if task.status != "open":
        raise HTTPException(409, f"Task is already {task.status}.")
    sku = (task.sku or "").strip()
    if not sku:
        raise HTTPException(422, "This task has no SKU.")
    try:
        # Shelf-expected (on-hand minus Unavailable): a damaged/reserved
        # unit off the shelf must not block the sold-out shortcut.
        live = shopify.get_shelf_on_hand(sku)
    except Exception as error:  # noqa: BLE001 — surfaced, not swallowed
        raise HTTPException(
            502, f"Couldn't read the live on-hand: {str(error)[:200]}"
        )
    if live is None:
        raise HTTPException(404, f"No Shopify product for SKU {sku}.")
    if live != 0:
        raise HTTPException(
            422,
            f"Shopify shows {live} on hand for {sku} - boxes are still "
            f"expected on the shelf, so a blanket presumed-sold is not "
            f"safe. Run the bin audit instead.",
        )
    tags = session.scalars(
        select(RfidAssignment).where(
            func.upper(RfidAssignment.sku) == sku.upper()
        ).order_by(RfidAssignment.id)
    ).all()
    if not tags:
        raise HTTPException(404, f"No live tags on file for {sku}.")
    units = sum((t.case_units or 1) for t in tags)
    by = (payload.changed_by or "").strip()[:100] or None
    if not payload.confirmed:
        raise HTTPException(
            409,
            f"Shopify on-hand is 0 for {sku}: this retires all "
            f"{len(tags)} tag(s) ({units} unit(s)) as presumed-sold and "
            f"resolves the task. Each tag is restorable from History. "
            f"Confirm to proceed.",
        )
    moved_rows: list[tuple[RetiredTag, RfidAssignment]] = []
    for t in tags:
        rt = RetiredTag(
            rfid_id=t.rfid_id,
            sku=t.sku,
            product_title=t.product_title,
            shopify_variant_id=t.shopify_variant_id,
            bin_location=t.bin_location,
            case_units=t.case_units,
            kind="presumed-sold",
            retired_by=by,
            note=f"review task #{task.id} · on-hand 0",
        )
        session.add(rt)
        session.add(BarcodeChange(
            sku=t.sku,
            product_title=t.product_title,
            shopify_variant_id=t.shopify_variant_id,
            changed_field="tag-retired",
            old_barcode=t.rfid_id,
            new_barcode="presumed-sold",
            changed_by=by,
        ))
        session.delete(t)
        moved_rows.append((rt, t))
    _consume_ledger_for_retirements(session, moved_rows)
    task.status = "resolved"
    task.resolved_by = by
    task.resolved_at = datetime.now(timezone.utc)
    task.resolution_note = (
        f"All {len(tags)} tag(s) marked presumed sold "
        f"(Shopify on-hand 0)."
    )[:255]
    session.commit()
    return {
        "retired": [t.rfid_id for t in tags],
        "units": units,
        "message": (
            f"{len(tags)} tag(s) retired presumed-sold and the task "
            f"resolved. Each is restorable from History."
        ),
    }


@app.post(
    "/api/backorder-debts/{debt_id}/clear",
    dependencies=[Depends(require_user)],
)
def clear_backorder_debt(
    debt_id: int,
    payload: SoldOutRetireIn,
    session: Session = Depends(get_session),
):
    """History's undo for a Backorder Noted event: the note was wrong
    (or the books got fixed another way), so stop carrying it. The
    expected-tag check may re-flag the SKU on its next run - that's the
    point of clearing a bad note."""
    row = session.get(BackorderDebt, debt_id)
    if row is None:
        raise HTTPException(404, "No such backorder note.")
    if row.cleared_at is not None:
        raise HTTPException(409, "That note is already cleared.")
    by = (payload.changed_by or "").strip()[:100] or None
    row.cleared_at = datetime.now(timezone.utc)
    row.cleared_by = by or "manual"
    session.add(BarcodeChange(
        sku=row.sku,
        changed_field="backorder-debt-clear",
        old_barcode=str(row.id),
        new_barcode=str(row.units),
        changed_by=by,
    ))
    session.commit()
    return {
        "cleared": row.as_dict(),
        "message": (
            f"Backorder note cleared - the expected count for {row.sku} "
            f"drops by {row.units} unit(s) and the daily check may "
            f"flag it again."
        ),
    }


@app.post(
    "/api/review-tasks/{task_id}/resolve",
    dependencies=[Depends(require_user)],
)
def resolve_review_task(
    task_id: int, payload: ResolveIn, session: Session = Depends(get_session)
):
    task = session.get(ReviewTask, task_id)
    if task is None:
        raise HTTPException(404, "No such review task.")
    if task.status != "open":
        raise HTTPException(409, f"Task is already {task.status}.")
    task.status = "dismissed" if payload.dismissed else "resolved"
    task.resolved_by = payload.resolved_by
    task.resolved_at = datetime.now(timezone.utc)
    task.resolution_note = payload.note
    session.commit()
    return task.as_dict()


@app.post(
    "/api/review-tasks/{task_id}/reopen",
    dependencies=[Depends(require_user)],
)
def reopen_review_task(
    task_id: int, session: Session = Depends(get_session)
):
    """Undo a resolve/dismiss: the task returns to the open inbox. The
    original resolution stays in History (that event already happened) —
    this just puts the work back on the list."""
    task = session.get(ReviewTask, task_id)
    if task is None:
        raise HTTPException(404, "No such review task.")
    if task.status == "open":
        raise HTTPException(409, "That task is already open.")
    task.status = "open"
    task.resolved_by = None
    task.resolved_at = None
    task.resolution_note = None
    session.commit()
    return task.as_dict()


class ReviewNoteIn(BaseModel):
    """A note pinned to a Review entry — stored tasks key by their id as
    text; synthetic bin-mismatch entries by their "binmm:SKU" id."""

    task_key: str = Field(min_length=1, max_length=120)
    note: str = Field(min_length=1, max_length=500)
    created_by: str | None = Field(default=None, max_length=100)

    @field_validator("task_key", "note")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


@app.post(
    "/api/review-notes",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def add_review_note(
    payload: ReviewNoteIn, session: Session = Depends(get_session)
):
    row = ReviewNote(
        task_key=payload.task_key,
        note=payload.note,
        created_by=payload.created_by,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row.as_dict()


class MismatchDismissIn(BaseModel):
    """Dismiss one live bin-mismatch: records the exact disagreement so
    the synthetic entry stays suppressed while it still holds."""

    sku: str = Field(min_length=1, max_length=100)
    tag_bin: str = Field(min_length=1, max_length=100)
    shopify_bin: str = Field(min_length=1, max_length=255)
    dismissed_by: str | None = Field(default=None, max_length=100)


@app.post(
    "/api/review/mismatch-dismissals",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def dismiss_mismatch(
    payload: MismatchDismissIn, session: Session = Depends(get_session)
):
    row = MismatchDismissal(
        sku=payload.sku.strip(),
        tag_bin=payload.tag_bin.strip(),
        shopify_bin=payload.shopify_bin.strip(),
        dismissed_by=payload.dismissed_by,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return {"id": row.id, "sku": row.sku}


@app.delete(
    "/api/review/mismatch-dismissals/{dismissal_id}",
    dependencies=[Depends(require_user)],
)
def undismiss_mismatch(
    dismissal_id: int, session: Session = Depends(get_session)
):
    """History's undo: delete the suppression and the live entry is back
    on the next Review fetch (if the disagreement still exists at all)."""
    row = session.get(MismatchDismissal, dismissal_id)
    if row is None:
        raise HTTPException(404, "No such dismissal.")
    session.delete(row)
    session.commit()
    return {"ok": True}


@app.get(
    "/api/review-tasks/{task_id}/context",
    dependencies=[Depends(require_user)],
)
def review_task_context(
    task_id: int, session: Session = Depends(get_session)
):
    """Live context for the resolve window, per category — the checks
    that let half the backlog resolve itself with one click. One quick
    query each; the single Shopify call (inventory-check) degrades to
    null on failure rather than slowing the window down."""
    task = session.get(ReviewTask, task_id)
    if task is None:
        raise HTTPException(404, "No such review task.")
    ctx: dict = {"category": task.category}
    sku = (task.sku or "").strip()
    if task.category == "inventory-check" and sku:
        try:
            ctx["live_on_hand"] = _expected_qty(session, sku)
        except Exception:  # noqa: BLE001 — live extras fail soft
            ctx["live_on_hand"] = None
        ctx["units_on_file"] = _tag_units_for_sku(session, sku)
    elif task.category == "tag-onhand-mismatch" and sku:
        # The sold-out shortcut (Nick, 2026-08-26): live on-hand at 0
        # means every remaining box has sold, so the window can offer
        # retiring ALL tags presumed-sold in one click. Shelf-expected
        # since 2026-09-01 (on-hand minus Unavailable).
        try:
            ctx["live_on_hand"] = shopify.get_shelf_on_hand(sku)
        except Exception:  # noqa: BLE001 — live extras fail soft
            ctx["live_on_hand"] = None
        ctx["units_on_file"] = _tag_units_for_sku(session, sku)
        ctx["tag_count"] = len(session.scalars(
            select(RfidAssignment).where(
                func.upper(RfidAssignment.sku) == sku.upper()
            )
        ).all())
    elif task.category == "pairing-incomplete" and task.batch_id and sku:
        item = session.scalar(
            select(BatchItem).where(
                BatchItem.batch_id == task.batch_id,
                func.upper(BatchItem.sku) == sku.upper(),
            )
        )
        if item is not None:
            ctx["paired_count"] = item.paired_count
            ctx["labels_total"] = item.qty_scanned + item.case_count
    elif task.category == "could-not-scan" and sku:
        ctx["units_on_file"] = _tag_units_for_sku(session, sku)
        # The desk-sort flow: bundles offer their components to tag
        # (defined once, reused); an undefined bundle offers the setup.
        kind, _ = resolve_product_kind(
            session, task.product_title, sku, None
        )
        contents = _bundle_contents(session, sku)
        ctx["kind"] = "bundle" if contents else kind
        ctx["bundle_contents"] = contents
    elif task.category == "bin-check":
        cap = session.scalar(
            select(EpcCapture).order_by(EpcCapture.id.desc()).limit(1)
        )
        if cap is not None:
            ctx["latest_sweep_at"] = (
                cap.created_at.isoformat() if cap.created_at else None
            )
            ctx["latest_sweep_device"] = cap.device
    elif task.category == "duplicate-product":
        # Both sides of the suspected pair, for the merge picker: tag
        # units, every distinct recorded name, and whether the live
        # catalog actually knows the SKU (the misspelled one won't be).
        # Separator-tolerant: "<->" (current, ASCII-safe), "⇄" (original),
        # or the "?" SQL Server's VARCHAR turned that arrow into.
        m = re.match(
            r"Possible duplicate products: (.+?) (?:<->|⇄|\?) (.+?) —",
            task.detail or "",
        )
        if m:
            sides = []
            for raw in (m.group(1).strip(), m.group(2).strip()):
                tags = session.scalars(
                    select(RfidAssignment).where(
                        func.upper(RfidAssignment.sku) == raw.upper()
                    )
                ).all()
                bm = session.scalar(
                    select(BinMapEntry).where(
                        func.upper(BinMapEntry.sku) == raw.upper()
                    )
                )
                titles = sorted({
                    t.product_title for t in tags if t.product_title
                })
                if bm is not None and bm.product_title:
                    titles = sorted(set(titles) | {bm.product_title})
                newest = max(tags, key=lambda t: t.id) if tags else None
                sides.append({
                    "sku": raw,
                    "units": sum((t.case_units or 1) for t in tags),
                    "titles": titles,
                    # The card's display name: what the catalog calls it,
                    # else the newest tag's recorded title.
                    "title": (
                        (bm.product_title if bm is not None else None)
                        or (newest.product_title if newest else None)
                    ),
                    "in_catalog": bm is not None,
                    "barcode": (
                        (bm.barcode if bm is not None else None)
                        or (newest.barcode if newest else None)
                    ),
                    "image_url": bm.image_url if bm is not None else None,
                    "bin": (
                        (bm.bin if bm is not None else None)
                        or (newest.bin_location if newest else None)
                    ),
                })
            ctx["sides"] = sides
    return ctx


def _tag_units_for_sku(session: Session, sku: str) -> int:
    """Units the RFID system holds for a SKU (case tags count their
    units)."""
    total = 0
    for row in session.scalars(
        select(RfidAssignment).where(
            func.upper(RfidAssignment.sku) == sku.strip().upper()
        )
    ):
        total += row.case_units if (row.case_units or 0) > 1 else 1
    return total


# ------------------------------------------------------------ EPC captures ---
# Sweeps sent by the C72 companion app over Wi-Fi (scan anywhere, Send once
# when done — no Bluetooth). The browser pulls the latest into batch verify.

class CaptureIn(BaseModel):
    epcs: list[str] = Field(min_length=1, max_length=20000)
    device: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=255)
    # Sweeps taken inside a batch carry it, so the web terminal watching
    # that batch can pick the sweep up by itself.
    batch_id: int | None = None


@app.post(
    "/api/epc-captures",
    status_code=201,
    dependencies=[Depends(require_user)],
)
def create_capture(payload: CaptureIn, session: Session = Depends(get_session)):
    seen: set[str] = set()
    epcs: list[str] = []
    for raw in payload.epcs:
        epc = (raw or "").strip().upper()
        if epc and epc not in seen:
            seen.add(epc)
            epcs.append(epc)
    if not epcs:
        raise HTTPException(422, "No usable EPCs in the sweep.")
    row = EpcCapture(
        device=(payload.device or "").strip() or None,
        note=(payload.note or "").strip() or None,
        batch_id=payload.batch_id,
        epc_count=len(epcs),
        epcs="\n".join(epcs),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    # A sweep is a shelf physically read — old tags heard now are stock
    # discovered now, which may clear 1-left checks.
    oneleft.kick("C72 sweep", payload.device)
    return row.as_dict()


@app.get("/api/epc-captures", dependencies=[Depends(require_user)])
def list_captures(limit: int = 20, session: Session = Depends(get_session)):
    rows = session.scalars(
        select(EpcCapture).order_by(EpcCapture.id.desc()).limit(min(limit, 100))
    ).all()
    return {"count": len(rows), "captures": [r.as_dict() for r in rows]}


@app.get("/api/epc-captures/latest", dependencies=[Depends(require_user)])
def latest_capture(session: Session = Depends(get_session)):
    row = session.scalar(
        select(EpcCapture).order_by(EpcCapture.id.desc()).limit(1)
    )
    if row is None:
        raise HTTPException(404, "No sweeps received yet.")
    return row.as_dict(with_epcs=True)


@app.get(
    "/api/epc-captures/latest-summary",
    dependencies=[Depends(require_user)],
)
def latest_capture_summary(session: Session = Depends(get_session)):
    """The bulk-link waiting chip's feed (Nick, 2026-08-26): how many
    tags the newest C72 sweep holds that are NOT tied to anything yet -
    counted exactly the way batch tagging counts a sweep (tags already
    assigned never count). Always 200; exists=False when no sweep has
    ever arrived. Registered BEFORE /{capture_id} so the path resolves."""
    row = session.scalar(
        select(EpcCapture).order_by(EpcCapture.id.desc()).limit(1)
    )
    if row is None:
        return {"ok": True, "exists": False}
    seen: set = set()
    epcs: list[str] = []
    for raw in (row.epcs or "").split("\n"):
        epc = raw.strip().upper()
        if epc and epc not in seen:
            seen.add(epc)
            epcs.append(epc)
    taken: set = set()
    if epcs:
        taken = {
            (e or "").strip().upper()
            for e in session.scalars(
                select(RfidAssignment.rfid_id).where(
                    func.upper(RfidAssignment.rfid_id).in_(epcs)
                )
            )
        }
    age = None
    if row.created_at is not None:
        created = row.created_at
        # Azure SQL hands timestamps back tz-aware; sqlite naive.
        if created.tzinfo is not None:
            created = created.astimezone(timezone.utc).replace(tzinfo=None)
        age = max(0, int((datetime.utcnow() - created).total_seconds()))
    return {
        "ok": True,
        "exists": True,
        "id": row.id,
        "created_at": (
            row.created_at.isoformat() if row.created_at else None
        ),
        "age_seconds": age,
        "device": row.device,
        "epc_count": len(epcs),
        "untagged": sum(1 for e in epcs if e not in taken),
    }


@app.get("/api/epc-captures/{capture_id}", dependencies=[Depends(require_user)])
def get_capture(capture_id: int, session: Session = Depends(get_session)):
    row = session.get(EpcCapture, capture_id)
    if row is None:
        raise HTTPException(404, "No such sweep.")
    return row.as_dict(with_epcs=True)


# ------------------------------------------------------- planner (read-only) ---
# The TC-Planner bridge is STRICTLY read-only: these endpoints answer "is
# this product on an open purchase order" for scan-time hints. Nothing
# here (or anywhere in this app) files receipts, changes PO statuses, or
# pushes stock — that future feature is Steve's TODO #2 and will be its
# own explicitly gated, operator-confirmed flow.


@app.get("/api/planner/status", dependencies=[Depends(require_user)])
def planner_status(operator: str | None = None):
    # `operator` rides the "Who's scanning?" pick: with a matching entry
    # in PLANNER_USER_TOKENS the planner sees THAT person, otherwise the
    # dedicated RFID identity — status echoes who it resolved to.
    return planner.health(operator=operator)


@app.get("/api/planner/on-order/{sku}", dependencies=[Depends(require_user)])
def planner_on_order(sku: str, operator: str | None = None):
    # Always 200: planner hints are decoration on the scan flow, so an
    # outage answers ok=False instead of failing the caller.
    return planner.on_order_for_sku(sku, operator=operator)


# --------------------------------------------------- 1-left stock checks ---
# Bridge to the Inventory Verification dashboard (see app/oneleft.py for
# the full contract: read pending, confirm, re-queue — nothing else).
# Every action, auto or manual, leaves an OneLeftCheck receipt that
# History renders with the evidence.

class OneLeftActIn(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    worker: str | None = Field(default=None, max_length=100)
    # What the operator actually counted on the shelf (the confirm
    # window's number). None = confirmed without entering a count.
    counted: int | None = Field(default=None, ge=0, le=100000)


class OneLeftAutoIn(BaseModel):
    on: bool
    worker: str | None = Field(default=None, max_length=100)


class OneLeftScanIn(BaseModel):
    worker: str | None = Field(default=None, max_length=100)


@app.get("/api/oneleft/board", dependencies=[Depends(require_user)])
def oneleft_board(session: Session = Depends(get_session)):
    """Their pending queue joined against RFID evidence, plus recent
    receipts. Pure read — building the board never confirms anything."""
    base = {
        "configured": oneleft.configured(),
        "mode": config.ONELEFT_MODE,
        "auto": oneleft.auto_enabled(session),
        "ok": False,
        "count": 0,
        "items": [],
        "receipts": [
            r.as_dict() for r in session.scalars(
                select(OneLeftCheck).order_by(OneLeftCheck.id.desc())
                .limit(30)
            )
        ],
    }
    if not oneleft.configured():
        return base
    pending = oneleft.get_pending()
    if not pending["ok"]:
        return {**base, "error": pending.get("error", "no answer")}
    items = oneleft.build_board(session, pending["items"])
    counts: dict[str, int] = {}
    for row in items:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    return {
        **base,
        "ok": True,
        "count": len(items),
        "items": items,
        "verdicts": counts,
    }


@app.get("/api/oneleft/stock/{sku}", dependencies=[Depends(require_user)])
def oneleft_stock(sku: str):
    """Live Shopify quantity breakdown for the confirm window —
    unavailable / committed / available / on-hand, read-only."""
    try:
        breakdown = shopify.get_quantity_breakdown(sku.strip())
    except Exception as error:  # noqa: BLE001 — window degrades gracefully
        return {"ok": False, "error": str(error)[:200]}
    if breakdown is None:
        return {"ok": False, "error": "SKU not found in Shopify."}
    return {"ok": True, "sku": sku.strip(), **breakdown}


@app.post("/api/oneleft/confirm", dependencies=[Depends(require_user)])
def oneleft_confirm(
    payload: OneLeftActIn, session: Session = Depends(get_session)
):
    """Operator-driven confirm of ONE check — the same call their UI's
    Verify button makes. The operator's judgment, not the evidence rule,
    is the authority here; the receipt records both."""
    if not oneleft.can_confirm():
        raise HTTPException(
            409, "Confirming is disabled (ONELEFT_MODE is not 'confirm')."
        )
    sku = payload.sku.strip()
    employee = oneleft.employee_for(payload.worker)
    # Whatever evidence exists rides along on the receipt.
    pending = oneleft.get_pending()
    row = next(
        (r for r in oneleft.build_board(session, pending["items"])
         if r["sku"].upper() == sku.upper()),
        None,
    ) if pending["ok"] else None
    try:
        oneleft.confirm(sku, employee)
        ok, error = True, None
    except Exception as exc:  # noqa: BLE001 — recorded, surfaced, not raised
        ok, error = False, str(exc)[:300]
    oneleft.invalidate_pending_cache()
    evidence_parts = (row or {}).get("evidence") or []
    if payload.counted is not None:
        evidence_parts = [f"counted {payload.counted} at confirm",
                          *evidence_parts]
    session.add(OneLeftCheck(
        sku=sku,
        product_title=(row or {}).get("product_title"),
        vendor=(row or {}).get("vendor"),
        claimed=(row or {}).get("claimed"),
        evidence_units=(row or {}).get("evidence_units") or 0,
        evidence="; ".join(evidence_parts)[:500] or None,
        action="manual",
        employee=employee,
        operator=(payload.worker or "").strip()[:100] or None,
        ok=ok,
        error=error,
    ))
    # A counted number BELOW Shopify's on-hand can't be written down from
    # here (hard rule: nothing in this app lowers stock) — file the
    # discrepancy for Review instead of losing it.
    if ok and payload.counted is not None:
        try:
            live = shopify.get_quantity_breakdown(sku)
        except Exception:  # noqa: BLE001 — the check is decoration
            live = None
        if live is not None and payload.counted < live["on_hand"]:
            session.add(ReviewTask(
                category="inventory-check",
                sku=sku,
                product_title=(row or {}).get("product_title"),
                detail=(f"1-left check: {payload.counted} unit(s) counted "
                        f"but Shopify on-hand is {live['on_hand']}. "
                        f"Lowering stock stays a Shopify-admin job — "
                        f"recount or fix it there."),
                created_by=(payload.worker or "").strip()[:100] or None,
            ))
    session.commit()
    if not ok:
        raise HTTPException(502, f"The dashboard refused the confirm: {error}")
    return {"ok": True, "sku": sku, "employee": employee}


@app.post("/api/oneleft/requeue", dependencies=[Depends(require_user)])
def oneleft_requeue(
    payload: OneLeftActIn, session: Session = Depends(get_session)
):
    """Put a SKU back on their pending queue — the undo for a confirm
    that shouldn't have happened. Their import endpoint re-fetches the
    product's details from Shopify itself."""
    if not oneleft.can_confirm():
        raise HTTPException(
            409, "The bridge is read-only (ONELEFT_MODE is not 'confirm')."
        )
    sku = payload.sku.strip()
    try:
        oneleft.requeue(sku)
        ok, error = True, None
    except Exception as exc:  # noqa: BLE001
        ok, error = False, str(exc)[:300]
    oneleft.invalidate_pending_cache()
    session.add(OneLeftCheck(
        sku=sku,
        action="requeue",
        operator=(payload.worker or "").strip()[:100] or None,
        ok=ok,
        error=error,
    ))
    session.commit()
    if not ok:
        raise HTTPException(502, f"The dashboard refused the re-queue: {error}")
    return {"ok": True, "sku": sku}


@app.post("/api/oneleft/scan", dependencies=[Depends(require_user)])
def oneleft_scan(payload: OneLeftScanIn):
    """Run the auto pass right now (the panel's button). Same rules as
    the background kicks — only evidence-complete checks confirm."""
    if not oneleft.can_confirm():
        raise HTTPException(
            409, "Confirming is disabled (ONELEFT_MODE is not 'confirm')."
        )
    return oneleft.scan_and_confirm("manual scan", payload.worker)


@app.post("/api/oneleft/auto", dependencies=[Depends(require_user)])
def oneleft_auto(
    payload: OneLeftAutoIn, session: Session = Depends(get_session)
):
    """Pause/resume auto-confirms — server-stored so every terminal and
    every background kick honours it immediately."""
    row = session.get(AppSetting, oneleft.AUTO_SETTING_KEY)
    value = "on" if payload.on else "off"
    if row is None:
        row = AppSetting(key=oneleft.AUTO_SETTING_KEY, value=value)
        session.add(row)
    else:
        row.value = value
    row.updated_by = (payload.worker or "").strip()[:100] or None
    session.add(BarcodeChange(
        changed_field="oneleft",
        old_barcode=f"auto-confirm {'off' if payload.on else 'on'}",
        new_barcode=f"auto-confirm {value}",
        changed_by=payload.worker,
    ))
    session.commit()
    return {"ok": True, "auto": payload.on}


# ---------------------------------------------------------- audit sessions ---
# A named, resumable audit: bundle a scope (bins, or a slice of the
# 1-left queue), walk it across days and people, finish when everything
# is accounted for. Local records only — sessions never write anywhere.

class AuditSessionIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["bins", "oneleft"]
    # bins kind: explicit bins and/or every bin-map bin starting `rack`.
    bins: list[str] = Field(default_factory=list)
    rack: str | None = Field(default=None, max_length=100)
    # oneleft kind: explicit SKUs, or (default) every currently-pending
    # check, optionally narrowed to one vendor.
    skus: list[str] = Field(default_factory=list)
    vendor: str | None = Field(default=None, max_length=150)
    worker: str | None = Field(default=None, max_length=100)


class AuditItemDoneIn(BaseModel):
    done: bool = True
    note: str | None = Field(default=None, max_length=255)
    worker: str | None = Field(default=None, max_length=100)


class AuditFinishIn(BaseModel):
    worker: str | None = Field(default=None, max_length=100)


def _session_progress(session: Session, s: AuditSession) -> dict:
    items = session.scalars(
        select(AuditSessionItem)
        .where(AuditSessionItem.session_id == s.id)
        .order_by(AuditSessionItem.id)
    ).all()
    # 1-left items settle themselves: a dashboard confirm for the SKU
    # that landed after the session opened counts as done.
    if s.kind == "oneleft":
        open_keys = {i.key.upper() for i in items if not i.done}
        if open_keys:
            confirmed_since = {
                oc.sku.strip().upper(): oc
                for oc in session.scalars(
                    select(OneLeftCheck).where(
                        func.upper(OneLeftCheck.sku).in_(sorted(open_keys)),
                        OneLeftCheck.action.in_(["auto", "manual"]),
                        OneLeftCheck.ok == True,  # noqa: E712
                        # 1s slack: sqlite compares datetimes as TEXT,
                        # and the bound param's ".000000" microseconds
                        # would sort a same-second confirm before it.
                        OneLeftCheck.created_at
                        >= s.created_at - timedelta(seconds=1),
                    )
                )
            }
            changed = False
            for i in items:
                oc = confirmed_since.get(i.key.upper())
                if oc is not None and not i.done:
                    i.done = True
                    i.done_at = oc.created_at
                    i.done_by = oc.operator or oc.employee
                    i.note = (i.note or "confirmed on the dashboard")[:255]
                    changed = True
            if changed:
                session.commit()
    done = sum(1 for i in items if i.done)
    return {
        **s.as_dict(),
        "total": len(items),
        "done": done,
        "items": [i.as_dict() for i in items],
    }


@app.get("/api/audit-sessions", dependencies=[Depends(require_user)])
def list_audit_sessions(
    status: str = "open", session: Session = Depends(get_session)
):
    """The session index (newest first). status=open|done|all; item
    lists ride along so one fetch draws the whole board."""
    q = select(AuditSession).order_by(AuditSession.id.desc())
    if status != "all":
        wanted = ["open"] if status == "open" else ["done", "abandoned"]
        q = q.where(AuditSession.status.in_(wanted))
    return {
        "sessions": [
            _session_progress(session, s)
            for s in session.scalars(q.limit(50))
        ]
    }


@app.post(
    "/api/audit-sessions", status_code=201,
    dependencies=[Depends(require_user)],
)
def create_audit_session(
    payload: AuditSessionIn, session: Session = Depends(get_session)
):
    """Open a session and seed its scope. Bins can come typed or as a
    rack prefix; 1-left scope snapshots the dashboard's CURRENT pending
    queue (optionally one vendor) so the goalposts can't move mid-walk."""
    keys: list[tuple[str, str | None]] = []
    if payload.kind == "bins":
        wanted = {b.strip() for b in payload.bins if b.strip()}
        if payload.rack and payload.rack.strip():
            prefix = payload.rack.strip().lower()
            for (bin_name,) in session.execute(
                select(BinMapEntry.bin).distinct()
            ):
                if (bin_name or "").strip().lower().startswith(prefix):
                    wanted.add(bin_name.strip())
        keys = [(b, None) for b in sorted(wanted)]
        if not keys:
            raise HTTPException(
                422, "No bins matched. Name bins or give a rack prefix."
            )
    else:
        if payload.skus:
            keys = [(s.strip(), None) for s in payload.skus if s.strip()]
        else:
            pending = oneleft.get_pending()
            if not pending["ok"]:
                raise HTTPException(
                    502,
                    "The 1-left dashboard didn't answer, so its queue "
                    "can't seed a session right now.",
                )
            vendor = (payload.vendor or "").strip().lower()
            for item in pending["items"]:
                if vendor and (item.get("vendor") or "").strip().lower() != vendor:
                    continue
                sku = (item.get("sku") or "").strip()
                if sku:
                    keys.append((sku, (item.get("product_title") or "")[:255]
                                 or None))
        if not keys:
            raise HTTPException(422, "Nothing matched that 1-left scope.")
        seen: set[str] = set()
        deduped = []
        for k, label in keys:
            if k.upper() not in seen:
                seen.add(k.upper())
                deduped.append((k, label))
        keys = deduped

    s = AuditSession(
        name=payload.name.strip(),
        kind=payload.kind,
        created_by=payload.worker,
    )
    session.add(s)
    session.flush()
    for key, label in keys:
        session.add(AuditSessionItem(session_id=s.id, key=key, label=label))
    session.commit()
    return _session_progress(session, s)


@app.post(
    "/api/audit-sessions/{sid}/items/{item_id}/done",
    dependencies=[Depends(require_user)],
)
def audit_item_done(
    sid: int, item_id: int, payload: AuditItemDoneIn,
    session: Session = Depends(get_session),
):
    item = session.get(AuditSessionItem, item_id)
    if item is None or item.session_id != sid:
        raise HTTPException(404, "No such item in that session.")
    item.done = payload.done
    item.done_at = datetime.now(timezone.utc) if payload.done else None
    item.done_by = (payload.worker or "").strip()[:100] or None
    if payload.note is not None:
        item.note = payload.note.strip()[:255] or None
    session.commit()
    parent = session.get(AuditSession, sid)
    return _session_progress(session, parent)


@app.post(
    "/api/audit-sessions/{sid}/finish",
    dependencies=[Depends(require_user)],
)
def finish_audit_session(
    sid: int, payload: AuditFinishIn, session: Session = Depends(get_session)
):
    """Close the session (done). Finishing with open items is allowed —
    the confirm dialog on the web side names how many are left."""
    s = session.get(AuditSession, sid)
    if s is None:
        raise HTTPException(404, "No such audit session.")
    if s.status != "open":
        raise HTTPException(409, f"Session is already {s.status}.")
    s.status = "done"
    s.completed_at = datetime.now(timezone.utc)
    s.completed_by = (payload.worker or "").strip()[:100] or None
    session.commit()
    return _session_progress(session, s)


@app.post(
    "/api/audit-sessions/{sid}/abandon",
    dependencies=[Depends(require_user)],
)
def abandon_audit_session(
    sid: int, payload: AuditFinishIn, session: Session = Depends(get_session)
):
    s = session.get(AuditSession, sid)
    if s is None:
        raise HTTPException(404, "No such audit session.")
    if s.status != "open":
        raise HTTPException(409, f"Session is already {s.status}.")
    s.status = "abandoned"
    s.completed_at = datetime.now(timezone.utc)
    s.completed_by = (payload.worker or "").strip()[:100] or None
    session.commit()
    return _session_progress(session, s)


# ------------------------------------------------------------ label names ---
class LabelNameIn(BaseModel):
    label_name: str = Field(default="", max_length=76)
    placement: str = Field(default="header", pattern="^(header|sku|both)$")
    updated_by: str | None = Field(default=None, max_length=100)
    # Two-box style (the shared label editor): when either field is
    # present, the top/centre pair wins over label_name+placement.
    top_text: str | None = Field(default=None, max_length=76)
    sku_line: str | None = Field(default=None, max_length=56)


@app.get("/api/label-names/{sku}", dependencies=[Depends(require_user)])
def get_label_name(sku: str, session: Session = Depends(get_session)):
    """What the preferred label for this SKU currently is — lets the fix-
    and-reprint dialog show the operator what they're correcting."""
    row = session.get(LabelName, sku.strip())
    if row is None:
        return {
            "sku": sku.strip(),
            "label_name": None,
            "placement": "header",
            "sku_text": None,
        }
    return {
        "sku": row.sku,
        "label_name": row.label_name,
        "placement": row.placement or "header",
        "sku_text": row.sku_text,
    }


@app.put("/api/label-names/{sku}", dependencies=[Depends(require_user)])
def set_label_name(
    sku: str, payload: LabelNameIn, session: Session = Depends(get_session)
):
    """Set (or clear, with a blank name) the preferred label header for a
    non-serialized product. Local record only — labels pick it up on the
    next print; nothing in Shopify changes."""
    sku = sku.strip()
    if not sku:
        raise HTTPException(422, "SKU required.")
    # Two-box style from the shared label editor.
    if payload.top_text is not None or payload.sku_line is not None:
        _save_two_line_label(
            session, sku, payload.top_text, payload.sku_line,
            payload.updated_by,
        )
        session.commit()
        row = session.get(LabelName, sku)
        return {
            "sku": sku,
            "label_name": row.label_name if row else None,
            "placement": row.placement if row else "header",
            "sku_text": row.sku_text if row else None,
        }
    name = payload.label_name.strip()
    row = session.get(LabelName, sku)
    if not name:
        if row is not None:
            session.delete(row)
            session.commit()
        return {"sku": sku, "label_name": None}
    if row is None:
        row = LabelName(sku=sku)
        session.add(row)
    row.label_name = name
    row.placement = payload.placement
    # This API speaks the single-text model; a leftover two-line centre
    # text would silently override the placement being saved here.
    row.sku_text = None
    row.updated_by = payload.updated_by
    session.commit()
    return {"sku": sku, "label_name": name, "placement": row.placement}


# ------------------------------------------------- won't-RFID-scan flag ---
def _noscan_skus(session: Session) -> set[str]:
    """Upper-cased SKUs flagged "won't RFID scan": the applied tag never
    answers a sweep (packaging kills the read), so sweep-side checks must
    not treat silence as a problem."""
    return {
        (r.sku or "").strip().upper()
        for r in session.scalars(select(RfidIncompatible))
    }


class NoScanIn(BaseModel):
    incompatible: bool = True
    changed_by: str | None = Field(default=None, max_length=100)


@app.put(
    "/api/products/{sku}/rfid-incompatible",
    dependencies=[Depends(require_user)],
)
def set_rfid_incompatible(
    sku: str, payload: NoScanIn, session: Session = Depends(get_session)
):
    """Flag (or unflag) a product as "won't RFID scan". Labels still print
    and tags still pair — only the expectation that sweeps HEAR the tag
    changes. Every flip is logged as a change event."""
    sku = sku.strip()
    if not sku:
        raise HTTPException(422, "SKU required.")
    row = session.get(RfidIncompatible, sku)
    changed = False
    if payload.incompatible and row is None:
        session.add(RfidIncompatible(sku=sku, set_by=payload.changed_by))
        changed = True
    elif not payload.incompatible and row is not None:
        session.delete(row)
        changed = True
    if changed:
        session.add(BarcodeChange(
            sku=sku,
            changed_field="rfid-scan",
            old_barcode=(
                "scans normally" if payload.incompatible
                else "won't scan on box"
            ),
            new_barcode=(
                "won't scan on box" if payload.incompatible
                else "scans normally"
            ),
            changed_by=payload.changed_by,
        ))
    session.commit()
    return {"sku": sku, "rfid_incompatible": payload.incompatible}


@app.get(
    "/api/products/{sku}/rfid-incompatible",
    dependencies=[Depends(require_user)],
)
def get_rfid_incompatible(sku: str, session: Session = Depends(get_session)):
    row = session.get(RfidIncompatible, sku.strip())
    return {
        "sku": sku.strip(),
        "rfid_incompatible": row is not None,
        "set_by": row.set_by if row else None,
        "set_at": row.set_at.isoformat() if row and row.set_at else None,
    }


def _non_taggable_skus(session: Session) -> set[str]:
    """Upper-cased SKUs marked non-taggable (a big bin of thumbscrews):
    never seeded into batches, never labelled, skipped by audits and the
    tags-vs-on-hand arithmetic. A hand-paired tag may still exist as a
    bag marker for Locate."""
    return {
        (r.sku or "").strip().upper()
        for r in session.scalars(select(NonTaggable))
    }


class NonTaggableIn(BaseModel):
    non_taggable: bool = True
    changed_by: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=255)


@app.put(
    "/api/products/{sku}/non-taggable",
    dependencies=[Depends(require_user)],
)
def set_non_taggable(
    sku: str, payload: NonTaggableIn, session: Session = Depends(get_session)
):
    """Mark (or unmark) a product as not worth individual tags at all
    (Nick, 2026-08-25: 500 loose thumbscrews in one bin). Stronger than
    "won't RFID scan": the product is never seeded into batches, never
    gets labels, and audits skip it. Pairing ONE tag to it by hand as a
    bag marker still works, and Locate can find that marker; the tag
    carries no inventory meaning. Every flip is History-logged."""
    sku = sku.strip()
    if not sku:
        raise HTTPException(422, "SKU required.")
    row = session.get(NonTaggable, sku)
    changed = False
    if payload.non_taggable and row is None:
        session.add(NonTaggable(
            sku=sku, set_by=payload.changed_by, note=payload.note,
        ))
        changed = True
    elif not payload.non_taggable and row is not None:
        session.delete(row)
        changed = True
    if changed:
        session.add(BarcodeChange(
            sku=sku,
            changed_field="non-taggable",
            old_barcode=(
                "in the RFID system" if payload.non_taggable
                else "non-taggable"
            ),
            new_barcode=(
                "non-taggable" if payload.non_taggable
                else "in the RFID system"
            ),
            changed_by=payload.changed_by,
        ))
    session.commit()
    return {"sku": sku, "non_taggable": payload.non_taggable}


class ScanNoteIn(BaseModel):
    note: str = Field(default="", max_length=255)
    changed_by: str | None = Field(default=None, max_length=100)


@app.put(
    "/api/products/{sku}/scan-note", dependencies=[Depends(require_user)]
)
def put_scan_note(
    sku: str, payload: ScanNoteIn, session: Session = Depends(get_session)
):
    """Set (or clear, with an empty note) the product's scan note — the
    line that shows loudly on every scan, web and C72. History-logged."""
    sku = sku.strip()
    if not sku:
        raise HTTPException(422, "Provide a SKU.")
    note = payload.note.strip()
    row = session.get(ScanNote, sku)
    old = row.note if row else None
    if note:
        if row is None:
            row = ScanNote(sku=sku)
            session.add(row)
        row.note = note
        row.updated_by = (payload.changed_by or "").strip()[:100] or None
    elif row is not None:
        session.delete(row)
    if (old or "") != note:
        session.add(BarcodeChange(
            sku=sku,
            changed_field="scan-note",
            old_barcode=(old or "")[:64] or None,
            # new_barcode is NOT NULL — a cleared note records "(none)".
            new_barcode=note[:64] or "(none)",
            changed_by=(payload.changed_by or "").strip()[:100] or None,
        ))
    session.commit()
    return {"sku": sku, "scan_note": note or None}


# -------------------------------------------------------- product history ---
def _admin_product_url(pid: str | None) -> str | None:
    """The Shopify-admin page for a product gid. Built on the store's
    .myshopify.com domain - its /admin path redirects to the right
    admin.shopify.com handle without us knowing it."""
    m = re.search(r"/Product/(\d+)$", pid or "")
    if not m or not (config.SHOPIFY_STORE or "").strip():
        return None
    return (
        f"https://{config.SHOPIFY_STORE.strip()}"
        f"/admin/products/{m.group(1)}"
    )


@app.get("/api/product-history", dependencies=[Depends(require_user)])
def product_history(term: str, session: Session = Depends(get_session)):
    """One product's complete paper trail, newest first. Every event says
    whether it touched Shopify ("shopify": true) or only this system's
    records — count observations from batches are always local; nothing
    in the RFID system writes stock numbers to Shopify today."""
    term = term.strip()
    if not term:
        raise HTTPException(422, "Provide a SKU or barcode.")

    product = None
    try:
        product = product_by_barcode(term)
    except HTTPException as error:
        if error.status_code != 404:
            raise
    # The preview window's title links here (Nick, 2026-08-26).
    if product is not None:
        product["admin_url"] = _admin_product_url(
            product.get("shopify_product_id")
        )
    sku = (product.get("sku") if product else None) or term
    barcode = (product.get("barcode") if product else None) or term

    def iso(dt):
        return dt.isoformat() if dt else None

    events = []

    # Tag assignments: one event per pairing SESSION, not per tag. The
    # same worker tying tags to this product within 20 minutes of each
    # other is one piece of work — a batch pairing run, or a sweep plus
    # the straggler scanned right after — so it folds into one
    # expandable event (Nick, 2026-08-18).
    assigns = sorted(
        session.scalars(
            select(RfidAssignment).where(or_(
                RfidAssignment.sku == sku,
                RfidAssignment.barcode == barcode,
            ))
        ),
        key=lambda a: (a.assigned_at is None, a.assigned_at or a.id, a.id),
    )
    tag_sessions: list[dict] = []
    for a in assigns:
        prev = tag_sessions[-1] if tag_sessions else None
        if (
            prev is not None
            and (prev["worker"] or "") == (a.assigned_by or "")
            and prev["end"] is not None
            and a.assigned_at is not None
            and (a.assigned_at - prev["end"]).total_seconds() <= 1200
        ):
            prev["tags"].append(a)
            prev["end"] = a.assigned_at
        else:
            tag_sessions.append({
                "worker": a.assigned_by,
                "start": a.assigned_at,
                "end": a.assigned_at,
                "tags": [a],
            })
    for g in tag_sessions:
        tags = g["tags"]
        suspects = sum(1 for x in tags if x.suspect)
        bin_ = next((x.bin_location for x in tags if x.bin_location), None)
        if len(tags) > 1:
            events.append({
                "at": iso(g["start"]),
                "type": "tag-assigned",
                "worker": g["worker"],
                "detail": f"{len(tags)} × RFID tag"
                          + (f" · {suspects} SUSPECT" if suspects else "")
                          + (f" · bin {bin_}" if bin_ else ""),
                "epcs": [x.rfid_id for x in tags],
                "shopify": False,
            })
        else:
            a = tags[0]
            events.append({
                "at": iso(a.assigned_at),
                "type": "tag-assigned",
                "worker": a.assigned_by,
                "detail": f"EPC {a.rfid_id}"
                          + (" · SUSPECT read" if a.suspect else "")
                          + (f" · bin {a.bin_location}"
                             if a.bin_location else ""),
                "shopify": False,
            })

    change_types = {
        "barcode": "barcode-replaced", "sku": "sku-updated",
        "bin": "bin-updated", "bin-local": "tags-rebinned",
        "vendor": "vendor-updated",
        "recount": "manual-recount",
        "bundle-contents": "bundle-contents-set",
        "rfid-scan": "rfid-flag-changed",
        "non-taggable": "non-taggable",
        "batch-reprint": "batch-reprinted",
        "on-hand": "on-hand-updated", "on-hand-undo": "on-hand-undone",
        "on-hand-lower": "on-hand-lowered",
        "on-hand-lower-undo": "on-hand-lower-undone",
        "tagged-before": "already-tagged-set",
        "tag-unlinked": "tag-unlinked",
        "tag-released": "tag-released",
        "tag-reapplied": "tag-reapplied",
        "locate-list": "locate-list",
        "oneleft": "oneleft",
        "tag-sold": "tag-sold",
        "scan-note": "scan-note",
        "tag-retired": "tag-retired",
        "tag-unretired": "tag-unretired",
    }
    for c in session.scalars(
        select(BarcodeChange).where(or_(
            BarcodeChange.sku == sku,
            BarcodeChange.old_barcode == barcode,
            BarcodeChange.new_barcode == barcode,
        ))
    ):
        events.append({
            "at": iso(c.changed_at),
            "type": change_types.get(c.changed_field, c.changed_field),
            "worker": c.changed_by,
            "detail": f"{c.old_barcode or '(none)'} → {c.new_barcode}",
            # Barcode/SKU/bin flows write to the store; the RFID-scan flag,
            # locate list, tag-sold and scan notes are local markers only.
            "shopify": c.changed_field
            not in ("rfid-scan", "locate-list", "tag-sold", "scan-note",
                    "tag-retired", "tag-unretired", "tag-released",
                    "tag-reapplied"),
        })

    # What Shopify currently says the product's bin IS, and when we last
    # read it — the "Shopify side" row of a bin-mismatch timeline.
    for bm in session.scalars(
        select(BinMapEntry).where(
            func.upper(BinMapEntry.sku) == sku.upper()
        )
    ):
        events.append({
            "at": iso(bm.updated_at),
            "type": "shopify-bin-read",
            "worker": None,
            "detail": (bm.bin or "").strip()
                      + (f" · other: {bm.other_bins}" if bm.other_bins
                         else ""),
            "shopify": True,
        })

    # Fulfilled-order sales from the sold ledger: the events that lower
    # the EXPECTED tag count while the tags themselves stay on file.
    for sr in session.scalars(
        select(SoldRecord).where(func.upper(SoldRecord.sku) == sku.upper())
    ):
        events.append({
            "at": iso(sr.fulfilled_at or sr.created_at),
            "type": "order-sold",
            "worker": None,
            "detail": f"{sr.quantity} × sold on order "
                      f"{sr.order_name or sr.order_id}"
                      + (f" · {sr.retired} tag(s) since marked sold"
                         if sr.retired else ""),
            "shopify": True,
        })

    # Labels: one event per print RUN (same outcome, batch and requester,
    # chained ≤ 20 min apart) — "6 labels · batch #12", never six rows.
    # No EPC in the detail: the code is pre-generated for RFID-ENCODING
    # printers; on the barcode-only Zebra it never reaches the sticker,
    # so showing it read as random hex (Nick, 2026-08-18). The real tag
    # pairing appears as its own Assigned Tag event.
    job_types = {
        "done": "label-printed", "error": "label-failed",
        "canceled": "label-canceled", "pending": "label-queued",
        "printing": "label-printing",
    }
    jobs = sorted(
        session.scalars(
            select(PrintJob).where(or_(
                PrintJob.sku == sku, PrintJob.barcode == barcode
            ))
        ),
        key=lambda j: (
            (j.printed_at or j.created_at) is None,
            j.printed_at or j.created_at or j.id,
            j.id,
        ),
    )
    print_runs: list[dict] = []
    for j in jobs:
        at = j.printed_at or j.created_at
        prev = print_runs[-1] if print_runs else None
        if (
            prev is not None
            and prev["status"] == j.status
            and prev["batch_id"] == j.batch_id
            and (prev["worker"] or "") == (j.requested_by or "")
            and prev["end"] is not None
            and at is not None
            and (at - prev["end"]).total_seconds() <= 1200
        ):
            prev["n"] += 1
            prev["end"] = at
        else:
            print_runs.append({
                "status": j.status, "batch_id": j.batch_id,
                "worker": j.requested_by, "start": at, "end": at, "n": 1,
            })
    for r in print_runs:
        events.append({
            "at": iso(r["start"]),
            "type": job_types.get(r["status"], r["status"]),
            "worker": r["worker"],
            "detail": f"{r['n']} label{'s' if r['n'] != 1 else ''}"
                      + (f" · batch #{r['batch_id']}"
                         if r["batch_id"] else ""),
            "shopify": False,
        })

    for al in session.scalars(
        select(BarcodeAlias).where(or_(
            BarcodeAlias.sku == sku, BarcodeAlias.alias_barcode == barcode
        ))
    ):
        events.append({
            "at": iso(al.created_at),
            "type": "barcode-linked",
            "worker": al.created_by,
            "detail": f"{al.alias_barcode} → {al.barcode or al.sku}"
                      + (" (label line)" if al.kind == "label"
                         else " (old code kept after a fix)"
                         if al.kind == "legacy" else ""),
            "shopify": False,
        })

    kind_row = session.get(ProductKind, sku) if sku else None
    if kind_row is not None:
        events.append({
            "at": iso(kind_row.updated_at),
            "type": ("dropped-from-rfid" if kind_row.excluded
                     else "marked-bundle" if kind_row.kind == "bundle"
                     else "marked-multi-box"),
            "worker": kind_row.updated_by,
            "detail": (
                "no labels print for it, and it is kept out of new batches"
                if kind_row.excluded
                else "no labels print for it — its components carry the tags"
                if kind_row.kind == "bundle"
                else "one label per box"
            ),
            "shopify": False,
        })

    # Count observations: what the shelf actually held, per batch. These
    # never change Shopify stock — they are the record a future (explicit)
    # write-back would act on. "Counted" is the PHYSICAL box count
    # (scanned + already-tagged): re-tag batches used to render
    # "counted 0" because only qty_scanned was reported (Nick's EAF PRO,
    # 2026-08-24). The sweep's heard count rides along when one exists.
    count_rows = session.execute(
        select(BatchItem, Batch)
        .join(Batch, Batch.id == BatchItem.batch_id)
        .where(BatchItem.sku == sku)
    ).all()
    heard_by_batch: dict[int, int] = {}
    if count_rows:
        sku_epcs = {
            t.rfid_id.strip().upper()
            for t in session.scalars(
                select(RfidAssignment).where(
                    func.upper(RfidAssignment.sku) == sku.upper()
                )
            )
        } | {
            t.rfid_id.strip().upper()
            for t in session.scalars(
                select(RetiredTag).where(
                    func.upper(RetiredTag.sku) == sku.upper()
                )
            )
        }
        for _, batch in count_rows:
            if batch.id in heard_by_batch:
                continue
            cap = _latest_shelf_sweep(session, batch.id)
            if cap is None or not cap.epcs:
                continue
            heard_by_batch[batch.id] = sum(
                1 for e in cap.epcs.split("\n")
                if e.strip().upper() in sku_epcs
            )
    for item, batch in count_rows:
        detail = f"bin {batch.bin_name}: counted {_units_on_shelf(item)}"
        if item.tagged_before:
            detail += (
                f" ({item.qty_scanned or 0} new, "
                f"{item.tagged_before} already tagged)"
            )
        if item.expected_qty is not None:
            detail += f" (expected {item.expected_qty})"
        if batch.id in heard_by_batch:
            detail += f", sweep heard {heard_by_batch[batch.id]}"
        if item.paired_count:
            detail += f", {item.paired_count} tag(s) paired"
        detail += f" · batch #{batch.id} {batch.status}"
        events.append({
            "at": iso(batch.completed_at or batch.created_at),
            "type": "batch-counted",
            "worker": batch.created_by,
            "detail": detail,
            "shopify": False,  # counts are recorded, never pushed (yet)
        })

    for t in session.scalars(
        select(ReviewTask).where(ReviewTask.sku == sku)
    ):
        events.append({
            "at": iso(t.created_at),
            "type": "review-opened",
            "worker": t.created_by,
            "detail": f"[{t.category}] {t.detail}",
            "shopify": False,
        })
        if t.resolved_at:
            events.append({
                "at": iso(t.resolved_at),
                "type": f"review-{t.status}",
                "worker": t.resolved_by,
                "detail": f"[{t.category}]"
                          + (f" {t.resolution_note}"
                             if t.resolution_note else ""),
                "shopify": False,
            })

    for oc in session.scalars(
        select(OneLeftCheck).where(
            func.upper(OneLeftCheck.sku) == sku.upper()
        )
    ):
        events.append({
            "at": iso(oc.created_at),
            "type": "oneleft",
            "worker": oc.operator or oc.employee,
            "detail": _oneleft_detail(oc),
            # It moves a record on the dashboard system, never in Shopify.
            "shopify": False,
        })

    events.sort(key=lambda e: e["at"] or "", reverse=True)

    # The live tags themselves ride along (not just the count): the panel
    # lists them with a manual unpair for the tag-fell-off/bad-tag case
    # where the sticker is gone and a one-unit product can't be audited
    # (Nick, 2026-08-25). Null-safe: `sku == None` would match every
    # NULL-sku row.
    tag_conds = []
    if sku:
        tag_conds.append(func.upper(RfidAssignment.sku) == sku.upper())
    if barcode:
        tag_conds.append(RfidAssignment.barcode == barcode)
    live_tags = (
        session.scalars(
            select(RfidAssignment).where(or_(*tag_conds))
            .order_by(RfidAssignment.assigned_at)
        ).all()
        if tag_conds else []
    )
    tag_count = len(live_tags)
    image_url = (product or {}).get("image_url")
    if not image_url:
        image_url = session.scalar(
            select(BinMapEntry.image_url).where(BinMapEntry.sku == sku)
        )
    # Serialized brands: surface the preferred label name so the panel can
    # edit it (looking up by SKU — the serial fields on `product` only
    # populate when the scanned term was itself a serial).
    sp = session.scalar(
        select(SerialPrefix).where(SerialPrefix.sku == sku)
        .order_by(SerialPrefix.prefix)
    )
    custom = session.get(LabelName, sku)
    return {
        "product": product,
        "sku": sku,
        "barcode": barcode,
        "image_url": image_url,
        "tag_count": tag_count,
        "tags": [
            {
                "epc": t.rfid_id,
                "bin": t.bin_location,
                "assigned_at": iso(t.assigned_at),
                "assigned_by": t.assigned_by,
                "case_units": t.case_units,
            }
            for t in live_tags
        ],
        # Presumed-sold tombstones ride along too (Nick, 2026-08-26):
        # the tag list tells the whole story - live tags to unpair, and
        # the ones already judged sold with their retirement date.
        "sold_tags": [
            {
                "epc": r.rfid_id,
                "bin": r.bin_location,
                "retired_at": iso(r.retired_at),
                "retired_by": r.retired_by,
                "case_units": r.case_units,
            }
            for r in session.scalars(
                select(RetiredTag).where(
                    RetiredTag.kind == "presumed-sold",
                    func.upper(RetiredTag.sku) == (sku or "").upper(),
                ).order_by(RetiredTag.retired_at)
            )
        ] if sku else [],
        # The product's brand, for the Change vendor button.
        "vendor": session.scalar(
            select(BinMapEntry.vendor).where(
                func.upper(BinMapEntry.sku) == (sku or "").upper()
            )
        ) if sku else None,
        "on_hand": _expected_qty(session, sku),
        "serial_prefix": sp.prefix if sp else None,
        "serial_label": (
            (sp.label_name or _default_serial_label(sp.item_name))
            if sp else None
        ),
        "serial_label_saved": bool(sp and sp.label_name),
        # Non-serial products keep their preferred header here instead.
        "custom_label": custom.label_name if custom else None,
        "custom_placement": custom.placement if custom else "header",
        # Two-line customs: the centre line when it differs from the top.
        "custom_sku_text": custom.sku_text if custom else None,
        "rfid_incompatible": (
            session.get(RfidIncompatible, sku) is not None if sku else False
        ),
        "non_taggable": (
            session.get(NonTaggable, sku) is not None if sku else False
        ),
        # Current multi-box/bundle standing, so the panel can offer the undo.
        "product_kind": (
            {
                "kind": kind_row.kind,
                "excluded": bool(kind_row.excluded),
                "updated_by": kind_row.updated_by,
                "updated_at": iso(kind_row.updated_at),
            }
            if kind_row is not None
            else None
        ),
        "count": len(events),
        "events": events,
    }


# ---------------------------------------------------------------- history ---
@app.get("/api/history", dependencies=[Depends(require_user)])
def history(
    limit: int = 200,
    session: Session = Depends(get_session),
):
    """Unified append-only event feed across every app-owned table. Each
    source keeps its own audit row; this endpoint just merges them into one
    timeline (newest first). Nothing here is ever rewritten."""
    limit = min(limit, 500)
    events = []

    def iso(dt):
        return dt.isoformat() if dt else None

    # Sweep-assigned tags share one product, operator and timestamp — one
    # expandable event ("4 × RFID tag") beats four identical rows. Singles
    # keep their EPC in the detail as always.
    assign_groups: dict = {}
    assign_order: list = []
    for a in session.scalars(
        select(RfidAssignment)
        .order_by(RfidAssignment.id.desc()).limit(limit)
    ):
        key = (a.sku or "", a.assigned_by or "", iso(a.assigned_at))
        if key not in assign_groups:
            assign_groups[key] = []
            assign_order.append(key)
        assign_groups[key].append(a)
    for key in assign_order:
        group = assign_groups[key]
        a = group[0]
        # These rows are LIVE, which is exactly what makes the undo valid:
        # it releases the tags with a full snapshot kept, so the release
        # can itself be undone (re-apply) from its own History row. The
        # two can loop forever - both are manual (Nick, 2026-08-25).
        if len(group) == 1:
            events.append({
                "at": iso(a.assigned_at),
                "type": "tag-assigned",
                "worker": a.assigned_by,
                "sku": a.sku,
                "title": a.product_title,
                "detail": f"EPC {a.rfid_id}"
                          + (" · SUSPECT read" if a.suspect else "")
                          + (f" · bin {a.bin_location}"
                             if a.bin_location else ""),
                "undo": {"kind": "tag-assign", "sku": a.sku,
                         "epcs": [a.rfid_id]},
            })
            continue
        suspects = sum(1 for x in group if x.suspect)
        events.append({
            "at": iso(a.assigned_at),
            "type": "tag-assigned",
            "worker": a.assigned_by,
            "sku": a.sku,
            "title": a.product_title,
            "detail": f"{len(group)} × RFID tag (sweep)"
                      + (f" · {suspects} SUSPECT" if suspects else "")
                      + (f" · bin {a.bin_location}" if a.bin_location else ""),
            "epcs": [x.rfid_id for x in group],
            "undo": {"kind": "tag-assign", "sku": a.sku,
                     "epcs": [x.rfid_id for x in group]},
        })

    change_types = {
        "barcode": "barcode-replaced", "sku": "sku-updated",
        "bin": "bin-updated", "bin-local": "tags-rebinned",
        "vendor": "vendor-updated",
        "recount": "manual-recount",
        "bundle-contents": "bundle-contents-set",
        "rfid-scan": "rfid-flag-changed",
        "non-taggable": "non-taggable",
        "batch-reprint": "batch-reprinted",
        "print-stop": "printing-stopped",
        "print-resume": "printing-resumed",
        "on-hand": "on-hand-updated", "on-hand-undo": "on-hand-undone",
        "on-hand-lower": "on-hand-lowered",
        "on-hand-lower-undo": "on-hand-lower-undone",
        "tagged-before": "already-tagged-set",
        "tag-unlinked": "tag-unlinked",
        "tag-released": "tag-released",
        "tag-reapplied": "tag-reapplied",
        "locate-list": "locate-list",
        "oneleft": "oneleft",
        "tag-sold": "tag-sold",
        "scan-note": "scan-note",
        "tag-retired": "tag-retired",
        "tag-unretired": "tag-unretired",
        "backorder-debt": "backorder-noted",
        "backorder-debt-clear": "backorder-cleared",
    }
    # A sweep undo unlinks its tags with one shared timestamp — fold
    # those the same way sweep assigns fold. Release/re-apply rows (the
    # Assigned Tag undo chain) fold identically, keyed by their field so
    # the three families never merge into one event. Retirements fold
    # too (by kind as well — a presumed-sold pass and a dead-tag drop
    # stay separate events even in the same second).
    unlink_groups: dict = {}
    unlink_order: list = []
    retire_groups: dict = {}
    retire_order: list = []
    for c in session.scalars(
        select(BarcodeChange).order_by(BarcodeChange.id.desc()).limit(limit)
    ):
        if c.changed_field in ("tag-unlinked", "tag-released",
                               "tag-reapplied"):
            key = (c.changed_field, c.sku or "", c.changed_by or "",
                   iso(c.changed_at))
            if key not in unlink_groups:
                unlink_groups[key] = []
                unlink_order.append(key)
            unlink_groups[key].append(c)
            continue
        if c.changed_field == "tag-retired" and c.old_barcode:
            key = (c.sku or "", c.changed_by or "", iso(c.changed_at),
                   c.new_barcode or "")
            if key not in retire_groups:
                retire_groups[key] = []
                retire_order.append(key)
            retire_groups[key].append(c)
            continue
        event = {
            "at": iso(c.changed_at),
            "type": change_types.get(c.changed_field, c.changed_field),
            "worker": c.changed_by,
            "sku": c.sku,
            "title": c.product_title,
            "detail": f"{c.old_barcode or '(none)'} → {c.new_barcode}",
        }
        # On-hand corrections carry their undo: one click sets the number
        # back to what it was before the update (confirmed first).
        if c.changed_field == "on-hand":
            event["undo"] = {"kind": "on-hand", "change_id": c.id}
        # A lowering's undo restores everything the one click did:
        # on-hand back up, the retired tags live, the ledger units back.
        elif c.changed_field == "on-hand-lower":
            event["undo"] = {"kind": "on-hand-lower", "change_id": c.id}
        # Bin writes too (Nick, 2026-08-18): undo writes the OLD bin back
        # through the normal audited endpoint — a new History row, never
        # an erasure. Only offered when there was an old bin to go back to.
        elif c.changed_field == "bin" and c.sku and c.old_barcode:
            event["undo"] = {
                "kind": "bin",
                "sku": c.sku,
                "old_bin": c.old_barcode,
                "new_bin": c.new_barcode,
            }
        # A completed audit logs its location + one-line summary; the
        # generic "(none) → x" rendering would just add noise.
        elif c.changed_field == "bin-audited":
            event["detail"] = c.new_barcode
        # Backorder debt noted at a receiving close: Shopify's on-hand
        # runs N units behind the shelf until an operator write catches
        # it up. Undo clears the note by hand (the check may re-flag).
        elif c.changed_field == "backorder-debt":
            event["detail"] = (
                f"Shopify on-hand runs {c.new_barcode} unit(s) behind "
                f"the shelf (customer backorder filled by this delivery); "
                f"the expected-tag count carries the difference"
            )
            try:
                debt_id = int(c.old_barcode or "")
            except ValueError:
                debt_id = None
            if debt_id is not None and session.scalar(
                select(BackorderDebt).where(
                    BackorderDebt.id == debt_id,
                    BackorderDebt.cleared_at.is_(None),
                )
            ) is not None:
                event["undo"] = {
                    "kind": "backorder-debt",
                    "debt_id": debt_id,
                    "sku": c.sku,
                    "units": c.new_barcode,
                }
        elif c.changed_field == "backorder-debt-clear":
            event["detail"] = (
                f"backorder note cleared ({c.new_barcode} unit(s))"
            )
        elif c.changed_field == "tag-unretired" and c.old_barcode:
            event["detail"] = (
                f"EPC {c.old_barcode} restored (was {c.new_barcode})"
            )
        events.append(event)
    # Whether a release/re-apply row can still offer its undo depends on
    # where its tags sit NOW: a release is undoable while the snapshot
    # waits in the released table, a re-apply while the tags are live.
    chain_epcs = {
        (x.old_barcode or "").strip().upper()
        for k in unlink_order if k[0] != "tag-unlinked"
        for x in unlink_groups[k] if x.old_barcode
    }
    released_now: set = set()
    live_now: set = set()
    if chain_epcs:
        released_now = {
            (e or "").strip().upper()
            for e in session.scalars(
                select(ReleasedTag.rfid_id).where(
                    func.upper(ReleasedTag.rfid_id).in_(sorted(chain_epcs))
                )
            )
        }
        live_now = {
            (e or "").strip().upper()
            for e in session.scalars(
                select(RfidAssignment.rfid_id).where(
                    func.upper(RfidAssignment.rfid_id).in_(
                        sorted(chain_epcs)
                    )
                )
            )
        }
    chain_words = {
        "tag-unlinked": "unlinked (sweep undo)",
        "tag-released": "released",
        "tag-reapplied": "re-applied",
    }
    for key in unlink_order:
        field = key[0]
        group = unlink_groups[key]
        c = group[0]
        epcs = [x.old_barcode for x in group]
        undo = None
        if field == "tag-released":
            still = [e for e in epcs
                     if (e or "").strip().upper() in released_now]
            if still:
                undo = {"kind": "tag-release", "sku": c.sku, "epcs": still}
        elif field == "tag-reapplied":
            still = [e for e in epcs
                     if (e or "").strip().upper() in live_now]
            if still:
                undo = {"kind": "tag-assign", "sku": c.sku, "epcs": still}
        if len(group) == 1:
            event = {
                "at": iso(c.changed_at),
                "type": field,
                "worker": c.changed_by,
                "sku": c.sku,
                "title": c.product_title,
                "detail": (
                    f"{c.old_barcode or '(none)'} → {c.new_barcode}"
                    if field == "tag-unlinked"
                    else f"EPC {c.old_barcode or '?'} "
                         f"{chain_words[field].split(' ')[0]}"
                         + (f" · bin {c.new_barcode}"
                            if c.new_barcode else "")
                ),
            }
            if undo:
                event["undo"] = undo
            events.append(event)
            continue
        event = {
            "at": iso(c.changed_at),
            "type": field,
            "worker": c.changed_by,
            "sku": c.sku,
            "title": c.product_title,
            "detail": f"{len(group)} × RFID tag {chain_words[field]}",
            "epcs": epcs,
        }
        if undo:
            event["undo"] = undo
        events.append(event)

    # Retirements: singles keep the classic per-EPC row; a one-click
    # pass (a sweep cleanup, the sold-out button) folds into one event
    # whose undo restores the whole group. Unretire skips any EPC
    # re-used on a new box since, so the grouped undo stays safe.
    for key in retire_order:
        group = retire_groups[key]
        c = group[0]
        kind = c.new_barcode or "?"
        if len(group) == 1:
            events.append({
                "at": iso(c.changed_at),
                "type": "tag-retired",
                "worker": c.changed_by,
                "sku": c.sku,
                "title": c.product_title,
                "detail": f"EPC {c.old_barcode} retired ({kind})",
                "undo": {"kind": "tag-retired", "epc": c.old_barcode},
            })
            continue
        epcs = [x.old_barcode for x in group]
        events.append({
            "at": iso(c.changed_at),
            "type": "tag-retired",
            "worker": c.changed_by,
            "sku": c.sku,
            "title": c.product_title,
            "detail": f"{len(group)} × RFID tag retired ({kind})",
            "epcs": epcs,
            "undo": {"kind": "tag-retired", "epcs": epcs},
        })

    job_types = {
        "done": "label-printed", "error": "label-failed",
        "canceled": "label-canceled", "pending": "label-queued",
        "printing": "label-printing",
    }
    for j in session.scalars(
        select(PrintJob).order_by(PrintJob.id.desc()).limit(limit)
    ):
        events.append({
            "at": iso(j.printed_at or j.created_at),
            "type": job_types.get(j.status, j.status),
            "worker": j.requested_by,
            "sku": j.sku,
            "title": j.label_name or j.product_title,
            # No EPC here: it's pre-generated for RFID-encoding printers
            # and never reaches the sticker on the barcode-only Zebra —
            # it read as random hex (Nick, 2026-08-18).
            "detail": (f"batch #{j.batch_id}" if j.batch_id else "1 label")
                      + (f" · {j.error}" if j.error else ""),
        })

    for al in session.scalars(
        select(BarcodeAlias).order_by(BarcodeAlias.id.desc()).limit(limit)
    ):
        events.append({
            "at": iso(al.created_at),
            "type": "barcode-linked",
            "worker": al.created_by,
            "sku": al.sku,
            "title": al.product_title,
            "detail": f"{al.alias_barcode} → {al.barcode or al.sku}"
                      + (" (label line)" if al.kind == "label"
                         else " (old code kept after a fix)"
                         if al.kind == "legacy" else ""),
            # Alias rows are live (this event exists because the link still
            # does), so History can offer to undo it: DELETE the alias and
            # the scanned code stops resolving to this product.
            "undo": {
                "kind": "barcode-alias",
                "alias_barcode": al.alias_barcode,
            },
        })

    # Multi-box/bundle decisions. The row holds only the current answer, so
    # this is one event per product showing where it stands — and, like the
    # alias rows above, the row being live IS what makes it undoable.
    kind_titles: dict = {}
    for sku, title in session.execute(
        select(BinMapEntry.sku, BinMapEntry.product_title)
        .where(BinMapEntry.sku.isnot(None))
    ):
        if sku:
            kind_titles.setdefault(sku, title)
    for pk in session.scalars(
        select(ProductKind).order_by(ProductKind.updated_at.desc())
        .limit(limit)
    ):
        events.append({
            "at": iso(pk.updated_at),
            "type": ("dropped-from-rfid" if pk.excluded
                     else "marked-bundle" if pk.kind == "bundle"
                     else "marked-multi-box"),
            "worker": pk.updated_by,
            "sku": pk.sku,
            "title": kind_titles.get(pk.sku),
            "detail": (
                "no labels print for it, and it is kept out of new batches"
                if pk.excluded
                else "no labels print for it — its components carry the tags"
                if pk.kind == "bundle"
                else "one label per box"
            ),
            "undo": {"kind": "product-kind", "sku": pk.sku,
                     "excluded": pk.excluded},
        })

    # Tie counts for the batch events below. Pulled once and matched in
    # memory: ties made before assignments carried a batch_id can only be
    # recognised by bin + time window, and per-batch queries would be
    # dozens of round trips.
    tie_by_batch: dict = {}
    legacy_ties: list = []
    for bid, bin_loc, at in session.execute(
        select(RfidAssignment.batch_id, RfidAssignment.bin_location,
               RfidAssignment.assigned_at)
    ):
        if bid is not None:
            tie_by_batch[bid] = tie_by_batch.get(bid, 0) + 1
        else:
            legacy_ties.append(((bin_loc or "").strip().lower(), _aware(at)))

    def _ties_for(b: Batch) -> int:
        n = tie_by_batch.get(b.id, 0)
        bin_name = (b.bin_name or "").strip().lower()
        start = _aware(b.created_at)
        if not bin_name or start is None:
            return n
        end = _aware(b.completed_at) or datetime.now(timezone.utc)
        for loc, at in legacy_ties:
            if loc == bin_name and at is not None and start <= at <= end:
                n += 1
        return n

    for b in session.scalars(
        select(Batch).order_by(Batch.id.desc()).limit(limit)
    ):
        # Every batch event can release that batch's tag ties in one click.
        tie_count = _ties_for(b)
        undo = (
            {"kind": "batch-ties", "batch_id": b.id, "ties": tie_count}
            if tie_count else None
        )
        # A bin recorded as tagged from an audit never had a shelf walk:
        # one honest event, not the started/verified/completed triple
        # (which would all carry the same timestamp anyway).
        if b.ui_step == "audit-complete":
            events.append({
                "at": iso(b.completed_at or b.created_at),
                "type": "bin-marked-tagged",
                "worker": b.created_by,
                "sku": None,
                "title": f"Bin {b.bin_name}",
                "detail": f"Recorded as batch tagged from an audit sweep "
                          f"(#{b.id}) — from tags already on file; no "
                          f"shelf walk, nothing printed or written",
                "undo": undo,
            })
            continue
        # Receiving batches have no bin — History calls them what they are
        # (a shipment worked at the desk/pallet), never a bin's batch.
        if b.kind == "receiving":
            events.append({
                "at": iso(b.created_at),
                "type": "receiving-started",
                "worker": b.created_by,
                "sku": None,
                "title": "Receiving",
                "detail": f"Receiving #{b.id}"
                          + (f" · {tie_count} tag(s) tied"
                             if tie_count else ""),
                "undo": undo,
            })
            if b.completed_at:
                events.append({
                    "at": iso(b.completed_at),
                    "type": ("receiving-abandoned"
                             if b.status == "abandoned"
                             else "receiving-completed"),
                    "worker": b.created_by,
                    "sku": None,
                    "title": "Receiving",
                    "detail": f"Receiving #{b.id}"
                              + (f" · {tie_count} tag(s) tied"
                                 if tie_count else ""),
                    "undo": undo,
                })
            continue
        # A side trip is a few boxes carried to their real shelf, not a
        # check of that shelf — its events must never read as if the bin
        # was batch-verified.
        side = b.parent_batch_id is not None
        what = (
            f"Side trip #{b.id} (from batch #{b.parent_batch_id})"
            if side else f"Batch #{b.id}"
        )
        events.append({
            "at": iso(b.created_at),
            "type": "side-trip-started" if side else "batch-started",
            "worker": b.created_by,
            "sku": None,
            "title": f"Bin {b.bin_name}",
            "detail": what
                      + (f" · {tie_count} tag(s) tied" if tie_count else ""),
            "undo": undo,
        })
        if b.verified_at:
            events.append({
                "at": iso(b.verified_at),
                "type": "side-trip-verified" if side else "batch-verified",
                "worker": b.created_by,
                "sku": None,
                "title": f"Bin {b.bin_name}",
                "detail": f"{what} swept and checked"
                          + (f" · {tie_count} tag(s) tied" if tie_count
                             else ""),
                "undo": undo,
            })
        if b.completed_at:
            events.append({
                "at": iso(b.completed_at),
                "type": (
                    ("side-trip-abandoned" if side else "batch-abandoned")
                    if b.status == "abandoned"
                    else ("side-trip-completed" if side
                          else "batch-completed")
                ),
                "worker": b.created_by,
                "sku": None,
                "title": f"Bin {b.bin_name}",
                "detail": what
                          + (f" · {tie_count} tag(s) still tied"
                             if tie_count else ""),
                "undo": undo,
            })

    for t in session.scalars(
        select(ReviewTask).order_by(ReviewTask.id.desc()).limit(limit)
    ):
        events.append({
            "at": iso(t.created_at),
            "type": "review-opened",
            "worker": t.created_by,
            "sku": t.sku,
            "title": t.product_title,
            "detail": f"[{t.category}] {t.detail}",
        })
        if t.resolved_at:
            # System closures wear their own tag so the record never
            # reads as a person's click (Nick, 2026-08-26).
            auto = (
                t.status == "resolved"
                and (t.resolved_by or "") in AUTO_CLOSERS
            )
            events.append({
                "at": iso(t.resolved_at),
                "type": "review-autoclosed" if auto else f"review-{t.status}",
                "worker": t.resolved_by,
                "sku": t.sku,
                "title": t.product_title,
                "detail": f"[{t.category}]"
                          + (f" {t.resolution_note}" if t.resolution_note
                             else ""),
                # Undo = reopen: the task goes back to the inbox.
                "undo": {"kind": "review-reopen", "task_id": t.id},
            })

    # Dismissed live bin-mismatches: their "task" is synthetic, so the
    # dismissal itself is the record — undo deletes it and the entry is
    # back on the next Review fetch (if the disagreement still exists).
    for md in session.scalars(
        select(MismatchDismissal)
        .order_by(MismatchDismissal.id.desc()).limit(limit)
    ):
        events.append({
            "at": iso(md.created_at),
            "type": "review-dismissed",
            "worker": md.dismissed_by,
            "sku": md.sku,
            "title": None,
            "detail": (
                f"[bin-mismatch] tags at {md.tag_bin}, Shopify says "
                f"{md.shopify_bin} — stays quiet while both bins hold"
            ),
            "undo": {"kind": "mismatch-undismiss", "dismissal_id": md.id},
        })

    for oc in session.scalars(
        select(OneLeftCheck).order_by(OneLeftCheck.id.desc()).limit(limit)
    ):
        events.append({
            "at": iso(oc.created_at),
            "type": "oneleft",
            "worker": oc.operator or oc.employee,
            "sku": oc.sku,
            "title": oc.product_title,
            "detail": _oneleft_detail(oc),
        })

    for aud in session.scalars(
        select(AuditSession).order_by(AuditSession.id.desc()).limit(limit)
    ):
        what = ("bin walk" if aud.kind == "bins" else "1-left checks")
        events.append({
            "at": iso(aud.created_at),
            "type": "audit-session",
            "worker": aud.created_by,
            "sku": None,
            "title": aud.name,
            "detail": f"audit session #{aud.id} started ({what})",
        })
        if aud.completed_at:
            events.append({
                "at": iso(aud.completed_at),
                "type": "audit-session",
                "worker": aud.completed_by or aud.created_by,
                "sku": None,
                "title": aud.name,
                "detail": f"audit session #{aud.id} {aud.status}",
            })

    # ISO strings sort chronologically; string sort also avoids the
    # naive-vs-aware datetime comparison trap across DB backends.
    events.sort(key=lambda e: e["at"] or "", reverse=True)
    return {"count": len(events[:limit]), "events": events[:limit]}


def _oneleft_detail(oc: OneLeftCheck) -> str:
    """One line telling the whole story of a 1-left dashboard action."""
    if oc.action == "requeue":
        body = "re-queued on the 1-left dashboard"
    elif oc.action == "manual":
        body = f"1-left check confirmed on the dashboard (as {oc.employee})"
    else:
        body = (
            f"1-left check auto-cleared (as {oc.employee}) — evidence "
            f"{oc.evidence_units} unit(s) vs claimed "
            f"{oc.claimed if oc.claimed is not None else '?'}"
        )
    if oc.evidence:
        body += f" · {oc.evidence}"
    if not oc.ok:
        body += f" · FAILED: {oc.error or 'unknown error'}"
    return body[:500]
