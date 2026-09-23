"""Read-only ShipStation V1 API client (ssapi.shipstation.com).

The sold ledger's primary source since 2026-09-23 (Nick): a shipping
label created and not voided means a box PHYSICALLY left the building -
independent of Shopify's fulfillment bookkeeping, and it also captures
manual (non-Shopify) orders that Shopify never hears about.

Ground rules:
- READ-ONLY. This module must never grow a POST/PUT/DELETE call; the
  account can create real shipping labels and we will not be the ones
  who buy postage by accident.
- V1 timestamps are PACIFIC local time with no offset (ShipStation's
  documented quirk). Everything returned from here is converted to
  aware UTC datetimes; date-window request params are converted back.
- Rate limit is 40 requests/minute; _get() honors 429 + Retry-After.
"""
from __future__ import annotations

import base64
import logging
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

from app import config

logger = logging.getLogger("rfid.shipstation")

BASE = "https://ssapi.shipstation.com"
PAGE_SIZE = 500
# Rate-quote utility stores (marketplace "RateBrowser") hold no real
# shipments; anything from one would poison the ledger.
_EXCLUDED_MARKETPLACES = {"ratebrowser"}
_PACIFIC = ZoneInfo("America/Los_Angeles")

_stores_cache: dict = {"at": 0.0, "stores": None}
_STORES_TTL = 3600


def configured() -> bool:
    return config.check_shipstation_env()


def _auth_header() -> dict:
    raw = f"{config.SHIPSTATION_API_KEY}:{config.SHIPSTATION_API_SECRET}"
    token = base64.b64encode(raw.encode("ascii")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _get(path: str, params: dict | None = None) -> dict | list:
    """One GET with 429 handling. Raises RuntimeError on anything the
    caller can't fix by retrying later (auth, 4xx)."""
    for attempt in range(3):
        resp = requests.get(
            f"{BASE}{path}", params=params or {},
            headers=_auth_header(), timeout=45,
        )
        if resp.status_code == 429:
            wait = min(int(resp.headers.get("Retry-After") or 40), 90)
            logger.info("shipstation throttled; waiting %ss", wait)
            time.sleep(wait)
            continue
        if resp.status_code >= 400:
            raise RuntimeError(
                f"ShipStation {path} -> {resp.status_code}: "
                f"{resp.text[:200]}"
            )
        return resp.json()
    raise RuntimeError(f"ShipStation {path}: throttled three times over")


def _to_utc(value: str | None) -> datetime | None:
    """A V1 timestamp ('2026-09-24T08:15:32.5' in Pacific local, no
    offset) -> aware UTC."""
    if not value:
        return None
    raw = value.strip().split(".")[0]
    try:
        naive = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if naive.tzinfo is not None:
        return naive.astimezone(timezone.utc)
    return naive.replace(tzinfo=_PACIFIC).astimezone(timezone.utc)


def _to_pacific_param(dt: datetime) -> str:
    """An aware UTC datetime -> the Pacific-local string the V1 date
    filters expect."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_PACIFIC).strftime("%Y-%m-%d %H:%M:%S")


def get_stores() -> list[dict]:
    """[{store_id, name, marketplace}], cached for an hour."""
    now = time.time()
    if (_stores_cache["stores"] is not None
            and now - _stores_cache["at"] < _STORES_TTL):
        return _stores_cache["stores"]
    data = _get("/stores", {"showInactive": "false"})
    stores = [
        {
            "store_id": s.get("storeId"),
            "name": s.get("storeName"),
            "marketplace": s.get("marketplaceName"),
        }
        for s in (data or [])
    ]
    _stores_cache.update(at=now, stores=stores)
    return stores


def store_kinds() -> tuple[set, set]:
    """(shopify_store_ids, excluded_store_ids). Shipments from a store
    in neither set are real orders outside Shopify (manual store)."""
    shopify_ids, excluded = set(), set()
    for s in get_stores():
        mk = (s.get("marketplace") or "").strip().lower()
        if mk == "shopify":
            shopify_ids.add(s["store_id"])
        elif mk in _EXCLUDED_MARKETPLACES:
            excluded.add(s["store_id"])
    return shopify_ids, excluded


def _parse_shipment(sh: dict) -> dict | None:
    items = []
    for it in sh.get("shipmentItems") or []:
        sku = (it.get("sku") or "").strip()
        qty = it.get("quantity") or 0
        if qty > 0:
            items.append({"sku": sku, "qty": qty})
    return {
        "shipment_id": sh.get("shipmentId"),
        "ss_order_id": sh.get("orderId"),
        "order_number": (sh.get("orderNumber") or "").strip(),
        "store_id": (sh.get("advancedOptions") or {}).get("storeId"),
        "created_at": _to_utc(sh.get("createDate")),
        "ship_date": sh.get("shipDate"),
        "voided": bool(sh.get("voided")),
        "items": items,
    }


def _walk_shipments(params: dict, max_pages: int) -> list[dict]:
    out: list[dict] = []
    page = 1
    while page <= max_pages:
        data = _get("/shipments", {
            **params,
            "pageSize": PAGE_SIZE,
            "page": page,
            "includeShipmentItems": "true",
            "sortBy": "CreateDate",
            "sortDir": "ASC",
        })
        for sh in data.get("shipments") or []:
            parsed = _parse_shipment(sh)
            if parsed is not None:
                out.append(parsed)
        if page >= (data.get("pages") or 1):
            break
        page += 1
    return out


def get_shipments_since(created_since: datetime,
                        max_pages: int = 40) -> list[dict]:
    """Every shipment CREATED at/after `created_since` (UTC), oldest
    first, voided ones included (flagged) so callers can skip them."""
    return _walk_shipments(
        {"createDateStart": _to_pacific_param(created_since)}, max_pages
    )


def get_voids_since(voided_since: datetime,
                    max_pages: int = 10) -> list[dict]:
    """Every shipment VOIDED at/after `voided_since` (UTC) - the undo
    feed for labels cancelled after a sync recorded them."""
    return _walk_shipments(
        {"voidDateStart": _to_pacific_param(voided_since)}, max_pages
    )


def get_order_line_quantities(ss_order_id) -> dict[str, int]:
    """Upper SKU -> ordered quantity for one ShipStation order - the
    cap that stops overlapping multi-parcel item lists double-counting
    (a reprinted label lists the same items again)."""
    data = _get(f"/orders/{ss_order_id}")
    out: dict[str, int] = {}
    for it in (data or {}).get("items") or []:
        sku = (it.get("sku") or "").strip().upper()
        qty = it.get("quantity") or 0
        if sku and qty > 0:
            out[sku] = out.get(sku, 0) + qty
    return out
