# RFID Inventory System — Roadmap

Source of truth for project status. Updated by Claude each working session.
Last updated: 2026-08-17.

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
