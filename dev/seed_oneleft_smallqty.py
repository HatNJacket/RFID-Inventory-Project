"""One-off (2026-08-26, Nick's go): seed the 1-left dashboard's
direction-guard cache (inventory_last_small_qty.json in the
inventory-verification container) with every item currently holding
0-2 available units - exactly what the cache would contain had it
existed forever.

Why: the 0->1 "receiving never queues" guard (added 2026-08-18) can
only suppress a check when it knows the item's previous quantity, and
its cache only learns a value when stock MOVES while small. Items
dormant at 0 since before the cache existed fail open on their first
restock - today's eight iOptron checks. Seeding closes that cold-start
gap in one pass; record_small_qty maintains it from then on.

Usage (Shopify env must be set - pull telcan-rfid's app settings):

    py dev/seed_oneleft_smallqty.py <current-cache.json> <merged-out.json>

Reads the store via the RFID app's own client-credentials client
(read-only queries), MERGES into the given cache file (existing
entries always win - they are real, fresher observations), and writes
the merged JSON for upload. Blob download/backup/upload happen outside
this script via az so no storage keys live here.
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import shopify  # noqa: E402

QUERY = """
query smallQty($cursor: String) {
  productVariants(first: 250, after: $cursor) {
    nodes {
      sku
      inventoryQuantity
      inventoryItem { id }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("usage: py dev/seed_oneleft_smallqty.py <cache-in> <out>")
    with open(sys.argv[1], encoding="utf-8") as f:
        cache = json.load(f)
    existing = len(cache)

    now = datetime.now(timezone.utc).isoformat()
    added = 0
    scanned = 0
    band = 0
    cursor = None
    while True:
        data = shopify.query_shopify(QUERY, {"cursor": cursor})
        page = data["productVariants"]
        for v in page["nodes"]:
            scanned += 1
            qty = v.get("inventoryQuantity")
            item = v.get("inventoryItem") or {}
            gid = item.get("id") or ""
            if qty is None or not gid:
                continue
            if qty > 2:
                continue
            band += 1
            key = gid.rsplit("/", 1)[-1]
            # Real observations (the webhook wrote them) always win over
            # a snapshot taken now.
            if key in cache:
                continue
            cache[key] = {"q": qty, "t": now}
            added += 1
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]

    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(cache, f)
    print(f"variants scanned: {scanned}")
    print(f"in the 0-2 band:  {band}")
    print(f"already cached:   {existing}")
    print(f"seeded (new):     {added}")
    print(f"total entries:    {len(cache)}")


if __name__ == "__main__":
    main()
