# Unification roadmap — RFID + TC-Planner + 1-left into one service

Design written 2026-08-08 (Nick's ask, while he batch tags). Status
snapshot + phased plan. Update as phases land; ROADMAP.md points here.

## The systems

| System | Compute | Data | Auth | Source |
|---|---|---|---|---|
| RFID (this repo) | App Service `telcan-rfid` | Azure SQL `telcansql/TELCAN` (`rfid_*` tables) | STATION_KEY + Shopify session | GitHub `HatNJacket/RFID-Inventory-Project` |
| TC-Planner | App Service `tc-planner-app` (container) | **Same DB: `telcansql/TELCAN`** (stock_orders, product_velocity_cache, …) | Bearer TC_PLANNER_AUTH_TOKEN + per-user tokens | GitHub `HatNJacket/TC-Inventory-Planner` (2 commits — snapshot, not history; sync before modifying) |
| 1-left (Inventory Verification) | Function App `inventory-verification-func` + static site in `shopifyautomationsa/$web` | Unknown (own storage; webhook-driven) | **NONE — 10 anonymous endpoints** (only its Shopify webhooks are keyed) | Not in git. Recoverable: `function-releases` container in `shopifyautomationsa` holds the deploy packages (verified 2026-08-08 — the older "not recoverable" note in docs/inventory-verification-app.md predates this find) |

**The key fact (verified 2026-08-08):** planner and RFID already share ONE
database. Conglomeration is a code move, not a data migration.

## Connection status

- **RFID ↔ Planner: LIVE, read-only** (2026-08-08). On-order hints on
  Scan Station + receiving (web and C72 v3.30+), per-operator token
  attribution, /api/planner/status probe. Bridge fails soft; planner
  outage can never break a scan.
- **RFID ↔ 1-left: none.** Read-only join is buildable today (their
  api/pending is open). Writes blocked on auth; auth blocked on source;
  source blocked on one blob download.
- **Planner ↔ 1-left: none needed** — both converge through the RFID hub.

## Phase A — finish the bridges (each independently shippable)

1. **Planner receive filing** (designed, approved in principle):
   finishing a receiving batch matches counted SKUs to open-PO lines and
   offers per-PO, operator-confirmed POST /receive. Planner-local only —
   its Shopify push (apply-stock-update) stays untouched. Improvement to
   ride along: referenceDocumentUri on the planner's own
   inventoryAdjustQuantities so Shopify history names the PO.
2. **1-left read-only panel** in Review: pull api/pending, join by
   SKU/bin against rfid_assignments, split the queue into "RFID answers
   this — confirm?" vs "needs a walk". NEVER auto-confirm — tag counts
   are evidence, not truth; drift is why the app exists.
3. **Recover 1-left source**: download newest blob from
   `function-releases` in `shopifyautomationsa`, unzip, commit to a new
   repo. Then add STATION_KEY-style auth to the ten anonymous endpoints
   (app/auth.py pattern drops in) and update the one-file frontend.

## Phase B — converge identity and events

- One auth story: station key everywhere; planner keeps per-user tokens
  (already mirrored in PLANNER_USER_TOKENS); 1-left gains the key.
- Route the 1-left webhook queue into a TELCAN table so its items join
  the shared Review/History conventions (a "1-left" Review category with
  live tag context, resolve window included).

## Phase C — one service

- Mount the planner backend into the RFID FastAPI app (`/planner/*`),
  serve its React frontend from the same App Service. Code move only —
  the DB is already shared. Retire `tc-planner-app` after cutover.
- Retire the Function App last: its Shopify webhook becomes a route on
  the main app, its UI becomes the Review category, the anonymous API
  disappears.
- **Contracts that must survive every step:**
  - `shopify-jobs` Function App (`C:\Shopify-Azure\shopify-jobs`) calls
    the planner's /api/stock-orders/on-order-skus (More-on-the-Way
    tagger) — keep the path or migrate the caller in the same change.
  - The C72 APK talks to the RFID API — versioned, guns update manually.
  - Bundles.app writes `bundles_app.content` variant metafields — the
    bundle import reads them; nothing to do, just don't assume native
    Shopify bundles.

## Risks / open questions

- Planner repo is a 2-commit snapshot: adopt "commit before deploy"
  the moment planner code changes, or drift is guaranteed.
- Single DB = single blast radius: confirm TELCAN backup/PITR settings
  before Phase C concentrates everything on it.
- Planner has at least one live bug (GET /api/replenishment/summary
  500s: float + Decimal). Fix lands whenever planner code is first
  touched.
- 1-left package recovery assumed from container layout; verify the
  blob actually contains current source before building on it.
- Standing guardrails unchanged throughout: no auto on-hand writes
  until the whole store is tagged; increase-only; every write
  operator-confirmed, logged, undoable.
