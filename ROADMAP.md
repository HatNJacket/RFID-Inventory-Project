# RFID Inventory System — Roadmap

Source of truth for project status. Updated by Claude each working session.
Last updated: 2026-08-25.

## 📦 Receiving ↔ TC-Planner bridge, phase 1 (2026-08-25)

Nick's direction: connect receiving to the RFID system. First slice:

- **Planner "Print labels" button** (TC-Planner repo, Stock Orders
  order view, bottom-right after a receive is saved): sends the
  just-received items to the RFID app server-to-server (the planner
  backend holds the station key; POST /api/stock-orders/{id}/rfid-labels
  → RFID POST /api/receiving/prints). Deliberately does NOT navigate
  anywhere. Planner needs app settings RFID_STATION_KEY (+ optional
  RFID_APP_URL); bridge reports 503 until set. The planner Dockerfile
  is now multi-stage (frontend builds inside az acr build - no local
  Node needed, stale local bundles can't ship).
- **RFID /api/receiving/prints**: creates or reuses (per stock-order
  reference, carried in created_by as "TC-Planner · SO 42 · Vendor")
  an open receiving batch, adds received quantities to its rows, and
  queues labels exactly like a receiving PRINT pass: only unlabelled
  boxes, labels carry each item's HOME bin, no-bin items held out and
  named, unknown SKUs and non-taggable SKUs skipped and named. Nothing
  writes to Shopify - "Increase stock in Shopify" stays the planner's
  separate explicit step. Suite: test_recvbridge.py (12).
- **Print queue grouping**: jobs collapse under their batch. A batch-
  tagging run expands to its flat rows; a receiving batch gets a
  second level - one sub-group per product, then the individual labels
  - so one bad barcode/SKU/label is handled alone without blocking the
  shipment. Loose jobs (Scan Station, single reprints) stay flat.
  Fold state survives refreshes; the listing carries a `batches` map.
- **Receiving view rework (Batch tagging)**: for receiving batches the
  collect list reads tagged / labels printed per product (0/N until
  pairing; green when every printed label found its tag), the summary
  says "N products · X labels printed · Y tagged", clicking a product
  jumps straight to pairing it, the C72-shelf-baseline button is
  hidden, and the hint explains the planner-fed flow. Scanning still
  adds boxes the planner didn't know about; PRINT still queues only
  unlabelled boxes; Finish still files per-bin inventory checks.

## 🖨 Print truth: saved labels, walking order, re-align (2026-08-25)

- **Live print-run list + selective reprint (same day, out-of-labels
  incident)**: the Print step's poll used to STOP once every job
  reported done and went blind to requeues - "Printed 2/4" nonsense
  after reprints, no way to see what actually printed. It now polls
  for as long as the step is open, voided/canceled labels leave the
  math, and a per-label run list shows every job in walking order with
  its status. Tick the ones that printed wrong or never came out
  (shift-click selects a range - "everything after the roll ran dry"
  is two clicks) and "Reprint selected" voids just those (ghost tag
  records unlinked), queuing fresh replacements while the rest of the
  run is untouched. POST /api/batches/{id}/reprint-jobs, same guards
  and History row as reprint-all. Suite: test_reprintall.py (15).
- **Clear queue & reprint all (same day)**: the printer ran out of wax
  mid-run, printed 46 blanks, and marked every job done. New button in
  the Print step beside "Labels applied": POST
  /api/batches/{id}/reprint-all voids EVERY label queued for the batch
  (done/pending/error alike), deletes the auto-created tag records for
  the blank labels' EPCs (no ghost tags), and queues a fresh full set
  in the same walking order. Confirmation required (the old strip must
  be binned - a voided label on a box answers sweeps as an unknown
  tag); refuses on receiving batches, off the Print step, or once any
  pairing started. History logs "batch-reprinted".
  Suite: test_reprintall.py.

- **Scan Station prints now use the saved label lines** - the actual
  bug behind Nick's stale Softbag1 sticker: `/api/print-jobs` trusted
  whatever the client sent (nothing, for non-serials) while the batch
  flows consulted the store. The endpoint now fills label_name /
  placement / label_sku from the saved store whenever no explicit
  label_name arrives (serial confirms still win untouched); the product
  panel's print sends its custom centre line; Queue reprints carry the
  old job's placement + centre line. Suite: test_printorder.py.
- **Product card rework + label preview (⚙ setting)**: fixed two-column
  head - title (2-line clamp + ellipsis, variant folded in), "SKU /
  Barcode" line, "Tags on file / Shopify onhand / Bin" line (on-hand
  and saved label lines ride along on /api/products/tags), Edit
  product + Edit label buttons (no ellipsis), divider, then the print
  row. Every line renders with "—" fallbacks and the preview column is
  fixed-width, so a missing barcode never reflows anything. The
  preview shows exactly what the next print will say, including saved
  lines and live serial-name edits. "Live catalog" chip retired.
- **Labels queue in the operator's WALKING order**: new
  `rfid_batch_items.first_scanned_at` (ALTER run 2026-08-25) stamps
  each row's first physical scan (scan endpoint, manual qty bump from
  zero; merges keep the earlier stamp; split rows inherit). Both label
  builders iterate first-scanned-first, and the agent already claims
  by job id - the printed stack now matches the shelf walk.
- **Agent capability visibility (2026-08-25 evening, "still not
  re-aligning")**: nothing could say whether the warehouse PC had been
  restarted on the new agent code - the whole fix hinges on it. The
  agent now reports AGENT_VERSION on its command polls (the poll
  itself proves capability); /api/print-agent/status carries
  `realign_capable` + `agent_version`; the Queue pill shows "online ·
  v2" or "online · NEEDS UPDATE" (amber) and the Re-align button
  disables itself with update instructions while the agent is old; the
  batch Print step says it too. GET /api/print-agent/script serves the
  CURRENT print_agent.py from the app (station link opens it in a
  browser), so updating the warehouse PC is download -> replace ->
  restart task, no repo hunting. The agent also re-asserts ~JSB at the
  head of EVERY print burst now (a printer power cycle silently
  dropped the startup-only setting).
- **Zebra rip-drift, zero-waste fix (Nick picked option 2)**: the
  updated print_agent now sends ZPL `~JSB` (backfeed BEFORE printing)
  once at startup: the printer backfeeds and re-registers on its gap
  sensor at PRINT time, so tear-bar pull self-corrects with NO wasted
  labels - a clean rip costs nothing, a hard rip is absorbed before
  the first label prints. `--no-backfeed-fix` opts out if the printer
  dislikes it. The setting doesn't survive a printer power cycle
  (deliberately not written to the printer's saved config), so the
  manual "Re-align labels (feed one)" button stays as the fallback and
  re-asserts `~JSB` whenever pressed. Everything is INERT until the
  warehouse PC's agent scheduled task is restarted on the new code -
  nothing prints, nothing feeds, no tags at risk until then. If drift
  somehow persists after the restart, the remaining lever is a
  one-time gap calibration at the printer (feeds 2-3 blanks, once).

## Ⅱ Broken-character rescue + keep-the-old-code-linked (2026-08-25)

Nick's two ZWO edge cases (the unicode 'Ⅱ' roman numeral). C72 **3.59
(code 77)** on the update link.

- **Lookup rescue (server, both UIs + gun)**: a miss now retries with
  NFKC folding (a scan carrying the REAL 'Ⅱ' finds a record since fixed
  to plain "II" - the Nikon-T2-II broken-labels case) and with
  non-ASCII folded to '?' (finds a record the VARCHAR database mangled -
  the FD-M42-? case). Full chain re-entry, aliases included; the
  response carries `charfold_from` so a UI can say what the scan really
  said (groundwork for a one-tap "Recommended fix", future).
- **Overwrites keep broken values linked**: fixing a SKU/barcode whose
  OLD value was mojibake ('?' or non-ASCII) auto-creates a `legacy`
  alias for it, so already-printed labels keep scanning. Clean replaced
  values are never auto-linked (they might belong elsewhere). History
  shows "(old code kept after a fix)" with the normal unlink undo.
  Also fixed in passing: aliases anchored to a SKU now FOLLOW a SKU
  overwrite (they used to die quietly).
- **C72 3.59**: the odd-barcode picker's confirm is now "WRITE or
  LINK" (link = alias only, Shopify untouched, counts stay on the
  row); unresolved rows get a direct "LINK TO A PRODUCT…" button
  (type/scan the real SKU or barcode, confirm the product card, link +
  resolve in place) for when the right product isn't in the odd list;
  the CHANGE SKU / BARCODE note says broken replaced values stay
  linked automatically. Suite: dev/tests/test_charfix.py.
- **Web terminal parity (same day)**: the Check window's unresolved
  rescue offers "Link only - Shopify untouched" beside the overwrite in
  the odd-barcodes picker, plus a direct "Link scan to it" input (name
  the real SKU/barcode when the product isn't in the odd list); links
  resolve the row IN PLACE, counts kept. Overwrite flows (Check ident
  editor, edit window, unknown-barcode replace) say when the old broken
  value was kept linked; the Scan Station card says "Matched via
  broken-character fix (scan said ...)" on a folded hit. Also fixed the
  web's own dead "Use this listing" button (disabled on the current
  listing - it now reads "Keep this listing" and settles the flag, same
  as the C72 fix).
- **Future**: one-tap "Recommended fix" on bad-chars items - propose
  NFKC(live value) as the clean SKU/barcode, write it, and keep the
  broken original linked, all in one confirm (charfold_from is the
  hook).

## 🏷️ Label-line aliases, non-taggable products, audit truth (2026-08-25)

- **Label lines double as lookup aliases (ephemeral)**: saving a custom
  top line or SKU line through any label editor (product panel, reprint
  dialog, serial-prefix names) links that string to the product via the
  barcode-alias store (`kind='label'`; prod ALTER
  `dev/alter_add_alias_kind.py` run 2026-08-25). Typing what the
  sticker says finds the product, case-insensitively. Replaced lines
  lose their alias automatically (Nick's ZWO Softbag example is
  test-pinned); manual links are never touched; duplicate lines don't
  steal an existing link; real SKUs/barcodes always win because the
  resolver tries them first. History shows label links with
  "(label line)".
- **Scan Station product card**: new "Edit label…" button beside Edit
  product (opens the product panel's label editor), and Show tags moved
  to the right with its EPC list expanding BELOW the card instead of
  shoving Edit product sideways.
- **Non-taggable products**: new per-SKU flag (`rfid_non_taggable`
  table, ALTER run 2026-08-25; PUT /api/products/{sku}/non-taggable;
  toggle in the product panel options). For thumbscrew-bin products not
  worth individual tags: never seeded into batches, no labels, audits
  and mismatch tasks skip them (open mismatch tasks auto-close with a
  note). ONE hand-paired tag still works as a bag marker and Locate
  finds it; the marker never counts or orphan-flags. History-logged
  both ways.
- **Audit "Shopify vs RFID by bin" truth**: the sold-unretired
  adjustment is now WINDOWED to each SKU's tag-pool baseline (the
  unwindowed sum produced "3 in Shopify, 4 tags, difference of -19"
  from pre-tagging sales); SKUs with no live tags get no sold
  adjustment; bundles and dropped products leave the audit instead of
  scoring phantom drift; skip counts shown in the header note.
  Suites: test_labelalias.py, test_auditbins.py.

## 🔗 Edit window: Save under the inputs + Link SKU / Link Barcode (2026-08-25)

The edit product window (one window, both doors: Scan Station's Edit
button and the Inventory/product panel's docked editor) restacked its
SKU and barcode rows: each input now has its Save button underneath,
with a new Link button beside it. Link records the typed value as a
lookup ALIAS for the product through the existing barcode-alias store
(POST /api/barcode-aliases) - scanning or searching that code finds the
product everywhere (scan station, batch scans, the gun; the resolver
chain already consults aliases) while the real Shopify SKU/barcode stay
untouched. History logs the link with its existing one-click unlink.
After a successful link the input snaps back to the saved value so the
Save button doesn't stay armed with the alias. Both buttons grey out
when the box still equals the saved value. No server changes needed.

## 🏷️ Manual tag unpair from the Inventory tab (2026-08-25)

Nick's case: a tag fell off (and was bad anyway), the sticker is gone,
and with one unit there's no audit to run. The Inventory tab's product
panel (click a SKU) now shows "N live tag(s): view or unpair" - each
row lists EPC, bin, paired date/by, and an Unpair… button. Behind a
strong confirm (sticker physically gone/dead; a dead tag still ON a box
belongs in the check step's replace-tag flow), it retires the record as
`dead` via the existing retire endpoint: tombstone kept (future sweeps
name the EPC), no ledger consumption, History `tag-retired` row with
the one-click undo, Shopify untouched. `/api/product-history` now
returns the `tags` array (case-insensitive SKU match, null-safe).
Suite: dev/tests/test_unpair.py (12 checks).

## 📋 C72 3.58 list polish + collect-anchored pair target (2026-08-25)

Nick's field notes after running 3.57. C72 **3.58 (code 76)** built and
on the update link; web side deployed the same day.

- **Worst-first lists on the gun**: the pair list groups over-paired
  (red) rows on top, unfinished pairing in the middle by recency, green
  done rows at the bottom; the shelf-sweep results sort silent (red) →
  unheard (yellow) → noscan → match (green), and plain "N boxes get
  labels" rows (no earlier tags, nothing to check) no longer appear in
  that list at all. After SEND SWEEP on Verify, each report verdict is
  remembered per SKU and the verify list behind the report sorts AND
  tints the same way (red, yellow, no-verdict, green); the memory clears
  on new sweep / batch change. The verify REPORT dialog already sorted
  worst-first.
- **USE THIS LISTING dead button found**: the editor opens focused on
  the CURRENTLY selected listing and the button was disabled for it —
  a primary button that silently did nothing, which is exactly the
  "yes, keep this one" press that settles the several-listings flag.
  Now always enabled; reads "KEEP THIS LISTING" on the current one,
  posts the same reassign (server locks the choice), and the status
  line says the flag is settled.
- **One-letter loading overlay fixed**: the overlay text ("Checking
  the batch…") was measured inside a wrap-content box whose width came
  from the 64dp spinner only (match-parent children contribute just
  margins to a wrap-content LinearLayout), rendering one letter ("C").
  Explicit wrap-content params + full-width box.
- **Pull-to-refresh on the batch picker**: dragging the open-batch list
  down past the top (~100dp, at scroll top) refetches it — batches
  started on the web terminal show up without leaving the tab.
- **Web: "Fix label & reprint" → "Reprint label(s)"**, and the pair
  target is now COLLECT-anchored everywhere (`labels_total`, matching
  the C72): reprinting 3 of 5 labels reads 0/5 with "(3 label(s)
  printed)" as a note, never 0/3. The tracker also stopped using
  max(labels, paired) — 5 tags on 4 labels reads 5/4, not 5/5. If the
  collected count itself is wrong, fix it at Collect.

## 📐 Batch-tagging truth rework (2026-08-24, evening session)

Nick's field cases (batches 157/159, bin F1-2) drove a rework of how
counts and sales are reasoned about. All server math is test-pinned to
the REAL prod numbers (dev/tests/test_ledger_flow.py, 41 checks).

- **Windowed sales**: silent earlier tags are judged only against
  unretired sales fulfilled AFTER the tag pool's baseline (newest
  pairing in the bin, or a newer confirmed on-hand write). His AIRPLUS
  case now reads "2 tags silent, recorded sales account for all 2"
  instead of the false "beyond what recorded sales explain" (deleted).
  Reasons carry the decomposition: sales split, on-hand cross-check,
  and a sales-history-coverage note, all independently.
- **Ledger consumption**: presumed-sold retirements (verify, shelf
  resolve, and the new lower flow) consume matching sold-ledger units,
  windowed-first then oldest-first; `rfid_retired_tags.ledger_consumed`
  records it per tag (**prod ALTER: dev/alter_add_retired_ledger.py —
  run BEFORE deploying**); unretire/undo hands exactly that many back.
- **Collection is fact**: the sweep split now CAPS tagged_before at the
  collected box count (over-hearing reports `over_heard` on web + C72
  instead of inflating counts), and the C72 already-tagged dialog on
  re-tag bins defaults to 1, never the record count (3.56 / code 74).
- **Lower on-hand**: `POST /api/onhand-updates/lower`, feature
  `verify_onhand_lower` (**prod SHOPIFY_WRITE_MODE must gain it AFTER
  the deploy**). Gate: drop fully backed by windowed unretired sales;
  EPCs must be live, in-bin, unheard-if-swept. One confirmed click =
  lower + retire silent tags + consume ledger; one History undo
  reverses all three. Verify rows carry `can_lower`; the increase path
  stays increase-only.
- **Verify table rework**: Counted (renamed, split note on its own
  line), Paired column dropped, numbers centered, Set-to buttons are
  now small ⇪/⇩ icons in the Expected cell, Detected shows green ✓ on
  match, already-tagged chip removed (Counted's note covers it), the
  expanded row's sum is a read-only centered box.
- **Timeline fix**: `batch-counted` events report the physical count
  (`_units_on_shelf`) with the new/already-tagged split and the sweep's
  heard count ("counted 5" / "counted 4, sweep heard 5" instead of
  "counted 0 (expected 6)").
- **Sold-ledger backfill (same evening)**: `dev/backfill_orders.py` ran
  against prod for the full read_orders window (updated since Jun 26):
  301 rows added back to Jun 29 fulfillments. Going deeper than ~60
  days needs the `read_all_orders` scope (Nick declined for now).
  The mismatch-task math (`refresh_mismatch_tasks`) is now WINDOWED to
  each SKU's tag-pool baseline like everything else — the unwindowed
  version false-flagged 106 SKUs with pre-tagging sales the moment the
  backfill landed (windowed re-run: 102 closed, 9 real ones stand).
- **Manual retire button**: verify's expanded flagged row offers
  "Retire N unheard tag(s) manually..." for ANY unheard earlier tags,
  behind an attestation confirm (physically checked, boxes really gone,
  tags not just dead). Same retire endpoint, tombstones + History undo.
- **USE THIS LISTING settles the ambiguous flag**: the flag was
  re-derived from candidate counts every review, so picking a listing
  could never clear it (the twins keep existing). The reassign endpoint
  now records the choice (`rfid_batch_items.listing_locked`, one-off
  ALTER `dev/alter_add_listing_locked.py`, run 2026-08-25) and review
  stops re-raising; candidates stay listed for a change of mind. Both
  UIs follow (server-driven flag). Suite: test_listingpick.py.
  Note: the per-step Shelf-sweep power default Nick asked for shipped
  in 3.57 (pow_step_shelf + its settings row); update the gun.
- **C72 3.57 (code 75) + state-ladder unification**: the shelf verdict
  ladder in `_shelf_reconcile` is now the single source of truth both
  UIs render: green = sales fully explain the silence OR (no windowed
  sales) heard == on-hand-capped expected OR every in-bin record
  answered (over-hearing a neighbor is the over_heard note, never a
  yellow). Fixes gun-vs-web disagreements and "2 heard, expected 1"
  confusion; C72 rows now show the same silent/explained/unaccounted
  decomposition as the web reasons, and the raw sweep total is labeled
  "strays included". **Step-power off-by-one fixed**: STEP_SHELF's
  insertion had shifted the step->power array (Verify fell off the end
  and dropped to power 1); resolution now goes through an explicit
  per-step switch (a future step simply has no default instead of
  stealing a neighbor's) and Shelf sweep gets its own settings row.

## 🧭 Tutorial rebuild + LINK presence + replace menu (2026-08-24)

- **Unified barcode/SKU replace menu — ✅ DEPLOYED**: the unknown-barcode
  window's "Replace product barcode…" button and separate SKU section are
  now ONE ack-gated section with a Barcode/SKU mode toggle (label, ack
  text, and button follow the mode). Mode auto-detects from the code the
  operator scanned (12-14 digit numeric = barcode; letters = SKU;
  default barcode — most failed lookups are barcodes set to the SKU).
  **Freshness fix**: both overwrite endpoints now update the live
  `rfid_bin_map` rows (and SKU changes follow through to
  `rfid_assignments`) in the same commit, so the change shows on the
  very next scan instead of after the bin-map rebuild (Nick hit the
  stale window in the field). Suite: `dev/tests/test_replacefresh.py`.
- **C72 LINK presence + in-use check — ✅ DEPLOYED**: gun heartbeats via
  the tuning poll (`?device=&tab=`) and every LINK scan POST; terminals
  stamp a per-page-load tid through the scan poll; toggle-ON warns when
  another terminal is already listening (double-print hazard), late
  joiners get a non-blocking amber ⚠; hidden/backgrounded terminals stop
  acting and NEVER burst-replay stale scans on resume (skipped + counted
  instead). `GET /api/link/status` + release endpoint are the future
  auto-on seam (auto-on itself deferred, needs its own test round).
  Suite: `dev/tests/test_link_presence.py`.
- **C72 3.55 (code 73) — ✅ DEPLOYED**: ActionBar titles the current tab
  (Batch/Station/Sweep/Find Bin/Locate/Link); drawer keeps the app name.
- **Help slideshow rebuild — ✅ DEPLOYED**: 6 new dark-mode slides
  (`help/s1-link.png` … `s6-fixbarcode.gif`), every preview approved by
  Nick: link setup (gun mockup + highlighted toggle), label conventions
  (rotated highlights on his photos), SKU-entry + print GIF, gun-sweep +
  auto-reset GIF (drawn gun illustration, trigger/burst animation),
  edit-product GIF (the real Ⅱ→II SKU fix), and the new replace-menu
  barcode repair GIF. Old slide files stay on disk for cached clients;
  the C72 Link-tab mockup swaps for a real gun screenshot whenever one
  lands in `assets/`.
- **TODO next web-terminal build (Nick 2026-08-24): strip every em dash
  ("—") from UI copy** — replace with a plain hyphen or reword. New
  strings written this session already comply.

## 📦 Sold detection + Nick's big batch (2026-08-18, second session)

Nick's task list while he runs stock checks, all built the same day.
**BLOCKED piece: the Shopify custom app still lacks the `read_orders`
scope** (probed live) — Settings → Apps and sales channels → Develop
apps → the RFID app → Configuration → check `read_orders` → Save. The
sync is deployed fail-soft and reports "waiting for scope" until then.

- **Sold ledger** (`rfid_sold_ledger`, auto-create; `app/orders_sync.py`):
  fulfilled orders (read-only) recorded per tracked SKU. Expected tags =
  live on-hand + sold-unretired. Daily sync 8 AM Toronto + manual ↻ on
  Review; files/auto-closes ONE `tag-onhand-mismatch` review task per
  SKU (indigo/purple chips — deliberately distinct from the amber
  human-count families). Audits: silent tags fully covered by sales get
  a **MARK N SOLD** button (removes tag records, History `tag-sold`,
  retires ledger oldest-first, never touches Shopify); partially covered
  silence flags "count off". Bins-out-of-sync math now adds sold to
  expected. Product history gains `order-sold` events. Receiving/batch
  on-hand raises flow through live on-hand automatically. Audits remain
  the only CONFIRMATION of counts (Nick's rule).
- **Refresh parent component**: every refresh-ish button (bins pull,
  audit on-hand, batch re-pull, checks, 1-left board/scan, orders sync)
  shares refreshify() — server-logged durations (`rfid_refresh_log`),
  "Estimated Ns" + countdown, dim-green left-to-right fill; server-side
  autos mark `refresh_running:<kind>` so a page loading mid-run resumes
  the fill at the right level.
- **Printer picker**: `rfid_printers` (agent-claim upserts = detection +
  liveness) + `rfid_print_jobs.printer` (**ALTER script
  dev/alter_printjob_printer.py — run against prod before deploy**).
  Agents: `--printer-id/--printer-kind`; a named agent claims only its
  own + untargeted jobs; the CURRENT warehouse agent (no restart yet)
  claims everything, exactly as before. Scan Station: picker cards w/
  online dots; printing with no live selection opens the picker first.
- **Scan Station batch**: auto-print now fires for ANY product on scan
  (Astronomik serial flow demoted to an indented sub-setting, default
  ON); poor-print detection (mirrors print_agent ZPL geometry — ^FB
  overprints, never clips) holds auto-print + shows a red warning
  beside Print, live as an Astronomik name is typed; >10 labels needs a
  checkbox confirm; >1 label auto-enables BULK for the visit; operator
  list = hidden "Who's scanning?" placeholder + Guest always appended.
- **Help slideshow**: ❓ "How do I use Scan Station?" (top right, beside
  C72 LINK) — 6 SVG-illustrated steps (ZWO double-barcode + Svbony
  SKU:W9180A drawn from Nick's photos; /static/help/*.svg swappable for
  real photos later). **C72 v3.45** (code 63): opens on LINK, not BATCH.
- **Review tab**: yellow inline bin chip removed (resolve window covers
  it); 📝 with-notes filter (appears only when notes exist); expanded
  tasks show scoped TIMELINES (filter over product history — bin moves
  since filing for bin-mismatch; baseline + stock events with a running
  "tags: N" column for inventory-check; filed-when/by-whom for the
  rest). Resolving hides the view; product history keeps everything.
- Tests: test_printers (12), test_refresh (7), test_orders_sync (23) +
  full run_all before deploy.

Late additions (same day, all deployed): product window + Scan Station
card rebuilt (image header, print-first, options folded/one-line, edit
window with SKU/barcode/scan-note rows + greyed-at-saved saves); SCAN
NOTES (rfid_scan_notes, ride every lookup, amber banner + C72 triple
beep — C72 side code-only, ships with next APK); duplicate-product
detection (EXACT evidence only: shared barcode or same-SKU-different-
formatting, open-box ignored; merge picker moves tags + files an
inventory check; VARCHAR ate the old '⇄' key → 8,460 ghost tasks
closed, keys are ASCII now); bin-updated History rows carry Undo;
1-left Confirm opens a stock-tile window (unavailable/committed/
available/on-hand + actual count; higher → increase-only write offered,
lower → inventory-check filed); THEIR func app redeployed on Nick's go
(Nick+Clay valid employees; webhook guards: 0→1 never queues, 7-day
confirm cooldown, update-stock echo suppression — sync verified intact
after).

Bad-chars fix-on-the-spot (2026-08-19, deployed; C72 3.47/code 65
released with it — that APK also ships the waiting scan-note
display+beep): ZWO SKUs carrying the single Unicode char 'Ⅱ' get
stored as literal '?' by VARCHAR, so records stop matching the live
product (Nick hit it on EFW-Nikon-II; six more ZWO SKUs still carry
it in Shopify). The Check step now flags 'bad-chars' (literal '?' or
any non-ASCII in SKU/barcode), and BOTH terminals let the operator
change any resolved item's SKU/barcode right there (web: editor rows
in the item window; C72: CHANGE SKU / BARCODE… in the item editor).
Overwrites target a CLEAN barcode/scanned code (a mangled SKU
matches nothing live); SKU saves re-resolve the row so labels print
the new code. test_badchars.py covers the matrix.

RE-TAGGING done bins (2026-08-19, DEPLOYED through C72 3.53/code 71
after four field-test rounds with Nick; the 3.48 self-updater shipped
with it — the first self-update was 3.49→3.50). Field revisions:
shelf sweep became its own trigger-driven STEP between collect and
check (power chip reachable, reads accumulate, RESULTS dialog with
pinned-on-top buttons); apply SPLITS the collected count (tagged =
heard, qty = boxes − heard) — never additive (the first cut double-
counted every heard box); nothing-to-print asks instead of blocking
and jumps to verify; VERIFY is tri-state — the red chain is printed →
paired → THIS batch's tags heard (detected split by provenance), and
earlier tags going quiet is YELLOW ("likely sold or moved before this
batch"), with the C72 report rebuilt as EasyScan-style cards (status
stripe/pill + labelled count chips, both themes). Original design:
sold stock leaves stale tag records, so a
bin with a COMPLETED full batch gets the re-tag flow. Scanning a bin
barcode on the gun now CREATES the batch without entering it (card on
the pick list; yellow "⚠ Previous batch tagging: X ago" chip;
untouched batches self-expire at 4h). Collect never pops the
already-tagged question on those bins; Check opens ONE bin-level
SHELF SWEEP instead (burst reads ACCUMULATE — continue vs NEW SWEEP)
with per-product verdicts: match (heard = expected, expected = tags
on file minus sold; real sales once read_orders lands, else
min(on-file, live on-hand)), yellow unheard, red silent, noscan
exempt (never zeroes hand-set counts). Apply writes tagged_before
from what was HEARD (sweep is the counter — eye-counts only derive
"N boxes get labels"). Tap a highlighted row: one-by-one close-range
scanning, count-by-eye, and the dead-tag LAST RESORT: peel the
sticker, scan it OFF the box — reads = that exact EPC retired as
'replaced' (product was blocking RF); silent = oldest unheard record
retired as 'dead'. Retired EPCs live FOREVER in rfid_retired_tags
(new table, auto-creates): presumed-sold ones read as "possible
return", replaced/dead ones make future sweeps say "replaced sticker
still on a box — peel it" instead of 'unknown tag' (Nick doesn't
trust every worker to peel). Verify: presumed-sold note + one-tap
"Retire N" per product (web), tombstones named in the report, undo
via History (tag-retired/unretired events; unretire endpoint).
Web check shows the needs-a-sweep banner and clears it ITSELF when
the gun's sweep arrives; yellow/red row tints mirror the gun.
Verify sweeps on the gun: CONTINUE SWEEP (keep reads, add the missed
boxes, send again) vs NEW SWEEP — no more full redos; report rows
carry product thumbnails. test_retag.py: 31 checks.

Duplicate RESOLVER finalized 2026-08-19 (several rounds to Nick's
spec, all deployed): reason line names the shared value ("Duplicate
barcodes detected: X"); preview cards lead with the product name
(2-line clamp) then the identifier the pair DOESN'T share; differing
traits as bundle-outline selector pairs (barcode dup → Name+SKU, SKU
dup → Name+Barcode, both → name only), one pick each arms "Merge
products into one"; "Split products into two" swaps the pairs for
SKU+Barcode inputs in the same slots with live red/green verdicts
(offending fields red-while-clashing/green-when-fixed; innocent
fields red only if MADE to clash, never green) and a ↴ use-SKU-as-
barcode button per side; disabled actions grey at 45%; footer is
Dismiss (merge/split IS the resolution; dismissed pairs never
re-flag); window geometry frozen at first draw (synchronous — rAF
never fires in hidden tabs). Test pair verified in prod, then
deleted (tags/bin rows/task; zero DUMMY remnants confirmed).

Decisions (Nick, this session): sync cadence = daily 8 AM + manual
(15 min was too hot); workers = Steve, Matt, Clay, Nick + Guest; the
1-left dashboard's stale VALID_EMPLOYEES stays untouched (their system
is being worked on, logs must stay intact); print-agent restart any
time (RFID hardware idle) — only the Azure app + DB must stay up;
mark-sold prompts always require the user (auto-clear only when found
count exactly matches expected).

## 🧭 Audits hub + audit sessions — ✅ DEPLOYED 2026-08-18

Nick picked ideas 1 + 4 from the consolidation previews (EasyScan's
dashboard + stocktakes shapes, viewed in the store admin):

- **Hub landing**: the Audits tab opens on four stat cards that ARE the
  navigation — 1-left checks (pending/answered), Bins out of sync
  (count + worst drift), Run a bin audit (newest sweep age), Product
  checks — one tool pane on screen at a time with a ← back. All
  existing tool internals kept their ids; jumpToBinAudit now lands on
  the right pane.
- **Audit sessions** (`rfid_audit_sessions` + `_items`, auto-create):
  a named, resumable audit bundling a scope — bins (typed list and/or
  rack prefix, expanded from the bin map) or a slice of the 1-left
  queue (whole queue or one vendor, snapshotted at creation). Items
  tick per operator; **1-left items tick themselves** when a dashboard
  confirm for their SKU lands after the session opened. Progress is
  derived, never stored. Finish (open items allowed, confirm names how
  many remain) / abandon; History carries start/end ("Audit Session"
  chip). Endpoints: GET/POST /api/audit-sessions, POST .../items/{id}/
  done, .../finish, .../abandon. test_audit_sessions (16 checks);
  22/22 suites.
- **Ops Dashboard investigation (same session, Nick's ask):** Steve's
  dashboard work is healthy (planner-sourced vendor counts via the
  proxy's new /api/stock-checks — both endpoints verified live, page
  renders clean). The REAL problem: the $web root URL used to serve
  the 1-left verification checker (the page staff confirm stock checks
  on) and the Ops Dashboard was published OVER it on 2026-08-17; no
  path serves the checker now and nothing links to it. Storage keeps
  no old versions, but the checker's full source was recovered from
  the func app's own deploy package
  (scm-releases/scm-latest-inventory-verification-func.zip, squashfs →
  re-extractable any time). **FIXED same day (Nick's go, option a):**
  the checker now lives at
  https://shopifyautomationsa.z13.web.core.windows.net/check/
  (verified live: 431 cards, operator + vendor pickers working), and
  the dashboard's Stock Checks card's 🔍 icon became an outlined
  button linking to it (only that icon restyled; the other cards'
  watermark icons untouched). The pre-edit dashboard was backed up to
  the $web blob `backups/index-root-2026-08-18.html` before the write.
  Steve's dashboard was NOT otherwise touched.

## 🔍 Audits ↔ 1-left dashboard bridge — ✅ DEPLOYED 2026-08-18

Nick's ask (label shortage pause: "build the audit tab out as much as
you can" + connect the warehouse dashboard's 1-left stock checks so RFID
activity clears them). The Inventory Verification Function App
(`inventory-verification-func` — the queue behind the Ops Dashboard's
"Stock Checks" number) queues every product Shopify drops to 1 on-hand
for a human walk; the queue was at 431 items.

- **Source recovered** (unification Phase A item 3, partially): their
  backend + UI live in `scm-releases/scm-latest-inventory-verification-func.zip`
  (squashfs, NOT the function-releases container the unification doc
  guessed — that held tc-dashboard-proxy and shopify-jobs). Their data
  is CSVs in the `inventory-verification` blob container. Their app also
  runs live TelescopesCA↔TechGearCA inventory sync — their code is
  NEVER touched or redeployed from here.
- **`app/oneleft.py`**: bridge + evidence engine. Calls ONLY their
  `/pending` (read), `/confirm` + `/bulk-confirm` (what their Verify
  button calls), `/import-skus` (re-queue = the undo). update-stock /
  update-bin / update-barcode / issues endpoints are never called — the
  bridge cannot move a stock number anywhere, by construction. All
  calls fail SOFT (their outage can never break a scan). Gated by
  `ONELEFT_MODE` app setting: off (default in code) / read / confirm;
  prod = confirm.
- **Evidence rules** (all time-gated to AFTER the check's
  detected_date): tag paired (box in hand) · sweep heard one of the
  SKU's tags, however old the tag (box on shelf now) · batch counted
  units, incl. sealed cases + already-tagged/baseline boxes
  (audit-recorded bins excluded — copies, not fresh eyes).
  evidence_units = MAX across sources (they overlap on the same boxes).
  Auto-clear requires evidence ≥ claimed and claimed ≥ 1; claim of 0
  never auto-clears (evidence AGAINST a zero flags as a discrepancy
  instead); their stock fetch failing ("?") is treated as claiming 1.
  A re-queue PINS the check for a human until evidence NEWER than the
  re-queue arrives (else the same evidence would re-clear it next pass).
- **Auto passes** run on stock-discovering actions — tag pair, bulk
  sweep assign, C72 sweep upload, batch/receiving completion — via
  `oneleft.kick()` (throttled ≥45 s, background thread), plus a manual
  "Clear answered checks now" button. Server-stored pause switch
  (`rfid_app_settings.oneleft_auto`, Audits-tab toggle, History-logged).
  Confirms are attributed to the operator when they're on the
  dashboard's fixed employee list (Danielle/Evie/Matt/Noor/Steve),
  else `ONELEFT_EMPLOYEE` (default Steve — Nick isn't on their list;
  adding "RFID" to their VALID_EMPLOYEES needs a one-line change +
  redeploy of THEIR app, Nick/Steve's call). The full evidence trail
  lives in OUR receipts (`rfid_oneleft_checks`) — History renders every
  action ("1-left Check" chip) in both the main feed and per-product
  panels.
- **Audits tab panel** (top of tab): pending queue joined live with
  verdict chips — "RFID answers this" / "Shopify 0, RFID sees stock" /
  "now 0 — walk it" / "re-queued — walk it" / "needs a walk" — search,
  answered-only filter, per-row Confirm ✓ (operator judgment; same as
  their Verify button), receipts list with Re-queue undo. Read-only
  mode renders everything but the write buttons.
- test_oneleft (33 checks incl. the requeue-pin loop guard and a
  never-touches-their-write-endpoints assertion); 21/21 suites;
  run_local seeds a fake dashboard so the panel browser-verifies
  offline. NOTE for future sessions: `dev/run_local.py` sets
  ONELEFT_MODE=confirm with the bridge faked — never point run_local at
  the real dashboard.
- **Evidence freshness window (same session, Nick's call):** the first
  live board showed 60 of 430 checks "answered" by evidence that was
  often weeks old — "too old for me to confidently say stock hasn't
  changed or been sold since." Evidence outside `ONELEFT_FRESH_HOURS`
  (code default 24; prod app setting = 4, i.e. same-shift) now demotes
  to a "evidence too old" verdict and never auto-clears. Costs nothing
  in practice: auto passes fire within ~a minute of the interaction
  itself. The backlog stays for humans; only fresh discoveries clear.
- **Deploy incident (third packaging outage):** mkdeploy's hand-kept
  FILES list didn't have the new app/oneleft.py, so the first deploy
  crash-looped prod on the ImportError (~15 min down, fixed same
  session). `dev/mkdeploy.py` now GLOBS `app/*.py` and refuses to build
  a package missing anything `app/main.py` imports from app — new
  modules can't be forgotten again. Non-app files still need a FILES
  entry.

## 🎨 C72 v3.36: theme system + visual consolidation — ✅ DEPLOYED 2026-08-17

Nick's demo-prep pass (widget previews approved before build):

- **Palette, not constants**: every C_* colour is now derived in
  `applyThemePalette()` — mode (Settings → Theme: System/Light/Dark;
  System follows Android's night mode) plus five grouped slots (Main
  colour, Highlight, Good, Warning, Alert), each preset-swatch or
  custom-hex, saved per mode. Surfaces/lines/tints derive from the
  slots by mixing, so no pick can go unreadable. Changes save
  immediately; APPLY NOW recreates the screen (open batch survives
  server-side; confirm guard when in one). All 53 dialogs go through a
  themed `dlg()` so dark mode has no white frames.
- **Batch tab picker is inline** (PICK OPEN BATCH button gone): no
  batch loaded → the list pane IS the open-batch cards + dashed
  "scan a BIN barcode" placeholder + START RECEIVING. New status
  wording to match.
- **Consolidation**: locate LIST cards use the shared product-card
  look (image + bold name + meta, ✕ accessory) — server's
  /api/locate-queue GET now carries image_url (+ title fallback) from
  the live bin map. Link feed rows became verdict cards (✓/✕/… mark).
  Shared `emptyBox()` dashed placeholder (batch picker, link feed).
- **Chrome**: status line wears a severity edge (highlight = guidance,
  `alertStatus()` = Alert red, self-resetting); phase chip is a pill;
  FAR/NEAR/TOUCH is a segmented control; SOUND ON/OFF text replaces
  emoji; LIST… wears the queue count; Station hint cut to one line
  (full story stays behind ?).
- test_binfix immunized against the startup bin-map-rebuild race (same
  fix as test_taginfo, 2026-08-08). 19/19 suites; APK v3.36 (code 54)
  hash-verified on prod.
- **v3.37 (code 55, same day): locate meter sawtooth fixed.** The 400 ms
  tick mixed locPctOf(-999)=0 into the EMA on every window with no read
  while the last read was still <1.2 s old — reads often arrive slower
  than the tick, so the % halved per readless tick then leapt back on
  the next read (Nick's peak-decay-peak report). Readless-but-fresh
  ticks now HOLD the needle; the ×0.7 fade waits for real silence; the
  window best is captured-then-reset so mid-tick reads count toward the
  next window instead of being wiped. Hash-verified on prod.
- **v3.38 (code 56, same day): live tuning + telemetry channel.** Nick
  (still seeing 63→40→27→63 next to the box — the quiet-fade firing on
  real read gaps) asked for on-the-fly debugging instead of APK loops.
  The gun now polls `/api/c72/tuning` every ~2 s on the Locate tab and
  applies parameter changes live: fresh_ms, fade, blend, rssi_lo,
  rssi_span, debug. With debug on it streams per-tick telemetry
  (reads-in-window, best RSSI, EMA, pct, QUIET marker, power, gap) to
  `/api/c72/debug-log` (2000-row ring, pruned server-side). Claude
  reads the log and POSTs tuning with the station key — field tuning is
  now a conversation. Diagnostic plumbing: deliberately NOT in History.
  Initial prod tuning seeded: fresh_ms=2500, fade=0.85, debug=on.
  test_c72_debug (10 checks), 20/20 suites, APK hash verified.
- **v3.39 (code 57, same day): hunt read-rate — Gen2 session S0 +
  narrow-EPC filter.** Telemetry showed ONE read per ~2 s at point-blank
  full power: Gen2 session persistence (right for sweeps, wrong for a
  geiger). While locating, the gun now applies session S0/Target A
  (saved via getGen2, restored on stop — batch/sweep dedupe untouched)
  and, when narrowed to one tag, an EPC filter so inventory rounds
  aren't shared with the whole shelf (cleared on stop and on
  ALL-retarget). Both live-tunable: gen2_session (-1 leaves the radio
  alone), gen2_q (-1 default), filter_narrow. Live tuning session also
  set blend=0.9 (sparse-read staircase: EMA closed only half the gap
  per read). Hash-verified on prod; field verification = Nick's next
  hunt with debug streaming.
- **v3.40 (code 58): RADAR bearing + thermometer auto-power** (the
  Locate rework, previewed as widgets and spec'd by Nick before build).
  METER | RADAR modes: radar is single-target (auto-narrows a one-tag
  product; else asks for TARGET…), draws a dial — ping dots, confidence
  wedge, average line — and speaks plain language ("Slight left", bands
  ±10/30/60/110°, "Behind you"), designed around Nick's natural 120°
  ~1 Hz back-and-forth (samples accumulate ~15 s, sweeps counted).
  Engine picked at runtime: gyro histogram (reads tagged with
  integrated heading; axis/sign live-tunable) or Chainway
  startRadarLocation fallback when no gyro. Height/tilt phase CUT
  (racking interferes; bays barely above head height). Power UI: the
  FAR/NEAR/TOUCH segments became a tap/drag 1–30 thermometer with
  reference ticks + floor marker, AUTO toggle beside it — opt-in
  (Settings → Locate: default toggle + floor, default 5), steps down
  only while pegged, up when starved, penalty memory prevents the
  drop/lose/raise loop, radar samples flush on power change. Sensor
  inventory posts to the debug channel once per launch (answers the
  gyro question). All thresholds live-tunable (auto_*, gyro_axis/sign,
  radar_decay_s/max_age_s). Hash-verified on prod; NEXT = field test
  via the telemetry channel.
- **v3.41 (code 59): accel sweep engine.** First field test settled it:
  sensor inventory shows NO gyro (and no magnetometer), and Chainway's
  radar mode returns start ok=false on this module — both fallbacks
  dead. New engine: for a back-and-forth arc the lateral (tangential)
  acceleration is in antiphase with heading, so heading = -k × smoothed
  lateral accel, normalized by a decaying running peak (sweep-speed
  independent), assumed arc 120° (`arc_deg`), mirror flip via
  `accel_sign` — both live-tunable. Bearing is relative to the sweep's
  CENTRE; hysteresis on centre-crossings counts sweeps. Same
  histogram/dial/words as v3.40. Field-test tuning same session:
  auto_high 85→75 (point-blank pct plateaus ~77–83, AUTO never fired),
  auto_step_down 5→8 (fewer setPower blips — telemetry showed reads
  resume in <1 tick after changes; S0+filter delivering ~30 reads/s).
- **v3.43 (code 61): RADAR retired; power pause fixed; remote command
  channel.** Field test 3 verdict (Nick): the meter is great, RADAR
  isn't going to work — no yaw sensor exists on this C72 (no gyro, no
  magnetometer, Chainway radar refused) and the accel can't separate
  panning from tilt wobble. UI removed; engines dormant in code for a
  future gyro-equipped gun. The power-change pause: reader.setPower is
  a synchronized radio command and was running ON THE UI THREAD —
  every AUTO/manual change froze the app for its duration. Hunt power
  now applies on a worker thread, timed to telemetry, with a
  live-tunable strategy (`pow_strategy`: "live" = mid-inventory,
  "restart" = stop/set/start) so A/B happens over the wire. NEW:
  remote command channel — `rfid_c72_commands` + POST/GET-pending/ack
  endpoints; the gun polls every ~2 s on EVERY tab (tuning poll also
  global now) and executes: ping, say, beep, get_state, get_pref,
  set_pref, del_pref, dump_prefs (station key redacted), set_power,
  recreate — each acked with its result. Get/set INTO the app with no
  APK. test_c72_debug grew to 16 checks; 20/20 suites; APK hash
  verified; a ping command is queued to confirm the channel when the
  gun updates.
- **v3.44 (code 62): field-test polish round** (Nick's five notes, label
  list approved before build). Target switch mid-hunt now STOPS the
  hunt ("trigger to hunt" — mid-flight radio retune was unreliable);
  rssi_span default 45→42 so contact-on-tag reads 100%, and holding
  100% prompts "Right on top of it — MARK FOUND and hunt the rest?"
  (tracks the loudest EPC, 10 s snooze on decline, ends the hunt when
  none remain); TARGET dialog rebuilt as cards — green FOUND ✓ chip,
  blue TARGET chip, ALL/RESET as cards; hold SOUND opens a beep-volume
  slider (0–100, test beep on release, pref beep_vol, ToneGenerator
  rebuilt); button language unified: all-caps words, no ellipses /
  question marks / emoji / line-breaks, lit-means-active (SOUND,
  IDENTIFY, AUTO) — LIST (3), TARGET, MARK FOUND, IDENTIFY, UNLINK,
  BASELINE/APPLY one-line, START RECEIVING; arrows stay only on
  BACK ← / NEXT →.
- **v3.42 (code 60): motion gate.** Field test 2: standing still, the
  bearing jumped — the engine amplified hand tremor into fake headings
  (the amplitude normalizer ADAPTS to whatever it sees, so stillness
  cranked sensitivity up) while reads kept arriving and dragged the
  histogram. Real sweeps measure 2–6 m/s² lateral vs ~0.1 tremor, so a
  fast envelope (halves ~0.5 s) gates the engine with hysteresis
  (`sweep_gate_hi` 0.8 / `sweep_gate_lo` 0.35, live-tunable): below the
  gate heading freezes, no samples record, no sweeps count, the slow
  normalizer stops ratcheting, and the UI says "paused (not sweeping)".
  Gate transitions log to telemetry for threshold tuning.

## 📡 Locate list (web → C72) — ✅ DEPLOYED 2026-08-17 (C72 v3.35)

Nick's mid-review ask (stuck on S20300): hunt a product's tags without
typing a 24-hex EPC. A shared to-hunt queue, removable from either side:

- **Server**: `rfid_locate_queue` (auto-creates) + GET/POST/DELETE
  `/api/locate-queue`. Adds are idempotent per CI SKU; the GET carries
  LIVE tag context (tag count + the bins the tags think they're in), not
  a snapshot. Every add/remove logs a local-only "Locate List" History
  event with the operator.
- **Web**: product panel (opened from Review/History/Print queue —
  everywhere) gains a "📡 Send to C72 locate list" row that flips to
  "Remove from list" when queued; Review's header gains "📡 Locate list"
  opening the full queue with per-row ✕ remove and SKU links back into
  the product panel.
- **C72 v3.35 (code 53)**: LOCATE tab gains LIST… — the queue as
  tappable cards (SKU, name, "N tag(s) · tags say G4-4"); tap starts the
  normal locateLookup hunt, ✕ removes (attributed to the gun's device
  name). Empty-tab status now points at LIST….
- test_locate_queue (14 checks), 19/19 suites green; browser-verified
  add→list→remove on the seed server; prod smoke + APK hash verified.

## 🔫 C72 v3.34: uniform tab headers + per-context power — ✅ DEPLOYED 2026-08-17

- **One tab scaffold, enforced in code:** the app-level header (drawer ≡,
  help ?, scanner input, status/alert line) was already built once and
  shared; the per-tab sub-headers are now too — a single `tabHeader()`
  builder (bold title left, PWR chip right) used by all six tabs, with
  every chip in one registry so a power change repaints them all. LINK,
  Find bin and Locate gained the PWR chip they were missing (Nick's ask);
  changes to the scaffold now propagate everywhere by construction.
- **Settings → Scan power:** a default power per tab (Batch, Station,
  Sweep, Find bin, Locate, Link), each picked from the starred
  favourites / 1–30 slider with a "No default" option — plus an opt-in
  "different power per batch step" section (Collect/Check/Pair/Verify;
  a set step beats the Batch tab default). Applying a default acts
  exactly like tapping the PWR chip (prefs, chips, radio, status all
  move together), fires on tab switch and every batch step change, and
  Off everywhere = today's behaviour untouched. Hold-to-sweep's
  save/restore stays consistent since defaults live in the same pref.

## 📦 Bundle contents — ✅ DEPLOYED 2026-08-08 (the W9184B case)

**Import from Shopify (same day):** POST /api/bundle-contents/import
reads a bundle's components straight from the store — no typing.
shopify.get_bundle_components tries three shapes in order: native
variant components, **the Bundles.app variant metafield
`bundles_app.content`** (public JSON — what THIS store uses; found by
probing W9184Bx10's metafields), and product-level bundleComponents.
Buttons: product panel "⇣ Import from Shopify" (shown when undefined)
and the could-not-scan setup. Live-verified on prod: W9184Bx10 import
answered 10× W9184B from the app's own record. Non-bundles get a clear
404 pointing at hand entry. The W9184B story end-to-end: the "backwards"
collect list was a Shopify BIN gap (bins sat on the bundle listings,
not the single) — the three ratios are defined, the single gets its bin
via scan-and-move in batch #142.

One record answers two problems: rfid_bundle_contents stores what ONE
unit of a bundle SKU physically contains (e.g. bundle-of-10 = 10 ×
W9184B). Set once, used everywhere:
- **Batch collect** holds defined bundles OUT of the countable list —
  their boxes ARE the component's boxes, so 63 W9184B covers the
  bundle-of-10/-of-5 listings by arithmetic. The start-batch note names
  what was held out ("📦 N bundle listing(s) covered by their
  components: …"). Undefined bundles still seed and still get the
  Check-step bundle flag; excluded bundles unchanged.
- **Could-not-scan resolve window** (the 51701 rings): recognizes
  bundles, lists their components with "Tag N× SKU at the Scan Station"
  buttons (prefilled — print labels, BULK-sweep, back in the bin), and
  offers the one-time contents setup inline when undefined ("SKU x QTY,
  SKU x QTY" format).
- **Product panel** gains a bundle-contents row on every product:
  define/edit/clear via a prompt; defining also settles the product
  kind to bundle; clearing makes it countable again. Every change
  writes a History receipt ("Bundle Contents").
- Endpoints: GET/POST /api/bundle-contents (SKU in body — bundle SKUs
  carry "+"). New table auto-creates. test_bundles (10 checks), 18/18.
- What one bundle listing's own Shopify on-hand should BE given the
  component count (6 = 63 div 10) is display math for the audit
  revisit, not written anywhere today.

## 📥 Review resolve windows + notes — ✅ DEPLOYED 2026-08-08

Plan approved by Nick (category by category), then built same day:
- **Notes** on every Review entry (incl. synthetic mismatches, keyed
  "binmm:SKU"): 📝 flag with count on the card, thread + add box in the
  expanded view, and dismissing a noted task takes a second, deliberate
  press (inline are-you-sure strip). Notes survive resolution.
- **Resolve opens a window** (per-category actions, every write via the
  existing audited endpoints; dismiss stays quick on the card):
  · inventory-check — live on-hand re-fetch on open: matches → one-click
    resolve; counted higher → gated Set-to-N then resolve; counted lower
    → the window says write-downs stay blocked, recount-with-required-
    note; jump-to-bin-audit.
  · pairing-incomplete — live catch-up check (one-click resolve when
    pairing completed since), open-at-Scan-Station, resolve w/ note.
  · bin-check — run-audit jump + newest-sweep info line.
  · bin-mismatch — now has resolve AND dismiss (Nick's ask): window
    offers both truth directions — "Shopify is wrong" (audited bin
    write) or "Shopify is right" (NEW local-only POST /api/assignments/
    rebin: tag records + open-batch snapshots follow Shopify's bin;
    History "tags-rebinned"). Dismiss = suppression row keyed
    (sku, tags' bin, Shopify's bin) — reappears if either bin changes;
    History carries it with an un-dismiss undo.
  · could-not-scan / legacy — open-at-Scan-Station + resolve w/ note
    (deeper flow pending planning — the 51701 rings case).
- **Unresolved barcodes leave Review** (Nick's call): normal batch
  completion no longer files them; the verify step shows a non-blocking
  note naming them instead (completion drops them either way).
  Receiving still files its version (no verify step there).
- New tables rfid_review_notes + rfid_mismatch_dismissals (auto-create).
  test_binfix grew 8 checks; 17/17 suites.

## 📥 Review tab upgrade — ✅ DEPLOYED 2026-08-08

- **Mismatched Bins**: products whose tags sit on a different shelf
  than Shopify's bin now appear in Review as LIVE synthetic entries
  (computed per fetch from assignments vs the bin map, never stored —
  they clear themselves when either side is fixed). Card offers the
  audited "bin ⇢ <tags' shelf>" write instead of resolve/dismiss;
  12 live on prod at ship time (the backfill's differs list).
- Filtering by type shows a plain-language note under the filter
  explaining what the tag means and why products land there (all six
  categories covered).
- Search bar filters open tasks by SKU, barcode, title, or detail text.

## 🔫 C72 v3.32: empty bins are an answer — ✅ DEPLOYED 2026-08-08

- NEXT at batch collect now accepts a shelf handled entirely through
  "already tagged" records (tagged_before counts as work done — it used
  to refuse with "Scan at least one box").
- Nothing scanned AND nothing already-tagged → asks "Is the shelf
  actually EMPTY?" — confirming completes the batch from the gun
  (finalize; the one scanner-side finalize, since an empty shelf has no
  counts to check on any screen). Files the normal inventory-check
  tasks (0 vs Shopify's expectations) and the bin leaves the to-do
  board honestly.
- Web "Bins to do" board gains **Show done (N)**: lists every
  batch-tagged bin (✓ row, products, when, by whom) under the to-do
  list, filter-aware; /api/bins/overview now returns the `done` list.

## 🔫 C72 v3.31 + Scan Station polish — ✅ DEPLOYED 2026-08-08

C72 v3.31 (versionCode 49), design iterated with Nick over five widget
previews before building:
- **Hold-to-sweep** (⚙ → Trigger pulls, OFF by default): on LINK and
  STATION, holding the trigger past a threshold turns the pull into a
  sweep — release auto-sends it as an EPC capture (the thing bulk scan /
  verify / bin audits pull), so LINK no longer needs SWEEP-tab round
  trips. Quick pulls stay single reads but fire on RELEASE while the
  mode is on (the known tradeoff, why it's a toggle). Armed identify on
  STATION keeps its instant read; batch pair/verify sweeps untouched.
- **Trigger pulls window**: threshold in ms (clamped 200–2000) + "Set
  threshold with trigger pull" calibration (times a real pull);
  "Sweep at its own power" toggle with the pick behind a button —
  favourites picker (starred fav_powers pills + 1–30 slider, pills
  sync to the slider only on release), default PWR 1 per Nick.
- Everything under the master toggle greys out together when it's off.

Scan Station same day (all Nick's asks): step 2 ("Scan RFID tag")
hidden until a barcode loads a product; Labels input 52px/centered,
digits-only (0–999, 3-digit cap, click selects all, spinners gone,
resets to 1 per new barcode); "Live catalog" hover explains itself;
header pills restyled Shopify-admin quiet (hairline badge + status
dot; hover on "Shopify connected"); toggleable red "No bin set"
warning beside Print & encode (⚙, ON by default, printer stations).

## 🔶 Current field-test round (C72 v3.27, installed from the terminal)

Everything below shipped 2026-08-03 → 08-06 and works in tests/browser;
Nick is running the bins and feeding fixes back same-day. Since v3.21:
- Batches start (scan a bin barcode) and abandon on the gun; already-
  tagged dialog gained recorded-shelf + sweep-to-count (v3.22).
- Web verify: flagged rows expand into a resolution panel (new vs
  already-tagged counts summed against expected); detected accepted at
  X or X+Y, flagged only in between/overflow; double-count guard at
  Check with one-tap fix (v3.23).
- **Lookups answer from the LIVE bin map, mirror demoted to fallback**
  (the F9394B-printed-as-DB24010501 fix — see CLAUDE.md hard rule).
- "Move product to this bin" now clears its flag (open-batch bin
  snapshots move with the update).
- Bin audit: sweep any shelf vs Shopify (verify-style diffs, on-hand
  button, untagged toggle, record-as-batch-tagged rescue, "already
  recorded" notice naming abandoned attempts).
- Scan a tag → full identity + warnings (orphan SKU, wrong bin, case,
  suspect) + UNLINK with History receipt (TODO #8 done, v3.26);
  identify is a trigger-armed toggle (v3.27). Tap the scanner input to
  type (v3.25).
- Inventory tab shows ON-HAND (was "available" — negative on oversells).
- **Bin backfill (2026-08-08, Nick's ask):** one-off
  `dev/backfill_bins.py` wrote tag placements to Shopify for every
  product whose bin was MISSING there — 47 written (0 failed) through
  the normal audited /api/bin-updates (History receipts by
  "bin-backfill", undoable). 14 products where Shopify has a
  DIFFERENT bin were deliberately left for the tab's per-row button
  (listed in the script's dry run; one has garbage value "F 1 3").
  Root-cause fix shipped with it: a bin write for a product with no
  bin-map row now CREATES the row (before, the write looked like a
  no-op on the Inventory tab until the 6-hour map refresh). The
  "⇢ Shopify"/verify bin-fix pill was restyled onto the theme's warn
  tokens — it was hard-coded light-mode amber and glowed in dark mode.
- **Bin-fix offers (2026-08-07):** a walked batch counts as a deep manual
  check of its shelf, so products it physically handled whose Shopify bin
  disagrees (or is missing) get a "bin ⇢ <bin>" button on their Verify
  row, and Inventory rows whose tag placement disagrees with Shopify's
  bin get "⇢ Shopify" — both are the existing audited /api/bin-updates
  write (History + bin map + tags follow). Untouched pre-seed rows never
  offer (the batch proves nothing about them); split-shelf listings that
  include the bin count as agreement. Inventory bin chip no longer wraps
  mid-code ("K4-" / "1").
- Unbuilt ideas on the table: Review resolve-actions per category
  (analysis done, nothing built), point-reads clamping their own power
  (metal-shelf misreads at power 30), docs/inventory-verification-app.md
  for the 1-left-check tie-in.
- Already-tagged flow: first scan of a product with prior tags asks how
  many boxes are stickered (one-screen stepper + held-box checkbox,
  v3.20); verify counts those boxes everywhere (web + C72 + server).
- Wrong-shelf review at Check: per-item keep-or-move with product
  cards; KEEP = audited bin update, MOVE = side trip; warns when the
  home shelf already holds recorded tagged boxes (v3.21).
- C72 verify popup rebuilt on the whole-bin check (SKUs shown, off-map
  products counted, tappable preview cards) (v3.18–3.19).
- Side trips excluded from "Recently done"/bin-done everywhere; History
  labels them as side trips.
- Verify tag ownership is CI-SKU alone (replaced barcodes no longer
  read as "foreign"); flagged verify rows expand into a resolution
  panel (new/already-tagged counts vs expected); detected accepted at
  X or X+Y, flagged in between.
- Audit tab: sweep-a-bin audit (C72 SWEEP → SEND → pull vs any bin;
  Check-step verdicts, strays, unknown tags; display only).
- LOCATE tab (v3.24) — Steve's TODO #5 + the locate backlog item,
  built: RSSI hunt, FAR/NEAR/TOUCH power, geiger audio, power-1
  confirm-a-find that filters found boxes. FIELD TESTED 2026-08-06
  (Nick): works, but finicky around the metal bins (multipath) — usable
  as-is; tuning knobs identified (smoothing weight, best-of window,
  dBm range) if it starts to annoy.

## Architecture (target)

All logic lives server-side (Azure FastAPI + Azure SQL). Every device is a
terminal: PC (printing, batch start, review), iPad (optional live
view/edit), C72 (primary shelf tool: barcode collect + RFID pair + verify).
Operator returns to the PC only to collect printed stickers and start the
next bin.

## ✅ Done

### Scan Station (single-product flow)
- Barcode → product lookup: TELCAN mirror first, Shopify API fallback
- Two-scan RFID pairing (barcode, then tag) with duplicate/suspect guards
- Label printing: Zebra ZD220t via print agent on the warehouse laptop
  (barcode-only mode — no RFID encode; pairing stays two-scan)
- Label layout: "Telescopes Canada" header + SKU + Code 128 + BIN,
  centered/calibrated for 2.125×1.25" stickers
- Product edits with confirmation: barcode overwrite, SKU update, bin move
  (all audited, all gated by SHOPIFY_WRITE_MODE)
- Barcode alias system: link unknown codes to products; undo from History
- Astronomik serials: prefix→product resolution, operator-confirmed label
  names (name-at-top labels are Scan Station ONLY), auto-print on scan,
  register-new-prefix UI
- Operator picker; auth = Shopify session tokens (embedded) + station key

### Batch Tagging (bin-first flow)
- Enter bin → pre-seeded expected products with 0/N tickers
  (bin map: Shopify metafield walk, ~3,200 binned variants across ~290
  bins, refreshed every 6h, multi-worker safe)
- Scan counts up tickers; over-scan allowed; unknown barcodes appended;
  scanned rows float to top; collect summary line
- Bin mismatch prompt: keep saved bin / move product (confirmed write)
- Label step: SKU-labeled store labels, one per box, batch bin printed
- Pair stage: product barcode selects, EPC scans pair, 409 on duplicates
  (names the owning product), undo last tag, barcode-shaped non-matches
  rejected (never saved as EPCs)
- Verify stage: RFID sweep (C72 app "Pull latest sweep" or wedge) →
  per-product boxes/paired/detected + foreign/unknown report
- Finish check (web + C72): the confirm shows per-product entered-by-RFID
  counts; finishing with untagged boxes requires an explicit are-you-sure
  naming how many products/boxes are missing ("Finish anyway")
- Complete → auto-files Review tasks (count mismatch, pairing incomplete,
  unresolved barcode); abandon; cross-device resume + Refresh

### Other tabs
- Print queue: job table, cancel pending, reprint (new EPC), printer-agent
  online/offline pill (heartbeat)
- History: merged append-only timeline (assignments, edits, labels,
  aliases, batches, review tasks) + search + undo for barcode links
- Per-product history: click any SKU in History (or look one up) →
  product panel + full timeline of that product's events, each marked
  Shopify ✓ (wrote to the store) or local (recorded here only)
- Review: open-task inbox with resolve/dismiss
- Audits: placeholder (recommended checks + recent C72 sweeps)
- Inventory: product summary with live Shopify quantities

### C72 companion app (TC RFID Sweep, v1.2)
- Native Chainway app; wireless install from
  https://telcan-rfid.azurewebsites.net/static/tc-rfid-sweep.apk
- RFID sweep: trigger-toggled inventory, on-device dedupe + counts,
  SEND over Wi-Fi → server → "Pull latest C72 sweep" in batch verify
  (no Bluetooth anywhere)
- Power: 1–30 slider + presets (2 station / 5 bin / 10 rack / 30 locate)
- BARCODE mode (v1.2, built, NOT yet field-tested): 2D imager via SDK,
  ding/buzz sounds, deduped list — capability test for the C72-first
  workflow
- UTF-8 build fix (garbled …/✓ characters)

### Infrastructure
- Azure App Service deploy pipeline (zip deploy), Azure SQL (TELCAN),
  bin map table, SHOPIFY_WRITE_MODE safety gate (default: scan-station
  writes only), print agent heartbeat

## 🔶 In progress / blocked

- **C72 v2.0 FIELD TEST** (deployed 2026-07-26): tabbed app —
  BATCH | STATION | SWEEP | LOCATE(WIP), tabs hideable in ⚙. Batch
  screen: bin+boxes top-left, tappable COLLECT/PAIR chip top-right,
  PWR chip → power dialog, product preview card (image/name/SKU +
  scanned/expected tracker in the corner), scan list owns the screen.
  Station tab = single-product tag linking with the same card. Live web
  mirror (3s poll) while a batch is open.
  ACTION (Steve): install v2.0, pair the BT scanner, run one real bin
  end to end.
- Built-in imager: confirmed absent (no aimer light, instant
  DECODE_FAILURE) — barcodes come from the BT scanner permanently.
- ~~Print agent update~~ RESOLVED 2026-07-27: the agent runs on the dev
  laptop FROM this repo directory (scheduled task "RFID Print Agent" →
  print_agent_loop.cmd), so agent fixes apply by restarting the process
  (loop relaunches in 10s). Header rule + long-name font fixes are live.
- **Inventory-check screenshot** — ACTION (Steve): attach it so the batch
  UX revamp matches the look of the old system.

## 🔜 Next up (the revamp — after the C72 barcode test)

- ✅ SHIPPED 2026-07-27 (round 3, C72 v2.5): batch ties are now
  batch-scoped — abandoning releases them, History can undo a whole
  batch's ties, and pairing can be undone wholesale for a re-scan;
  skip-printing goes straight to pairing; unresolved barcodes get a
  rescue flow (odd-barcode candidates → fix the Shopify barcode);
  wrong-shelf products can be dropped/moved/ignored; label format
  (Name / SKU / Both) editable per product in the Check step; pair
  ticker counts printed labels; verify auto-checks on pull, states
  whether boxes/paired/detected agree, and can look up any bin; C72
  gained a FIND BIN tab and a sweep-for-unlinked-tags rescue.
- ✅ SHIPPED 2026-07-27: ambiguous-barcode Check step (web + C72 v2.4).
  Batch flow is now linear Collect → Check → Pair on both surfaces; the
  Check step flags shared barcodes (candidate arrows, main listing
  default), count mismatches, unconfirmed serial names, unknown
  barcodes. Preferred names gained a placement toggle (store header vs
  SKU line) + ✕-to-clear. Field test pending.
- ✅ SHIPPED 2026-07-27: web batch UI mirrors the C72 (cards with
  image/SKU/Barcode/tracker, green/red glow, ding/other-ding/buzz
  sounds, clickable stage chips) and C72 v2.2 drawer (slide-in over
  content with scrim, header row reclaims the old tab bar's space,
  tones on the alarm stream so device media volume can't mute them).
  Field test pending.

- C72-first batch workflow (the 8-step flow):
  server endpoints for barcode-driven collect + pair; C72 app batch
  screen: pick bin → collect with dings + expected tickers → pair
  (barcode, then its stickers) → confirm; iPad/PC become live views
- Batch UX revamp (web):
  - clickable stage chips (go back to any earlier step)
  - product image previews in collect rows (bin map gains image column);
    roomier, less compact cards
  - sounds: ding = expected match, distinct ding = valid product not
    expected in this bin, buzz = no match
  - glow: green border when scanned == expected, red when over
  - completion screen with per-product stock deltas ("+1 (5 → 6)") to
    confirm before filing inventory changes

## 📦 Receiving — ✅ SHIPPED 2026-08-07 (server + web + C72 v3.29)

Both features below are BUILT, tested (14/14 suites incl. new test_link +
test_receiving), browser-verified on the seed server, and deployed.
Prod got the one-off `rfid_batches.kind` ALTER (dev/alter_add_batch_kind.py)
before the deploy. Field test pending — Nick has the v3.29 APK link.
Also shipped same day (C72 v3.28): settings redesign (Connection
sub-window + switches + strongest-tag-on-trigger toggle) and the
open-batch picker cards.

What shipped, per the design below: LINK tab (barcode + RFID relay,
outcome ding/buzz, web C72 LINK toggle on Scan Station, operator-keyed by
device name); receiving batches (RECEIVING sentinel bin, repeatable PRINT
of only-unlabelled boxes with home-bin labels, no-bin items held out by
name, pair records home bin, verify/side-trip/wrong-bin/count-mismatch
all correctly refuse or stay silent, finish files per-bin "bin-check"
Review tasks + History receiving-started/completed); manual
POST /api/review/bin-checks (bins list or rack= prefix). Web: Start
receiving button, collect→print→pair chips, print/finish bar. C72:
START RECEIVING in the picker, COLLECT⟳/PAIR⟳ loop, EXIT → FINISH
RECEIVING with per-bin summary.

### Original design (2026-08-07, agreed with Nick)

Two features cover every receiving workflow (desk, pallet, or a mix).
Planner (TC-Inventory-Planner) integration deliberately SKIPPED for v1:
invoices are often wrong, shipments arrive partial, boxes sometimes have
no distributor barcode — so receiving is open-ended manual capture, not
PO reconciliation. (The planner repo is now fully pulled at
`Desktop\Stuff\Inventory Planner`; it already has stock orders,
`/receive`, and an increase-only Shopify apply flow — that tie-in is
Steve's TODO #2, still open, later.)

**Feature A — LINK tab (C72): gun as a networked input device.**
- New C72 tab arms BOTH inputs: BT-scanner barcodes and trigger RFID
  reads (existing strongest-of-600ms pick). Each scan POSTs to the
  server immediately — no Bluetooth to the PC, ever.
- Web terminal gets a "C72 LINK" toggle (Scan Station first); while on,
  it polls ~1s and treats incoming barcodes exactly like wedge input and
  EPCs like tag scans — same code paths, every existing guard intact.
- Scans keyed to the operator-picker identity (two guns = two streams).
- Feedback on both ends: gun dings on delivery, then gets the outcome
  (paired ✓ / duplicate 409 / no product selected) so the user isn't
  glued to the monitor; web shows the same on the product card.
- Pairing may be driven from the gun OR the computer — LINK just makes
  the gun an extension of whichever screen is driving.

**Feature B — receiving batches (server + web + C72).**
- Batch kind = 'receiving' (new column → one-off ALTER for prod). No
  bin. Excluded from bin-done/"Recently done" like side trips; History
  labels it as receiving.
- Loop, not a line: collect → PRINT → pair → back to collect, as many
  passes/pallets as needed. PRINT is repeatable and queues labels only
  for collected-but-unprinted boxes, in scan order (sticker stack
  matches the walking order). Confirm screen shows "new since last
  print" to catch re-scanned boxes; printed-vs-paired ticker flags
  orphan labels at finish.
- Labels carry each product's HOME BIN (live bin map) so every box
  leaves the desk knowing where it goes. No-bin products: assign-a-bin
  prompt at print time (existing sanctioned bin write) or hold them out
  of the job.
- No-barcode boxes: typed SKU is first-class; distributor barcodes get
  linked once via the existing alias system.
- Finish: NO verify step. Instead files one Review task per bin that
  received stock ("Inventory check <bin>") + a manual mark-a-rack
  option. Nick confirmed per-bin volume is fine (~10/shipment; each is
  a quick RFID walk-scan). Resolving = run the existing bin audit on
  that shelf — on-hand updates happen ONLY through the audit's existing
  operator-confirmed increase-only button. Receiving itself never
  touches counts (standing decision holds).
- Printer walks between passes are acceptable (small warehouse; the
  printer sits on the desk, so desk receiving has zero walks).

Build order was A then B, as planned. Open receiving follow-ups:
- ✅ SHIPPED 2026-08-07: Review "bin-check" cards now carry a one-tap
  "run audit" jump — lands on the Audits tab with the bin loaded, and if
  the newest C72 sweep is under 5 minutes old (the operator clearly just
  walked the shelf) the audit runs itself; a stale sweep instead gets a
  "walk-scan <bin>, then RUN" prompt naming the sweep's age. Fixing the
  age math surfaced an app-wide bug: server timestamps are UTC but
  unsuffixed, so new Date() read them as LOCAL and everything under 4 h
  old displayed "just now" — all client-side timestamp parsing now goes
  through tsDate() (assumes UTC when no zone is present).
- The C72 item editor's change-bin flow is how held no-bin products get
  bins at the desk; a dedicated prompt at PRINT time could streamline it.
- On-hand counts still only move via the bin audit's gated button
  (standing decision holds).

## ⚡ Bulk scan on the web Scan Station — ✅ DEPLOYED 2026-08-07

Nick approved the preview; deployed same day. BULK chip lives beside
auto-reset INSIDE the Scan RFID cell (auto-reset moved out of Settings);
chip is disabled/gray unless auto-reset is on and defaults OFF per
product. Tracks tags assigned vs labels printed this visit: exact →
auto-reset, over → inline warning with UNDO THIS SWEEP (SKU-guarded,
only the offending sweep; hover text points at History for more) and
KEEP ALL (won't re-ask until the count grows). Sweeps write with one
shared timestamp so History folds them into "N × RFID tag (sweep)"
expandable rows (▸ show EPCs); undos fold the same way. Sweep assigns
never steal: already-assigned tags are skipped and named. Server:
POST /api/rfid-assignments/sweep + /sweep/undo. test_bulkscan (14).

## 🔗 TC-Planner bridge — ✅ phase 1 (READ-ONLY) DEPLOYED 2026-08-07

The RFID server now talks to TC-Planner (tc-planner-app, same resource
group). STRICTLY read-only: it answers "is this SKU on an open purchase
order, how many are still expected" — it never files receipts, never
changes PO statuses, never emails vendors, never touches Shopify stock.

- `app/planner.py`: Bearer-token client (PLANNER_URL + PLANNER_TOKEN app
  settings; token unset = bridge off, all surfaces degrade silently).
  Per-SKU answers cached 5 min; planner outages fail SOFT (ok=False,
  still 200) because hints must never break a scan.
- Endpoints: GET /api/planner/status, GET /api/planner/on-order/{sku}
  (open-PO lines for that exact CI SKU with ordered/received/remaining).
- UI: "📦 On order: N more expected — PO#935 Sky-Watcher (ETA …)" hint
  on the Scan Station product card AND under the receiving-batch collect
  result. Hidden when off/down/nothing-on-order. test_planner (8).
- Verified against the LIVE planner: 45 open POs, 320 on-order SKUs;
  prod smoke S11710 → 6 expected on PO#935.
- ✅ Attribution (2026-08-08, Nick's call): the planner now has a
  dedicated `RFID` entry in TC_PLANNER_USER_TOKENS, and the RFID app
  carries PLANNER_USER_TOKENS (same name:token pairs as the planner's
  own). Planner calls ride the "Who's scanning?" operator's PERSONAL
  token when one exists (planner whoami answers "Nick"/"Steve"/…),
  falling back to the RFID identity. Verified live in prod. The C72
  (v3.30) shows the on-order hint during receiving collect too —
  appended to the status line after the count, attributed by the gun's
  device name; Nick: "display it for now and we'll see."
- Found in passing (planner-side, NOT fixed): GET
  /api/replenishment/summary 500s with "unsupported operand type(s)
  for +=: 'float' and 'decimal.Decimal'". /api/refresh/status is idle
  and PO detail's live Shopify bin fetch works, so the shpat token
  itself looks healthy.
- The shpat story per Nick (2026-08-08): the real complaint was that
  planner-made Shopify inventory adjustments weren't attributed to the
  planner in Shopify's adjustment history. Code inspection: the
  planner's adjust_inventory sends reason="received" and nothing else —
  attribution in Shopify admin comes from the NAME of the custom app
  that owns the shpat token, so any fix happened in Shopify admin (app
  rename), not in the repo (which has no history — 2 commits total).
  Improvement candidate for phase 2: pass referenceDocumentUri (a PO
  link) on inventoryAdjustQuantities so each adjustment names its PO.

**Phase 2 plan (NOT built — Nick/Steve to approve):** finishing a
receiving batch offers an operator-confirmed "file against PO" step:
match the batch's counted SKUs to open-PO lines, preview per PO, then
POST /receive on confirm (planner-local only — its own Shopify write,
apply-stock-update, stays untouched; our standing never-auto-write
decision holds on both sides). Same offer from Scan Station sessions is
possible once wanted. This is the on-ramp to Steve's TODO #2.

## 🧭 Unification (RFID + TC-Planner + 1-left → one service)

Design written 2026-08-08 at Nick's ask —
[docs/unification-roadmap.md](docs/unification-roadmap.md). Headlines:
the planner already shares the RFID database (telcansql/TELCAN, verified),
so conglomeration is a code move, not a migration; the 1-left backend
source IS recoverable (function-releases container — supersedes the
"not recoverable" note in docs/inventory-verification-app.md); phased
plan is A) planner receive filing + read-only 1-left panel + source
recovery/auth, B) shared identity + 1-left queue into TELCAN, C) mount
planner into this app, retire the Function App last. Contracts to keep
alive: shopify-jobs → on-order-skus, the C72 API, Bundles.app metafields.

## 📥 Nick's TODO list (captured 2026-08-25, not yet designed)

Noted from Nick's field feedback. Not scoped; ask before starting the
bigger ones (receiving in particular needs interviews).

1. ~~**Zebra printer label drift.**~~ ✅ Addressed 2026-08-25: manual
   "Re-align labels (feed one)" button shipped (inert until the agent
   task restarts; see the print-truth section above), plus printer-side
   config suggestions (web sensing calibration, backfeed-before) that
   could remove the drift entirely. Revisit only if drift persists
   after Nick tries both.
2. **Receiving, robustly, hooked to Inventory Planner.** An extremely
   robust receiving flow that needs little know-how. Use cases include
   at least: scanning each box one-by-one until all items are printed
   and tagged; pulling the actual manifest of what was sent (or was
   supposed to be sent) and printing from that list, with per-product
   check-off of what did and didn't arrive before printing. Requires
   interviewing the people who do receiving to map the real process.
   (Overlaps Steve's TODO #2; the 1-left dashboard bridge memory notes
   where Inventory Planner data lives.)
3. ~~**Print jobs in collect-scan order.**~~ ✅ Done 2026-08-25
   (first_scanned_at stamps; see the print-truth section above).
4. **Consolidate "tags != on hand" vs "inventory check" review tasks.**
   Decide whether both categories are really needed or whether they can
   be compacted into one (they answer the same question from different
   triggers: arithmetic vs a human count).
5. **RFID-scanning at shipping-out.** Deliberately LAST: knowing where
   inventory is, and tracking/confirming/locating it, comes first.
6. **Locator marker tags for non-taggable products.** The non-taggable
   flag (shipped 2026-08-25) already keeps thumbscrew-style bins out of
   batches/audits, and a hand-paired tag works as a bag marker findable
   via Locate. Design a first-class "marker tag" type on top: pair it
   with an explicit marker role from the UI, show it as a marker
   everywhere (never a unit), and keep it out of every count by type
   rather than by SKU flag.

## 📥 Steve's TODO list (captured 2026-07-28, not yet designed)

Noted verbatim-in-substance from Steve. **Not designed, not scoped, no
code written.** Do not start any of these without asking him first — the
first two in particular have ordering constraints that make "helpfully
starting early" actively harmful.

1. **Sync found inventory → Shopify on-hand.**
   ⛔ **Do not begin until the ENTIRE store is batch tagged.** Right now
   many products sit in the wrong place, and Steve is deliberately doing a
   manual hard reset of product locations. Writing on-hand numbers before
   every product is found, tagged and correctly binned would push wrong
   counts into Shopify. The counts we hold are observations until then.
   (See the standing decision on never auto-writing inventory.)

2. **Sync with incoming inventory (receiving).**
   Ideally one item at a time, with a permissioned bulk-add for a whole
   shipment, everything added flagged internally as needing tagging. Wins:
   incoming products are already in the system instead of the operator
   hunting untagged stock, and receiving stops being manual. Receiving is
   manual today only because a shipment can't be trusted to be 100%
   accurate — but if every incoming product is flagged for an inventory
   check (or the operator scans it in at the desk for a true count), the
   bulk path becomes safe.

3. **Finish the Review and Audits tabs.** Both are WIP stubs. Steve
   doesn't remember what each was for — work out the intended split before
   building (Review = task inbox from batch completion; Audits = shelf
   reconciliation, per the backlog entry below) and confirm with him.

4. **Make the C72 and web terminal genuinely usable by other people.**
   Steve can drive it because he co-designed it across ~100 commits; no
   one else can. Wants a full aesthetic redesign, guidance walking the
   user through every decision point, and more intuitive buttons. This is
   the difference between a tool one person can use and one the warehouse
   can use.

5. **Locate a product on the C72.** (Overlaps the locate-mode backlog
   entry below.)

6. ~~**Scan a batch of tags, then pick the closest by signal strength.**~~
   ✅ Done 2026-08-03 (C72 v3.15): every trigger read (batch pair + Scan
   Station) now listens ~600 ms, collects every answering tag with its
   RSSI, and pairs the STRONGEST — with a status note when several
   answered, and a caution when the runner-up was within 2 dB. Falls back
   to most-often-heard if the SDK returns no usable RSSI. Field test at
   the warehouse still pending.

7. **Unpair a single product during collect,** instead of undoing the
   whole batch because one product was got wrong early on.

8. **Scan an RFID tag and be told what it is,** with actions — chiefly
   unpair, so a mis-tagged sticker can be re-tagged as the right product
   during or after batch collection.

9. ~~**"?" help icon on every usable C72 window**~~ ✅ Done 2026-08-03
   (C72 v3.15): a "?" next to the drawer button explains the CURRENT
   screen — each batch step (collect/check/pair/verify) gets its own
   text, plus Scan Station, Sweep, Find Bin, Locate, and the batch list.
   The item editor has its own "?" covering every control in it. Still a
   slice of item 4; the full guided-workflow redesign remains open.

10. **Support page: name + message → opens a GitHub issue** (added
    2026-07-29; reworked same day — was "email Nicholas Drapak directly",
    now a GitHub issue on this repo instead, no direct email at all).
    A user leaves their name and a message; the server opens an issue
    titled from the message with name + message in the body. Nicholas
    gets notified through GitHub's own watch/notification settings, which
    kills the two hardest parts of the email version: no sending
    mechanism to build, and no personal address to keep correct. What it
    needs instead: a repo-scoped GitHub token stored as an Azure app
    setting, because warehouse users won't have GitHub accounts — the
    SERVER files the issue on their behalf. Rate-limit or dedupe the
    endpoint lightly so a stuck scanner can't file fifty issues. Still
    open: whether the C72, the web terminal, or both get the page.

11. **Print labels FROM the C72 and pair them there — no PC/iPad in the
    loop at all** (added 2026-08-03; noted only, not designed). Today the
    C72 collects and pairs, but queueing labels and closing batches still
    route through the web terminal. Goal: the C72 queues the print jobs
    itself (the print agent already polls the server, so "printing from
    the C72" is really just "queueing from the C72") and walks the whole
    collect → labels → pair flow standalone. Needs a C72 UI for the
    label/print step and a think about where the Check step's human
    decisions land when no big screen is involved.

## 🗓️ Later / backlog

- **The "1-left check" app** (separate system — Inventory Verification,
  the one that asks a human to confirm 0/1-left counts). Reconnaissance
  written up in [docs/inventory-verification-app.md](docs/inventory-verification-app.md):
  where it lives, its full endpoint list, that it's webhook-driven, and
  that all ten operator-facing endpoints are ANONYMOUS. Possible RFID
  tie-in: its queue is 200+ items, and tag data can already speak to any
  batch-tagged SKU — read-only join first. Do NOT start without asking;
  its backend source isn't recoverable yet and its API needs auth first.
- Locate mode: max-power geiger-counter search for a specific EPC on the
  C72 (SDK supports radar/location APIs)
- Weak-RFID product flag (e.g. Optolong filters detune stickers): verify
  treats them as barcode-confirm instead of expecting tag reads
- Audits tab, real version: shelf audit + reconciliation (sweep rack →
  compare vs assignments + Shopify → missing/mismatch report),
  assumed-sold lifecycle, ambiguity groups
- Stock-number write-back to Shopify (needs SHOPIFY_WRITE_MODE
  "production" + confirm flow)
- Barcode captures upload from C72 (SEND in barcode mode)
- Tap-to-copy EPCs in tag lists
- On-metal / spacer sticker sourcing decision for problem SKUs

## 📌 Standing decisions

- **NEVER auto-write inventory counts** — to Shopify or any inventory
  system, from any device. Batch counts are observations; a future
  write-back is a separate, explicit, operator-confirmed step and stays
  OFF (SHOPIFY_WRITE_MODE) until testing is done. Correcting a display
  problem means fixing where data is READ from, never overwriting stock.
- **The TELCAN mirror is REMOVED from the app (2026-08-07, Nick's
  call).** Its dead sync (stalled 2025-12-08) poisoned records through
  every path it was left in — last straw: batch 126's ToupTek shelf got
  renamed SKUs (G3M662C for the live G3M662C-L) and handles cross-wired
  to the wrong products, breaking Shopify links and Review photos.
  509 records repaired via dev/repair_mirror_records.py (374 tags, 135
  batch items, 2 review tasks; 18 SKU transitions, History receipts by
  "mirror-repair"). Lookup order is now live bin map → live Shopify API,
  nothing else. The dbo.Shopify_* tables still sit in the database
  unused; dropping them is Steve's call.
- Expected/shelf counts display Shopify ON-HAND, pulled LIVE from the
  Shopify API (inventoryLevels quantities); the bin map's live-sourced
  snapshot (≤6h old) is the only offline fallback.

- Bins live in Shopify metafields (stock.bin → my_fields.bin_location);
  the TELCAN mirror's Bin_Name is empty store-wide
- ZD220t cannot RFID-encode → print agent runs --no-rfid; pairing is
  always two-scan
- Astronomik name-at-top labels: Scan Station only, everywhere else
  prints store header + SKU
- New Shopify-write features ship blocked until explicitly promoted
  (SHOPIFY_WRITE_MODE)
