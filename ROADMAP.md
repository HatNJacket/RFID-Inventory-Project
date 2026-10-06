# RFID Inventory System — Roadmap

Source of truth for project status. Updated by Claude each working session.
Last updated: 2026-09-28 (scope reset + C72 4.18, ON PROD).

## ⚠️ SCOPE RESET (Nick, 2026-09-28) — the app is an RFID EXTENSION, not a do-everything app

Three core workflows: **1. Label printing** (simplified scan station +
receiving strips), **2. Inventory verification** (massively simplified,
see the scored-audit design below), **3. Product locating**. Plus
returns-assist, batch tagging, and history past Shopify's 6 months.
Nothing has been removed yet - this section is the agreed plan.

**REMOVE (approved):** held/unpaired label strips + the printed-never-
paired warnings + label dismissals; multibox leftovers (companion tags,
boxset parts, multibox tables); sort handoff; Boxify dims mirror;
open-box LISTING tools (returns-assist stays); product merge/split UI
(scripts stay in dev/); C72 tuning/telemetry/debug plumbing; backorder
debt; the review task inbox (differences show live in audit instead);
the per-feature SHOPIFY_WRITE_MODE comma list (one on/off).

**KEEP (decided):** returns-assist (scan box → pick option → restock +
print label; tag paired by hand until an RFID printer); LINK relay;
draft-listing creation FIXED UP (dummy name format
"[INGREDIENT SKU] DRAFT BUNDLE COMPONENT -> [MAIN SKU]", two text
boxes autofilled - ingredient "SKU-X", main = SKU without the suffix -
normal confirm on the trigger); bundle contents, plus a NEW un-bundling
workflow (scan bundle → box count → confirm contents → deaden bundle
tags → print per-box labels) since the warehouse is moving bundles to
per-component SKUs; the first-batch-tagging lower ban STAYS.

**Guardrails:** predictive/blocking guards go (lower-capped-by-sales,
heard-sweep raise requirement, stale-sweep guard); what stays is the
interaction pattern (writes operator-confirmed + History + undo) and
the first-tagging lower ban. Audits become the source of truth; the
remaining prompts just ask the user to re-scan when a count is off.

**Scored audit queue (replaces 1-left/0-left marking) - DESIGN
SETTLED 2026-09-28:**
- New dataset: `last_heard_at` per tag (stamped by every sweep
  ingest) + `last_audited_at` per bin. Ship EARLY so heard-history
  accumulates before the queue needs it.
- Everything anchors at the bin's LAST COMPLETED AUDIT (audits are
  the source of truth and cheap to run).
- Per-product diff: |H − (heard at last audit − sold since audit +
  received since audit)| with H = live on-hand, sold from the
  ShipStation ledger, received from planner receipts.
- Bin score = SUM of its products' diffs (many small mismatches walk
  the bin too; no skew toward big fast-moving shelves). Queue split:
  bins overdue past a ~1-2 week Settings threshold first, then the
  rest; each half ordered by score.
- Expected value is a RANGE [min, max] of {H, tags − sold since
  audit}. A scan outside it prompts "check the count thoroughly",
  then manual confirm (normal confirmed on-hand write) or silent
  tags → locate list. NOTHING blocks the lower.
- Silent-tag triage via last_heard_at: silent count matches sales
  since audit AND each silent tag last heard before a matching
  order → "Sales agree" note (retire-sold strongly suggested);
  mismatch → note replaced with "might be a missing, misplaced or
  mislabeled product" message, locate-list add recommended harder.
  Heard elsewhere recently → offer re-bin.
- Confirm+undo stays until the system earns its removal (Nick).
  Auto-retire-on-fulfillment: designed toward, not built.
- The external 1-left bridge KEEPS auto-confirming their board
  (user-confirmed inventory closes that product's check) until the
  Unification hub collects the apps.

**Draft-listing autofill (settled):** box 1 (ingredient) autofills
the scanned code when it is a SKU (13-digit barcodes filtered out);
box 2 (main) autofills the suffix-stripped SKU whenever box 1 holds
a hyphen-number pattern ("ABC-2" → "ABC") - fired on scan OR when
box 1 is confirmed with that pattern by hand, even if the scan was a
barcode. Normal confirm on the trigger. Dummy name format:
"[INGREDIENT SKU] DRAFT BUNDLE COMPONENT -> [MAIN SKU]".

**BUILD STATUS (2026-09-28): phases 1-3, 5, 6 BUILT; phase 4
delivered light (see note). ✅ ON PROD 2026-09-28 (Nick's call),
dev/backfill_last_heard.py run against prod (2841 tags stamped);
dev mirrors prod again.** Detail in the two
scope-reset commits. Notes that survive the session:
- Held-strip removal kept LabelDismissal + the write-off flows on
  purpose: they silence blank/test labels in sweeps and power the
  unlinked hunt - removing them would resurface every discarded label
  forever.
- ReviewTask/ReviewNote MODELS stay read-only so History keeps every
  past event; all endpoints, creation sites and UI are gone. The
  mismatch/dupe checkers in orders_sync went with them.
- Phase 4 judgment call: the scan station kept its daily-driver flows
  (LINK, case/set/alias/edit boxes, print panel) and gained a
  one-click "Product card" jump; the WHOLESALE replacement of its
  legacy product panel by the card is deferred to the C72-update
  session - breaking the daily station mid-queue was the wrong risk.
  Nick can overrule.
- Run dev/backfill_last_heard.py once against each DB so old sweeps
  seed last_heard_at (consumers already fall back to assigned_at).
- The audit threshold lives in AppSetting "audit_threshold_days"
  (default 14); no Settings UI yet - say the word.

**AUDITS TAB OVERHAUL — ✅ BUILT + ON PROD 2026-09-28 (Nick's spec,
iterated over the "Audits Tab Redesign" preview artifact, 3 rounds):**
- Landing: ONE row of four home-style tiles (1-left checks, Audit
  queue, Packing, Unavailable stock; live numbers as corner badges).
  "Run an audit" and the cleanup subheader are gone - audits start
  from the recommended racks, a session, or the one-bin input.
- Audit sessions: description text gone. RECOMMENDED racks up top
  (the scored queue rolled up per rack prefix - overdue past the
  threshold first, then up-to-date, both by summed drift; open
  1-left checks ride as a chip; "Start audit" creates the rack's
  walk session in one click; tapping a card unfolds its scored bin
  pills). Open sessions are cards with a progress bar and a LIVE
  bin-pill strip. Finished audits sit behind the 🗄 icon (count on
  it, collapsible list below).
- Session detail: Rack ↔ Bins segmented views (progress ring +
  worst-open-bin callout vs a severity-edged bin-card grid with
  audit/mark-done per bin).
- Bin audit: table → product CARDS in red/yellow/green order, flags
  VERTICAL with their recommended action attached (Sales agree →
  Mark sold; unexplained silence → "Unpair + print replacement" in
  one step, warning kept; ghosts → Un-retire; label shortfall →
  Print N labels, the neutral W9177 path; out-of-range → re-scan
  hint + Set stock). Silent-tag drawer per card (per-tag last-heard
  + Unpair / Sold / Locate). Shopify on-hand is a −/+ stepper with
  one Apply (raise = normal confirmed write, lower = the guarded
  path, first-tagging ban server-enforced). Summary bar with the
  Audit-complete anchor button; strays/never-paired labels fold
  into one collapsed "Also heard" block.
- Recent sweeps is a real popover: pinned banner inside it, fresh
  dot per row, Pin buttons, write-off behind a ⋯ menu, tick-to-
  combine footer ("Check X with N sweeps"), Show older. The old
  bottom "Recent C72 sweeps" list is gone.
- Web-only: every action maps to existing endpoints (the one new
  wiring is DELETE /api/rfid-assignments/{epc} surfaced as Unpair).

**ROUND 9 (Nick's list, 2026-09-28) — ✅ BUILT + ON PROD:**
- Boxes & Tags: condition chip removed; the paired line says who
  paired the tag and through what work (server-derived source:
  Receiving / Batch tagging (bin) / Printed label / Manually
  scanned). Unpair button (light red) with a note popover (24ch box,
  hint "Optional Note", 200 max, Cancel/Submit below) - the note
  rides the tag-unlinked History event. Locate re-tinted light blue.
- Product preview card: "Set to N (tags)" button under On hand -
  one confirmed write aligning Shopify to the tag records' units
  (raises via the normal write, lowers via the guarded path).
- Card lookup speed: /api/product-history now accepts
  sku/barcode/pid from the caller and SKIPS its second full product
  lookup (which could hit the live Shopify API again);
  /api/products/tags?light=1 skips the live on-hand + label
  decorations for the open-box twin call; the client keeps a 5-min
  in-memory cache of resolved products. Net: one product resolve
  and one live on-hand fetch per card, repeats instant.
- C72 4.19 (code 137): FIND BIN and SWEEP tabs removed (sweeping
  lives in AUDIT; toggleScan stays for the batch steps); LINK is
  the FIRST tab; the gun restores its last tab on recreate and the
  manifest ignores keyboard config changes, so the BT scanner
  connecting no longer dumps the operator onto LINK; PACK mode
  removed (packing goes through an audit sweep; the server's
  /api/packing/* endpoints and the web pane stay).

**ROUND 15 (Nick, 2026-09-29) — ✅ BUILT: the C72 Audit tab lands on
a rack picker; the audit window is scoped to one rack (C72 4.24).**
- Entering the Audit tab, or leaving an audit, lands on a picker
  shaped like the batch tab's menu: a rack/bin box with autofill
  (racks by prefix; a rack's bins once a dash is typed), then the
  web's recommended racks as cards (Option A of the previews) in
  the web's order - overdue by drift, then up to date - each with
  age, bin count, mismatches, "Drift: N", open stock checks, and
  "in progress · 2 of 5" / "next: I1-3" / "continue · I1-2" chips.
- Tapping a rack (or typing one, or a bin, or scanning a BIN
  barcode) opens today's audit window with a rack strip: ◀ RACKS,
  RACK I1, "bin 2 of 5", the drift chip. The arrows walk only that
  rack's bins and stop at the ends. LOG AUDIT returns to the
  landing with the rack's next bin remembered on its card.
- Server: GET /api/audit/racks - the audit queue rolled up per rack
  (batch-done bins only, same as the web), open 1-left checks per
  rack (fail-soft), open walk sessions, and EVERY mapped rack's
  bins in natural order for the arrows. dev/tests/test_auditracks.py.
- 4.25 (same day, Nick): the list gets the room. One strip now:
  ◀ [bin · "Rack I1 · bin 2 of 5"] ▶ (tap the middle to jump to any
  bin); drift chip gone; ◀ RACKS moved to the bottom bar left of ⋯.
  The status box hides inside an audit - messages float in as a
  timed popup at the top (progress "…" chatter stays silent). Silence
  verdicts, range flags, MARK ALL SOLD and ALL CLEAR wait until the
  report came from a real sweep (live set or saved sweep).
  test_plurals.py went case-blind and caught 15 uppercase "(S)" leftovers.
- 4.26 (same day, Nick, walking rack I1):
  - Rack cache: opening a rack checks the open bin, then the rest of
    the rack in the background; arrows paint instantly from the
    cache while the evidence (collected set, or each bin's saved
    sweep) is unchanged. CHECK / any fix / a stopped sweep re-check
    and re-prefetch. The strip shows "Checking N tags..." /
    "Sweeping - trigger to stop and check" in place of the position.
  - After a check of the live set, tags whose products live on OTHER
    racks leave the collected set (this rack's other bins' tags stay);
    they come back if the audit moves to another rack.
  - Summary: "Strays" removed. Unexplained silence reads "N silent
    tags, unexplained by sales" with no button - tap the card.
  - Product window: "Confirm Stock Level" with square -/+, one button
    (Set to N / Lower to N / "Confirm N stock"). Confirm shows when the
    shelf is in the expected range and silence outruns sales+pickups:
    the extra silent tags retire as not-in-storage (missing, no sale
    consumed), Shopify untouched (F9143D: 0 on hand, 1 silent tag).
    Silent-tag rows: Unpair / Unsure / Locate (Sold stays on the web),
    compact. Unpair+print and Add-to-locate buttons gone; "Print a
    label" (owed count or 1), "Can't RFID scan" (won't-scan flag),
    "Open in station". ↻ REFRESH runs the read-only orders sync and
    re-checks skipping the 3-min pickup cache (F9168A: pickups
    fulfilled mid-audit), then reopens the window.
  - Printed labels heard unpaired: tap to pair each to its print job's
    product (homed to this bin) or dismiss.
  - Server: rfid_audit_unsure + POST/GET /api/audit/unsure,
    POST /api/audit/unsure/{id}/resolve; check items carry
    unsure_epcs; BinCheckIn.fresh. Web: "Marked unsure" beside Locate
    list (count badge) - Unpair / Sold / Locate / Dismiss per row; rows
    whose tag is gone resolve themselves. dev/tests/test_audit_unsure.py.
  - Unavailable stock (F9160A: 1 unavailable, not on the shelf, showed
    "expect 1"): the server now sends the SHELF range - shelf_lo =
    the sellable floor, shelf_hi = top + unavailable - and in_range
    tests against it; both UIs display it. On the C72, up to
    <unavailable> of a swept shelf's missing units are "explainable":
    a yellow "N unavailable units in Shopify - set aside, not on this
    shelf?" [Note it] row; one tap files rfid_audit_unavail_notes
    (POST /api/audit/unavailable-note, History "Unavailable Noted",
    read by the bin's checks until its next completed audit) and the
    card goes green when that was the only difference - otherwise the
    remaining silence runs the usual sales / pickup / unexplained
    flags and label-owed counts against the sellable expectation. The
    old auto-green "likely the set-aside unit" is gone on the C72 (the
    web keeps its old flag wording). dev/tests/test_unavail_shelf.py.
- 4.27 (same day, Nick, still on rack I1):
  - Speed. Measured on prod: the landing's /api/audit/racks took 8 s
    (the scored queue re-reads every tag, map and ledger row), one bin
    check 0.5-1.7 s, and the gun checked a rack's bins one after
    another. Now: the scored queue is cached (stale-while-revalidate,
    3 min, warmed at startup, dropped on LOG AUDIT / batch done; sqlite
    engines skip it); a saved sweep stamps last-heard ONCE, not per bin;
    the check's tag/ledger lookups use database.ci_in - plain IN on a
    case-insensitive collation (asked once), UPPER() elsewhere - so the
    Basic-tier DB can use its indexes; the gun loads the rack's bins
    three at a time beside the open one, keys its cache on collected +
    trimmed tags (trimming no longer discards the rack), and a fix
    re-checks only the current bin (the 20 s after Confirm).
  - Mark sold / ↻ REFRESH re-read that product's on-hand from Shopify
    into the snapshot (F9168A kept "expecting 3" after its pickups).
  - "Never got a label" INSIDE the expected range: Resolve asks for
    the shelf count (default = heard), files it (rfid_audit_stock_
    confirms, POST /api/audit/stock-confirm, History "Shelf Count
    Confirmed"), prints only count - tags on file; the check carries
    stock_confirmed so the card goes green (F9172D: right at 0).
  - Confirm-stock popup: one line, "1 silent tag is recorded in case
    it's found again." dev/tests/test_audit_cache.py.

**ROUND 14 (Nick, 2026-09-29) — ✅ BUILT: the Audits hub tightened
(iterated over live previews, approved). Web only.**
- Tiles first; one slim row under them: Up-to-date chip + Locate
  list + Sync orders.
- New-audit button/dropdown REMOVED. The promoted "Audit a bin or
  rack" box (under the sessions header) is the custom entry: bin
  token opens its audit, rack token starts the walk session.
  1-left sessions moved to a button in the checks pane; the
  packed-orders audit has NO entry until it lives in Packing
  (Nick's call - packedOpen() and its pane stay wired).
- Rack reco cards: one tap on the card starts the walk (button
  card; Start-audit button, bins hint and pill strip retired).
- In-progress card rebuilt: session short name INSIDE the ring
  ("J2" / "40%", tail-trim past 7 chars), Finish/Abandon pills
  under the ring, Resume bottom-right (accent pill; turns into a
  green Finish audit at 100%), Started-line tucked top-right, and
  a full-width segmented RUNWAY - one 78px hover-lit slice per
  bin, styled labels above dividers, walked slices filled, whole
  slice clicks through to that bin's audit.
- Copy rules landed + memorized: chips read "Label: value"
  capitalized ("Drift: 9", "Worst bin: X"); never "(s)" plurals -
  countNoun() in app.js. Global "(s)" sweep is a TODO below.

**ROUND 13 (Nick, 2026-09-28) — ✅ BUILT: bundle cards in the batch
steps (Design C) + the three-way display setting.**
- A kit renders as one purple card with its component rows tucked
  inside - the SAME rows and steppers, so counting never moves and
  nothing double-counts. Families merge listings sharing a pool
  (the x10/x5 case is ONE card with both masters). Per-master chip:
  "covers N listed ✓" / "builds N of M" / "builds 0 - short on X".
- The "Bundle cards" mode on BOTH terminals: Started (any component
  box scanned - default), Buildable (only kits the scans complete),
  Off (flat list). Web: a segmented control per batch step
  (localStorage); C72: a Settings card that cycles (prefs).
- Web: collect / check / pair group into cards; verify gets purple
  family header rows in its table (filter-aware). Check gains the
  approved BOX WALK flipper (listing > product > box, ◀ ▶, per-box
  tick, Fix count through the real qty write).
- Server: batch payloads carry `box_sets` built from bundle
  families - the C72's DORMANT multi-box grouping UI (2026-09-09)
  drives the kit cards, with a per-kit `qty` on parts (the gun
  divides; old sets were one box each). Components shelved in
  another bin ride as read-only rows naming their bin.
- C72 4.21 (code 139): bundle-aware headers (📦 + recipe +
  buildable/listed tracker), per-kit qty prefixes on member rows,
  grouping extended to CHECK, the Bundle cards Settings row, a
  WALK button on CHECK opening the box flipper dialog, and the
  check list's tap now maps by ITEM (the old position mapping
  broke under reordering).
- /api/bundles gained the listing's own on_hand snapshot (the
  "covers N listed" denominator). Covered in test_bundles.py.

**ROUND 12 (Nick, 2026-09-28) — ✅ BUILT: bundles.app bundles as
first-class RFID records.**
- POST /api/bundles/pull walks every variant's bundles_app.content
  metafield and builds/refreshes the definitions in one go
  (BundleContent + the new rfid_bundles table: title, source,
  synced_at). App-sourced bundles re-sync on every pull; a bundle
  the operator edited by hand flips to "manual" and survives pulls
  until re-imported. GET /api/bundles is the enriched index
  (component titles/bins/snapshot stock/tag counts + buildable,
  ?sku= and ?component= views).
- Inventory tab: a Bundles panel (📦 button at the right of the
  Bin/Vendor/Sort filter row) with the Pull button and per-bundle
  cards - add/remove components inline (wholesale replace through
  /api/bundle-contents, History receipt as before). The tab's
  search box narrows the bundle cards too (bundle SKU/title/
  barcode or any component's SKU/title).
- SOLD LEDGER: bundle sales are COMPONENT sales now. Both feeds
  (ShipStation shipments + voids, Shopify fallback) explode bundle
  line items into component lines at ingest, and the pull re-books
  any old rows still sitting under a bundle SKU (quantities and
  parcel maps scaled, pre-baseline rows settled). Audits can now
  explain component silences that bundle sales caused.
- Audit: bin checks hold defined bundles out of the report and
  return them as covered_bundles - the web report shows a "covered
  by their components" line instead of a never-tagged flag.
- Locate: queueing a defined bundle expands to its components
  server-side (the C72's LIST hunts real tags; web callers show
  the expansion message).
- Product card: a bundle strip - component rows (qty, bin, tags,
  on hand, tap-through) with the buildable count when every
  component's stock is known; component cards show "Part of
  bundle(s): X (n× each)" chips.
- Batch tagging needed nothing new: defined bundles were already
  held out at collect (covered_bundles note) - the pull just makes
  that automatic. No C72 changes (server-side data covers the gun).
- Tests: test_bundles.py (+16 checks) and test_shipstation.py
  (bundle shipment + void unwind).

**ROUND 11 (Nick's list, 2026-09-28) — ✅ BUILT + ON PROD: the
In-progress card IS the walk.**
- The In-progress session card carries the rack view itself: ring +
  bins-walked + drift + worst-open-bin on the left with Finish
  audit / Abandon under them, progress bar and bin chips moved
  right. The Rack/Bins detail pane for walks is GONE (the per-rack
  bin grid entirely removed); a finished walk's View shows the same
  card read-only. 1-left sessions keep their tick-list detail.
- Resume opens the bin audit on the FIRST bin not walked yet; Start
  audit and the + New audit form (bins kind) land there too instead
  of on the old detail pane.
- A bin with NO sweep at all opens as its expected list (product /
  SKU / Shopify / RFID tags / diff, rack token = each of its bins)
  with a sweep-and-Run prompt - never a dead-end message. Fresh
  captures (<5 min) still auto-run the real check first.
- An audit sign-off (web ✓ button or C72 LOG - both post
  /api/bins/{bin}/audit-complete) now TICKS that bin in every open
  bin-walk session server-side; a rack sign-off ticks all its bins.
  That replaces the grid's "mark done" button. Covered in
  test_audit_sessions.py.

**ROUND 10 (Nick's list, 2026-09-28) — ✅ BUILT + ON PROD:**
- Web: the session cards were being flattened by recent__list's
  row styling (Nick's screenshot) - the list is a plain div now and
  the cards stack properly. Sessions + recommended racks paint
  INSTANTLY from localStorage on tab entry (slim caches of the last
  visit; the freshness tag still says when live data lands).
- Per-location saved sweeps: EpcCapture grew a `bin` column
  (idempotent upgrade). Audit captures name their location, a check
  claims unstamped captures for the bin it ran against, and
  GET /api/bins/{bin}/sweeps lists a location's history (rack
  sweeps cover their bins). Opening a bin on the WEB or the GUN
  with nothing collected shows the location's latest saved audit,
  amber-marked when from another day, still fully usable.
- Faster sweeps: gun requests gzip (reports shrink ~8x); audit
  checks save the collected set ONCE as a capture and every check /
  re-check / sign-off goes by capture_id; batch verify accepts
  capture_id the same way. The EPC list crosses the Wi-Fi once per
  sweep instead of once per press.
- C72 4.20 (code 138), the audit tab matched to the web: summary
  strip (products / all match / flagged / strays), verdict FLAG
  ROWS inside each card with their recommended fix inline (Mark
  sold / Un-retire / Unpair+print / Print N / Set to N), the
  product dialog rebuilt on shared actions with the on-hand STEPPER
  (one Apply, raise or guarded lower) and the silent-tag drawer
  (per-tag last-heard + Unpair / Sold / Locate), strays folded into
  one "Also heard" row. STATION hold-sweeps now PAIR every heard
  tag to the loaded product (bulk, duplicates skipped) instead of
  just sending a capture. Every tab has its own default trigger
  power again (Settings), audit and returns included.
- test_binsweeps.py covers the capture/bin/verify surface.

**C72 UPDATE — ✅ BUILT 2026-09-28 (C72 4.18, code 136), Nick's
five asks + the queued cleanup, one session. ✅ ON PROD 2026-09-28:
prod serves the 4.18 APK, so the gun self-updates on its next app
open pointed at prod. Once 4.18 is confirmed on the gun, delete the
/api/c72/* stubs.**
- Dialog buttons stay in ONE horizontal row (the platform stacked
  every confirm's buttons into a scrolling vertical list; fixed in
  dlg() for all ~50 dialogs at once).
- The AUDIT tab's list IS the check screen now: the pop-up window is
  gone, LOAD/arrows run a real /check, CHECK re-submits and repaints
  in place, and STOPPING a trigger sweep re-checks by itself. Cards
  show the SKU, sort red → yellow → green → untagged, and carry the
  web's verdict language (expected RANGE, Sales agree, "missing,
  misplaced or mislabeled", out-of-range re-scan prompt).
- LOG AUDIT (new bottom-bar button) signs the shelf off through
  /api/bins/{bin}/audit-complete - the gun anchors BinAudit rows now,
  same as the web.
- "PRINT X LABELS - expected but not paired" in the product fix menu
  (the W9177 case): N defaults to expected − units-here, editable,
  queues plain /api/print-jobs - pairing them NEVER moves on-hand.
- PACK toggle on the LINK tab: PACK mode posts every barcode/trigger
  read to /api/packing/scans, verdict rows in the feed, ding/buzz.
- Draft dialog autofills per the settled spec (box 1 = scanned SKU,
  13-digit barcodes filtered; box 2 follows the hyphen-number
  pattern live; main_sku sent to the server).
- Cleanup landed: tuning/commands/telemetry client plumbing removed
  (field-tuned values baked in; the heartbeat is the new
  POST /api/link/presence), sort-handoff sender removed, multibox_ok
  dropped, companions_skipped/companions_heard keys removed from the
  server + the web's dead renderer. The /api/c72/* server stubs STAY
  until C72 4.18 is confirmed on the gun (the 4.17 APK polls them
  every 2 s until it self-updates) - then delete them.
- The broken GET /api/c72/debug-log (referenced the deleted
  C72DebugEvent model, latent 500) is gone.
- STILL OPEN from phase 4: the wholesale scan-station panel
  replacement (Nick can call it).

**Build order (one session per phase, Nick fires each):**
1. Removals (independent; shrink everything after).
2. last_heard_at + last_audited_at dataset (can run parallel with 1;
   ship first so data accumulates).
3. Expected-range + scored queue + silent-tag triage (replaces the
   review inbox and internal 1-left marking).
4. Scan-station thinning + returns-assist.
5. Draft-listing fix + un-bundling workflow.
6. Packing scan (planned 2026-09-28, Nick's personal tool, lives as
   a subset of Audit): C72 PACK toggle POSTs every read to
   /api/packing/scans; server resolves SKU/EPC and allocates against
   ShipStation's awaiting-shipment orders (cached, oldest first) →
   "scanned not fulfilled" / "duplicate" / "not part of shipping",
   and rows flip to "shipped" live when the hourly sync sees the
   label. Web: live Packing panel (rows + summary, tap to remove a
   mis-scan, rows swept after days like LINK). One pack_scans table.
   KEY: the EPC↔order link makes tag retirement EXACT for anything
   scanned at packing - no silent-tag inference. Gun answers with
   good/warn beeps only. OPEN: auto-retire linked tag on shipment
   confirm (lean yes - internal + undoable); rolling-day list vs
   explicit sessions (lean rolling); barcode scans allowed but
   badged "no tag" (no retirement precision).

## 🎞 Strip pairing box (Nick, 2026-10-06) — PLANNED, Nick builds the hardware after work

Labels print barcode-only (no encoding printer), so every tag is paired
by hand, one antenna touch per label. The box pairs a whole printed
strip in one pass: the strip feeds past a shielded window, the gun
reads one tag at a time, and the print order says which job each tag
belongs to. Agreed design:

- **Tunnel:** aluminium rectangular tube or U-channel (no printing, no
  foil seams), inside width = liner + 1-2 mm, height 2-3 mm, about
  8 cm each side of a window cut in the top. A slot that narrow is a
  waveguide below cutoff at 915 MHz (~2 dB/cm plus detuning), so tags
  inside stay quiet at power 1-3. Window ≈ 3/4 of a label pitch.
- **Path:** vertical, strip hangs from above and drops past the window;
  gentle entry/exit flares in the scanning plane so the strip droops
  under its own weight (no left/right turns). Labels face the gun, so
  any curve that bends AWAY from the gun puts them on the outside of
  the bend - keep that radius ≥ 30 mm or curve toward the gun side.
- **Roller:** one rubber drive wheel (printed hub + O-rings or a TPU
  tyre) against a spring-loaded idler, AT THE OUTPUT below the window,
  pushing the spent strip out the back. Every label clears the window
  before the tail leaves the roller. Leader card taped to the strip's
  start for threading. NEMA 17 + A4988/DRV8825 (the 28BYJ-48 tops out
  near 1 label/s; Nick wants a few labels/s, speed as a setting).
- **Shielding:** adhesive aluminium tape on printed parts. Hood the
  gun's UHF antenna head only (top module) - the Wi-Fi radio is in the
  body and must stay in free air. Box lives away from stray tags.
- **Controller:** ESP32 (Wi-Fi), never Bluetooth to the gun. It polls
  the server for feeder commands exactly like the print agent polls
  printer-commands, with its OWN device key limited to the feeder
  endpoints. Physical jog button for threading.
- **Gun "Pair strip" mode (1-2 days):** loads the batch's unpaired jobs
  in print order; auto-scans at power 1; each NEW EPC (first-heard
  order, RSSI peak time breaks ties) pairs to the next job through the
  existing pairing call, beep per label, different tone at a SKU
  boundary, one-tap undo. Continuous feed means it can't just count:
  it watches CADENCE - a gap of two pitch-times = a label went by
  unread -> tell the feeder to stop and back up two pitches, ask dead
  tag (void the job) or retry. Two tags at once -> take the loudest,
  warn; persistent = window too wide. Barcode check at SKU boundaries.
- **Server (half a day):** feeder command queue + pair-strip order
  endpoint; a manual "advance" button on the Queue tab doubles as the
  first feeder before any motor exists.
- **Order of work:** tunnel + cradle, hand-pull test at power 1 (one
  tag per position is the go/no-go), then stepper + ESP32, then the
  gun mode. Print order is only trustworthy for a run with no faults
  (swallowed-label retries and reprints reorder the tail), which is why
  the SKU-boundary barcode check stays.

## 🖨 SO 969 post-mortem: double booking + lost-status reprints — ✅ DEPLOYED 2026-10-06

A 69-label ZWO order queued 219 labels. Three faults, three fixes:
- **Overlapping syncs** - the planner's Save books in the background
  (slow: a Shopify lookup per line) and Print all 24 s later ran a
  second sync; neither saw the other's uncommitted batch, both booked
  the whole order, then the print step queued 69 labels on EACH. Now
  one sync per stock order at a time: a process lock plus a SQL Server
  `sp_getapplock` (transaction-owned - prod runs 2 gunicorn workers) in
  `M.so_serialized`, also taken inside `_receiving_intake` for the
  legacy /prints and /unprinted paths. The planner serializes its own
  callers per order too (asyncio.Lock around `_rfid_sync`).
- **Lost printer status failed printed labels** - the ZD621's ~HS went
  quiet mid-run six times (new since v9; cause still open) and the
  agent failed the whole burst, labels it had itself confirmed
  included; `/fail` happily flipped 90 done rows to error and the next
  pass printed them again. Agent v10 reconciles against the odometer
  after a mid-run fault (counted = printed, only the rest fail) and
  logs each missed status reply; `/fail` refuses done/canceled/voided
  jobs with a 409.
- **Abandoned batches counted** - `_so_batches` skips them now, so
  abandoning the duplicate batch (#356) is the cleanup. Finished
  batches still report their printed labels, so the planner shows
  "8 printed" instead of a dead "Print 0" (SO 965).
- **Clear stopped** (Nick): Queue tab button next to Resume - stopped
  labels stay canceled, leave the list, Resume switches off
  (`POST /api/print-jobs/clear-stopped`, History "stopped-cleared").
- **OPEN - Unicode SKUs:** "ZWO FS-Ⅱ" is stored as "ZWO FS-?" (all SKU
  and title columns are VARCHAR under a Latin-1 collation), which is
  why the planner's Print button did nothing on SO 969: the total said
  9 owed, no line matched. Planner matches through a cp1252 fold for
  now; the real fix is NVARCHAR columns + a repair of the "?" rows.
  Needs a quiet window on prod (indexes drop/recreate).
- **OPEN - why ~HS goes quiet under v9/v10's continuous feed.** Watch
  the agent log for "no ~HS answer"; if it recurs, make the mid-run
  refill opt-out and fall back to v8 pacing.

## 📦 Shipment sorter polish — ✅ DEPLOYED 2026-10-06

Four quick asks while receiving: scans QUEUE while a lookup runs
(1cb5aba, no more "sorter was busy"); a "Recently scanned" list under
the status line, newest first with time/code/outcome, kept with the
pile (851e74d); a loud blip/buzz per result via the sorter's own tones
(audio context warmed on any keystroke/click - f2ebb23, 43dbe44); and
"Print N labels + clear" on the unexplained group, each SKU recorded as
History "sorter-leftover" and listed in a small "Leftovers printed"
fold under the sorter (c6008a1, POST/GET /api/sorter/leftovers).

## 🧭 Audit UI overhaul — PLANNING (Nick, 2026-10-05) — stages 1-2 of the verdicts BUILT

Trigger: bins like K4-2 hold solid-metal products that block RFID and
need box-by-box sweeping. An AUTOMATIC scan-difficulty score was
proposed and REJECTED ("too much, will cause problems"): flags are set
by hand. Mockups (8 artboards, gun + web) live on the Design canvas
"Audit UI Overhaul Previews" https://claude.ai/artifact/DeWUN8TQKrCxPMGZKz81oJ
(private to Nick until shared). Order of work Nick set: **1. user
authentication, 2. everything below.** Each starred item gets its OWN
intentional session, not a pass inside a big batch.

**Decided:**
- One state per product card, one primary action (Match / Short /
  Covered / Over / Needs labels / Count by hand / Not in RFID); the rest
  one tap deeper (Fix / Investigate / Flags).
- A "Count by hand" divider in the same bin list: RFID-incompatible and
  non-taggable products show on-hand + a counted stepper (− n +, tap the
  number for a keypad, a barcode scan adds 1); hard-to-scan ones also keep
  a "Heard tags: X/Y" counter and still flag silent tags. Count by hand
  is the DEFAULT for incompatible products, with a way to sweep them
  anyway if the operator thinks it will work.
- Audit sessions (Finish/Abandon, runways, finished-audits drawer) are
  DROPPED on both surfaces: opening a bin or rack IS the audit; "logged
  N of M" on the rack card is the progress.
- Gun audit = racks + 1-left checks ONLY; Packing and Unavailable stock
  stay web-only. Web hub keeps its four tiles; recommended racks are the
  main body; "Audit a bin or rack" shrinks into the button row.
- Both rack lists stay in drift order (the preview had G3 under F9 - a
  mockup slip, not the rule).
- Web keeps desk-only powers (bundles + connected inventory, ghost
  cleanup, batch-tagged record) but NOT in a side "Desk tools" box -
  fold them into the product cards/list. Nick dislikes the preview's bin
  page as drawn; ★ redo it with SEVERAL preview variants.
  **Web bin page session 2026-10-06** (canvas "Web Bin Audit Previews"
  https://claude.ai/artifact/HmrEXbU4ozEwPNp3B6Lds8, 4 layouts at the
  real 1334 px width): Steve picked **B, list + detail panel**. Left:
  the bin's products grouped To resolve / Match / Count by hand (matches
  listed in full, as in B); right: the selected product's numbers,
  its problem rows with their buttons + Resolve all + Count instead,
  then tags on file here, bundles and connected inventory, and "since
  the last count" timeline (the desk-only powers live here, no Desk
  tools box). Top bar: bin arrows, sweep picker (replaces Pull latest
  sweep & check), bin ⋯ menu, Log audit. The list's count column is
  reduced to Heard/Shopify ("2/3") with Expected underneath and a
  header indicator - Steve picked the BAR: "heard/Shopify" numbers
  (hover: "N heard in this sweep" / "N on hand in Shopify", no header
  words) over one block per Shopify unit, filled when heard, a white
  tick at Expected; heard beyond Shopify shows as dashed extra blocks.
  Header keeps only a tiny legend (heard / Shopify / expected). "Also heard":
  NO per-tag list or Move here (racks are too close; nearly every
  sweep hears more strays than expected tags) - just the chip, with
  the hover note "Tags heard in this sweep that belong in other
  racks".
- Gun ⋯ menu: Pull latest / Pick + merge / Send sweep GONE (sweeps save
  as the trigger stops). Clear becomes a main-screen button. ★ Plan the
  menu around what users do there 90% of the time: pick/clear/switch
  sweeps; optional, hidden-by-default sweep label/name. (Bin flags
  dropped 2026-10-06.)
- ONE Locate function: audit (silent tags, one product, whole list) opens
  Locate preloaded with its values; no separate "audit locate". ★ Plan the
  product → list → silent-tags workflow and Locate's own controls.
- Product window: keep Refresh/Back, the image header and the Confirm
  Stock Level stepper; descriptive text moves behind [?] icons.
- ★ [?] help system (LARGE, later, once the C72 is settled): replace the
  plaintext bullet help with per-window help - a tap-an-option overlay
  or a dictionary of functions with illustrations/screenshots.
- Flags sheet: one-sentence (max two-line) descriptions are fine HERE.
  ★ Review which flags are really needed vs solved elsewhere.
- **RFID-incompatible flag - PLANNED 2026-10-06 (team meeting agreed)**,
  previews: Design canvas "RFID-Incompatible Flag Previews"
  https://claude.ai/artifact/1dRBQphGXYo2EBPhx3mQmS. ONE product flag
  replaces won't-scan (rfid_incompatible), un-labelable box and
  non-taggable (rfid_non_taggable, both kinds). Two toggles under it:
  "RFID tag / Paper label" and "One per product / One per bin" (the
  illustration switches: labels each pointing at a box vs one label
  pointing at a bag). Thumbscrews = RFID tag, one per bin.
  Non-taggable is gone as its own idea.
  - Web product card: Steve picked "Lookup A" on the canvas (2026-10-06).
    The flag sits in the card's right-hand column (280 px at 1920 wide):
    a switch row, then two pill toggles stacked ("RFID LABEL | PAPER
    LABEL", "ONE PER BIN | ONE PER PRODUCT"; each pill is ONE button
    that flips wherever it is clicked, not a pick-a-half control), then
    one white picture showing the current choice with a caption ("One
    RFID label for the whole bin"). Pictures only illustrate, never
    click. The card also reacts: label preview gains the no-scan mark,
    one-per-bin adds "ALL STOCK" to the bin line, the tags tile reads
    "Tags ignored", the print line names the printer.
  - Printed labels carry a no-scan mark (crossed signal arcs) in the
    corner; one-per-bin labels add "ALL STOCK" to the bin line.
  - Paper labels print on the second (non-RFID) printer: a Munbyn
    ITPP941 on the same warehouse laptop (Windows queue "Munbyn
    ITPP941", port USB003, driver 2.6.2.1). It speaks TSPL, NOT ZPL
    (driver DLL emits SIZE/GAP/BITMAP/PRINT), so the agent needs a TSPL
    label builder sent RAW through the spooler; no odometer readback.
    Roll: 2.25" x 1.125" labels, design to ~2.0" x 1.0" printable.
    No EPC, no pairing. Tested at the printer 2026-10-06: TSPL's BOX
    and/or small built-in fonts FREEZE it (power cycle needed); a single
    big TEXT line and a BITMAP print fine. So paper labels go out as ONE
    rendered image (TSPL BITMAP over direct USB). Layout approved by
    Steve, prototype in dev/paper_label_prototype.py. SKU line guardrail
    (tested at the printer): 27 px bold, up to 440 dots wide (about 1 mm
    from each label edge), shrinking to 15 px, refusing below that. All
    3,526 live SKUs fit: 3,476 at full size, 50 shrunk (smallest 17 px,
    the 47-char Optolong L-PRO name). The check is by measured width, not
    character count; the label editor should still warn when a paper
    label's line would be refused.
  - PAPER PATH LIVE 2026-10-06 (e18aa82, agent v11): stock="paper" jobs
    (kind "paper", printer "warehouse-paper") print only through the
    paper agent's exclusive claims; RFID agents skip them; no tag
    record, never owed pairing; per-printer agent status with a "paper"
    summary; the web picker hides the paper printer. Warehouse laptop:
    C:\rfid\paper\ holds its own print_agent.py copy (self-updates
    separately), run_paper_agent.cmd (--paper --printer-id
    warehouse-paper, log C:\rfid\paper\paper_agent.log), started from
    the TCWarehouse Startup folder ("RFID Paper Agent.cmd") exactly like
    the Zebra agent. Pillow installed there by Steve. FINDING: the Zebra
    agent also runs from that Startup folder as the non-admin tcwarehouse
    user (the shell channel can't read or create scheduled tasks), not
    as the SYSTEM task the CLAUDE.md note describes. First live paper
    label (job 4726) printed end to end. Still to build: the flag UI
    that queues paper jobs.
  - Audits: incompatible products are ALWAYS hand counted (stepper,
    keypad, barcode scan adds 1). Heard tags are ignored entirely: no
    "Heard X of Y", no silent-tag rows, no "sweep anyway" option.
  - Locate: incompatible products only show their bin; the details and
    the "this product is flagged" alert belong to the Locate session.
  - Batch tagging: one-per-product stays in the batch (labels print,
    the verify sweep skips it); one-per-bin leaves the batch and prints
    one label for the bin.
  - Bin-level flags are OUT (setting a whole bin incompatible would flag
    products that scan fine). Not built, and dropped from the gun menu
    plan.
  - Prod state at planning: won't-scan on ZWO DESICC (+ a stray
    "ZWO+DESICC" row to delete), 2459281, 2459286 -> RFID tag, one per
    product; CR2032 un-labelable box -> RFID tag, one per bin; no plain
    non-taggable rows.
  - Printer routing RISK: jobs with no printer named go to ANY agent
    that claims. A paper agent must never claim those, or RFID labels
    print on paper un-encoded. Server routing has to ship BEFORE the
    paper agent starts.
- ★ Expected range + verification logic: "Needs labels: N boxes never got
  a label" is wrong more often than right - rooted in how Expected is
  built. Walk through expected_qty/shelf_lo/shelf_hi/in_range vs heard
  counts case by case. Same session: bin load speed and RE-SWEEPS (a
  second sweep re-checks everything instead of folding in the new tags;
  suspect GraphQL lookups vs the DB and the DB's size).
- Bottom bar buttons and the card UI itself: ★ redesign in the Expected
  session.
- **Expected session groundwork (2026-10-05, after Nick read the
  logic summary).** Decided: three numbers per card (Shopify / Tagged
  here / Heard), one verdict ladder; Expected becomes ONE number
  (on-hand trued up live for the whole bin at rack open, age shown),
  no range. In-range still prompts "check this product" and offers a
  one-tap resolve: set on-hand to heard, retire the silent tags as
  SOLD where orders/fulfilment cover them, else as MISSING. Heard
  means heard HERE (other bins' tags go under "also heard"). Compute
  sweeps on the gun from one rack fetch; the ledger auto-clear leaves
  the check. One shared verdict function for both surfaces.
  Causes of heard != true, by evidence (build the explanation lines
  from A+B, flag only the residual, C is the user's call, D by
  stamping the sweep time and freezing the records for re-sweeps):
  A records know: shipped since last audit; local pickup staged;
  Unavailable bucket (+ noted); received not shelved; returned item's
  old tag answering (ghost); label printed never applied / paired then
  stuck to nothing / reprint paired twice; POS + manual non-shipped
  fulfilments (GAP: ShipStation never sees them); order edits that
  swapped the item; cancel/refund restocked; sealed-case units;
  bundles; manual on-hand edits in Shopify.
  B the sweep shows: tag not read (metal, position, power, stopped
  early; 1-2 read answers = near miss); tag read from the neighbouring
  shelf; tag recorded in bin A but box in bin B; hard-to-scan /
  incompatible (silence expected); dead tags.
  C only a person: box moved elsewhere (packing zone, demo, desk);
  sticker peeled/transferred; vendor mislabel / shared barcode;
  split-shelf product (one on-hand, two bins, split unknown).
  D timing: 3 h snapshot + 1 h ledger + 3 h bin map lag; sale between
  sweep and check; mid-audit actions changing records between sweeps;
  the sales-window anchor differing before/after a bin's first Log.
  Pre-session read-only checks: how many of last month's fulfilments
  were non-shipped and whether the ledger has them; how many products
  live on split shelves.
  **SUPERSEDED the same evening by Nick's direction: weight the
  PREVIOUS and CURRENT COUNTS, not a pile of explanations.** Anchor:
  Last count (the Log baseline; never audited = tags paired here) ->
  Expected now = last count - sold since (ledger) + received since
  (receiving records), nothing else moves it. Heard = heard HERE.
  Shopify on-hand = reference line with age + "set on-hand to N",
  never the verdict's input. Three priorities, in order: (1) silent
  tags covered by order history: pair silent tags' last-heard with
  sales since the last count, one tap Mark sold retires exactly those
  tags and consumes exactly those ledger rows; a tag heard after its
  supposed sale is just not covered. (2) Unavailable stock: silent +
  covered = green with "N set aside"; a HEARD unavailable unit offers
  "Return N to available" (existing confirmed write) so the bucket
  gets fixed as it's scanned; set-aside units found elsewhere get a
  location. (3) Problem vs simple fix, from facts: received-not-put-
  away (receiving record since last count, units unpaired -> Print/
  Pair); it's-somewhere-else (NEW: stamp WHERE a tag was last heard -
  sweeps, locate, packing scans all know their location -> "last heard
  at Packing 10:40 today" + Locate); nothing explains it -> red
  "missing since <date>", the only red, with Confirm stock. Dropped:
  the range, pickup/bundle folds in the arithmetic, explanation lines
  for every cause. First build step: the last-heard location stamp.
  Three more rules (Nick, same evening): (a) ANY Shopify mismatch is
  YELLOW with a resolution, even when the count anchor agrees -
  Shopify is the source everyone uses, so if it's wrong we must know;
  green = Shopify, records and shelf all agree. (b) Unavailable stock
  resolves TOWARD the truth: default = clear the unavailable units
  and set on-hand to what the audit believes (Shopify model: on-hand
  INCLUDES unavailable, so e.g. Unavail 1 / On-hand 3 / 4 heard =
  move 1 unavailable->available AND raise on-hand to 4, one Resolve
  button, both in the History row; cards must display it that way).
  If the product's unavailable metafield carries a staff comment,
  Resolve shows it first and asks to proceed. IMPLEMENTATION RULE:
  always MOVE unavailable->available (inventoryMoveQuantities, on-hand
  preserved) and then SET on-hand to the counted value; never adjust
  unavailable directly - Shopify admin's unavailable editor is an
  adjustment that also lowers on-hand (Nick hit Unavail 1 / On-hand 0
  -> clearing it gave On-hand -1, a 2-unit fix-up). A negative
  available (oversold) shows plainly on the card as its own problem. (c) Red "nothing
  explains it" Resolve = ONE window: Shopify on-hand, tags heard,
  sales since last audit, collapsible timeline since last audit, and
  the counter preset to heard with a Confirm that writes whatever the
  user enters - no "doesn't make sense" guardrails (inventory can be
  fixed later). Only exception: entered count LOWER than tags heard
  in this audit -> prompt a re-sweep of that product; still high ->
  edge-case workflow (locate each heard tag: mis-paired tag vs missed
  box), to be designed. Implication: the first-tagging lower ban does
  not apply to audit resolutions (treated as decided unless Nick
  says otherwise).
  **Verdict spec settled 2026-10-06 (planning only, nothing built):**
  numbers per product/shelf: Last count (Log baseline; never logged =
  batch-tagging count), Sold since, Received since (planner receiving
  history = truth; a rise between our 3-hourly snapshots with no stock
  order shows as "on-hand rose by N, no stock order", never as
  received; batches not used until robust; Shopify inventory history
  only if the API exposes it - verify), Expected now = last - sold +
  received, Heard here, Heard elsewhere (listed, never counted),
  Shopify on-hand/available/committed/unavailable live at rack open
  with age. Card shows at most Expected · Heard · Shopify + verdict
  lines; the rest is in the product/Resolve windows. Ladder (first
  match; rows 3-6 stack): 1 retired tag answered (red, Un-retire);
  2 heard > expected (yellow, Resolve preset heard); 3 silent paired
  with a sale since last count (Mark sold, NO confirm when the
  pairing is clean, consumes exactly those ledger rows, undoable);
  4 unavailable covers it (Resolve unavailable: move then set, staff
  comment first - product metafield "Staff Comments", look up its
  namespace/key read-only); 5 received not shelved ONLY with a stock-
  order receipt record (Print/Pair); 6 silent tag heard elsewhere
  recently (Locate); 7 nothing explains it (red "missing since",
  Resolve window: Shopify figures, heard, sold since, collapsible
  timeline, counter preset heard, Confirm writes freely; count below
  heard -> re-sweep -> tag-by-tag locate flow TBD); 8 Shopify
  disagrees after all of the above (yellow, Set on-hand, evaluated
  LAST); 9 match (green); 10 incompatible/non-taggable -> hand count.
  Several lines on one card: stacked, each with its button, worst
  colour wins, plus "Resolve all" that runs them in ladder order after
  one summary and pauses only for input (comment, count), recomputing
  after each step. Timing: sweep time is the reference; records
  frozen at rack open; actions refresh only that product; Log writes
  Last count = heard (or the hand count) + time, and takes over the
  ledger auto-clear. Runs: one rack fetch (numbers, EPC lists with
  recorded shelf + last-heard, sales since, receipts) -> the gun
  computes every sweep locally; one shared ladder function on the
  server for the web. Data changes: last-heard (action, location,
  time) on tags - "last heard during F9-2 audit / D7-2 batch tag sweep
  / packing scan / locate", stamped by every read incl. other bins'
  tags heard during an audit; receipts per SKU per shelf with
  paired/unpaired; sale<->tag consumption records for Mark sold;
  Log baseline gains time + hand counts.
  **Card treatment + copy (Nick, 2026-10-06; sketches on the canvas
  row 3):** Card B wins - the list card keeps its height, one summary
  line "N to resolve: frag · frag · frag" and one "Resolve (N)"
  button opening the Resolve window (numbers strip Expected · Heard ·
  Shopify · Unavailable + last count date; one row per problem in
  ladder order with title, description, its own button; "Resolve all
  (N)" listing the sequence; "Count instead" always present). Say
  "unavailable", never "set aside". Copy per problem (fragment /
  title / description / action):
  1 "retired tag answered" / Retired tag answered / "…A41F2C was
    retired as sold on Sep 28, but it answered this sweep. The box
    never left." / Un-retire.
  2 "heard 4, expected 3" / More heard than expected / "Expected 3
    from the last count, 4 tags answered here. Check for a tag on the
    wrong product or a box that was never counted." / Count and set.
  3 "1 shipped" / 1 silent tag, shipped / "…7C21E0 was last heard
    Sep 30 during the I1-2 audit. Order #50527 shipped on Oct 1." /
    Mark sold (no confirm when clean).
  4 "1 unavailable" / 1 silent tag, set as unavailable / "…B0D4F1 was
    last heard Sep 30 during the I1-2 audit. Shopify holds 1 unit as
    unavailable. Staff comment: '...'" / Clear unavailable (comment
    first: Keep unavailable / Continue).
  5 "2 received, not shelved" / Received, not shelved / "Stock order
    #1003 received Oct 3: 2 units, no labels paired yet." / Print 2
    labels.
  6 "1 heard elsewhere" / 1 silent tag, heard elsewhere / "…D91E07
    was last heard today 10:40 during the packing scan." / Locate.
  7 "1 missing" / 1 tag missing since Oct 1 / "…E2A4C3 was last heard
    Oct 1 during the I1-2 audit. No sale, no receipt, not heard
    anywhere since." / Count and set.
  8 "shelf 3, Shopify 4" / Shopify disagrees / "The shelf is settled
    at 3. Shopify shows on-hand 4 (available 3, unavailable 1)." /
    Set on-hand to 3.
  10 "count by hand" / Count by hand / "RFID-incompatible: 6 on hand
    in Shopify, 4 tags heard." / Confirm N.
  a "1 label never paired" / Printed label, not paired / "A label for
    this product was printed Oct 2 and never paired. It answered this
    sweep." / Pair to this product · Dismiss.
  b "oversold" / Oversold in Shopify / "Available is -1: Shopify has
    sold one more than it holds. Setting on-hand will clear it." /
    (resolves through 8).
  Resolve all summary: "Un-retire 1 tag · Mark 1 sold against #50527
  · Clear 1 unavailable · Set on-hand 3".
  **BUILD STATUS (2026-10-06):** stage 1 DONE (034113f: app/verdicts.py
  rack_model + judge + copy, GET /api/audit/model/{loc}, POST
  /api/audit/verdicts/{loc}, rfid_assignments.last_heard_ctx stamped by
  every sweep, test_verdicts.py). Stage 2 DONE (gun 4.35: the ladder
  judged on the gun from one model fetch per rack, Card B, the Resolve
  window with Resolve all / Count instead, OnHandLowerIn.resolution).
  Still to do: stage 3 = the web bin page on the same ladder (the
  shared server function) and the staff-comment true-up
  (custom.staff_comments) into the model; stage 4 = retire the per-bin
  bin_check from the gun's open/sweep path (LOG + saved sweeps keep
  working today) and make LOG write the last count + hand counts;
  sale<->tag consumption records for Mark sold; the tag-by-tag locate
  flow for "counted below heard".
- **Ledger wedge fixed 2026-10-05 (080aa8d):** the hourly ShipStation
  sync had failed since 10-02 16:07 UTC (a SKU the varchar column
  stored as "ZWO FD-M54-?" re-inserted hourly, duplicate key, batch
  rolled back). SKUs now fold to the DB's spelling; each shipment
  commits alone. 72 rows landed on the first good run. Audits run
  10-02..10-05 saw no sales after the 2nd.
- **Navigation:** standalone site = tabs along the TOP (the sidebar
  "doesn't do anything"); inside Shopify admin = tabs in Shopify's app
  sidebar like EasyScan (Barcodes & SKUs / Orders / ... as nav items).
- **Customizable hubs** (team ask): move/hide/show functions, tiles vs
  buttons, per user. Needs per-user identity first → auth is step 1.

**Auth findings (2026-10-05):** today = shared STATION_KEY (browser +
gun + agent) or a Shopify App Bridge session token (app/auth.py); the
operator is a localStorage dropdown (OPERATORS env). telescopescanada.ca
mail is hosted on Microsoft 365 (MX → mail.protection.outlook.com), NOT
Google: Google sign-in cannot authenticate that domain's accounts as-is.

**Sign-in BUILT + DEPLOYED (dormant) 2026-10-05**, commit e35d8e1:
Microsoft (company tenant 22fdb4d9..., a GoDaddy-resold M365 - the
domain is FEDERATED, so the Microsoft login page may bounce via
GoDaddy) + Google, hand-rolled OIDC in app/auth.py (no new deps),
rfid_users table, /login landing page, account card replaces the
dropdown, Settings → Users tab, gun 4.34 "Who's scanning?" picker +
drawer switch. Everyone admin. Seeded: stephen, nick, matt, clay,
kevin, evie @telescopescanada.ca; Alex (no company email) gets added
from Settings → Users later. sdrapak@gmail.com deliberately NOT added.
Goes live once the app settings carry AUTH_SESSION_SECRET + MS_TENANT_ID
+ MS_CLIENT_ID/SECRET + GOOGLE_CLIENT_ID/SECRET (Entra app registration
+ Google OAuth client still to be created). Verify locally with launch
config rfid-uiverify-auth (fake IdP). Deploy lesson (c925f76): a new
table's create_all RACES between the two gunicorn workers - the loser
used to exit and take the container down (25 min of 502); init_db now
tolerates it.

## 🏠 Home landing page + sidebar navigation — ✅ ON PROD 2026-09-28 (the WIP-badge bundle)

Nick (09-24): the terminal is finicky to learn - it should open to a
menu of use cases (EasyScan-style), ordered by what average workers
actually do, not by how the system is built. Iterated over five
preview mockups, then built. NAVIGATION LAYER ONLY by his call: every
tile routes to the feature as it exists today; the features themselves
get redesigned later, one at a time (notes below).

- **Sidebar replaces the top tab bar**: same `.tabs__tab`/`data-tab`
  buttons (every programmatic `.click()` still works), now a sticky
  card below the resume/search row, in line with the tiles. Hamburger
  in the header collapses it to an ICON RAIL (never fully hidden);
  state per device in localStorage `sideRail`. Home is a new first tab
  and the default view; `#<tabname>` in the URL deep-links any tab.
- **Header**: Who's Scanning stays; new PRINTER CHIP reads
  /api/print-agent/status every 60s - green "Printer ready", amber
  held/queued counts, red with the actual fault ("Printer fault: media
  out", "Print agent offline - last seen 2h ago", wedged queue). Click
  opens Print queue. No more remote-desktopping to see printer health.
- **Global row above everything**: "Pick up where you left off" card
  (newest open batch, from /api/batches?status=open) + the scan/search
  box. Typing 2+ chars with letters = typeahead over the catalog
  (/api/products/suggest, new: (sku,title,barcode) triples from the
  bin-map snapshot, 5-min server cache, session browser cache);
  digits-only never opens the list (that's a wedge burst ending in
  Enter). Enter or a pick opens the PRODUCT CARD.
- **Product preview card** (Home): image/name/SKU/barcode/chips (bin,
  on-hand, tag count, won't-scan) + four tabs. History = the event
  feed re-dressed (EVENT_META colours, timeline dots, plain-English) -
  Nick wants this LOOK to replace the computerized History table
  eventually. Boxes & tags = live EPCs with condition chips. Shopify
  info and RFID & labels are READ-ONLY v1 with "Edit in Scan station"
  jumps (runs stationBarcodeScan, the real flow) + Shopify admin link.
- **Tiles, Nick's priority order**: 1 Receive a shipment (badge: open
  receiving batches) → 2 Process a return → 3 Find a product (focuses
  the search) → 4 Inventory checks (1-left/0-left ONLY, no tag math;
  badge: oneleft board count) → 5 RFID Inventory (all tag-vs-stock
  machinery; badge: open review tasks) → 6 Product & label tools.
  Small row: Tag a shelf, Browse inventory, History.
- Suite: dev/tests/test_home.py (13 checks). 83/84 (test_link = the
  known cp1252 console noise). Browser-verified on the seeded local
  server: typeahead, card tabs, scan-station jump, rail toggle, mobile
  wrap, zero console errors.

**Polish round 2 (Nick's list, 2026-09-24, same day):** resume/search
row shows on Home only; hamburger docks at the screen edge with the
sidebar; margins widened to 15% per side (sidebar + rail still clear
them, tiles align to the margin); printer chip replaced the old 🖨
picker button and now OPENS the printer window, which gained a live
agent-health line + a "Restart print agent" button (queues the
control-plane restart command; honest copy when no agent polls - on
DEV that is always, the warehouse agent serves prod's queue only);
chip wording: green "Printer online", agent-up-printer-dead reads
"Printer offline", agent-down reads "Print agent offline"; product
card: 340px identity column, bigger image, 13px SKU/barcode, all four
panes one fixed height, the X actually centered; sidebar buttons carry
hover titles for rail mode; Settings moved from the header into the
sidebar bottom (panel opens to its right). Dev twin got
ONELEFT_MODE=read (the "Bridge off" Nick saw was just the unset dev
setting; prod stays "confirm", dev can never confirm their checks).

**Round 3 (Nick's list, 2026-09-24):** toprow (resume + search) moved
INSIDE Home so the sidebar never jumps between tabs; embedded-in-
Shopify mode (body.embedded + App Bridge ui-nav-menu links with ?tab=
deep links, our sidebar/hamburger hidden, EasyScan-style - NEEDS a
check inside real admin); Settings became a sidebar TAB (#tab-settings,
the dropdown is gone); search suggestions carry product thumbnails
(suggest endpoint now ships image urls); page-title header with the
status pills below it; Resume deep-links into the actual batch
(resumeBatch id). Product card: labelled/indented SKU-Barcode-Bin rows
(bin as a chip), On hand / Tags as clickable stat cells (admin /
RFID tab), HISTORY STYLE B (Nick's pick): day groups, fixed-width
family chips (global width), right-justified time, PER-TYPE CHAINS
(gap <= 60 min to the previous event of the same type; interleaved
types never break a chain; anchored at the chain's earliest event;
within a day rows run chronologically by anchor) with a click-open
dropdown - chained sales list each order and click through to Shopify
admin (order-sold events now carry order_name/qty/order_admin_url).
Boxes & tags folds the -O open-box twin in under a divider. Shopify
info shows the live bucket row (available/committed/on-hand/
unavailable via new /api/products/{sku}/stock-breakdown), vendor,
planner on-order lines, recent ledger shipments; buttons pinned
bottom. RFID & labels = the FOUR-BOX editor (header / description /
barcode-encodes toggle / bin value) with per-box reset X, dirty-gated
Save, and a TRUE print preview (real Code 128 bars + the agent's own
wrap arithmetic, ported constants). LabelName grew
barcode_mode/bin_text (startup column upgrade); saves apply AT CLAIM
TIME so edits reach even already-queued labels. One-off
dev/cleanup_aug18_reviews.py ran on PROD: 8,497 resolved Aug-18 flood
tasks deleted (8,462 duplicate-product + 35 tag-onhand-mismatch), one
specimen of each kept. NOTE for a later call: the editor's
"description" box DEFAULTS to the product name per Nick's spec, but
untouched products still PRINT the SKU centre line - flipping the
store-wide default is a one-liner when Nick says so.

**Round 4 (Nick's list, 2026-09-24):** header is a full-width BLUE
BAND (--head-bg token both themes, more space below, title left /
interactables right, hamburger docked at the band's BOTTOM-left over
the sidebar's column); search box gained Up/Down recent-lookup history
(localStorage, scan-station semantics, each entry previews its product
in the dropdown when the catalog knows it); history chips are now
COLOURED (event colour at low alpha), the right column is fixed-width
and shows only the chain's EARLIEST time, SO numbers move from the
who-slot into the description (planner name stays by the date),
shopify-bin-read ("Read From Shopify") is hidden BY DEFAULT via the
new per-device default-visibility set (Settings grew a "History events
shown by default" checklist; the card filter starts from those
defaults, has a Show-all row auto-checked when nothing else is, styled
fully-clickable rows, and lives in the card's tab strip next to the
X); Tags stat now opens Boxes & tags; the bin chip is a BUTTON that
re-bins the product through /api/bin-updates (confirmed Shopify write,
logged with undo - errors surface, e.g. dev's writes-disabled 403).
RFID & labels: compact inputs, 3-line description textarea, the
barcode box is EDITABLE free text (LabelName.barcode_text column +
claim override; the SKU/Barcode button just fills the box), everything
live-updates the true preview, and a qty + "Print N labels" button
queues jobs that pick up the SAVED settings at claim.

**Round 5 (Nick's list, 2026-09-24):** the Settings checklist merged
back into EVENT COLOURS - the full hex + colour-gamut editor (search,
pager, per-row reset, reset-all) moved out of its modal into the RIGHT
half of the Settings page, each row now leading with the
shown-in-history-by-default tick; the sidebar toggle left the header
for the TOP of the sidebar itself ("Sidebar Toggle" when expanded, the
hamburger alone on the rail); the rail keeps the exact paddings of the
open sidebar so collapsing shifts no icon (rail is 62px now, margins
recalibrated); the resume card always says something - the open batch,
"Nothing to resume" with waiting check/task counts, or a green
"All clear" tick; the label editor packed to the RIGHT of its pane
with centred legend-style field labels, a fixed-width SKU/Barcode
toggle (no input jumping), and Print-N-labels stacked under Save.

**Rounds 6-7 + layout tuning (2026-09-24, detail in commits):** card
tabs reordered RFID & labels / Shopify info / Boxes & tags / History
(opens on RFID); label editor = bare boxes with inline reset X's,
click-a-label-line-to-focus-its-input, amber preview text for unsaved
edits (black on save), form left / sticker middle / options column
right, layout dialled in LIVE on dev via the TEMPORARY slider tuner
(dev/localhost only - REMOVE once Nick signs off; baked: gap 28, row
gap 14, box pad 6, print-mt 15, save-mt 0, Save stretched to form
width); Shopify info = bucket row + vendor + day-grouped colour-coded
INVENTORY CHANGES (sold/received/manual/unavailable, from ledger +
on-hand History + planner receipts - direct admin edits leave no
trail) + sales graphs (all-time / per-week / per-month + 12-week
bars); product name links to Shopify admin; Event colours expander
opens in Settings' right half, page 1 = the curated ten most-used
events, filter box one line.

**Round 8 (Nick, 2026-09-24 late, detail in the commit):** label
editor centre box = SKU by default (matches what an untouched sticker
actually prints - closed old open-decision 4) with a Name/SKU fill
toggle and a 3-line box (row gap 14 → 8 to compensate); Shopify info
v3 = weaker row tints, cause-first rows ("Order #50950" / "Stock Order
943" / "Adjusted by Nick"), unavailable rows hidden, narrower list
with its own scrollbar (graph column scrolls separately), HOVER a
change row and the four bucket tiles show before → after (red down,
green up) - estimates anchored on the new rfid_stock_snapshots table
(a row lands only when a live breakdown read DIFFERS from the SKU's
newest stored row; permanent history beyond Shopify's 6 months, and
the estimates sharpen as rows accumulate); sales graphs now Chart.js
(vendored, app/static/vendor/) with three dropdowns UNDER the chart -
graph (units sold / cumulative), bucket (day/week/month), duration
(4w/3m/YTD/1y/5y/all), fed by a per-day series in stock-breakdown;
Boxes & tags rows each grew a Locate button (merges that EPC into the
existing C72 locate queue entry); card-history batch-counted rows show
just who + date (SO/vendor junk scrubbed from the worker slot).

**BUNDLE SHIPPED TO PROD (Nick's call, 2026-09-24 late): "Push this to
main with a WIP tag next to the RFID Inventory page header."** The
full `py dev/deploy.py` run took everything since 2026-09-23 live -
ShipStation-fed sold ledger, Home landing page + sidebar, product
card rounds 1-8, snapshots + Chart.js graphs - with a WIP badge on
the header while the redesign settles. Remove the badge when he says
the new UI is settled.

**OPEN DECISIONS WAITING ON NICK (as of 2026-09-24 late evening):**
1. Product-options column layout: previews sent (A grouped switches /
   B chip toggles + detail panel / C accordion bundles) - pick or mix,
   then build v1 (flags + scan note first).
2. History event styles: style B chosen and built; Undo buttons in the
   card's history rows still missing (events lack undo handles) -
   build if wanted.
3. Shopify-info graphs: more kinds (lead-time from receipt to sale
   etc.) on request - the round-8 dropdown has room for them.
4. Embedded-in-admin nav (ui-nav-menu) needs one check inside real
   Shopify admin.
5. app/devsync.py + config DEV_SYNC_SOURCE_DB (parallel session's
   uncommitted/inert dev-mirror draft) still await his pick vs the
   live deploy-script mirror.
6. Remove the layout tuner once the label editor is signed off.

**Audit expected-count fix (Nick, 2026-09-24, the F9152B I1 audit) -
HOTFIXED TO PROD 2026-09-24** (branch hotfix/audit-expected = prod's
7f4209b + this fix alone, built and deployed from a separate worktree;
the rest of the bundle stays dev-only):**
the bin-audit row's Expected now folds SOLD-UNRETIRED into the
record-side expectation (expected + backorder + unavailable + sold,
each with its own "incl." note) - F9152B's 3 records vs 0 on-hand and
2 known sales reads "+1 unexplained" instead of "+3". The "Set to N"
raise now requires PHYSICAL evidence: it only appears when the sweep
actually HEARD more boxes than Shopify counts, and targets the heard
count - tag records alone are paper (F9152B's old button offered
"Set to 3" on an empty shelf). bin_check's sold map is windowed like
every other consumer; the fully-confirmed-shelf auto-clear keeps the
unwindowed total (its job is consuming stale pre-baseline sales).
Residual drift (+N with no sale on record) carries a tooltip naming
the likely causes: an admin-side correction, or a replaced sticker
whose old tag was never retired - those tags need retiring by hand.
Regression check rides test_home.

**Ready-for-pickup audit awareness (Nick, 2026-09-24, F9168A) - on
dev, NOT yet on prod:** "Mark as ready for pickup" never writes a
fulfillment, so a staged box sits in no ledger, answers no sweep, and
the audit read it as missing (F9168A: off by 2, orders #50894/#50895).
Pickup is NOT treated as sold anywhere else - on-hand only drops when
"picked up" finally lands, so folding it into the ledger would
double-count. It surfaces ONLY in the bin audit, and only when the
numbers close exactly: shopify.get_open_pickup_lines() (read-only,
order search delivery_method:pick-up - the fulfillmentOrders field
carrying the READY state is outside our token's scopes, and an
unprepared pickup order's box answers the sweep anyway) feeds a
store-wide 180s cache in main (_pickup_pending_map, empty on any
Shopify hiccup); bin_check rows carry pickup_pending + pickup_orders;
the UI silence ladder explains a silent count covered by sold+pickup
with an OK chip naming the orders, and the Lower offer is suppressed
when the shortfall beyond recorded sales is pickup-covered (lowering
would double-drop when the customer collects). Sales-only coverage
keeps its Lower (that flow consumes recorded sales on purpose).
Regression checks ride test_home. NOTE: the main.py half was swept
into a803dd2 (round 8) by the parallel window mid-edit; the rest is
its own commit.

**Feature redesign notes (later passes, per Nick - each feature gets
reworked to fit the streamlined menu, one at a time):**
- Receiving: per-SO view inside tile 1; stable /#receive/so-NNN URLs;
  TC-Planner stock orders link straight in; labels-not-printed tasks
  pinned to their SO. Eventually receiving may live inside TC-Planner.
- Returns: desk flow with the SCAN-FIRST empty card (fills on RFID
  read; barcode secondary with a "scan the sticker" nudge when the
  product is tagged); triage sellable/open-box/damaged/parts. C72
  stays the main use point.
- Find a product: evolve the card into the evidence page (last sweep
  heard, sales explaining absence, honest verdict, locate-on-gun).
- Review tab dissolves: receiving tasks → tile 1, count disputes +
  audit machinery → tile 5, duplicates/misc → tile 6.
- History tab restyle: adopt the card's feed look (colour dots,
  plain-English titles, inline undo).
- Product card v2: in-place edits (barcode/bin/on-hand/labels) using
  the existing guarded endpoints, replacing the jump buttons.
- Type scale going forward: 12px meta / 14px body / 16px headings,
  mono only for machine strings - stop minting one-off sizes.

## 🚚 Expected Count reworked onto ShipStation — ✅ ON PROD 2026-09-28 (the WIP-badge bundle)

Nick (09-23): the adjustment-history-windowed sales math "relies
heavily on hoping to read the adjustment history at the right time" -
rework Expected Count to use ShipStation, keep Shopify on-hand, and
plan for inconsistencies between the two sources.

- **Identity unchanged**: expected tags = live Shopify ON-HAND (still
  the only stock source) + sold-unretired + backorder debt. What
  changed is where "sold" comes from and how often it updates.
- **`app/shipstation.py`** (new): read-only V1 client (Basic auth,
  creds in app settings on BOTH sites, same account as
  shopify-automation-func). 40 req/min honored; V1's Pacific-local
  timestamps converted to UTC. MUST stay read-only - the account can
  buy real postage.
- **Primary feed = shipments**: a label created and not voided is a box
  that physically LEFT. One ledger row per (order, SKU) whatever the
  parcel count: label ids + units ride `ss_shipments` (JSON on the
  row), a second parcel triggers ONE order-line fetch whose quantity
  caps the sum (reprinted labels list the whole order again - the cap
  is what stops double-counting), voids subtract exactly their label id
  and may lower (floored at the audit-retired count). Manual
  (non-Shopify) store orders record as `source='ss-manual'`: Shopify's
  on-hand never dropped for those, so the mismatch check now correctly
  surfaces the stale count instead of never seeing the sale.
- **Shopify orders feed KEPT** as fallback + gap-filler (pickup/no-label
  fulfillments; runs even if ShipStation dies and vice versa). It never
  inserts a line ShipStation already covered and never overrides a
  ShipStation quantity - disagreements land in the run status as
  `qty_conflicts` instead of silently picking a side.
- **Backfill**: first run walks shipments back to the oldest live
  pairing (pad 7d, cap 400d). Rows older than the SKU's baseline OR the
  ledger's prior coverage arrive PRE-SETTLED (retired=quantity): they
  extend sales-history coverage without re-blaming already-audited
  gaps. The tracked-SKUs blind spot is gone for ShipStation rows (all
  SKUs record; the Shopify feed still filters to tagged SKUs).
- **Hourly sync** (:07 past the hour, worker-deduped) replaces once
  daily - the ledger now lags a shipment by <=1h instead of <=24h, so
  sales stop falling on the wrong side of freshly-moved adjustment
  baselines. Duplicate detection stays daily (8 AM Toronto pass).
- **Schema**: SoldRecord + source / ss_order_id / ss_shipments /
  ss_line_qty, added by init_db()'s new idempotent column upgrade
  (database.py `_COLUMN_UPGRADES`) on sqlite AND Azure SQL at boot - no
  separate one-off ALTER script needed for these.
- Suite: dev/tests/test_shipstation.py (24 checks). 83/83 pass.
- **NOT on prod yet**: Nick is bundling several updates into one
  deploy. Prod already holds the (inert) creds; `py dev/deploy.py`
  ships it when the bundle is ready. First prod run does the backfill
  automatically.

## 🖨 Print agent v6: direct USB + truthful dones + cloud control — ✅ DEPLOYED 2026-09-23

Nick (09-23), after a week of labels vanishing between "spooler says
printed" and the paper (9/24 on batch #301, 5/15 on the burst reprint,
2/10 on paced singles, printer healthy every time) and one remote-
desktop session too many: stop patching, interface with the agent via
the cloud, and rewrite the print path properly. Everything below the
label builders (which are field-calibrated and untouched) is new:

- **Direct USB transport (the drop fix)**: the agent opens the Zebra's
  usbprint.sys interface itself - ctypes, overlapped I/O with real
  timeouts - and holds it EXCLUSIVELY. No Windows spooler, no driver
  bidi polling, and ShipStation Connect physically cannot grab the
  device while the agent runs. Spooler survives as the automatic
  fallback (and reports "another program holds the printer" when the
  USB open loses a race). A stalled USB write now raises loudly
  instead of vanishing.
- **Truthful printed-state (was TODO #10)**: with the read channel the
  agent asks the PRINTER: ~HS status before/during every label and the
  SGD odometer (total_label_count) around it. A job completes only
  when the printer's own counter moved ("[printer-confirmed]" in the
  log); a drained-but-uncounted label is retried up to 3x then FAILED
  loudly ("printer swallowed this label"); media out / head open /
  pause / buffer full HOLD the queue - the Queue tab pill says
  "Printer FAULTED - media out - N label(s) held", and the run resumes
  by itself when the fault clears. under-temp never holds (a cold
  morning printhead heats as it prints).
- **Cloud control plane (no more remote desktop)**: every poll is a
  heartbeat (POST /api/print-agent/heartbeat: fault, transport,
  readback level, counters, ~HS snapshot) answered with queued
  commands + the server's agent version. Commands (station-key-gated
  to queue, agent-key to claim/answer): shell (PowerShell on the
  warehouse PC, output posted back), getlog, query (raw printer
  query), zpl, testlabel, feed, purge, restart, update. Results land
  at /api/print-agent/command-result/{id}. The agent SELF-UPDATES:
  heartbeat says the server serves a newer AGENT_VERSION -> download
  (script endpoint now accepts the agent key), py_compile verify,
  atomic swap, clean exit; the new LOOPING run_agent.cmd relaunches
  it. The agent also owns its log now (--log-file, 3MB rotation).
- **Bootstrap**: GET /api/print-agent/bootstrap (unauthenticated ON
  PURPOSE - remote keyboards mangle shifted chars, and it holds no
  secrets; the agent key is read from the machine's own run_agent.cmd)
  serves the one-time upgrade script: stop v5, download v6, write the
  looping runner + Startup copy, evict ShipStation autostart / driver
  bidi / USB selective suspend, relaunch.
- Suites 81/81 incl. new test_agentv6.py (heartbeat/commands/results/
  key handling on the server; ~HS parsing, fault gating and the
  confirmed/vanished/drained verdicts on the agent).
- **Rollout + the REAL drop culprit (same afternoon)**: bootstrap ran
  on the warehouse PC over one last remote-desktop session (fetched
  with an all-typable `curl.exe --location` line - no file upload
  needed); v6 came up transport usb-direct, readback counter (the
  ZD220's odometer answers). Then the cloud status showed a v5 STILL
  polling: Nick's laptop's old agent had resurrected - its
  print_agent_loop.cmd wrapper survived both the task disable
  (disabling a task never kills the running instance from that
  morning's logon) and the earlier process kills (the loop relaunches
  python 10s later). It had been stealing claims into its ghost
  printer entry all day, which is most of what looked like USB drops
  (2/10 paced singles). Killed the LOOP cmd + children this time;
  task stays disabled so it cannot return at next logon. Acceptance:
  batch #301's 8 missing labels (3 PHOBASE, 3 PHOAST, 2 OCTWHP)
  re-queued from the cloud and printed 8/8 [printer-confirmed], zero
  vanished. Ops note: the warehouse PC is now managed ENTIRELY via
  POST /api/printer-commands (kind shell/getlog/query/zpl/testlabel/
  restart/update) + GET /api/print-agent/command-result/{id} - do
  not reach for remote desktop again. Loose F9177A jobs the ghost
  swallowed earlier remain falsely done; requeue on request.

## 🔁 Dev site mirrors prod automatically — ✅ DEPLOYED 2026-09-23

Nick: "make sure the dev version duplicates the production version
automatically, make sure it can read the inventory." The dev sqlite
was empty, so the terminal showed no RFID inventory. Now:
- **Snapshot endpoints** (app/main.py): GET /api/admin/snapshot/tables
  + /export?table= on any site (station-key reads, capped exports for
  the big history tables); POST /api/admin/snapshot/import DOUBLE-
  guarded (needs ALLOW_SNAPSHOT_IMPORT=1 - only the dev app sets it -
  AND a sqlite engine) so it can never wipe prod.
- **`py dev/deploy.py` is THE deploy command now**: mkdeploy -> prod.
  SUPERSEDED 2026-09-29 (Nick): "stop pushing to dev and just push to
  production - dev wasn't really used". The default is prod only; the
  dev twin and the prod->dev mirror (dev/sync_dev.py, which also
  loaded the shared Basic DB with a full export per deploy) run only
  with --with-dev, if Nick asks for dev again. (Was: prod -> dev ->
  mirror, flags --prod-only / --dev-only / --no-sync.)
- COORDINATION NOTE: a parallel session drafted an app-side mirror
  (app/devsync.py + DEV_SYNC_SOURCE_DB, dev pulls straight from the
  prod DB on a timer) - uncommitted, unwired. Only ONE mechanism
  should land; the deploy-script mirror above is live and keeps the
  prod DB URL off the dev app (CLAUDE.md: never point dev at the prod
  database). Nick to pick; until then don't wire devsync.py.

## 🚫 Standing decision: NO Claude chat window in the terminal (Nick, 2026-09-23)

Built on the dev site, then SCRAPPED the same day at Nick's call, all
code removed before it ever committed: "it would be a bad idea to give
unfiltered claude access to any user who accesses the site." The
station key is shared, so a chat endpoint means anyone with the link
can spend API money and interrogate the model without accountability.
Do not rebuild without Nick explicitly re-opening the decision (and
then only with per-user auth + spend caps).

## 📦 Receiving batches are one-per-STOCK-ORDER — ✅ DEPLOYED 2026-09-23

Nick: a brand-new order merged into an old open receiving batch.
"I assume it bundles based on the vendor... which is wrong. Tasks
should always be based on the stock order number." This REPLACES the
2026-08-31 per-vendor merge (standing decision reversed by Nick):
- _receiving_intake now matches open receiving batches by SO number
  (any SO listed in the tag, so vendor-merge-era multi-SO batches
  still catch their own repeat pushes). merge_vendor param renamed
  merge_order; full-shipment exact-tag behavior unchanged.
- The SO leads everywhere the batch shows: resume list reads
  "📦 Receiving · SO 968 - 1 product(s), ..." and the open-batch chip
  reads "📦 Receiving · SO 968" (receivingSoOf in app.js).
- Repeat pushes of the SAME order still fold into its batch, and the
  receiving list now draws dated dividers between push days ("order
  pushed Sep 21" / "order pushed Sep 23") - derived from
  first_scanned_at, drawn only when the batch spans multiple days
  (recvAppendWithPushDividers). A same-SKU top-up folds into its
  original row, so it stays under its first push's divider.
- Prod note: batch 230 ("SO 943, SO 965 · Svbony") is the merged
  victim and stays merged - splitting would mean reassigning
  items/jobs/pairings; the dividers make it readable. Batch 302
  (SO 968) shows 66 boxes for the 33-unit S50 Pro push - the planner
  push appears doubled; Update count fixes the row before resolving
  its labels-not-printed task.
Suites 81/81 (test_recvbridge rewritten for per-SO); browser-verified
resume label, chip and dividers on the seeded server.

## 📥 Labels Not Printed can't forgive a bin-blocked debt — ✅ DEPLOYED 2026-09-23

Nick (SO 968, 33x Seestar S50 Pro): the safety-net task read "0
label(s) are waiting" while noting a 33-unit push, and resolving it
removed the task without printing anything. Root cause: the product
has NO BIN, so the label builder held every label out (skipped_no_bin)
- and queue-labels resolved the task anyway, silently forgiving the
owed labels. Fixes:
- Resolve now queues what CAN print and, when any product is
  bin-blocked, KEEPS THE TASK OPEN with a detail that names the
  product and says exactly what to do (scan it at the Scan Station,
  click its bin chip, resolve again). The web card stays on the board
  (resolved:false + fresh detail in the response).
- Task creation names the bin-less products outright instead of the
  old "(plus N held for a bin)" riddle, and mentions held-strip
  coverage (the third _build return value was silently discarded in
  both places - held-strip notes now reach the resolve message and
  resolution note too).
- /api/bin-updates already propagates a new bin onto open batches'
  item snapshots, so assign-then-resolve works with no extra step.
Suites 81/81 (test_safety.py now walks blocked resolve -> second
press no-op -> bin assigned -> clean close). Live-verified on task
#9063: press queued 0, kept it open, named the S50 Pro.

## 🖨 Agent v7: burst printing with ordered confirms — ✅ LIVE 2026-09-23

Nick, on v6's per-label handshake pauses: "Burst printing is the best
way... I'd rather work with burst printing until it has no digital
problems." v7 sends each claim as ONE continuous run (no pauses
between labels) and keeps every v6 guarantee by exploiting order: a
Zebra prints formats strictly in arrival order, so odometer position
base+i is label i's receipt. Each job completes the moment the
counter passes it; only the EATEN TAIL of a run retries (3x then a
loud fail); faults mid-run still hold with the rest buffered; and the
stall watchdog now demands PROGRESS (formats moving or count
climbing) instead of extending forever on a wedged buffer. Deployed
to prod + dev; the warehouse agent SELF-UPDATED 6->7 within seconds
of the deploy (first real use of the update channel - no remote
desktop, no hands). Remaining known label risk is physical only
(uneven tearing), handled by the existing re-align tools.
Test-suite scar (worth remembering): the agent-side suite once let
_self_update run for real against a hardcoded old version string and
it clobbered the repo's print_agent.py with the fake download; the
suite now derives the served version from AGENT_VERSION and stubs
os.replace during that test. Suites 81/81.

## 🧪 Private dev site — ✅ LIVE 2026-09-23

Nick: other workers now use the terminal on their own computers, so
new features get tested on a PRIVATE copy first.
https://telcan-rfid-dev.azurewebsites.net - a second web app on the
SAME B1 plan ($0/month extra), zero code changes:
- Own sqlite at /home/dev.db (persists across deploys; wipe = delete
  the file). Never touches the prod Azure SQL or its 5 DTUs.
- Own STATION_KEY - the workers' saved prod keys don't open it. The
  station link lives in Desktop\dev-terminal-link.txt on Nick's
  laptop (kept out of the repo and the chat).
- Real Shopify READ creds so lookups/bin map are realistic, but
  SHOPIFY_WRITE_MODE=disabled (server-enforced: no store writes,
  period). 1-left bridge off (default), planner bridge off (no
  token), orders sync off, no PRINT_AGENT_KEY (the real printer
  never claims from it).
- Deploy: `py dev/mkdeploy.py` then the usual az command with
  `-n telcan-rfid-dev`. 1 gunicorn worker (RAM-light; it shares the
  B1 instance with prod - `az webapp stop -n telcan-rfid-dev -g
  shopify-automation-rg` parks it when unused).
- Bare URL serves the empty page shell like prod; all data behind
  the key.

## 🚨 Weekend outage: pool exhaustion + the watchdog — ✅ DEPLOYED 2026-09-23

Nick (09-21/23): "web terminal can't access the database / doesn't
load inventory." Postmortem + fix:
- **What happened**: from Fri 2026-09-19 14:39 UTC every DB-backed
  endpoint failed with SQLAlchemy "QueuePool limit of size 10
  overflow 20 reached" - all 30 connections checked out and never
  returned - while the DATABASE sat idle (0 DTU) and Online. Static
  pages still served, so the site "looked up". It stayed dead all
  weekend because nothing inside a worker can reclaim a connection a
  thread still holds. An app RESTART (09-23) recovered it instantly.
- **What it wasn't**: no login/firewall errors, no Shopify hangs
  (every outbound call has a timeout), no unclosed sessions (all
  context-managed, get_session has finally-close). Prime suspect:
  a convoy - the 3h bin-map rebuild's whole-table delete+reinsert
  transaction on the Basic (5 DTU) tier blocking every bin-map
  reader, each blocked reader holding a pool slot - but the logs
  that would prove it rotated away.
- **The fix is self-healing + evidence**: pool_timeout 10 (fail
  fast, was 30) + pool_use_lifo; and a POOL WATCHDOG daemon
  (database.py start_pool_watchdog, started in lifespan): if all 30
  connections stay checked out for 4 minutes straight, it dumps
  EVERY thread's stack to the docker log (the who-held-what evidence
  this outage never left) and hard-exits the worker; gunicorn
  respawns it in seconds with a fresh pool. Worst case is now a
  ~5-minute blip that leaves a full diagnosis in the log, not a dead
  weekend. Inert on sqlite (tests/dev).
- If the watchdog ever fires, pull the docker log for "POOL
  WATCHDOG" + the stack dump and fix the true culprit.

## 🏷 Sealed cases count in every label tally — ✅ DEPLOYED 2026-09-16 (C72 4.17)

Nick's 2-labels-instead-of-11 report: the SERVER always queued case
labels correctly, but the CLIENTS' own label math only counted loose
scans (qty_scanned), so sealed cases vanished from the tallies:
- C72 queueLabels dialog said "Queue 2 label(s)" for an 11-label
  batch, and the check->print gate ("Nothing to print") dead-ended a
  batch that was ALL sealed cases. Both now use labels_total (loose
  + cases); the dialog names the case share ("9 of them are sealed
  cases counting 36 units between them").
- Web: labelItems() filtered on qty_scanned > 0, so a row whose
  scans all converted to cases vanished from every label count -
  same dead end, same undercount. Filter + queue confirm + collect
  "boxes total" all count cases now.
Browser-verified Nick's exact shape (2 loose + 9 cases of 4): the
confirm reads "Queue 11 label(s)" and 11 jobs queue. Suites 80/80
(server behavior was already covered and unchanged).

## 🔧 Field-test round 2 (six asks, one afternoon) — ✅ DEPLOYED 2026-09-16 (C72 4.16)

- **Staged collect counts**: the item editor's +/- (and the exact-
  count dialog) edit LOCALLY and flush ONE server write when the
  window closes ("Count N - saves when this window closes").
- **Case scans ask once per batch**: after a SEALED answer for a
  product in a batch, re-scans of its case barcode just add one more
  sealed box (server-side, `case_auto` in the scan answer; both
  clients fall through). An "opened" first answer keeps asking - the
  guidance is to open boxes BEFORE batch tagging. Also fixed a
  latent crash: sealed FIRST scan creating a brand-new row hit
  case_count=None.
- **Returned tags vs the sold ledger** (Nick's review-task report):
  a returns-processed restore no longer blindly hands the consumed
  sale back. Restock SUCCESS keeps the SoldRecord consumed - the
  return IS that sale's physical resolution (message: "its sold
  order stays settled by this return"), so expected-tag math stops
  hunting the unit that came back. No/failed restock hands back as
  before, keeping every combination balanced. History's
  return-processed row is the when/who log.
- **Unresolved case rows print** via the what's-inside flow (the
  earlier build's case-on-unresolved rows couldn't label - no
  product). Legacy stuck rows get a gun dialog: UNDO, then declare
  again naming the product.
- **Drawer scrolls**: 8 tabs outgrew the screen and crushed every
  row; the tab list scrolls, Settings + version pinned full-size.
- **Whole-strip toggle on the gun**: Settings > Batch tagging gains
  the server-stored "Whole strip at once" switch (same switch as the
  web Queue tab, applied live, no Save).
Suites 80/80 (returnstab 25 checks incl. ledger arithmetic;
casedeclare 21 incl. ask-once + auto-add).

## ↩️ Returns polish: done = clear + restock; case dialog rework — ✅ DEPLOYED 2026-09-16 (C72 4.14)

Three field asks, same day:
- **Done means done**: finishing any Returns-tab workflow (process
  actions AND open-box) now CLEARS the card and leaves the outcome
  message standing - the old auto re-lookup refilled the card and
  wiped the message a split second later.
- **Restock on confirmed re-entry**: as-new and used raise the
  product's Shopify on-hand by the tag's units; open-box raises the
  -O TWIN's by 1. Same rails as the verify raise: gated by
  SHOPIFY_WRITE_MODE `returns_restock` (ENABLED on prod), History
  "on-hand" event with undo, bin-map snapshot refreshed. The gun's
  confirm dialog states the raise (that dialog IS the operator
  confirmation, `restock:true` on the call); FAIL-SOFT - a Shopify
  hiccup (e.g. a fresh unpublished -O draft can't take stock) becomes
  a note in the answer, never a rollback of the RFID side. The gun
  dialog warns NOT to also use the returns app's add-back-to-stock
  (double count). Web flows unchanged (they don't send restock).
- **Box of multiple products, unresolved rework**: on an unknown
  barcode the dialog now asks WHAT'S INSIDE - type a barcode/SKU or
  PICK FROM THIS BIN (the batch's pre-seeded rows are the bin list).
  Declaring resolves the row to that product, registers the unknown
  outer code as a durable CASE barcode (POST /api/cases semantics:
  future scans auto-ask opened/sealed everywhere), and converts the
  scans to sealed cases (boxes prefilled with the scan count).
Suites 80/80 (test_returnstab +5 restock checks, test_casedeclare
reworked for the contains flow + durable re-scan check).

## ↩️ C72 RETURNS tab — ✅ BUILT & DEPLOYED 2026-09-16 (C72 4.13)

The approved preview (artifact "C72 Returns Bridge", Version 3),
built. Scan the returned box on the new RETURNS tab; the card fills
with the tag's whole story; one tap settles the RFID side. The money
side (refunds, fees, their drafts) STAYS in the returns app.
- **Bridge (config-only, their code untouched)**: an `RFIDSvc` token
  now sits in tc-dashboard-proxy's TC_USER_TOKENS; our server reads
  `open-returns` with it (RETURNS_API_URL/RETURNS_API_TOKEN app
  settings, 90s cache, fail-soft). The gun card shows "Active return
  matched: order · customer · reason" when the SKU has one. We never
  write to their API.
- **Server**: GET /api/returns/tag/{epc} (live / retired / printed-
  only / companion / unknown + product, condition, bin-map enrich,
  open watch, matches); POST /api/returns/process actions: as-new
  (retired tag restored live + ledger handback, condition cleared),
  used (live + condition used), unsellable / display (tombstoned -
  NEW RetiredTag kinds, named by sweeps instead of ghost-prompted).
  History "return-processed" chip + the underlying tag events.
- **C72 4.13 (code 131)**: RETURNS tab (Settings toggle, default
  on): trigger = strongest single read -> FULL-preview card (image,
  title, SKU/Barcode/Bin, condition) + tinted tag-state line + match
  panel + AS NEW / OPEN BOX / USED / UNSELLABLE / DISPLAY ONLY (and
  NOT OURS on unknown tags); ✕ clears. OPEN BOX with the tag in hand
  runs the peel flow (no watch, -O label prints); barcode-only scans
  fall back to the classic watch flow. Confirm dialog per action.
- Same round: "Box of multiple products" now works on UNRESOLVED
  barcode rows too (Nick: that's where it's needed most) - gun
  button no longer hides, server accepts, split rides along when the
  row resolves.
Suites 80/80 (new test_returnstab.py; test_casedeclare grew the
unresolved check). NOT built yet, by design: writing process-return
into the returns app from the gun (fees/refunds need their form),
and the ROW/MINI preview refactor of older screens.

## 🔍 Check step opens the real Edit-product view — ✅ DEPLOYED 2026-09-16

Nick: "clicking an item on check step produces the actual edit
product view rather than what it currently shows."
- Clicking a resolved row now opens the product window
  (openProductHistory) - the same Edit-product view every other tab
  uses (label lines + preview, Edit product dock, bin chip, flags,
  live tags, full history) - instead of the check-editor clone.
- Batch-ONLY decisions stay reachable: flagged rows carry a
  "🛠 Batch fixes…" button that opens the old check editor (pick
  between listings sharing a barcode, wrong-bin drop/move/ignore,
  the bundle call, split, per-row reprint). Unresolved rows still
  open the check editor directly - the barcode rescue lives there
  and there is no product to open.
Browser-verified all three routes on run_local.

## 🖨️ Whole-strip printing + hand-declared cases — ✅ DEPLOYED 2026-09-16 (C72 4.12)

Two asks in one round (Nick, 2026-09-16):

**Whole strip at once** — "toggle whether it prints the entire strip
at once instead of one print job per bin (side bin and main bin)".
- Server-stored toggle (AppSetting `print_strip_at_once`, default
  OFF = old behavior). Flip it on the Queue tab ("Whole strip at
  once", by the printer buttons); POST /api/print-strip-mode, state
  rides /api/print-agent/status; History "Strip Mode" chip.
- ON: a side trip diverted while the parent still collects HOLDS its
  labels (trip stays "collecting", no jobs); the parent's PRINT
  queues parent labels first, then each held trip's labels grouped
  (_queue_held_side_trips - runs unconditionally, so held labels
  print even if the toggle flipped off since). One strip: main bin,
  then each side bin. Trips diverted AFTER the parent printed keep
  the immediate print (nothing to join).
- Guards: close-divert refuses a held trip (labels unprinted) with
  the way out (PRINT the parent, or PRINT the trip alone for its own
  strip). Carry-only trips (tagged_before) are never held.
- C72 4.12 (code 130): a held divert does NOT enter the trip - stays
  in the parent with the message; PRINT's status names the strip's
  tail ("N more for G6-6 (3)..."); held trips are resumed from the
  batch list after printing. Web divert likewise stays in the parent.

**Box of multiple products** — boxes full of several units of ONE
product, labelled per box instead of unpacked.
- POST /api/batches/{id}/items/{id}/case declares sealed cases BY
  HAND (no registered case barcode): units (2-500) + boxes; one label
  + one tag per box, label reads "N x SKU", tag counts N units -
  exactly a registered case left sealed. Already-scanned boxes
  counted 1 loose each, so up to `boxes` loose scans convert (no
  double count). `undo` opens them all back to loose scans. Refused:
  bundles, mixed case sizes on one row (undo first), and once the
  item's labels are queued (strip would desync). History
  "case-declared" -> "Sealed Cases" chip.
- C72 item editor gains "BOX OF MULTIPLE PRODUCTS…" (collect/check
  steps): asks units per box + box count; once declared it reads
  "✓ N SEALED CASE(S) OF U - CHANGE…" offering add-one / undo.
  Pairing, audits and counts already understood cases - this only
  adds the manual declaration path.
Suites 79/79 (new test_stripmode.py, test_casedeclare.py). No schema
change. Gun self-updates to 4.12.

## ↩️ Open-box return with the box IN HAND — ✅ DEPLOYED 2026-09-16

Nick: "unpair the old tag, check for an open box listing and create
one if there isn't one, then print that new tag with the -O barcode
suffix." The watch-and-prompt loop already covered a tag SOMEWHERE;
this covers the tag on the box you're holding.
- /api/openbox-returns gained `peel_old`: with the scanned EPC, a
  LIVE tag of the product retires as replaced on the spot (condition
  carried, History tag-retired), a presumed-sold tombstone flips to
  replaced exactly like the watch's peeled answer, an unknown EPC is
  reported ("nothing to unpair; peel it anyway"), and another
  product's live tag is refused. When peeling, NO watch/Review task
  opens - the unit's tag is accounted for (server-enforced even if
  watch:true rides along).
- Web "Set as Open Box" overlay gained an "Old tag on the returned
  box - scan it to unpair now" input; filling it unchecks + disables
  the watch checkbox, and the wedge's Enter stays in the field. The
  rest is the existing flow: twin found or drafted, card flips,
  "Switch card & print" queues the -O label (base SKU line, OPEN BOX
  bin note; the -O BARCODE is written by the print-time migration
  when the printer claims the job, as before).
Suites 77/77 (new test_obxpeel.py). The future C72 Returns tab
reuses peel_old as its open-box action.

## 📦 Per-box conditions — ✅ DEPLOYED 2026-09-16 (C72 4.11)

The foundation for returns + future condition workflows (Nick: "each
product [box] its own condition... different from stock levels").
- **Vocabulary** (BOX_CONDITIONS in main.py, single source): good
  (default, stored as NULL), open-box, used, damaged, needs-parts,
  display, safety-stock. Deliberately INERT semantics for now - no
  count, audit or sweep math reads conditions yet.
- **Model**: `condition` on rfid_assignments + rfid_retired_tags +
  rfid_released_tags (dev/alter_add_condition.py RUN ON PROD;
  "condition" is reserved on SQL Server - bracketed). The value rides
  the whole tag lifecycle: all 7 retire paths carry it, both unretire
  restores return it, release/re-apply snapshots it.
- **Setter**: POST /api/tags/{epc}/condition (live tags only; "good"
  clears; 422 unknown slug). History "tag-condition" -> "Box
  Condition" chip ("…{epc6}: Good → Damaged").
- **Auto-seeded** where the answer is known: pairing to a -O twin
  (all five pair paths) and the open-box adopt set "open-box".
- **Surfaces**: web Scan Station recent-tags rows get a quiet
  dropdown (tints when a real condition is set); tag-info carries
  condition + label + a note; C72 4.11 (code 129) sticker sheet shows
  CONDITION: X ▸ with a single-choice picker.
- The planned RETURNS tab (preview approved 2026-09-16) will be
  another writer; the standardized product previews will render the
  per-box condition badge from this field.
Suites 76/76 (new test_condition.py).

## ⇩ On-hand lowering past sales — ✅ DEPLOYED 2026-09-15

Nick: "let the user decrease shopify product on audits or future
batch tags as long as it's not the very first batch tag completed."
- The /api/onhand-updates/lower gate is now two-tier: sales-backed
  drops work exactly as before; drops BEYOND recorded sales are
  allowed only for products with a COMPLETED batch tagging on file
  (any done batch but the current one - _prior_tagged_skus). On a
  first tagging the old refusal stands, reworded to say why (an
  undercount usually means untagged boxes, not missing stock).
- Unbacked units are SHRINKAGE: silent tags still retire
  presumed-sold, the ledger consumes only what sales cover (both
  consume paths were already fail-soft), and the 409 confirm, the
  success message and both UIs name the shrinkage count. The
  existing one-click undo reverses everything (tags restore, only
  actually-consumed ledger units hand back).
- Audits: bin/product audit rows gained "Lower to N" (N = sweep-heard
  units + the unavailable set-asides, so those never get written
  off). Offered only against a real sweep, for tagged
  non-RFID-incompatible products, NOT on rack-zone reports (the
  endpoint's tag-bin check needs the exact bin), with an extra
  confirm warning when NOTHING of the product answered. Verify's ⇩
  button now also appears past sales for prior-tagged products, its
  confirm naming the unbacked count.
Suites 75/75 (new test_lowerguard.py; test_ledger_flow's first-
tagging refusal still holds by design).

## 🔍 Product audits — ✅ DEPLOYED 2026-09-15

Nick: audit ONE product, not just a bin. The Audits hub card is now
"Run an audit"; the pane gained a second input under the bin row
(barcode or SKU + "Audit this product"). A product audit looks the
product up, checks its HOME bin with the same sweep machinery
(bin_check with the sku force-included via the existing skus extra -
no server change), and renders ONLY that product: expected math,
tagged-here, seen, silent boxes, its ghosts / open-box prompts /
never-paired labels. Bin-level noise (other products, strays,
batch-tagged state, record-as-tagged) is suppressed; a footer points
at the full bin audit. The product row DISAPPEARS the moment a bin
audit runs (bin typed + sweep pulled, or the ◀ ▶ arrows) and comes
back when the pane is reopened from the hub. Per-product fix flows
(Set to N, clean ghosts, obx answers) keep the product force-included
across their quiet re-checks.

## 📝 Draft product from the gun — ✅ DEPLOYED 2026-09-15 (C72 4.10)

Nick: unresolved box that NO listing owns -> draft it at the shelf.
- POST /api/products/create-draft (gated require_shopify_write
  "draft_listings" - the flag was already in prod's mode): DRAFT
  Shopify listing with the typed SKU, the scanned code as barcode,
  bin metafields; 409 when any listing (active/draft/archived)
  already carries the SKU. History "draft-created" -> "Draft Created"
  chip. Pricing/publishing stay human jobs in Shopify.
- C72 item editor (unresolved rows only): CREATE DRAFT PRODUCT…
  asks for the SKU, then FULL PRODUCT vs INGREDIENT - an ingredient
  (one box of a multi-box BUNDLE) only differs by the draft title
  wearing "INGREDIENT", per the bundles-not-boxsets model. On
  success the row re-resolves IN PLACE via the same /resolve call
  the alias-link rescue uses; counts kept.
Suites 74/74 (new test_draftcreate.py).

## 📡 Locate + unpaired labels round — ✅ DEPLOYED 2026-09-15 (C72 4.09)

Nick's five asks, all shipped:
- **NOT OURS** (foreign tag found in the store): POST /api/epcs/not-ours
  - permanent dismissal via LabelDismissal (the _still_unlinked
  chokepoint honours it everywhere: hunt stash, classifier, sweeps,
  bin checks), but OUR printed labels are REFUSED (opposite stance to
  ignore-heard, which only reports them). History event "epc-not-ours"
  -> "Not Our Tag" chip, marker undo shared with the sweep write-off.
  Gun: NOT OURS button on the unpaired-hunt pair sheet (confirm ->
  post -> tag leaves the hunt on the spot).
- **Pinned bin cache** (gun): up_pin_cache_json pref (house _json
  pattern, bin = validity token). The pin button paints its count and
  the bin list OPENS instantly from cache; every fetch (tab entry,
  every pair, every dismissal) rewrites it. Staleness window is one
  pair; a stale EPC pairing just 409s harmlessly.
- **Tap a bin-list product -> Scan station** (gun): rows in LABEL BINS
  bin lists and UNRESOLVED PRINTED LABELS now open the product in the
  gun's own Station tab (selectTab + stationLookup) for hand-pairing -
  the workaround when the hunt won't ping a label you can touch.
- **Sold-without-label dismissal from the unpaired list**: the list
  endpoint now carries item_id per row; web overlay gained a
  "Sold, no label" button and the gun's lists dismiss on LONG-PRESS -
  both drive the existing dismiss-sold flow (our accounting only,
  History-undoable).
- **C72 read/processing speed** (4.09): locTags/locFound/upKnown are
  concurrent (SDK callback vs UI thread races could corrupt or crash);
  the per-READ locTargets() set copy on the SDK thread replaced with an
  O(1) test; audit merge skips the full uppercase pass + view rebuild
  when no new tag arrived (was every 400 ms while holding the lock the
  SDK callback blocks on); locate start/stop radio commands moved off
  the UI thread (the per-trigger-pull hitch) onto one serial executor;
  stopLocate's power restore default aligned to 5 (was 20).
Suites 73/73 (new test_notours.py).

## ⚡ Performance + consolidation pass — ✅ DEPLOYED 2026-09-15

Nick: "see what you can combine... improve the speed at which things
are loaded... increase the caches, we're barely using 20% of our
database storage." No behavior changes intended (one drift bug fixed).
- **Transport**: GZipMiddleware (cold page load ~915 KB -> ~230 KB);
  ASSET_VERSION is now a CONTENT HASH of app.js+styles.css (the old
  per-process time.time() differed between the two gunicorn workers,
  so alternating loads busted each other's browser cache and re-pulled
  the 700 KB app.js forever); versioned /static URLs get
  Cache-Control: immutable (the APK keeps defaults; the page keeps
  no-cache so deploys land).
- **DB**: index on rfid_bin_map.barcode (the FIRST query of every
  scan); engine pool_recycle=1500 + pool 10/20; column widenings (we
  use ~20% of the tier): History old/new_barcode 64 -> 255 (they double
  as event detail slots and summaries were getting chopped), review
  detail/notes + print error 500 -> 1000, openbox not_epcs -> 2000,
  app settings value -> 2000, other_bins -> 500, image_url -> 1000.
  One-off migration dev/alter_perf_and_widen.py RUN ON PROD
  (idempotent, metadata-only).
- **Query fixes**: locate-queue N+1 (2 queries per entry -> 2 grouped
  queries); /api/history undo checks (up to 5 point lookups PER ROW ->
  5 batched IN queries per page); review-tasks image lookup filters in
  SQL; audit/unavailable no longer loads the whole tag table;
  epc-captures/latest-summary (4 s poll) stops shipping thousands of
  EPCs as one giant IN; inventory summary reads the bin map ONCE
  (was three full walks per request).
- **Caches enlarged**: inventory live-qty cache re-keyed PER SKU
  (was one all-or-nothing entry keyed on the whole SKU tuple), TTL
  120 -> 180 s; scan-card expected-qty Shopify call cached 45 s per SKU
  (was a live HTTP round trip on EVERY scan; display-only - on-hand
  writes still read live); bin-map snapshot TTL 6 h -> 3 h; planner
  caches get pruning caps.
- **Consolidation** (~350 net lines out of main.py, behavior
  identical): _log_change() replaces all 68 hand-built BarcodeChange
  History rows (truncation now in ONE place, at the new 255 cap);
  _up() for the 153 (x or "").strip().upper() sites; _naive_utc();
  _get_batch_item()/_get_review_task() fetch-or-404s (24 sites);
  _require_shopify_env() (7); ONE module-level _CHANGE_TYPE_LABELS
  map - the two history endpoints carried drifting private copies, and
  product history rendered backorder-debt events as raw field names
  (FIXED). Dead code removed (_live_barcode_map, refresh-running
  markers, app.js queueStatusSummary). app.js: setPreviewHeader() +
  renderFitWarn() replace 5+3 copied preview blocks; tab switches
  skip refetching read-only tabs already under 15 s fresh (their
  refresh buttons still force).
- Suites 72/72; browser-verified on run_local (all tabs, zero JS
  errors); prod smoke-tested (gzip + immutable headers live, authed
  history/locate/review endpoints answering).

## 🧹 Blank-roll sweep cleanup — ✅ RUN ON PROD 2026-09-15

Nick accidentally swept the whole store including the unprinted blank
RFID roll; the factory EPCs stuffed the orphaned-tags hunt list, and
separately many receiving labels had been replaced by hand-paired
tags (the ALP-T-2-Ha/OIII-HS shape: strip printed, boxes tagged with
other stickers minutes later, before the Sept-15 credit system).
dev/cleanup_unpaired.py (RUN --apply ONCE - a rerun would
over-credit):
- 238 blank/foreign stickers written off (ignore-heard mechanism;
  printed-label EPCs were protected, though none were in the stash).
  The orphaned-tags hunt list is empty.
- 37 labels across receiving batches 219/230/233/240/241/242 credited
  as covered by later hand pairs - off the unpaired-labels list, and
  their receiving tasks settled.
- Still genuinely owed (~61 labels, Nick walking them): most of batch
  230's F/W SKUs, TL-ST3B-00, ALP-T-3NM/3.5NM-SET, one EPWP5210-01,
  one F9127A.
The Locate unpaired-labels list already reads ONLY receiving labels
(by design since 2026-09-09) - no code change needed.

## 🗑 Multi-box sets SCRAPPED — ✅ DEPLOYED 2026-09-15 (C72 4.08)

Nick's decision, his words: "as long as we know where the individual
boxes are it doesn't matter what box it is." Every box is its own
individual product now; Box X of Y is dead everywhere.
- **Removed** (server + web + gun): the box-set registry and all its
  endpoints (create/list/renumber/relabel), the "Part of a set" marks
  and both mark dialogs, the set builder and verify panel, collect
  grouping (set headers, remote-part rows), Edit Product's Box X of Y
  and multibox rows, min-count set math in audits/checks/inventory,
  the part-barcode lookup override and part fallback, the family
  guardrail lift (duplicate code/SKU overwrites ask again, always),
  MultiboxProduct + companion label CREATION. Companion-tag
  RECOGNITION plumbing stays (inert - prod has zero companion rows
  and nothing can create one now).
- **Labels**: the only bin-line note is OPEN BOX (always shown on
  open-box products); legacy "Box N of M" notes are STRIPPED wherever
  a job re-derives. _strip_box_note keeps cleaning legacy text off
  records.
- **Prod migration** (dev/unlink_box_sets.py, RUN --apply): S11230 /
  S11810 / S11830 masters converted to BUNDLES of their box products
  (ProductKind bundle + BundleContent qty 1 - the go-forward model
  for any boxes-sold-as-one-unit product); S11230-1's barcode written
  to its draft listing; S11830-1/-2/-3 (registry-only identities) got
  real draft listings created with their barcodes and the master's
  bin, and their live tags repointed to the new variants; 7 registry
  rows + the S11740 multibox row deleted; 13 marks cleared; 2 tag
  titles cleaned. Draft listings and the products pointing at them
  KEPT, per Nick.
- Old History events (box-set, multibox, box-renumbered) keep their
  chips so the paper trail still reads.
Suites 72/72 (test_boxsets/test_boxset_collect/test_setmarks/
test_multibox deleted; test_labeledit rewritten note-strip-first).

## 📦 Open-box returns + marks rule the stickers — ✅ DEPLOYED 2026-09-15 (C72 4.07)

Three threads, one round:
- **Open-box returns, closed loop** (built from the approved preview):
  Scan Station's "⧉ Set as Open Box…" flips the card to the -O twin
  (found via SKU probe, or a DRAFT created on the spot - "<title> -
  Open Box"), optionally prints its label right away (the existing
  print-time migration writes the -O barcode + alias; prod gate
  openbox_barcode confirmed ON), and opens a return watch: an
  OpenboxReturn row + a Review task (category openbox-return). Sweeps
  and audits hearing the original's presumed-sold tags upgrade the
  generic ghost warning to "is this box the open-box unit?" - on the
  web bin audit (prompt block with Yes/No) AND the gun (tappable rows
  in verify/shelf-sweep, per-ghost action in audit CHECK). YES adopts
  the old tag as the -O product's live tag (unretire + reassign, the
  ledger units hand back) - or, when a fresh -O label already paired
  since filing, says PEEL and flips the old record to replaced. NO
  stops asking about that EPC. Manual outs: "old sticker peeled" in
  the Review window, dismiss. History changed_field "openbox".
  New table rfid_openbox_returns (dev/alter_add_openbox_returns.py
  RUN ON PROD). New suite test_openboxreturn (26 checks).
  Label pass (Nick's field report, same day): the SKU LINE prints the
  BASE SKU - the -O suffix belongs to the barcode only - and the bin
  line carries "OPEN BOX" (like multibox's Box X of Y note, and it
  OUTRANKS box-set/multibox notes). Records strip the note; the card
  preview matches.
- **S30810: marks reach the stickers.** The Box X of Y note came only
  from the REGISTRY (defined at verify), so labels printed at the
  Print step had no note. Now `_apply_part_box_notes` also reads open
  batches' set MARKS (registry outranks a mark for the same SKU), and
  saving/clearing a mark restamps already-queued PENDING labels
  (family Y sync restamps siblings too).
- **S11810: defaults never override intent.** The registry rows
  (suffix-derived) overrode Nick's marks (-1 is physically box 2).
  Saving a mark on a REGISTERED part now RENUMBERS the set to match
  (swap via the shared `_renumber_boxset_part` core; tags + pending
  labels follow), and both mark dialogs default from the registry's
  real numbers (boxset_of/box_no/boxes) before falling back to the
  SKU suffix. Prod repaired: S11810-1 = box 2, S11810-2 = box 1.
test_labeledit +12. Suites 76/76.

## 🏷 Reprints stop cloning stale labels; Queue edit button — ✅ DEPLOYED 2026-09-15

Nick's report: reprinting during batch tagging still printed S11830-3
as "Box 1 of 3" - reprints CLONE the old job's bin-line text, and the
renumber only fixed pending jobs.
- **Auto-refresh on every queue path**: `_apply_part_box_notes`
  re-derives a registered box-set part's "Box N of M" note from the
  CURRENT registry, replacing any stale note the job carried. Runs in
  `_expand_multibox` (Scan Station prints, Queue-tab reprints, batch
  label runs, receiving) AND in `_void_and_requeue` (the Print-step
  reprint-selected / reprint-all path, which never re-derived at all).
- **Queue tab "edit" button** on pending jobs: dialog with the three
  printed lines (top / SKU / bin) + live sticker preview + fit
  warnings. Save applies to THIS job only (POST
  /api/print-jobs/{id}/edit); "Refresh from product" re-derives the
  lines server-side - saved preferred name + current box note (POST
  /api/print-jobs/{id}/refresh). Both 409 once the label printed.
  History event "Label Edited" (changed_field label-edit).
- Overlay lives OUTSIDE the tab sections (an overlay inside a hidden
  tab never shows - same lesson as the printer picker).
New suite test_labeledit (14 checks). Suites 75/75.

## 🔢 Box X of Y made robust — ✅ DEPLOYED 2026-09-15 (C72 4.06)

Nick's report: S11830-3 saved as box 3 of 3 still PRINTED "Box 1 of
3" (the registry numbered by scan order), and the marks' defaults
ignored the SKU's own story.
- **Prod repaired**: S11830 renumbered so box numbers match the SKU
  suffixes (-1=1, -2=2, -3=3). The already-printed S11830-3 label
  still says "Box 1 of 3" on paper - reprint from the product window
  if it matters.
- **The registry honors explicit numbers**: BoxSetPartIn takes
  box_no; parts carrying one sort by it (rest follow in list order;
  storage stays a clean 1..N), and the web builder passes each
  marked row's Box X. Marks now SURVIVE into labels.
- **Master = parent, smart defaults** (both clients, C72 4.06 code
  124): an X-Y SKU (S11830-3) defaults master X and box Y; the box
  count defaults to the largest number the family knows - other
  marks' totals/numbers, a registered set's size, the own suffix
  (Nick's exact example: S11830-2 first = box 2 of 2; set Y=3; then
  S11830-1 = box 1 of 3). Saving Y syncs every same-master mark on
  open batches (server-side); the web dialog re-derives Y when the
  master field changes until the steppers are touched.
- **Renumber anywhere**: POST /api/box-sets/{set}/renumber swaps a
  box into a new slot (History "Box Renumbered"); open-batch rows,
  live tag titles and PENDING labels follow (printed labels keep
  their text). The row lives beside Save SKU / Save Barcode in BOTH
  the Edit Product window and the batch-tagging check window; a
  merely MARKED box offers its mark editor there instead.
- **Check window restyled** to the Edit-product shape: title +
  SKU/Barcode/Bin grid header; every batch-specific block (found-in-
  bin-but-system-says, split, rescue) unchanged.
test_boxsets +7, test_setmarks +2. Suites 74/74.

## 🔀 S11810 crossed part barcodes — repaired + guarded — ✅ DEPLOYED 2026-09-15

Nick scanned 050234810111 (physically box 2) and got box 1. No alias
anywhere - the box-set REGISTRY (created Sept 9, before the builder
showed barcodes) held the two codes SWAPPED relative to the parts'
own draft listings, and since the part-registry override moved into
_product_lookup (Sept 14) the registry outranked the correct catalog
answer on every surface. Prod repaired via a History-logged redefine
(S11810-1 = 050234181013 box 1, S11810-2 = 050234810111 box 2; his
open A7-1 batch re-resolved) - both codes verified resolving to their
own boxes. A sweep of every registered part found NO other
disagreement (S11230 and S11830 clean). New guard in create_box_set:
two boxes whose OWN listings carry different barcodes, submitted with
each other's codes (a perfect two-way swap), are refused with the
crossing named - a shared code across boxes and one-sided reuse stay
allowed (real set shapes). test_boxsets +4. Suites 74/74.

## 🏷 "Sold before labeling" resolution on Labels Not Printed — ✅ DEPLOYED 2026-09-15

Nick: products sometimes sell or get set aside before anyone can
label them. The Update-stock safety-net task (planner pushed stock
without printing) now offers a SECOND resolution beside "Queue the
missing labels": **"The unlabelled units were sold or set aside"**
(POST /api/review-tasks/{id}/unprinted-sold). Nothing prints; per
owed SKU the batch row's count drops to what was actually labelled
(so the batch settles honestly and stops owing), up to that many of
the SKU's unretired recorded sales are consumed via
orders_sync.retire_units (the expected-tag arithmetic stops waiting
for tags never applied), and History gets an "Unlabelled Sold"
receipt per SKU. Set-asides need no ledger touch (the Unavailable
bucket already folds into expectations); when no sales are recorded
yet the message says so honestly. Unresolved/skipped/bundle rows are
never written off; a resolved task refuses a second pass. No undo -
the receipts and resolution note carry the whole story.
test_safety.py +8. Suites 74/74.

## 🎯 Unpaired labels round 2 + bundles + partial-order sales — ✅ DEPLOYED 2026-09-15 (C72 4.05)

Nick's field notes, all landed:
- **Pairing ANYWHERE consumes owed printed labels**
  (_consume_unpaired_label): EPC-exact first (print jobs carry their
  encoded EPCs - the exact batch that printed the label gets the
  credit), then by SKU (newest owing receiving batch, then OPEN bin
  batches). Wired into Scan Station pairs, sweep pairs and the locate
  pair, each bump exactly as if paired from the batch's own pair
  screen - so the locate hunt, the receiving unpaired list and open
  batch tagging tasks all shrink together (his 3x F9123A + 3x F9127A
  + 1x F9127B run). Held-strip labels stay NOT-owed (credit runs
  before the held-note consumption); finished bin batches never
  change; a fully-paired receiving batch still closes itself.
  test_paircredit.py (10 checks).
- **C72 4.05 (code 123)**: the LABEL BINS product list leads with the
  SKU (the stickers in hand say SKUs); the found/pair prompt snooze
  dropped from a fixed 10 s to a Settings knob (Locate section, taps
  cycle 1-2-3-4-5-10 s, default 1 s); a bin can be PINNED from its
  labels-to-pair window onto the main Locate screen - live owed count
  (refreshed on tab entry and after every pair/undo), one tap reopens
  the bin's list fresh and enters Unpaired Tags mode if needed.
- **Bundle groups imported** (dev/import_bundle_groups.py, CSV
  gitignored as bundle-groups-*.csv): Nick's export decodes as
  master/component variant rows per group; 93 of 108 groups resolved
  (bin map first, live Shopify fallback) and imported through
  _write_bundle_contents - BundleContent recipes + ProductKind
  kind="bundle" + History receipts. The DSLR Buddy V2 couplers are
  covered; the 12 D2-5 inventory-check tasks they had opened were
  closed with the story. The mismatch checker now skips-and-closes
  bundle SKUs like non-taggables. 15 groups skipped: their master
  variants have NO SKU in Shopify (give them SKUs and re-run the
  import to cover them).
- **Partially-fulfilled orders finally count their SHIPPED lines**
  (Nick's 8H0045 / order #50260): the sync's whole-order-FULFILLED
  gate hid any line that shipped while a sibling stayed backordered.
  get_fulfilled_orders now searches shipped OR partial and counts
  quantity-minus-unfulfilled per line; the existing upsert raises
  quantities as the rest ships. dev/backfill_partial_orders.py
  walked updated_at >= Jul 20 on prod: **279 missing sold records**
  across ~230 orders backfilled. 8H0045's task closed itself; 7 real
  previously-hidden mismatches opened for walking (SS TC20-R, S11740,
  F9198J, ALP-T x2, 2423011, 18768).

## ⧉ Multi-box redo: marks at collect, sets at verify — ✅ DEPLOYED 2026-09-15 (C72 4.04)

Nick's teardown (TODO #7, now done): set ASSEMBLY left the collect
stage entirely - "doing this automatically has only caused pain".
- **"Part of a set" mark**: the C72's MULTI-BOX SET menu (picker /
  sku pass / draft pass / full product - all 340 lines removed) is
  replaced by one prompt: master SKU (defaulted to the row's own SKU
  when it has one) + Box X of Y on -/+ steppers; REMOVE MARK to
  clear. The web collect rows get the same dialog (the old builder
  entry is gone from collect). Marks are new nullable columns on
  rfid_batch_items (dev/alter_add_setmarks.py RAN ON PROD); POST
  /api/batches/{id}/items/{iid}/set-mark. A mark never resolves
  anything by itself.
- **Sets are defined on the WEB during VERIFICATION**: the verify
  report flags every marked box in an amber panel per master
  ("N box(es) marked as parts of a set", incomplete counts called
  out) with "Define the set…" opening the builder seeded from the
  marks - marked rows pre-ticked in Box X order (list order = box
  numbering), master pre-filled. Ingredient-part behavior, draft
  creation and the premade-listing ask all survive unchanged;
  defining consumes the marks. Completing a batch with undefined
  marks warns first (one plain confirm - flagged to Nick).
- **Tags follow late sets**: pairing now happens BEFORE the set
  exists, so create_box_set re-stamps THIS batch's paired tags from
  a row's old identity to its box identity when the mapping is
  unambiguous; ambiguous old identities stay for the re-label pass.
- **Duplicate guardrails stand down inside a family** (set + boxes,
  REGISTERED or MARKED in an open batch): barcode/SKU overwrite
  clashes within the family neither ask nor file Review tasks
  (_same_set_family), and the duplicate-task checker skips family
  pairs - a box often carries the parent's real barcode. Clashes
  outside the family keep every guard. (Scoped by family rather than
  by a verify-step timer: strictly safer, never fights set work.)
test_setmarks.py (17 checks). Suites 73/73. Also: the row button
moved INSIDE .bcell__info - as a flex child it overlapped the
tracker/qty stepper (Nick's report, fixed same day).

## 🔧 S11230 rescue: physical codes win + barcode removal — ✅ DEPLOYED 2026-09-14

Nick's S11230 came back dead ("no barcode on this product works"):
the main barcode is printed on BOTH boxes, and the set had registered
box 1 under the LISTING's then-current barcode (050234112307) instead
of the code physically on the carton (050234123013) - which he then
wrote onto the S11230 listing itself, so scanning it resolved the
un-scannable full-set row. Fixed four ways:
- **Prod data repaired** (History-logged set redefine): S11230-1 =
  box 1 = 050234123013, S11230-2 = box 2 = 050234230117. Both
  physical codes resolve to their boxes; typing S11230 still finds
  the full product. His open A7-1 batch works as-is.
- **The part-barcode override moved INTO _product_lookup**: a code
  registered to a box resolves to that box on EVERY surface (collect
  scans, audit finds, task re-checks) - it was previously bolted onto
  the by-barcode endpoint only. create_box_set keeps a raw catalog
  lookup so redefining a set by its shared barcode still works.
- **The builder shows each ticked row's barcode** (editable), and the
  pre-fill prefers the code the scanner ACTUALLY read over the
  resolved listing's catalog barcode - the silent wrong-code
  registration can't recur. A typed SKU never pre-fills as a barcode.
- **Barcodes can now be REMOVED**: an emptied barcode field in Edit
  Product saves (its own confirm names the consequence), writes "" to
  Shopify, clears the bin map + open batches + tag records, and shows
  in History as "old -> (removed)". The save button no longer greys
  on empty - the exact wall Nick hit.
test_boxsets +4, test_charfix +5. Suites 72/72. (The run_local
MISMATCH-1 demo row now carries the fake API's variant id, so the
Edit-product barcode flows are demoable - the 2026-09-09 twin pin
refused the old mismatched seed.)

## ⧉ Box-set builder: full-set rows + premade drafts — ✅ DEPLOYED 2026-09-14 (C72 4.03)

Nick's S11230S: box 1 carries the FULL product's barcode (scans as
the whole set), box 2's barcode resolves nowhere, and he had already
hand-made draft listings S11230-1/-2 in Shopify. Three holes closed:
- **Rows that scanned AS the full product convert to a box in
  place**: opening the builder from a resolved row pre-fills the Full
  product field with that row's identity, and any ticked row matching
  the full code gets a warn hint ("Scanned as the FULL product - give
  this box its own SKU") with the pre-filled full SKU blanked so the
  operator types the box's own. The server's full-SKU-as-part refusal
  now says the same thing. Unresolved rows keep linking as before.
- **Premade listings are reused, never duplicated**: every new-box
  SKU is probed against live Shopify (shopify.find_sku_listing -
  exact match, drafts and archived included; a failed probe falls
  back to plain create). A hit answers 409 naming the listing(s);
  the web builder shows the question INSIDE the overlay (no bare
  confirm()) and the same button confirms; the C72 (4.03, code 121)
  asks with a USE PREMADE dialog. Confirmed = the premade listing is
  used as the box, its barcode filling a blank entry, and no draft is
  created (`use_existing` on POST /api/box-sets; response carries
  `premade_used`).
- run_local: `-2`-suffixed new-box SKUs fake a premade draft so the
  ask is demoable end to end; draft_listings write enabled locally.
test_boxsets.py +6 checks. Suites 72/72.

## 📦 Packed-orders audit v2 — ✅ DEPLOYED 2026-09-14

Nick's six-point rework, all landed:
1. Pager: the current page keeps its accent (inert via click guard,
   never greyed).
2. RETIRE SOLD is gone from the listings. Entering the audit runs a
   fulfilled-orders sync (incremental; 5-min throttle, a row's ↻
   forces it), and every sub-20-tag sweep auto-classifies against
   fulfilled sales: GREEN "whole sweep matches orders fulfilled the
   same day" (Toronto dates), YELLOW "part verified: N of M" at
   >=50%, RED "most of the sweep cannot be verified" under 50%, RED
   "doesn't look like packed orders" at zero. 20+ tags = shelf
   sweep, no verdict. Rows carry the verdict tag, a spinner ↻ (rfbtn
   family, no ETA text - Nick's spec) and CHECK AGAINST ORDERS…
   Unowned tags never count against a sweep (unlabelled shipped
   product is normal). /api/epcs/packed-classify.
3. CHECK opens a native overlay window (mlOverlay shell): product
   previews with images, per-product coverage + same-day/newest-sale
   notes, and the RETIRE button lives in the window - no plain-text
   alert. STYLEGUIDE grew the matching rule (no structured content
   in alert()/confirm(); new plain alerts must be called out to Nick
   before shipping).
4. A sweep is spent ONCE: applying stamps rfid_epc_captures
   (packed_retired_at/by - dev/alter_add_packed_retired.py RAN ON
   PROD), the listing swaps its buttons for "Swept products retired
   on DATE by NAME", and a second retire answers 409.
5. History event "Packed Orders Retired" with a whole-sweep undo
   (/api/epcs/retire-sold/undo): tags back live, ledger units handed
   back, sweep un-spent.
6. EVERY History undo pressed more than a day after its event now
   confirms first, naming the age (days/weeks/months/years).

## 🧹 Sweep hygiene + inventory-check guard 5 — ✅ DEPLOYED 2026-09-14

- Batch-tagging sweeps (batch_id set) stay OUT of the big-picture
  sweep lists: the audit pickers, the packed audit and the audit
  hub's recent-sweeps card all fetch pickable=1; the audit "pull
  latest" skips them too. They still exist for their batch's verify,
  history and undo.
- Drift guard 5: 3 Inventory Checks landed a minute after a collect
  (the batch-open sync kick compared mid-window - tags jump at
  pairing, on-hand catches up at the verify raise). The mismatch
  checker now holds fire for SKUs on any OPEN batch and SKUs whose
  newest pairing is under an hour old; the daily run still files
  anything persistent.

## 🔄 Manual product refresh + confirm-not-block guardrails — ✅ DEPLOYED 2026-09-14

- ↻ Refresh on the Scan Station product card AND inside the Edit
  Product window: re-reads the exact variant on screen by gid
  (twins can't swap identities) and takes LIVE Shopify as the source
  of truth - the card, catalog row, tag records (physical bin kept)
  and open-batch rows all follow. History event "Product Refreshed".
  /api/products/refresh + shopify.lookup_variant_by_gid.
- Guardrails ask, never block (Nick's rule): a refresh whose fresh
  codes collide with another local product, and the SKU/barcode
  overwrite saves hitting a code another product wears, all answer a
  confirm (409 with the story) - confirmed writes go through and
  file a Review task recording the clash (duplicate-product
  category, own wording so the dupe-checker never touches it).
- Scan Station card: the doubled divider is gone (.print's own
  border-top removed; the card's hr is the one divider).
  test_prodrefresh.py (14 checks); test_dupes covers the forced
  overwrite path.

## 🧿 Duplicate detection: catalog gate — ✅ DEPLOYED 2026-09-14

The transposition rule flagged pairs of REAL listings. Nick's razor,
applied to EVERY duplicate rule (barcode, normalized-SKU,
transposition): a pair only files when at least one side is NOT in
the live catalog (bin map = the Shopify mirror) - the ASIAIR shape,
where the catalog knows one spelling and orphan tags wear the other.
Re-linking a side closes the open task by itself on the next run.
NOTE the trade: two real listings sharing a barcode no longer file
either (Nick's call).

## 📐 Boxify dimensions — ✅ DEPLOYED 2026-09-14 (CSV-snapshot flow)

Boxify keeps its dimensions in its own external database (metafield
sweep found nothing dimension-shaped; no API, no write access - so
no in-terminal dimension editor, ever). Nick exported the product
list CSV from Boxify's admin, and the terminal now works from that
snapshot:
- rfid_boxify_dims mirrors the export (auto-created table; import
  replaces it wholesale; zero/blank/junk dims all count missing).
- Audit tab grew a "Shopify product cleanup" row holding the MOVED
  Unavailable Stock card + the new Missing Boxify Dimensions card
  (count = products with a dimensionless variant). Its pane lists
  the missing variants (search by SKU/title), says dimensions are
  fixed in Boxify's admin, and imports a fresh export via file
  picker (/api/boxify/import, /api/boxify/status; History event
  "Boxify Import"). test_boxify.py.
- Nick's 2026-09-14 export: 4886 variants across 3328 products;
  1342 variants (27%) missing dimensions, across 1115 products
  (34%). Imported to prod at deploy.

## 📦 Audit packed orders is its own audit + sweep-list pages — ✅ DEPLOYED 2026-09-14

Nick: retiring a packed-orders sweep isn't bound to a bin, so it
left the bin-audit sweep rows and became its own audit. The + New
audit dropdown (Walk-scan bins / 1-left checks) grew "Audit packed
orders": the scope input box leaves with that selection, Start opens
its pane, and the user picks from the recent sweeps as usual - each
row's RETIRE SOLD… runs the preview/confirm flow, with the result
plan rendered in the pane. One-shot, not a tracked session (the
sweep IS the scope).
The recent-sweeps list (both the bin-audit picker and the packed
pane) got the standard windowed pager: ← [X-2..X+2] →, 10 per page,
arrows greyed at the ends, current page inert
(/api/epc-captures grew offset + total).

## 🔄 Per-product row refresh: writes stop repainting the page — ✅ DEPLOYED 2026-09-14

Nick: raising on-hand from the bin audit (and the verify table)
re-checked and re-rendered the whole bin. Now every per-product write
- audit Set-to-N, MARK SOLD, ghost cleanup; verify raise/lower,
count save, bin fix, both retire buttons - re-runs the check quietly
and swaps ONLY that product's row when the answer returns: scroll,
open panels and the rest of the report never move (an open verify
detail panel even stays open through the swap). Whole-view actions
(RUN, Raise-all, a new sweep landing, mark-tagged) still repaint in
full, honestly. Audit: binAuditScoreRow/RowHtml + data-rowsku +
binAuditRefreshRow. Verify: runVerifyCheck(onlyItemId) grafts the
fresh row pair over the live one.

## 🎯 Unpaired hunt: auto-target + hunt-by-bin — ✅ DEPLOYED 2026-09-14 (C72 4.02)

The "pings a few times then goes quiet" diagnosis, built. Locate mode
runs Session 0 on purpose (a lone target answers every round), but in
an aisle that means EVERY tag answers every round and the unpaired
sticker loses the airtime lottery.
- Auto-target: one target leading the meter with fresh signal for
  1.2s locks the single-EPC select filter onto it (the TARGET
  mechanism swapped under the running inventory) - solid pings; four
  quiet seconds release it. Pair/write-off release too; a manual
  TARGET outranks the automatics. Settings -> Locate toggle
  (up_autonarrow, default on).
- Hunt-by-bin: LABEL BINS lists every bin owing unresolved printed
  receiving labels - bins from the LIVE bin map (job bin fallback;
  server overlay on /api/receiving/unpaired-labels). Picking a bin
  shows the owed products (count + SO reference) and seeds their
  label EPCs as hunt targets.

## 🧽 Inline-style cleanup + em-dash sweep — ✅ DEPLOYED 2026-09-14

The standing STYLEGUIDE debt, paid: 49/54 app.js inline styles (5
left, all dynamic values), 57/57 index.html, 17/17 cssText blocks,
and 7 static .style assignments converted to token-driven classes
(+132 styles.css lines, utilities + modal widths + component
classes); two stray hexes tokenized (unpaired badge, fs-status err -
both now theme properly). Em dashes: 315 swept from user-facing copy
(hyphen or reword), lone "—" cell placeholders and all code comments
deliberately kept. Verified in light AND dark; suites 70/70.

## 📦 Packed-order sweep retires sold — ✅ DEPLOYED 2026-09-14

Nick sweeps the boxes he packs for orders; the web Recent Sweeps rows
grew "retire sold": every heard OWNED tag retires presumed-sold,
capped per product at its unretired FULFILLED sales - the guard, so a
stray read of something he wasn't working on stays live (named in the
answer: "re-run after those orders fulfill"). Preview-then-confirm
shows the per-product plan first. Unowned and already-retired EPCs
counted, never touched. Tombstones + History rows identical to
/api/assignments/retire; unretire per tag hands its ledger unit back.
/api/epcs/retire-sold (raw EPCs or capture_id). test_retiresold.py.

## 🛡 Over-pair guard + guided ghost cleanup + typo-twin rule — ✅ DEPLOYED 2026-09-14

Bracket-mess prevention, all three layers Nick approved:
- Scan-Station pairs (single + sweep endpoints) answer with a WARNING
  when the product's tag records exceed on-hand + sold-unretired +
  backorder + unavailable. Pair stands; web shows it amber and holds
  the product on screen, C72 (station + audit pair) shows it with the
  OTHER beep.
- Bin audit rows offer "CLEAN UP N GHOST TAG(S)" when the sweep heard
  EXACTLY what Shopify expects but more silents linger than sales
  explain: preview splits oldest-first into presumed-sold
  (ledger-consuming) + replaced, confirm applies
  (/api/assignments/cleanup-silent). Only on a confirmed shelf -
  real missing stock is never tidied away.
- Duplicate detection grew the adjacent-transposition rule (the
  ASIAIR/AISAIR class). Still NOT general edit distance - that
  drowned Review 2026-08-18; substitution neighbours (SV-105/106)
  stay unflagged.

## 🎯 Unpaired Tags hunt un-target fix — ✅ DEPLOYED 2026-09-14 (C72 4.01)

The UNPAIRED TAGS button cleared the target list, and toggleLocate's
empty-list guard answered the trigger with "scan a barcode" - dead
end. Now: entering the mode seeds targets from the server's unpaired
list (live classification still adds new ones), picking the unlinked
entry from LIST enters the SAME mode (so 100% opens the
pair-by-barcode window, as designed), and the hunt may start empty -
listening is what builds its targets.

## 🧪 run_all flake armor — ✅ DONE 2026-09-14

The bare no-output in-batch FAILs (test_batch6, test_backorder):
each suite deletes a fixed-name sqlite file in shared %TEMP%, and a
transient Windows file lock killed the suite before its first print -
with the traceback on stderr, which run_all never showed. Now every
suite runs in its OWN fresh temp dir (TEMP/TMP overridden - the
delete never fires), stderr prints on failure, and a failed suite
retries once (reported FLAKY, still worth reading).

## 🧹 Sweep write-off of unpaired stickers — ✅ DEPLOYED 2026-09-14 (C72 4.00)

The unpaired hunt worked too well: the blank roll and the broken /
mislabeled / test labels sitting by the desk answered every sweep and
drowned the locate list (hundreds, one-at-a-time was hopeless).
POST /api/epcs/ignore-heard takes a raw EPC list OR a sent capture id
and dismisses every OWNERLESS EPC in it (LabelDismissal rows, so
_still_unlinked excludes them forever - they never re-stash). Owned /
retired / companion tags are untouched: sweeping near live shelves is
safe. Printed receiving labels in the pile are written off too but
counted and named in the answer (the operator should hear when the
junk contained owed labels). Surfaces: C72 4.00 Unpaired Tags mode
grew a WRITE OFF button (confirm dialog, sends everything the hunt
heard); the web Recent Sweeps rows each carry "write off unpaired"
(capture id path). One History event per write-off ("Unpaired
Write-off"), whole-batch undo via marker (dismissed_by carries it);
undone stickers rejoin the list on the next sweep that hears them.
test_ignoreheard.py (22 checks).

## 🗂 Bin audit rework: sweep cards, arrow walk, page cache — ✅ DEPLOYED 2026-09-14

Nick: the Recent Sweeps checkbox list looked bad, the selection was
invisible, and every arrow press re-did the whole lookup. Now:
- Recent Sweeps are card rows (device, note, count, age) with USE to
  select one; the selected sweep is a prominent accent-framed card
  (SELECTED ✓ on its row) - union checks via the tick boxes survive.
- With a sweep selected, ◀ ▶ don't just step the bin name - they run
  the check against the new bin immediately: the arrows ARE the walk.
- Every (sweep, bin) report is cached on the page: stepping back
  re-renders instantly, zero server calls. An explicit RUN press
  busts the current bin's cache (a fix/mark-sold just changed the
  answer), so post-mutation refreshes stay live.
- bin_check accepts `capture_id`: a pinned single sweep is named by
  id and the server reads its EPCs itself - no re-upload of thousands
  of tags per bin step (unions still send their merged list).

## 🖼 Print queue: OPEN task frames actually visible — ✅ DEPLOYED 2026-09-14

The tier-0 (task) frame drew its rails in --line grey on a grey card:
invisible, so only the tier-1 tint ever read as "expanded". An open
task now tints its header and draws the whole frame - top, side
rails, closing bottom - in --accent. Same pre-reserved transparent
borders, so opening still never shifts a pixel (STYLEGUIDE rule 3).

## 🔢 SO 1275 + the planner that was never actually deployed — ✅ FIXED 2026-09-14

Nick saw "SO 1275" (~320 too high) on a no-labels receiving task, and
the planner's "autofill" deep link stopped working. ONE root cause:
the tc-planner webapp restarted at 20:24 on 2026-09-09 but the fixed
image was pushed 21:07 - it ran a stale container all week (no relay
fix, no #receive deep-link handler, no index no-cache). Fixed:
- Rebuilt the image (digest 79c7ec, tags 2026-09-14a + latest) and
  pointed the webapp at the DATED tag - a tag change forces the pull,
  and dated tags kill the ":latest didn't re-pull" trap for good.
  Deep link verified end-to-end in Chrome (PO #964 opened pre-filled;
  one browser reload was needed to shed the heuristically-cached old
  index - the served no-cache header now prevents recurrence).
- Batch #241 label repaired on prod: SO 1275 -> SO 952 (Antlia,
  planner-verified; no receipts/history rows carried it).
- Intake belt gate fixed: order 1275 was vendor-matched but CLOSED -
  the planner closes an order the moment its receive saves, so the
  no-labels task always names an already-closed order. The gate is
  now open-ish OR created within 90 days (a genuine same-vendor id
  collision is months old - ids run ~300 ahead of references).
  test_sonumbers.py grew to 11 checks.

## 🏷 ASIAIR bracket audit mystery — ✅ RESOLVED 2026-09-14 (records cleaned)

The 10-tags-for-3-boxes audit: Aug 3 batch 79 tagged 6 boxes; on the
Aug 18 printer-fight day the real boxes were re-stickered at the Scan
Station under the OLD "ASIAIR" spelling (the ASIAIR/AISAIR duplicate,
merged 9/8, hid the existing records); the six Aug-3 records stayed.
Cleaned via prod's own /api/assignments/retire: 4 oldest Aug-3 tags
presumed-sold (consumed the 4 unretired ledger units #49360/#49500/
#50035/#49663), 2 Aug-3 + …E513 retired "replaced". End state: 3 live
tags = the 3 real boxes = on-hand 3; ledger fully consumed;
History-logged with per-tag undo.

## 📦 Sorter: one-pile alternative + RE-PILE — ✅ DEPLOYED 2026-09-09 (C72 3.99)

Nick's 6-box pallet fit SO 943 entirely, but one product's LINE was
already fully received there, so strict coverage split a lone box off
to SO 931. sort-match now also answers `one_order_alternative`: the
single order whose line list - exhausted lines included (planner
open_orders_lines now carries them at remaining 0) - covers every
matched product, extras flagged as overflow (least-overflow, then
newest, wins). Offered, never forced: the C72 3.99 verdict shows
"PACK ALL INTO SO X" beside the split/soft verdicts, and pile mode
grew "RE-PILE - RECHECK ORDERS" that re-runs the match for a fresh
verdict. test_sortrepile.py (6 checks); suites 68/68.

## 🔢 SO numbers: history repaired + intake belt — ✅ DONE 2026-09-09

Before TC-Planner's 2026-09-08 fix its Print-labels payload carried
the planner's INTERNAL order id where the SO number belonged (batch
219 read "SO 1268" for SO 945) - and the fix only reached the
DEPLOYED planner with today's 20:53 UTC image, so wrong ids kept
landing until then (batch #230 mixed "SO 943, SO 1266" - the same
Svbony order twice). Fixed three ways:
- dev/repair_so_numbers.py RUN ON PROD (dry-run first): 9 rows
  repaired - receiving batch labels (History derives from them) and
  the open #230 collapsed to the real numbers. Vendor-matched, and
  ambiguous tokens (ids/references overlap: "SO 940" is also a closed
  firefly-books id) deliberately left alone. Receipts turned out
  already correct (their stock_order_id is the authoritative planner
  id).
- Intake belt (_normalize_so_reference): /api/receiving/prints now
  translates an internal id in the incoming reference to the real SO
  number - only when the planner order under that id names the SAME
  vendor and is still open-ish (closed-status gate defeats the
  same-vendor ancient-id collision). Fail-soft on planner outages.
  test_sonumbers.py (9 checks); suites 67/67.
- Planner side confirmed fixed and deployed (image 2026-09-09 20:53).

## ⏱ Stale-sweep guard on stock writes — ✅ DEPLOYED 2026-09-09 (C72 3.98)

Nick's ASI676MC: 2 on hand, one sold 1PM, and a 4PM write from an
11AM-sweep audit raised the count right back. Both on-hand endpoints
(raise + guarded lower) now take optional `sweep_at`; when the count
comes from sweep evidence and Shopify's on-hand bucket moved AFTER
that sweep (shopify.get_onhand_updated_at), the write is refused with
a re-sweep message - the lower's unconfirmed preview refuses too.
Evidence freshness = the OLDEST sweep in a merged union. Senders: web
bin audit (pinned/picked sweeps carry oldest_at), batch verify
(verifySweepAt from arrived/pulled captures; live trigger reads never
age it), C72 3.98 audit (auditEvidenceAt: local sweep = now, merged
captures = their stamps, cleared with the tag set). Typed human
counts send nothing and skip the guard; unreadable stamps never block
(fail-open - the guard exists for KNOWN newer truth).
test_stalesweep.py (6 checks); suites 66/66.

## ✅ 1-left Confirm actually confirms; the count box IS the on-hand — ✅ DEPLOYED 2026-09-09

Root cause of "Confirm doesn't do anything": their func app answers
rejections (the invalid-employee case) as HTTP 200 + success:false,
and oneleft._post only raised on status >= 400 - a refused confirm
looked done here while their dashboard kept the check. _post now
treats a 200 that SAYS it failed as a failure (surfaced as the
endpoint's 502 alert); with the employee fallback from earlier today,
Nick's confirms go through as Steve.
The confirm window's count box (already prefilled with live on-hand)
now MEANS on-hand: equal = plain confirm; higher = the audited raise;
lower = the sales-guarded /api/onhand-updates/lower (bin rides the
row) - if the guard refuses, the check still confirms and the
discrepancy files for Review as before. Copy says so on the box.

## 💸 Sold-before-label dismissal on receiving — ✅ DEPLOYED 2026-09-09 (web/server)

Nick: stock sometimes sells before a label reaches it, and the count
already went through Shopify. "💸 Sold - dismiss" on a focused
receiving card: the row leaves the working list (summary shows
"N dismissed (sold)"), its outstanding printed labels are dismissed
with a traceable marker (unresolved list, audits and the watchdog all
stop asking), the shipment can close itself, and completion files NO
review task for it. OUR accounting only - received counts, Shopify
and the planner story untouched. History "Sold Before Label" event
with an exact undo (row rejoins, marker dismissals removed).
POST /api/batches/{id}/items/{item}/dismiss-sold (+/undo);
test_dismissold.py (16 checks); suites 65/65.

## 🕵 Unresolved printed labels + Unpaired Tags hunt — ✅ DEPLOYED 2026-09-09 (C72 3.97)

- **Unresolved printed labels**: GET /api/receiving/unpaired-labels -
  receiving-only (TC-Planner prints + Receive entire shipment), per
  product per batch, printed minus paired minus held minus dismissed,
  with EPC candidates. Web: button under the Batch tab's open-batches
  list, overlay with per-instance "Dismiss one" (plain LabelDismissal,
  so audits agree). C72 3.97: "UNRESOLVED PRINTED LABELS…" button on
  the batch picker, read-only list.
- **Unpaired Tags hunt** (Locate tab, UNPAIRED TAGS button): listens
  for stickers linked to NOTHING. Reads are classified server-side in
  batches (POST /api/epcs/unlinked, 40 tags/1.2s, verdicts cached for
  the hunt) - unlinked EPCs become meter targets. At 99% the mode's
  OWN pair sheet opens: scan the box's barcode, done. Barcode-FIRST
  arms the product, drops to the favourited Station power, and the
  trigger reads the sticker in hand; power restores after.
- **Pairing** (POST /api/locate/pair-unlinked): refuses non-unlinked
  EPCs, consumes ONE unresolved receiving label instance of that
  product (newest owing batch's paired_count +1), logs the unique
  "Locate Assigned Tag" History event with UNDO (web History + the
  gun's UNDO PAIR button) that unlinks and gives the instance back.
- oneleft VALID_EMPLOYEES mirrored back to their five (their func app
  reverted; Nick's confirms fall back to Steve, our receipts keep the
  true actor - redeploying their app stays banned).
- test_unpairedhunt.py (17 checks); suites 64/64.

## 🤝 Pair step ignores won't-RFID-scan products — ✅ DEPLOYED 2026-09-09 (C72 3.96)

Nick: 2459281/2459286 wear the flag but kept surfacing as next in
line. Past the check step they are not pairing work: the C72's pair
auto-advance steps straight over them, their rows sink below even the
done rows and grey out (C72 + web pair list, "skipped for pairing"
note), and a deliberate by-hand pair (scan the product barcode) still
works. Collect/check behavior unchanged.

## 📊 Code 128 centering: subset-aware width model — ✅ DEPLOYED 2026-09-09 (agent bounce)

The -O barcodes exposed it: the width model charged MIXED codes one
symbol per char, but ZPL's auto-encoder packs digit RUNS into
subset-C pairs - "12345678-O" is 7 symbols, not 10. Overstating the
width centered the bars visibly LEFT. _code128_symbols now mirrors
the encoder's subset rules (print_agent.py + app.js code128Dots stay
identical); pure-digit and pure-letter codes compute unchanged.
Agent bounced 2:36 PM with the fix.

## 📦 Open-box (-O) products + queue/colour restyle — ✅ DEPLOYED 2026-09-09
(`openbox_barcode` promoted into SHOPIFY_WRITE_MODE after the deploy settled.)

**Open-box convention**: SKUs ending in -O are open-box twins. When a
-O label is claimed for printing: Shopify gets barcode+-O written TO
THE EXACT VARIANT GID THE JOB CARRIES (never re-resolved by code -
code lookups rank the primary twin first, which is how a manual fix
once rewrote the WRONG variant's barcode, Nick's field report); the
label itself prints the -O code; the original barcode is linked to
the open-box listing (alias kind "openbox") so scanning the physical
box still surfaces BOTH listings in the Check step. Self-pruning
edge handling: idempotent via the bin map, collision-checked before
writing, refuses fake ids, fail-soft (printing never waits), gated by
NEW write feature `openbox_barcode` - PROMOTE INTO SHOPIFY_WRITE_MODE
AFTER the deploy (never during). _sku_root treats -O as open-box
wording; _candidate_rank ranks -O SKUs secondary; manual
/api/barcode-overwrites accepts a pinning variant_gid (web product
window/check/candidate flows send it). test_openbox.py (17 checks).

**Queue/colour restyle (previewed to Nick, approved pending deploy)**:
columnar 3-tier print queue (frames not indentation), Event colours
pages of 10 + search + per-colour ✕ + reset-all confirm, freshness
tags on theme tokens, eye-button flush fix. Suites 63/63.

## 🏷 Label-not-paired watchdog is BACK for receiving — ✅ DEPLOYED 2026-09-09 (C72 3.95)

Nick: workers label boxes without RFID-pairing them (fair - not walked
through the system yet). The 2026-09-02 removal of pairing-incomplete
tasks stands for BIN batches; receiving gets a dedicated watchdog:
- Lazy + throttled (Review inbox read, 5-min gate): a receiving batch
  whose printed labels are still unpaired 2+ hours after its last
  print files ONE `label-unpaired` Review task (per-SKU breakdown).
  Held vendor strips (the kept label sheets), dismissed labels and
  companion labels never count. The task closes itself (resolved_by
  "auto") once pairing/strips/dismissals account for everything.
- Resolve window: "Resume receiving #N and pair the boxes" jumps
  straight into the batch.
- GET /api/batches tags receiving batches with `unpaired_labels`
  (printed - paired - held, coarse). Web resume list wears a red
  "🏷 N label(s) not RFID-paired" badge and grew a sort/filter select:
  Newest first (default, youngest→oldest) / Oldest first / Not
  RFID-paired only; limit raised 10→50. C72 3.95 batch picker shows
  the same red chip on receiving cards.
- test_labelunpaired.py (8 checks); suites 62/62.

## 🖨 Printer backfeed reverted to ~JSA (factory) — ✅ LIVE 2026-09-09 (agent v5)

Field verdict: ~JSB (backfeed-before-print, tried 2026-08-25) caused
the pseudo-jams - the retraction runs at NEXT-print time, dead-
reckoned from wherever the operator's tear left the media, and on
these short labels it pulled the leading edge BEHIND the platen
roller. Agent v5 sends ~JSA at startup (retract right after printing,
before anyone touches the media); process bounced, printer updated.
Nick will try a cleaner tear (maybe a blade/serrated edge below the
opening). SEPARATE open issue (2026-09-09 afternoon): clanking +
misfeeds + two reprints printing CENTERED ON THE GAPS across three
labels - that arithmetic (fed ~label-length 253 dots instead of pitch
~277) means the printer is NOT registering on the gap sensor: lost
media calibration, a dirty/nudged movable sensor, or media stuck in
the path from the jam-clearing era. Needs hands-on: inspect path +
platen for stuck label/wrinkled liner, reseat head latch, check the
movable sensor position, then SmartCal. No remote test prints while
Nick is away (a jam would strand the queue).

## ⧉ Box sets lump on the collect screens + cross-bin awareness — ✅ DEPLOYED 2026-09-09 (C72 3.94)

Nick's two field sets showed as loose part rows. Now:
- **Batch seeding**: the SET product is never a scannable row (its
  boxes ARE the parts); in-bin parts inherit the set's expected UNIT
  count (their own draft listings read 0, which had them dropped as
  noise). A set shelved in another bin gets one live stock fetch so a
  stray box still reads 0/N.
- **Batch GET carries `box_sets` on the batch object** (both clients,
  no new plumbing): per set - title, expected units, and EVERY box
  with its home bin, whether it's in this bin, its batch item id and
  its known tag count. Items get `boxset_of`/`boxset_box_no` stamps;
  a legacy batch that seeded the set itself gets `boxset_set` so
  clients fold that row into the header.
- **Web collect + C72 collect/pair**: one header row per set (⧉,
  "N box SKUs = 1 unit", tracker = smallest box count / expected),
  part rows lumped under it, and read-only rows for boxes whose home
  is a DIFFERENT bin - named bin, known count used in the rollup.
  Membership derives from the box_sets meta (not item stamps) so a
  fresh scan groups before the next pull. Header/remote rows ignore
  taps; CHECK/VERIFY lists stay flat.
- run_local seeds a demo set (SETDEMO-KIT, box 2 in T9-9);
  test_boxset_collect.py covers seeding, GET shape, cross-bin,
  legacy-set folding. Suites 61/61.

## 🏷 Unlinked stickers → locate list — ✅ DEPLOYED 2026-09-09 (C72 3.94)

"There are a bunch of labels that aren't linked to the RFID system."
Every sweep upload (SWEEP send, audit CHECK copy, batch sweeps - all
land on POST /api/epc-captures) now stashes the EPCs that belong to
NOTHING - no assignment, not retired, not dismissed, not a companion;
printed-but-never-paired labels included - onto ONE locate-queue entry
(sku `UNLINKED-TAGS`, capped 500). Self-pruning on every listing:
stickers since paired/retired/dismissed drop off, an emptied entry
deletes itself. The C72's Locate LIST hunts it RAW (`epc_hunt` flag,
new locateHuntEpcs - no catalog lookup); each find goes through the
normal FOUND flow whose EDIT sheet pairs or retires unknown stickers.
Audit CHECK now says "N sticker(s) not linked to any product - saved
to the locate list" instead of lumping them into neighbour noise; the
web's locate overlay shows the entry with EPC tails.
test_unlinked_hunt.py covers stash/merge/prune/delete.

## 📄 Inventory pages of 50 — ✅ DEPLOYED 2026-09-08

The tab's slow paint was rendering the whole store at once. Data
still loads once (fast snapshot + live swap unchanged); the TABLE
renders 50 rows with a Prev/Next pager; filters, search and sort
reset to page 1.

## ↩ Box-set merge + History UNDO; overlays restyled — ✅ DEPLOYED 2026-09-08

Nick's first S11810 set came out 4 parts for 2 boxes (ticked rows AND
drafts on the same barcodes). Undone on prod (drafts S11810-1/-2 kept
in Shopify for reuse). Now: entries sharing a barcode MERGE - a
ticked row plus a new-draft entry becomes ONE part that gets the
draft; two plain entries on one barcode are refused. Box-set create
events in History carry an UNDO button while the set stands (removes
part records only; drafts stay). Also: the overlay windows (Linked
barcodes, mis-label manager/picker, box-set builder) now use the
site's native modal shell (.phist-overlay/.linkbox, reset buttons,
.mlrow cards) instead of generic inline-styled boxes.

## 📝 Box sets create DRAFT listings + barcode override — ✅ DEPLOYED 2026-09-08 (C72 3.93)

Nick's S11810 exposed two holes: the alias mis-link, and box 2's
barcode BEING the full product's catalog barcode (both boxes resolved
to S11810 and double-counted). Now: the set builders (C72 picker "-
N +" row; web -/+ rows) take NEW boxes with no listing anywhere -
barcode/SKU (blank = auto SET-X, lowest unused) + bin (default the
batch bin) - and the server creates a REAL Shopify DRAFT listing per
new box ("DRAFT LISTING - INGREDIENT <title> <SKU-X>", variant
sku/barcode untracked, bin metafields; write feature "draft_listings"
PROMOTED in prod settings; mutation chain proven with a live
create-verify-delete). A physical box barcode now ALWAYS resolves to
its PART, even when it collides with the set's own catalog barcode.
The Linked-barcodes button moved out of the Product-options fold to
under the Flags group (Nick couldn't find it).

## 🗓 Unavailable dates + guarded return + pinned sweep — ✅ DEPLOYED 2026-09-08

Shopify stamps every bucket's last change (InventoryQuantity
.updatedAt) - admin-side set-asides now show their REAL date (8HGNZE
= Apr 18; live data reaches back to Aug 2024, so nothing needed the
"6+ months" fallback). Order: longest set-aside first. Rows whose tag
records AND latest sweep cover the FULL on-hand get RETURN N TO
AVAILABLE - the gated unavailable-move with new bucket "auto"
(direction out only; server finds the real buckets; on-hand total
unchanged, sellable rises). Bin audits keep a PINNED sweep: whatever
sweep a check used is reused for every next bin (pin bar + "use
newest instead") - no re-ticking per bin.

## 🔗 Linked-barcode manager + box-set alias shadow fix — ✅ DEPLOYED 2026-09-08

Nick mis-linked S11810-1 straight to S11810 with no way to see or
undo it. Product window now has "Linked barcodes (N)…": this
product's aliases (who linked, when, kind) with per-row Unlink
(History receipt "alias-unlinked" - the live row was the only trace)
plus a store-wide view. GET /api/barcode-aliases[?sku=]. Creating a
box set clears aliases sitting on part codes (aliases resolve BEFORE
the part registry - the mis-link would have shadowed the set
forever). Multi-box products with NO draft listings need none: the
part registry IS the identity, made-up part SKUs work.

## 🗂 Audits: Unavailable-stock section — ✅ DEPLOYED 2026-09-08

Fifth Audits hub card, "Unavailable stock": every product with units
in Shopify's Unavailable bucket, with bins, sellable-vs-set-aside
counts, when/who/bucket from the unavailable-move History (admin-side
moves honestly say "no local record"), and the LIVE Staff Comments
metafield per product (batched fetch, degrades gracefully). First
prod read: 44 products / 46 units, comments carrying the whole story
("Clay took one...", "missing screw set", ticket numbers). GET
/api/audit/unavailable.

## ⧉ Multi-box SETS (distinct-SKU boxes sold only whole) — ✅ DEPLOYED 2026-09-08 (C72 3.92)

Nick's A/B rack walk, the S11230: boxes each carry their OWN
barcode/SKU (usually drafts, S11230-1/-2) under an active full
listing. A THIRD thing - not a bundle (recipes), not the same-SKU
multibox mark. rfid_boxset_parts (migration RUN): ordered parts under
a set SKU; part codes resolve through the lookup chain as synthetic
products riding the full product's ids/bin. Counting = min(part tag
counts) vs the FULL product's on-hand (checks never file for parts;
audit_bins rolls parts into the set row; bin sweeps audit each part
against the SET's shelf number). Inventory shows the set with each
box identity in its own column. Defined AT COLLECT: web rows
(resolved AND unresolved) get "Multi-box set…" (builder overlay), C72
3.92 gets MULTI-BOX SET in the collect item editor. Legacy full-SKU
tags trigger the re-label offer (per-box labels + unlink, peel old
stickers). Labels carry "Box N of M" on the bin line (pairing strips
it). Follow-up when wanted: receiving + shipment-sort awareness of
part scans (v1 is collect + inventory/audits, Nick's pick).

## 📐 Audit expected folds in UNAVAILABLE; chosen SKU breaks; no audit names — ✅ DEPLOYED 2026-09-08 (C72 3.91)

Nick's ASI432MM case (3 sellable + 1 reserved, 4 tag records): the
audit compared records to the SELLABLE expectation and forever offered
"Set to 4" - a raise Shopify already had. Expected now folds the
Unavailable bucket in on the web binaudit AND both C72 audit views
("expected 4 (incl 1 unavailable)"); silence covered by the bucket
reads as the set-aside unit (green), and the raise only offers past
the folded total. SKU wrap round 3: wrapped lines inset 10 dots/side
(label-variance clipping), break points CHOSEN (operator "|" in the
label editor wins; else the separator nearest the middle; auto-wrap
only for solid tokens). Audit sessions have NO naming step - the name
derives from the scope ("Rack I1" / "Bin F1-2" / "1-left: ZWO").

## 🏷 SKU line wraps on stickers + previews; barcode max = 33 — ✅ DEPLOYED 2026-09-08

Nick's long-centre-line labels printed wrong (ZPL overprints a
too-wide single ^FB line). Threshold test prints on the ZD220 pinned
the geometry: barcode bars are thick to 15 alphanumeric chars,
hairline-but-scannable to 33 (Nick verified), off the sticker at 34+.
The centre line now wraps to TWO lines when it outgrows one - at the
same big font (Nick's call after v1's font 16 read too small); the
barcode block shifts down 30 dots and trims its bars 72 -> 56 to make
the room. Text too wide for two font-30 lines steps down only as far
as needed (floor 20; the 56-char cap lands ~22). The fit check is
FIELD-CALIBRATED: the width model runs ~13% narrow of the printer's
real font 0 (TEST 3 overprinted at font 28), so SKU_WIDTH_FUDGE 1.13
+ a 20-dot per-line break reserve - constants duplicated
print_agent.py <-> app.js, keep them in lockstep. All four web label
previews (Scan Station card, batch item, reprint, product-window
editor) mirror the wrap + tier via renderSkuPreviewLine;
labelFitIssues dropped the obsolete overlap warning and gained a
"barcode over 33 chars won't scan" one. Every path capped at 56
chars. Physically verified on three test-print rounds; the exact
overprinting string is a regression pin in test_labelwrap.py.
Remember: print_agent.py changes need the warehouse process bounced
(kill the py/python pair; the loop relaunches in 10s).

## 🎯 Locate hunts the SILENT tags — ✅ DEPLOYED 2026-09-08 (C72 3.87)

Nick's ...B3F1EB hunt: ADD TO LOCATE queued only the SKU, so the
LOCATE tab hunted every tag and the answering shelf boxes drowned
out the missing one - he had to hand-check every ASI 432MM. Now the
audit's add-to-locate sends its silent_epcs with the queue entry
(rfid_locate_queue.epcs, migration RUN), re-queuing refreshes the
set, and picking the entry targets exactly those ("🎯 hunting 1
SILENT of 4 tag(s) on file"; stale sets fall back to all tags).
Take the gun update (3.87, code 105).

## 📦 CAN'T SCAN chooser on the gun — ✅ DEPLOYED 2026-09-08 (C72 3.90)

Nick's correction: the flag family belonged in BATCH TAGGING's CAN'T
SCAN dialog (he'd only gotten the one-off skip reasons there). The
button now opens a chooser - skip just this box (old reasons), or flag
the product won't-scan / un-labelable box / non-taggable, each behind
a full-meaning confirmation. Flagging from the chooser drops the item
from the batch; the un-labelable path offers PRINT BOX LABEL on the
spot. batch_scan also refuses flagged products now (a collect scan
right after flagging used to re-add them quietly).

## 📦 Un-labelable box + mis-label picker + tab speedups — ✅ DEPLOYED 2026-09-08 (C72 3.89)

Nick's CR2032 assorted-box request plus the mis-label upgrade and the
approved caching plan, one deploy (migration
dev/alter_add_flag_kinds.py RUN on prod):

- **"Box of Un-Labelable Product"** joins the Can't Scan family
  (rfid_non_taggable.kind): the BOX gets ONE label + bin (print from
  the product window's new button; the found-untagged walk can also
  print it), on-hand still shows and stays updatable, per-unit tags
  never count anywhere (batches, receiving labels, audits, inventory
  checks, sweeps all treat it as location-only; audit rows wear a 📦
  chip with on-hand shown, zero drift). Every Can't Scan flag now
  CONFIRMS with its full meaning before applying. NOTE: prod's
  non-taggable table was EMPTY - the thumbscrews were never actually
  flagged; Nick flags them + CR2032 with the new option himself.
- **Mis-label picker**: each vendor mis-label flag carries the
  products the label might ACTUALLY be (rfid_mislabel_flags.alt_skus;
  the EXOS pair is cross-linked both ways on prod). The flag button
  manages the list (preview cards, add by barcode/SKU); scanning a
  flagged product pops "which product is this?" with previews +
  add-another - Scan Station overlay on web, and on the C72 (3.89,
  code 107) at the find / station-link / audit-pair / locate lookups.
  C72 receiving/sort keep the loud text warning (server resolves those
  scans; picker there is a future step if wanted).
- **Speedups** (Nick-approved plan): /api/audit/bins dropped from a
  measured 48s to 4s (the per-product ProductKind N+1 is now one
  query); Inventory paints instantly from
  /api/inventory/summary?fast=1 (~2s) under a yellow "last refreshed"
  tag and swaps to live numbers with a green "Up to date ✓"; the
  Audits hub restores its last-known card numbers (localStorage) the
  same way. Review untouched and the S0 tier bump dropped - both
  Nick's call.

## 🎯 Locate window retool — ✅ DEPLOYED 2026-09-08 (C72 3.88)

Preview accepted with amendments, built (3.88, code 106):

- Volume = 4-state icon (🔊🔉🔈🔇 cycle 100/50/25/0%) on the
  POWER row beside AUTO; long-press keeps the fine slider. EDIT TAG
  takes SOUND's action-row slot.
- EDIT TAG acts on the TARGETED tag (singular hunts auto-target;
  several targets = a tag picker). Sheet: SET ASIDE - UNAVAILABLE,
  NOT IN STORAGE - RETIRE, MARK PRESUMED SOLD (only shown when
  unretired sales cover it - tag-info's sold_cover), UNLINK - WRONG
  PRODUCT. Descriptions live behind "?" help buttons, orange buttons
  only, so no sheet scrolls (Nick's amendment).
- MARK FOUND fork: FOUND - ALL GOOD (trigger confirms) / FOUND - BUT
  IT MOVED (the stray trio: take it back / move ALL of the product
  to the found bin / send it somewhere new - audited bin update) /
  FOUND - NEEDS EDITING / KEEP HUNTING.
- SET ASIDE = the real Shopify bucket move (Nick's Q1 answer):
  inventoryMoveQuantities available -> Damaged / Quality control /
  Safety stock / Other (=reserved), on-hand TOTAL untouched, staff
  comment APPENDED to custom.staff_comments (default "Product moved
  to unavailable from C72."). Endpoint
  POST /api/products/{sku}/unavailable-move, gated by write feature
  `unavailable_move` (promoted in prod app settings 2026-09-08).
  Mutation shape proven live with a net-zero cycle on ZWO ASI432MM.
- NOT IN STORAGE = retire kind "not-in-storage": tombstone + undo,
  no sold-ledger consumption, honest "Shopify still counts it" text.

## 🐛 Two field bugs — ✅ FIXED same deploy (2026-09-08)

- **F1-2 "(+1)" raises for numbers Shopify already had**: stale bin-
  map snapshot kept re-offering made raises, and clicking errored.
  Equal-value raise is now a friendly no-op that self-heals the
  snapshot; every on-hand write path refreshes the snapshot
  immediately (_refresh_binmap_onhand).
- **ZWO OAG check contradicting a just-done audit**: an unconsumed
  sold-ledger row for a NEVER-tagged unit kept "1 fewer tags than
  expected" alive. A bin audit that hears EVERY tag with units ==
  expected now consumes those stale sales tag-free ("ledger-cleared"
  History event) and resolves the SKU's open Inventory Check.

## 🏷 Vendor mis-label flag + SO-reference fix — ✅ DEPLOYED 2026-09-08

Nick's EXOS2CWB5-barcode-on-EXOS2CW case (vendor printed the 5lb
variant's barcode on the 10lb boxes; on-hand 3 vs 1 on the shelf
still needs a human look - the merged Inventory Check will flag it):

- **Mis-label flag** (rfid_mislabel_flags, migration RUN): set from
  the product window's Flags group ("Flag: vendor labels
  mis-labeled"); every scan of a flagged product warns "check the
  physical product" - the warning rides the SCAN-NOTE channel, so the
  Scan Station card AND every C72 surface that shows notes carry it
  with NO gun update. Structured mislabel_flag rides lookups +
  product history; chips + History event ("Mis-label Flag").
  EXOS2CW and EXOS2CWB5 flagged on prod.
- **Planner relays now send the HUMAN SO reference** (both
  rfid-labels and the unprinted safety net used the planner's
  INTERNAL order id - batch 219 read "SO 1268" for SO 945). RFID's
  unprinted no-op guard matches either spelling; batch 219 relabeled
  to "TC-Planner · SO 945 · Explore Scientific" on prod
  (History-logged). Planner + RFID both deployed. Batch 219 (SO 945
  partial receive, Sep 3: epwp5210-01 ×3, TL-ST3B-00 ×1,
  FL-EXOSNANOT1-00 ×4, nothing paired) is still open and waiting to
  be finished - Nick's to work.

## 🔗 Planner deep link revived + html no-cache — ✅ DEPLOYED 2026-09-08

Nick: "Finish in TC-Planner just opens the planner." The deployed
planner container was running a STALE frontend bundle
(index-Cxrf8MC4.js, predating the #receive= deep-link handler from
2026-08-31) - source was fine, the running image had regressed.
Rebuilt from main (acr-build + restart; bundle back to
index-BkqYNhO- with the handler) and fixed the CLASS of bug: the
planner now serves index.html with Cache-Control no-cache (hashed
/assets stay cacheable), so browsers stop pinning old bundles across
deploys. Planner commit 38d86fb. NOTE: anyone who saw the bare
planner should hard-refresh ONCE (Ctrl+F5); after that, plain
reloads always get the current build.

## 📋 Inventory Check merger + Review self-clears — ✅ DEPLOYED 2026-09-02 (web/server only)

Nick's Review-task redesign, built from the "Inventory Checks"
artifact (https://claude.ai/code/artifact/a797cc23-5af6-4088-af16-a7278238e3df):

- **ONE "inventory-check" per SKU** from either trigger (human count
  or the nightly tag arithmetic; "tag-onhand-mismatch" retired, prod
  tasks migrated: 74 re-categorized, 16 merged). The sync only
  auto-closes its OWN filings.
- **Drift guards**: mid-receive SKUs skipped (open receiving batch or
  settled-not-planner-saved receipt); sales window baseline = OLDEST
  live pairing; surplus within Shopify's Unavailable bucket =
  agreement; rfid_onhand_log observes on-hand changes (migration
  dev/migrate_review_merge.py RUN on prod).
- **The window**: Active RFID tags / Last Heard / Shopify On-hand
  tiles with hover stories + ONE verdict line - green "Retiring N
  unheard tags would make both systems match" (one-click
  /retire-sold, sales-guarded, unheard-first), green "matches its
  unavailable stock", yellow surplus/shortfall/receive-in-progress.
- **pairing-incomplete RETIRED** (never filed again) - "Held label
  strips" window on the Batch tab (GET /api/held-lists) shows what
  waits on receiving strips instead.
- **Self-clears**: bin-check resolves on a covering non-empty sweep
  ("bin-audit" closer); could-not-scan offers closure when tags were
  added since filing; unresolved-barcode re-runs the lookup and gains
  in-window Link-as-alias / Set-as-Shopify-barcode.
- Same-day: a FULLY-PAIRED receiving batch's settle button is now
  "➡ Finish in TC-Planner (pre-filled)" - one click straight to the
  planner deep link, no strip ceremony (Nick's report).
- Suite dev/tests/test_invcheck.py (25); 55/55. No gun changes (the
  C72 audit CHECK already retires tags).

## 🔀 Stray decision: third bin — ✅ DEPLOYED 2026-09-02 (C72 3.76)

The wrong-bin window during batch-tag CHECK now has a third choice
beside MOVE IT TO {home} and KEEP IT HERE: **SEND TO A DIFFERENT
BIN…** - type the bin, the product's recorded bin updates through the
audited /api/bin-updates (Shopify + records, undo-logged), and the
item is marked MOVING so the side trip carries the box to the NEW
home with labels printed for it. No server changes. Take the gun
update (3.76, code 94).

## 📦 Full-shipment receives stay open until the planner saves — ✅ DEPLOYED 2026-09-02 (C72 3.86)

Nick's SO 941/938 reports: the close kept beating the planner
hand-off. New rule (his words): the count stays open until the
inventory planner order is checked off. Full pairing SETTLES (1h
clock) and shows the gun's planner hand-off dialog; the stock-updated
ping CLOSES the batch (backorder debt noted there). held-list is
replace-on-repost, never closes, and names swept tags that are
recorded as PAIRED boxes of the shipment's own products - the
"are these on the strip in your hand?" question re-posts with
unpair_owned to roll the mis-counted pairings back onto the strip
(the recovery for pair sweeps over-hearing the leftover strip).
Prod cleanup: SO 941's junk duplicate strip deleted, good 3-label
strip kept. 3.85 in between: collect-step multi-box count gate.
Take the gun update (3.86, code 104).

## 📦 Multi-box pairing + strip-sweep fix — ✅ DEPLOYED 2026-09-02 (C72 3.84)

Round 3 of the S11740: companion labels now have a real pairing
story - trigger-reading box 2's sticker answers "Box 2 of 2 confirmed
- the unit counts by Box 1's tag" (no tie, no count; wrong product =
loud 409), and pairing a marked product's counting tag replies "Box 1
of N paired - stick the other box label(s) on and trigger each".
Station links and sweep-assigns of companion EPCs are refused/skipped
by name. 3.84 on top: the full-shipment STRIP SWEEP no longer falls
into the pair hold-sweep (a held trigger once bulk-assigned the
leftover strip to the focused product) - strip mode outranks it, and
the sweep is hold-to-sweep: pull starts, release stops and confirms.
Take the gun update (3.84, code 102).

## ⚠ KNOWN DATA ISSUE: the 11740 has TWO counting tags on one unit

Nick, 2026-09-02: the first 11740 was received BEFORE companion
labels existed, so its single unit got two normal labels and both
were paired - two counting RfidAssignments for one physical unit.
Every count (audits, verify, expected math) reads it one high until
fixed. Nick's call: leave it for now, fix later. The fix when he's
ready: unlink one of the two ties (product window > its tag row, or
tag-unlink), peel or replace that sticker with a companion-label
reprint from the product window. Do NOT "fix" it by lowering
on-hand - the stock is right, the tag count is wrong.

## 📦 Multi-box units — ✅ DEPLOYED 2026-09-02 (C72 3.82)

The S11740 (one telescope, two cartons): durable mark in
rfid_multibox_products (boxes_per_unit + per-box bins, editable from
the product window's "📦" chip; the C72 receiving mark saves it too
and batch payloads seed the gun's guidance). Labels for a marked
product print "BOX 1 OF Y" (counting label, box 1's bin) + companion
labels "BOX 2 OF Y · {bin}" behind it - companion tags live in
rfid_companion_tags, counted NOWHERE (never RfidAssignments),
recognized by bin audits/verify/tag-info as "companion boxes heard".
Audit finds refuse a multibox code with the double-count question
(gun asks, multibox_ok retries). Migration RUN on prod (2 tables +
rfid_print_jobs.kind). One tag per unit stays the invariant.
3.81 in between: silent self-update on Android 12+ + auto-reopen;
Play Protect must be off on the gun for warning-free updates.

## 📦 Sorter round 4: retry, soft consolidation, C72→web hand-off — ✅ DEPLOYED 2026-09-02 (C72 3.80)

Nick's SO 941 test (nothing matched, no SKUs on cards): sort-match
retries UNFILTERED when the vendor-scoped walk matches nothing;
"mostly sure" consolidation (one order holds ≥80% of matched →
consolidate, strays SKIPPED with their real order named); and the
big one - SEND TO WEB TERMINAL on the gun's sort pane (and the NO
MATCH dialog) posts the counted pass to the new rfid_sort_handoffs
table (migration RUN on prod), where the web sorter's banner loads it
through sortShipScan - label-match, near-misses, bundles, planner
buckets all apply; the planner's print then creates the receiving
batch the gun pairs. Gun cards now show SKUs; unresolved boxes say
the web sorter can match them by label. Take the gun update (3.80,
code 98).

## 📦 Receiving box marks on the C72 — ✅ DEPLOYED 2026-09-02 (C72 3.79)

Nick's S30-collection and 11740 cases: the receiving item editor
gains BUNDLE OTHER BOXES ONTO THIS… (scan the other boxes of a SET;
trigger finishes; any bundled box then opens the set's card - one
label per set) and ONE UNIT = SEVERAL BOXES… (the 11740: two cartons,
one unit, one label - focusing the product says so). Gun-kept marks
(prefs keyed to batch, like scan order), no server changes; the
receiving check lists them per product. Take the gun update (3.79,
code 97). 3.78 in between: vendor-scoped sort-match (products name
their vendor, only that vendor's orders get detail fetches; recency
tie-break) + the CANNOT RESOLVE pile in pile mode.

## 📦 Pallet receiving + shipment sorter on the C72 — ✅ DEPLOYED 2026-09-02 (C72 3.77)

Nick's too-big-for-the-desk pallet, approved from the preview
artifact (https://claude.ai/code/artifact/d7d2cfb4-1cd5-4cf2-bf28-a756df654331,
now the reference doc). BATCH tab, no batch loaded:

- **RECEIVE A SHIPMENT…** — order picker → preview with flags →
  PRINT ALL + START (same full-shipment batch as the web, shared
  live) → waits at the printer via new GET
  /api/batches/{id}/print-progress with CONTINUE ANYWAY → existing
  pair UI. Wrap-up on full-shipment batches (order_receipt on the
  batch): ALL BOXES LABELLED → settle (1 h clock) → trigger strip
  sweep → HOLD STRIP + CLOSE (held-list) → desk hand-off note.
- **SORT A SHIPMENT…** — no-order-number pallet: barcode scan pass
  (counts + live "wanted by" per product, nothing written), then
  POST /api/receiving/sort-match verdict: one covering order
  consolidates (overflow flagged, not blocking; labels print in
  SCAN order via full-shipment's new scan_order); otherwise the gun
  becomes a pile sorter and the receives chain one after another.
  Unmatched boxes = "update by hand in Shopify admin".
- planner.open_orders_lines (120 s cache) feeds the matcher.
  dev/tests/test_gunship.py covers it all. Take the gun update
  (3.77, code 95).

## 🎯 Trigger = CONFIRM on dialogs — ✅ DEPLOYED 2026-09-01 (C72 3.74)

Settings → Trigger read → "Trigger = CONFIRM on dialogs" (opt-in,
default OFF): with a dialog up that HAS an enabled confirm button, a
trigger pull presses it - and ONLY then; dialogs without one (lists,
info boxes, the shelf-sweep results pane) leave the trigger to its
normal job. Built on the shared dlg() builder registering the current
dialog, so every confirmable dialog gets it for free. Take the gun
update (3.74, code 92).

## 📉 Unavailable stock + product-window polish — ✅ DEPLOYED 2026-09-01 (C72 3.73)

Nick's W9160A case (on-hand 1, unavailable 1, not on the shelf):

- **Shelf math subtracts Shopify's Unavailable bucket everywhere**
  (reserved + damaged + safety stock + QC): the bin map's qty is now
  effective (new rfid_bin_map.unavailable column, migration RUN on
  prod; values land on the next map rebuild), get_quantities/
  stock-info/expected-qty all subtract, and get_shelf_on_hand serves
  display/gate paths. ON-HAND WRITE FLOWS STAY RAW (get_on_hand
  untouched) - Shopify's own on-hand field includes unavailable, so
  raise/lower/undo bookkeeping must not shift.
- **Over-counts explained**: shelf > expected but within the
  unavailable bucket = the "unavailable" unit was on the shelf after
  all. Verify rows (web + C72, server-computed over_unavailable), the
  web bin audit chip, and the C72 audit check card all say
  "matches its unavailable stock" in green instead of flagging red.
- **Parent product window**: the bin is click-to-edit right in the
  head (Enter saves through /api/bin-updates - Shopify + local
  records + open-batch snapshots; Escape cancels) - no Edit Product
  detour. This brings the window in line with the Scan Station card's
  bin chip.
- **Multi-select bulk bin moves** (⚙ "Multi-select in batch tagging"):
  checkboxes on every resolved batch-list product; a selection bar
  offers "Set bin for selected…" - one prompt, one audited
  /api/bin-updates write per product (each with its own History
  undo), per-SKU failures named.

Take the gun update (3.73, code 91).

## 🔍 Audit check round 2 — ✅ DEPLOYED 2026-09-01 (C72 3.72)

Nick's three: (1) "printed label answered but never paired" warnings
are DISMISSIBLE for good - tap the red card, confirm, and those EPCs
vanish from every future report (rfid_label_dismissals, migration
dev/alter_add_label_dismissals.py RUN on prod; the web audit honors it
too since bin_check filters server-side). The per-product
finds_printed note gets its own dismiss action (existing finds
dismiss). (2) Check cards sort red → yellow → green, then by bin
within each colour. (3) Real cards everywhere: severity stripe,
product image, bold title over muted detail, big count on the right -
in the live audit list AND the check screen; the tap-a-card window
shows the product image + numbers with one full-width button per
action. Take the gun update (3.72, code 90).

## 🔍 Audit check polish — ✅ DEPLOYED 2026-09-01 (C72 3.71)

Nick's J2 rack audit: the check cards now lead with the product's
BIN(s) ("BIN J2-3 · 2/3 heard · …" - a rack audit lists several bins'
products, so each card says which level), and a MARK ALL SOLD (n)
button on the check dialog retires EVERY sales-covered silent tag in
one confirmed pass (same eligibility rule as the per-row button; one
undo per product in History; re-checks automatically after). Take the
gun update (3.71, code 89).

## 📦 Receive entire shipment + held vendor strips — ✅ DEPLOYED 2026-09-01 (both apps)

Nick's RFID-first receiving (Q&A'd, replaces nothing). Batch tab →
"Receive entire shipment" → pick/type an SO → REMAINING lines load
with catalog checks (unknown / non-taggable / bundle / no-bin flagged
like receiving always has) → every label prints up front → normal
label + pair work → "✅ All boxes labelled - count unused" (starts the
1-HOUR clock) → the leftover labels stay ON the liner, get swept as a
strip, and become a HELD LIST in a vendor container → the batch closes
and TC-Planner opens pre-filled with what actually PAIRED (Save, then
Update stock there - Print labels is grayed, the labels are on boxes).

- Held labels count in NOTHING (not stock, audits, or expected tags):
  rfid_held_lists (EPC pool per strip - labels aren't per-product
  encoded, the strip sweep is one pool; accounting is per-SKU counts
  in rfid_held_items). EVERY receiving label pass prints fewer when a
  product has strip labels and says "take N from the {vendor} strip";
  pairing a pooled EPC consumes it.
- Full-shipment batches are their own animal ("Full shipment · SO n ·
  vendor"): never vendor-merged (the settle math reads the batch as
  one order's story), reused live, refused once done. Old-style
  receiving is untouched.
- Watchdog: settle + 1h with no planner stock update files ONE
  "stock-not-updated" Review task (lazy, on inbox reads); resolving
  opens the pre-filled planner page; the planner's stock push pings
  /api/receiving/stock-updated which auto-resolves it
  (planner-update ∈ AUTO_CLOSERS).
- Planner touches (c38dba5, acr-deployed): apply-stock-update pings
  the RFID app; the PO detail asks /api/receiving/order-status/{id}
  (4s, fail-soft) for rfid_labels_printed → Print labels grays.
- Migration dev/alter_add_order_receipts.py RUN on prod. Suite
  test_fullship.py (24); 52/52.
- Nick's note for later: "in the future we'll have printed labels for
  all remaining products" - not built, remaining-only for now.

## 🎛 Audit tab decluttered — ✅ DEPLOYED 2026-09-01 (C72 3.70)

Nick: "the audit tab is pretty full of buttons." Preview approved in
the "Audit Tab, Decluttered" artifact; 10 always-visible controls down
to 5, zero features removed:

- SWEEP button gone - the trigger is the sweep (chip + status show
  state). LOAD gone - Enter on the bin field or an ◀ ▶ tap loads the
  location immediately (arrows auto-load, Nick's answer #1).
- PULL / SEND / CLEAR / AUTO-PRINT moved into one ⋯ tools sheet,
  which gained "Pick recent sweeps…" - the last 10 server captures,
  tick several to merge (parity with the web picker).
- PRINT LABELS is now a contextual banner: amber with the owed count
  while finds exist, the pair-mode exit while pairing, hidden
  otherwise. CHECK ✓ is the one big bottom action next to ⋯.
- CHECK also SAVES the collected sweep server-side (note "AUDIT
  {loc}", deduped on repeat checks), so the web terminal can re-run
  the same audit later from Recent sweeps - Nick's answer #3.

Take the gun update (3.70, code 88).

## 🧭 Side trips, streamlined — ✅ DEPLOYED 2026-09-01 (C72 3.69)

Nick's five-part rework of the wrong-shelf flow (all gun-side except
one server change):

1. **SET ALL TO MOVE HOME** on the stray-review window marks every
   stray for its home bin in one tap - nothing moves until the START
   button is pressed (never auto-starts).
2. **Trips chain**: with strays bound for several bins, START creates
   ALL the trips up front (every label prints in ONE burst) and FINISH
   on one trip drops straight into the next - no detour through the
   parent batch. startSideTrips + pendingTrips queue in MainActivity.
3. **Already-tagged strays just get carried**: divert now accepts
   tagged_before-only rows (they were invisible to the movers filter
   AND the gun's stray list) and allows a ZERO-label trip (status
   "pairing", message says carry). The gun greets such a trip with
   "ALREADY TAGGED - JUST CARRY" and one MOVED ✓ confirm closes it.
   Bundles-only trips stay refused.
4. **A lone product focuses itself** on trip entry - no barcode scan
   before the trigger.
5. **A one-product trip closes itself** when its last printed label is
   paired (exact landing only - an overshoot never auto-closes), then
   chains to the next trip or back to the parent.

Also: resuming a side trip from the batch picker now restores its
parent link (it used to be lost, stranding FINISH TRIP). Suite
test_tripflow.py (7); 51/51. Take the gun update (3.69, code 87).

## 🖨 Printer wedge watchdog + purge — ✅ DEPLOYED 2026-09-01 (agent v4)

The ZD220 silently stopped taking data overnight: Windows said
"no error, printing" while 16 labels piled behind a Printing-Retained
head, and the app said "agent online" because jobs are marked done at
Windows hand-off. Power cycle fixed it; nothing could see it.

- **print_agent v4** (RUNNING - updated in place and task restarted):
  reports the WINDOWS queue's depth + oldest-job age on every command
  poll, and answers a new "purge" command by deleting every Windows
  job. The Queue pill goes red "Printer WEDGED - N label(s) stuck for
  Xm - power-cycle or Clear stuck jobs" when the oldest job outlives
  90s; new confirmed "Clear stuck jobs" button (disabled with update
  instructions on pre-v4 agents). Server-side those jobs already read
  done - reprints cover anything that never came out.
- **Printer picker opens from any tab**: the overlay markup lived
  inside the Scan station section, so the header 🖨 from another tab
  unhid a window nobody could see until Scan station opened. Moved to
  body level. Suite test_printerwedge.py (11); 50/50.
- Field note: the wedge predates yesterday's prints and is NOT tied
  to the tearing test (that was ~Aug 26); cause unknown - the
  watchdog exists precisely because Windows reports nothing.

## 🔍 C72 AUDIT tab + rack audits — ✅ DEPLOYED 2026-09-01 (C72 3.68)

Nick's untagged-boxes-broke-my-audit case, designed over three Q&A
rounds. Core model: **the final sweep is the only counter of physical
presence** — expected on shelf = Shopify on-hand + uncleared backorder
debt (sold-unretired explains silent tag RECORDS, not missing boxes);
barcode "finds" of tagless boxes are WORK ITEMS (label → pair → the tag
answers the next sweep), never audit evidence.

- **New AUDIT tab on the gun (3.68 code 86)**, two modes in one screen:
  WALK (trigger toggles a continuous sweep, live unique-tag counter;
  barcode any tagless box) and DIRECTED (type/arrow a bin or rack,
  LOAD the expected list, per-product counters fill live from the
  collected set; PULL SWEEP merges the newest server capture, SEND
  uploads, CLEAR confirmed). CHECK posts the collected tags and shows
  the verdict screen — ALWAYS shown, "✓ ALL CLEAR" included (Nick:
  audit is information, never skip the answer). Tapping a row offers:
  raise Shopify stock (heard units incl. ghosts), sales-guarded LOWER
  (only when the product's bins were batch tagged), MARK N PRESUMED
  SOLD, ADD TO LOCATE LIST, UN-RETIRE answered ghosts, and Open in
  STATION for flags/edit. LOG AUDIT files a "bin-audited" History
  event (a log, not a live link — Nick's call).
- **Tagless-box finds** (`rfid_audit_finds`, migration
  dev/alter_add_audit_finds.py RUN on prod): POST /api/audit/finds
  resolves the code through the full rescue chain, remembers the HOME
  bin (labels say it), flags no-bin products (alert dialog in
  auto-print mode, list flag otherwise). In-tab AUTO-PRINT toggle
  queues per scan; PRINT LABELS queues all open finds (no-bin held out
  and named) and offers PAIR MODE (barcode focuses, trigger pairs,
  ends on dismiss or when every label is paired — Scan Station always
  works too). Finds live until paired, dismissed, or 1h old
  (unprinted); printed ones stay flagged. Resolution is automatic:
  pairing ANYWHERE consumes the oldest find by SKU
  (`_consume_audit_find` in both pair endpoints — the ZD220t pairs
  factory EPCs), and label-EPC matches also resolve (encoder-printer
  future).
- **Rack = one zone**: a dash-less location ("F1") expands
  server-side to every F1-* bin; same-SKU rows on several levels merge
  (qty summed, bins named). No per-bin RFID attribution — the C72's
  read field can't localize below a rack (Nick); the barcode gun is
  the only surgical locator.
- **bin_check grew the audit annotations** (web + gun share it):
  per-product backorder_debt, finds open/printed, GHOSTS (retired tags
  that answered = box never left — flagged, then treated as one more
  scan), and heard printed-label EPCs called out as "printed label
  never paired" instead of anonymous unknowns.
- **Bin arrows + natural order everywhere**: GET /api/bins/names
  (E1-1 … F2-1 … F10-1; odd names last); ◀ ▶ on the web bin-audit
  input and the gun's audit tab — stepping RACKS when a rack is typed.
- **Recent-sweeps picker** (web "Recent sweeps ▾" + gun PULL SWEEP):
  the last 10 captures listed; ticking several combines them into one
  union check, so a good sweep survives being overwritten by a newer
  one.
- Suite test_auditwalk.py (28); 49/49. EVENT_META "bin-audited"
  (Audit Done). Rejected by design: misplaced-box catcher and
  all-green auto-advance (both die on the big-antenna reality),
  live web link (log only), walk checkpointing (warehouse too small).

## 🔗 LINK relay drives the whole Batch Tagging tab — ✅ DEPLOYED 2026-09-01

Nick's 2026-08-31 note, built next day (client-only; no gun change —
the C72's LINK tab just forwards scans). One dispatcher
(batchLinkMode in app.js) decides per scan what the screen shows:

- **Sorter open** → a relayed barcode is a sorter scan (same
  by-barcode → label-match → bundle routing); tag reads are refused
  with a message. The sorter's status line answers the gun's ding.
- **Open receiving batch** → unchanged (barcode focuses the card, tag
  pairs to the focused product).
- **Open regular batch, ANY step** → Collect: barcode adds a box
  through the same path as wedge input (case-decision dialog and all);
  Pair: barcode switches the active product, tag pairs to it; Verify:
  tag reads collect into the sweep set; Check/Print: clear "no scan
  action" answer. Tag-vs-barcode mixups all get explicit refusals.
- **Batch tab with nothing actionable** (bin list, batch done) →
  relay suspends exactly like a hidden tab: scans are skipped, never
  burst-replayed.
- **The LINK bar now appears ON the Batch tab** (mirrored copy of the
  Scan tab's bar via MutationObserver — one shared state, click either).

Wedge handlers were extracted (batchCollectScan, batchPairTag) so the
relay reuses them with every guard intact. Browser-verified end to end
(every mode + both regressions); 48/48 suites.

## 📥 Shipment sort → planner hand-off — ✅ DEPLOYED 2026-08-31 (both apps)

Nick's mixed BuckeyeStargazer delivery. Batch tab "Sort a shipment":
scan every box; each scan resolves the product and asks the planner
bridge which OPEN stock orders expect it, filling the oldest-expected
order first, spilling to the next, unexplained list for the rest. Each
bucket opens PRE-FILLED in the TC-Planner (#receive= deep link, capped
at Remaining, one-shot, survives login) where Save / Update stock /
Print labels stay manual (Nick's explicit wish). Pile survives reloads
until cleared; per-row mis-scan minus.

**Component bundles (added same day, deployed)**: "Bundle components…"
links picked rows + a set SKU into a remembered definition (survives
Clear pile; Unbundle forgets). Component scans tally inside a dashed
outline with per-component counts; the SET count (minimum across
components) sits centered beside them; the bundle lives in exactly ONE
bucket (excess noted on the block, never spread). The planner hand-off
carries only {set sku, qty} - the planner is untouched, per Nick.

Label matching (his 10 recovered scans drove it): after by-barcode
misses, /api/products/label-match runs separator folds over sku,
barcode, and VARIANT titles (EAF-FTF30→EAF-FTF-30; ZWO-Slider-Gen2→
variant "ZWO Slider Gen2" on ZWO-SliderCase-Gen2) - unique hits only,
rows labeled "matched by …". A token-level near-miss (RigelQF-Synta→
Synta-RigelQuikfinder: word order free, 5+ char prefixes, short tokens
exact so Tilter-I never suggests Tilter-II) returns a SUGGESTION the
sorter renders with Link (alias) / Set-as-Shopify-barcode buttons that
re-sort the row on click. Sets/combos stay human (S30Pro-Set's four
component labels; the NexStar+Tripod combo variant). Suites
test_labelmatch (8); planner.health carries app_url.

## 🧵 Receiving-as-pairing round — ✅ DEPLOYED 2026-08-31 (C72 3.66)

Also in this round (Nick's answer, same day): **the C72 LINK relay
drives an on-screen receiving batch** - while the Batch tab shows an
open receiving batch, a relayed barcode focuses that product's card
(server-resolved fallback for aliases/rescued chars) and a relayed tag
read pairs to the focused product, announcing the auto-close. Desk
linking completes receiving from the web without the gun's pair
screen. The verify-dialog third button (NEW SWEEP) was confirmed
redundant with the on-screen CLEAR and removed.

Second local-only round on top of the planner streamlining (C72 3.66
code 84 built; ships with the round below):

1. **Web receiving focus**: the focused card jumps to the top of the
   list over an "everything else in this shipment" divider.
2. **Web focused-card sweep pairing**: "📶 Use latest C72 sweep" on the
   focused receiving card pairs every unowned tag from the newest
   sweep into that product (same /unlinked + /pair mechanics as the
   gun's held sweep; announces auto-close), with the bulk chip beside
   it (shared sweepNoteState renderer; green when the sweep matches
   the labels left).
3. **C72 receiving = pair-only task**: entry always lands on PAIR
   (planner collected/printed); NEXT is "CHECK ✓" - a two-button
   dialog listing expected · printed · tagged per product, red (printed
   but 0 tagged) / yellow (part) / green (every box paired) - CONFIRM
   exits straight to the batch list; auto-close does the real closing.
   Gun-side START RECEIVING, the collect/PRINT pass, and the FINISH
   RECEIVING exit path are removed (planner-driven end to end).
4. **C72 card tap = focus**: tapping the product card selects it for
   pairing like a barcode scan; the new ✎ chip (bottom-right) opens
   the editor a tap used to.
5. **C72 verify dialog is two buttons**: NEW SWEEP removed - it
   duplicated the CLEAR button on the verify screen underneath
   (that's the answer to Nick's third-button question).
6. **Web verify**: the "Sweep #N arrived - checking the bin…" line now
   resolves to "checked ✓ … sweep again or Complete batch" once the
   check lands.

## 🚚 Planner streamlining round — ✅ DEPLOYED 2026-08-31 (both apps)

Three pieces, tested (47/47 + browser), shipped together with the
receiving-as-pairing round (RFID: mkdeploy+az; planner: acr-build):

1. **Bulk-link sweep chip**: the Scan Station's BULK mode polls
   /api/epc-captures/latest-summary (registered before /{capture_id})
   and shows, next to "Use latest C72 sweep", how many UNTAGGED tags
   the newest sweep holds (counted like batch tagging counts a sweep).
   Green = matches the labels left this bulk; yellow = partial/over;
   red = no sweep, or a sweep of only already-tagged labels. Stale
   sweeps get an "Nm old" suffix.
2. **Planner: Print labels moved INTO the Update-stock window**
   (StockUpdateModal): prints for the CHECKED lines
   (adjustment→received_qty), tracks which line-ids got labels, turns
   into "Labels printed ✓". The bottom-bar button is gone.
3. **Update-stock safety net**: apply-stock-update relays
   pushed-without-labels lines (frontend sends unprinted_item_ids) to
   the RFID app's new POST /api/receiving/unprinted — books the SAME
   receiving-batch rows as Print labels but queues NOTHING, and ONE
   open "Labels Not Printed" Review task per batch tracks the owed
   labels (repeat pushes fold in; ReviewNotes count units). Resolution
   = the window's "Queue the missing labels" button →
   /api/review-tasks/{id}/queue-labels, mechanically identical to a
   print pass (home bins, no-bin items held out). receiving_prints
   refactored onto shared _receiving_intake. Suite test_safety.py (18).

## 🧷 SKUs containing a double-quote: searches escape, not strip — ✅ FIXED 2026-08-26

Nick's three Antlia 2" filters (ANT-ULTRA-2.5nm-2"-Ha/-OIII/-SII)
refused every bin-move path. All six Shopify search builders STRIPPED
'"' from terms before embedding them in sku:"..." queries — the search
ran for a SKU that doesn't exist, lookups 404'd before writing, and
the same stripping silently blanked on-hand/stock-info fetches for
those SKUs store-wide. Verified live: stripped term = 0 matches,
backslash-escaped = hit. shopify._search_term() (backslashes doubled,
quotes escaped) replaces every strip site; exact post-filters still
compare the original string. The three filters were then moved to
D1-1 through the fixed endpoint (Shopify + bin map + History). Suite
test_quotesku.py (5); 46/46.

## 🔄 Inventory checks follow the newest count + Auto-Resolved tag — ✅ DEPLOYED 2026-08-26

Nick's ZWO M54-M54-7.5: a stale "0 counted vs 1" task outlived a newer
batch that counted the 1. Now batch_complete reconciles every OPEN
inventory-check per counted SKU: figures rewrite to the fresh
observation (ReviewNote names the batch), an AGREEING count closes the
task itself (resolved_by "batch-count"), and a still-standing mismatch
lands on the existing task instead of stacking a duplicate. History
derives a distinct "review-autoclosed" (Auto-Resolved, slate) event
for system closures — batch-count, orders-sync, dupe-check (the
AUTO_CLOSERS set) — so they never pose as a person's click; the reopen
undo is unchanged. Backfill ran on prod: 29 stale tasks closed against
newer agreeing counts (incl. #8882 M54-M54-7.5), 28 updated but
honestly still open. Suite test_autoclose.py (12); 45/45.

## ✅ Inventory Check: "Shopify is wrong → use the RFID count" — ✅ DEPLOYED 2026-08-26

Bin-mismatch-style choice button in the resolve window (plus the −/+
recount steppers grew to 48×42px anchored at 20%/80% of the button).
Writes the CURRENT counted number (post-recount) to Shopify: raises via
the audited /api/onhand-updates; lowers via the bin-audit
/api/onhand-updates/lower with its sales-coverage guard (the server's
unconfirmed description is the confirm prompt; uncovered drops are
refused with the guard's message). Equal counts resolve with a note.
Client-only; all three paths browser-verified. Note: prod
SHOPIFY_WRITE_MODE already includes verify_onhand_lower (CLAUDE.md's
two-mode list was stale).

## 🔁 Sweep pairs feed the pair auto-advance — ✅ DEPLOYED 2026-08-26 (C72 3.65)

The 3.60 auto-advance only hooked single trigger reads, but most of
Nick's pairing is held sweeps — so the hop never fired for him.
assignEpcs (the sweep's assign path) now bumps the local paired count
by what the server accepted and runs maybeAutoAdvance before the
reload; the reload re-points selection by id, so an advanced selection
sticks. Exact-landing rule unchanged: an overshooting sweep never
advances. Take the gun update (3.65, code 83).

## 🖨 Printer offline (claim 500) + Resume printing — ✅ DEPLOYED 2026-08-26

Two same-day field reports from Nick:

- **Claim 500 / printer "offline"**: the morning's DTU write-trim added
  a 45s throttle to `_touch_printer` that subtracted Azure SQL's
  tz-AWARE last_seen from a naive utcnow() — TypeError, so every agent
  claim after the first stamp returned 500 while the agent itself ran
  fine. sqlite (tests) reads naive, which is why suites stayed green.
  Fixed with the same normalization list_printers uses; regression
  check pins an aware value through the session identity map. The
  "Add a printer" hint now gives the FULL working command (app URL
  filled in; agent key lives in print_agent_loop.cmd).
- **Resume printing** (Queue tab, green ▶ next to Stop): a stopped
  label never printed and its EPC was never used, so
  POST /api/print-jobs/resume returns the SAME jobs to pending —
  original ids keep the original print order, ahead of anything queued
  since. Enabled from a server-side `resumable_stopped` count; skips
  batches that have since finished; History "Resumed Printing"; Stop ⇄
  Resume can loop forever. Nick's stopped F2-5 run (82 labels) resumed
  with it minutes after the deploy. test_printorder +6.

## 📦 Backorders vs the tag arithmetic + sold-out shortcut — ✅ DEPLOYED 2026-08-26

Nick's AirGradient I-9PSL-DE-KIT: Shopify on-hand sat at **-1** (one
unit committed on customer backorder), 48 boxes arrived and were
tagged, so Shopify counted 47 against 48 tags and the Tags ≠ On-hand
check false-flagged the SKU forever (task 8886).

- **rfid_backorder_debt** table (migration `dev/alter_add_backorder_
  debt.py`, RUN on prod): units Shopify's on-hand runs behind the
  shelf. Noted automatically when a receiving batch closes — gap =
  tags − on-hand − sold-since-baseline − existing debt, **capped at
  the committed units** Shopify still owes customers, so a plain
  mistag can never hide in it. Fail-soft; one batched on-hand call,
  per-SKU breakdown only for gap>0 rows.
- Expected count = on-hand + sold-unretired + **debt**; the task
  detail names it ("+ N on customer backorder when received").
- Clearing: any operator on-hand write supersedes older notes (lazily
  in the refresh); History "Backorder Noted" event's undo clears one
  by hand (POST /api/backorder-debts/{id}/clear → "Backorder
  Cleared"). AirGradient's unit backfilled; next sync closed 8886
  ("Tags, on-hand and the sold ledger agree again").
- **Sold-out shortcut**: the Tags ≠ On-hand resolve window shows live
  on-hand + tag units, and at on-hand **0** offers "Mark all N tag(s)
  presumed sold" (POST /api/review-tasks/{id}/retire-all-sold —
  re-checks live on-hand server-side, retires every tag presumed-sold,
  consumes the ledger, resolves the task). History folds same-moment
  retirements into ONE event with a grouped undo restoring the set.
- Suite test_backorder.py (27 checks).

## 🧹 Check-step polish: named broken chars, instant fixes, ordering, admin links — ✅ DEPLOYED 2026-08-26 (C72 3.64)

Nick's four asks (suite test_checkpolish.py, 12 checks; 44/44):

1. **bad-chars names its field and character**: new
   `shopify.get_variant_idents(gid)` recovers the REAL character from
   live Shopify (VARCHAR stores it as '?'), review entries carry
   `bad_chars` {sku/barcode: value with the offender bracketed}, and
   the web editor + C72 3.64 dialog read e.g. "The SKU contains a
   character the database can't store: ZWO-HA 7nm 1.25[″]. Recommend
   updating the SKU." Fallback brackets the stored '?' if live fails.
2. **Overwrites show up immediately**: SKU/barcode overwrites push the
   new value into OPEN batches' rows and live tag records
   (`_refresh_item_idents`) — web Check list and the C72's next pull
   both see the fix without waiting for the nightly rebuild. Finished
   batches keep their history.
3. **Check list ordering**: server-side rank (web + C72 for free,
   `_FLAG_RANK`) — biggest problems first, count-mismatch explicitly
   LAST; stable within tiers; web re-sorts after its local wrong-bin
   "ignore" filter.
4. **Preview title → Shopify admin**: the parent product-preview
   window's title links to the product's admin page (`admin_url` in
   the product-history payload; store-domain /admin redirects).

## 🔤 Spaced SKUs from the gun: form-encoded paths — ✅ FIXED 2026-08-26 (C72 3.63)

Nick's ZWO D25AR: typing the SKU into the gun's "LINK TO A PRODUCT"
found nothing, though Shopify has it. Java's URLEncoder is a FORM
encoder - spaces become '+', which a URL PATH segment reads back as a
literal plus - so the gun asked for "ZWO+D25AR". Space-free barcodes
never trip it. C72 3.63 (code 81): new encPath() (%20) used at all 14
path-segment call sites (by-barcode, tag-info, cases, label-names,
serial-prefixes, planner/on-order, products/{sku}, bins/{bin},
rfid-assignments); query-string values keep form encoding (correct
there). Server safety net for older builds: the lookup rescue chain
gained a LAST-resort '+' -> space fold - real plus-bearing SKUs
("22451+81037+93575") always win untouched first. test_charfix 16.

## 🔥 DTU poll storm: the gun's orphaned UI loops — ✅ FIXED 2026-08-26 (C72 3.62)

The midday 100%-DTU flatline was pure CPU with near-zero IO, and the
plan cache told the story: /api/c72/tuning + /api/c72/commands hit
~243k times in 48 minutes (~85 polls/s vs the designed 0.5/s). The
C72's 400ms refreshTick (which carries those polls) is posted in
onCreate and reschedules itself forever - activity recreations (theme
toggle, remote recreate, config changes) each leaked one more copy of
the loop, stacking into the storm. Sub-0.1ms queries, but that request
volume alone saturates Basic's CPU sliver. Fix: static generation
counter per onCreate; stale loops die on their next tick (3.62, code
80, on prod - the gun should take the update). Until it updates, a
couple of stacked loops (~1.2 polls/s) linger, which is harmless.
Lesson recorded: DTU on this database = REQUEST VOLUME first, query
weight second.

## 🧰 Six-pack: product window, review truth, wrong-bin strays — ✅ DEPLOYED 2026-08-26 (C72 3.61)

Nick's afternoon batch, all six shipped (suite test_batch6.py, 15
checks; 42/42):

1. **Change vendor** in Product options: POST /api/vendor-overwrites
   (product-LEVEL Shopify write - every variant follows; confirmed,
   History "Updated Vendor", reverse by re-running with the old name;
   bin-map vendor column follows immediately). Button shows the
   current vendor (product-history payload now carries `vendor`).
2. **Flags group**: won't-RFID-scan + non-taggable (renamed "Flag:
   non-taggable") sit in an outlined "Flags" fieldset like Bundle
   options; active flags light their button (optflag--on) AND render
   as chips under SKU/Barcode at the top of EVERY preview built on
   this panel. The live-tags expandable moved below Product options.
3. **Tags ≠ On-hand zero-tag guard** (orders_sync): a SKU with ZERO
   live tags files no mismatch task and auto-closes a stale one - the
   ANTI-DEW "0 units but expected 1" came from old ledger rows with
   nothing tagged (no tag pool to reconcile, and the task's own remedy
   - a sweep - can't work).
4. **Wrong-bin accidental scans assert nothing**: root cause found in
   prod (F2-4 batch 171: vendor barcodes mis-resolved to ZWO EAF PRO/
   F1-2 and 8h00ls/D2-2, decremented to 0, still filed "counted 0"
   checks against other bins). Rows with 0 units + 0 paired + a home
   bin foreign to the batch are now DROPPED from verify and file
   nothing at complete. C72 3.61 (code 79): verify-report card of a
   wrong-bin row gains "IGNORE IN THIS BATCH" (zeroes qty +
   tagged_before; refuses while tags are paired). test_binfix updated
   to the stronger contract (row absent, not merely no-offer).
5. **Tag timeline**: the product window's tag list also shows
   presumed-sold tombstones - "EPC · bin · presumed sold [date] by X"
   (product-history `sold_tags`; dead/replaced don't list).
6. **Review inventory-check manual recount**: below Jump-to-audit, a
   [−  Set counted to N  +] composite; the pending correction rides
   next to Counted as "(+1)" in yellow, green when counted+delta
   equals live Shopify. Apply = POST /api/review-tasks/{id}/recount
   (rewrites the task's counted figure, corrects the source batch row,
   History "Manual Recount", ReviewNote) then - only if Shopify
   differs upward - the existing audited on-hand raise. Lowering
   Shopify stays a bin-audit job, said in the confirm.

## 🔫 C72 3.60: pair auto-advance + slowness + printer verdict (2026-08-26, afternoon)

Three of Nick's field reports in one round:

- **Pair auto-advance (C72 3.60, code 78, setting OFF by default)**:
  Settings > Batch tagging > "Auto-advance pairing". When a product's
  paired count reaches EXACTLY its printed-label count, selection hops
  to the neighboring product in PRINT order (the label-stack order:
  first_scanned_at, now included in the batch item payload; the pair
  response's item merge preserves printed_count). Direction (top-down
  vs bottom-up through the stack) is inferred - first/last-position
  completions and completion-to-completion movement are the signals -
  and LOCKS after 2 consistent ones, per Nick's "only check until
  sure". Over-pairing never advances (red rows should hold attention);
  when nothing is left, it says every printed label is paired.
- **C72 slowness = the DATABASE, not the gun**: TELCAN runs on Basic
  (5 DTU) and pegged 100% DTU 8:30-11:00 while Nick worked - request
  maxima hit 240s; every 1-row UPDATE (qty +1, scan deletes) queued
  behind the throttle. Trimmed the constant write load (_touch_printer
  now refreshes last_seen at most every 45s instead of on every 3s
  agent claim; liveness window is 120s so nothing changes visibly).
  REAL fix = tier bump, WAITING ON NICK/STEVE (monthly cost):
  `az sql db update -g shopify-automation-rg -s telcansql -n TELCAN --service-objective S0`
  (10 DTU, ~$15 USD/mo; S1 = 20 DTU ~$30/mo). Basic is ~$5/mo.
- **Printer rip-drift verdict**: ~JSB does NOT fix tear drift (field-
  confirmed; backfeed is dead-reckoned, only a ~PH gap-sensor feed
  re-registers). Agent v3 (running): optional --realign-after-idle MIN
  feeds once at the first burst after MIN minutes idle (1 blank label
  per fresh session instead of 2 misprints + 1 blank after a hard
  rip). Briefly ON 2026-08-26 evening at 0.5 min, then **OFF again the
  same evening**: the recurring drag traced to Nick tearing UPWARD
  (which pulls the liner through the platen grip); he is testing
  downward tears for a few jobs first, and the flag comes back only if
  clean tears still drift. Flag lives in print_agent_loop.cmd; the
  Queue tab's manual Re-align button stays as the fallback. Agent
  logging is line-buffered now so the log file is finally live.

## 🖨 Print step stays put during a live run — ✅ DEPLOYED 2026-08-26

Nick: the web Print step jumped to Pair after the first burst of 5
prints with jobs still queued. Root cause: the C72 has no print step -
its pair screen publishes ui_step "pair" the moment the operator gets
there (which is exactly when the first burst lands), and the first
pair also flips batch status to "pairing"; the web's follow-along
obeyed both signals unconditionally. Fix (app.js): while the Print
step is watching a LIVE run (pending+printing > 0, tracked in
bprintOutstanding by the poll; null = not yet known = hold), a "pair"
target from either signal is ignored - the gun pairing while labels
still print is the normal rhythm, not a step change. Following
resumes by itself once everything printed, and the step chips always
work by hand. Browser-verified both ways (gate holds at 0/8..5/8,
releases at 8/8).

## 🔎 Show Recommended: near-miss folds — ✅ DEPLOYED 2026-08-26

Nick's ZWO T2-Tilter-II case: the product's SKU is "ZWO T2-Tilter-Ⅱ"
(Roman numeral) and its barcode "ZWO-T2-Tilter-II" (hyphen for the
space), so the printed code matched neither and Show Recommended found
nothing. /api/bins/{bin}/odd-barcodes `recommended` now matches in
tiers: exact (as before), then NFKC lookalike-folding (Ⅱ -> II,
fullwidth chars), then separator-folding on top (space/hyphen/
underscore/dot/slash runs are interchangeable; keys under 3 chars
never fold-match). Lower tier wins, SKU beats barcode, the WHOLE bin
is searched (not just odd-barcode rows), and the `reason` string says
which fold matched - both the web terminal and the C72 render that
server string verbatim, so NO APK build was needed. Deliberately NOT
edit distance (Nick's explicit rule: one character off is how genuine
neighboring SKUs differ - test pins that a Tilter-I neighbor never
folds into Tilter-II). test_charfix.py grows to 15.

## 🔍 1-left checks on receiving: cold-start gap closed — ✅ DONE 2026-08-26

Receiving the iOptron order raised 8 one-left checks (detected 14:29-
14:32 UTC, the moment the planner's stock increase hit Shopify). Root
cause: the 0→1 direction guard in THEIR func app (added 08-18) can only
suppress a restock when its small-qty cache knows the previous value,
and the cache only learns a value when stock MOVES while ≤2 - items
dormant at 0 since before 08-18 fail open once. Nick's go, both fixes:

- **Cache seeded store-wide** (dev/seed_oneleft_smallqty.py + az blob
  upload): all 4,758 variants currently holding ≤2 available units now
  have entries (4,506 added; real webhook-written entries always win).
  Pre-seed blob backed up at inventory-verification/
  backups/inventory_last_small_qty-2026-08-26.json.
- **New-item guard** in inventory-verification-func: on a cache miss at
  available==1, `inventory_item_created_recently()` (GraphQL createdAt,
  30 days) skips the check - a brand-new item's first intake landing on
  1 is receiving, not a sale. Old items with no entry still queue (that
  only happens when big stock drops straight to 1 - a sale, the
  dashboard's job). Fail-open like the other guards. Deployed via the
  squashfs-extract → edit → forward-slash zip → config-zip recipe;
  verified after: all 13 functions listed, both sync webhooks answer
  401 to unsigned posts, /api/api/pending serves (NOTE the double
  prefix: host.json keeps routePrefix "api" and their routes also
  start "api/").
- The 8 open iOptron checks were left in the queue on purpose - they
  auto-clear with tag evidence once the shipment is tagged.

## 📦 Receiving phase 2: stepless list + Assigned Tag undo chain — ✅ DEPLOYED 2026-08-26

Nick's evening direction, refined by his answers next morning and
deployed (rfid_released_tags migration run on prod). Previews live in
the "Receiving Rework Preview" artifact.

- **Receiving has no steps any more**: opening a receiving batch shows
  ONE list (no stage chips) of every product the planner sent to
  receive - preview card, "Expected N from the planner", tagged/received
  tracker, and per-card buttons that need no expanding: [Reprint labels]
  (small window asking only how many; count untouched; labels carry the
  item's HOME bin), [Update count] (− / + around a number box, planner's
  original number kept in view; drives how many labels the product
  needs), [Cancel] (unfocus). Selecting a card shows the progress bar
  (green tagged / amber printed-not-tagged) with "X printed · Y tagged ·
  Z left to scan"; when the received count outruns printed labels the
  card flags it and offers to print exactly the missing ones. Empty
  list says "No products set to receive". The manual "Start receiving"
  button is GONE - receiving batches come only from the planner's
  "Print labels" save.
- **Entirely planner-driven closure (Nick's answer #1)**: no Finish
  button and no bin-check filing - the shipment closes ITSELF when
  every received box is tagged (checked after each pair, count update
  and problem fix; `_maybe_close_receiving`). Flagged informational
  rows (non-taggable) never block; an unfixed unknown row does (its
  boxes are real untagged stock); lowering a count to what's tagged
  closes too. History's receiving-completed event derives from the
  closed batch as before. The C72 keeps its normal pair flow (his
  answer #3: LINK is fine).
- **Link to product on flagged cards (answer #2)**: an unknown planner
  code gets a 🔗 button right on the card - alias (Shopify untouched,
  unlinkable in History) + in-place resolve; the row rejoins the
  shipment (merging into an existing row when the product already has
  one, planner counts folded), and its labels queue automatically
  (`_queue_receiving_labels_after_fix`; resolve preserves the
  planner's expected_qty against the Shopify-on-hand overwrite).
- **Queue tab Stop printing (answer #4)**: red-live only while labels
  are pending/printing, grayed when idle. POST /api/print-jobs/stop
  cancels everything still waiting (the agent claims ≤5 per 3s burst,
  so at most that many still come out - no agent update needed), logs
  a "Stopped Printing" History event, and canceled labels reprint from
  the normal flows. Explicitly NOT wired to count-lowering - that
  never cancels anything (his answer).
- **Problems live on the batch now**: /api/receiving/prints keeps
  failures as flagged rows (skip_reason) instead of only naming them in
  the response - unknown SKU/barcode, non-taggable, and a catch-all for
  unforeseen per-item errors (one bad line can't eat the save). Flagged
  cards show ⚠ and explain themselves when selected; they never print
  (server-guarded). No-bin items flag client-side off the missing bin.
  expected_qty on receiving rows = the planner's cumulative number
  (kept apart from qty_scanned = received count).
- **Assigned Tag undo chain (History)**: tag-assigned events (grouped
  sweeps AND singles) now carry Undo → POST /api/tags/release moves the
  assignments into the new rfid_released_tags snapshot table (every
  field kept) and logs per-EPC "tag-released" rows (shared timestamp →
  one foldable event). That event's Undo → /api/tags/reapply restores
  each assignment EXACTLY (original assigned_at/by, case units, batch,
  suspect flag - so counts and sold-window baselines don't move), logs
  "tag-reapplied" (also undoable). Endless manual loop by design. A
  consumed release / re-released re-apply drops its Undo (state-checked
  per render). Guards: SKU scoping, never steals an EPC re-claimed by
  another product. EVENT_META: Released Tag (#8a4b0e), Re-applied Tag
  (#0c5132). Suites: test_tagchain.py (19), test_recvbridge.py grew to
  21.

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
- **Loose-job grouping + ranges (same day, follow-up)**: Scan Station
  prints stamp a `print_session` token per product LOAD
  (rfid_print_jobs.print_session, ALTER run 2026-08-25), so everything
  printed before the next barcode reset shares it. The Queue groups a
  product's loose jobs into one main group ("Baader × 14 · #5 - #18")
  with one sub-group per session run ("#15 - #18 · 4 label(s)");
  pre-column jobs fall back to adjacency runs; single jobs stay flat.
  Batch and receiving group headers show job-id ranges too, and bin
  cells no longer wrap mid-name ("F2-\\n3").
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
- **✅ DONE 2026-09-29 — the em-dash sweep, app-wide.** The web was
  already clean from the 2026-09-14 pass; this one covered the C72
  (197 strings, 4.23) and the server's API/History messages (75 in
  main.py, shopify.py, orders_sync.py, print_agent.py). Only text
  inside string literals changed - comments and docstrings keep
  theirs, and lone "—" placeholders (empty cells, "SKU —") stay on
  purpose because code compares against them. dev/tests/
  test_emdash.py fails the suite if one returns (proven by planting
  one). Print-agent copy rides its next real version bump.
- **✅ DONE 2026-09-29 — the "(s)" plural sweep, app-wide** (web,
  server messages, print agent, C72 4.22): ~500 sites now read "1 tag"
  / "2 tags" with verbs agreeing ("1 bin is / 2 bins are"). Helpers:
  countNoun() in app.js, _count() in main.py / oneleft.py /
  print_agent.py, plural() in MainActivity.java. dev/tests/
  test_plurals.py fails the build if a quoted "word(s)" creeps back.
  Chip rule stands: capitalized "Label: value" ("Drift: 9"). The
  print agent's copy rides its next real version bump (AGENT_VERSION
  unchanged, so the warehouse PC isn't forced to update for text).

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
7. ~~**TEAR OUT multi-box SETS and redo them semi-manually**~~
   ✅ Done 2026-09-15 (C72 4.04) - see "Multi-box redo: marks at
   collect, sets at verify" above.
8. **In-app bug reporter** (captured 2026-09-16, Nick's words, NOT
   scoped - do not build unasked). A bug-icon button on every C72
   page - best spot: top-right of the drawer's title card ("TC RFID
   Sweep") - that snaps a screenshot of the current screen (when
   tapped from inside a window/dialog, likely just that window).
   Tapping it offers categories like "Aesthetic", "Bad Feature",
   "Missing Feature" (plus others) and an optional message. The same
   bug icon appears on the web terminal. Reports land in the Review
   tab under their own tag/category.
9. **Review tab type filter becomes a checklist** (captured
   2026-09-16). The category dropdown should be a multi-select
   checklist instead of a single pick: each type toggles on/off with
   a green checkmark, and the list shows/hides tasks per toggle.
10. ~~**Truthful printed-state from the printer itself**~~ ✅ SHIPPED
   2026-09-23 as part of print agent v6 (see the v6 section up top):
   the agent queries ~HS + the odometer around every label, faults
   hold the queue and show on the Queue tab pill, and "done" means
   the printer's own counter moved.

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

- **Gyro for the locate radar** (Nick, 2026-09-01 — parked, revisit
  before any purchase). The C72 has no gyro/magnetometer (light,
  proximity and gravity sensors only), which is why RADAR was
  retired: no way to know which way the gun points. Best candidate:
  WitMotion BWT901CL (~$40) — cased Bluetooth 9-axis IMU with its own
  battery, strapped RIGIDLY to the gun body (rigid mounting is the
  one hard requirement); the C72's built-in Bluetooth is the
  receiver, USB port stays free. BT lag (~20-40ms at up to 200Hz) is
  negligible for bucketing RSSI by heading over a multi-second arc
  sweep. Use gyro-relative heading only — NEVER the magnetometer
  (steel racking). No finished "sensor-in-a-USB-dongle" product
  exists (mouse dongles are just radios); the DIY port-dongle route
  is a Seeed XIAO nRF52840 Sense + custom firmware, but it blocks
  charging and props the port flap open. Build plan when bought:
  settings pairing row, hold-level trigger calibration, LOCATE radar
  mode that buckets RSSI by bearing. Nick's open concerns: BT delay
  and mounting (both assessed minor, recorded above).
- **Physical map of the warehouse** (Nick, 2026-09-01: "I've always
  wanted a physical map of all our stuff") — map the racks/bins as a
  drawn layout the apps can render (audit walk order, locate hints,
  the bin arrows' TRUE walking order instead of alphabetical). Map it
  out with Nick another time.
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

- **Unavailable/damaged/repair-section stock: batch collect takes the
  SHELF quantity as truth for now** (Nick, 2026-09-08, the ASI432MM
  missing-piece case). A unit set aside off the shelf is NOT chased
  during collect; the numbers reconcile when the product re-enters
  available stock (bring-back = unavailable-move direction "out").
  The Inventory Check's unavailable-agreement rule and the audit's
  "matches its unavailable stock" line are the guard rails meanwhile.
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
