// Scan station logic.
//
// Two-scan loop:
//   barcode field (active) --scan--> lookup --> product shows -->
//   rfid field (active) --scan--> save --> back to barcode field.
//
// Scanners in keyboard/HID mode type the value and press Enter, so each
// field just listens for Enter. No hardware driver involved.

const el = {
  barcode: document.getElementById("barcode"),
  rfid: document.getElementById("rfid"),
  stepBarcode: document.getElementById("step-barcode"),
  stepRfid: document.getElementById("step-rfid"),
  productCard: document.getElementById("product-card"),
  pTitle: document.getElementById("p-title"),
  pSku: document.getElementById("p-sku"),
  pBarcode: document.getElementById("p-barcode"),
  pBin: document.getElementById("p-bin"),
  pOnhand: document.getElementById("p-onhand"),
  pTagCount: document.getElementById("p-tagcount"),
  tagsPanel: document.getElementById("tags-panel"),
  tagsList: document.getElementById("tags-list"),
  printPanel: document.getElementById("print-panel"),
  printQty: document.getElementById("print-qty"),
  printBtn: document.getElementById("print-btn"),
  printStatus: document.getElementById("print-status"),
  result: document.getElementById("result"),
  resultRfid: document.getElementById("result-rfid"),
  reset: document.getElementById("reset"),
  recentList: document.getElementById("recent-list"),
  search: document.getElementById("search"),
  flow: document.getElementById("tab-scan"),
  linkbox: document.getElementById("linkbox"),
  linkboxTitle: document.getElementById("linkbox-title"),
  linkboxText: document.getElementById("linkbox-text"),
  linkboxForm: document.getElementById("linkbox-form"),
  aliasTarget: document.getElementById("alias-target"),
  aliasCheck: document.getElementById("alias-check"),
  aliasPreview: document.getElementById("alias-preview"),
  aliasImg: document.getElementById("alias-img"),
  aliasPtitle: document.getElementById("alias-ptitle"),
  aliasPsku: document.getElementById("alias-psku"),
  aliasPbarcode: document.getElementById("alias-pbarcode"),
  aliasPbin: document.getElementById("alias-pbin"),
  aliasAccept: document.getElementById("alias-accept"),
  aliasUnlink: document.getElementById("alias-unlink"),
  aliasCancel: document.getElementById("alias-cancel"),
  replaceSection: document.getElementById("replace-section"),
  replaceLabel: document.getElementById("replace-label"),
  replaceModeBarcode: document.getElementById("replace-mode-barcode"),
  replaceModeSku: document.getElementById("replace-mode-sku"),
  replaceInput: document.getElementById("replace-input"),
  replaceAck: document.getElementById("replace-ack"),
  replaceAckText: document.getElementById("replace-ack-text"),
  replaceGo: document.getElementById("replace-go"),
  serialPanel: document.getElementById("serial-panel"),
  serialNote: document.getElementById("serial-note"),
  serialSheetName: document.getElementById("serial-sheet-name"),
  serialLabelInput: document.getElementById("serial-label-input"),
  serialLabelSave: document.getElementById("serial-label-save"),
  prefixNote: document.getElementById("prefix-note"),
  prefixReco: document.getElementById("prefix-reco"),
  prefixRecoText: document.getElementById("prefix-reco-text"),
  prefixRecoApply: document.getElementById("prefix-reco-apply"),
  autoPrint: document.getElementById("auto-print"),
  autoPrintSerial: document.getElementById("auto-print-serial"),
  showLabelPreview: document.getElementById("show-label-preview"),
  autoReset: document.getElementById("auto-reset"),
  requireBin: document.getElementById("require-bin"),
  warnNobin: document.getElementById("warn-nobin"),
  printNobin: document.getElementById("print-nobin"),
  prefixSection: document.getElementById("prefix-section"),
  prefixInput: document.getElementById("prefix-input"),
  prefixSave: document.getElementById("prefix-save"),
  binInput: document.getElementById("bin-input"),
  productEdit: document.getElementById("product-edit"),
  productCardJump: document.getElementById("product-card-jump"),
  setbox: document.getElementById("setbox"),
  setScanInput: document.getElementById("set-scan-input"),
  setboxChoose: document.getElementById("setbox-choose"),
  setCandidates: document.getElementById("set-candidates"),
  setSkuInput: document.getElementById("set-sku-input"),
  setConfirm: document.getElementById("set-confirm"),
  setSingle: document.getElementById("set-single"),
  setCancel: document.getElementById("set-cancel"),
};

// --- Click-to-edit bin: chip -> empty text box -> Enter saves to Shopify ---
el.pBin.addEventListener("click", () => {
  if (!pendingProduct) return;
  el.pBin.hidden = true;
  el.binInput.value = "";
  el.binInput.hidden = false;
  el.binInput.focus();
});

function closeBinEditor() {
  el.binInput.hidden = true;
  el.pBin.hidden = false;
}

el.binInput.addEventListener("keydown", async (event) => {
  if (event.key === "Escape") {
    event.stopPropagation(); // don't let the global Esc reset the station
    closeBinEditor();
    return;
  }
  if (event.key !== "Enter") return;
  const bin = el.binInput.value.trim();
  if (!bin || !pendingProduct) return;
  el.binInput.disabled = true;
  try {
    const res = await apiFetch("/api/bin-updates", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target: pendingProduct.sku || pendingProduct.barcode,
        bin,
        changed_by: operatorEl.value || null,
      }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setResult(body.detail || "Bin update failed.", "err");
      return;
    }
    pendingProduct.bin_location = bin;
    el.pBin.textContent = bin;
    updateNoBinWarn(pendingProduct);
    setResult(`Bin set to ${bin} (saved to Shopify).`, "ok");
    closeBinEditor();
    el.rfid.focus();
    // A held auto-print (missing bin) can proceed now.
    maybeAutoPrint();
  } catch (err) {
    setResult("Network error during the bin update.", "err");
  } finally {
    el.binInput.disabled = false;
  }
});

el.binInput.addEventListener("blur", () => {
  if (!el.binInput.disabled) closeBinEditor();
});

// Station settings (the ⚙ menu): all persisted per device.
function bindSetting(input, key, defaultOn = false) {
  const raw = localStorage.getItem(key);
  input.checked = raw === null ? defaultOn : raw === "1";
  input.addEventListener("change", () => {
    localStorage.setItem(key, input.checked ? "1" : "0");
  });
}
bindSetting(el.autoPrint, "autoPrint");
// Sub-setting of auto-print: the Astronomik serial flow. Defaults ON so
// stations that had the old Astronomik-only auto-print keep it.
bindSetting(el.autoPrintSerial, "autoPrintSerial", true);
function syncAutoPrintSub() {
  el.autoPrintSerial.disabled = !el.autoPrint.checked;
  document
    .getElementById("auto-print-serial-item")
    .classList.toggle("settings__item--off", !el.autoPrint.checked);
}
el.autoPrint.addEventListener("change", syncAutoPrintSub);
syncAutoPrintSub();
bindSetting(el.autoReset, "autoReset");
bindSetting(el.requireBin, "requireBinForAutoPrint");
// The no-bin print warning starts ON — a silent bin-less label is the
// kind of surprise you only notice at the shelf.
bindSetting(el.warnNobin, "warnNoBinOnPrint", true);
el.warnNobin.addEventListener("change", () => {
  if (lastShownProduct) updateNoBinWarn(lastShownProduct);
});
// Label preview on the product card — re-renders live when toggled.
bindSetting(el.showLabelPreview, "showLabelPreview");
el.showLabelPreview.addEventListener("change", () => {
  renderCardLabelPreview(pendingProduct, lastTagData);
});
// (Print-related items are hidden after printingEnabled is computed below.)

// Printing UI shows on printer stations, or everywhere when the server flag
// ALLOW_REMOTE_PRINT is on. Station status is sticky per device: visiting
// once with ?printer=1 marks it permanently (?printer=0 unmarks), so the
// bare URL keeps working afterwards.
{
  const p = new URLSearchParams(location.search).get("printer");
  if (p === "0") localStorage.removeItem("printerStation");
  else if (p !== null) localStorage.setItem("printerStation", "1");
}
const printingEnabled =
  document.body.dataset.remotePrint === "on" ||
  localStorage.getItem("printerStation") === "1";
document.getElementById("auto-print-item").hidden = !printingEnabled;
document.getElementById("auto-print-serial-item").hidden = !printingEnabled;
document.getElementById("require-bin-item").hidden = !printingEnabled;
document.getElementById("warn-nobin-item").hidden = !printingEnabled;

// --- Access + identity ------------------------------------------------------
// Station key: captured once from a ?key=... link, remembered, then sent as
// a header on every API call. Inside Shopify admin, App Bridge injects its
// own Authorization header instead, so both paths work through apiFetch.
const urlParams = new URLSearchParams(location.search);
if (urlParams.get("key")) {
  localStorage.setItem("stationKey", urlParams.get("key"));
}
const stationKey = localStorage.getItem("stationKey");

function apiFetch(url, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (stationKey) headers["X-Station-Key"] = stationKey;
  return fetch(url, { ...opts, headers });
}

// Operator: who is physically using the station. Persisted per device and
// stamped onto every assignment and print job.
const operatorEl = document.getElementById("operator");
operatorEl.value = localStorage.getItem("operator") || "";
operatorEl.addEventListener("change", () => {
  localStorage.setItem("operator", operatorEl.value);
});

function requireOperator() {
  if (operatorEl.value) return operatorEl.value;
  setResult("Pick who's scanning (top right) first.", "err");
  operatorEl.focus();
  return null;
}

// === Refresh buttons =========================================================
// One parent behavior every refresh-ish button on the site shares
// (refreshify keeps each button's own name, size and styling):
//  - durations are logged server-side (manual AND automatic runs), so the
//    button can promise "Estimated N seconds" and mean it;
//  - while running, the label counts down and the button fills left to
//    right with the site's dim green;
//  - a server-side auto refresh already underway when the page loads (or
//    finishing as the user watches) shows the same animation, picked up
//    at the right fill level rather than starting from zero.
const RF_STATS = {}; // kind -> recent median ms (server, blended locally)
const RF_BUTTONS = {}; // kind -> {btn, run}
const RF_DEFAULT_ETA = 4000;

function rfEta(kind) {
  return RF_STATS[kind] || RF_DEFAULT_ETA;
}

function refreshify(btnId, kind, run) {
  const btn = document.getElementById(btnId);
  if (!btn) return;
  RF_BUTTONS[kind] = { btn, run };
  btn.classList.add("rfbtn");
  btn.addEventListener("click", () => runRefresh(kind, "manual"));
}

function rfPaint(btn, orig, startedAt, eta) {
  const elapsed = Date.now() - startedAt;
  const pct = Math.min(96, (elapsed / eta) * 100);
  btn.style.setProperty("--rf-fill", pct.toFixed(1) + "%");
  const left = Math.ceil(Math.max(0, eta - elapsed) / 1000);
  btn.textContent = left > 0 ? `${orig} · ~${left}s` : `${orig} · almost…`;
}

function rfFinish(btn, orig, resultText) {
  btn.style.setProperty("--rf-fill", "100%");
  setTimeout(() => {
    btn.classList.remove("rfbtn--run");
    btn.style.removeProperty("--rf-fill");
    btn.disabled = false;
    delete btn.dataset.rfRunning;
    const custom = typeof resultText === "string" && resultText;
    btn.textContent = custom ? resultText : orig;
    // A run's outcome text ("Cleared 3 ✓") shows briefly, then the
    // button goes back to being itself.
    if (custom) setTimeout(() => (btn.textContent = orig), 2500);
  }, 350);
}

async function runRefresh(kind, source, startedAt = Date.now()) {
  const entry = RF_BUTTONS[kind];
  if (!entry || entry.btn.dataset.rfRunning) return;
  const { btn, run } = entry;
  btn.dataset.rfRunning = "1";
  btn.disabled = true;
  btn.classList.add("rfbtn--run");
  const orig = btn.dataset.rfLabel || (btn.dataset.rfLabel = btn.textContent);
  const eta = rfEta(kind);
  btn.title = `Estimated ${Math.max(1, Math.round(eta / 1000))} seconds`;
  rfPaint(btn, orig, startedAt, eta);
  const timer = setInterval(() => rfPaint(btn, orig, startedAt, eta), 250);
  let resultText = null;
  try {
    resultText = await run();
  } finally {
    clearInterval(timer);
    const ms = Date.now() - startedAt;
    // Blend locally so the very next run is already smarter, and feed the
    // shared log (fire and forget).
    RF_STATS[kind] = Math.max(500, Math.round((eta + ms) / 2));
    rfFinish(btn, orig, resultText);
    postJson("/api/refresh-log", { kind, source, ms }).catch(() => {});
  }
}

// A refresh the SERVER is running (the daily order sync, etc.): animate
// from its real start time and let the stats endpoint tell us when it's
// done — the server logs its own duration.
async function rfAnimateServerAuto(kind, startedAt) {
  const entry = RF_BUTTONS[kind];
  if (!entry || entry.btn.dataset.rfRunning) return;
  const { btn } = entry;
  btn.dataset.rfRunning = "1";
  btn.disabled = true;
  btn.classList.add("rfbtn--run");
  const orig = btn.dataset.rfLabel || (btn.dataset.rfLabel = btn.textContent);
  const eta = rfEta(kind);
  const timer = setInterval(() => rfPaint(btn, orig, startedAt, eta), 250);
  const poll = setInterval(async () => {
    try {
      const data = await apiJson("/api/refresh-stats");
      if (!(data.running || {})[kind]) {
        clearInterval(timer);
        clearInterval(poll);
        Object.assign(RF_STATS, data.stats || {});
        rfFinish(btn, orig, null);
      }
    } catch {
      /* transient — keep polling */
    }
  }, 5000);
  rfPaint(btn, orig, startedAt, eta);
}

async function loadRefreshStats() {
  try {
    const data = await apiJson("/api/refresh-stats");
    Object.assign(RF_STATS, data.stats || {});
    Object.entries(data.running || {}).forEach(([kind, startedIso]) => {
      const t = Date.parse(startedIso + "Z");
      rfAnimateServerAuto(kind, isNaN(t) ? Date.now() : t);
    });
  } catch {
    /* stats are decoration — buttons still work without them */
  }
}

// --- Event chips -------------------------------------------------------------
// Every event/category tag renders as readable text in a coloured chip.
// Colours live in CSS variables (--ev-<type>) set from defaults merged
// with the operator's own picks (Settings → Event colours, stored in this
// browser) — editing one repaints every chip on the page instantly.
const EVENT_META = {
  "tag-assigned": ["Assigned Tag", "#29845a"],
  "tag-unlinked": ["Unlinked Tag", "#d72c0d"],
  // The Assigned Tag undo chain: release keeps a full snapshot, so its
  // own Undo re-applies the tags exactly - and around it goes, manually,
  // as many times as anyone cares to press (Nick, 2026-08-25).
  "tag-released": ["Released Tag", "#8a4b0e"],
  "tag-reapplied": ["Re-applied Tag", "#0c5132"],
  "barcode-linked": ["Linked Barcode", "#6f42c1"],
  "barcode-replaced": ["Replaced Barcode", "#b98900"],
  "sku-updated": ["Updated SKU", "#b98900"],
  "bin-updated": ["Updated Bin", "#0e7a8a"],
  "vendor-updated": ["Updated Vendor", "#b98900"],
  "manual-recount": ["Manual Recount", "#8a6116"],
  "rfid-flag-changed": ["RFID Flag", "#d72c0d"],
  "non-taggable": ["Non-taggable", "#8a6116"],
  "unlabelable-box": ["Un-labelable Box", "#8a6116"],
  "box-set": ["Multi-box Set", "#0b6e99"],
  "box-renumbered": ["Box Renumbered", "#0b6e99"],
  "alias-unlinked": ["Link Removed", "#8a6116"],
  "batch-reprinted": ["Batch Reprint", "#5c5f62"],
  "label-edited": ["Label Edited", "#5e548e"],
  "openbox-return": ["Open-Box Return", "#b06a2e"],
  "not-our-tag": ["Not Our Tag", "#6d3f5b"],
  "draft-created": ["Draft Created", "#2f6f44"],
  "condition-set": ["Box Condition", "#4a7a6a"],
  "printing-stopped": ["Stopped Printing", "#d72c0d"],
  "printing-resumed": ["Resumed Printing", "#116329"],
  "strip-mode": ["Strip Mode", "#5b5b8a"],
  "case-declared": ["Sealed Cases", "#3f5b6d"],
  "return-processed": ["Return Processed", "#2f5f6f"],
  "on-hand-updated": ["Raised On-hand", "#0c5132"],
  "on-hand-undone": ["Undid On-hand", "#6d7175"],
  "on-hand-lowered": ["Lowered On-hand", "#8a4b0e"],
  "on-hand-lower-undone": ["Undid Lowering", "#6d7175"],
  "label-queued": ["Queued Label", "#4a86d8"],
  "label-printing": ["Printing Label", "#005bd3"],
  "label-printed": ["Printed Label", "#005bd3"],
  "label-failed": ["Label Failed", "#d72c0d"],
  "label-canceled": ["Canceled Label", "#6d7175"],
  "marked-bundle": ["Marked Bundle", "#6f42c1"],
  "marked-multi-box": ["Marked Multi-box", "#6f42c1"],
  "dropped-from-rfid": ["Dropped From RFID", "#d72c0d"],
  "batch-started": ["Started Batch", "#3f51b5"],
  "batch-verified": ["Verified Batch", "#3f51b5"],
  "batch-completed": ["Completed Batch", "#29845a"],
  "batch-abandoned": ["Abandoned Batch", "#6d7175"],
  "batch-counted": ["Batch Counted", "#3f51b5"],
  "side-trip-started": ["Started Side Trip", "#0e7a8a"],
  "side-trip-verified": ["Verified Side Trip", "#0e7a8a"],
  "side-trip-completed": ["Completed Side Trip", "#0e7a8a"],
  "side-trip-abandoned": ["Abandoned Side Trip", "#6d7175"],
  "bin-marked-tagged": ["Bin Marked Tagged", "#8a6116"],
  "receiving-started": ["Started Receiving", "#7a5c0e"],
  "receiving-completed": ["Completed Receiving", "#29845a"],
  "receiving-abandoned": ["Abandoned Receiving", "#6d7175"],
  "bin-check": ["Bin Check", "#7a5c0e"],
  "already-tagged-set": ["Already-tagged Count", "#6f42c1"],
  "review-opened": ["Opened Review", "#8a6116"],
  "review-resolved": ["Resolved Review", "#29845a"],
  "review-dismissed": ["Dismissed Review", "#6d7175"],
  // System closures (a newer count agreed, the arithmetic caught up):
  // never a person's click, so they wear their own tag.
  "review-autoclosed": ["Auto-Resolved", "#57748c"],
  "labels-not-printed": ["Labels Not Printed", "#c05717"],
  "unprinted-sold": ["Unlabelled Sold", "#a8570f"],
  "label-unpaired": ["Label Not Paired", "#d72c0d"],
  "stock-not-updated": ["Stock Not Updated", "#8250df"],
  "inventory-check": ["Inventory Check", "#8a6116"],
  "pairing-incomplete": ["Pairing Incomplete", "#d72c0d"],
  "unresolved-barcode": ["Unresolved Barcode", "#d72c0d"],
  "could-not-scan": ["Could Not Scan", "#8a6116"],
  "bin-mismatch": ["Mismatched Bins", "#0e7a8a"],
  "tags-rebinned": ["Tags Re-binned", "#0e7a8a"],
  "bundle-contents-set": ["Bundle Contents", "#6f42c1"],
  "bundles-pulled": ["Bundles Pulled", "#6f42c1"],
  "locate-list": ["Locate List", "#5561c9"],
  "locate-paired": ["Locate Assigned Tag", "#2f9e6e"],
  "receiving-dismissed": ["Sold Before Label", "#5c5f62"],
  "unpaired-ignored": ["Unpaired Write-off", "#7a7d80"],
  "unpaired-unignored": ["Write-off Undone", "#5561c9"],
  "packed-retired": ["Packed Orders Retired", "#2f9e6e"],
  "packed-unretired": ["Packed Retire Undone", "#5561c9"],
  "boxify-import": ["Boxify Import", "#1f5f8b"],
  "product-refreshed": ["Product Refreshed", "#1f5f8b"],
  oneleft: ["1-left Check", "#b07d00"],
  "audit-session": ["Audit Session", "#0e7a8a"],
  "bin-audited": ["Audit Done", "#0b6e99"],
  "barcode-clash": ["Barcode Clash", "#8e1f0b"],
  "sku-clash": ["SKU Clash", "#8e1f0b"],
  "unbundled": ["Un-Bundled", "#6f42c1"],
  multibox: ["Multi-box", "#0b6e99"],
  "mislabel-flag": ["Mis-label Flag", "#b07d00"],
  "unavailable-move": ["Set Aside", "#6b21a8"],
  "ledger-cleared": ["Ledger Cleared", "#4338ca"],
  sweep: ["Sweep", "#0e7a8a"],
  // The sold system wears indigo/purple on purpose: product/on-hand
  // arithmetic, visually distinct from the amber human-count families.
  "order-sold": ["Order Sold", "#5c6ac4"],
  "tag-sold": ["Tag Sold", "#4053b8"],
  "tag-retired": ["Tag Retired", "#7a5ea8"],
  "tag-unretired": ["Tag Restored", "#3f8f6b"],
  "backorder-noted": ["Backorder Noted", "#146c60"],
  "backorder-cleared": ["Backorder Cleared", "#5c5f62"],
  "tag-onhand-mismatch": ["Tags ≠ On-hand", "#8e44ad"],
  "shopify-bin-read": ["Read From Shopify", "#1f5f8b"],
  "scan-note": ["Scan Note", "#8a6116"],
  "duplicate-product": ["Possible Duplicate", "#c9367c"],
  "product-merged": ["Products Merged", "#6f42c1"],
};

// Multi-tag events render their EPC list behind an expander — the cell
// reads "4× EPC tags", the tags are one click away. Everything that
// shows a product's assigned tags (product history, review timelines)
// goes through this so a sweep is never a mystery event (Nick's note).
function epcsDetailCell(e) {
  if (!e.epcs || !e.epcs.length) return escapeHtml(e.detail || "");
  // The prefix duplicates what the expander summary says; keep only the
  // trailing facts (bin, suspects).
  const rest = String(e.detail || "")
    .replace(/^\d+\s*×\s*RFID tag(\s*\(sweep\))?/, "")
    .replace(/^\s*·\s*/, "");
  return (
    `<details class="epc-exp"><summary>${e.epcs.length}× EPC tags</summary>` +
    `<div class="hist-epclist">${e.epcs
      .map((x) => `<div class="mono">${escapeHtml(x || "?")}</div>`)
      .join("")}</div></details>` +
    (rest ? ` <span>${escapeHtml(rest)}</span>` : "")
  );
}

function evLabel(type) {
  const m = EVENT_META[type];
  if (m) return m[0];
  // Unknown types still read as words, never as raw tags.
  return String(type || "")
    .split("-")
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}

function evVarName(type) {
  return "--ev-" + String(type).replace(/[^a-z0-9]+/gi, "-").toLowerCase();
}

function eventColorOverrides() {
  try {
    return JSON.parse(localStorage.getItem("eventColors") || "{}");
  } catch {
    return {};
  }
}

function applyEventColors() {
  const overrides = eventColorOverrides();
  const root = document.documentElement.style;
  Object.keys(EVENT_META).forEach((type) => {
    root.setProperty(
      evVarName(type),
      overrides[type] || EVENT_META[type][1]
    );
  });
}
applyEventColors();

function evChip(type, extraTitle) {
  const known = !!EVENT_META[type];
  const color = known
    ? `var(${evVarName(type)})`
    : "var(--chip-ink)";
  const bg = known
    ? `color-mix(in srgb, var(${evVarName(type)}) 15%, transparent)`
    : "var(--chip-bg)";
  return (
    `<span class="evtype" style="color:${color};background:${bg}"` +
    (extraTitle ? ` title="${escapeHtml(extraTitle)}"` : "") +
    `>${escapeHtml(evLabel(type))}</span>`
  );
}

// Settings → Event colours: a picker + hex box + live preview per
// event. Pages of 10 with a search filter (Nick, 2026-09-09), an ✕ on
// any customized colour to reset just that one, and a confirm on the
// reset-all - ~90 types in one endless scroll was unusable.
const EVCOLOR_PAGE_SIZE = 10;
let evColorPage = 0;

function renderEvColorList() {
  const wrap = document.getElementById("evcolor-list");
  const pager = document.getElementById("evcolor-pager");
  const q = (document.getElementById("evcolor-search").value || "")
    .trim()
    .toLowerCase();
  const overrides = eventColorOverrides();
  // Page 1 = the ten most-used events, in importance order (Nick,
  // 2026-09-24); everything else follows alphabetically.
  const COMMON = [
    "order-sold", "batch-counted", "review-opened", "review-resolved",
    "label-printed", "tag-assigned", "condition-set", "bin-updated",
    "on-hand-updated", "on-hand-lowered",
  ];
  const rest = Object.keys(EVENT_META)
    .filter((t) => !COMMON.includes(t))
    .sort((a, b) => evLabel(a).localeCompare(evLabel(b)));
  const types = COMMON.concat(rest).filter(
    (t) =>
      !q ||
      evLabel(t).toLowerCase().includes(q) ||
      t.toLowerCase().includes(q)
  );
  const pages = Math.max(1, Math.ceil(types.length / EVCOLOR_PAGE_SIZE));
  if (evColorPage >= pages) evColorPage = pages - 1;
  if (evColorPage < 0) evColorPage = 0;
  wrap.innerHTML = "";
  if (!types.length) {
    wrap.innerHTML =
      '<p class="linkbox__text">No event names match that.</p>';
  } else if (!q) {
    const cap = document.createElement("div");
    cap.className = "evcolor-cap";
    cap.textContent = evColorPage === 0
      ? "Most used" : "Everything else, A to Z";
    wrap.append(cap);
  }
  types
    .slice(
      evColorPage * EVCOLOR_PAGE_SIZE,
      (evColorPage + 1) * EVCOLOR_PAGE_SIZE
    )
    .forEach((type) => {
      const current = overrides[type] || EVENT_META[type][1];
      const custom = !!overrides[type];
      const row = document.createElement("div");
      row.className = "evcolor-row";
      const shownByDefault = !histHiddenDefaults().has(type);
      row.innerHTML = `
        <button class="reset evcolor-vis${shownByDefault ? " on" : ""}" type="button"
          title="Shown in product history by default - click to hide it there">${shownByDefault ? "\u2713" : ""}</button>
        <span class="evcolor-preview">${evChip(type)}</span>
        <button class="reset evcolor-clear" type="button"
          title="Reset this colour to its default" ${custom ? "" : "hidden"}>✕</button>
        <input type="color" value="${current}" aria-label="colour for ${escapeHtml(evLabel(type))}" />
        <input type="text" class="linkbox__input evcolor-hex" value="${current}" maxlength="7" spellcheck="false" />`;
      const picker = row.querySelector('input[type="color"]');
      const hex = row.querySelector(".evcolor-hex");
      const clear = row.querySelector(".evcolor-clear");
      const save = (value) => {
        if (!/^#[0-9a-fA-F]{6}$/.test(value)) return;
        const o = eventColorOverrides();
        if (value.toLowerCase() === EVENT_META[type][1].toLowerCase()) {
          delete o[type];
        } else {
          o[type] = value;
        }
        localStorage.setItem("eventColors", JSON.stringify(o));
        clear.hidden = !o[type];
        applyEventColors(); // every chip on the page follows instantly
      };
      picker.addEventListener("input", () => {
        hex.value = picker.value;
        save(picker.value);
      });
      hex.addEventListener("input", () => {
        const v = hex.value.trim();
        if (/^#[0-9a-fA-F]{6}$/.test(v)) {
          picker.value = v;
          save(v);
        }
      });
      clear.addEventListener("click", () => {
        const o = eventColorOverrides();
        delete o[type];
        localStorage.setItem("eventColors", JSON.stringify(o));
        applyEventColors();
        picker.value = EVENT_META[type][1];
        hex.value = EVENT_META[type][1];
        clear.hidden = true;
      });
      row.querySelector(".evcolor-vis").addEventListener("click", () => {
        const h = histHiddenDefaults();
        if (h.has(type)) h.delete(type); else h.add(type);
        localStorage.setItem(HIST_HIDDEN_KEY, JSON.stringify([...h]));
        renderEvColorList();
      });
      wrap.append(row);
    });
  pager.innerHTML = `
    <button class="reset" id="evcolor-prev" type="button"
      ${evColorPage === 0 ? "disabled" : ""}>‹ Prev</button>
    <span class="recent__meta">Page ${evColorPage + 1} of ${pages}</span>
    <button class="reset" id="evcolor-next" type="button"
      ${evColorPage >= pages - 1 ? "disabled" : ""}>Next ›</button>`;
  pager.querySelector("#evcolor-prev").addEventListener("click", () => {
    evColorPage--;
    renderEvColorList();
  });
  pager.querySelector("#evcolor-next").addEventListener("click", () => {
    evColorPage++;
    renderEvColorList();
  });
}

document.getElementById("evcolor-search").addEventListener("input", () => {
  evColorPage = 0;
  renderEvColorList();
});

document.getElementById("evcolor-reset").addEventListener("click", () => {
  const n = Object.keys(eventColorOverrides()).length;
  if (!n) return;
  if (
    !confirm(
      `Reset ${n} customized event colour(s) back to the defaults? ` +
        "This can't be undone."
    )
  )
    return;
  localStorage.removeItem("eventColors");
  applyEventColors();
  renderEvColorList();
});

// Server timestamps are UTC but arrive with no timezone suffix, which
// new Date() reads as LOCAL — every fresh event then sits "in the future"
// for a whole UTC offset (Toronto: 4 h of "just now"). Parse them as the
// UTC they are; strings that already carry a zone pass through untouched.
function tsDate(iso) {
  return new Date(
    /[Zz]$|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z"
  );
}

// "3 days ago" style timestamps for list surfaces (exact time in hover).
function fmtAgo(iso) {
  if (!iso) return "—";
  const ms = Date.now() - tsDate(iso).getTime();
  if (!Number.isFinite(ms)) return "—";
  const mins = Math.floor(ms / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.floor(hours / 24);
  return days === 1 ? "1 day ago" : `${days} days ago`;
}

// --- Tabs -------------------------------------------------------------------
// Same tabs on PC and iPad; each tab loads (or refreshes) its data on entry.
const tabSections = {
  home: [document.getElementById("tab-home")],
  scan: [document.getElementById("tab-scan"), document.getElementById("scan-footer")],
  batch: [document.getElementById("tab-batch")],
  inventory: [document.getElementById("tab-inventory")],
  queue: [document.getElementById("tab-queue")],
  audits: [document.getElementById("tab-audits")],
  history: [document.getElementById("tab-history")],
  settings: [document.getElementById("tab-settings")],
};
const tabLoaders = {
  home: () => loadHome(),
  settings: () => {
    const panel = document.querySelector(".settingspage__colors");
    if (panel && !panel.hidden) renderEvColorList();
  },
  batch: () => enterBatchTab(),
  inventory: () => loadInventory(),
  queue: () => loadQueue(),
  audits: () => loadAudits(),
  history: () => loadHistory(),
};
// Bouncing between tabs mid-scan-session used to refire every tab's full
// load per click. The read-only tabs skip the refetch while their data is
// under 15 s old - their own refresh buttons still force a live pull.
// Batch and Queue always reload (they manage live polling state).
const tabLoadedAt = {};
const TAB_FRESH_MS = 15000;
const FRESHNESS_GATED_TABS = ["inventory", "audits", "history"];
document.querySelectorAll(".tabs__tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tabs__tab").forEach((b) =>
      b.classList.toggle("tabs__tab--active", b === btn)
    );
    const name = btn.dataset.tab;
    Object.entries(tabSections).forEach(([key, els]) =>
      els.forEach((s) => (s.hidden = key !== name))
    );
    stopBatchPrintPoll();
    if (tabLoaders[name]) {
      const fresh =
        FRESHNESS_GATED_TABS.includes(name) &&
        Date.now() - (tabLoadedAt[name] || 0) < TAB_FRESH_MS;
      if (!fresh) {
        tabLoadedAt[name] = Date.now();
        tabLoaders[name]();
      }
    }
    if (name === "scan") el.barcode.focus();
  });
});

// Current product awaiting an RFID tag. Null when we're on step 1.
let pendingProduct = null;

function setResult(message, kind, where = "barcode") {
  // Two status slots — barcode/printer news up top, tag-assignment news by
  // the RFID step — but never both at once.
  const target = where === "rfid" ? el.resultRfid : el.result;
  const other = where === "rfid" ? el.result : el.resultRfid;
  target.textContent = message;
  target.className = "result" + (kind ? ` result--${kind}` : "");
  other.textContent = "";
  other.className = "result";
}

function activate(step) {
  const onBarcode = step === "barcode";
  el.stepBarcode.classList.toggle("step--active", onBarcode);
  el.stepRfid.classList.toggle("step--active", !onBarcode);
  // Step 2 doesn't exist until a barcode scan loads a product — an empty
  // "Scan RFID tag" box with nothing to pair it to only invites mistakes.
  el.stepRfid.hidden = onBarcode;
  el.rfid.disabled = onBarcode;
  el.barcode.disabled = !onBarcode;
  (onBarcode ? el.barcode : el.rfid).focus();
}

function resetStation() {
  pendingProduct = null;
  el.barcode.value = "";
  el.rfid.value = "";
  el.productCard.hidden = true;
  el.tagsPanel.hidden = true;
  el.tagsPanel.open = false;
  el.tagsList.hidden = true;
  el.printPanel.hidden = true;
  el.printStatus.textContent = "";
  el.serialPanel.hidden = true;
  serialLoadedLabel = null;
  closeLinkbox();
  closeSetbox();
  setResult("", null);
  bulkVisitReset();
  activate("barcode");
}

// --- Step 1: barcode -> Shopify lookup -------------------------------------
el.barcode.addEventListener("keydown", async (event) => {
  if (event.key !== "Enter") return;
  const barcode = el.barcode.value.trim();
  if (!barcode) return;
  await stationBarcodeScan(barcode);
});

// Shell-style history in the barcode box: ArrowUp walks the last 10 RAW
// entries (exactly what was typed or wedge-read — never the SKU a lookup
// resolved to), newest first; ArrowDown walks back toward the fresh
// draft. Survives reloads (per device, like the other station settings).
let barcodeHistory = [];
try {
  barcodeHistory =
    JSON.parse(localStorage.getItem("barcodeHistory")) || [];
} catch (err) {
  barcodeHistory = [];
}
let barcodeHistIdx = -1; // -1 = not browsing; 0 = newest entry
let barcodeDraft = "";

function rememberBarcodeEntry(code) {
  const c = (code || "").trim();
  if (!c) return;
  // A repeat moves to the front rather than filling the list with dupes.
  barcodeHistory = [c, ...barcodeHistory.filter((x) => x !== c)].slice(0, 10);
  localStorage.setItem("barcodeHistory", JSON.stringify(barcodeHistory));
  barcodeHistIdx = -1;
}

el.barcode.addEventListener("keydown", (event) => {
  if (event.key === "ArrowUp") {
    if (!barcodeHistory.length) return;
    event.preventDefault();
    if (barcodeHistIdx === -1) barcodeDraft = el.barcode.value;
    barcodeHistIdx = Math.min(barcodeHistIdx + 1, barcodeHistory.length - 1);
    el.barcode.value = barcodeHistory[barcodeHistIdx];
    el.barcode.select();
  } else if (event.key === "ArrowDown") {
    if (barcodeHistIdx === -1) return;
    event.preventDefault();
    barcodeHistIdx -= 1;
    el.barcode.value =
      barcodeHistIdx === -1 ? barcodeDraft : barcodeHistory[barcodeHistIdx];
    if (barcodeHistIdx >= 0) el.barcode.select();
  }
});
// Typing anything by hand ends the browsing session.
el.barcode.addEventListener("input", () => {
  barcodeHistIdx = -1;
});

// Callable form of the barcode-input Enter handler, so C72 LINK relays can
// run the exact same path the wedge scanner does (guards, windows and all).
async function stationBarcodeScan(barcode) {
  // The RAW entry goes into ArrowUp history no matter how it arrived
  // (typed, wedge, or C72 LINK relay) and no matter what it resolves to.
  rememberBarcodeEntry(barcode);
  setResult("Looking up product…", "busy");
  try {
    const res = await apiFetch(
      `/api/products/by-barcode/${encodeURIComponent(barcode)}`
    );
    if (res.status === 404) {
      const body = await res.json().catch(() => ({}));
      const info =
        body.detail && typeof body.detail === "object" ? body.detail : null;
      // A known case code answers the question outright — show what's in
      // the box instead of any "unknown barcode" window.
      const known = await apiFetch(
        `/api/cases/${encodeURIComponent(barcode)}`
      ).catch(() => null);
      if (known && known.ok) {
        showCaseScan(await known.json());
        setResult("That's a box of multiple products.", "ok");
        return;
      }
      // Unknown serial-shaped scans might be one filter of a multi-box
      // set — offer the set flow first (one click bails to the normal
      // unknown-barcode window). Known-prefix problems keep their window.
      if (!info && /^\d{5,12}$/.test(barcode)) {
        openSetbox(barcode);
      } else {
        openLinkbox(barcode, info);
      }
      return;
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setResult(body.detail || "Lookup failed.", "err");
      return;
    }
    const product = await res.json();
    // Mis-label picker (Nick, 2026-09-08): a flagged product with a
    // "might actually be" list asks which product is physically in
    // hand before the station accepts anything.
    if (product.mislabel_options && product.mislabel_options.length > 1) {
      openMislabelPicker(product, async (opt) => {
        if (
          (opt.sku || "").toUpperCase() ===
          (product.sku || "").toUpperCase()
        ) {
          acceptProduct(
            product,
            "Confirmed against the physical box. Scan the RFID tag."
          );
          return;
        }
        setResult(`Loading ${opt.sku}…`, "busy");
        try {
          const chosen = await apiJson(
            `/api/products/by-barcode/${encodeURIComponent(opt.sku)}`
          );
          acceptProduct(
            chosen,
            "Product picked from the mis-label list. Scan the RFID tag."
          );
        } catch (err) {
          setResult(err.message || "Lookup failed.", "err");
        }
      });
      return;
    }
    if (product.alias_warning) {
      openConfirmBox(product);
      return;
    }
    acceptProduct(
      product,
      product.serial_brand
        ? `${product.serial_brand} serial number recognized - the first ` +
          `digits identify the product. Scan the RFID tag.`
        : product.charfold_from
          ? `Matched via broken-character fix (scan said ` +
            `"${product.charfold_from}"). Scan the RFID tag.`
          : "Product found. Scan the RFID tag."
    );
  } catch (err) {
    setResult("Network error during lookup.", "err");
  }
}

// One print session per product LOAD: every print pressed before the
// next barcode reset shares the token, so the Queue tab can group
// "printed 1, then 9, then 4 of the same thing" as one run instead of
// 14 flat rows (Nick, 2026-08-25).
let printSession = null;
function makePrintSession() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}
function newPrintSession() {
  printSession = makePrintSession();
}

function acceptProduct(product, message) {
  pendingProduct = product;
  newPrintSession();
  autoPrintedThisScan = false;
  closeLinkbox();
  showProduct(product);
  showSerialPanel(product);
  setResult(message, "ok");
  activate("rfid");
  // Bulk defaults to OFF for every product; auto-print below still counts
  // into the fresh printed-this-visit ledger.
  bulkVisitReset();
  maybeAutoPrint();
}

// --- Set as Open Box (Nick, 2026-09-15) -------------------------------------
// A sold, RFID-tagged product returned as open box: the card flips to
// the -O twin (found, or drafted on the spot), the open-box label prints
// under the twin (the print-time migration gives it the -O barcode), and
// a return watch opens - sweeps hearing the original's presumed-sold
// tags will ask "is this box the open-box unit?".
let obxProduct = null;
let obxInfo = null;

document
  .getElementById("product-openbox")
  .addEventListener("click", () => {
    if (pendingProduct) openObxWindow(pendingProduct);
  });

async function openObxWindow(p) {
  obxProduct = p;
  obxInfo = null;
  document.getElementById("obx-sku").textContent = p.sku || "?";
  document.getElementById("obx-listing").textContent = "Checking Shopify…";
  document.getElementById("obx-retired").innerHTML = "";
  document.getElementById("obx-msg").textContent = "";
  document.getElementById("obx-oldtag").value = "";
  const watchEl = document.getElementById("obx-watch");
  watchEl.checked = true;
  watchEl.disabled = false;
  document.getElementById("obx-go-print").disabled = true;
  document.getElementById("obx-go").disabled = true;
  document.getElementById("obx-overlay").hidden = false;
  try {
    obxInfo = await apiJson(
      `/api/products/openbox-info/${encodeURIComponent(p.sku)}`
    );
  } catch (err) {
    document.getElementById("obx-listing").textContent = err.message;
    return;
  }
  const L = obxInfo.listing;
  document.getElementById("obx-listing").innerHTML = L
    ? `Found the open-box listing: <b>${escapeHtml(L.product_title)}</b> ` +
      `(<span class="mono">${escapeHtml(L.sku)}</span>, ${escapeHtml((L.status || "?").toLowerCase())}).`
    : obxInfo.listing_error
      ? `Shopify probe failed: ${escapeHtml(obxInfo.listing_error)}`
      : `No <span class="mono">${escapeHtml(obxInfo.openbox_sku)}</span> listing yet - ` +
        `a DRAFT (<b>${escapeHtml(p.product_title || p.sku)} - Open Box</b>) will be ` +
        `created for it. Publish and price it in Shopify when ready.`;
  const host = document.getElementById("obx-retired");
  const rt = obxInfo.retired || [];
  if (rt.length) {
    host.innerHTML =
      `<div class="step__label">Which old tag came back? (optional)</div>` +
      `<label class="obx-tagopt"><input type="radio" name="obx-epc" value="" checked /> Not sure - watch all of this product's sold tags</label>` +
      rt
        .map(
          (r) =>
            `<label class="obx-tagopt"><input type="radio" name="obx-epc" value="${escapeHtml(r.rfid_id)}" /> ` +
            `<span class="mono">…${escapeHtml((r.rfid_id || "").slice(-6))}</span> · retired ${escapeHtml(fmtAgo(r.retired_at))}` +
            `${r.note ? ` · ${escapeHtml(r.note)}` : ""}</label>`
        )
        .join("");
  } else {
    host.innerHTML =
      `<p class="linkbox__text">No presumed-sold tags on record for this ` +
      `product yet. The watch still arms - a tag retired later gets the ` +
      `same prompt when heard.</p>`;
  }
  if ((obxInfo.open_returns || []).length) {
    document.getElementById("obx-msg").textContent =
      `${obxInfo.open_returns.length} return(s) of this product already ` +
      `waiting - filing another is fine for another unit.`;
  }
  document.getElementById("obx-go-print").disabled = false;
  document.getElementById("obx-go").disabled = false;
}

async function obxSubmit(printAfter) {
  const p = obxProduct;
  if (!p || !obxInfo) return;
  const goBtns = [
    document.getElementById("obx-go-print"),
    document.getElementById("obx-go"),
  ];
  goBtns.forEach((b) => (b.disabled = true));
  const picked = document.querySelector('input[name="obx-epc"]:checked');
  // A scanned in-hand tag outranks the retired-list radios: it is
  // unpaired on the spot and the watch stands down.
  const inHand = document.getElementById("obx-oldtag").value.trim();
  try {
    const res = await postJson("/api/openbox-returns", {
      sku: p.sku,
      product_title: p.product_title || null,
      barcode: p.barcode || null,
      bin_location: p.bin_location || null,
      create_draft: !obxInfo.listing,
      watch: document.getElementById("obx-watch").checked,
      epc: inHand || (picked && picked.value ? picked.value : null),
      peel_old: !!inHand,
      created_by: operatorEl.value || null,
    });
    document.getElementById("obx-overlay").hidden = true;
    // Flip the card to the twin - the normal lookup first (full card:
    // bin, image, preview), the create response as the fallback for a
    // second-old draft the search index hasn't caught up with.
    const ob = res.openbox || {};
    let twin = null;
    try {
      twin = await apiJson(
        `/api/products/by-barcode/${encodeURIComponent(ob.sku)}`
      );
    } catch {
      twin = {
        sku: ob.sku,
        product_title: ob.product_title,
        barcode: ob.barcode || null,
        bin_location: p.bin_location || null,
        shopify_variant_id: ob.shopify_variant_id,
        shopify_product_id: ob.shopify_product_id,
      };
    }
    acceptProduct(twin, res.message);
    if (printAfter) {
      el.printQty.value = 1;
      document.getElementById("print-btn").click();
    }
  } catch (err) {
    document.getElementById("obx-msg").textContent = err.message;
    goBtns.forEach((b) => (b.disabled = false));
  }
}

document
  .getElementById("obx-go-print")
  .addEventListener("click", () => obxSubmit(true));
document
  .getElementById("obx-go")
  .addEventListener("click", () => obxSubmit(false));
document.getElementById("obx-cancel").addEventListener("click", () => {
  document.getElementById("obx-overlay").hidden = true;
});
// Scanning the in-hand tag makes the watch pointless - THE tag is
// dealt with. Clearing the box brings the watch back.
document.getElementById("obx-oldtag").addEventListener("input", () => {
  const has = !!document.getElementById("obx-oldtag").value.trim();
  const watchEl = document.getElementById("obx-watch");
  watchEl.checked = !has;
  watchEl.disabled = has;
});
document.getElementById("obx-oldtag").addEventListener("keydown", (e) => {
  if (e.key === "Enter") e.preventDefault(); // wedge's Enter stays put
});

// One label per unit scanned: when auto-print is on, any product that loads
// from a scan prints one label with no button press. Astronomik serials ride
// the sub-setting and additionally need their label name confirmed.
let autoPrintedThisScan = false;

// --- Label fit estimator ----------------------------------------------------
// Mirror of print_agent.build_zpl's layout math (203 dpi, 2.125 in → 431
// dots). ZPL's ^FB never clips: text past the line limit overprints the
// last line — the "wrapped around itself" failure — so estimate the printed
// width of every line and flag anything that can't fit BEFORE it prints.
const LABEL_PW_DOTS = Math.floor(2.125 * 203);

function zplLineChars(fontH, lines = 1) {
  // CF0 glyphs run ~0.56× their height in width.
  return Math.floor(LABEL_PW_DOTS / (fontH * 0.56)) * lines;
}

function labelFitProblems(p, serialName) {
  const problems = [];
  const label = (serialName || "").trim();
  if (label) {
    // The agent steps the font down with length (28/20/16) and hard-cuts
    // at 76 — past that the name prints truncated and crowded.
    const size = label.length <= 26 ? 28 : label.length <= 56 ? 20 : 16;
    if (label.length > 76)
      problems.push(
        `the label name is ${label.length} characters - it gets cut off ` +
          `at 76 and prints crowded`
      );
    else if (label.length > zplLineChars(size, 2))
      problems.push(
        "the label name is too long for two printed lines - they would " +
          "overlap"
      );
  }
  const sku = String(p.sku || "").trim();
  if (sku.length > zplLineChars(30, 1))
    problems.push(
      `the SKU (${sku}) is longer than one printed line - it overlaps itself`
    );
  const bin =
    p.bin_location && p.bin_location !== "No bin assigned"
      ? p.bin_location
      : "";
  if (bin && `BIN: ${bin}`.length > zplLineChars(30, 1))
    problems.push(
      `the bin line (BIN: ${bin}) is longer than one printed line - it ` +
        `overlaps itself`
    );
  return problems;
}

// Red text beside the Print button whenever the loaded product's label
// would print badly — visible before ANY print, manual or auto.
function updateFitWarn(p) {
  const warnEl = document.getElementById("print-fitwarn");
  if (!warnEl) return;
  const problems = p
    ? labelFitProblems(
        p,
        p.serial_prefix ? el.serialLabelInput.value.trim() : null
      )
    : [];
  warnEl.hidden = !problems.length;
  if (problems.length)
    warnEl.textContent = `⚠ Label will print badly: ${problems[0]} - update the text before printing.`;
}

function maybeAutoPrint() {
  if (!pendingProduct) return;
  if (!el.autoPrint.checked) return;
  const isSerial = !!pendingProduct.serial_prefix;
  if (isSerial && !el.autoPrintSerial.checked) return;
  // From here on the operator expects a print — never refuse silently.
  if (!printingEnabled) {
    setResult(
      "Auto-print skipped: this isn't the printer-station page " +
        "(the address needs ?printer=1).",
      "err"
    );
    return;
  }
  if (isSerial && !pendingProduct.serial_label_saved) {
    setResult(
      "Auto-print skipped: the label name isn't confirmed yet - check the " +
        "name below and press Enter to confirm it.",
      "err"
    );
    // Put the operator right where the fix happens.
    el.serialLabelInput.focus();
    el.serialLabelInput.select();
    return;
  }
  const bin = pendingProduct.bin_location;
  if (el.requireBin.checked && (!bin || bin === "No bin assigned")) {
    setResult(
      "Auto-print held: no bin assigned - click the bin chip to set one " +
        "and the label will print.",
      "err"
    );
    return;
  }
  const fit = labelFitProblems(
    pendingProduct,
    isSerial ? el.serialLabelInput.value.trim() : null
  );
  if (fit.length) {
    setResult(
      `Auto-print held: ${fit[0]}. Fix the text, then print manually.`,
      "err"
    );
    return;
  }
  if (autoPrintedThisScan) return;
  queueLabels(1);
}

// --- Serialized-brand label names (Astronomik) ------------------------------
// The panel opens whenever a serial-recognized product loads: shows the
// manufacturer's sheet name and an editable preferred name that prints at
// the top of the label. Saved per serial prefix; survives sheet reloads.
let serialLoadedLabel = null;

function showSerialPanel(p) {
  if (!p || !p.serial_prefix) {
    el.serialPanel.hidden = true;
    serialLoadedLabel = null;
    return;
  }
  if (p.serial_note) {
    el.serialNote.textContent = `⚠ ${p.serial_note}`;
    el.serialNote.hidden = false;
  } else {
    el.serialNote.hidden = true;
  }
  el.serialSheetName.textContent =
    `${p.serial_brand} sheet name: ${p.serial_item_name || "—"}`;
  el.serialLabelInput.value = p.serial_label || "";
  serialLoadedLabel = el.serialLabelInput.value.trim();
  el.serialLabelSave.textContent = "Save name";
  el.serialPanel.hidden = false;
}

async function saveSerialLabel(showFeedback) {
  const name = el.serialLabelInput.value.trim();
  if (!pendingProduct || !pendingProduct.serial_prefix || !name) return;
  // Skip only when this exact name is already confirmed server-side.
  // An unchanged-but-never-saved default still needs saving — printing or
  // hitting Save IS the confirmation that makes auto-print trust it.
  if (name === serialLoadedLabel && pendingProduct.serial_label_saved) {
    if (showFeedback) {
      el.serialLabelSave.textContent = "Saved ✓";
      setTimeout(() => (el.serialLabelSave.textContent = "Save name"), 1500);
    }
    return;
  }
  try {
    const res = await apiFetch(
      `/api/serial-prefixes/${encodeURIComponent(pendingProduct.serial_prefix)}/label`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label_name: name }),
      }
    );
    if (res.ok) {
      serialLoadedLabel = name;
      if (pendingProduct) pendingProduct.serial_label_saved = true;
      if (showFeedback) {
        el.serialLabelSave.textContent = "Saved ✓";
        setTimeout(() => (el.serialLabelSave.textContent = "Save name"), 1500);
        // Freshly confirmed name + auto-print mode = print this unit now.
        maybeAutoPrint();
      }
    } else if (showFeedback) {
      setResult("Could not save the label name.", "err");
    }
  } catch (err) {
    if (showFeedback) setResult("Network error saving the label name.", "err");
  }
}

el.serialLabelSave.addEventListener("click", () => {
  saveSerialLabel(true);
  el.rfid.focus();
});
el.serialLabelInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    saveSerialLabel(true);
    // Same idea as after printing: next action is scanning the tag.
    el.rfid.focus();
  }
});
// The fit warning tracks the name as it's typed — the operator sees the
// red note die the moment the text is short enough.
el.serialLabelInput.addEventListener("input", () =>
  updateFitWarn(lastShownProduct)
);

// --- Case codes -------------------------------------------------------------
// A barcode that isn't a listing at all but the manufacturer's case code:
// "8 x 93581". Scanning one used to come back empty, which is how a box ends
// up in someone's hands with nowhere to put it. The record lives against the
// BARCODE, so the note follows the scan onto every surface instead of each
// tab inventing its own message.
let caseCode = null;      // the code being viewed/defined
let caseProduct = null;   // the product chosen as contents

function closeCasebox() {
  const box = document.getElementById("casebox");
  box.hidden = true;
  caseCode = null;
  caseProduct = null;
  // Docked inside the edit window? Send the element home and report it,
  // so the cancel/done handlers know not to reset the scan flow.
  const host = document.getElementById("edit-casehost");
  if (host && box.parentElement === host) {
    host.hidden = true;
    if (caseboxHome)
      caseboxHome.parent.insertBefore(box, caseboxHome.next);
    return true;
  }
  return false;
}

function showCaseScan(data) {
  closeLinkbox();
  closeSetbox();
  caseCode = data.barcode;
  const box = document.getElementById("casebox");
  box.hidden = false;
  document.getElementById("casebox-view").hidden = false;
  document.getElementById("casebox-form").hidden = true;
  document.getElementById("casebox-msg").textContent = "";
  document.getElementById("casebox-title").textContent =
    "Box of multiple products";
  document.getElementById("casebox-intro").textContent =
    `${data.barcode} isn't a product of its own - it's a box holding ` +
    `${data.units} of one.`;
  // "8 x" in front of the normal preview, per the product it contains.
  document.getElementById("casebox-mult").textContent = `${data.units} ×`;
  const p = data.product || {};
  const img = document.getElementById("casebox-img");
  if (p.image_url) {
    img.src = p.image_url;
    img.hidden = false;
  } else {
    img.hidden = true;
    img.removeAttribute("src");
  }
  document.getElementById("casebox-ptitle").textContent =
    data.product_title || data.sku;
  document.getElementById("casebox-pmeta").textContent =
    `SKU: ${data.sku}` +
    (p.bin_location ? ` · Bin: ${p.bin_location}` : "") +
    (p.barcode ? ` · Item barcode: ${p.barcode}` : "");
  const note = document.getElementById("casebox-note");
  note.hidden = !data.scan_note;
  note.textContent = data.scan_note ? `⚠ ${data.scan_note}` : "";
  batchSound("other");
}

function openCaseForm(code, existing, docked = false) {
  // Docked = opened INSIDE the edit window (edit-casehost): the edit
  // window stays up, so nothing gets closed on the way in.
  if (!docked) {
    closeLinkbox();
    closeSetbox();
  }
  caseCode = code;
  caseProduct = null;
  const box = document.getElementById("casebox");
  box.hidden = false;
  document.getElementById("casebox-view").hidden = true;
  document.getElementById("casebox-form").hidden = false;
  document.getElementById("casebox-msg").textContent = "";
  document.getElementById("casebox-found").textContent = "";
  document.getElementById("casebox-title").textContent =
    existing ? "Edit this box" : "Box of multiple products";
  document.getElementById("casebox-intro").textContent =
    `${code} - record what's inside so every scan of it says so.`;
  document.getElementById("casebox-sku").value = existing ? existing.sku : "";
  document.getElementById("casebox-units").value = existing
    ? existing.units
    : 8;
  document.getElementById("casebox-notein").value =
    existing && existing.scan_note ? existing.scan_note : "";
  document.getElementById("casebox-sku").focus();
}

async function caseFindProduct() {
  const term = document.getElementById("casebox-sku").value.trim();
  const found = document.getElementById("casebox-found");
  if (!term) return;
  found.textContent = "Looking up…";
  try {
    const p = await apiJson(
      `/api/products/by-barcode/${encodeURIComponent(term)}`
    );
    caseProduct = p;
    found.textContent =
      `✓ ${p.product_title} · SKU ${p.sku}` +
      (p.bin_location ? ` · Bin ${p.bin_location}` : "");
  } catch (err) {
    caseProduct = null;
    found.textContent = `No product found for "${term}".`;
  }
}

document.getElementById("casebox-find").addEventListener("click", caseFindProduct);
document.getElementById("casebox-sku").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    caseFindProduct();
  }
});

document.getElementById("casebox-save").addEventListener("click", async () => {
  const msg = document.getElementById("casebox-msg");
  const sku = (caseProduct && caseProduct.sku)
    || document.getElementById("casebox-sku").value.trim();
  if (!sku) {
    msg.textContent = "Say which product is inside first.";
    return;
  }
  try {
    const res = await postJson("/api/cases", {
      barcode: caseCode,
      sku,
      units: Number(document.getElementById("casebox-units").value) || 0,
      scan_note:
        document.getElementById("casebox-notein").value.trim() || null,
      created_by: operatorEl.value.trim() || null,
    });
    msg.textContent = res.message;
    const fresh = await apiJson(`/api/cases/${encodeURIComponent(caseCode)}`);
    showCaseScan(fresh);
    document.getElementById("casebox-msg").textContent = res.message;
  } catch (err) {
    msg.textContent = err.message;
  }
});

document.getElementById("casebox-edit").addEventListener("click", async () => {
  try {
    const c = await apiJson(`/api/cases/${encodeURIComponent(caseCode)}`);
    openCaseForm(caseCode, c);
  } catch (err) {
    document.getElementById("casebox-msg").textContent = err.message;
  }
});

document.getElementById("casebox-forget").addEventListener("click", async () => {
  if (
    !confirm(
      `Stop treating ${caseCode} as a box of multiple products?\n\n` +
        `Scanning it will go back to coming up empty.`
    )
  )
    return;
  try {
    await apiJson(`/api/cases/${encodeURIComponent(caseCode)}`, {
      method: "DELETE",
    });
    closeCasebox();
    resetFlow();
    setResult("That barcode is no longer a box.", "ok");
  } catch (err) {
    document.getElementById("casebox-msg").textContent = err.message;
  }
});

// Both unknown-barcode windows can hand off to the case form — that is how
// a new case code gets recorded in the first place.
document.getElementById("set-case").addEventListener("click", () => {
  openCaseForm(setSerials[0], null);
});
document.getElementById("alias-case").addEventListener("click", () => {
  openCaseForm(aliasCandidate || el.barcode.value.trim(), null);
});

document.getElementById("casebox-cancel").addEventListener("click", () => {
  if (!closeCasebox()) resetFlow();
});
document.getElementById("casebox-done").addEventListener("click", () => {
  if (!closeCasebox()) resetFlow();
});

// --- Foreign-barcode linking ------------------------------------------------
// State for the linkbox: the unknown code just scanned, and the product the
// operator is previewing (link mode) or confirming (alias-scan mode).
let aliasCandidate = null;
let aliasPreviewProduct = null;
let linkboxInfo = null; // structured 404 detail (e.g. known-prefix, bad SKU)

function hideLinkboxExtras() {
  el.prefixSection.hidden = true;
  el.replaceSection.hidden = true;
  el.replaceAck.checked = false;
  el.replaceGo.disabled = true;
  el.prefixNote.value = "";
  el.prefixReco.hidden = true;
  // Edit-mode body: hidden for the unknown-barcode flows, and the old
  // actions-row case button comes back for them.
  const editbox = document.getElementById("editbox");
  if (editbox) editbox.hidden = true;
  const aliasCase = document.getElementById("alias-case");
  if (aliasCase) aliasCase.hidden = false;
  // A case cell still docked in the edit window goes home.
  const box = document.getElementById("casebox");
  const host = document.getElementById("edit-casehost");
  if (host && box && box.parentElement === host) closeCasebox();
}

// Recommended SKU: whenever a 4-digit prefix is entered, consult the loaded
// manufacturer sheet and surface its SKU when it differs from the product's.
let prefixRecoTimer;
el.prefixInput.addEventListener("input", () => {
  clearTimeout(prefixRecoTimer);
  el.prefixReco.hidden = true;
  const p = el.prefixInput.value.trim();
  if (!/^\d{4}$/.test(p)) return;
  prefixRecoTimer = setTimeout(async () => {
    try {
      const res = await apiFetch(
        `/api/serial-prefixes/${encodeURIComponent(p)}`
      );
      if (!res.ok) return;
      const row = await res.json();
      const currentSku = aliasPreviewProduct && aliasPreviewProduct.sku;
      if (row.sku && row.sku !== currentSku) {
        el.prefixRecoText.textContent =
          `Astronomik sheet: prefix ${p} → SKU ${row.sku}` +
          (row.item_name ? ` · ${row.item_name}` : "");
        el.prefixReco.hidden = false;
      }
    } catch (err) {
      /* recommendation is best-effort */
    }
  }, 250);
});

el.prefixRecoApply.addEventListener("click", () => {
  const text = el.prefixRecoText.textContent;
  const match = text.match(/SKU (\S+)/);
  if (!match) return;
  setReplaceMode("sku", match[1]);
  el.replaceSection.hidden = false;
  el.replaceInput.focus();
});

// Re-run the original scan after a fix (new prefix, updated SKU) so the
// normal flow — serial recognition, name panel, auto-print — takes over.
function retryLookup(code) {
  closeLinkbox();
  el.barcode.disabled = false;
  el.barcode.value = code;
  el.barcode.dispatchEvent(
    new KeyboardEvent("keydown", { key: "Enter", bubbles: true })
  );
}

function renderAliasPreview(p) {
  aliasPreviewProduct = p;
  // Title links to the product in Shopify admin whenever a URL can be
  // built (real GID, or the SKU-filtered fallback).
  el.aliasPtitle.innerHTML = productLink(
    (p.product_title || "—") +
      (p.variant_title && p.variant_title !== "Default Title"
        ? ` (${p.variant_title})`
        : ""),
    p.shopify_product_id,
    p.sku
  );
  el.aliasPsku.textContent = p.sku || "—";
  el.aliasPbarcode.textContent = p.barcode || "—";
  el.aliasPbin.textContent = p.bin_location || "—";
  if (p.image_url) {
    el.aliasImg.src = p.image_url;
    el.aliasImg.hidden = false;
  } else {
    el.aliasImg.hidden = true;
    el.aliasImg.removeAttribute("src");
  }
  el.aliasPreview.hidden = false;
}

function openLinkbox(scannedCode, info = null) {
  el.flow.classList.add("flow--side");
  aliasCandidate = scannedCode;
  aliasPreviewProduct = null;
  linkboxInfo = info;
  hideLinkboxExtras();
  el.linkboxTitle.textContent = info
    ? "Serial recognized - store SKU outdated"
    : "Unknown barcode";
  el.linkboxText.textContent = info
    ? `${info.message} Look up the product below (by its current barcode ` +
      `or SKU), then update its SKU.`
    : `"${scannedCode}" isn't in the system. If this is a manufacturer ` +
      `barcode on a known product, enter our barcode or SKU to link them.`;
  el.linkboxForm.hidden = false;
  el.aliasTarget.value = "";
  el.aliasPreview.hidden = true;
  el.aliasAccept.hidden = true;
  el.aliasAccept.textContent = "Link barcode & continue";
  el.aliasUnlink.hidden = true;
  el.linkbox.hidden = false;
  setResult("No product found for that barcode or SKU.", "err");
  el.aliasTarget.focus();
}

function openConfirmBox(product) {
  el.flow.classList.add("flow--side");
  aliasCandidate = product.alias_barcode;
  el.linkboxTitle.textContent = "Linked barcode - confirm the item";
  el.linkboxText.textContent =
    `"${product.alias_barcode}" doesn't match internal barcodes; it was ` +
    `previously linked to this product. Confirm this is the right item.`;
  el.linkboxForm.hidden = true;
  renderAliasPreview(product);
  el.aliasAccept.hidden = false;
  el.aliasAccept.textContent = "Confirm item";
  el.aliasUnlink.hidden = false;
  hideLinkboxExtras();
  el.linkbox.hidden = false;
  setResult("", null);
}

function closeLinkbox() {
  el.linkbox.hidden = true;
  el.flow.classList.remove("flow--side");
  hideLinkboxExtras();
  aliasCandidate = null;
  aliasPreviewProduct = null;
  linkboxEditMode = false;
  // Docked beside the product window? Send the element back home so the
  // Scan Station flows keep working (see phistOpenEdit).
  const dock = document.getElementById("phist-editdock");
  if (dock && el.linkbox.parentElement === dock) {
    dock.hidden = true;
    if (linkboxHome)
      linkboxHome.parent.insertBefore(el.linkbox, linkboxHome.next);
  }
}

// --- Multi-box filter sets --------------------------------------------------
// Three component serials (R/G/B slots) -> one set product. Confirming
// registers all three prefixes with a ONE-TAG-PER-SET scan note, then
// re-runs the original scan so the normal serial flow takes over.
let setSerials = [];
let setSelectedSku = null;

function setSlotEls() {
  return [0, 1, 2].map((i) => document.getElementById(`set-slot-${i}`));
}

function renderSetSlots() {
  setSlotEls().forEach((slot, i) => {
    const val = setSerials[i];
    slot.querySelector("span").textContent = val || "—";
    slot.classList.toggle("setslot--filled", !!val);
    slot.classList.toggle("setslot--active", i === setSerials.length);
  });
  const full = setSerials.length >= 3;
  el.setScanInput.disabled = full;
  el.setboxChoose.hidden = !full;
  if (full) loadSetCandidates();
}

function openSetbox(seedSerial) {
  closeLinkbox();
  el.flow.classList.add("flow--side");
  setSerials = [seedSerial];
  setSelectedSku = null;
  el.setSkuInput.value = "";
  el.setCandidates.innerHTML = "";
  el.setbox.hidden = false;
  renderSetSlots();
  setResult(
    "Serial not recognized - set flow opened. Scan the remaining filters, " +
      "or mark it a single product.",
    null
  );
  el.setScanInput.value = "";
  el.setScanInput.focus();
}

function closeSetbox() {
  el.setbox.hidden = true;
  el.flow.classList.remove("flow--side");
  setSerials = [];
  setSelectedSku = null;
}

el.setScanInput.addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  const code = el.setScanInput.value.trim();
  el.setScanInput.value = "";
  if (!code) return;
  if (!/^\d{5,12}$/.test(code)) {
    setResult("That doesn't look like a filter serial number.", "err");
    return;
  }
  if (setSerials.some((s) => s.slice(0, 4) === code.slice(0, 4))) {
    setResult(
      "That filter's prefix is already in a slot - scan a different one.",
      "err"
    );
    return;
  }
  setSerials.push(code);
  setResult("", null);
  renderSetSlots();
  if (setSerials.length < 3) el.setScanInput.focus();
});

async function loadSetCandidates() {
  if (el.setCandidates.childElementCount) return; // already loaded
  try {
    const res = await apiFetch("/api/filter-sets");
    if (!res.ok) return;
    const { sets } = await res.json();
    el.setCandidates.innerHTML = "";
    sets.forEach((s) => {
      const li = document.createElement("li");
      li.innerHTML = `${escapeHtml(s.title)} - ${escapeHtml(s.variant || "")}
        <span class="mono">(SKU ${escapeHtml(s.sku || "?")})</span>`;
      li.addEventListener("click", () => {
        setSelectedSku = s.sku;
        el.setSkuInput.value = s.sku || "";
        el.setCandidates
          .querySelectorAll("li")
          .forEach((x) => x.classList.toggle("selected", x === li));
      });
      el.setCandidates.append(li);
    });
  } catch (err) {
    /* candidate list is best-effort; the SKU box still works */
  }
}

el.setConfirm.addEventListener("click", async () => {
  const target = el.setSkuInput.value.trim();
  if (setSerials.length < 3 || !target) {
    setResult("Scan all three filters and pick or type the set SKU.", "err");
    return;
  }
  const operator = requireOperator();
  if (!operator) return;
  el.setConfirm.disabled = true;
  try {
    const res = await apiFetch("/api/filter-sets/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        serials: setSerials,
        target,
        created_by: operator,
      }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setResult(
        typeof body.detail === "string" ? body.detail : "Set registration failed.",
        "err"
      );
      return;
    }
    const seed = setSerials[0];
    closeSetbox();
    setResult("Filter set registered - rescanning…", "ok");
    retryLookup(seed);
  } catch (err) {
    setResult("Network error during set registration.", "err");
  } finally {
    el.setConfirm.disabled = false;
  }
});

el.setSingle.addEventListener("click", () => {
  const seed = setSerials[0];
  closeSetbox();
  openLinkbox(seed);
});

el.setCancel.addEventListener("click", () => {
  closeSetbox();
  el.barcode.value = "";
  setResult("", null);
  activate("barcode");
});

// Edit mode: the same window, opened from a loaded product's Edit button —
// no unknown scan involved. Offers the serial-prefix and SKU tools wired
// to the current product.
let linkboxEditMode = false;

// The edit window's three saved values — the ✕ buttons restore these,
// and each Save greys out while its input still equals them.
let editDefaults = { sku: "", barcode: "", note: "" };

function editRowSync() {
  const rows = [
    ["edit-sku", "edit-sku-save", editDefaults.sku, false],
    // An empty barcode is a VALID save since 2026-09-14 (it REMOVES
    // the barcode from the listing - Nick's S11230 needed the main
    // code moved off the full product and there was no way to clear
    // it). An empty SKU never is.
    ["edit-barcode", "edit-barcode-save", editDefaults.barcode, true],
    // An empty note is a VALID save (it clears the note) — only equality
    // with the saved value greys the button.
    ["edit-note", "edit-note-save", editDefaults.note, true],
  ];
  rows.forEach(([inputId, saveId, def, emptyOk]) => {
    const value = document.getElementById(inputId).value.trim();
    document.getElementById(saveId).disabled =
      value === (def || "") || (!emptyOk && !value);
  });
  // The Link buttons follow the same rule: a value equal to the saved
  // field needs no link (it already finds this product).
  [["edit-sku", "edit-sku-link", editDefaults.sku],
   ["edit-barcode", "edit-barcode-link", editDefaults.barcode],
  ].forEach(([inputId, linkId, def]) => {
    const value = document.getElementById(inputId).value.trim();
    document.getElementById(linkId).disabled = value === def || !value;
  });
}

function openEditbox() {
  if (!pendingProduct) return;
  linkboxEditMode = true;
  aliasCandidate = null;
  linkboxInfo = null;
  el.flow.classList.add("flow--side");
  el.linkboxTitle.textContent = "Edit product";
  el.linkboxText.textContent = "";
  el.linkboxForm.hidden = true;
  // The hidden target field feeds the save handlers.
  el.aliasTarget.value = pendingProduct.barcode || pendingProduct.sku || "";
  renderAliasPreview(pendingProduct);
  el.aliasAccept.hidden = true;
  el.aliasUnlink.hidden = true;
  // The old actions-row case button is replaced by the edit body's own.
  document.getElementById("alias-case").hidden = true;
  // Edit rows, prefilled from the saved values.
  editDefaults = {
    sku: (pendingProduct.sku || "").trim(),
    barcode: (pendingProduct.barcode || "").trim(),
    note: (pendingProduct.scan_note || "").trim(),
  };
  document.getElementById("edit-sku").value = editDefaults.sku;
  document.getElementById("edit-barcode").value = editDefaults.barcode;
  document.getElementById("edit-note").value = editDefaults.note;
  document.getElementById("edit-msg").textContent = "";
  editRowSync();
  document.getElementById("editbox").hidden = false;
  // Astronomik + case live behind their buttons now.
  el.prefixInput.value = pendingProduct.serial_prefix || "";
  el.prefixNote.value = pendingProduct.serial_note || "";
  el.prefixSection.hidden = true;
  el.replaceSection.hidden = true;
  el.linkbox.hidden = false;
}

el.productCardJump.addEventListener("click", () => {
  const p = pendingProduct;
  if (!p) return;
  goTab("home");
  openProductCard(p.sku || p.barcode || "");
});
el.productEdit.addEventListener("click", openEditbox);

// --- Edit-window rows: inputs, ✕ resets, dynamic-grey saves ------------------
["edit-sku", "edit-barcode", "edit-note"].forEach((id) =>
  document.getElementById(id).addEventListener("input", editRowSync)
);
document.getElementById("edit-sku-reset").addEventListener("click", () => {
  document.getElementById("edit-sku").value = editDefaults.sku;
  editRowSync();
});
document.getElementById("edit-barcode-reset").addEventListener("click", () => {
  document.getElementById("edit-barcode").value = editDefaults.barcode;
  editRowSync();
});
document.getElementById("edit-note-reset").addEventListener("click", () => {
  document.getElementById("edit-note").value = editDefaults.note;
  editRowSync();
});

const editMsg = (text) =>
  (document.getElementById("edit-msg").textContent = text);

// The [?] beside the scan note: hover text carries the explanation, and
// a tap shows the same words for touch screens.
document.getElementById("edit-note-help").addEventListener("click", (ev) => {
  alert(ev.currentTarget.title);
});

// Manual product refresh (Nick, 2026-09-14): live Shopify is the
// source of truth - one press re-reads the exact variant on screen
// and the card, catalog row and tag records all follow. A code clash
// with another product asks to confirm and files a Review task, it
// never blocks.
async function refreshProductFromShopify(btn, fromEdit) {
  if (!pendingProduct || !pendingProduct.shopify_variant_id) {
    alert("Scan a product first.");
    return;
  }
  await spinRefresh(btn, async () => {
    const body = {
      variant_gid: pendingProduct.shopify_variant_id,
      changed_by: operatorEl.value || null,
    };
    let res = null;
    try {
      res = await postJson("/api/products/refresh", body);
    } catch (err) {
      if (
        /Confirm to refresh anyway/.test(err.message) &&
        confirm(err.message)
      ) {
        try {
          res = await postJson("/api/products/refresh", {
            ...body,
            confirmed: true,
          });
        } catch (e2) {
          setResult(e2.message, "err", "rfid");
          return;
        }
      } else {
        setResult(err.message, "err", "rfid");
        return;
      }
    }
    pendingProduct = { ...pendingProduct, ...res.product };
    showProduct(pendingProduct);
    if (fromEdit) {
      // The edit window's saved values follow the fresh identity.
      editDefaults = {
        sku: (pendingProduct.sku || "").trim(),
        barcode: (pendingProduct.barcode || "").trim(),
        note: editDefaults.note,
      };
      document.getElementById("edit-sku").value = editDefaults.sku;
      document.getElementById("edit-barcode").value = editDefaults.barcode;
      editRowSync();
      renderAliasPreview(pendingProduct);
      editMsg(res.message);
    } else {
      setResult(
        res.message,
        res.clashes && res.clashes.length ? "warn-soft" : "ok",
        "rfid"
      );
    }
  });
}
document
  .getElementById("product-refresh")
  .addEventListener("click", (e) =>
    refreshProductFromShopify(e.currentTarget, false)
  );
document
  .getElementById("edit-refresh")
  .addEventListener("click", (e) =>
    refreshProductFromShopify(e.currentTarget, true)
  );

// SKU + barcode go through the SAME audited Shopify-write endpoints as
// the unknown-barcode flows (History-logged there); the checkbox ritual
// is replaced by a confirm() since the greyed-at-saved-value buttons
// already stop accidental no-op writes. A code another product wears
// asks to confirm (and files a Review task server-side) - it never
// stops the save outright (Nick, 2026-09-14).
async function postOverwriteWithClashConfirm(url, body) {
  try {
    return await postJson(url, body);
  } catch (err) {
    if (
      /Confirm to write it anyway/.test(err.message) &&
      confirm(err.message)
    ) {
      return await postJson(url, { ...body, force: true });
    }
    throw err;
  }
}

document.getElementById("edit-sku-save").addEventListener("click", async () => {
  const operator = requireOperator();
  if (!operator || !pendingProduct) return;
  const newSku = document.getElementById("edit-sku").value.trim();
  if (
    !confirm(
      `Replace this product's SKU in Shopify?\n\n${editDefaults.sku || "(none)"} → ${newSku}\n\nPermanent (History keeps the record).`
    )
  )
    return;
  const btn = document.getElementById("edit-sku-save");
  btn.disabled = true;
  try {
    const res = await postOverwriteWithClashConfirm("/api/sku-overwrites", {
      new_sku: newSku,
      target: editDefaults.sku || editDefaults.barcode,
      changed_by: operator,
      confirmed: true,
    });
    editDefaults.sku = newSku;
    pendingProduct.sku = newSku;
    el.pSku.textContent = newSku; // the card behind follows immediately
    if (res.product) renderAliasPreview({ ...pendingProduct, ...res.product });
    editMsg(
      `SKU updated to ${newSku} ✓ (History-logged)` +
        (res.legacy_linked
          ? " - the old broken value stays linked, old labels still scan"
          : "")
    );
  } catch (err) {
    editMsg(err.message);
  }
  editRowSync();
});

document
  .getElementById("edit-barcode-save")
  .addEventListener("click", async () => {
    const operator = requireOperator();
    if (!operator || !pendingProduct) return;
    const newBarcode = document.getElementById("edit-barcode").value.trim();
    // Empty = REMOVE the barcode from the listing (Nick, 2026-09-14,
    // the S11230: the main code had to come OFF the full product and
    // nothing allowed it). Its own confirm names the consequence.
    const confirmText = newBarcode
      ? `Replace this product's barcode in Shopify?\n\n${editDefaults.barcode || "(none)"} → ${newBarcode}\n\nPermanent (History keeps the record).`
      : `REMOVE this product's barcode in Shopify?\n\n${editDefaults.barcode || "(none)"} → (no barcode)\n\nScanning the old code will stop finding this product. Permanent (History keeps the record).`;
    if (!confirm(confirmText)) return;
    const btn = document.getElementById("edit-barcode-save");
    btn.disabled = true;
    try {
      const res = await postOverwriteWithClashConfirm(
        "/api/barcode-overwrites",
        {
          new_barcode: newBarcode,
          target: editDefaults.sku || editDefaults.barcode,
          changed_by: operator,
          confirmed: true,
          // Pin to the listing on screen - twins share codes, and an
          // unpinned write once landed on the wrong variant (Nick,
          // 2026-09-09, open-box).
          variant_gid:
            (pendingProduct && pendingProduct.shopify_variant_id) || null,
        }
      );
      editDefaults.barcode = newBarcode;
      pendingProduct.barcode = newBarcode;
      el.pBarcode.textContent = newBarcode; // the card behind follows
      if (res.product)
        renderAliasPreview({ ...pendingProduct, ...res.product });
      editMsg(
        (newBarcode
          ? `Barcode updated to ${newBarcode} ✓ (History-logged)`
          : "Barcode removed ✓ (History-logged)") +
          (res.legacy_linked
            ? " - the old broken value stays linked, old labels still scan"
            : "")
      );
    } catch (err) {
      editMsg(err.message);
    }
    editRowSync();
  });

document.getElementById("edit-note-save").addEventListener("click", async () => {
  const operator = requireOperator();
  if (!operator || !pendingProduct || !pendingProduct.sku) return;
  const note = document.getElementById("edit-note").value.trim();
  const btn = document.getElementById("edit-note-save");
  btn.disabled = true;
  try {
    await apiJson(
      `/api/products/${encodeURIComponent(pendingProduct.sku)}/scan-note`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note, changed_by: operator }),
      }
    );
    editDefaults.note = note;
    pendingProduct.scan_note = note || null;
    if (phistData && phistData.product) phistData.product.scan_note = note || null;
    updateScanNote(pendingProduct);
    editMsg(note ? "Scan note saved ✓ - it shows on every scan." : "Scan note cleared ✓");
  } catch (err) {
    editMsg(err.message);
  }
  editRowSync();
});

// Link SKU / Link Barcode: the typed value becomes a lookup ALIAS for
// this product (the existing barcode-alias store) - scanning or searching
// it finds the product, while the real Shopify fields stay untouched
// (Nick, 2026-08-25). History logs the link with a one-click unlink.
async function editLinkAlias(inputId, what) {
  const operator = requireOperator();
  if (!operator || !pendingProduct) return;
  const value = document.getElementById(inputId).value.trim();
  if (!value) return;
  const title =
    pendingProduct.product_title || pendingProduct.sku || "this product";
  if (
    !confirm(
      `Link ${what} "${value}" to ${title}?\n\n` +
        `Scanning or looking up ${value} will find this product from ` +
        `now on. Its real Shopify SKU and barcode stay unchanged - ` +
        `use Save instead if Shopify itself should be corrected.\n\n` +
        `Undo lives in History (unlink).`
    )
  )
    return;
  const btn = document.getElementById(inputId + "-link");
  btn.disabled = true;
  try {
    await postJson("/api/barcode-aliases", {
      alias_barcode: value,
      target: editDefaults.sku || editDefaults.barcode,
      created_by: operator,
    });
    // Back to the saved value: leaving the typed alias in the box would
    // keep Save armed and invite an accidental Shopify overwrite of the
    // very thing the link just avoided.
    document.getElementById(inputId).value =
      inputId === "edit-sku" ? editDefaults.sku : editDefaults.barcode;
    editMsg(
      `${value} linked ✓ - it now finds this product; Shopify fields ` +
        `unchanged (unlink in History).`
    );
  } catch (err) {
    editMsg(err.message);
  }
  editRowSync();
}
document
  .getElementById("edit-sku-link")
  .addEventListener("click", () => editLinkAlias("edit-sku", "SKU"));
document
  .getElementById("edit-barcode-link")
  .addEventListener("click", () => editLinkAlias("edit-barcode", "barcode"));

// Astronomik: the serial-prefix cell folds out under the rows.
document.getElementById("edit-astro").addEventListener("click", () => {
  el.prefixSection.hidden = !el.prefixSection.hidden;
  if (!el.prefixSection.hidden) el.prefixInput.focus();
});

// Box of multiple products: the case cell docks INSIDE the edit window
// instead of replacing it (Nick, 2026-08-18).
let caseboxHome = null;
document.getElementById("edit-case").addEventListener("click", () => {
  const box = document.getElementById("casebox");
  const host = document.getElementById("edit-casehost");
  if (!caseboxHome)
    caseboxHome = { parent: box.parentElement, next: box.nextElementSibling };
  host.appendChild(box);
  host.hidden = false;
  openCaseForm(
    (pendingProduct && (pendingProduct.barcode || pendingProduct.sku)) ||
      el.aliasTarget.value.trim(),
    null,
    true
  );
});

// Bin chip on the preview card: same click-to-edit as the product card.
document.getElementById("alias-pbin").addEventListener("click", () => {
  if (!aliasPreviewProduct) return;
  el.aliasPbin.hidden = true;
  const input = document.getElementById("alias-bininput");
  input.value = "";
  input.hidden = false;
  input.focus();
});
document.getElementById("alias-bininput").addEventListener("blur", () => {
  const input = document.getElementById("alias-bininput");
  if (!input.disabled) {
    input.hidden = true;
    el.aliasPbin.hidden = false;
  }
});
document
  .getElementById("alias-bininput")
  .addEventListener("keydown", async (event) => {
    const input = document.getElementById("alias-bininput");
    if (event.key === "Escape") {
      event.stopPropagation();
      input.hidden = true;
      el.aliasPbin.hidden = false;
      return;
    }
    if (event.key !== "Enter") return;
    const bin = input.value.trim();
    if (!bin || !aliasPreviewProduct) return;
    const operator = requireOperator();
    if (!operator) return;
    input.disabled = true;
    try {
      await postJson("/api/bin-updates", {
        target: aliasPreviewProduct.sku || aliasPreviewProduct.barcode,
        bin,
        changed_by: operator,
      });
      aliasPreviewProduct.bin_location = bin;
      if (pendingProduct) pendingProduct.bin_location = bin;
      el.aliasPbin.textContent = bin;
      editMsg(`Bin set to ${bin} (saved to Shopify).`);
    } catch (err) {
      editMsg(err.message);
    } finally {
      input.disabled = false;
      input.hidden = true;
      el.aliasPbin.hidden = false;
    }
  });

// --- Edit product from the PRODUCT WINDOW -----------------------------------
// Reuses the Scan Station's edit window wholesale: the #linkbox element
// (listeners and all) docks beside the product panel, and moves back to
// its home in the scan flow when closed — one edit window, two doors.
let linkboxHome = null; // where #linkbox normally lives

function phistOpenEdit() {
  if (!phistData || !phistData.product) return;
  pendingProduct = {
    ...phistData.product,
    serial_prefix:
      phistData.serial_prefix || phistData.product.serial_prefix || null,
    serial_note:
      phistData.serial_note || phistData.product.serial_note || null,
  };
  if (!linkboxHome)
    linkboxHome = {
      parent: el.linkbox.parentElement,
      next: el.linkbox.nextElementSibling,
    };
  const dock = document.getElementById("phist-editdock");
  dock.appendChild(el.linkbox);
  dock.hidden = false;
  openEditbox();
  // openEditbox styles the scan flow for a side panel — not this door.
  el.flow.classList.remove("flow--side");
}

async function checkAliasTarget() {
  const term = el.aliasTarget.value.trim();
  if (!term) return;
  el.aliasCheck.disabled = true;
  try {
    const res = await apiFetch(
      `/api/products/by-barcode/${encodeURIComponent(term)}`
    );
    if (res.status === 404) {
      el.aliasPreview.hidden = true;
      el.aliasAccept.hidden = true;
      setResult("No product found for that barcode or SKU either.", "err");
      el.aliasTarget.select();
      return;
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setResult(body.detail || "Lookup failed.", "err");
      return;
    }
    renderAliasPreview(await res.json());
    el.aliasAccept.hidden = false;
    // Extra tools once a product is in view: register an Astronomik serial
    // prefix (when the scan looks like a serial), and replace a wrong
    // barcode or outdated SKU.
    if (/^\d{5,12}$/.test(aliasCandidate || "")) {
      el.prefixInput.value = aliasCandidate.slice(0, 4);
      el.prefixSection.hidden = false;
    }
    showReplaceSection();
    setResult("Check the product, then link.", null);
  } catch (err) {
    setResult("Network error during lookup.", "err");
  } finally {
    el.aliasCheck.disabled = false;
  }
}

el.aliasCheck.addEventListener("click", checkAliasTarget);
el.aliasTarget.addEventListener("keydown", (event) => {
  if (event.key === "Enter") checkAliasTarget();
});
// Any edit to the target invalidates the previewed product — otherwise a
// stale preview from the previous lookup could get linked to the wrong
// scan. Check product again to re-enable the actions.
el.aliasTarget.addEventListener("input", () => {
  aliasPreviewProduct = null;
  el.aliasPreview.hidden = true;
  el.aliasAccept.hidden = true;
  hideLinkboxExtras();
});

el.aliasAccept.addEventListener("click", async () => {
  if (!aliasPreviewProduct) return;
  // Confirm mode: the alias already exists, just proceed.
  if (el.linkboxForm.hidden) {
    acceptProduct(aliasPreviewProduct, "Item confirmed. Scan the RFID tag.");
    return;
  }
  // Link mode: create the alias, then proceed with the previewed product.
  const operator = requireOperator();
  if (!operator) return;
  el.aliasAccept.disabled = true;
  try {
    const res = await apiFetch("/api/barcode-aliases", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        alias_barcode: aliasCandidate,
        target: el.aliasTarget.value.trim(),
        created_by: operator,
      }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setResult(body.detail || "Linking failed.", "err");
      return;
    }
    const product = { ...aliasPreviewProduct, alias_barcode: aliasCandidate };
    acceptProduct(product, "Barcode linked. Scan the RFID tag.");
  } catch (err) {
    setResult("Network error while linking.", "err");
  } finally {
    el.aliasAccept.disabled = false;
  }
});

el.aliasUnlink.addEventListener("click", async () => {
  if (!aliasCandidate) return;
  if (!confirm(`Unlink barcode ${aliasCandidate} from this product?`)) return;
  const res = await apiFetch(
    `/api/barcode-aliases/${encodeURIComponent(aliasCandidate)}`,
    { method: "DELETE" }
  );
  if (res.ok || res.status === 404) {
    closeLinkbox();
    el.barcode.value = "";
    setResult("Barcode unlinked.", "ok");
    activate("barcode");
  } else {
    setResult("Could not unlink that barcode.", "err");
  }
});

el.aliasCancel.addEventListener("click", () => {
  closeLinkbox();
  el.barcode.select();
  setResult("", null);
});

// Register a new Astronomik serial prefix for the previewed product.
el.prefixSave.addEventListener("click", async () => {
  if (!aliasPreviewProduct) return;
  const operator = requireOperator();
  if (!operator) return;
  const prefix = el.prefixInput.value.trim();
  if (!/^\d{4}$/.test(prefix)) {
    setResult("The prefix must be exactly 4 digits.", "err");
    return;
  }
  el.prefixSave.disabled = true;
  try {
    const res = await apiFetch("/api/serial-prefixes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prefix,
        target: el.aliasTarget.value.trim(),
        scan_note: el.prefixNote.value.trim() || null,
        created_by: operator,
      }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setResult(body.detail || "Saving the prefix failed.", "err");
      return;
    }
    if (aliasCandidate) {
      setResult(`Prefix ${prefix} saved - rescanning…`, "ok");
      retryLookup(aliasCandidate);
    } else {
      // Edit mode: stay on the loaded product.
      setResult(`Serial prefix ${prefix} now points at this product.`, "ok");
      closeLinkbox();
      el.rfid.focus();
    }
  } catch (err) {
    setResult("Network error while saving the prefix.", "err");
  } finally {
    el.prefixSave.disabled = false;
  }
});

// --- Replace barcode / SKU (one input, mode-toggled, ack-gated) ------------
// Both replacements are destructive Shopify writes; the mode decides the
// label, the ack wording, the button, and which endpoint the save hits.
let replaceMode = "barcode";

function detectReplaceMode() {
  // The serial/outdated-SKU flow arrives with a suggested SKU: that IS
  // the SKU-repair path, whatever the scanned string looks like.
  if (linkboxInfo && linkboxInfo.suggested_sku) return "sku";
  const code = (aliasCandidate || "").trim();
  if (/^\d{12,14}$/.test(code)) return "barcode"; // EAN/UPC shaped
  if (/[A-Za-z]/.test(code)) return "sku";
  // Unsure: barcode. Most failed lookups are products whose barcode was
  // set to the SKU because the box never carried one.
  return "barcode";
}

function replaceModePrefill(mode) {
  if (mode === "sku") {
    return (linkboxInfo && linkboxInfo.suggested_sku) || aliasCandidate || "";
  }
  return aliasCandidate || "";
}

function setReplaceMode(mode, prefill) {
  replaceMode = mode;
  const bc = mode === "barcode";
  el.replaceModeBarcode.classList.toggle("replace__mode--on", bc);
  el.replaceModeSku.classList.toggle("replace__mode--on", !bc);
  el.replaceLabel.textContent = bc
    ? "Replace this product's barcode in Shopify (the scanned code " +
      "becomes its real barcode):"
    : "Replace this product's SKU in Shopify (e.g. the manufacturer's " +
      "current item number):";
  el.replaceInput.placeholder = bc ? "New barcode…" : "New SKU…";
  el.replaceAckText.textContent = bc
    ? "I understand this permanently replaces the product's barcode " +
      "in Shopify."
    : "I understand this permanently replaces the product's SKU " +
      "in Shopify.";
  el.replaceGo.textContent = bc ? "Update barcode" : "Update SKU";
  if (prefill !== undefined) el.replaceInput.value = prefill;
  el.replaceAck.checked = false;
  el.replaceGo.disabled = true;
}

function showReplaceSection() {
  const mode = detectReplaceMode();
  setReplaceMode(mode, replaceModePrefill(mode));
  el.replaceSection.hidden = false;
}

el.replaceModeBarcode.addEventListener("click", () =>
  setReplaceMode("barcode", replaceModePrefill("barcode"))
);
el.replaceModeSku.addEventListener("click", () =>
  setReplaceMode("sku", replaceModePrefill("sku"))
);

el.replaceAck.addEventListener("change", () => {
  el.replaceGo.disabled = !el.replaceAck.checked;
});
// Editing the value voids the ack: what was acknowledged has changed.
el.replaceInput.addEventListener("input", () => {
  el.replaceAck.checked = false;
  el.replaceGo.disabled = true;
});

el.replaceGo.addEventListener("click", async () => {
  if (!aliasPreviewProduct || !el.replaceAck.checked) return;
  const operator = requireOperator();
  if (!operator) return;
  const isBc = replaceMode === "barcode";
  const value = el.replaceInput.value.trim();
  if (!value) {
    setResult(
      isBc ? "Enter the new barcode first." : "Enter the new SKU first.",
      "err"
    );
    return;
  }
  el.replaceGo.disabled = true;
  try {
    const res = await apiFetch(
      isBc ? "/api/barcode-overwrites" : "/api/sku-overwrites",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...(isBc ? { new_barcode: value } : { new_sku: value }),
          target: el.aliasTarget.value.trim(),
          changed_by: operator,
          confirmed: true,
        }),
      }
    );
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setResult(
        body.detail ||
          (isBc ? "Barcode replacement failed." : "SKU update failed."),
        "err"
      );
      el.replaceGo.disabled = false;
      return;
    }
    const body = await res.json();
    const product = body.product;
    const legacyNote = body.legacy_linked
      ? " The old broken value stays linked - old labels still scan."
      : "";
    if (isBc) {
      acceptProduct(
        product,
        `Barcode replaced in Shopify.${legacyNote} Scan the RFID tag.`
      );
    } else if (aliasCandidate) {
      setResult(`SKU updated to ${value}. Rescanning…`, "ok");
      retryLookup(aliasCandidate);
    } else {
      // Edit mode: update the card in place.
      if (pendingProduct) {
        pendingProduct.sku = value;
        el.pSku.textContent = value;
      }
      setResult(`SKU updated to ${value} in Shopify.`, "ok");
      closeLinkbox();
      el.rfid.focus();
    }
  } catch (err) {
    setResult("Network error during the update.", "err");
    el.replaceGo.disabled = false;
  }
});

function showProduct(p) {
  // Variant folds into the title (the meta lines are fixed-shape now);
  // the title itself clamps to two lines in CSS.
  const hasVariant = !!(
    p.variant_title && p.variant_title !== "Default Title"
  );
  el.pTitle.textContent =
    (p.product_title || "—") + (hasVariant ? ` (${p.variant_title})` : "");
  el.pTitle.title = el.pTitle.textContent;
  const pImg = document.getElementById("p-img");
  if (p.image_url) {
    pImg.src = p.image_url;
    pImg.hidden = false;
  } else {
    pImg.hidden = true;
    pImg.removeAttribute("src");
  }
  el.pSku.textContent = p.sku || "—";
  el.pBarcode.textContent = p.barcode || "—";
  closeBinEditor();
  el.pBin.textContent = p.bin_location || "—";
  el.pOnhand.textContent = "…";
  // Every fresh barcode starts back at one label — yesterday's big print
  // run must never silently ride into the next product.
  el.printQty.value = 1;
  renderCardLabelPreview(p, null);
  // Set as Open Box only makes sense on a sealed product's card - the
  // -O twin never files a return against itself.
  const obxBtn = document.getElementById("product-openbox");
  if (obxBtn)
    obxBtn.hidden = !p.sku || /-O$/i.test(p.sku.trim());
  el.productCard.hidden = false;
  el.printPanel.hidden = !printingEnabled;
  updateNoBinWarn(p);
  updateFitWarn(p);
  updateScanNote(p);
  loadTags(p);
  loadPlannerHint(p);
}

// The product's standing scan note (set in Edit product): loud, on every
// scan, right where the on-order hint lives. The C72 shows the same note
// with its own sound.
function updateScanNote(p) {
  const elNote = document.getElementById("p-scannote");
  if (!elNote) return;
  const note = (p && p.scan_note) || "";
  elNote.hidden = !note;
  elNote.textContent = note ? `⚠ ${note}` : "";
}

// Red "No bin set" beside the Print button: this product's labels would
// print without a shelf on them. Toggleable in ⚙ (on by default).
// lastShownProduct tracks the card so flipping the setting re-evaluates
// live (pendingProduct clears on reset before the card does).
let lastShownProduct = null;
function updateNoBinWarn(p) {
  lastShownProduct = p;
  const binless =
    !p || !p.bin_location || p.bin_location === "No bin assigned";
  el.printNobin.hidden = !(el.warnNobin.checked && binless);
}

// --- TC-Planner on-order hint ----------------------------------------------
// "This product is on an open purchase order — N more expected." Pure
// decoration from the read-only planner bridge: it loads after the card,
// never blocks a scan, and stays hidden when the bridge is off, the
// planner is down, or nothing is on order. Shared by the Scan Station
// product card and the receiving-batch collect result.
const plannerHintSeqs = {};
async function showPlannerHint(sku, elId) {
  const hint = document.getElementById(elId);
  hint.hidden = true;
  if (!sku) return;
  const seq = (plannerHintSeqs[elId] = (plannerHintSeqs[elId] || 0) + 1);
  try {
    // The operator pick rides along so the planner attributes the call
    // to the person scanning (their own planner token, when one exists).
    const op = operatorEl.value
      ? `?operator=${encodeURIComponent(operatorEl.value)}`
      : "";
    const data = await apiJson(
      `/api/planner/on-order/${encodeURIComponent(sku)}${op}`
    );
    // A newer scan owns this surface now — drop the stale answer.
    if (seq !== plannerHintSeqs[elId]) return;
    if (!data.configured || !data.ok || !data.total_remaining) return;
    const pos = data.orders
      .map(
        (o) =>
          `PO#${o.reference_number} ${o.vendor} (${o.remaining} left` +
          (o.expected_date ? `, ETA ${o.expected_date}` : "") +
          `)`
      )
      .join(" · ");
    hint.textContent =
      `📦 On order: ${data.total_remaining} more expected - ${pos}`;
    hint.hidden = false;
  } catch (err) {
    /* hint only — a failure just means no hint */
  }
}

function loadPlannerHint(p) {
  showPlannerHint(p && p.sku, "planner-hint");
}

// The tags list lives OUTSIDE the details element (full width, below the
// option row) so opening it never pushes the buttons around — the details
// toggle drives its visibility instead.
el.tagsPanel.addEventListener("toggle", () => {
  el.tagsList.hidden = !el.tagsPanel.open;
});

// Edit label…: the product panel already carries the two-line label
// editor with live sticker preview — open it on the loaded product.
document.getElementById("product-label").addEventListener("click", () => {
  if (!pendingProduct) return;
  const term = pendingProduct.sku || pendingProduct.barcode;
  if (term) openProductHistory(term);
});

// --- Label preview on the product card (⚙ setting) --------------------------
// A miniature of what the NEXT print will say, using the same saved
// label lines the server now applies to Scan Station prints — so a
// freshly edited SKU line is visible before a single sticker comes out
// (Nick, 2026-08-25: the Softbag1 line printed stale with no way to see
// it coming). Serial products preview the name box's current text live.
let lastTagData = null;
function renderCardLabelPreview(p, data) {
  const box = document.getElementById("p-labelprev");
  if (!p || !el.showLabelPreview.checked) {
    box.hidden = true;
    return;
  }
  let top = STORE_HEADER;
  let skuLine = p.sku || "";
  // Open-box labels print the BASE SKU - the -O suffix belongs to the
  // barcode, not the product (Nick, 2026-09-15).
  if (/-O$/i.test(skuLine.trim())) skuLine = skuLine.trim().slice(0, -2);
  if (p.serial_prefix) {
    top =
      el.serialLabelInput.value.trim() ||
      p.serial_label ||
      STORE_HEADER;
  } else if (data && data.label_name) {
    const placement = data.label_placement || "header";
    if (placement === "header" || placement === "both")
      top = data.label_name;
    skuLine =
      data.label_sku_text ||
      (placement === "sku" || placement === "both"
        ? data.label_name
        : skuLine);
  } else if (data && data.label_sku_text) {
    skuLine = data.label_sku_text;
  }
  const head = document.getElementById("p-prev-header");
  setPreviewHeader(head, top, top === STORE_HEADER);
  renderSkuPreviewLine("p-prev-sku", skuLine);
  document.getElementById("p-prev-bc").textContent =
    p.barcode || p.sku || "";
  const obNote =
    /-O$/i.test((p.sku || "").trim()) ||
    /open[\s-]?box/i.test(p.product_title || "")
      ? ", OPEN BOX"
      : "";
  document.getElementById("p-prev-bin").textContent =
    "BIN: " +
    (p.bin_location && p.bin_location !== "No bin assigned"
      ? p.bin_location
      : "—") +
    obNote;
  box.hidden = false;
}

// The serial name box edits the label's top line — the preview follows
// every keystroke.
el.serialLabelInput.addEventListener("input", () => {
  if (pendingProduct && pendingProduct.serial_prefix)
    renderCardLabelPreview(pendingProduct, lastTagData);
});

// --- Tags on file for the scanned product ----------------------------------
async function loadTags(p) {
  el.pTagCount.textContent = "…";
  el.tagsList.innerHTML = "";
  el.tagsPanel.hidden = true;
  el.tagsPanel.open = false;
  el.tagsList.hidden = true;
  const params = new URLSearchParams();
  if (p.sku) params.set("sku", p.sku);
  if (p.barcode) params.set("barcode", p.barcode);
  if (![...params].length) {
    el.pTagCount.textContent = "—";
    el.pOnhand.textContent = "—";
    return;
  }
  try {
    const res = await apiFetch(`/api/products/tags?${params}`);
    if (!res.ok) {
      el.pTagCount.textContent = "—";
      el.pOnhand.textContent = "—";
      return;
    }
    const data = await res.json();
    el.pTagCount.textContent = String(data.count);
    el.pOnhand.textContent =
      data.on_hand != null ? String(data.on_hand) : "—";
    lastTagData = data;
    renderCardLabelPreview(p, data);
    if (data.count) {
      data.assignments.forEach((a) => {
        const li = document.createElement("li");
        li.innerHTML = `
          <span class="recent__epc">${escapeHtml(a.rfid_id)}</span>${
            a.suspect
              ? '<span class="suspect" title="Probably a bad read - ' +
                're-scan this tag.">⚠</span>'
              : ""
          }
          <span class="recent__meta">${escapeHtml(
            (a.assigned_at || "").slice(0, 10)
          )} · ${escapeHtml(a.assigned_by || "")}</span>`;
        el.tagsList.append(li);
      });
      el.tagsPanel.hidden = false;
    }
  } catch (err) {
    el.pTagCount.textContent = "—";
    el.pOnhand.textContent = "—";
  }
}

// --- Print & encode labels -------------------------------------------------
// The Labels count: digits only (no e/+/-/., no spinner arrows — CSS kills
// those), and clicking it selects the whole number so typing replaces it.
el.printQty.addEventListener("focus", () => el.printQty.select());
el.printQty.addEventListener("click", () => el.printQty.select());
el.printQty.addEventListener("keydown", (ev) => {
  if (["e", "E", "+", "-", "."].includes(ev.key)) ev.preventDefault();
});
el.printQty.addEventListener("input", () => {
  const digits = el.printQty.value.replace(/\D/g, "").slice(0, 3);
  if (el.printQty.value !== digits) el.printQty.value = digits;
});

async function queueLabels(quantity, confirmedBig = false) {
  if (!pendingProduct) return;
  const operator = requireOperator();
  if (!operator) return;
  // A mistyped quantity prints a pile of live RFID stickers — big runs
  // take a checkbox + confirm first.
  if (quantity > 10 && !confirmedBig) {
    openBigPrint(quantity);
    return;
  }
  // Printer gate: when printers ARE registered, printing needs a live
  // selection — otherwise the picker opens and this run continues after
  // the confirm. An empty registry queues exactly as before the picker.
  if (!(await printerReady())) {
    openPrinterPicker(() => queueLabels(quantity, confirmedBig));
    return;
  }
  autoPrintedThisScan = true; // any print covers the unit in hand
  el.printBtn.disabled = true;
  el.printStatus.textContent = "Queueing…";
  try {
    // Serialized-brand products print the operator's preferred name; save
    // any unsaved edit so the next scan remembers it too.
    let labelName = null;
    if (pendingProduct.serial_prefix) {
      labelName = el.serialLabelInput.value.trim() || null;
      saveSerialLabel(false);
    }
    const res = await apiFetch("/api/print-jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        quantity,
        ...pendingProduct,
        label_name: labelName,
        requested_by: operator,
        printer: selectedPrinter || null,
        print_session: printSession,
      }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      el.printStatus.textContent = body.detail || "Queueing failed.";
      return;
    }
    const data = await res.json();
    bulkPrinted += (data.jobs || []).length;
    // A multi-label run IS a bulk visit: turn the printed-vs-tagged ledger
    // on so it decides when this product is done. Auto-reset is a bulk
    // prerequisite — enabled for this visit only, the saved ⚙ setting is
    // untouched (programmatic .checked fires no change event).
    if (quantity > 1 && !bulkOn) {
      if (!el.autoReset.checked) el.autoReset.checked = true;
      bulkOn = true;
    }
    renderBulk();
    watchPrintJobs(data.jobs.map((j) => j.id));
  } catch (err) {
    el.printStatus.textContent = "Network error while queueing.";
  } finally {
    el.printBtn.disabled = false;
    // Hands back on the scanner: the label is printing, the next action is
    // scanning the tag — no mouse required.
    el.rfid.focus();
  }
}

el.printBtn.addEventListener("click", () =>
  queueLabels(Math.max(1, Math.min(100, Number(el.printQty.value) || 1)))
);

// --- "How do I use Scan Station?" walkthrough -------------------------------
// A slideshow of illustrated steps: image up top, arrows on the sides
// (greyed at the ends), explanation at the bottom. Real material now
// (Nick, 2026-08-24): screenshots of the site walking an actual product
// (the ZWO Nikon-T2-II) plus Nick's warehouse photos of the ZWO
// double-barcode box and the Svbony SKU label. The scanner slide stays
// an illustration until someone photographs the right scanner.
// Rebuilt 2026-08-24 (Nick's slide order): dark-mode captures + GIFs of
// the real flows. The old slide-*.png/svg files stay on disk for
// browsers still holding a cached app.js. No em dashes in captions.
const HELP_SLIDES = [
  {
    img: "/static/help/s1-link.png",
    text:
      "To link RFID tags to products, open the TC RFID app on the C72 " +
      "and make sure it's on its Link tab (it starts there). Then turn " +
      "on C72 LINK at the top of this page. The status line confirms " +
      "the gun is connected, and its scans act on this terminal.",
  },
  {
    img: "/static/help/s2-labels.png",
    html:
      "Different vendors have different label conventions, some with " +
      "invalid barcodes and some with no barcodes at all.<br>" +
      "<b>Example 1 (ZWO):</b> often two barcodes. The TOP one is the " +
      "product barcode; the lower one is a wholesale serial the system " +
      "doesn't know.<br>" +
      "<b>Example 2 (Svbony):</b> no barcode at all. Type the SKU " +
      "printed on the label instead.",
  },
  {
    img: "/static/help/s3-print.gif",
    text:
      "Enter the product SKU by hand, or scan the barcode with a linked " +
      "barcode scanner (a C72 on its Link tab passes barcodes here " +
      "too). When the product appears, set how many labels you need, " +
      "then click Print & encode RFID labels.",
  },
  {
    img: "/static/help/s4-sweep.gif",
    text:
      "Stick the labels on the boxes and scan them with the RFID gun. " +
      "By default the terminal resets for the next product once you " +
      "scan as many tags as labels were printed. That can be changed " +
      "in settings, and tag assignments can always be undone if a " +
      "mistake is made.",
  },
  {
    img: "/static/help/s5-edit.gif",
    text:
      "Wrong SKU or barcode on a product, or want to leave a scan " +
      "note? Click Edit product under the product card, fix just the " +
      "broken part (here a roman numeral becomes a plain II), and " +
      "save. Changes write to Shopify and History keeps the record.",
  },
  {
    img: "/static/help/s6-fixbarcode.gif",
    text:
      "Barcode not found? Some products never had one set, so their " +
      "barcode holds the SKU instead. Look the product up by its SKU " +
      "with Check product, confirm it's the right item, and Update " +
      "barcode adopts the code you scanned as its real barcode in " +
      "Shopify.",
  },
];
let helpIdx = 0;

function renderHelp() {
  const n = HELP_SLIDES.length;
  helpIdx = Math.max(0, Math.min(n - 1, helpIdx));
  const s = HELP_SLIDES[helpIdx];
  document.getElementById("help-img").src = s.img;
  document.getElementById("help-step").textContent =
    `Step ${helpIdx + 1} of ${n}`;
  // A slide with `html` gets markup (formatted examples); `text` stays
  // plain. html is authored in THIS file only — never user data.
  const cap = document.getElementById("help-text");
  if (s.html) cap.innerHTML = s.html;
  else cap.textContent = s.text;
  const prev = document.getElementById("help-prev");
  const next = document.getElementById("help-next");
  prev.disabled = helpIdx === 0;
  next.disabled = helpIdx === n - 1;
  document.getElementById("help-dots").innerHTML = HELP_SLIDES.map(
    (_, i) =>
      `<span class="help-dot${i === helpIdx ? " help-dot--on" : ""}"></span>`
  ).join("");
}

document.getElementById("help-open").addEventListener("click", () => {
  helpIdx = 0;
  renderHelp();
  document.getElementById("help-overlay").hidden = false;
});
document.getElementById("help-close").addEventListener("click", () => {
  document.getElementById("help-overlay").hidden = true;
});
document.getElementById("help-overlay").addEventListener("click", (e) => {
  if (e.target === e.currentTarget) e.currentTarget.hidden = true;
});
document.getElementById("help-prev").addEventListener("click", () => {
  helpIdx -= 1;
  renderHelp();
});
document.getElementById("help-next").addEventListener("click", () => {
  helpIdx += 1;
  renderHelp();
});
document.addEventListener("keydown", (e) => {
  if (document.getElementById("help-overlay").hidden) return;
  if (e.key === "ArrowLeft") { helpIdx -= 1; renderHelp(); }
  else if (e.key === "ArrowRight") { helpIdx += 1; renderHelp(); }
  else if (e.key === "Escape") {
    document.getElementById("help-overlay").hidden = true;
    e.stopPropagation();
  }
}, true);

// --- Printer picker ----------------------------------------------------------
// One card per detected printer (rows come from agent check-ins — nothing
// is hand-typed). The choice is per device; queued jobs carry it so a
// multi-printer future routes correctly, and today's single Zebra keeps
// printing everything either way.
let selectedPrinter = localStorage.getItem("printerName") || "";
let printersCache = { at: 0, printers: [] };
let printerAfterPick = null; // continuation for a print held by the picker

async function fetchPrinters(force = false) {
  if (!force && Date.now() - printersCache.at < 30000)
    return printersCache.printers;
  try {
    const data = await apiJson("/api/printers");
    printersCache = { at: Date.now(), printers: data.printers || [] };
  } catch (err) {
    /* offline app — keep the stale list */
  }
  return printersCache.printers;
}

function printerBtnRender() {
  const btn = document.getElementById("printer-btn");
  if (!btn) return;
  // Retired 2026-09-24 (Nick): the header's printer STATUS chip opens
  // the picker now, so the separate icon button stays hidden.
  btn.hidden = true;
  btn.textContent = "🖨";
  btn.title = selectedPrinter
    ? `Printer: ${selectedPrinter} - click to change`
    : "Choose which printer prints this device's labels";
  btn.classList.toggle("printerbtn--set", !!selectedPrinter);
}

const PRINTER_SVG = `<svg viewBox="0 0 48 40" width="44" height="37" aria-hidden="true">
  <rect x="6" y="4" width="36" height="14" rx="2" fill="none" stroke="currentColor" stroke-width="2.4"/>
  <rect x="2" y="16" width="44" height="14" rx="3" fill="none" stroke="currentColor" stroke-width="2.4"/>
  <circle cx="40" cy="23" r="2" fill="currentColor"/>
  <rect x="12" y="28" width="24" height="9" fill="none" stroke="currentColor" stroke-width="2.2"/>
  <line x1="16" y1="32.5" x2="32" y2="32.5" stroke="currentColor" stroke-width="1.6"/>
</svg>`;

let printerPickSel = "";

function renderPrinterCards(printers) {
  const wrap = document.getElementById("printer-cards");
  const confirmBtn = document.getElementById("printer-confirm");
  wrap.innerHTML = "";
  if (!printers.length) {
    wrap.innerHTML =
      `<p class="linkbox__text u-span-all">No printers detected yet. ` +
      `Start <span class="mono">print_agent.py</span> on the PC next to a printer and it registers itself here.</p>`;
    confirmBtn.disabled = true;
    return;
  }
  printers.forEach((p) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className =
      "printercard" + (p.name === printerPickSel ? " printercard--sel" : "");
    card.innerHTML =
      `<span class="printercard__icon">${PRINTER_SVG}</span>` +
      `<span class="printercard__name">${escapeHtml(p.name)}</span>` +
      (p.kind
        ? `<span class="printercard__kind">${escapeHtml(p.kind)}</span>`
        : "") +
      `<span class="printercard__dot ${p.online ? "printercard__dot--ok" : ""}">${
        p.online
          ? "● online"
          : p.last_seen_seconds != null
            ? `○ offline · seen ${fmtAgo(p.last_seen)}`
            : "○ never seen"
      }</span>`;
    card.addEventListener("click", () => {
      printerPickSel = p.name;
      renderPrinterCards(printers);
      confirmBtn.disabled = false;
      confirmBtn.textContent = p.online
        ? "Use this printer"
        : "Use it anyway (offline - labels wait)";
      confirmBtn.classList.add("print__btn--armed");
    });
    wrap.append(card);
  });
  confirmBtn.disabled = !printerPickSel;
}

async function openPrinterPicker(afterPick) {
  printerAfterPick = afterPick || null;
  printerPickSel = selectedPrinter;
  document.getElementById("printer-msg").textContent = "";
  document.getElementById("printer-overlay").hidden = false;
  renderPrinterAgentHealth();
  renderPrinterCards(await fetchPrinters(true));
}

// True when printing can proceed without asking: nothing registered yet
// (queue exactly as before the picker existed), or a live selection.
async function printerReady() {
  const printers = await fetchPrinters();
  if (!printers.length) return true;
  const sel = printers.find((p) => p.name === selectedPrinter);
  return !!(sel && sel.online);
}

document.getElementById("printer-btn").addEventListener("click", () =>
  openPrinterPicker(null)
);
document
  .getElementById("printer-cancel")
  .addEventListener("click", () => {
    document.getElementById("printer-overlay").hidden = true;
    printerAfterPick = null;
  });
document.getElementById("printer-overlay").addEventListener("click", (e) => {
  if (e.target === e.currentTarget) {
    e.currentTarget.hidden = true;
    printerAfterPick = null;
  }
});
document
  .getElementById("printer-confirm")
  .addEventListener("click", () => {
    if (!printerPickSel) return;
    selectedPrinter = printerPickSel;
    localStorage.setItem("printerName", selectedPrinter);
    printerBtnRender();
    document.getElementById("printer-overlay").hidden = true;
    const go = printerAfterPick;
    printerAfterPick = null;
    if (go) go();
  });
document
  .getElementById("printer-detect")
  .addEventListener("click", async () => {
    const before = new Set(printersCache.printers.map((p) => p.name));
    const printers = await fetchPrinters(true);
    renderPrinterCards(printers);
    const fresh = printers.filter((p) => !before.has(p.name));
    // The full working command - the bare "--printer-id" hint sent Nick
    // into argparse/401 errors (2026-08-26). The agent key is a secret
    // the page can't print; the warehouse PC's print_agent_loop.cmd has
    // it.
    document.getElementById("printer-msg").textContent = fresh.length
      ? `Detected: ${fresh.map((p) => p.name).join(", ")} ✓`
      : "No new printers found. On the PC beside the new printer run: " +
        `py print_agent.py --app ${location.origin} --agent-key ` +
        `<PRINT_AGENT_KEY - copy it from print_agent_loop.cmd on the ` +
        `warehouse PC> --printer-name "<its Windows printer name>" ` +
        "--printer-id <name for this picker> (add --no-rfid if it has " +
        "no RFID encoder). It appears here on its next check-in.";
  });
printerBtnRender();

// --- Big print run confirm (>10 labels of one product) ----------------------
const bigprintOverlay = document.getElementById("bigprint-overlay");
const bigprintAck = document.getElementById("bigprint-ack");
const bigprintGo = document.getElementById("bigprint-go");
let bigprintQty = 0;

function openBigPrint(qty) {
  bigprintQty = qty;
  document.getElementById("bigprint-text").textContent =
    `Print ${qty} labels for ${
      pendingProduct?.sku || pendingProduct?.product_title || "this product"
    }? Each one is a live RFID sticker.`;
  document.getElementById("bigprint-ack-text").textContent =
    `Yes - print all ${qty}`;
  bigprintAck.checked = false;
  bigprintGo.disabled = true;
  bigprintOverlay.hidden = false;
}

bigprintAck.addEventListener(
  "change",
  () => (bigprintGo.disabled = !bigprintAck.checked)
);
document
  .getElementById("bigprint-cancel")
  .addEventListener("click", () => (bigprintOverlay.hidden = true));
bigprintOverlay.addEventListener("click", (e) => {
  if (e.target === e.currentTarget) bigprintOverlay.hidden = true;
});
bigprintGo.addEventListener("click", () => {
  bigprintOverlay.hidden = true;
  queueLabels(bigprintQty, true);
});

// Poll the queued jobs until they all finish (or we give up watching —
// the agent keeps printing regardless).
async function watchPrintJobs(ids) {
  const started = Date.now();
  const idsParam = ids.join(",");
  while (Date.now() - started < 120000) {
    try {
      const res = await apiFetch(`/api/print-jobs?ids=${idsParam}`);
      if (res.ok) {
        const { jobs } = await res.json();
        const done = jobs.filter((j) => j.status === "done").length;
        const failed = jobs.filter((j) => j.status === "error");
        const waiting = jobs.length - done - failed.length;
        el.printStatus.textContent = failed.length
          ? `${done}/${jobs.length} printed, ${failed.length} FAILED: ${
              failed[0].error || "printer error"
            }`
          : waiting
          ? `Printing… ${done}/${jobs.length}`
          : `Printed ${done}/${jobs.length} ✓`;
        // Mirror the final outcome to the top status line, where the
        // operator is actually looking.
        if (!waiting) {
          setResult(
            failed.length
              ? `Label FAILED: ${failed[0].error || "printer error"}`
              : `Label printed ✓ - scan the RFID tag.`,
            failed.length ? "err" : "ok"
          );
          if (pendingProduct) loadTags(pendingProduct);
          loadRecent();
          return;
        }
      }
    } catch (err) {
      /* transient — keep polling */
    }
    await new Promise((r) => setTimeout(r, 2500));
  }
  el.printStatus.textContent += " (still queued - agent will print when up)";
}

// --- Step 2: rfid -> save assignment ---------------------------------------
el.rfid.addEventListener("keydown", async (event) => {
  if (event.key !== "Enter") return;
  const rfid = el.rfid.value.trim();
  if (!rfid || !pendingProduct) return;
  await stationTagScan(rfid);
});

// Callable form of the RFID-input Enter handler — the C72 LINK relay path.
async function stationTagScan(rfid) {
  if (!pendingProduct) {
    setResult("No product loaded - scan a barcode first.", "err", "rfid");
    return;
  }
  const operator = requireOperator();
  if (!operator) return;

  setResult("Saving assignment…", "busy", "rfid");
  try {
    const payload = { rfid_id: rfid, ...pendingProduct, assigned_by: operator };
    // Serialized brands: store the operator's preferred name as the title
    // (it already names the size, so the variant column would just repeat it).
    if (pendingProduct.serial_prefix) {
      const name = el.serialLabelInput.value.trim();
      if (name) {
        payload.product_title = name;
        payload.variant_title = null;
      }
      saveSerialLabel(false);
    }
    const res = await apiFetch("/api/rfid-assignments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (res.status === 409) {
      setResult(`Tag ${rfid} is already assigned.`, "err", "rfid");
      el.rfid.select();
      return;
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setResult(body.detail || "RFID tag save failed.", "err", "rfid");
      return;
    }
    const saved = await res.json();
    if (saved.suspect) {
      setResult(
        `Saved, but tag ${saved.rfid_id} is ${saved.rfid_id.length} ` +
          `characters (tags are normally 24) - likely a bad read. ` +
          `Re-scan this tag into inventory to be safe.`,
        "err",
        "rfid"
      );
    } else if (saved.warning) {
      // Over-pair guard (Nick, 2026-09-14): the pair stands, but this
      // product now has more tag records than stock explains - the
      // re-sticker-without-unlinking trap that made the bracket mess.
      setResult(
        `Assigned ${saved.rfid_id} → ${saved.product_title}. ` +
          `⚠ ${saved.warning}`,
        "warn-soft",
        "rfid"
      );
    } else {
      setResult(
        `Assigned ${saved.rfid_id} → ${saved.product_title}`,
        "ok",
        "rfid"
      );
    }
    prependRecent(saved);
    bulkTagged += 1;
    lastSweep = [saved.rfid_id];
    if (saved.suspect || saved.warning || !el.autoReset.checked) {
      // Keep the product loaded (stay-on-product mode, a flagged tag
      // to re-scan, or an over-pair warning that must be READ before
      // the station moves on).
      el.rfid.value = "";
      el.rfid.focus();
      loadTags(pendingProduct);
    } else if (bulkOn) {
      // Bulk scan: stay loaded — the printed-vs-tagged ledger decides
      // when this product is finished.
      el.rfid.value = "";
      el.rfid.focus();
      loadTags(pendingProduct);
      bulkCheckpoint();
    } else {
      // One tag per product: brief confirmation, then back to the barcode.
      setTimeout(resetStation, 700);
    }
  } catch (err) {
    setResult("Network error while saving the RFID tag.", "err", "rfid");
  }
}

// --- C72 LINK --------------------------------------------------------------
// The gun's LINK tab forwards every read here instead of acting on the gun:
// BT barcodes run the barcode path, trigger RFID reads run the tag path —
// identical to wedge input, every guard intact. Each scan's outcome is
// posted back so the gun can ding or buzz without the operator looking up.
let linkOn = false;
let linkCursor = -1;
let linkTimer = null;
let linkBusy = false;
// Per-page-load identity for presence (NOT sessionStorage: Chrome's
// "duplicate tab" copies sessionStorage and two tabs would share one id).
const linkTid = (crypto.randomUUID && crypto.randomUUID()) ||
  `t-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
let linkOthers = 0;
let linkSuspended = false;
const linkToggle = document.getElementById("link-toggle");
const linkStatus = document.getElementById("link-status");

function linkPresenceQS() {
  const op = (operatorEl && operatorEl.value) || "";
  return `tid=${encodeURIComponent(linkTid)}&op=${encodeURIComponent(op)}`;
}

// Gun scans act on EVERY listening terminal — the one warning that matters.
function renderLinkWarn() {
  if (!linkOn) return;
  const warn = linkOthers > 0;
  linkToggle.textContent = warn ? "C72 LINK: ON ⚠" : "C72 LINK: ON";
  linkToggle.classList.toggle("linkbar__btn--warn", warn);
  if (!warn) {
    linkStatus.textContent =
      linkStatus.textContent.replace(/ · ⚠ .*$/, "");
  }
  if (warn) {
    const n = linkOthers + 1;
    const note = ` · ⚠ ${n} terminals listening - labels can print twice.`;
    if (!linkStatus.textContent.includes("terminals listening")) {
      linkStatus.textContent =
        (linkStatus.textContent + note).slice(0, 200);
    }
  }
}

function linkGunStatusText(guns) {
  if (!guns.length) {
    return "Listening - no C72 checking in right now. Scans will act " +
      "here once a gun is on its LINK tab.";
  }
  const onLink = guns.filter((g) => g.tab === "link");
  if (onLink.length) {
    return `Listening - "${onLink[0].device}" is on its LINK tab. ` +
      "Gun scans act here now.";
  }
  if (guns.length === 1) {
    const tab = guns[0].tab ? ` (on the ${guns[0].tab} tab)` : "";
    return `Listening - "${guns[0].device}" is online${tab}. ` +
      "Open LINK on the gun.";
  }
  const names = guns.map((g) => `"${g.device}"`).join(", ");
  return `Listening - ${guns.length} guns online (${names}). ` +
    "Open LINK on one.";
}

function stationOutcome(where) {
  const target = where === "rfid" ? el.resultRfid : el.result;
  const cls = target.className || "";
  if (cls.includes("result--err")) {
    return { ok: false, text: target.textContent };
  }
  if (cls.includes("result--ok")) {
    return { ok: true, text: target.textContent };
  }
  // Still "busy" (or blank): the scan opened a window instead of settling —
  // unknown barcode, alias confirm, multi-box set. Human needed.
  return {
    ok: false,
    text: "Needs attention on the terminal screen (a window opened).",
  };
}

function linkRelease() {
  // Fire-and-forget: the TTL is the backstop if this never lands.
  apiFetch("/api/link/presence/release", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tid: linkTid }),
  }).catch(() => {});
}

function stopLink(msg) {
  linkOn = false;
  if (linkTimer) clearInterval(linkTimer);
  linkTimer = null;
  linkOthers = 0;
  linkSuspended = false;
  linkToggle.textContent = "C72 LINK: OFF";
  linkToggle.classList.remove("linkbar__btn--on", "linkbar__btn--warn");
  if (msg) linkStatus.textContent = msg;
  linkRelease();
}

// The relay serves whatever the screen shows (Nick's 2026-08-31 note):
// the Scan station, or the WHOLE Batch Tagging tab - an open batch at
// any step, a receiving list (barcode focuses the card, tag pairs to
// it), or the shipment sorter (a barcode is a sorter scan). Which
// surface acts is decided fresh for every scan from what is on screen.
function batchLinkMode() {
  if (document.getElementById("tab-batch").hidden) return null;
  if (
    !document.getElementById("batch-start").hidden &&
    !document.getElementById("sortship").hidden
  ) {
    return "sorter";
  }
  if (batch && batch.status !== "done" && batch.status !== "abandoned") {
    return isReceivingBatch() ? "receiving" : "batch";
  }
  return null;
}

function batchOutcome() {
  const cls = bEl.result.className || "";
  if (cls.includes("result--err")) {
    return { ok: false, text: bEl.result.textContent };
  }
  if (cls.includes("result--ok")) {
    return { ok: true, text: bEl.result.textContent };
  }
  // Still "busy" (or blank): a window opened mid-scan. Human needed.
  return {
    ok: false,
    text: "Needs attention on the terminal screen (a window opened).",
  };
}

async function actOnSorterLinkScan(s) {
  if (s.kind !== "barcode") {
    return {
      ok: false,
      text: "The sorter reads BARCODES - the tag read stayed on the gun.",
    };
  }
  const out = await sortShipScan(s.value);
  return out || { ok: false, text: "Scan skipped - the sorter was busy." };
}

// A relayed scan lands on whatever step the batch screen shows, through
// the same paths wedge input takes there - every guard intact.
async function actOnBatchLinkScan(s) {
  if (batchStage === "collect") {
    if (s.kind !== "barcode") {
      const t =
        "Collect counts boxes by BARCODE - the tag read stayed on the gun.";
      setBatchResult(t, "err");
      return { ok: false, text: t };
    }
    await batchCollectScan(s.value.trim());
    return batchOutcome();
  }
  if (batchStage === "pair") {
    if (s.kind === "barcode") {
      const item = matchBatchItem(s.value.trim());
      if (!item) {
        const t = `${s.value} doesn't match a product in this batch.`;
        setBatchResult(t, "err");
        return { ok: false, text: t };
      }
      pairActiveItemId = item.id;
      renderPairItems();
      renderPairCard();
      batchSound("ok");
      const t = `Active product: ${itemDisplayName(item)}`;
      setBatchResult(t, "ok");
      return { ok: true, text: t };
    }
    if (!pairActiveItemId) {
      const t =
        "Scan a product barcode from this batch first - then its tags.";
      setBatchResult(t, "err");
      return { ok: false, text: t };
    }
    await batchPairTag(s.value.trim());
    return batchOutcome();
  }
  if (batchStage === "verify") {
    if (s.kind === "barcode") {
      const t = "Verify collects TAG reads - the barcode stayed on the gun.";
      setBatchResult(t, "err");
      return { ok: false, text: t };
    }
    verifyEpcs.add(s.value.trim().toUpperCase());
    bEl.verifyCount.textContent = `${verifyEpcs.size} unique tags collected.`;
    const t = `Tag collected - ${verifyEpcs.size} unique tag(s) so far.`;
    setBatchResult(t, "ok");
    return { ok: true, text: t };
  }
  // labels (Check) and print have no scan action.
  const t =
    `No scan action on the ${batchStage === "labels" ? "Check" : "Print"} ` +
    "step - move the screen to Collect, Pair or Verify.";
  setBatchResult(t, "err");
  return { ok: false, text: t };
}

async function actOnReceivingLinkScan(s) {
  if (s.kind === "barcode") {
    const code = (s.value || "").trim().toUpperCase();
    let item = (batchItems || []).find((i) =>
      [i.barcode, i.sku, i.scanned_code].some(
        (v) => (v || "").trim().toUpperCase() === code
      )
    );
    if (!item) {
      // Aliases and rescued characters resolve server-side, then match
      // the shipment by SKU.
      try {
        const p = await apiJson(
          `/api/products/by-barcode/${encodeURIComponent(s.value)}`
        );
        const sku = (p.sku || "").trim().toUpperCase();
        if (sku)
          item = (batchItems || []).find(
            (i) => (i.sku || "").trim().toUpperCase() === sku
          );
      } catch (err) {
        /* falls through to not-in-shipment */
      }
    }
    if (!item) {
      const t = `${s.value} is not in this shipment.`;
      setBatchResult(t, "err");
      return { ok: false, text: t };
    }
    recvFocusId = item.id;
    renderReceivingList();
    const t = `${itemDisplayName(item)} focused - trigger on its stickers.`;
    setBatchResult(t, "ok");
    return { ok: true, text: t };
  }
  const item = (batchItems || []).find((i) => i.id === recvFocusId);
  if (!item) {
    const t =
      "No product focused - scan its barcode (or tap its card) first.";
    setBatchResult(t, "err");
    return { ok: false, text: t };
  }
  try {
    const r = await postJson(`/api/batches/${batch.id}/pair`, {
      epc: s.value,
      item_id: item.id,
      created_by: operatorEl.value || null,
    });
    // A companion label (box 2..N of a multi-box unit) confirms, never
    // counts - the unit already paired by box 1's tag.
    if (r.companion) {
      const t = r.message || "Companion label confirmed ✓ - not counted.";
      setBatchResult(t, "ok");
      return { ok: true, text: t };
    }
    recvRememberPairs([s.value], item.id, itemDisplayName(item));
    await pullBatch(false);
    const fresh =
      (batchItems || []).find((i) => i.id === item.id) || item;
    const t =
      r.message ||
      `paired to ${itemDisplayName(item)} ` +
        `(${fresh.paired_count}/${fresh.qty_scanned})` +
        (r.receiving_done ? " - shipment complete ✓" : "");
    setBatchResult(t, "ok");
    return { ok: true, text: t };
  } catch (err) {
    setBatchResult(err.message, "err");
    return { ok: false, text: err.message };
  }
}

async function actOnLinkScan(s) {
  let out;
  const mode = batchLinkMode();
  if (mode === "sorter") {
    out = await actOnSorterLinkScan(s);
  } else if (mode === "receiving") {
    out = await actOnReceivingLinkScan(s);
  } else if (mode === "batch") {
    out = await actOnBatchLinkScan(s);
  } else if (s.kind === "barcode") {
    el.barcode.value = s.value;
    await stationBarcodeScan(s.value);
    out = stationOutcome("barcode");
  } else if (!pendingProduct) {
    out = { ok: false, text: "No product loaded - scan a barcode first." };
    setResult(out.text, "err", "rfid");
  } else {
    el.rfid.value = s.value;
    await stationTagScan(s.value);
    out = stationOutcome("rfid");
  }
  linkStatus.textContent = `${s.value} → ${out.text}`.slice(0, 140);
  try {
    await apiFetch(`/api/link/scans/${s.id}/result`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ok: out.ok,
        outcome: (out.text || "").slice(0, 300),
      }),
    });
  } catch (err) {
    // The action already happened; a lost outcome just means no gun ding.
  }
}

async function pollLink() {
  if (!linkOn || linkBusy) return;
  // Only act while the Scan station is actually on screen — relayed scans
  // silently mutating a hidden browser tab or a backgrounded Scan tab
  // would be spooky (and print with nobody watching). Skipping also stops
  // presence stamping, so this terminal drops off other terminals'
  // listener counts within the TTL.
  // The Batch tab counts as "on screen" too while it shows something a
  // relayed scan can drive - an open batch at any step, receiving's
  // list, or the sorter (Nick, 2026-08-31).
  if (
    document.hidden ||
    (tabSections.scan[0].hidden && !batchLinkMode())
  ) {
    linkSuspended = true;
    return;
  }
  linkBusy = true;
  try {
    if (linkSuspended) {
      // Re-seat instead of polling forward: scans sent while nobody was
      // watching must be SKIPPED, never burst-replayed. The gun already
      // told the operator "delivered, no answer" for each of them.
      const res = await apiFetch(
        `/api/link/scans?after=-1&${linkPresenceQS()}`
      );
      if (!res.ok) return;
      const body = await res.json();
      const skipped = body.cursor - linkCursor;
      linkCursor = body.cursor;
      linkOthers = (body.listeners || []).length;
      linkSuspended = false;
      if (skipped > 0) {
        linkStatus.textContent =
          `Resumed - ${skipped} scan(s) sent while this screen was ` +
          "away were skipped.";
      }
      renderLinkWarn();
      return;
    }
    const res = await apiFetch(
      `/api/link/scans?after=${linkCursor}&${linkPresenceQS()}`
    );
    if (!res.ok) return;
    const body = await res.json();
    for (const s of body.scans) {
      await actOnLinkScan(s);
    }
    linkCursor = body.cursor;
    linkOthers = body.others || 0;
    renderLinkWarn();
  } catch (err) {
    // Poll again next tick.
  } finally {
    linkBusy = false;
  }
}

// Turning ON runs the in-use pre-check. interactive=false (the future
// auto-on seam) silently declines instead of asking.
async function startLink({ interactive } = { interactive: true }) {
  linkStatus.textContent = "Connecting…";
  try {
    // One request seats the cursor at "now" (pre-toggle scans never
    // replay), stamps this terminal, and reports everyone else.
    const res = await apiFetch(
      `/api/link/scans?after=-1&${linkPresenceQS()}`
    );
    const body = await res.json();
    const listeners = body.listeners || [];
    if (listeners.length) {
      const who = listeners
        .map((t) => `${t.operator || "no operator set"} ` +
          `(seen ${t.seen_seconds}s ago)`)
        .join(", ");
      const go = interactive && confirm(
        `Another terminal is already listening to the C72: ${who}.\n\n` +
        "Gun scans act on EVERY listening terminal - a label scan " +
        "would print twice.\n\nTurn LINK ON here anyway?"
      );
      if (!go) {
        linkRelease();
        linkStatus.textContent = interactive
          ? `Left OFF - ${listeners[0].operator || "another terminal"} ` +
            "is already listening."
          : "";
        return false;
      }
    }
    linkCursor = body.cursor;
    linkOn = true;
    linkToggle.textContent = "C72 LINK: ON";
    linkToggle.classList.add("linkbar__btn--on");
    linkStatus.textContent = linkGunStatusText(body.guns || []);
    linkOthers = listeners.length;
    renderLinkWarn();
    linkTimer = setInterval(pollLink, 1000);
    return true;
  } catch (err) {
    linkStatus.textContent = "Could not reach the server - try again.";
    return false;
  }
}

if (linkToggle) {
  linkToggle.addEventListener("click", () => {
    if (linkOn) {
      stopLink("Gun scans stay on the gun.");
      return;
    }
    startLink({ interactive: true });
  });
  // Coming back to a suspended tab: poll immediately rather than waiting
  // out the interval, so the "skipped N" note appears right away.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && linkOn) pollLink();
  });
}

// The Batch tab carries a mirrored copy of the LINK bar (Nick,
// 2026-08-31): same state, same click. The Scan tab's bar stays the
// single source of truth; this one reflects it via observers.
const linkToggleB = document.getElementById("link-toggle-b");
const linkStatusB = document.getElementById("link-status-b");
if (linkToggle && linkToggleB) {
  const mirrorLinkBar = () => {
    linkToggleB.textContent = linkToggle.textContent;
    linkToggleB.className = linkToggle.className;
    linkStatusB.textContent = linkStatus.textContent;
  };
  const watch = { childList: true, characterData: true, subtree: true };
  new MutationObserver(mirrorLinkBar).observe(linkToggle, {
    ...watch,
    attributes: true,
  });
  new MutationObserver(mirrorLinkBar).observe(linkStatus, watch);
  linkToggleB.addEventListener("click", () => linkToggle.click());
  mirrorLinkBar();
}

// --- Bulk scan --------------------------------------------------------------
// Apply many labels, then let the tags stream in — single reads (wedge or
// LINK) and pulled C72 sweeps both count against a printed-this-visit
// ledger that decides when the product is done. A strict subset of
// auto-reset: auto-reset OFF disables the chip, and the chip falls back to
// OFF every time a new product loads.
let bulkOn = false;
let bulkPrinted = 0; // labels queued while this product has been loaded
let bulkTagged = 0; // new tags assigned to it this visit
let lastSweep = []; // EPCs from the most recent assigning action
let bulkWarnedAt = -1; // over-count the operator already chose to keep
const bulkToggle = document.getElementById("bulk-toggle");
const bulkSweepBtn = document.getElementById("bulk-sweep");
const bulkProgress = document.getElementById("bulk-progress");
const bulkWarnEl = document.getElementById("bulk-warn");

function bulkVisitReset() {
  bulkOn = false;
  bulkPrinted = 0;
  bulkTagged = 0;
  lastSweep = [];
  bulkWarnedAt = -1;
  if (bulkWarnEl) bulkWarnEl.hidden = true;
  renderBulk();
}

function renderBulk() {
  if (!bulkToggle) return;
  const allowed = el.autoReset.checked;
  if (!allowed) bulkOn = false;
  bulkToggle.disabled = !allowed;
  bulkToggle.classList.toggle("chip-toggle--on", bulkOn);
  bulkToggle.textContent = bulkOn ? "⚡ BULK: ON" : "⚡ BULK: OFF";
  const active = bulkOn && !!pendingProduct;
  bulkSweepBtn.hidden = !active;
  bulkProgress.hidden = !active;
  syncBulkSweepPoll(active);
  if (active) {
    bulkProgress.textContent =
      bulkPrinted > 0
        ? `${bulkTagged} of ${bulkPrinted} label(s) printed this visit ` +
          `are tagged` +
          (bulkTagged < bulkPrinted
            ? ` - ${bulkPrinted - bulkTagged} to go.`
            : ".")
        : `${bulkTagged} tag(s) assigned · no labels printed this visit, ` +
          `so no auto-reset target - Reset (Esc) when done.`;
  }
}

bulkToggle.addEventListener("click", () => {
  if (bulkToggle.disabled) return;
  bulkOn = !bulkOn;
  bulkWarnEl.hidden = true;
  renderBulk();
});
el.autoReset.addEventListener("change", renderBulk);

// --- The sweep-waiting chip (Nick, 2026-08-26) ------------------------------
// While BULK is live the station watches for the gun's next link sweep:
// the chip says how many UNTAGGED tags the newest sweep holds (counted
// exactly like batch tagging counts a sweep), colored against the labels
// still unscanned in this bulk. Green = the sweep covers exactly what's
// left; yellow = a partial (or over-) sweep; red = nothing waiting, or a
// sweep of only already-tagged labels.
let bulkSweepTimer = null;
let bulkSweepSummary = null;

function syncBulkSweepPoll(active) {
  const note = document.getElementById("bulk-sweep-note");
  if (!note) return;
  if (!active) {
    if (bulkSweepTimer) clearInterval(bulkSweepTimer);
    bulkSweepTimer = null;
    bulkSweepSummary = null;
    note.hidden = true;
    return;
  }
  renderBulkSweepNote();
  if (!bulkSweepTimer) {
    pollBulkSweepNote();
    bulkSweepTimer = setInterval(pollBulkSweepNote, 4000);
  }
}

async function pollBulkSweepNote() {
  if (!(bulkOn && pendingProduct)) return;
  try {
    bulkSweepSummary = await apiJson("/api/epc-captures/latest-summary");
  } catch (err) {
    bulkSweepSummary = null;
  }
  renderBulkSweepNote();
}

function renderBulkSweepNote() {
  const note = document.getElementById("bulk-sweep-note");
  if (!note) return;
  if (!(bulkOn && pendingProduct)) {
    note.hidden = true;
    return;
  }
  const st = sweepNoteState(
    bulkSweepSummary,
    Math.max(0, bulkPrinted - bulkTagged)
  );
  note.className = `bulknote ${st.cls}`;
  note.textContent = st.text;
  note.hidden = false;
}

// The ledger's verdict after every assigning action: exact = done (reset
// as a single scan would), over = ask (a blank label may have been swept).
function bulkCheckpoint() {
  renderBulk();
  if (!bulkPrinted) return;
  if (bulkTagged === bulkPrinted) {
    setResult(
      `All ${bulkPrinted} label(s) printed this visit are tagged ✓ - ` +
        `resetting.`,
      "ok",
      "rfid"
    );
    bulkWarnEl.hidden = true;
    setTimeout(resetStation, 900);
  } else if (bulkTagged > bulkPrinted && bulkTagged > bulkWarnedAt) {
    document.getElementById("bulk-warn-text").textContent =
      `${bulkTagged} tag(s) assigned against ${bulkPrinted} label(s) ` +
      `printed this visit - a spare or blank label in range may have ` +
      `been swept and wrongly assigned. Undo removes only the ` +
      `${lastSweep.length} tag(s) this last action assigned.`;
    document.getElementById("bulk-warn-undo").textContent =
      `UNDO THIS SWEEP (${lastSweep.length})`;
    bulkWarnEl.hidden = false;
  }
}

bulkSweepBtn.addEventListener("click", async () => {
  if (!pendingProduct) return;
  const operator = requireOperator();
  if (!operator) return;
  bulkSweepBtn.disabled = true;
  try {
    const capRes = await apiFetch("/api/epc-captures/latest");
    if (capRes.status === 404) {
      setResult(
        "No C72 sweeps received yet - SWEEP then SEND on the gun first.",
        "err",
        "rfid"
      );
      return;
    }
    const cap = await capRes.json();
    const res = await postJson("/api/rfid-assignments/sweep", {
      epcs: cap.epcs || [],
      ...pendingProduct,
      assigned_by: operator,
    });
    (res.assigned || []).forEach(prependRecent);
    bulkTagged += res.count;
    if (res.count > 0) lastSweep = res.assigned.map((a) => a.rfid_id);
    const dup = (res.duplicates || []).length;
    setResult(
      `Sweep (${cap.epc_count} tag(s) heard): ${res.count} new assigned` +
        (dup ? ` · ${dup} already assigned - skipped` : "") +
        "." +
        (res.warning ? ` ⚠ ${res.warning}` : ""),
      res.warning ? "warn-soft" : res.count > 0 ? "ok" : "err",
      "rfid"
    );
    loadTags(pendingProduct);
    bulkCheckpoint();
  } catch (err) {
    setResult(err.message, "err", "rfid");
  } finally {
    bulkSweepBtn.disabled = false;
    // The pull consumed the sweep's orphans - refresh the chip now
    // rather than on the next 4s tick.
    pollBulkSweepNote();
  }
});

document
  .getElementById("bulk-warn-undo")
  .addEventListener("click", async () => {
    if (!lastSweep.length) {
      bulkWarnEl.hidden = true;
      return;
    }
    const btn = document.getElementById("bulk-warn-undo");
    btn.disabled = true;
    try {
      const res = await postJson("/api/rfid-assignments/sweep/undo", {
        epcs: lastSweep,
        sku: (pendingProduct && pendingProduct.sku) || null,
        by: operatorEl.value || null,
      });
      bulkTagged = Math.max(0, bulkTagged - res.count);
      for (const epc of res.epcs || []) {
        const li = el.recentList.querySelector(`li[data-rfid="${epc}"]`);
        if (li) li.remove();
      }
      lastSweep = [];
      bulkWarnEl.hidden = true;
      setResult(
        `Sweep undone - ${res.count} tag(s) unlinked (History has the ` +
          `receipt).`,
        "ok",
        "rfid"
      );
      if (pendingProduct) loadTags(pendingProduct);
      renderBulk();
    } catch (err) {
      setResult(err.message, "err", "rfid");
    } finally {
      btn.disabled = false;
    }
  });

document.getElementById("bulk-warn-keep").addEventListener("click", () => {
  bulkWarnedAt = bulkTagged; // don't re-ask until the count grows again
  bulkWarnEl.hidden = true;
  setResult("Kept - the over-count stands.", "ok", "rfid");
});

// --- Recent list -----------------------------------------------------------
// Per-BOX conditions (Nick, 2026-09-16) - mirror of the server's
// BOX_CONDITIONS; the server validates, this list just draws.
const BOX_CONDITIONS = {
  "good": "Good",
  "open-box": "Open Box",
  "used": "Used",
  "damaged": "Damaged",
  "needs-parts": "Needs Parts",
  "display": "Display Only",
  "safety-stock": "Safety Stock",
};

function conditionSelect(a) {
  const sel = document.createElement("select");
  sel.className = "condsel";
  sel.title =
    "This BOX's condition - rides the tag through retire/return. " +
    "Good is the default.";
  for (const [slug, label] of Object.entries(BOX_CONDITIONS)) {
    const o = document.createElement("option");
    o.value = slug;
    o.textContent = label;
    sel.append(o);
  }
  sel.value = a.condition || "good";
  sel.classList.toggle("condsel--set", !!a.condition);
  sel.addEventListener("change", async () => {
    sel.disabled = true;
    try {
      const r = await postJson(
        `/api/tags/${encodeURIComponent(a.rfid_id)}/condition`,
        { condition: sel.value, worker: operatorEl.value || null }
      );
      a.condition = r.condition;
      sel.classList.toggle("condsel--set", !!r.condition);
      setResult(r.message, "ok");
    } catch (err) {
      sel.value = a.condition || "good";
      setResult(err.message, "err");
    }
    sel.disabled = false;
  });
  return sel;
}

function recentRow(a) {
  const li = document.createElement("li");
  li.dataset.rfid = a.rfid_id;
  const when = a.assigned_at
    ? tsDate(a.assigned_at).toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      })
    : "—";
  li.innerHTML = `
    <span class="recent__epc">${escapeHtml(a.rfid_id)}</span>${
      a.suspect
        ? '<span class="suspect" title="Tag doesn\'t look like a normal ' +
          '24-character EPC - probably a bad read. Re-scan this tag into ' +
          'inventory.">⚠</span>'
        : ""
    }
    <span class="recent__prod">${escapeHtml(a.product_title || "")}${
      a.variant_title ? " (" + escapeHtml(a.variant_title) + ")" : ""
    }</span>
    <span class="binlabel">${escapeHtml(a.bin_location || "")}</span>
    <span class="recent__meta recent__when">${escapeHtml(when)}</span>
    <button class="recent__unassign" type="button">unassign</button>
  `;
  li.querySelector(".recent__when").before(conditionSelect(a));
  li.querySelector(".recent__unassign").addEventListener("click", () =>
    unassign(a.rfid_id, li)
  );
  return li;
}

function prependRecent(a) {
  const empty = el.recentList.querySelector(".recent__empty");
  if (empty) empty.remove();
  el.recentList.prepend(recentRow(a));
}

async function loadRecent(query = "") {
  try {
    // The Inventory tab is the full view; this list is just a live tail
    // of the last few scans (searches get more room).
    const url = query
      ? `/api/rfid-assignments?q=${encodeURIComponent(query)}&limit=50`
      : "/api/rfid-assignments?limit=10";
    const res = await apiFetch(url);
    if (!res.ok) return;
    const data = await res.json();
    el.recentList.innerHTML = "";
    if (!data.assignments.length) {
      el.recentList.innerHTML =
        '<li class="recent__empty">No assignments yet.</li>';
      return;
    }
    data.assignments.forEach((a) => el.recentList.append(recentRow(a)));
  } catch (err) {
    // Database not configured yet during Phase 1 — leave the list empty.
  }
}

async function unassign(rfid, li) {
  if (!confirm(`Unassign tag ${rfid}?`)) return;
  const res = await apiFetch(
    `/api/rfid-assignments/${encodeURIComponent(rfid)}`,
    { method: "DELETE" }
  );
  if (res.ok) li.remove();
}

// --- Inventory tab ----------------------------------------------------------
let inventoryRows = [];

// A text box that drops a list of the values actually present, narrowing
// as you type. Selection is exact-match filtering; free text narrows the
// list without filtering the table until something is picked.
function makeCombo(id, onPick) {
  const root = document.getElementById(id);
  const input = root.querySelector(".combo__input");
  const list = root.querySelector(".combo__list");
  const clear = root.querySelector(".combo__clear");
  let options = [];
  let value = "";

  function close() {
    list.hidden = true;
  }

  function open() {
    const typed = input.value.trim().toLowerCase();
    const shown = options.filter(
      (o) => !typed || o.label.toLowerCase().includes(typed)
    );
    list.innerHTML = "";
    if (!shown.length) {
      list.innerHTML = '<li class="combo__none">No matches</li>';
    } else {
      shown.slice(0, 200).forEach((o) => {
        const li = document.createElement("li");
        if (o.label === value) li.classList.add("combo--on");
        li.innerHTML =
          escapeHtml(o.label) +
          (o.count != null
            ? `<span class="combo__count">${o.count}</span>`
            : "");
        li.addEventListener("mousedown", (ev) => {
          ev.preventDefault(); // don't blur before we read the click
          value = o.label;
          input.value = o.label;
          close();
          onPick(value);
        });
        list.append(li);
      });
    }
    list.hidden = false;
  }

  input.addEventListener("focus", open);
  input.addEventListener("input", () => {
    open();
    // Typing a value that exactly matches an option applies it; otherwise
    // clearing the box clears the filter.
    const typed = input.value.trim();
    const exact = options.find(
      (o) => o.label.toLowerCase() === typed.toLowerCase()
    );
    const next = exact ? exact.label : "";
    if (next !== value) {
      value = next;
      onPick(value);
    }
  });
  input.addEventListener("blur", () => setTimeout(close, 120));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      close();
      input.blur();
    }
  });
  clear.addEventListener("click", () => {
    input.value = "";
    value = "";
    close();
    onPick("");
    input.focus();
  });

  return {
    setOptions(next) {
      options = next;
      // A chosen value that no longer exists shouldn't hide everything.
      if (value && !options.some((o) => o.label === value)) {
        value = "";
        input.value = "";
        onPick("");
      }
    },
    get value() {
      return value;
    },
  };
}

let invBinCombo = null;
let invVendorCombo = null;
let invBinFilter = "";
let invVendorFilter = "";
// Client-side paging (50 rows): position + the filter signature that
// resets it back to page 1 when the visible set changes.
let invPage = 0;
let invPageSig = "";

// Freshness tags (Nick, 2026-09-08): snapshot data paints instantly
// under a yellow "last refreshed" tag, then flips to a green checkmark
// when the live numbers land.
function agoText(ms) {
  if (ms == null || ms < 0) return "just now";
  const m = Math.round(ms / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.round(h / 24)} d ago`;
}

function setFreshTag(id, live, ageMs) {
  const tag = document.getElementById(id);
  if (!tag) return;
  tag.hidden = false;
  tag.classList.toggle("freshtag--live", !!live);
  tag.textContent = live
    ? "Up to date ✓"
    : `Showing saved numbers - last refreshed ${agoText(ageMs)}`;
}

function applyInventoryData(data) {
  inventoryRows = data.products;
  // Offer only values that exist, with how many products each covers.
  const countBy = (key) => {
    const m = new Map();
    inventoryRows.forEach((p) => {
      if (p[key]) m.set(p[key], (m.get(p[key]) || 0) + 1);
    });
    return m;
  };
  const binCounts = countBy("bin_location");
  const vendorCounts = countBy("vendor");
  if (invBinCombo)
    invBinCombo.setOptions(
      (data.bins || []).map((b) => ({ label: b, count: binCounts.get(b) }))
    );
  if (invVendorCombo)
    invVendorCombo.setOptions(
      (data.vendors || []).map((v) => ({
        label: v,
        count: vendorCounts.get(v),
      }))
    );
  renderInventory();
}

async function loadInventory() {
  const body = document.getElementById("inv-body");
  let livePainted = false;
  loadBundles(); // the panel's badge + cards (DB-only, fast)
  // Instant paint from the bin-map snapshot (the live Shopify walk
  // takes ~14s; nobody should stare at "Loading…" for it).
  apiFetch("/api/inventory/summary?fast=1")
    .then(async (r) => {
      if (!r.ok || livePainted) return;
      const d = await r.json();
      if (livePainted) return;
      applyInventoryData(d);
      setFreshTag(
        "inv-fresh", false,
        d.onhand_age_minutes == null ? null : d.onhand_age_minutes * 60000
      );
    })
    .catch(() => {});
  try {
    const res = await apiFetch("/api/inventory/summary");
    if (!res.ok) {
      if (!livePainted)
        body.innerHTML =
          '<tr><td colspan="7" class="inventory__empty">Could not load inventory.</td></tr>';
      return;
    }
    const data = await res.json();
    livePainted = true;
    applyInventoryData(data);
    setFreshTag("inv-fresh", true);
  } catch (err) {
    // The snapshot (if it painted) stays up with its yellow tag.
    if (!document.getElementById("inv-fresh").hidden) return;
    body.innerHTML =
      '<tr><td colspan="7" class="inventory__empty">Network error.</td></tr>';
  }
}

// --- bundles (Nick, 2026-09-28 round 12) ------------------------------------
// Bundle listings live in bundles.app; here they become RFID records
// pointing at the component products that carry the tags. One pull
// builds them all; add/remove edits go through the same wholesale
// replace the product panel uses, so History gets its receipt.
let bndlData = null;

async function loadBundles() {
  try {
    bndlData = await apiJson("/api/bundles");
  } catch (err) {
    bndlData = null;
  }
  renderBundles();
}

function renderBundles() {
  const cnt = document.getElementById("bndl-count");
  const list = document.getElementById("bndl-list");
  const meta = document.getElementById("bndl-meta");
  if (!cnt || !list) return;
  if (!bndlData) {
    list.innerHTML =
      '<p class="result result--err">Could not load bundles.</p>';
    return;
  }
  cnt.textContent = bndlData.count ? String(bndlData.count) : "";
  let last = "";
  try {
    const lp = JSON.parse(bndlData.last_pull || "null");
    if (lp && lp.at) last = ` · last pulled ${fmtAgo(lp.at)}`;
  } catch (e) { /* stamp is decoration */ }
  meta.textContent = `${bndlData.count} bundle(s) defined${last}`;
  list.innerHTML = bndlData.bundles.length
    ? bndlData.bundles.map(bndlCardHtml).join("")
    : `<p class="result">No bundles defined yet. Pull from bundles.app
       to build them all at once, or define one from a product panel's
       Bundle row.</p>`;
}

function bndlCardHtml(b) {
  const rows = b.contents
    .map(
      (c) => `
    <div class="bndl__row">
      <span class="bndl__qty">${c.qty}&#215;</span>
      <button class="reset bndl__comp" type="button"
        data-sku="${escapeHtml(c.component_sku)}"
        title="Open the product card">${escapeHtml(c.component_sku)}</button>
      <span class="bndl__ctitle">${escapeHtml(c.title || "")}</span>
      <span class="bndl__facts">${
        c.bin ? `${escapeHtml(c.bin)} · ` : ""
      }${c.tags} tag(s)${
        c.on_hand != null ? ` · ${c.on_hand} on hand` : ""
      }</span>
      <button class="reset bndl-rm" type="button"
        data-bundle="${escapeHtml(b.bundle_sku)}"
        data-comp="${escapeHtml(c.component_sku)}"
        title="Remove this component from the bundle">&#215;</button>
    </div>`
    )
    .join("");
  return `<div class="bndl__card" data-bundle="${escapeHtml(b.bundle_sku)}">
    <div class="bndl__top">
      <span class="bndl__title">${escapeHtml(
        b.title || b.bundle_sku
      )}</span>
      <button class="reset bndl__sku" type="button"
        data-sku="${escapeHtml(b.bundle_sku)}"
        title="Open the bundle's product card">${escapeHtml(b.bundle_sku)}</button>
      <span class="bndl__src${b.source === "app" ? "" : " bndl__src--man"}">${
        b.source === "app" ? "bundles.app" : "manual"
      }</span>
      ${b.excluded ? '<span class="bndl__src bndl__src--man">dropped from RFID</span>' : ""}
      <span class="bndl__grow"></span>
      ${
        b.buildable != null
          ? `<span class="bndl__build" title="How many units the components on hand can build">buildable: <b>${b.buildable}</b></span>`
          : ""
      }
    </div>
    ${rows}
    <div class="bndl__addrow">
      <input class="recent__search bndl-addsku" type="text"
             placeholder="Component SKU" autocomplete="off" />
      <input class="recent__search bndl-addqty" type="number" min="1"
             value="1" title="Units per bundle" />
      <button class="reset bndl-add" type="button"
        data-bundle="${escapeHtml(b.bundle_sku)}">+ Add component</button>
    </div>
  </div>`;
}

async function bndlWrite(bundleSku, contents) {
  const r = await postJson("/api/bundle-contents", {
    bundle_sku: bundleSku,
    contents,
    updated_by: operatorEl.value || null,
  });
  await loadBundles();
  return r;
}

document.getElementById("inv-bundles-btn").addEventListener("click", () => {
  const box = document.getElementById("inv-bundles");
  box.hidden = !box.hidden;
  if (!box.hidden && !bndlData) loadBundles();
});

document.getElementById("bndl-pull").addEventListener("click", async (ev) => {
  const btn = ev.currentTarget;
  const stopDots = startDots(btn, "Walking bundles.app");
  btn.disabled = true;
  try {
    const r = await postJson("/api/bundles/pull", {
      updated_by: operatorEl.value || null,
    });
    document.getElementById("bndl-meta").textContent = r.message;
    await loadBundles();
    document.getElementById("bndl-meta").textContent = r.message;
  } catch (err) {
    alert(err.message);
  }
  stopDots();
  btn.disabled = false;
});

document.getElementById("bndl-list").addEventListener("click", async (e) => {
  const open = e.target.closest(".bndl__comp, .bndl__sku");
  if (open) {
    openProductCard(open.dataset.sku);
    return;
  }
  const b = (sku) =>
    (bndlData.bundles || []).find((x) => x.bundle_sku === sku);
  const rm = e.target.closest(".bndl-rm");
  if (rm) {
    const bundle = b(rm.dataset.bundle);
    if (!bundle) return;
    const left = bundle.contents.filter(
      (c) => c.component_sku !== rm.dataset.comp
    );
    if (
      !left.length &&
      !window.confirm(
        `${rm.dataset.comp} is the last component - removing it clears ` +
          `the definition and ${bundle.bundle_sku} becomes a countable ` +
          `product again. Continue?`
      )
    )
      return;
    rm.disabled = true;
    try {
      await bndlWrite(
        bundle.bundle_sku,
        left.map((c) => ({ component_sku: c.component_sku, qty: c.qty }))
      );
    } catch (err) {
      alert(err.message);
      rm.disabled = false;
    }
    return;
  }
  const add = e.target.closest(".bndl-add");
  if (add) {
    const card = add.closest(".bndl__card");
    const sku = card.querySelector(".bndl-addsku").value.trim().toUpperCase();
    const qty = parseInt(card.querySelector(".bndl-addqty").value, 10) || 0;
    if (!sku || qty < 1) {
      alert("Component SKU and a quantity of at least 1, please.");
      return;
    }
    const bundle = b(add.dataset.bundle);
    if (!bundle) return;
    const contents = bundle.contents
      .filter((c) => c.component_sku.toUpperCase() !== sku)
      .map((c) => ({ component_sku: c.component_sku, qty: c.qty }));
    contents.push({ component_sku: sku, qty });
    add.disabled = true;
    try {
      await bndlWrite(bundle.bundle_sku, contents);
    } catch (err) {
      alert(err.message);
      add.disabled = false;
    }
  }
});

// Link to a product's page in Shopify admin. Real GIDs go straight to
// the product page; legacy "handle:…" ids (old TELCAN-sourced rows —
// the ZWO ASIAIR bracket case) and missing ids fall back to admin's
// product list FILTERED to the SKU/handle, so every product links
// somewhere useful instead of staying plain text (Nick, 2026-08-18).
function adminProductUrl(pid, sku) {
  const shop = document.body.dataset.shop;
  if (!shop) return null;
  const m = String(pid || "").match(/(?:gid:\/\/shopify\/Product\/)?(\d+)$/);
  if (m) return `https://admin.shopify.com/store/${shop}/products/${m[1]}`;
  const q =
    (sku || "").trim() ||
    (String(pid || "").startsWith("handle:")
      ? String(pid).slice(7).trim()
      : "");
  return q
    ? `https://admin.shopify.com/store/${shop}/products?query=${encodeURIComponent(q)}`
    : null;
}

function productLink(title, pid, sku) {
  const url = adminProductUrl(pid, sku);
  const name = escapeHtml(title || "");
  return url
    ? `<a class="prodlink" href="${url}" target="_blank" rel="noopener" title="Open in Shopify admin">${name}</a>`
    : name;
}

function renderInventory() {
  const body = document.getElementById("inv-body");
  const countEl = document.getElementById("inv-count");
  const q = document.getElementById("inv-search").value.trim().toLowerCase();
  let rows = inventoryRows.filter((p) => {
    if (
      invBinFilter &&
      (p.bin_location || "").toLowerCase() !== invBinFilter.toLowerCase()
    )
      return false;
    if (
      invVendorFilter &&
      (p.vendor || "").toLowerCase() !== invVendorFilter.toLowerCase()
    )
      return false;
    if (!q) return true;
    return [p.product_title, p.variant_title, p.sku, p.barcode, p.vendor]
      .filter(Boolean)
      .some((v) => String(v).toLowerCase().includes(q));
  });

  const sort = document.getElementById("inv-sort").value;
  const byText = (a, b, key) =>
    String(a[key] || "￿").localeCompare(String(b[key] || "￿"),
      undefined, { numeric: true, sensitivity: "base" });
  rows = [...rows];
  if (sort === "vendor")
    // Products with no vendor sort last rather than pretending to be "".
    rows.sort((a, b) => byText(a, b, "vendor") ||
      byText(a, b, "product_title"));
  else if (sort === "product") rows.sort((a, b) => byText(a, b, "product_title"));
  else if (sort === "bin") rows.sort((a, b) => byText(a, b, "bin_location"));
  else if (sort === "tags") rows.sort((a, b) => b.tag_count - a.tag_count);
  else
    rows.sort((a, b) =>
      String(b.last_assigned_at || "").localeCompare(
        String(a.last_assigned_at || "")
      )
    );

  const filtered = invBinFilter || invVendorFilter || q;
  countEl.textContent = filtered
    ? `(${rows.length} of ${inventoryRows.length})`
    : `(${inventoryRows.length})`;

  const pager = document.getElementById("inv-pager");
  if (!rows.length) {
    pager.hidden = true;
    body.innerHTML = `<tr><td colspan="7" class="inventory__empty">${
      filtered
        ? "Nothing matches those filters."
        : "No products yet - assign or print a first tag."
    }</td></tr>`;
    return;
  }

  // Pages of 50 (Nick, 2026-09-08): the DATA loads once; only the
  // rendering pages, so the tab paints instantly. Filters, search and
  // sort jump back to page 1 (their result set is a new list).
  const PAGE = 50;
  const sig = [q, invBinFilter, invVendorFilter, sort, rows.length].join("");
  if (sig !== invPageSig) {
    invPage = 0;
    invPageSig = sig;
  }
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  invPage = Math.min(invPage, pages - 1);
  const pageRows = rows.slice(invPage * PAGE, invPage * PAGE + PAGE);
  if (pages > 1) {
    pager.hidden = false;
    pager.innerHTML =
      `<button class="reset" id="inv-prev" type="button" ${invPage === 0 ? "disabled" : ""}>← Prev</button>` +
      `<span class="u-fs13">rows ${invPage * PAGE + 1}–${Math.min(rows.length, (invPage + 1) * PAGE)} of ${rows.length} · page ${invPage + 1} of ${pages}</span>` +
      `<button class="reset" id="inv-next" type="button" ${invPage >= pages - 1 ? "disabled" : ""}>Next →</button>`;
    pager.querySelector("#inv-prev").addEventListener("click", () => {
      invPage = Math.max(0, invPage - 1);
      renderInventory();
    });
    pager.querySelector("#inv-next").addEventListener("click", () => {
      invPage = Math.min(pages - 1, invPage + 1);
      renderInventory();
    });
  } else {
    pager.hidden = true;
    pager.innerHTML = "";
  }
  body.innerHTML = pageRows
    .map((p) => {
      const title =
        productLink(p.product_title, p.shopify_product_id, p.sku) +
        (p.variant_title
          ? ` <span class="inventory__variant">(${escapeHtml(p.variant_title)})</span>`
          : "") +
        (p.rfid_incompatible
          ? ' <span class="noscan-chip" title="tag won\'t scan when on ' +
            'box - sweeps don\'t expect it to answer">⊘ no RFID</span>'
          : "") +
        "";
      const when = p.last_assigned_at
        ? tsDate(p.last_assigned_at).toLocaleString(undefined, {
            dateStyle: "medium",
            timeStyle: "short",
          })
        : "—";
      return `<tr>
        <td>${title}</td>
        <td>${escapeHtml(p.vendor || "—")}</td>
        <td class="mono">${
          p.sku
            ? `<span class="skulink" data-sku="${escapeHtml(p.sku)}" title="Open this product - label editor, RFID flag, full history">${escapeHtml(p.sku)}</span>`
            : "—"
        }</td>
        <td>${p.bin_location && p.bin_location !== "No bin assigned"
          ? `<span class="inventory__bin">${escapeHtml(p.bin_location)}</span>`
          : "—"}${
          p.bin_differs && p.sku
            ? ` <button class="binfix inv-setbin" type="button" data-sku="${escapeHtml(
                p.sku
              )}" data-bin="${escapeHtml(
                p.bin_location
              )}" data-was="${escapeHtml(
                p.shopify_bin || "nothing"
              )}" title="Shopify's bin says ${escapeHtml(
                p.shopify_bin || "nothing"
              )}, but this product's tags were placed at ${escapeHtml(
                p.bin_location
              )}. Click to write ${escapeHtml(
                p.bin_location
              )} to Shopify (audited, undoable via History).">⇢ Shopify</button>`
            : ""
        }</td>
        <td class="num">${
          p.box_parts
            ? `${p.unit_count}<div class="inv__cases" title="Unit count is the smallest box count - every box identity must be present for a sellable unit">${p.box_parts
                .map((bp) => `${escapeHtml(bp.sku)}: ${bp.units}`)
                .join(" · ")}</div>`
            : p.unit_breakdown
              ? `${p.unit_count}<div class="inv__cases" title="${escapeHtml(
                  caseHint(p)
                )}">${escapeHtml(p.unit_breakdown)}</div>`
              : p.tag_count
        }</td>
        <td class="num">${p.shopify_qty ?? "—"}</td>
        <td>${escapeHtml(when)}</td>
      </tr>`;
    })
    .join("");
}

// Inventory rows open the same product panel History uses — label editor,
// preview, RFID flag and paper trail in one place.
document.getElementById("inv-body").addEventListener("click", async (e) => {
  // Tag placement is a physical fact; when Shopify's bin disagrees, this
  // writes the tags' bin to Shopify via the normal audited update.
  const fix = e.target.closest(".inv-setbin");
  if (fix) {
    const { sku, bin, was } = fix.dataset;
    if (
      !confirm(
        `Set the Shopify bin for ${sku} to ${bin}?\n\n` +
          `Shopify currently says: ${was}. This is the normal audited ` +
          `bin write - Shopify, the bin map and this product's tags all ` +
          `follow, with a History entry.`
      )
    )
      return;
    fix.disabled = true;
    try {
      await postJson("/api/bin-updates", {
        target: sku,
        bin,
        changed_by: operatorEl.value || null,
      });
      fix.textContent = "✓ written";
      await loadInventory();
    } catch (err) {
      alert(`Bin update failed: ${err.message}`);
      fix.disabled = false;
    }
    return;
  }
  const s = e.target.closest(".skulink");
  if (s && s.dataset.sku) openProductHistory(s.dataset.sku);
});

let invSearchTimer;
document.getElementById("inv-search").addEventListener("input", () => {
  clearTimeout(invSearchTimer);
  invSearchTimer = setTimeout(renderInventory, 150);
});

invBinCombo = makeCombo("combo-bin", (v) => {
  invBinFilter = v;
  renderInventory();
});
invVendorCombo = makeCombo("combo-vendor", (v) => {
  invVendorFilter = v;
  renderInventory();
});
document.getElementById("inv-sort").addEventListener("change", renderInventory);

let searchTimer;
el.search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadRecent(el.search.value.trim()), 200);
});

// --- Global controls -------------------------------------------------------
el.reset.addEventListener("click", resetStation);
document.addEventListener("keydown", (e) => {
  // Esc resets the scan station only while it's the visible tab — otherwise
  // it would steal focus from the batch/queue inputs.
  if (e.key === "Escape" && !document.getElementById("tab-scan").hidden) {
    resetStation();
  }
});

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtWhen(iso) {
  return iso
    ? tsDate(iso).toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      })
    : "—";
}

async function apiJson(url, opts) {
  const res = await apiFetch(url, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg =
      typeof body.detail === "string" ? body.detail : "Request failed.";
    throw new Error(msg);
  }
  return body;
}

function postJson(url, payload) {
  return apiJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

// === Batch tagging ==========================================================
// One bin at a time: collect -> labels -> print -> pair -> verify -> done.
// The server owns the batch; this block just drives the stages.
let batch = null; // {id, bin_name, status, ...}
let batchItems = []; // BatchItem dicts (server shape)
let batchStage = "collect";
let pairActiveItemId = null;
let pairHistory = []; // [{epc, item_id}] for undo
let verifyEpcs = new Set();
// Oldest SWEEP capture feeding verifyEpcs - the stale-sweep guard
// judges on-hand writes by it (Nick, 2026-09-09: an 11AM sweep
// re-raised a count a 1PM sale had taken down). Live trigger reads
// are "now" and never age the evidence.
let verifySweepAt = null;
function noteVerifySweep(ts) {
  if (ts && (!verifySweepAt || ts < verifySweepAt)) verifySweepAt = ts;
}
let batchPrintTimer = null;

const bEl = {
  start: document.getElementById("batch-start"),
  bin: document.getElementById("batch-bin"),
  create: document.getElementById("batch-create"),
  resumeWrap: document.getElementById("batch-resume-wrap"),
  resumeList: document.getElementById("batch-resume-list"),
  active: document.getElementById("batch-active"),
  binChip: document.getElementById("batch-bin-chip"),
  stages: document.getElementById("batch-stages"),
  abandon: document.getElementById("batch-abandon"),
  result: document.getElementById("batch-result"),
  scan: document.getElementById("batch-scan"),
  items: document.getElementById("batch-items"),
  toLabels: document.getElementById("batch-to-labels"),
  queue: document.getElementById("batch-queue"),
  printAgent: document.getElementById("bprint-agent"),
  printStatus: document.getElementById("bprint-status"),
  toPair: document.getElementById("batch-to-pair"),
  pairInput: document.getElementById("batch-pair-input"),
  pairCard: document.getElementById("bpair-card"),
  pairActive: document.getElementById("bpair-active"),
  pairProgress: document.getElementById("bpair-progress"),
  pairUndo: document.getElementById("bpair-undo"),
  pairItems: document.getElementById("bpair-items"),
  toVerify: document.getElementById("batch-to-verify"),
  verifyInput: document.getElementById("batch-verify-input"),
  verifyCount: document.getElementById("bverify-count"),
  verifyCheck: document.getElementById("bverify-check"),
  complete: document.getElementById("batch-complete"),
  verifyReport: document.getElementById("bverify-report"),
};

function setBatchResult(message, kind) {
  bEl.result.textContent = message;
  bEl.result.className = "result" + (kind ? ` result--${kind}` : "");
}

function itemDisplayName(item) {
  return (
    (item.label_name || item.product_title || item.scanned_code || "—") +
    (item.variant_title && !item.label_name
      ? ` (${item.variant_title})`
      : "")
  );
}

function enterBatchTab() {
  const board = document.querySelector(".binboard");
  if (batch) {
    board.hidden = true;
    showBatchStage(batchStage);
    return;
  }
  bEl.start.hidden = false;
  bEl.active.hidden = true;
  board.hidden = false;
  loadResumeList();
  loadBinBoard();
  bEl.bin.focus();
}

// Open-batch ordering (Nick, 2026-09-09): newest (youngest) first by
// default, oldest first, or ONLY receiving batches wearing the
// not-RFID-paired tag - boxes labelled without ever pairing a sticker.
let resumeSortMode = "new";
let resumeBatchesCache = [];

async function loadResumeList() {
  try {
    const { batches } = await apiJson("/api/batches?status=open&limit=50");
    resumeBatchesCache = batches;
    renderResumeList();
  } catch (err) {
    bEl.resumeWrap.hidden = true;
  }
}

function renderResumeList() {
  bEl.resumeList.innerHTML = "";
  bEl.resumeWrap.hidden = !resumeBatchesCache.length;
  let rows = resumeBatchesCache.slice();
  if (resumeSortMode === "old") rows.reverse(); // server sends newest first
  if (resumeSortMode === "unpaired")
    rows = rows.filter((b) => (b.unpaired_labels || 0) > 0);
  rows.forEach((b) => {
    const li = document.createElement("li");
    const so = b.kind === "receiving" ? receivingSoOf(b.created_by) : "";
    const label =
      b.kind === "receiving"
        ? "📦 Receiving" + (so ? ` · ${escapeHtml(so)}` : "")
        : `Bin ${escapeHtml(b.bin_name)}`;
    li.innerHTML =
      `<b>${label}</b> - ${b.products} product(s), ` +
      `${b.boxes} box(es), ${b.paired} paired · ${escapeHtml(b.status)} ` +
      `<span class="mono">${escapeHtml(fmtWhen(b.created_at))}` +
      `${b.created_by ? " · " + escapeHtml(b.created_by) : ""}</span>` +
      (b.unpaired_labels
        ? ` <span class="binlabel binlabel--bad">🏷 ${b.unpaired_labels} label(s) not RFID-paired</span>`
        : "");
    li.addEventListener("click", () => resumeBatch(b.id));
    bEl.resumeList.append(li);
  });
  if (!rows.length && resumeBatchesCache.length) {
    const li = document.createElement("li");
    li.className = "inventory__empty";
    li.textContent =
      "No open receiving batches with unpaired labels - clear the " +
      "filter to see everything.";
    bEl.resumeList.append(li);
  }
}

document
  .getElementById("batch-resume-sort")
  ?.addEventListener("change", (ev) => {
    resumeSortMode = ev.target.value;
    renderResumeList();
  });

// --- Unresolved printed labels (Nick, 2026-09-09) ---------------------------
// Receiving labels (TC-Planner prints / Receive entire shipment ONLY)
// never RFID-paired, one row per product per batch. Dismiss retires ONE
// label instance for good - the same LabelDismissal audits honor.
async function renderUnresolvedLabels() {
  const list = document.getElementById("unres-list");
  list.innerHTML = '<li class="inventory__empty">Loading…</li>';
  try {
    const r = await apiJson("/api/receiving/unpaired-labels");
    const rows = r.products || [];
    if (!rows.length) {
      list.innerHTML =
        '<li class="inventory__empty">Every receiving label is paired or dismissed ✓</li>';
      return;
    }
    list.innerHTML = "";
    rows.forEach((p) => {
      const li = document.createElement("li");
      li.className = "recent__item unres-row";
      li.innerHTML = `
        <div class="unres-row__main">
          <b>${escapeHtml(p.product_title || p.sku || "?")}</b>
          <div class="binlabel"><span class="mono">${escapeHtml(p.sku || "?")}</span> · ${p.count} label(s) · Receiving #${p.batch_id}${
            p.reference ? " · " + escapeHtml(p.reference) : ""
          }${p.bin_location ? " · bin " + escapeHtml(p.bin_location) : ""}</div>
        </div>
        <button class="reset" type="button" data-dismiss
          ${p.epcs && p.epcs.length ? "" : "disabled"}
          title="Retire ONE of these labels for good - it stops counting as owed everywhere, audits included">Dismiss one</button>
        <button class="reset" type="button" data-soldout
          ${p.item_id ? "" : "disabled"}
          title="This product sold before it could be labelled - dismiss the WHOLE row (all its labels) until an audit settles it. Undoable from History.">Sold, no label</button>`;
      li.querySelector("[data-dismiss]").addEventListener(
        "click",
        async (ev) => {
          const btn = ev.currentTarget;
          btn.disabled = true;
          try {
            await postJson("/api/audit/dismiss-labels", {
              epcs: [p.epcs[0]],
              by: operatorEl.value || null,
            });
            renderUnresolvedLabels();
          } catch (err) {
            alert(err.message);
            btn.disabled = false;
          }
        }
      );
      li.querySelector("[data-soldout]").addEventListener(
        "click",
        async (ev) => {
          if (
            !confirm(
              `${p.sku}: sold before labelling? Every owed label on ` +
                `this row is dismissed (undo in History).`
            )
          )
            return;
          const btn = ev.currentTarget;
          btn.disabled = true;
          try {
            await postJson(
              `/api/batches/${p.batch_id}/items/${p.item_id}/dismiss-sold`,
              { worker: operatorEl.value || null }
            );
            renderUnresolvedLabels();
          } catch (err) {
            alert(err.message);
            btn.disabled = false;
          }
        }
      );
      list.append(li);
    });
  } catch (err) {
    list.innerHTML = `<li class="inventory__empty">${escapeHtml(err.message)}</li>`;
  }
}
document.getElementById("unres-labels-open").addEventListener("click", () => {
  document.getElementById("unres-overlay").hidden = false;
  renderUnresolvedLabels();
});
document.getElementById("unres-close").addEventListener("click", () => {
  document.getElementById("unres-overlay").hidden = true;
});
document.getElementById("unres-overlay").addEventListener("click", (e) => {
  if (e.target.id === "unres-overlay") e.target.hidden = true;
});

// --- Bin work board ---------------------------------------------------------
// Every bin in the store (from the Shopify bin map) that hasn't been
// batched yet, plus the last few that were finished.
let binBoard = null;

async function loadBinBoard() {
  const list = document.getElementById("binboard-list");
  const recent = document.getElementById("binboard-recent");
  try {
    binBoard = await apiJson("/api/bins/overview?recent=8");
    renderBinBoard();
    recent.innerHTML = "";
    if (!binBoard.recent.length) {
      recent.innerHTML =
        '<li class="recent__empty">No finished bins yet.</li>';
      return;
    }
    binBoard.recent.forEach((r) => {
      const li = document.createElement("li");
      li.innerHTML =
        `<span class="binlist__name">${escapeHtml(r.bin)}</span>` +
        (r.side_trip
          ? '<span class="binlist__sidetrip" title="Only the boxes ' +
            "carried over were tagged - the rest of this shelf was " +
            'never checked">side trip</span>'
          : "") +
        `<div class="binlist__count">${r.products} product(s) · ` +
        `${r.boxes} box(es) · ${r.tags} tag(s)</div>` +
        `<div class="binlist__count">${escapeHtml(fmtWhen(r.completed_at))}` +
        `${r.by ? " · " + escapeHtml(r.by) : ""}</div>`;
      recent.append(li);
    });
  } catch (err) {
    list.innerHTML = `<li class="recent__empty">${escapeHtml(err.message)}</li>`;
  }
}

let showHiddenBins = false;
let showDoneBins = false;
// Odd-named bins (not the usual "B19-2" shape) are a known backlog — 76 of
// them last count — and they crowd out the bins actually worth working.
// Remembered, because someone clearing normal bins wants them gone every
// session, not just this one.
let hideOddBins = localStorage.getItem("hideOddBins") === "1";
let binSort = "products";

// Eye / crossed-out eye, drawn inline so there's no icon dependency.
const ICON_EYE =
  '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" ' +
  'stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
  'stroke-linejoin="round" aria-hidden="true">' +
  '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>' +
  '<circle cx="12" cy="12" r="3.2"/></svg>';
const ICON_EYE_OFF =
  '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" ' +
  'stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
  'stroke-linejoin="round" aria-hidden="true">' +
  '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>' +
  '<circle cx="12" cy="12" r="3.2"/>' +
  '<line x1="2.5" y1="2.5" x2="21.5" y2="21.5"/></svg>';

function sortBins(rows) {
  const byName = (a, b) =>
    a.bin.localeCompare(b.bin, undefined, {
      numeric: true,
      sensitivity: "base",
    });
  const copy = [...rows];
  if (binSort === "name") copy.sort(byName);
  else if (binSort === "name-desc") copy.sort((a, b) => byName(b, a));
  else if (binSort === "fewest")
    copy.sort((a, b) => a.products - b.products || byName(a, b));
  else copy.sort((a, b) => b.products - a.products || byName(a, b));
  // Bins already being worked stay at the top whatever the sort.
  copy.sort((a, b) => (a.open_batch_id ? 0 : 1) - (b.open_batch_id ? 0 : 1));
  return copy;
}

function renderBinBoard() {
  const list = document.getElementById("binboard-list");
  const countEl = document.getElementById("binboard-count");
  const hideBtn = document.getElementById("binboard-showhidden");
  if (!binBoard) return;
  const q = document
    .getElementById("binboard-filter")
    .value.trim()
    .toLowerCase();
  const rows = sortBins(
    binBoard.todo.filter(
      (b) =>
        (showHiddenBins || !b.hidden) &&
        (!hideOddBins || !b.malformed) &&
        (!q || b.bin.toLowerCase().includes(q))
    )
  );
  // How many the odd-name filter is actually holding back right now — the
  // store-wide malformed_count includes done and hidden bins, so quoting it
  // here would claim to be hiding bins that were never in this list.
  const oddInList = binBoard.todo.filter(
    (b) => b.malformed && (showHiddenBins || !b.hidden)
  ).length;
  countEl.textContent =
    `(${binBoard.todo_count} of ${binBoard.total_bins} left · ` +
    `${binBoard.done_bins} done` +
    `${binBoard.hidden_count ? ` · ${binBoard.hidden_count} hidden` : ""}` +
    `${
      binBoard.malformed_count
        ? ` · ${binBoard.malformed_count} odd name(s)`
        : ""
    }${
      binBoard.flagged_count ? ` · ${binBoard.flagged_count} flagged` : ""
    })`;
  hideBtn.innerHTML = showHiddenBins
    ? `${ICON_EYE_OFF}<span>Hide ignored</span>`
    : `${ICON_EYE}<span>Show hidden${
        binBoard.hidden_count ? ` (${binBoard.hidden_count})` : ""
      }</span>`;
  // Bins already batch tagged, on request — the full record, not just
  // the 8 in Recently done.
  const doneRows = showDoneBins
    ? (binBoard.done || []).filter(
        (b) => !q || b.bin.toLowerCase().includes(q)
      )
    : [];
  document.getElementById("binboard-showdone").textContent = showDoneBins
    ? "Hide done"
    : `Show done${binBoard.done_bins ? ` (${binBoard.done_bins})` : ""}`;
  const oddBtn = document.getElementById("binboard-oddfilter");
  oddBtn.innerHTML = hideOddBins
    ? `${ICON_EYE}<span>Show odd names${oddInList ? ` (${oddInList})` : ""}</span>`
    : `${ICON_EYE_OFF}<span>Hide odd names${
        oddInList ? ` (${oddInList})` : ""
      }</span>`;
  // Nothing to offer when every bin is well named.
  oddBtn.hidden = !oddInList && !hideOddBins;
  list.innerHTML = "";
  if (!rows.length && !doneRows.length) {
    list.innerHTML = `<li class="recent__empty">${
      q
        ? "No bins match that."
        : hideOddBins && oddInList
          ? `Nothing left but ${oddInList} odd-named bin(s), which are hidden.`
          : binBoard.hidden_count && !showHiddenBins
            ? `Nothing left to do - ${binBoard.hidden_count} bin(s) are hidden.`
            : "Every bin has been done ✓"
    }</li>`;
    return;
  }
  rows.forEach((b) => {
    const li = document.createElement("li");
    if (b.open_batch_id) li.classList.add("binlist--open");
    if (b.hidden) li.classList.add("binlist--hidden");
    if (b.malformed) li.classList.add("binlist--odd");
    if (b.flagged) li.classList.add("binlist--flagged");
    li.innerHTML =
      `<button class="binlist__eye" type="button" title="${
        b.hidden ? "Show bin" : "Hide bin"
      }" aria-label="${b.hidden ? "Show bin" : "Hide bin"}">${
        b.hidden ? ICON_EYE : ICON_EYE_OFF
      }</button>` +
      `<button class="binlist__flagbtn" type="button" title="${
        b.flagged
          ? "Remove the ask-first flag"
          : "Flag: ask someone before scanning this bin"
      }" aria-label="${b.flagged ? "Unflag bin" : "Flag bin"}">⚑</button>` +
      `<span class="binlist__name">${escapeHtml(b.bin)}</span>` +
      `${
        b.malformed
          ? `<span class="binlist__odd" title="Bin name doesn't match the A1-2 format (one letter, then 1-99, dash, 1-99). Usually means one product's stock is split across shelves - worth fixing in Shopify before tagging this bin.">⚠ odd name</span>`
          : ""
      }` +
      `${
        b.flagged
          ? `<span class="binlist__flag" title="${escapeHtml(
              b.flag_note || "Ask someone who knows this stock before scanning."
            )}">⚑ ask first</span>`
          : ""
      }` +
      `<span class="binlist__count">${b.products} product(s)${
        b.open_batch_id ? " · in progress" : ""
      }${b.hidden ? " · hidden" : ""}</span>` +
      `<button class="binlist__go" type="button">${
        b.open_batch_id ? "Resume" : "Start batch"
      }</button>`;
    // Clicking the name only fills the box — starting is a deliberate act.
    li.querySelector(".binlist__name").addEventListener("click", () => {
      bEl.bin.value = b.bin;
      bEl.bin.focus();
    });
    li.querySelector(".binlist__go").addEventListener("click", () => {
      if (b.open_batch_id) {
        resumeBatch(b.open_batch_id);
      } else {
        bEl.bin.value = b.bin;
        startBatch();
      }
    });
    li.querySelector(".binlist__eye").addEventListener("click", async (ev) => {
      const hidden = !b.hidden;
      ev.currentTarget.disabled = true;
      try {
        await apiJson(`/api/bins/${encodeURIComponent(b.bin)}/hidden`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            hidden,
            hidden_by: operatorEl.value || null,
          }),
        });
        b.hidden = hidden;
        binBoard.todo_count += hidden ? -1 : 1;
        binBoard.hidden_count += hidden ? 1 : -1;
        renderBinBoard();
      } catch (err) {
        ev.currentTarget.disabled = false;
        setBatchResult(err.message, "err");
      }
    });
    li.querySelector(".binlist__flagbtn").addEventListener(
      "click",
      async (ev) => {
        const flagged = !b.flagged;
        let note = null;
        if (flagged) {
          note = prompt(
            `Flag ${b.bin} as "ask first".\n\n` +
              `Why does it need a second opinion? (optional)`,
            b.flag_note || ""
          );
          if (note === null) return; // cancelled
          note = note.trim() || null;
        }
        ev.currentTarget.disabled = true;
        try {
          await apiJson(`/api/bins/${encodeURIComponent(b.bin)}/flagged`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              flagged,
              note,
              flagged_by: operatorEl.value || null,
            }),
          });
          b.flagged = flagged;
          b.flag_note = note;
          binBoard.flagged_count += flagged ? 1 : -1;
          renderBinBoard();
        } catch (err) {
          ev.currentTarget.disabled = false;
          setBatchResult(err.message, "err");
        }
      }
    );
    list.append(li);
  });
  doneRows.forEach((b) => {
    const li = document.createElement("li");
    li.classList.add("binlist--done");
    li.innerHTML =
      `<span class="binlist__check">✓</span>` +
      `<span class="binlist__name">${escapeHtml(b.bin)}</span>` +
      `<span class="binlist__count">${b.products} product(s) · done ` +
      `${escapeHtml(fmtAgo(b.completed_at))}${
        b.by ? ` · ${escapeHtml(b.by)}` : ""
      }</span>`;
    li.title = `Batch #${b.batch_id} finished ${fmtWhen(b.completed_at)}`;
    // Same affordance as to-do names: click fills the bin box, so a
    // re-walk of a done shelf is one click + Start batch away.
    li.querySelector(".binlist__name").addEventListener("click", () => {
      bEl.bin.value = b.bin;
      bEl.bin.focus();
    });
    list.append(li);
  });
}

document.getElementById("binboard-showhidden").addEventListener("click", () => {
  showHiddenBins = !showHiddenBins;
  renderBinBoard();
});

document.getElementById("binboard-showdone").addEventListener("click", () => {
  showDoneBins = !showDoneBins;
  renderBinBoard();
});

document.getElementById("binboard-oddfilter").addEventListener("click", () => {
  hideOddBins = !hideOddBins;
  localStorage.setItem("hideOddBins", hideOddBins ? "1" : "0");
  renderBinBoard();
});

document.getElementById("binboard-sort").addEventListener("change", (e) => {
  binSort = e.target.value;
  renderBinBoard();
});

// Force a full re-read of bins from Shopify. Needed because Shopify can't
// be asked "which products are in bin X" — only the whole catalog walk
// finds products that MOVED INTO a bin.
refreshify("binboard-refresh", "bin-map-pull", async () => {
  const countEl = document.getElementById("binboard-count");
  const original = countEl.textContent;
  const stopDots = startDots(countEl, "(re-reading bins from Shopify");
  try {
    await postJson("/api/bin-map/refresh", {});
    for (let i = 0; i < 40; i++) {
      await new Promise((r) => setTimeout(r, 3000));
      const s = await apiJson("/api/bin-map/status");
      if (!s.refreshing) break;
    }
    stopDots();
    await loadBinBoard();
  } catch (err) {
    stopDots();
    countEl.textContent = original;
    setBatchResult(err.message, "err");
  }
});

let binFilterTimer;
document.getElementById("binboard-filter").addEventListener("input", () => {
  clearTimeout(binFilterTimer);
  binFilterTimer = setTimeout(renderBinBoard, 120);
});

// --- shelf baseline: reconcile a part-tagged bin ---------------------------
// Some shelves were tagged in an earlier session (Astronomik on D2-2), and
// there was no way to know how far that got. Sweep the shelf on the C72's
// SWEEP tab, SEND it, then apply it here: every tag read marks its product
// already-done, and the batch becomes exactly the untagged remainder.
document.getElementById("batch-baseline").addEventListener("click", async () => {
  if (!batch) return;
  let cap;
  try {
    cap = await apiJson("/api/epc-captures/latest");
  } catch (err) {
    setBatchResult(
      "No sweep on file yet - on the C72, open SWEEP, hold the trigger " +
        "over the shelf, then hit SEND. Then click this again.",
      "err"
    );
    return;
  }
  const when = cap.created_at
    ? tsDate(cap.created_at).toLocaleTimeString()
    : "?";
  if (
    !confirm(
      `Use the last C72 sweep as the baseline for ${batch.bin_name}?\n\n` +
        `${cap.epc_count} tag(s), from ${cap.device || "C72"} at ${when}.\n\n` +
        `Every tag read marks its product as already tagged - those boxes ` +
        `won't get labels. Make sure that sweep was THIS shelf.`
    )
  )
    return;
  try {
    const res = await postJson(`/api/batches/${batch.id}/baseline`, {
      epcs: cap.epcs || [],
    });
    await pullBatch(false);
    renderBatchItems();
    let msg = res.message;
    if (res.strays && res.strays.length) {
      msg +=
        " Strays: " +
        res.strays
          .slice(0, 5)
          .map((s) => `${s.sku || "?"} (recorded in ${s.recorded_bin || "?"})`)
          .join(", ") +
        (res.strays.length > 5 ? "…" : "");
    }
    setBatchResult(msg, res.strays && res.strays.length ? "err" : "ok");
  } catch (err) {
    setBatchResult(err.message, "err");
  }
});

bEl.create.addEventListener("click", startBatch);

// Receiving batches are created by the Inventory Planner's "Print labels"
// button only, and close THEMSELVES when every received box is tagged
// (Nick, 2026-08-25) — no manual start, no print pass, no finish
// ceremony: entirely planner-driven.

bEl.bin.addEventListener("keydown", (e) => {
  if (e.key === "Enter") startBatch();
});

async function startBatch() {
  const bin = bEl.bin.value.trim();
  if (!bin) {
    bEl.bin.focus();
    return;
  }
  const operator = operatorEl.value;
  if (!operator) {
    setBatchResult("Pick who's scanning (top right) first.", "err");
    operatorEl.focus();
    return;
  }
  bEl.create.disabled = true;
  try {
    batch = await postJson("/api/batches", { bin, created_by: operator });
    batchItems = batch.items || [];
    bEl.bin.value = "";
    openBatchView("collect");
    // Bundles with defined contents are held out of the count on purpose:
    // their boxes ARE the component's boxes, so the note says which
    // listings the component counts already cover.
    const covered = (batch.covered_bundles || [])
      .map(
        (c) =>
          `${c.sku} (= ${c.contents
            .map((x) => `${x.qty}× ${x.component_sku}`)
            .join(" + ")})`
      )
      .join(", ");
    setBatchResult(
      (batchItems.length
        ? `${batchItems.length} product(s) expected in bin ${batch.bin_name} - start scanning boxes.`
        : `Nothing on file for bin ${batch.bin_name} - scan boxes and they'll be added.`) +
        (covered
          ? ` 📦 ${batch.covered_bundles.length} bundle listing(s) covered by their components - no separate count needed: ${covered}.`
          : ""),
      "ok"
    );
  } catch (err) {
    setBatchResult(err.message, "err");
  } finally {
    bEl.create.disabled = false;
  }
}

// Re-pull the batch from the server. The C72 (or another terminal) writes
// every scan/pair server-side, so pulling is all "live" means.
// Server status → the stage that status belongs to. Used to follow along
// when another terminal (the C72) moves the batch forward.
const STAGE_FOR_STATUS = {
  collecting: "collect",
  printing: "print",
  pairing: "pair",
  // The scanner finished at the shelf and handed the bin over for
  // sign-off — land on Verify.
  "awaiting-verify": "verify",
};

// The batch's shared "which step are we on" signal. Status can't carry it
// (collect and check are both "collecting"), so terminals publish the step
// they're on and everyone else follows. "check" is this page's "labels".
const STEP_TO_STAGE = {
  collect: "collect",
  check: "labels",
  print: "print",
  pair: "pair",
  verify: "verify",
};
const STAGE_TO_STEP = {
  collect: "collect",
  labels: "check",
  print: "print",
  pair: "pair",
  verify: "verify",
};
// Set while applying a step that came FROM the server, so following a
// change doesn't immediately publish it back.
let applyingRemoteStep = false;
let lastPublishedStep = null;

function publishBatchStep(stage) {
  if (!batch || applyingRemoteStep) return;
  const step = STAGE_TO_STEP[stage];
  if (!step || step === lastPublishedStep) return;
  lastPublishedStep = step;
  postJson(`/api/batches/${batch.id}/step`, { step }).catch(() => {
    lastPublishedStep = null; // let a later attempt retry
  });
}

async function pullBatch(announce) {
  if (!batch) return;
  try {
    const prevStatus = batch.status;
    const prevShelfSweep = batch.shelf_swept_at;
    const data = await apiJson(`/api/batches/${batch.id}`);
    batch = data.batch;
    batchItems = data.items;
    // Receiving is stepless: never follow the C72's published step, just
    // keep the list live. The shipment closes itself on the last pair -
    // say so the moment this screen notices.
    if (isReceivingBatch()) {
      if (prevStatus !== "done" && batch.status === "done")
        setBatchResult(
          "Shipment complete ✓ - every received box is tagged, so the " +
            "batch closed itself.",
          "ok"
        );
      if (batchStage !== "receiving") showBatchStage("receiving");
      else renderReceivingList();
      if (announce) setBatchResult("Refreshed from the server.", "ok");
      return;
    }
    // The C72 just sent the shelf sweep: clear the check-step banner and
    // pull the fresh verdicts once (the check list doesn't re-fetch on
    // the normal 3s poll — this transition is the exception).
    if (!prevShelfSweep && batch.shelf_swept_at) {
      updateShelfWarn();
      if (batchStage === "labels") loadBatchReview();
      setBatchResult("Shelf sweep received from the gun ✓", "ok");
    }
    // Resuming a side trip directly (or arriving from another terminal)
    // must still show the banner and the way back.
    renderSideTrip();
    // The C72 (or another browser) moved on — follow it, so this screen
    // doesn't sit on "1 Collect" while the scanner is checking or pairing.
    // The published step is the precise signal; status is the fallback for
    // moves made before this existed.
    const stepTarget = STEP_TO_STAGE[batch.ui_step || ""];
    const statusTarget =
      batch.status !== prevStatus ? STAGE_FOR_STATUS[batch.status] : null;
    // A status change is the stronger signal — the published step can be
    // stale (nobody republishes it when the server moves the batch on).
    let target = statusTarget || stepTarget;
    // The gun starts pairing while labels are still coming out - that is
    // the normal rhythm (the agent prints bursts of 5), NOT a sign that
    // printing is over. Its pair screen publishes "pair" and the first
    // pair flips the status to "pairing"; neither may yank this screen
    // off a LIVE print run (Nick, 2026-08-26) - the whole point of
    // standing here is watching the rest of the run. Following resumes
    // by itself once nothing is left to print, and the step chips
    // always work by hand.
    if (
      target === "pair" &&
      batchStage === "print" &&
      (bprintOutstanding == null || bprintOutstanding > 0)
    ) {
      target = null;
    }
    if (target && target !== batchStage) {
      applyingRemoteStep = true;
      lastPublishedStep = batch.ui_step || null;
      showBatchStage(target);
      applyingRemoteStep = false;
      setBatchResult(
        `Followed the scanner to the ${
          target === "labels" ? "check" : target
        } step.`,
        "ok"
      );
      return;
    }
    if (batchStage === "collect") renderBatchItems();
    else if (batchStage === "pair") {
      renderPairItems();
      renderPairCard();
    }
    // (check stage re-fetches its review on entry, not on the live poll —
    // the candidates lookups are too heavy to run every 3s)
    if (announce) setBatchResult("Refreshed from the server.", "ok");
  } catch (err) {
    if (announce) setBatchResult(err.message, "err");
  }
}

async function refreshBatch() {
  return pullBatch(true);
}

// Live feed: while a batch is open, poll every 3s so this screen mirrors
// whatever the C72 (or any other terminal) is doing to the same batch.
let batchLiveTimer = null;

// A sweep sent from the C72 lands here by itself: the scanner posts it,
// this screen notices, jumps to Verify and runs the check — no "pull"
// button dance. Only sweeps newer than the moment this batch was opened
// count, so an old capture can't hijack the screen.
let lastSweepId = null;

async function checkForIncomingSweep() {
  if (!batch) return;
  try {
    const { captures } = await apiJson("/api/epc-captures?limit=1");
    const newest = captures[0];
    if (lastSweepId === null) {
      // Baseline, set even when no sweep exists yet — otherwise the very
      // first sweep of a fresh system gets mistaken for history.
      lastSweepId = newest ? newest.id : 0;
      return;
    }
    if (!newest || newest.id <= lastSweepId) return;
    lastSweepId = newest.id;
    // Sweeps tagged for another batch aren't ours.
    if (newest.batch_id && newest.batch_id !== batch.id) return;
    const cap = await apiJson(`/api/epc-captures/${newest.id}`);
    if (batchStage !== "verify") showBatchStage("verify"); // this resets the set
    cap.epcs.forEach((e) => verifyEpcs.add(String(e).toUpperCase()));
    noteVerifySweep(cap.created_at);
    bEl.verifyCount.textContent = `${verifyEpcs.size} unique tags collected.`;
    setBatchResult(
      `Sweep #${cap.id} arrived from ${cap.device || "the C72"} ` +
        `(${cap.epc_count} tags) - checking the bin…`,
      "ok"
    );
    await runVerifyCheck();
    // The "checking…" line used to sit there forever (Nick, 2026-08-31)
    // - once the check lands, say so and point at the next move.
    setBatchResult(
      `Sweep #${cap.id} checked ✓ - ${verifyEpcs.size} unique tag(s) ` +
        `on file. Sweep again to add reads, or Complete batch below.`,
      "ok"
    );
    batchSound("ok");
  } catch (err) {
    /* transient; the next tick tries again */
  }
}

function startBatchLive() {
  stopBatchLive();
  lastSweepId = null;
  batchLiveTimer = setInterval(() => {
    // No document.hidden guard: embedded webviews (and some tablet shells)
    // misreport visibility, and a live feed that silently pauses is worse
    // than one cheap GET every 3s.
    if (!batch) return;
    if (document.getElementById("tab-batch").hidden) return;
    // Never clobber something the operator is typing (label names etc.);
    // the always-focused scan fields are exempt — they're transient.
    const ae = document.activeElement;
    if (
      ae &&
      ae.tagName === "INPUT" &&
      ae.closest("#tab-batch") &&
      // The always-focused scan fields are transient — polling must not
      // pause just because one has focus (it always does on those steps).
      ![
        "batch-scan",
        "batch-pair-input",
        "batch-verify-input",
        "batch-bin",
      ].includes(ae.id)
    )
      return;
    pullBatch(false);
    checkForIncomingSweep();
  }, 3000);
}

function stopBatchLive() {
  if (batchLiveTimer) {
    clearInterval(batchLiveTimer);
    batchLiveTimer = null;
  }
}

async function resumeBatch(id) {
  try {
    const data = await apiJson(`/api/batches/${id}`);
    batch = data.batch;
    batchItems = data.items;
    const stageByStatus = {
      collecting: "collect",
      printing: "print",
      pairing: "pair",
    };
    openBatchView(stageByStatus[batch.status] || "collect");
  } catch (err) {
    setBatchResult(err.message, "err");
  }
}

// Receiving: a bin-less shipment batch fed by the Inventory Planner.
// It has NO steps (Nick, 2026-08-25): one list of the products sent to
// receive, with tagging progress, per-product reprint and count fixes.
// Pairing happens on the C72 as usual; finishing files a bin-check
// Review task per touched bin.
function isReceivingBatch() {
  return !!(batch && batch.kind === "receiving");
}

// The stock order number out of a receiving batch's tag
// ("TC-Planner · SO 968 · ZWO" -> "SO 968"). Batches are one-per-SO
// now (Nick, 2026-09-23), so the SO is the batch's NAME and leads
// everywhere the batch shows; vendor-merge-era batches may still list
// several ("SO 42, SO 43").
function receivingSoOf(createdBy) {
  const parts = String(createdBy || "")
    .split("·")
    .map((s) => s.trim());
  return parts.length > 1 && /^SO\b/i.test(parts[1]) ? parts[1] : "";
}

function openBatchView(stage) {
  bEl.start.hidden = true;
  bEl.active.hidden = false;
  document.querySelector(".binboard").hidden = true;
  const so = isReceivingBatch() ? receivingSoOf(batch.created_by) : "";
  bEl.binChip.textContent = isReceivingBatch()
    ? "📦 Receiving" + (so ? ` · ${so}` : "")
    : `Bin ${batch.bin_name}`;
  // Receiving has no steps at all — the chip bar goes away entirely.
  bEl.stages.style.display = isReceivingBatch() ? "none" : "";
  setBatchResult("", null);
  showBatchStage(stage);
  startBatchLive();
}

const BATCH_STAGES = ["collect", "labels", "print", "pair", "verify"];

function showBatchStage(stage) {
  if (isReceivingBatch()) {
    // One list, no stages: whatever step was asked for, receiving shows
    // the receiving list.
    batchStage = "receiving";
    stopBatchPrintPoll();
    BATCH_STAGES.forEach((s) => {
      document.getElementById(`bstage-${s}`).hidden = true;
    });
    document.getElementById("bstage-receiving").hidden = false;
    renderReceivingList();
    return;
  }
  document.getElementById("bstage-receiving").hidden = true;
  batchStage = stage;
  stopBatchPrintPoll();
  const idx = BATCH_STAGES.indexOf(stage);
  bEl.stages.querySelectorAll(".stage").forEach((chip) => {
    const i = BATCH_STAGES.indexOf(chip.dataset.stage);
    chip.classList.toggle("stage--active", i === idx);
    chip.classList.toggle("stage--done", i < idx);
  });
  BATCH_STAGES.forEach((s) => {
    document.getElementById(`bstage-${s}`).hidden = s !== stage;
  });
  if (stage === "collect") {
    renderBatchItems();
    bEl.scan.focus();
  } else if (stage === "labels") {
    loadBatchReview();
  } else if (stage === "print") {
    bprintOutstanding = null; // unknown until the first poll answers
    pollBatchPrint();
    batchPrintTimer = setInterval(pollBatchPrint, 3000);
  } else if (stage === "pair") {
    renderPairItems();
    renderPairCard();
    bEl.pairInput.focus();
  } else if (stage === "verify") {
    verifyEpcs = new Set();
    verifySweepAt = null;
    bEl.verifyCount.textContent = "0 unique tags collected.";
    bEl.verifyReport.innerHTML = "";
    bEl.verifyInput.focus();
  }
  publishBatchStep(stage);
}

function stopBatchPrintPoll() {
  if (batchPrintTimer) {
    clearInterval(batchPrintTimer);
    batchPrintTimer = null;
  }
}

refreshify("batch-refresh", "batch-pull", () => refreshBatch());

// Leave the batch open and go back to the bin list — the batch keeps its
// counts and can be resumed from any device.
document.getElementById("batch-switch").addEventListener("click", () => {
  batch = null;
  batchItems = [];
  checkEntries = [];
  ignoredBinItems = new Set();
  stopBatchPrintPoll();
  stopBatchLive();
  enterBatchTab();
  setBatchResult("Batch left open - pick it up any time.", "ok");
});

bEl.abandon.addEventListener("click", async () => {
  if (!batch) return;
  const ties = batchItems.reduce((n, i) => n + (i.paired_count || 0), 0);
  const msg = ties
    ? `Abandon the batch for bin ${batch.bin_name}?\n\n${ties} tag(s) were ` +
      `paired in this batch - those ties will be REMOVED so the products ` +
      `aren't left tied to unverified labels. Counts stay in History.`
    : `Abandon the batch for bin ${batch.bin_name}? Collected counts are ` +
      `kept in History but the batch closes.`;
  if (!confirm(msg)) return;
  try {
    const res = await postJson(`/api/batches/${batch.id}/abandon`, {
      remove_ties: true,
    });
    if (res.ties_removed)
      setBatchResult(`Batch abandoned - ${res.ties_removed} tie(s) released.`, "ok");
  } catch (err) {
    /* already closed is fine */
  }
  batch = null;
  batchItems = [];
  checkEntries = [];
  ignoredBinItems = new Set();
  stopBatchPrintPoll();
  stopBatchLive();
  enterBatchTab();
});

// Scan sounds, mirroring the C72: ding = expected product ticked up,
// double-ding = real product that wasn't expected in this bin, buzz =
// unknown barcode or failure. WebAudio spins up lazily — the scan
// keystroke itself is the user gesture browsers require.
let audioCtx = null;

function batchSound(kind) {
  try {
    audioCtx =
      audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") audioCtx.resume();
    const tone = (freq, at, dur, type = "sine", vol = 0.25) => {
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = type;
      osc.frequency.value = freq;
      const t = audioCtx.currentTime + at;
      gain.gain.setValueAtTime(vol, t);
      gain.gain.exponentialRampToValueAtTime(0.001, t + dur);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start(t);
      osc.stop(t + dur + 0.02);
    };
    if (kind === "ok") {
      tone(880, 0, 0.14);
    } else if (kind === "other") {
      tone(660, 0, 0.09);
      tone(990, 0.11, 0.12);
    } else {
      tone(170, 0, 0.28, "square", 0.18);
    }
  } catch (err) {
    /* sound is best-effort */
  }
}

// One card renderer for collect and pair lists — the C72 view is the
// design reference (image | bold name + labeled lines, tracker top-right).
// --- multi-box products vs bundles ------------------------------------------
// Two listings can fill the same several box slots for opposite reasons: one
// product shipped in three cartons, or a bundle whose "boxes" are really
// separate products with their own listings and their own tags. The server
// guesses from the catalog's own convention ("BUNDLE: ...", SKU "91519+93973")
// and the operator corrects it here, holding the actual box.
const BIN_SPLIT_RE = /\s*(?:[&,;/+]|\band\b)\s*/i;

function boxSlots(item) {
  const count = (v) =>
    String(v || "")
      .split(BIN_SPLIT_RE)
      .map((p) => p.trim())
      .filter(Boolean).length;
  // Scanned rows carry the whole metafield in bin_location; seeded rows carry
  // only this shelf, with the rest in other_bins. Whichever says "more".
  return Math.max(count(item.bin_location), 1 + count(item.other_bins));
}

// Spell the "2 + 8x1" shorthand out in words, for the hover hint.
function caseHint(p) {
  const parts = String(p.unit_breakdown || "").split(" + ");
  const loose = parts.shift();
  const cases = parts
    .map((seg) => {
      const [units, n] = seg.split("x");
      return `${n} box${n === "1" ? "" : "es"} of ${units}`;
    })
    .join(", ");
  return (
    `${p.unit_count} units on the shelf: ${loose} on their own, plus ` +
    `${cases}. Shopify counts ${p.unit_count}.`
  );
}

// "2 + 8x1" — loose units, then units-per-case times cases. Null unless a
// sealed case is involved, because otherwise the total says it all.
function unitBreakdown(item) {
  if (!item || !item.case_count || !item.case_units) return null;
  return `${item.qty_scanned} + ${item.case_units}x${item.case_count}`;
}

function itemCard(item, mode) {
  const li = document.createElement("li");
  li.className = "bcell";
  if (item.skipped) li.classList.add("bcell--skipped");
  if (!item.resolved) li.classList.add("bcell--warn");
  if (mode === "pair") {
    if (item.id === pairActiveItemId) li.classList.add("bcell--active");
    const labelGoal =
      item.labels_total != null ? item.labels_total : item.qty_scanned;
    if (labelGoal > 0 && item.paired_count >= labelGoal)
      li.classList.add("bcell--exact");
  } else if (item.expected_qty != null) {
    // Compare UNITS to Shopify's on-hand — a sealed case is one box but
    // several units, so boxes would read short.
    const units = item.units_total != null ? item.units_total : item.qty_scanned;
    if (units === item.expected_qty && units > 0)
      li.classList.add("bcell--exact");
    else if (units > item.expected_qty) li.classList.add("bcell--over");
  }
  const units = item.units_total != null ? item.units_total : item.qty_scanned;
  const labels = item.labels_total != null ? item.labels_total : item.qty_scanned;
  const tracker =
    mode === "pair"
      ? // The denominator is the Collect step's count — a fixed target.
        // max(labels, paired) used to move the goalposts, so 5 tags on
        // 4 labels read "5/5" instead of an honest overshoot.
        `${item.paired_count}/${labels}`
      : item.expected_qty != null
        ? `${units}/${item.expected_qty}`
        : `${units}`;
  const barcode = item.barcode || item.scanned_code;
  li.innerHTML = `
    ${
      item.image_url
        ? `<img class="bcell__img" src="${escapeHtml(item.image_url)}" alt="" loading="lazy" />`
        : `<span class="bcell__img bcell__img--empty"></span>`
    }
    <div class="bcell__info">
      <div class="bcell__name">${escapeHtml(itemDisplayName(item))}</div>
      <div class="bcell__meta">${
        item.sku
          ? "SKU: " + escapeHtml(item.sku)
          : item.resolved
            ? "no SKU"
            : "⚠ unknown barcode"
      }</div>
      ${barcode ? `<div class="bcell__meta">Barcode: ${escapeHtml(barcode)}</div>` : ""}
      ${
        item.skipped
          ? `<div class="bcell__meta bcell__skipped">⊘ Skipped${
              item.skip_reason ? " - " + escapeHtml(item.skip_reason) : ""
            } · no label, nothing counted</div>`
          : ""
      }
      ${
        item.tagged_before
          ? `<div class="bcell__meta bcell__done">✓ ${item.tagged_before} already tagged - no labels will print for those</div>`
          : ""
      }
      ${
        unitBreakdown(item)
          ? `<div class="bcell__meta bcell__cases" title="${escapeHtml(
              `${item.qty_scanned} loose box(es) plus ${item.case_count} sealed case(s) of ${item.case_units} - ${item.labels_total} label(s) in total`
            )}">${escapeHtml(unitBreakdown(item))} - ${item.labels_total} label(s)</div>`
          : ""
      }
      ${
        item.other_bins
          ? `<div class="bcell__meta bcell__split">${
              item.kind === "bundle"
                ? `Components on ${escapeHtml(item.other_bins)} - a bundle, not a box of its own`
                : `Also on ${escapeHtml(item.other_bins)} - ${
                    item.kind === "multi_box"
                      ? `ships as ${boxSlots(item)} boxes`
                      : "this item is split across shelves"
                  }`
            }</div>`
          : ""
      }
    </div>
    <span class="bcell__tracker">${tracker}</span>`;
  return li;
}

// Stage chips are navigation: click any chip to jump to that step (going
// back to fix something is the whole point).
bEl.stages.querySelectorAll(".stage").forEach((chip) => {
  chip.addEventListener("click", () => {
    if (!batch) return;
    showBatchStage(chip.dataset.stage);
  });
});

// --- Stage 1: collect -------------------------------------------------------
bEl.scan.addEventListener("keydown", async (event) => {
  if (event.key !== "Enter") return;
  const code = bEl.scan.value.trim();
  bEl.scan.value = "";
  await batchCollectScan(code);
  bEl.scan.focus();
});

// Shared by the wedge input above and the C72 LINK relay.
async function batchCollectScan(code) {
  if (!code || !batch) return;
  setBatchResult("Looking up…", "busy");
  try {
    let data = await postJson(`/api/batches/${batch.id}/scan`, { code });
    // A case code pauses the scan to ask one question, because opening the
    // box or not changes the count, the labels and the tags.
    if (data.needs_case_decision) {
      batchSound("other");
      const c = data.case;
      const opened = confirm(
        `${c.barcode} is a box of ${c.units} × ${c.sku}\n` +
          `${c.product_title || ""}\n` +
          (c.scan_note ? `\n⚠ ${c.scan_note}\n` : "") +
          `\nAre you opening it?\n\n` +
          `OK  - opened: counts ${c.units} units and prints ${c.units} labels.\n` +
          `Cancel - left sealed: counts ${c.units} units but prints ONE ` +
          `label reading "${c.units} x ${c.sku}".`
      );
      data = await postJson(`/api/batches/${batch.id}/scan`, {
        code,
        case_action: opened ? "open" : "sealed",
      });
    }
    const item = data.item;
    const existing = batchItems.findIndex((i) => i.id === item.id);
    const wasListed = existing >= 0;
    if (existing >= 0) batchItems.splice(existing, 1);
    // Freshly scanned floats to the top — big bins pre-seed a long list
    // and the row you just ticked should stay in view.
    batchItems.unshift(item);
    if (data.bin_mismatch) item._binMismatch = true;
    renderBatchItems();
    batchSound(!item.resolved ? "err" : wasListed ? "ok" : "other");
    if (!item.resolved) {
      setBatchResult(
        `"${code}" isn't in the system - kept in the count as unresolved. ` +
          `Link it later at the Scan Station.`,
        "err"
      );
    } else if (data.serial_note) {
      setBatchResult(
        `⚠ ${data.serial_note} - ${itemDisplayName(item)}: ${item.qty_scanned} scanned.`,
        "err"
      );
    } else if (data.case) {
      // Say both numbers: a case makes units and labels diverge.
      setBatchResult(
        (data.case.scan_note ? `⚠ ${data.case.scan_note} - ` : "") +
          `${itemDisplayName(item)} - ${item.units_total} unit(s)` +
          (unitBreakdown(item) ? ` (${unitBreakdown(item)})` : "") +
          `, ${item.labels_total} label(s)` +
          (data.case_action === "sealed" ? " - box left sealed." : "."),
        data.case.scan_note ? "err" : "ok"
      );
    } else {
      setBatchResult(
        `${itemDisplayName(item)} - ${item.qty_scanned} scanned` +
          (item.expected_qty != null
            ? ` (Shopify on-hand ${item.expected_qty})`
            : ""),
        "ok"
      );
    }
    // Receiving: say when the box in hand sits on an open PO. The hint
    // clears on every scan so it always describes the LAST product.
    document.getElementById("batch-planner-hint").hidden = true;
    if (item.resolved && isReceivingBatch())
      showPlannerHint(item.sku, "batch-planner-hint");
  } catch (err) {
    batchSound("err");
    setBatchResult(err.message, "err");
  }
}

function renderBatchItems() {
  const summary = document.getElementById("bcollect-summary");
  if (isReceivingBatch()) {
    renderReceivingList();
    return;
  }
  const expected = batchItems.filter((i) => i.expected_qty != null);
  if (expected.length) {
    const started = expected.filter(
      (i) => i.qty_scanned > 0 || i.tagged_before > 0
    ).length;
    // Physical boxes = loose scans + sealed cases (one box each).
    const boxes = batchItems.reduce(
      (n, i) => n + i.qty_scanned + (i.case_count || 0),
      0
    );
    const tagged = batchItems.reduce(
      (n, i) => n + (i.tagged_before || 0), 0
    );
    summary.textContent =
      `${started} of ${expected.length} expected products scanned · ` +
      `${boxes} box(es) total` +
      (tagged ? ` · ${tagged} already tagged (baseline)` : "");
    summary.hidden = false;
  } else {
    summary.hidden = true;
  }
  bEl.items.innerHTML = "";
  batchItems.forEach((item) => {
    bEl.items.append(collectItemCard(item));
  });
  renderMultibinBar();
}

// One collect-mode item card, with its qty stepper and per-row actions.
function collectItemCard(item) {
  const li = itemCard(item, "collect");
    const qty = document.createElement("span");
    qty.className = "bqty";
    qty.innerHTML = `
      <button type="button" data-d="-1">−</button>
      <span class="bqty__n">${item.qty_scanned}</span>
      <button type="button" data-d="1">+</button>`;
    qty.querySelectorAll("button").forEach((btn) =>
      btn.addEventListener("click", () =>
        adjustItemQty(item, item.qty_scanned + Number(btn.dataset.d))
      )
    );
    li.append(qty);
    if (item._binMismatch) {
      const warn = document.createElement("div");
      warn.className = "binwarn";
      warn.innerHTML = `
        <span>Saved bin is <b>${escapeHtml(item.bin_location || "?")}</b>, not ${escapeHtml(batch.bin_name)}.</span>
        <button class="reset" type="button" data-act="keep">Keep saved bin</button>
        <button class="reset" type="button" data-act="move">Move product to ${escapeHtml(batch.bin_name)} (Shopify)</button>`;
      warn.querySelector('[data-act="keep"]').addEventListener("click", () => {
        item._binMismatch = false;
        renderBatchItems();
      });
      warn.querySelector('[data-act="move"]').addEventListener("click", () =>
        moveItemBin(item)
      );
      li.append(warn);
      li.classList.add("bcell--stacked");
    }
    // Anything filling more than one box slot needs an answer before labels
    // print, and only the person holding the box can give it.
    if (item.resolved && item.other_bins) {
      li.append(kindRow(item));
      // The card is nowrap by default; without this the row lands beside the
      // name instead of under it.
      li.classList.add("bcell--stacked");
    }
    // Multi-select (Nick, 2026-09-01, ⚙ toggle): tick several products,
    // set all their bins in one pass - each write goes through the same
    // audited /api/bin-updates, logged per product with its undo.
    if (multibinOn() && item.resolved && item.sku) {
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.className = "multibin__cb";
      cb.checked = multibinSel.has(item.sku);
      cb.title = "Select for a bulk bin update";
      cb.addEventListener("click", (ev) => {
        ev.stopPropagation();
        if (cb.checked) multibinSel.add(item.sku);
        else multibinSel.delete(item.sku);
        renderMultibinBar();
      });
      li.prepend(cb);
    }
    return li;
}

// --- Bulk bin updates (Nick, 2026-09-01) ------------------------------------
const multibinEl = document.getElementById("multibin-select");
if (multibinEl) {
  multibinEl.checked = localStorage.getItem("multibinSelect") === "1";
  multibinEl.addEventListener("change", () => {
    localStorage.setItem("multibinSelect", multibinEl.checked ? "1" : "0");
    multibinSel.clear();
    if (batch && !isReceivingBatch()) renderBatchItems();
  });
}
const multibinSel = new Set();

function multibinOn() {
  return !!(multibinEl && multibinEl.checked);
}

function renderMultibinBar() {
  let bar = document.getElementById("multibin-bar");
  if (!multibinOn() || multibinSel.size === 0) {
    if (bar) bar.remove();
    return;
  }
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "multibin-bar";
    bar.className = "sortselbar";
    bEl.items.parentElement.insertBefore(bar, bEl.items);
  }
  bar.innerHTML = `
    <span>${multibinSel.size} product(s) selected</span>
    <button class="print__btn" id="multibin-go" type="button">Set bin for selected…</button>
    <button class="reset" id="multibin-clear" type="button">Clear selection</button>`;
  bar.querySelector("#multibin-clear").addEventListener("click", () => {
    multibinSel.clear();
    renderBatchItems();
  });
  bar.querySelector("#multibin-go").addEventListener("click", async () => {
    const skus = [...multibinSel];
    const bin = prompt(
      `Move ${skus.length} product(s) to which bin?\n\n` +
        skus.join(", ").slice(0, 300) +
        `\n\nEach write goes to Shopify AND the local records, logged ` +
        `per product with its own undo in History.`
    );
    if (bin === null || !bin.trim()) return;
    const target = bin.trim();
    let ok = 0;
    const errs = [];
    for (const sku of skus) {
      try {
        await postJson("/api/bin-updates", {
          target: sku,
          bin: target,
          changed_by: operatorEl.value || null,
        });
        ok++;
      } catch (err) {
        errs.push(`${sku}: ${err.message}`);
      }
    }
    multibinSel.clear();
    await refreshBatch();
    setBatchResult(
      `${ok} of ${skus.length} product(s) moved to ${target}` +
        (errs.length ? ` - failed: ${errs.join("; ").slice(0, 200)}` : "") +
        `.`,
      errs.length ? "err" : "ok"
    );
  });
}

// === Receiving list (stepless) =============================================
// The planner's save already printed the labels; this list shows every
// product sent to receive - preview card, expected count, tagged progress -
// with per-card [Reprint labels] and [Update count], flagged rows that
// explain their problem when selected, and a focus view with the printed /
// left-to-scan bar (Nick, 2026-08-25). Pairing itself happens on the C72.
let recvFocusId = null;

function recvProblemText(item) {
  if (item.skip_reason) return item.skip_reason;
  if (!item.resolved)
    return (
      "Not found: no product matches this code, so no labels printed. " +
      "Fix it in Shopify or link the code at the Scan Station."
    );
  const bin = (item.bin_location || "").trim();
  if (!bin || bin.toLowerCase() === "no bin assigned")
    return (
      "No bin assigned: labels are held because they couldn't say where " +
      "the box goes. Set a bin (product preview > bin chip), then use " +
      "Reprint labels."
    );
  return null;
}

// Pairing actions made FROM THIS TERMINAL (sweep pulls, relayed gun
// links) stack here so the Undo button can walk them back one action
// at a time (Nick, 2026-08-31: an accidental tag needed the product
// window to fix). Each entry: {epcs, itemId, label}.
let recvPairHistory = [];

function recvRememberPairs(epcs, itemId, label) {
  if (epcs && epcs.length) {
    recvPairHistory.push({
      epcs: [...epcs],
      itemId,
      label,
      batchId: batch && batch.id,
    });
  }
  renderRecvUndo();
}

function renderRecvUndo() {
  const btn = document.getElementById("recv-undo");
  if (!btn) return;
  // History never crosses batches: switching shipments drops it.
  if (
    recvPairHistory.length &&
    (!batch || recvPairHistory[recvPairHistory.length - 1].batchId !== batch.id)
  ) {
    recvPairHistory = [];
  }
  const show =
    batch && isReceivingBatch() && recvPairHistory.length > 0;
  btn.hidden = !show;
  if (show) {
    const last = recvPairHistory[recvPairHistory.length - 1];
    btn.textContent = `↩ Undo last pair (${last.epcs.length} tag${
      last.epcs.length === 1 ? "" : "s"
    } · ${last.label})`;
  }
}

async function recvUndoLastPair() {
  const last = recvPairHistory[recvPairHistory.length - 1];
  if (!last || !batch) return;
  const btn = document.getElementById("recv-undo");
  btn.disabled = true;
  let ok = 0;
  let problem = null;
  try {
    for (const epc of last.epcs) {
      try {
        await postJson(`/api/batches/${batch.id}/pair/undo`, {
          epc,
          item_id: last.itemId,
        });
        ok++;
      } catch (err) {
        problem = err.message;
      }
    }
    recvPairHistory.pop();
    await pullBatch(false);
    setBatchResult(
      `Undid ${ok} tag(s) on ${last.label}` +
        (problem ? ` · ${problem}` : "") +
        (recvPairHistory.length
          ? ` - Undo again walks further back.`
          : "."),
      ok ? "ok" : "err"
    );
  } finally {
    btn.disabled = false;
    renderRecvUndo();
  }
}

// Over-pair dismissals survive reloads (localStorage; item ids never
// repeat). An over-paired product stays LISTED and flagged until the
// operator dismisses it by hand (Nick, 2026-08-31).
function recvOverDismissedSet() {
  try {
    return new Set(
      JSON.parse(localStorage.getItem("recv_over_dismissed") || "[]")
    );
  } catch (err) {
    return new Set();
  }
}

function recvDismissOver(id) {
  const s = recvOverDismissedSet();
  s.add(id);
  try {
    localStorage.setItem("recv_over_dismissed", JSON.stringify([...s]));
  } catch (err) {
    /* per-session fallback is fine */
  }
}

// Boxes this row stands for: loose scans plus sealed cases.
function recvWant(item) {
  return (item.qty_scanned || 0) + (item.case_count || 0);
}

// Sold before a label reached it (Nick, 2026-09-09): dismissed rows
// leave the working list like fully-paired ones - the stock already
// went through Shopify and left, nothing here is owed.
const SOLD_BEFORE_LABEL = "sold before labelling";
function recvSoldDismissed(item) {
  return !!item.skipped && item.skip_reason === SOLD_BEFORE_LABEL;
}

// Fully-paired products leave the list (Nick, 2026-08-31) - unless
// they're OVER-paired, which stays as a flag until dismissed.
function recvItemDone(item, dismissed) {
  if (recvSoldDismissed(item)) return true;
  if (recvProblemText(item)) return false;
  const want = recvWant(item);
  const paired = item.paired_count || 0;
  if (want <= 0 || paired < want) return false;
  return paired === want || dismissed.has(item.id);
}

function renderReceivingList() {
  const summary = document.getElementById("recv-summary");
  const list = document.getElementById("recv-list");
  const empty = document.getElementById("recv-empty");
  const items = batchItems || [];
  document.getElementById("recv-done").hidden = !(
    batch && batch.status === "done"
  );
  // Full-shipment receives get the settle button (Nick, 2026-09-01):
  // "every box that arrived is labelled" - count the unused labels.
  // A FULLY-arrived shipment auto-closes before the press (SO 946), so
  // the button survives on done batches as the planner hand-off, until
  // the planner reports the stock update.
  const settleBtn = document.getElementById("recv-settle");
  if (settleBtn) {
    const rec = batch && batch.order_receipt;
    settleBtn.hidden = !(
      rec && !rec.stock_updated_at && batch.status !== "abandoned"
    );
    // Fully paired = nothing to count (Nick, 2026-09-02): the button
    // IS the planner hand-off. The batch stays open either way until
    // the planner's save closes it.
    const fullyPaired =
      items.length > 0 &&
      items.every(
        (i) =>
          !i.resolved ||
          i.skipped ||
          (i.paired_count || 0) >=
            (i.qty_scanned || 0) + (i.case_count || 0)
      ) &&
      items.some((i) => (i.paired_count || 0) > 0);
    settleBtn.textContent =
      (batch && batch.status === "done") ||
      fullyPaired ||
      (rec && rec.settled_at)
        ? "➡ Finish in TC-Planner (pre-filled)"
        : "✅ All boxes labelled - count unused";
  }
  const dismissed = recvOverDismissedSet();
  const done = items.filter((i) => recvItemDone(i, dismissed));
  const shown = items.filter((i) => !recvItemDone(i, dismissed));
  const printed = items.reduce((n, i) => n + (i.printed_count || 0), 0);
  const tagged = items.reduce((n, i) => n + (i.paired_count || 0), 0);
  const flagged = shown.filter((i) => recvProblemText(i)).length;
  const soldOff = items.filter(recvSoldDismissed).length;
  summary.textContent = items.length
    ? `${items.length} product(s) · ${printed} label(s) printed · ` +
      `${tagged} tagged` +
      (flagged ? ` · ⚠ ${flagged} flagged` : "") +
      (soldOff ? ` · ${soldOff} dismissed (sold)` : "") +
      (done.length - soldOff > 0
        ? ` · ${done.length - soldOff} fully tagged (hidden)`
        : "")
    : "";
  summary.hidden = !items.length;
  empty.hidden = !!items.length;
  if (recvFocusId != null && !shown.some((i) => i.id === recvFocusId)) {
    recvFocusId = null;
  }
  list.innerHTML = "";
  if (items.length && !shown.length) {
    const all = document.createElement("li");
    all.className = "recvdivider";
    all.textContent = "every product is fully tagged ✓";
    list.append(all);
  }
  // The focused product leads the list with a divider under it - the
  // current work sits on top, the rest waits below (Nick, 2026-08-31).
  const focusedItem =
    recvFocusId != null ? shown.find((i) => i.id === recvFocusId) : null;
  if (focusedItem) {
    list.append(recvCard(focusedItem));
    const divider = document.createElement("li");
    divider.className = "recvdivider";
    divider.textContent = "products to scan";
    list.append(divider);
    recvAppendWithPushDividers(
      list,
      shown.filter((i) => i.id !== focusedItem.id)
    );
  } else {
    recvAppendWithPushDividers(list, shown);
  }
  syncRecvSweepPoll(
    !!focusedItem && !recvProblemText(focusedItem) && batch && !batch.completed_at
  );
  renderRecvUndo();
}

document
  .getElementById("recv-undo")
  .addEventListener("click", recvUndoLastPair);

// A repeat planner push for the SAME stock order folds into this batch
// (batches are one-per-SO since 2026-09-23), so dated dividers mark
// where each push's products start - "order pushed Sep 21" over the
// first wave, "order pushed Sep 23" over the top-up (Nick,
// 2026-09-23). Rows are in creation order already, so a divider is
// just a date change between neighbours; drawn only when the batch
// really spans more than one push day.
function recvPushDay(item) {
  return item.first_scanned_at ? item.first_scanned_at.slice(0, 10) : null;
}

function recvAppendWithPushDividers(list, cards) {
  const days = new Set(cards.map(recvPushDay).filter(Boolean));
  if (days.size < 2) {
    cards.forEach((item) => list.append(recvCard(item)));
    return;
  }
  let prev = null;
  cards.forEach((item) => {
    const day = recvPushDay(item);
    if (day && day !== prev) {
      const divider = document.createElement("li");
      divider.className = "recvdivider";
      divider.textContent =
        "order pushed " +
        new Date(item.first_scanned_at).toLocaleDateString(undefined, {
          month: "short",
          day: "numeric",
        });
      list.append(divider);
      prev = day;
    }
    list.append(recvCard(item));
  });
}

function recvCard(item) {
  const li = document.createElement("li");
  li.className = "bcell bcell--stacked recvcard bcell--clickable";
  const problem = recvProblemText(item);
  const focused = item.id === recvFocusId;
  const received = item.qty_scanned || 0;
  const printedN = item.printed_count || 0;
  const taggedN = item.paired_count || 0;
  const planner = item.expected_qty;
  const want = recvWant(item);
  const over = !problem && want > 0 && taggedN > want;
  if (problem || over) li.classList.add("bcell--warn");
  else if (received > 0 && taggedN >= received)
    li.classList.add("bcell--exact");
  if (focused) li.classList.add("recvcard--focused");
  // Labels the planner's save could not queue (count raised, or a bin
  // arrived late) are a fixable gap, not a mystery.
  const missing = !problem ? Math.max(0, received - printedN) : 0;
  const bin = (item.bin_location || "").trim();
  li.innerHTML = `
    <div class="recvcard__head">
      ${
        item.image_url
          ? `<img class="bcell__img" src="${escapeHtml(item.image_url)}" alt="" loading="lazy" />`
          : `<span class="bcell__img bcell__img--empty"></span>`
      }
      <div class="bcell__info">
        <div class="bcell__name">${escapeHtml(
          item.resolved ? itemDisplayName(item) : item.scanned_code || "?"
        )}${
          item.nickname
            ? ` <span class="vendorname" title="What the vendor calls this product - the box says THIS, not our SKU or title">(${escapeHtml(item.nickname)})</span>`
            : ""
        }</div>
        <div class="bcell__meta">${
          item.sku
            ? "SKU: " + escapeHtml(item.sku)
            : item.resolved
              ? "no SKU"
              : "⚠ unknown code"
        }${bin && bin.toLowerCase() !== "no bin assigned" ? " · Bin: " + escapeHtml(bin) : ""}</div>
        <div class="bcell__meta">Expected ${
          planner != null ? planner : "?"
        } from the planner${
          planner != null && received !== planner
            ? ` · count updated to ${received}`
            : ""
        }</div>
        ${
          problem
            ? `<div class="bcell__meta recvcard__flag">⚠ Problem${focused ? "" : " · select to see why"}</div>`
            : over
              ? `<div class="bcell__meta recvcard__flag">⚠ ${taggedN - want} more tag(s) than boxes${focused ? "" : " · select to review"}</div>`
              : missing
                ? `<div class="bcell__meta recvcard__flag">⚠ ${missing} label(s) not printed yet${focused ? "" : " · select for details"}</div>`
                : ""
        }
      </div>
      <span class="bcell__tracker" title="tags paired / products received">${taggedN}/${received}</span>
    </div>
    ${focused ? recvFocusBody(item, problem, received, printedN, taggedN, missing, over) : ""}
    <div class="recvcard__btns">
      ${
        item.resolved && !item.skip_reason
          ? `<button class="reset" type="button" data-act="reprint"
              title="Print fresh labels for this product - the received count is not changed">🖨 Reprint labels</button>
             <button class="reset" type="button" data-act="count"
              title="Correct how many were actually received, in case the planner was off">✎ Update count</button>`
          : ""
      }
      ${
        item.resolved && (item.sku || item.barcode)
          ? `<button class="reset" type="button" data-act="edit"
              title="Open the full product window - set its bin (the held-for-a-bin fix), flags, label names, vendor, tags">📦 Edit product</button>`
          : ""
      }
      ${
        focused && item.resolved && !item.skipped
          ? `<button class="reset" type="button" data-act="soldout"
              title="This stock sold before it could be labelled. The row leaves the list and its outstanding printed labels stop counting as owed - OUR accounting only. Received counts, Shopify and the planner are untouched (the sale already went through Shopify). Undoable from History.">💸 Sold - dismiss</button>`
          : ""
      }
      ${
        focused && item.resolved && item.sku
          ? `<button class="reset" type="button" data-act="nickname"
              title="What the VENDOR calls this product (the box's own wording) - shown bracketed and highlighted on receiving lists, and typing it finds the product anywhere">🏷 ${item.nickname ? "Vendor name…" : "Add vendor name…"}</button>`
          : ""
      }
      ${
        !item.resolved
          ? `<button class="reset" type="button" data-act="link"
              title="Pick the product this code really is - it becomes a lookup alias (Shopify untouched) and the row rejoins the shipment with labels queued">🔗 Link to product</button>`
          : ""
      }
      ${
        focused
          ? `<button class="reset" type="button" data-act="cancel">Cancel</button>`
          : ""
      }
    </div>`;
  li.querySelectorAll("[data-act]").forEach((btn) =>
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (btn.dataset.act === "cancel") {
        recvFocusId = null;
        renderReceivingList();
      } else if (btn.dataset.act === "reprint") {
        openRecvReprint(item);
      } else if (btn.dataset.act === "count") {
        openRecvCount(item);
      } else if (btn.dataset.act === "link") {
        openRecvLink(item);
      } else if (btn.dataset.act === "missing") {
        recvPrintMissing(item, missing);
      } else if (btn.dataset.act === "sweep") {
        recvPullSweep(item);
      } else if (btn.dataset.act === "edit") {
        openProductHistory(item.sku || item.barcode);
      } else if (btn.dataset.act === "nickname") {
        recvSetNickname(item);
      } else if (btn.dataset.act === "soldout") {
        recvDismissSold(item);
      } else if (btn.dataset.act === "overdismiss") {
        recvDismissOver(item.id);
        recvFocusId = null;
        renderReceivingList();
        setBatchResult(
          `${itemDisplayName(item)}: over-pair flag dismissed - the ` +
            `extra tag(s) stay paired.`,
          "ok"
        );
      }
    })
  );
  li.addEventListener("click", () => {
    recvFocusId = focused ? null : item.id;
    renderReceivingList();
  });
  return li;
}

// Sold before a label reached it (Nick, 2026-09-09): drop the row from
// the receiving list. OUR accounting only - the sale already went
// through Shopify, so nothing else moves. Undoable from History.
async function recvDismissSold(item) {
  if (
    !confirm(
      `Dismiss ${itemDisplayName(item)} - sold before labelling?\n\n` +
        `The row leaves this list and its outstanding printed labels ` +
        `stop counting as owed. Received counts, Shopify and the ` +
        `planner are untouched. Undoable from History.`
    )
  )
    return;
  try {
    const res = await postJson(
      `/api/batches/${batch.id}/items/${item.id}/dismiss-sold`,
      { worker: operatorEl.value || null }
    );
    await pullBatch(false);
    renderReceivingList();
    setBatchResult(res.message, "ok");
  } catch (err) {
    setBatchResult(err.message, "err");
  }
}

// Vendor nickname (Nick, 2026-09-01): what the BOX says when the
// vendor's labelling has nothing to do with our SKU or title ("RN" is
// "Collimating Eyepiece For Newtonian" on the carton). Stored as a
// nickname alias - shown bracketed + highlighted, and typing it finds
// the product anywhere. One per product; saving replaces the old.
async function recvSetNickname(item) {
  const cur = item.nickname || "";
  const name = prompt(
    "What does the vendor call this product (the box's own wording)?\n\n" +
      "Shown bracketed and highlighted on receiving lists, and typing " +
      "it finds the product anywhere. Max 64 characters. Clear the box " +
      "to remove it.",
    cur
  );
  if (name === null) return;
  const trimmed = name.trim().slice(0, 64);
  try {
    if (!trimmed) {
      if (cur) {
        const res = await apiFetch(
          `/api/barcode-aliases/${encodeURIComponent(cur)}`,
          { method: "DELETE" }
        );
        if (!res.ok && res.status !== 404) {
          throw new Error("Could not remove the vendor name.");
        }
      }
    } else {
      await postJson("/api/barcode-aliases", {
        alias_barcode: trimmed,
        target: item.sku,
        created_by: operatorEl.value || null,
        kind: "nickname",
      });
    }
    await pullBatch(false);
    setBatchResult(
      trimmed
        ? `Vendor name saved - ${itemDisplayName(item)} shows as ` +
          `(${trimmed}) and the name now finds the product anywhere.`
        : "Vendor name removed.",
      "ok"
    );
  } catch (err) {
    alert(err.message);
  }
}

function recvFocusBody(item, problem, received, printedN, taggedN, missing, over) {
  if (problem) {
    return `<div class="recvcard__body recvcard__body--problem">${escapeHtml(problem)}</div>`;
  }
  // Over-paired: more tags answered to this product than it has boxes -
  // usually a spare or blank label swept by accident. Stays flagged
  // until dismissed by hand (Nick, 2026-08-31).
  const overBlock = over
    ? `<div class="recvcard__body recvcard__body--problem">
        ⚠ ${taggedN} tag(s) paired against ${recvWant(item)} box(es).
        A spare or blank label in range may have been swept onto this
        product - unpair it from Edit product → live tags. If the extra
        tag is real (a box the count missed), fix the count or dismiss.
        <button class="reset" type="button" data-act="overdismiss">Dismiss this flag</button>
      </div>`
    : "";
  const target = Math.max(received, printedN, taggedN, 1);
  const tagPct = Math.round((taggedN / target) * 100);
  const prtPct = Math.round((Math.max(printedN - taggedN, 0) / target) * 100);
  const left = Math.max(0, received - taggedN);
  return `
    <div class="recvcard__body">
      <div class="recvbar2" title="green = tagged, amber = printed but not yet tagged">
        <span class="recvbar2__tag" style="width:${tagPct}%"></span>
        <span class="recvbar2__prt" style="width:${prtPct}%"></span>
      </div>
      <div class="recvcard__caption">
        ${printedN} label(s) printed · ${taggedN} tagged · ${left} left to scan
        ${
          missing
            ? ` · <button class="reset recvcard__missing" type="button" data-act="missing">🖨 Print ${missing} missing label(s)</button>`
            : ""
        }
      </div>
      <div class="recvcard__sweep">
        <button class="reset sweep-pull" type="button" data-act="sweep"
          title="Pair every NEW tag from the most recent C72 sweep (SWEEP tab → SEND on the gun) to this product. Tags already tied to anything are skipped, never stolen.">📶 Use latest C72 sweep</button>
        <span class="bulknote" id="recv-sweep-note" hidden></span>
      </div>
      ${overBlock}
    </div>`;
}

// --- Focused-card sweep pairing (Nick, 2026-08-31) --------------------------
// The web terminal can now finish a receiving product without the gun's
// pair screen: pull the newest C72 sweep and every unowned tag in it
// pairs to the FOCUSED product - same mechanics as the gun's held sweep.
// The chip beside the button watches for waiting sweeps, colored like
// the Scan Station's bulk chip: green = matches the labels left to scan,
// yellow = partial or over-sweep, red = nothing waiting or an
// all-tagged sweep.
let recvSweepTimer = null;
let recvSweepSummary = null;

function sweepNoteState(s, remaining) {
  if (!s || !s.exists) {
    return {
      cls: "bulknote--red",
      text: "no sweep waiting - SWEEP then SEND on the gun",
    };
  }
  let out;
  if (s.untagged === 0) {
    out = {
      cls: "bulknote--red",
      text: `sweep holds 0 untagged of ${s.epc_count} heard`,
    };
  } else if (remaining > 0 && s.untagged === remaining) {
    out = {
      cls: "bulknote--green",
      text: `${s.untagged} tag(s) waiting - matches the ${remaining} left`,
    };
  } else {
    out = {
      cls: "bulknote--yellow",
      text:
        `${s.untagged} untagged tag(s) waiting` +
        (remaining > 0 ? ` vs ${remaining} left` : ""),
    };
  }
  if (s.age_seconds > 120) {
    out.text += ` · sweep is ${Math.round(s.age_seconds / 60)}m old`;
  }
  return out;
}

function syncRecvSweepPoll(active) {
  if (!active) {
    if (recvSweepTimer) clearInterval(recvSweepTimer);
    recvSweepTimer = null;
    recvSweepSummary = null;
    return;
  }
  renderRecvSweepNote();
  if (!recvSweepTimer) {
    pollRecvSweepNote();
    recvSweepTimer = setInterval(pollRecvSweepNote, 4000);
  }
}

async function pollRecvSweepNote() {
  if (recvFocusId == null || !batch) return;
  try {
    recvSweepSummary = await apiJson("/api/epc-captures/latest-summary");
  } catch (err) {
    recvSweepSummary = null;
  }
  renderRecvSweepNote();
}

function renderRecvSweepNote() {
  const note = document.getElementById("recv-sweep-note");
  if (!note) return;
  const item = (batchItems || []).find((i) => i.id === recvFocusId);
  if (!item) {
    note.hidden = true;
    return;
  }
  const remaining = Math.max(
    0,
    (item.qty_scanned || 0) - (item.paired_count || 0)
  );
  // Fully paired = nothing to wait for: the chip leaves instead of
  // turning red at a job well done (Nick, 2026-08-31).
  if (remaining === 0) {
    note.hidden = true;
    return;
  }
  const st = sweepNoteState(recvSweepSummary, remaining);
  note.className = `bulknote ${st.cls}`;
  note.textContent = st.text;
  note.hidden = false;
}

async function recvPullSweep(item) {
  const operator = operatorEl.value;
  if (!operator) {
    alert("Pick who's scanning (top right) first.");
    return;
  }
  try {
    const capRes = await apiFetch("/api/epc-captures/latest");
    if (capRes.status === 404) {
      setBatchResult(
        "No C72 sweeps received yet - SWEEP then SEND on the gun first.",
        "err"
      );
      return;
    }
    const cap = await capRes.json();
    const un = await postJson(`/api/batches/${batch.id}/unlinked`, {
      epcs: cap.epcs || [],
    });
    const orphans = un.unlinked || [];
    if (!orphans.length) {
      setBatchResult(
        `Sweep (${cap.epc_count} tag(s) heard): every one is already ` +
          `linked - nothing new to pair.`,
        "err"
      );
      return;
    }
    let ok = 0;
    let done = false;
    let problem = null;
    const landed = [];
    for (const epc of orphans) {
      try {
        const r = await postJson(`/api/batches/${batch.id}/pair`, {
          epc,
          item_id: item.id,
          created_by: operator,
        });
        if (r.companion) continue; // confirmed, never counted
        ok++;
        landed.push(epc);
        if (r.receiving_done) done = true;
      } catch (err) {
        problem = err.message;
      }
    }
    recvRememberPairs(landed, item.id, itemDisplayName(item));
    setBatchResult(
      `Sweep: ${ok} tag(s) paired to ${itemDisplayName(item)}` +
        (problem ? ` · ${problem}` : "") +
        (done ? " - every box is paired, shipment complete ✓" : "") +
        ".",
      ok ? "ok" : "err"
    );
    await pullBatch(false);
    pollRecvSweepNote();
  } catch (err) {
    setBatchResult(err.message, "err");
  }
}

async function recvPrintMissing(item, missing) {
  if (!missing) return;
  try {
    const res = await postJson(
      `/api/batches/${batch.id}/items/${item.id}/labels`,
      { quantity: missing, requested_by: operatorEl.value || null }
    );
    setBatchResult(
      `${res.count} label(s) queued for ${itemDisplayName(item)} ✓ - ` +
        `the Queue tab tracks them.`,
      "ok"
    );
    await pullBatch(false);
  } catch (err) {
    setBatchResult(err.message, "err");
  }
}

// --- the two small windows (reprint count / received count) ---------------
let recvModalItemId = null;

function recvModalProduct(host, item) {
  document.getElementById(host).innerHTML = `
    ${
      item.image_url
        ? `<img class="bcell__img" src="${escapeHtml(item.image_url)}" alt="" />`
        : `<span class="bcell__img bcell__img--empty"></span>`
    }
    <div>
      <div class="bcell__name">${escapeHtml(itemDisplayName(item))}</div>
      <div class="bcell__meta">${item.sku ? "SKU: " + escapeHtml(item.sku) : ""}</div>
    </div>`;
}

function openRecvReprint(item) {
  recvModalItemId = item.id;
  recvModalProduct("recv-reprint-product", item);
  document.getElementById("recv-reprint-count").value = 1;
  document.getElementById("recv-reprint-overlay").hidden = false;
}

function openRecvCount(item) {
  recvModalItemId = item.id;
  recvModalProduct("recv-count-product", item);
  document.getElementById("recv-count-num").value = item.qty_scanned || 0;
  document.getElementById("recv-count-expected").textContent =
    `Inventory planner expected: ${
      item.expected_qty != null ? item.expected_qty : "?"
    }`;
  document.getElementById("recv-count-overlay").hidden = false;
}

// Link an unknown planner row to the right product (Nick, 2026-08-25):
// same alias-then-resolve flow as the batch Check step, without leaving
// the receiving list. The server re-queues the row's labels on success.
function openRecvLink(item) {
  recvModalItemId = item.id;
  document.getElementById("recv-link-product").innerHTML = `
    <span class="bcell__img bcell__img--empty"></span>
    <div>
      <div class="bcell__name">${escapeHtml(item.scanned_code || "?")}</div>
      <div class="bcell__meta">the planner sent ${item.qty_scanned || 0} of this unknown code</div>
    </div>`;
  document.getElementById("recv-link-target").value = "";
  document.getElementById("recv-link-msg").textContent = "";
  document.getElementById("recv-link-overlay").hidden = false;
  document.getElementById("recv-link-target").focus();
}

async function recvLinkGo() {
  const item = (batchItems || []).find((i) => i.id === recvModalItemId);
  const msg = document.getElementById("recv-link-msg");
  const term = document.getElementById("recv-link-target").value.trim();
  if (!item || !batch || !term) return;
  msg.textContent = `Looking up ${term}…`;
  let p;
  try {
    p = await apiJson(`/api/products/by-barcode/${encodeURIComponent(term)}`);
  } catch (err) {
    msg.textContent = `No product found for ${term} (${err.message}).`;
    return;
  }
  const title = p.product_title || p.sku || term;
  if (
    !confirm(
      `Link ${item.scanned_code} to "${title}"` +
        (p.sku ? ` (SKU ${p.sku})` : "") +
        `?\n\nThe planner's code will find this product from now on; ` +
        `Shopify is not touched. Labels for its boxes queue right away. ` +
        `Unlink any time in History.`
    )
  )
    return;
  const btn = document.getElementById("recv-link-go");
  btn.disabled = true;
  try {
    await postJson("/api/barcode-aliases", {
      alias_barcode: item.scanned_code,
      target: p.sku || term,
      created_by: operatorEl.value || null,
    });
    const res = await postJson(
      `/api/batches/${batch.id}/items/${item.id}/resolve`, {}
    );
    document.getElementById("recv-link-overlay").hidden = true;
    setBatchResult(res.message, res.resolved ? "ok" : "err");
    await pullBatch(false);
  } catch (err) {
    msg.textContent = err.message;
  } finally {
    btn.disabled = false;
  }
}
document.getElementById("recv-link-go").addEventListener("click", recvLinkGo);
document.getElementById("recv-link-target").addEventListener("keydown", (e) => {
  if (e.key === "Enter") recvLinkGo();
});

document.getElementById("recv-reprint-cancel").addEventListener("click", () => {
  document.getElementById("recv-reprint-overlay").hidden = true;
});
document.getElementById("recv-count-cancel").addEventListener("click", () => {
  document.getElementById("recv-count-overlay").hidden = true;
});
document.getElementById("recv-link-cancel").addEventListener("click", () => {
  document.getElementById("recv-link-overlay").hidden = true;
});
["recv-reprint-overlay", "recv-count-overlay", "recv-link-overlay"].forEach(
  (id) => {
    document.getElementById(id).addEventListener("click", (e) => {
      if (e.target === e.currentTarget) e.currentTarget.hidden = true;
    });
  }
);
// The − / + steppers beside each number box.
document.querySelectorAll(".recvcounter__btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const input = document.getElementById(btn.dataset.for);
    const min = Number(input.min || 0);
    const max = Number(input.max || 500);
    const next = (Number(input.value) || 0) + Number(btn.dataset.d);
    input.value = Math.min(max, Math.max(min, next));
  });
});

document.getElementById("recv-reprint-go").addEventListener("click", async () => {
  const item = (batchItems || []).find((i) => i.id === recvModalItemId);
  if (!item || !batch) return;
  const qty = Math.max(1, Math.min(50,
    Number(document.getElementById("recv-reprint-count").value) || 1));
  const btn = document.getElementById("recv-reprint-go");
  btn.disabled = true;
  try {
    const res = await postJson(
      `/api/batches/${batch.id}/items/${item.id}/labels`,
      { quantity: qty, requested_by: operatorEl.value || null }
    );
    document.getElementById("recv-reprint-overlay").hidden = true;
    setBatchResult(
      `${res.count} label(s) queued for ${itemDisplayName(item)} ✓ - ` +
        `the Queue tab tracks them. The received count is unchanged.`,
      "ok"
    );
    await pullBatch(false);
  } catch (err) {
    setBatchResult(err.message, "err");
    document.getElementById("recv-reprint-overlay").hidden = true;
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("recv-count-save").addEventListener("click", async () => {
  const item = (batchItems || []).find((i) => i.id === recvModalItemId);
  if (!item || !batch) return;
  const qty = Math.max(0, Math.min(500,
    Number(document.getElementById("recv-count-num").value) || 0));
  const btn = document.getElementById("recv-count-save");
  btn.disabled = true;
  try {
    const updated = await postJson(
      `/api/batches/${batch.id}/items/${item.id}/qty`,
      { qty }
    );
    Object.assign(item, updated);
    document.getElementById("recv-count-overlay").hidden = true;
    const planner = item.expected_qty;
    setBatchResult(
      `Received count set to ${qty}` +
        (planner != null && planner !== qty
          ? ` (planner said ${planner})`
          : "") +
        ` ✓` +
        (qty > (item.printed_count || 0)
          ? ` - ${qty - (item.printed_count || 0)} box(es) have no label ` +
            `yet; the card offers to print them.`
          : ""),
      "ok"
    );
    renderReceivingList();
  } catch (err) {
    setBatchResult(err.message, "err");
  } finally {
    btn.disabled = false;
  }
});

function kindRow(item) {
  const bundle = item.kind === "bundle";
  const row = document.createElement("div");
  row.className = "kindrow" + (bundle ? " kindrow--bundle" : "");
  const n = boxSlots(item);
  row.innerHTML = `
    <span class="kindrow__what">${
      bundle
        ? "Bundle - made of separate products, so nothing here gets a tag"
        : `Multi-box product - ${n} boxes, one label each`
    }</span>
    <button class="reset" type="button" data-act="toggle">${
      bundle ? "No - it's one product in " + n + " boxes" : "No - it's a bundle"
    }</button>
    ${
      bundle
        ? `<button class="reset" type="button" data-act="drop">Drop from RFID entirely</button>`
        : ""
    }`;
  row.querySelector('[data-act="toggle"]').addEventListener("click", () =>
    setItemKind(item, bundle ? "multi_box" : "bundle", false)
  );
  const drop = row.querySelector('[data-act="drop"]');
  if (drop) {
    drop.addEventListener("click", () => {
      if (
        !confirm(
          `Drop "${itemDisplayName(item)}" from the RFID system?\n\n` +
            `It won't be added to future batches and will never be ` +
            `labelled. Its component products are unaffected - they keep ` +
            `their own tags.\n\nYou can undo this from the product's panel ` +
            `in History.`
        )
      )
        return;
      setItemKind(item, "bundle", true);
    });
  }
  return row;
}

async function setItemKind(item, kind, excluded) {
  try {
    const data = await postJson(
      `/api/batches/${batch.id}/items/${item.id}/kind`,
      // Blank is fine — the server falls back to whoever started the batch.
      { kind, excluded, updated_by: operatorEl.value.trim() || null }
    );
    setBatchResult(data.message, "ok");
    await pullBatch(false);
  } catch (err) {
    setBatchResult(err.message, "err");
  }
  bEl.scan.focus();
}

async function adjustItemQty(item, qty) {
  qty = Math.max(0, qty);
  try {
    const updated = await postJson(
      `/api/batches/${batch.id}/items/${item.id}/qty`,
      { qty }
    );
    Object.assign(item, updated);
    renderBatchItems();
  } catch (err) {
    setBatchResult(err.message, "err");
  }
  bEl.scan.focus();
}

// The one Shopify write reachable from a batch — the existing, confirmed
// Scan Station bin update, re-used verbatim.
async function moveItemBin(item) {
  if (
    !confirm(
      `Update the bin on "${item.product_title}" in Shopify: ` +
        `${item.bin_location || "(none)"} → ${batch.bin_name}?`
    )
  )
    return;
  try {
    await postJson("/api/bin-updates", {
      target: item.sku || item.barcode,
      bin: batch.bin_name,
      changed_by: operatorEl.value || null,
    });
    item.bin_location = batch.bin_name;
    item._binMismatch = false;
    renderBatchItems();
    setBatchResult(`Bin updated to ${batch.bin_name} in Shopify.`, "ok");
  } catch (err) {
    setBatchResult(err.message, "err");
  }
}

bEl.toLabels.addEventListener("click", () => {
  if (!labelItems().length) {
    setBatchResult("Nothing scanned yet - scan at least one known product.", "err");
    return;
  }
  showBatchStage("labels");
});

// --- Stage 2: check ---------------------------------------------------------
// Only items needing a human decision appear here (server decides why);
// everything else sails straight through to label queueing.
function labelItems() {
  // A row can be ALL sealed cases (loose scans converted), so
  // qty_scanned alone made it vanish from every label count
  // (Nick, 2026-09-16).
  return batchItems.filter(
    (i) => i.resolved && (i.qty_scanned > 0 || (i.case_count || 0) > 0)
  );
}

const FLAG_TEXT = {
  skipped: "skipped - couldn't be scanned, nothing counted",
  "tagged-not-detected":
    "tags on file for this shelf, but the sweep read none - find the " +
    "tagged box(es) before printing more",
  bundle: "a bundle - no box of its own to tag",
  "not-on-shelf":
    "Shopify expects this here, but none was scanned - it's in another " +
    "bin or the count is wrong",
  ambiguous: "barcode matches several listings",
  "count-mismatch": "count differs from Shopify",
  "unconfirmed-name": "serial name not confirmed",
  unresolved: "unknown barcode",
  "bad-chars":
    "the SKU or barcode has a broken special character - records can't " +
    "match until it's fixed",
  "tags-unheard":
    "the shelf sweep heard fewer tags than expected - tap to resolve " +
    "(scan one-by-one on the gun, or count by eye)",
  "tags-silent":
    "tags were expected on this shelf but the sweep heard NONE - find " +
    "the stickered boxes before printing more",
  "wrong-bin": "saved bin is a different shelf",
  "double-count":
    "boxes scanned AND marked already-tagged - if the stickered boxes " +
    "were among the scans, lower the scan count (−/+ in the editor)",
};

// Check-list importance (mirror of the server's ranking): biggest
// problems first, count-mismatch explicitly LAST (Nick, 2026-08-26).
const FLAG_RANK = {
  unresolved: 9,
  "bad-chars": 8,
  ambiguous: 7,
  "tags-silent": 6,
  "wrong-bin": 5,
  "tags-unheard": 4,
  "double-count": 3,
  "tagged-not-detected": 3,
  skipped: 2,
  bundle: 2,
  "unconfirmed-name": 2,
  "not-on-shelf": 1,
  "count-mismatch": 0,
};
function entryRank(e) {
  return (e.flags || []).reduce(
    (m, f) => Math.max(m, FLAG_RANK[f] ?? 1),
    0
  );
}

let checkEntries = [];
let bitemEntry = null;
let bitemIdx = 0;
// Wrong-bin warnings the operator chose to ignore for this batch only.
let ignoredBinItems = new Set();
// Odd-barcode rescue state (unresolved scans).
let oddList = [];
let oddIdx = 0;
let bitemLabelMode = "header";

// --- Side trips ------------------------------------------------------------
// Boxes found on the wrong shelf, caught at Check before anything prints.
// Rather than rewriting the product's bin, carry them to where the rest of
// that product already lives: a small batch for THAT bin, whose labels — and
// so whose tags — name the right shelf. Nothing to reprint or peel off.
let parentBatch = null;

function renderStrayBins(bins) {
  const wrap = document.getElementById("bcheck-strays");
  wrap.innerHTML = "";
  // A side trip can't start from inside a side trip; finish this one first.
  if (!bins.length || (batch && batch.parent_batch_id)) return;
  bins.forEach((b) => {
    const row = document.createElement("div");
    row.className = "kindrow";
    row.innerHTML = `
      <span class="kindrow__what">${b.count} product(s) here actually live in
        <b>${escapeHtml(b.bin)}</b> - ${escapeHtml(b.skus.filter(Boolean).join(", "))}</span>
      <button class="reset" type="button">Take them to ${escapeHtml(b.bin)}…</button>`;
    row.querySelector("button").addEventListener("click", () => divertToBin(b.bin));
    wrap.append(row);
  });
}

async function divertToBin(binName) {
  if (
    !confirm(
      `Carry these boxes to ${binName}?\n\n` +
        `They leave this batch and become a short side trip for ${binName}: ` +
        `their labels print with ${binName} on them, you pair them there, ` +
        `then you're back here.\n\n` +
        `Nothing has printed yet, so there's nothing to reprint or peel off.`
    )
  )
    return;
  try {
    const res = await postJson(`/api/batches/${batch.id}/divert`, {
      bin: binName,
      created_by: operatorEl.value.trim() || null,
    });
    if (res.labels_held) {
      // Whole-strip printing: the trip's labels wait for THIS bin's
      // PRINT step, so stay here - the trip is walked after printing.
      batch = res.parent;
      batchItems = [];
      await pullBatch(false);
      loadBatchReview();
      setBatchResult(res.message, "ok");
      return;
    }
    parentBatch = res.parent;
    batch = res.batch;
    batchItems = [];
    await pullBatch(false);
    renderSideTrip();
    showBatchStage("pair");
    setBatchResult(res.message, "ok");
  } catch (err) {
    setBatchResult(err.message, "err");
  }
}

function renderSideTrip() {
  const bar = document.getElementById("batch-sidetrip");
  const on = !!(batch && batch.parent_batch_id);
  bar.hidden = !on;
  if (!on) return;
  // The parent's stray offer is still sitting in the DOM; leaving it there
  // would invite a side trip from inside a side trip.
  document.getElementById("bcheck-strays").innerHTML = "";
  document.getElementById("sidetrip-what").textContent =
    `Side trip - tagging strays into ${batch.bin_name}` +
    (parentBatch ? `, then back to ${parentBatch.bin_name}` : "") +
    `. These labels say ${batch.bin_name}.`;
}

document
  .getElementById("sidetrip-finish")
  .addEventListener("click", async () => {
    const left = batchItems.filter(
      (i) => i.resolved && i.paired_count < (i.labels_total ?? i.qty_scanned)
    );
    if (
      left.length &&
      !confirm(
        `${left.length} product(s) here still have labels waiting to be ` +
          `paired.\n\nClose the side trip anyway?`
      )
    )
      return;
    try {
      const res = await postJson(`/api/batches/${batch.id}/close-divert`, {});
      if (res.parent) {
        batch = res.parent;
        batchItems = [];
        parentBatch = null;
        await pullBatch(false);
        renderSideTrip();
        showBatchStage("labels");
        loadBatchReview();
      }
      setBatchResult(res.message, "ok");
    } catch (err) {
      setBatchResult(err.message, "err");
    }
  });

// The re-tag shelf-sweep banner: shown while a previously-done bin's
// batch has no shelf sweep yet; clears itself when the C72's sweep
// arrives (pullBatch watches for the flip).
function updateShelfWarn() {
  const warn = document.getElementById("bcheck-shelfwarn");
  if (!warn) return;
  warn.hidden = !(
    batch &&
    batch.prev_done_at &&
    !batch.shelf_swept_at &&
    !isReceivingBatch()
  );
}

async function loadBatchReview(showAll) {
  const list = document.getElementById("bcheck-list");
  const empty = document.getElementById("bcheck-empty");
  updateShelfWarn();
  empty.hidden = true;
  list.innerHTML = '<li class="recent__empty">Checking the batch…</li>';
  try {
    const data = await apiJson(`/api/batches/${batch.id}/review`);
    renderStrayBins(data.stray_bins || []);
    checkEntries = data.items
      .map((e) => ({
        ...e,
        flags: e.flags.filter(
          (f) => !(f === "wrong-bin" && ignoredBinItems.has(e.item.id))
        ),
      }))
      .filter((e) => e.flags.length);
    // Re-rank after the local wrong-bin filter: the server already
    // orders biggest-problem-first, but ignoring a wrong-bin warning
    // can demote an entry to count-mismatch-only, which belongs at the
    // bottom (Nick, 2026-08-26).
    checkEntries.sort((a, b) => entryRank(b) - entryRank(a));
    if (showAll) {
      // "Review all products": every scanned product, flagged or not, so
      // label names/SKUs can be edited before printing.
      const flagged = new Map(checkEntries.map((e) => [e.item.id, e]));
      checkEntries = labelItems().map(
        (item) =>
          flagged.get(item.id) || { item, flags: [], candidates: [] }
      );
    }
    renderCheckList();
  } catch (err) {
    list.innerHTML = `<li class="recent__empty">${escapeHtml(err.message)}</li>`;
  }
}

// Draw the Check list from what's already loaded. Kept apart from the fetch
// because re-checking is expensive — it asks Shopify about every item — and
// closing an edit window is no reason to pay for it. The ↻ button does that.
function renderCheckList() {
  const list = document.getElementById("bcheck-list");
  const empty = document.getElementById("bcheck-empty");
  // Only offer the bulk re-check when there's something unknown to re-check.
  document.getElementById("bcheck-recheck").hidden = !checkEntries.some(
    (e) => !e.item.resolved
  );
  list.innerHTML = "";
  empty.hidden = checkEntries.length > 0;
  if (!checkEntries.length) return;
  checkEntries.forEach((entry) => {
    const li = itemCard(entry.item, "collect");
    // Shelf-sweep verdicts tint the whole row, mirroring the gun.
    if (entry.flags.includes("tags-silent")) {
      li.classList.add("bcell--shelf-red");
    } else if (entry.flags.includes("tags-unheard")) {
      li.classList.add("bcell--shelf-yellow");
    }
    if (entry.flags.length) {
      const flags = document.createElement("div");
      flags.className = "bcell__meta bcell__flags";
      const sh = entry.shelf;
      // bad-chars names its broken field(s) when the server could tell.
      const flagText = (f) => {
        if (f === "bad-chars" && entry.bad_chars) {
          const parts = [];
          if (entry.bad_chars.sku) parts.push("SKU");
          if (entry.bad_chars.barcode) parts.push("barcode");
          if (parts.length)
            return (
              `the ${parts.join(" and ")} ` +
              `${parts.length > 1 ? "have" : "has"} a broken special ` +
              `character - records can't match until it's fixed`
            );
        }
        return FLAG_TEXT[f] || f;
      };
      flags.textContent =
        "⚠ " +
        entry.flags.map(flagText).join(" · ") +
        (sh && sh.on_file
          ? ` - sweep heard ${sh.heard} of ${sh.on_file} on file, expected ${sh.expected}` +
            (sh.presumed_sold ? ` (${sh.presumed_sold} presumed sold)` : "") +
            (sh.over_heard
              ? ` · heard ${sh.over_heard} more tag(s) than boxes collected, check for a neighboring shelf or uncollected stock`
              : "") +
            (sh.over_unavailable
              ? ` · ${sh.over_unavailable} more on the shelf than expected - matches its UNAVAILABLE stock in Shopify (reserved/damaged), so the extra is explained`
              : "")
          : "");
      li.querySelector(".bcell__info").append(flags);
    }
    li.classList.add("u-pointer");
    // Clicking a row opens the ACTUAL Edit-product view (Nick,
    // 2026-09-16) - the same product window every other tab uses -
    // instead of the check editor clone. Unresolved rows keep the
    // check editor: the barcode rescue lives there, and there is no
    // product to open.
    const canOpenProduct =
      entry.item.resolved && (entry.item.sku || entry.item.barcode);
    li.addEventListener("click", () =>
      canOpenProduct
        ? openProductHistory(entry.item.sku || entry.item.barcode)
        : openBitem(entry)
    );
    // Batch-only decisions (pick between listings sharing a barcode,
    // wrong-bin actions, the bundle call, per-row reprints) still
    // live in the check editor - its own button on flagged rows, so
    // nothing becomes unreachable.
    if (
      canOpenProduct &&
      (entry.flags.length || (entry.candidates || []).length > 1)
    ) {
      const row = document.createElement("div");
      row.className = "bcell__meta";
      const fix = document.createElement("button");
      fix.className = "reset";
      fix.type = "button";
      fix.textContent = "🛠 Batch fixes…";
      fix.title =
        "Check-step tools for this row: pick between listings sharing " +
        "the barcode, act on the wrong-bin warning, make the bundle " +
        "call, edit the label, or reprint";
      fix.addEventListener("click", (ev) => {
        ev.stopPropagation();
        openBitem(entry);
      });
      row.append(fix);
      li.querySelector(".bcell__info").append(row);
    }
    list.append(li);
  });
}

// --- Check-item editor (candidates arrows, counts, serial name) -------------
function openBitem(entry) {
  bitemEntry = entry;
  const cands = entry.candidates || [];
  bitemIdx = Math.max(
    0,
    cands.findIndex(
      (c) => c.shopify_variant_id === entry.item.shopify_variant_id
    )
  );
  document.getElementById("bitem-msg").textContent = "";
  document.getElementById("bitem-overlay").hidden = false;
  renderBitem();
}

function renderBitem() {
  const it = bitemEntry.item;
  const cands = bitemEntry.candidates || [];
  const multi = cands.length > 1;
  const showing = multi ? cands[bitemIdx] : it;
  document.getElementById("bitem-title").textContent =
    (showing.product_title || "(unknown)") +
    (showing.variant_title ? ` (${showing.variant_title})` : "");
  // Same grid the Edit-product window uses (Nick, 2026-09-15).
  document.getElementById("bitem-gsku").textContent = showing.sku || "—";
  document.getElementById("bitem-gbarcode").textContent =
    showing.barcode || it.scanned_code || "—";
  document.getElementById("bitem-gbin").textContent =
    showing.bin_location || "—";
  const img = document.getElementById("bitem-img");
  const imgUrl = showing.image_url || (showing === it ? it.image_url : null);
  if (imgUrl) {
    img.src = imgUrl;
    img.hidden = false;
  } else {
    img.hidden = true;
    img.removeAttribute("src");
  }
  document.getElementById("bitem-flags").textContent =
    "⚠ " + bitemEntry.flags.map((f) => FLAG_TEXT[f] || f).join(" · ");

  const prev = document.getElementById("bitem-prev");
  const next = document.getElementById("bitem-next");
  prev.style.visibility = multi ? "visible" : "hidden";
  next.style.visibility = multi ? "visible" : "hidden";
  prev.disabled = bitemIdx === 0;
  next.disabled = bitemIdx >= cands.length - 1;
  const pos = document.getElementById("bitem-candpos");
  pos.hidden = !multi;
  if (multi) {
    const current =
      cands[bitemIdx].shopify_variant_id === it.shopify_variant_id;
    pos.textContent =
      `Listing ${bitemIdx + 1} of ${cands.length} sharing this barcode` +
      (current ? " - currently selected" : "");
    const useWrap = document.getElementById("bitem-usewrap");
    useWrap.hidden = false;
    // Never disabled: confirming the CURRENT listing is the usual move
    // ("yes, this one") and settles the several-listings flag server-side
    // — the same dead-primary-button fix the C72 got (Nick, 2026-08-25).
    const useBtn = document.getElementById("bitem-use");
    useBtn.disabled = false;
    useBtn.textContent = current ? "Keep this listing" : "Use this listing";
    // Splitting needs at least two boxes to divide and no tags yet — the
    // server refuses both anyway, but a button that can only fail is worse
    // than no button.
    document.getElementById("bitem-split").hidden =
      it.qty_scanned < 2 || it.paired_count > 0;
  } else {
    document.getElementById("bitem-usewrap").hidden = true;
  }
  document.getElementById("bitem-splitwrap").hidden = true;

  // SKU / barcode editor — any resolved product, right here in Check.
  // The warning line only appears when a broken char was flagged.
  const identWrap = document.getElementById("bitem-identwrap");
  identWrap.hidden = !it.resolved;
  if (it.resolved) {
    const identWarn = document.getElementById("bitem-identwarn");
    identWarn.hidden = !bitemEntry.flags.includes("bad-chars");
    // Name WHICH field broke and SHOW the character, bracketed - the
    // live Shopify value carries the real one (Nick, 2026-08-26: a
    // bare "shows as ?" left the operator guessing).
    const bc = bitemEntry.bad_chars;
    const warnSpan = identWarn.querySelector("span");
    if (bc && (bc.sku || bc.barcode) && warnSpan) {
      const lines = [];
      if (bc.sku)
        lines.push(
          `⚠ The SKU contains a character the database can't store: ` +
            `${bc.sku}. Recommend updating the SKU.`
        );
      if (bc.barcode)
        lines.push(
          `⚠ The barcode contains a character the database can't store: ` +
            `${bc.barcode}. Recommend updating the barcode.`
        );
      warnSpan.textContent =
        lines.join(" ") + " Fix it below; the change writes to Shopify.";
    } else if (warnSpan) {
      // Reset: the element is shared across opens.
      warnSpan.innerHTML =
        "⚠ The SKU or barcode contains a character the database can't " +
        "store (it shows as <b>?</b>) - records won't match until it's " +
        "replaced. Fix it below; the change writes to Shopify.";
    }
    const skuIn = document.getElementById("bitem-sku");
    const bcIn = document.getElementById("bitem-bc");
    skuIn.value = it.sku || "";
    bcIn.value = it.barcode || "";
    updateBitemIdentButtons();
  }

  const nameWrap = document.getElementById("bitem-namewrap");
  nameWrap.hidden = !bitemEntry.flags.includes("unconfirmed-name");
  if (!nameWrap.hidden) {
    document.getElementById("bitem-name").value = it.label_name || "";
  }

  // Wrong shelf: saved bin differs from the bin being walked.
  const binWarn = document.getElementById("bitem-binwarn");
  binWarn.hidden = !bitemEntry.flags.includes("wrong-bin");
  if (!binWarn.hidden) {
    document.getElementById("bitem-bintext").innerHTML =
      `Found here in <b>${escapeHtml(batch.bin_name)}</b>, but the system ` +
      `has it in <b>${escapeHtml(it.bin_location || "?")}</b>.`;
  }

  // Unresolved barcode rescue.
  const unres = document.getElementById("bitem-unresolved");
  unres.hidden = it.resolved;
  if (!unres.hidden) {
    document.getElementById("bitem-oddwrap").hidden = true;
  }

  // Bundle: flagged here so the call gets made before labels print.
  const bundleWrap = document.getElementById("bitem-bundlewrap");
  bundleWrap.hidden = it.kind !== "bundle";

  // Label format editor — every resolved product gets one.
  const labelWrap = document.getElementById("bitem-labelwrap");
  labelWrap.hidden = !it.resolved;
  if (it.resolved) {
    bitemLabelMode = it._labelPlacement || "header";
    document.getElementById("bitem-labeltext").value = it._labelText || "";
    updateBitemLabelMode();
  }

  document.getElementById("bitem-qty").textContent = it.qty_scanned;
  document.getElementById("bitem-expected").textContent =
    it.expected_qty != null
      ? `boxes scanned · Shopify on-hand ${it.expected_qty}`
      : "boxes scanned";
  // Reprinting one product's labels only makes sense once it resolved.
  document.getElementById("bitem-refreshwrap").hidden = !it.resolved;
  // A bundle has no box to put a label on, and the server refuses the
  // print — so don't offer a button that can only fail.
  document.getElementById("bitem-printwrap").hidden =
    !it.resolved || it.kind === "bundle";
  document.getElementById("bitem-printqty").value = 1;
}

// --- label format (Change Name / Change SKU / Change Both) ------------------
const BITEM_MODES = ["header", "sku", "both"];
const BITEM_MODE_TEXT = {
  header: "Change Name",
  sku: "Change SKU",
  both: "Change Both",
};

function updateBitemLabelMode() {
  document.getElementById("bitem-labelmode").textContent =
    BITEM_MODE_TEXT[bitemLabelMode];
  const it = bitemEntry ? bitemEntry.item : {};
  const typed = document.getElementById("bitem-labeltext").value.trim();
  const asHeader = typed && (bitemLabelMode === "header" || bitemLabelMode === "both");
  const asSku = typed && (bitemLabelMode === "sku" || bitemLabelMode === "both");
  const header = asHeader ? typed : "Telescopes Canada";
  const el = document.getElementById("bitem-prev-header");
  setPreviewHeader(el, header, !asHeader);
  renderSkuPreviewLine("bitem-prev-sku", asSku ? typed : it.sku || "");
  document.getElementById("bitem-prev-bc").textContent =
    it.barcode || it.sku || "";
  document.getElementById("bitem-prev-bin").textContent =
    "BIN: " + (batch ? batch.bin_name : "—");
}

document.getElementById("bitem-labelmode").addEventListener("click", () => {
  bitemLabelMode =
    BITEM_MODES[(BITEM_MODES.indexOf(bitemLabelMode) + 1) % BITEM_MODES.length];
  updateBitemLabelMode();
});

// --- SKU / barcode fixes in the Check step ----------------------------------
// Any resolved product's SKU or barcode can be changed on the spot (the
// mangled-character flag is the loud case, but it works for all). Save
// buttons grey out at the saved value; ✕ returns the box to what's saved.
function updateBitemIdentButtons() {
  const it = bitemEntry ? bitemEntry.item : {};
  const sku = document.getElementById("bitem-sku").value.trim();
  const bc = document.getElementById("bitem-bc").value.trim();
  document.getElementById("bitem-skusave").disabled =
    !sku || sku === (it.sku || "");
  document.getElementById("bitem-bcsave").disabled =
    !bc || bc === (it.barcode || "");
}

// The overwrite endpoints look the product up in LIVE Shopify, so the
// target must be a value that still matches there. A mangled SKU
// ("ZWO EFW-Nikon-?") matches nothing — prefer a clean barcode or
// scanned code and only fall back to the SKU.
function bitemIdentTarget() {
  const it = bitemEntry.item;
  const clean = (v) => v && !/[?-￿]/.test(v);
  const vals = [it.barcode, it.scanned_code, it.sku];
  return vals.find(clean) || vals.find((v) => v) || "";
}

document
  .getElementById("bitem-sku")
  .addEventListener("input", updateBitemIdentButtons);
document
  .getElementById("bitem-bc")
  .addEventListener("input", updateBitemIdentButtons);
document.getElementById("bitem-skureset").addEventListener("click", () => {
  if (!bitemEntry) return;
  document.getElementById("bitem-sku").value = bitemEntry.item.sku || "";
  updateBitemIdentButtons();
});
document.getElementById("bitem-bcreset").addEventListener("click", () => {
  if (!bitemEntry) return;
  document.getElementById("bitem-bc").value = bitemEntry.item.barcode || "";
  updateBitemIdentButtons();
});

document.getElementById("bitem-skusave").addEventListener("click", async () => {
  if (!bitemEntry || !batch) return;
  const operator = operatorEl.value;
  if (!operator) {
    alert("Pick who's scanning (top right) first.");
    return;
  }
  const it = bitemEntry.item;
  const newSku = document.getElementById("bitem-sku").value.trim();
  const msg = document.getElementById("bitem-msg");
  const btn = document.getElementById("bitem-skusave");
  btn.disabled = true;
  msg.textContent = "Writing the SKU to Shopify…";
  try {
    const ow = await postJson("/api/sku-overwrites", {
      target: bitemIdentTarget(),
      new_sku: newSku,
      changed_by: operator,
      confirmed: true,
    });
    // Pull the change into this batch row too, so the labels print the
    // NEW SKU. Shopify's search can trail the write by a few seconds —
    // if the re-read misses, the row catches up on the next ↻.
    let note = "";
    try {
      const r = await postJson(
        `/api/batches/${batch.id}/items/${it.id}/resolve`,
        {}
      );
      if (r.item) bitemEntry.item = r.item;
      if (!r.resolved)
        note = " (batch row catches up in a few seconds - hit ↻ if needed)";
    } catch (e) {
      note = ` (batch row refresh failed: ${e.message})`;
    }
    bitemEntry.flags = bitemEntry.flags.filter((f) => f !== "bad-chars");
    renderBitem();
    renderCheckList();
    msg.textContent =
      `SKU saved ✓ - now ${newSku}${note}.` +
      (ow.legacy_linked
        ? " The old broken value stays linked, so old labels still scan."
        : "");
  } catch (err) {
    msg.textContent = err.message;
    updateBitemIdentButtons();
  }
});

document.getElementById("bitem-bcsave").addEventListener("click", async () => {
  if (!bitemEntry || !batch) return;
  const operator = operatorEl.value;
  if (!operator) {
    alert("Pick who's scanning (top right) first.");
    return;
  }
  const it = bitemEntry.item;
  const newBc = document.getElementById("bitem-bc").value.trim();
  const msg = document.getElementById("bitem-msg");
  const btn = document.getElementById("bitem-bcsave");
  btn.disabled = true;
  msg.textContent = "Writing the barcode to Shopify…";
  try {
    const ow = await postJson("/api/barcode-overwrites", {
      target: bitemIdentTarget(),
      new_barcode: newBc,
      changed_by: operator,
      confirmed: true,
      variant_gid: it.shopify_variant_id || null,
    });
    // No re-lookup here: the OLD barcode is what this row scanned as, so
    // a live search by it would now miss. The row's display just follows.
    it.barcode = newBc;
    bitemEntry.flags = bitemEntry.flags.filter((f) => f !== "bad-chars");
    renderBitem();
    renderCheckList();
    msg.textContent =
      `Barcode saved ✓ - now ${newBc}.` +
      (ow.legacy_linked
        ? " The old broken value stays linked, so old labels still scan."
        : "");
  } catch (err) {
    msg.textContent = err.message;
    updateBitemIdentButtons();
  }
});
document
  .getElementById("bitem-labeltext")
  .addEventListener("input", updateBitemLabelMode);
document.getElementById("bitem-labelclear").addEventListener("click", () => {
  document.getElementById("bitem-labeltext").value = "";
  updateBitemLabelMode();
  document.getElementById("bitem-labelsave").click();
});

document.getElementById("bitem-labelsave").addEventListener("click", async () => {
  const it = bitemEntry.item;
  const msg = document.getElementById("bitem-msg");
  if (!it.sku) {
    msg.textContent = "This product has no SKU to attach a label name to.";
    return;
  }
  const name = document.getElementById("bitem-labeltext").value.trim();
  try {
    await apiJson(`/api/label-names/${encodeURIComponent(it.sku)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        label_name: name,
        placement: bitemLabelMode,
        updated_by: operatorEl.value || null,
      }),
    });
    it._labelText = name;
    it._labelPlacement = bitemLabelMode;
    msg.textContent = name
      ? `Saved ✓ - labels print this as the ${
          bitemLabelMode === "both"
            ? "name and SKU"
            : bitemLabelMode === "sku"
              ? "SKU line"
              : "name"
        }.`
      : "Cleared ✓ - standard label.";
  } catch (err) {
    msg.textContent = err.message;
  }
});

// --- wrong shelf: drop / move / ignore --------------------------------------
document.getElementById("bitem-binwarn").addEventListener("click", async (ev) => {
  const act = ev.target.dataset ? ev.target.dataset.act : null;
  if (!act || !bitemEntry) return;
  const it = bitemEntry.item;
  const msg = document.getElementById("bitem-msg");
  if (act === "ignore") {
    ignoredBinItems.add(it.id);
    document.getElementById("bitem-overlay").hidden = true;
    setBatchResult(
      "Ignored for this batch - it'll come up again next time.",
      "ok"
    );
    loadBatchReview();
    return;
  }
  if (act === "drop") {
    if (
      !confirm(
        `Drop ${it.product_title || it.sku} from this batch? ` +
          `Its ${it.qty_scanned} box(es) stop counting here and no labels ` +
          `print for it - take them to bin ${it.bin_location}.`
      )
    )
      return;
    try {
      await apiFetch(`/api/batches/${batch.id}/items/${it.id}`, {
        method: "DELETE",
      });
      document.getElementById("bitem-overlay").hidden = true;
      await pullBatch(false);
      loadBatchReview();
      setBatchResult("Dropped from this batch.", "ok");
    } catch (err) {
      msg.textContent = err.message;
    }
    return;
  }
  if (act === "move") {
    if (
      !confirm(
        `Update the bin on "${it.product_title}" in Shopify: ` +
          `${it.bin_location || "(none)"} → ${batch.bin_name}?`
      )
    )
      return;
    try {
      await postJson("/api/bin-updates", {
        target: it.sku || it.barcode,
        bin: batch.bin_name,
        changed_by: operatorEl.value || null,
      });
      it.bin_location = batch.bin_name;
      document.getElementById("bitem-overlay").hidden = true;
      await pullBatch(false);
      loadBatchReview();
      setBatchResult(`Bin updated to ${batch.bin_name} in Shopify.`, "ok");
    } catch (err) {
      msg.textContent = err.message;
    }
  }
});

// --- re-check against Shopify ----------------------------------------------
// The answer to "the product had no barcode, so I set one in Shopify — now
// what": ask the server to look the row up again instead of making the
// operator re-scan the boxes. Read-only; nothing is written to the store.
function recheckItem(item) {
  return apiJson(`/api/batches/${batch.id}/items/${item.id}/resolve`, {
    method: "POST",
  });
}

async function bitemRecheck() {
  const it = bitemEntry.item;
  const msg = document.getElementById("bitem-msg");
  msg.textContent = "Asking Shopify again…";
  try {
    const data = await recheckItem(it);
    if (!data.resolved) {
      msg.textContent = data.message;
      return;
    }
    // A plain refresh of an already-resolved product: stay put and show the
    // updated details. Anything structural (it just resolved, or it merged
    // into another row) changes the list, so close and let it reload.
    if (data.was_resolved && !data.merged) {
      bitemEntry.item = data.item;
      renderBitem();
      msg.textContent = data.message;
      await pullBatch(false);
      loadBatchReview();
      return;
    }
    document.getElementById("bitem-overlay").hidden = true;
    await pullBatch(false);
    loadBatchReview();
    setBatchResult(data.message, "ok");
  } catch (err) {
    msg.textContent = err.message;
  }
}

// Bundle decisions from the Check step. Both change the list (a bundle stops
// being labelled; a drop removes the row), so close the editor and reload.
async function bitemSetKind(kind, excluded) {
  await setItemKind(bitemEntry.item, kind, excluded);
  document.getElementById("bitem-overlay").hidden = true;
  loadBatchReview();
}

refreshify("bcheck-refresh", "batch-checks", () => loadBatchReview());

document
  .getElementById("bitem-kind-multi")
  .addEventListener("click", () => bitemSetKind("multi_box", false));

document.getElementById("bitem-kind-drop").addEventListener("click", () => {
  const it = bitemEntry.item;
  if (
    !confirm(
      `Drop "${itemDisplayName(it)}" from the RFID system?\n\n` +
        `It won't be added to future batches and will never be labelled. ` +
        `Its component products are unaffected - they keep their own tags.`
    )
  )
    return;
  bitemSetKind("bundle", true);
});

document
  .getElementById("bitem-recheck")
  .addEventListener("click", bitemRecheck);
refreshify("bitem-refresh", "product-recheck", () => bitemRecheck());

// Same thing for every unknown barcode at once — one at a time so a bin
// full of them doesn't fire twenty Shopify lookups in parallel.
document.getElementById("bcheck-recheck").addEventListener("click", async () => {
  const btn = document.getElementById("bcheck-recheck");
  const rows = checkEntries.filter((e) => !e.item.resolved).map((e) => e.item);
  if (!rows.length) return;
  const label = btn.textContent;
  btn.disabled = true;
  let fixed = 0;
  const stuck = [];
  try {
    for (let i = 0; i < rows.length; i++) {
      btn.textContent = `Re-checking ${i + 1} of ${rows.length}…`;
      try {
        const data = await recheckItem(rows[i]);
        if (data.resolved) fixed += 1;
        else stuck.push(rows[i].scanned_code);
      } catch (err) {
        stuck.push(rows[i].scanned_code);
      }
    }
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
  await pullBatch(false);
  loadBatchReview();
  if (fixed && !stuck.length) {
    setBatchResult(`Re-checked ✓ - ${fixed} now resolved.`, "ok");
  } else if (fixed) {
    setBatchResult(
      `${fixed} now resolved ✓ - still unknown: ${stuck.join(", ")}.`,
      "ok"
    );
  } else {
    setBatchResult(
      `Still nothing in Shopify for ${stuck.join(", ")}. If you just ` +
        `changed a barcode there, give it a few seconds and try again.`,
      "err"
    );
  }
});

// --- unresolved barcode rescue ---------------------------------------------
function renderOdd() {
  const wrap = document.getElementById("bitem-oddwrap");
  if (!oddList.length) {
    wrap.hidden = true;
    document.getElementById("bitem-msg").textContent =
      "No products in this bin have an odd barcode.";
    return;
  }
  wrap.hidden = false;
  const p = oddList[oddIdx];
  document.getElementById("bitem-oddtitle").textContent =
    (p.product_title || "(unknown)") +
    (p.variant_title ? ` (${p.variant_title})` : "");
  document.getElementById("bitem-oddmeta").textContent =
    `SKU: ${p.sku || "—"} · current barcode: ${p.barcode || "(none)"} · ${p.reason}`;
  const img = document.getElementById("bitem-oddimg");
  if (p.image_url) {
    img.src = p.image_url;
    img.hidden = false;
  } else {
    img.hidden = true;
    img.removeAttribute("src");
  }
  document.getElementById("bitem-oddpos").textContent =
    `Candidate ${oddIdx + 1} of ${oddList.length}`;
  const prev = document.getElementById("bitem-oddprev");
  const next = document.getElementById("bitem-oddnext");
  prev.style.visibility = oddList.length > 1 ? "visible" : "hidden";
  next.style.visibility = oddList.length > 1 ? "visible" : "hidden";
  prev.disabled = oddIdx === 0;
  next.disabled = oddIdx >= oddList.length - 1;
}

async function loadOdd(recommendedOnly) {
  const it = bitemEntry.item;
  const code = it.scanned_code;
  const msg = document.getElementById("bitem-msg");
  msg.textContent = "Looking through this bin…";
  try {
    const data = await apiJson(
      `/api/bins/${encodeURIComponent(batch.bin_name)}/odd-barcodes` +
        `?scanned=${encodeURIComponent(code)}`
    );
    if (recommendedOnly) {
      oddList = data.recommended ? [data.recommended] : [];
    } else {
      oddList = data.candidates;
    }
    oddIdx = 0;
    msg.textContent = "";
    renderOdd();
  } catch (err) {
    msg.textContent = err.message;
  }
}

document
  .getElementById("bitem-odd")
  .addEventListener("click", () => loadOdd(false));
document
  .getElementById("bitem-recommend")
  .addEventListener("click", () => loadOdd(true));
document.getElementById("bitem-oddprev").addEventListener("click", () => {
  if (oddIdx > 0) {
    oddIdx--;
    renderOdd();
  }
});
document.getElementById("bitem-oddnext").addEventListener("click", () => {
  if (oddIdx < oddList.length - 1) {
    oddIdx++;
    renderOdd();
  }
});

// Link an unresolved scan to a product WITHOUT touching Shopify - the
// C72 3.59 flow, web edition (Nick, 2026-08-25): the code becomes a
// lookup alias and the row resolves IN PLACE, counts intact. For old
// labels printed with a broken or foreign code whose product's real
// barcode is already correct.
async function bitemLinkScan(targetTerm, title) {
  const it = bitemEntry.item;
  const msg = document.getElementById("bitem-msg");
  try {
    await postJson("/api/barcode-aliases", {
      alias_barcode: it.scanned_code,
      target: targetTerm,
      created_by: operatorEl.value || null,
    });
    try {
      await postJson(`/api/batches/${batch.id}/items/${it.id}/resolve`, {});
    } catch {
      /* the row catches up on the next re-check */
    }
    document.getElementById("bitem-overlay").hidden = true;
    await pullBatch(false);
    loadBatchReview();
    setBatchResult(
      `Linked ✓ - ${it.scanned_code} now finds ${title}; Shopify ` +
        `untouched (unlink in History).`,
      "ok"
    );
  } catch (err) {
    msg.textContent = err.message;
  }
}

document.getElementById("bitem-oddlink").addEventListener("click", () => {
  const p = oddList[oddIdx];
  const it = bitemEntry && bitemEntry.item;
  if (!p || !it) return;
  if (
    !confirm(
      `Link ${it.scanned_code} to "${p.product_title}"?\n\n` +
        `The scanned code will find this product from now on. Shopify's ` +
        `own SKU and barcode stay unchanged - use "Give this product the ` +
        `scanned barcode" instead if Shopify itself is wrong. The counted ` +
        `boxes stay on this row. Unlink any time in History.`
    )
  )
    return;
  bitemLinkScan(p.sku || p.barcode, p.product_title);
});

document.getElementById("bitem-linkgo").addEventListener("click", async () => {
  const term = document.getElementById("bitem-linktarget").value.trim();
  const msg = document.getElementById("bitem-msg");
  if (!term || !bitemEntry) return;
  msg.textContent = `Looking up ${term}…`;
  let p;
  try {
    p = await apiJson(`/api/products/by-barcode/${encodeURIComponent(term)}`);
  } catch (err) {
    msg.textContent = `No product found for ${term} (${err.message}).`;
    return;
  }
  msg.textContent = "";
  const title = p.product_title || p.sku || term;
  if (
    !confirm(
      `Link ${bitemEntry.item.scanned_code} to "${title}"` +
        (p.sku ? ` (SKU ${p.sku})` : "") +
        `?\n\nThe scanned code will find this product from now on; ` +
        `Shopify is not touched. Unlink any time in History.`
    )
  )
    return;
  bitemLinkScan(p.sku || term, title);
});

// Give the chosen product the barcode that wouldn't resolve. This is a real
// Shopify write — the same audited overwrite the Scan Station uses.
document.getElementById("bitem-oddapply").addEventListener("click", async () => {
  const p = oddList[oddIdx];
  const it = bitemEntry.item;
  const msg = document.getElementById("bitem-msg");
  if (!p) return;
  if (
    !confirm(
      `Are you absolutely sure?\n\n` +
        `"${p.product_title}"\n` +
        `barcode ${p.barcode || "(none)"} → ${it.scanned_code}\n\n` +
        `This changes the barcode in Shopify for real. Only do this if ` +
        `the box in your hand IS this product.`
    )
  )
    return;
  try {
    await postJson("/api/barcode-overwrites", {
      target: p.sku || p.barcode,
      new_barcode: it.scanned_code,
      changed_by: operatorEl.value || null,
      // The operator just answered "are you absolutely sure?" above; the
      // endpoint refuses to touch Shopify without this.
      confirmed: true,
      variant_gid: p.shopify_variant_id || null,
    });
    // The unresolved row's count has to be re-scanned against the real
    // product, so take it out of the batch.
    await apiFetch(`/api/batches/${batch.id}/items/${it.id}`, {
      method: "DELETE",
    });
    document.getElementById("bitem-overlay").hidden = true;
    await pullBatch(false);
    loadBatchReview();
    setBatchResult(
      `Barcode updated in Shopify ✓ - now RE-SCAN those ` +
        `${it.qty_scanned} box(es); they'll come up as ${p.product_title}.`,
      "ok"
    );
  } catch (err) {
    msg.textContent = err.message;
  }
});

document.getElementById("bitem-drop").addEventListener("click", async () => {
  const it = bitemEntry.item;
  if (
    !confirm(
      `Remove this unresolved scan (${it.scanned_code}, ${it.qty_scanned} ` +
        `box(es)) from the list? Nothing permanent changes - scanning it ` +
        `again brings it back.`
    )
  )
    return;
  try {
    await apiFetch(`/api/batches/${batch.id}/items/${it.id}`, {
      method: "DELETE",
    });
    document.getElementById("bitem-overlay").hidden = true;
    await pullBatch(false);
    loadBatchReview();
    setBatchResult("Removed from the list.", "ok");
  } catch (err) {
    document.getElementById("bitem-msg").textContent = err.message;
  }
});

document.getElementById("bitem-prev").addEventListener("click", () => {
  if (bitemIdx > 0) {
    bitemIdx--;
    renderBitem();
  }
});
document.getElementById("bitem-next").addEventListener("click", () => {
  if (bitemIdx < (bitemEntry.candidates || []).length - 1) {
    bitemIdx++;
    renderBitem();
  }
});

// --- split one scanned pile between listings sharing a barcode -------------
// Two 94216 boxes, one regular and one open-box, same barcode: reassign
// moves ALL of them, so there was no honest way to say "one of each". The
// form gives every candidate a count; Split stays locked until the counts
// add up to exactly what was scanned, so a box can't vanish or duplicate.
function openSplitForm() {
  const it = bitemEntry.item;
  const cands = bitemEntry.candidates || [];
  const wrap = document.getElementById("bitem-splitwrap");
  const rows = document.getElementById("bitem-split-rows");
  document.getElementById("bitem-split-title").textContent =
    `Divide the ${it.qty_scanned} scanned box(es) between these listings:`;
  rows.innerHTML = "";
  cands.forEach((c, i) => {
    const row = document.createElement("div");
    row.className = "linkbox__form u-mb6";
    // The row it's currently sitting on starts with the full count; the
    // operator moves boxes off it.
    const startQty =
      c.shopify_variant_id === it.shopify_variant_id ? it.qty_scanned : 0;
    row.innerHTML = `
      <input type="number" class="linkbox__input bitem-split-qty u-maxw70" min="0"
             max="${it.qty_scanned}" value="${startQty}"
             data-variant="${escapeHtml(c.shopify_variant_id)}" />
      <span class="linkbox__text">${escapeHtml(
        c.product_title || c.sku || "?"
      )}${c.sku ? ` · ${escapeHtml(c.sku)}` : ""}</span>`;
    rows.append(row);
  });
  const refresh = () => {
    const total = [...rows.querySelectorAll(".bitem-split-qty")].reduce(
      (n, inp) => n + (Number(inp.value) || 0),
      0
    );
    const ok = total === it.qty_scanned;
    document.getElementById("bitem-split-count").textContent = ok
      ? `${total} of ${it.qty_scanned} assigned ✓`
      : `${total} of ${it.qty_scanned} assigned - every box needs a home`;
    document.getElementById("bitem-split-go").disabled = !ok;
  };
  rows.querySelectorAll(".bitem-split-qty").forEach((inp) =>
    inp.addEventListener("input", refresh)
  );
  refresh();
  wrap.hidden = false;
}

document
  .getElementById("bitem-split")
  .addEventListener("click", openSplitForm);
document
  .getElementById("bitem-split-cancel")
  .addEventListener("click", () => {
    document.getElementById("bitem-splitwrap").hidden = true;
  });

document
  .getElementById("bitem-split-go")
  .addEventListener("click", async () => {
    const msg = document.getElementById("bitem-msg");
    const parts = [
      ...document.querySelectorAll("#bitem-split-rows .bitem-split-qty"),
    ].map((inp) => ({
      shopify_variant_id: inp.dataset.variant,
      qty: Number(inp.value) || 0,
    }));
    try {
      const data = await postJson(
        `/api/batches/${batch.id}/items/${bitemEntry.item.id}/split`,
        { parts }
      );
      batchSound("ok");
      document.getElementById("bitem-overlay").hidden = true;
      setBatchResult(data.message, "ok");
      await pullBatch(false);
      loadBatchReview();
    } catch (err) {
      msg.textContent = err.message;
    }
  });

document.getElementById("bitem-use").addEventListener("click", async () => {
  const cand = bitemEntry.candidates[bitemIdx];
  const msg = document.getElementById("bitem-msg");
  try {
    const data = await apiJson(
      `/api/batches/${batch.id}/items/${bitemEntry.item.id}/reassign`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ shopify_variant_id: cand.shopify_variant_id }),
      }
    );
    batchSound("ok");
    document.getElementById("bitem-overlay").hidden = true;
    setBatchResult(
      (data.merged ? "Merged into the existing row for " : "Reassigned to ") +
        (data.item.product_title || data.item.sku) +
        ".",
      "ok"
    );
    await pullBatch(false);
    loadBatchReview();
  } catch (err) {
    msg.textContent = err.message;
  }
});

document.getElementById("bitem-name-save").addEventListener("click", async () => {
  const it = bitemEntry.item;
  const name = document.getElementById("bitem-name").value.trim();
  const msg = document.getElementById("bitem-msg");
  if (!name || !it.serial_prefix) return;
  try {
    await apiJson(
      `/api/serial-prefixes/${encodeURIComponent(it.serial_prefix)}/label`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label_name: name }),
      }
    );
    it.label_name = name;
    msg.textContent = "Name confirmed ✓";
  } catch (err) {
    msg.textContent = err.message;
  }
});

async function bitemAdjust(delta) {
  const it = bitemEntry.item;
  const qty = Math.max(0, it.qty_scanned + delta);
  try {
    const updated = await postJson(
      `/api/batches/${batch.id}/items/${it.id}/qty`,
      { qty }
    );
    Object.assign(it, updated);
    const inList = batchItems.find((i) => i.id === it.id);
    if (inList) Object.assign(inList, updated);
    renderBitem();
  } catch (err) {
    document.getElementById("bitem-msg").textContent = err.message;
  }
}
document.getElementById("bitem-minus").addEventListener("click", () => bitemAdjust(-1));
document.getElementById("bitem-plus").addEventListener("click", () => bitemAdjust(1));

document.getElementById("bcheck-all").addEventListener("click", () =>
  loadBatchReview(true)
);

// Labels already printed? Jump to pairing without queueing a second run.
document.getElementById("batch-skip-print").addEventListener("click", async () => {
  if (!batch) return;
  if (
    !confirm(
      `Skip printing for bin ${batch.bin_name} and go straight to pairing?` +
        `\n\nUse this when the labels are already printed and applied.`
    )
  )
    return;
  try {
    const b = await postJson(`/api/batches/${batch.id}/skip-print`, {});
    batch.status = b.status;
    showBatchStage("pair");
    setBatchResult("Straight to pairing - no labels queued.", "ok");
  } catch (err) {
    setBatchResult(err.message, "err");
  }
});

// Print labels for just this product — a damaged sticker shouldn't mean
// reprinting the whole bin.
document.getElementById("bitem-print").addEventListener("click", async () => {
  const it = bitemEntry.item;
  const msg = document.getElementById("bitem-msg");
  const btn = document.getElementById("bitem-print");
  const qty = Math.max(
    1,
    Math.min(50, Number(document.getElementById("bitem-printqty").value) || 1)
  );
  if (
    !confirm(
      `Print ${qty} label(s) for ${it.product_title || it.sku}?\n\n` +
        `They join the print queue with the rest - the other products in ` +
        `this bin aren't reprinted.`
    )
  )
    return;
  btn.disabled = true;
  msg.textContent = "Queueing…";
  try {
    const res = await postJson(
      `/api/batches/${batch.id}/items/${it.id}/labels`,
      { quantity: qty, requested_by: operatorEl.value || null }
    );
    batchSound("ok");
    msg.textContent = `${res.count} label(s) queued - collect them at the printer.`;
  } catch (err) {
    batchSound("err");
    msg.textContent = err.message;
  } finally {
    btn.disabled = false;
  }
});

// Closing an edit window redraws the list from what's already loaded; it no
// longer re-runs the whole check. Re-checking asks Shopify about every item,
// which is slow and threw the list around after each edit — the ↻ button
// does it when the operator actually wants it.
document.getElementById("bitem-close").addEventListener("click", () => {
  document.getElementById("bitem-overlay").hidden = true;
  renderCheckList();
});
document.getElementById("bitem-overlay").addEventListener("click", (e) => {
  if (e.target.id === "bitem-overlay") {
    document.getElementById("bitem-overlay").hidden = true;
    renderCheckList();
  }
});

bEl.queue.addEventListener("click", async () => {
  // Labels = loose boxes + sealed cases (one label per case, worth
  // N units) - counting qty_scanned alone hid the cases and a
  // case-only batch dead-ended on "nothing to print" (Nick,
  // 2026-09-16).
  const total = labelItems().reduce(
    (n, i) => n + (i.labels_total ?? i.qty_scanned),
    0
  );
  // Re-tagged bins often have NOTHING to print — every box already
  // wears a sticker. Ask instead of dead-ending on the server's 422;
  // verify still matters (the final sweep checks every tag).
  if (total === 0) {
    const tagged = batchItems.reduce((n, i) => n + (i.tagged_before || 0), 0);
    if (
      !confirm(
        `No untagged boxes were counted` +
          (tagged ? ` - all ${tagged} box(es) here already wear a tag` : "") +
          `, so there are no labels to queue and nothing to pair.\n\n` +
          `Sure there's nothing to print? OK skips straight ahead - ` +
          `run the verify sweep to finish the bin.`
      )
    )
      return;
    bEl.queue.disabled = true;
    try {
      await postJson(`/api/batches/${batch.id}/skip-print`, {});
      batch.status = "pairing";
      setBatchResult(
        "No labels - go to Verify and sweep the shelf to finish.",
        "ok"
      );
      showBatchStage("verify");
    } catch (err) {
      setBatchResult(err.message, "err");
    } finally {
      bEl.queue.disabled = false;
    }
    return;
  }
  if (!confirm(`Queue ${total} label(s) for bin ${batch.bin_name}?`)) return;
  bEl.queue.disabled = true;
  try {
    const data = await postJson(`/api/batches/${batch.id}/queue-labels`, {
      requested_by: operatorEl.value || null,
    });
    batch.status = "printing";
    setBatchResult(
      `${data.count} label(s) queued.` +
        (data.side_labels
          ? ` The strip's tail carries ${data.side_labels} more for ` +
            `${(data.side_trips || [])
              .map((t) => `${t.bin} (${t.labels})`)
              .join(", ")} - pair those on their side trips.`
          : ""),
      "ok"
    );
    showBatchStage("print");
  } catch (err) {
    setBatchResult(err.message, "err");
  } finally {
    bEl.queue.disabled = false;
  }
});

// --- Stage 3: print ---------------------------------------------------------
// The run list: every live label of this batch's print run, oldest
// first, with a checkbox to pick the ones that printed wrong or never
// came out (out of labels, debris on the stock). The poll NEVER stops
// itself any more - it used to stop at "all done" and go blind to
// requeued labels, which is why the step "didn't update" (Nick,
// 2026-08-25). Voided/canceled jobs leave the math and the list.
let bprintSelected = new Set();
let bprintLastSig = "";
let bprintLastClicked = null;

function renderBatchPrintRun(all) {
  const wrap = document.getElementById("bprint-run");
  const list = document.getElementById("bprint-run-list");
  const live = all
    .filter((j) => !["canceled", "voided"].includes(j.status))
    .sort((a, b) => a.id - b.id);
  wrap.hidden = !live.length;
  if (!live.length) return;
  // Drop selections that no longer exist (e.g. just reprinted).
  const ids = new Set(live.map((j) => j.id));
  bprintSelected = new Set([...bprintSelected].filter((i) => ids.has(i)));
  const sig =
    live.map((j) => `${j.id}:${j.status}`).join(",") +
    `|${[...bprintSelected].join(",")}`;
  if (sig === bprintLastSig) return; // no re-render mid-click for nothing
  bprintLastSig = sig;
  const chip = (s) =>
    s === "done"
      ? '<span class="chip-status chip-status--done">printed</span>'
      : s === "error"
        ? '<span class="chip-status chip-status--error">FAILED</span>'
        : `<span class="chip-status chip-status--pending">${s}</span>`;
  list.innerHTML = live
    .map(
      (j, i) => `
    <label class="bprint-run__row">
      <input type="checkbox" data-job="${j.id}" data-idx="${i}"
        ${bprintSelected.has(j.id) ? "checked" : ""} />
      <span class="bprint-run__n">${i + 1}</span>
      <span class="bprint-run__name">${escapeHtml(
        j.product_title || j.sku || "?"
      )}${j.case_units ? ` (case of ${j.case_units})` : ""}</span>
      <span class="mono recent__meta">${escapeHtml(j.sku || "")}</span>
      ${chip(j.status)}
    </label>`
    )
    .join("");
  const btn = document.getElementById("bprint-reprint-sel");
  btn.disabled = !bprintSelected.size;
  btn.textContent = bprintSelected.size
    ? `Reprint selected (${bprintSelected.size})`
    : "Reprint selected";
}

document
  .getElementById("bprint-run-list")
  .addEventListener("change", (ev) => {
    const cb = ev.target.closest("input[type=checkbox]");
    if (!cb) return;
    const id = Number(cb.dataset.job);
    const idx = Number(cb.dataset.idx);
    const boxes = [
      ...document.querySelectorAll("#bprint-run-list input[type=checkbox]"),
    ];
    // Shift-click selects the whole range since the last clicked row —
    // "everything after the printer ran dry" is one click + one
    // shift-click.
    if (
      bprintShift &&
      bprintLastClicked != null &&
      bprintLastClicked !== idx
    ) {
      const [a, b] = [
        Math.min(bprintLastClicked, idx),
        Math.max(bprintLastClicked, idx),
      ];
      boxes.slice(a, b + 1).forEach((box) => {
        box.checked = cb.checked;
        const jid = Number(box.dataset.job);
        cb.checked ? bprintSelected.add(jid) : bprintSelected.delete(jid);
      });
    } else {
      cb.checked ? bprintSelected.add(id) : bprintSelected.delete(id);
    }
    bprintLastClicked = idx;
    const btn = document.getElementById("bprint-reprint-sel");
    btn.disabled = !bprintSelected.size;
    btn.textContent = bprintSelected.size
      ? `Reprint selected (${bprintSelected.size})`
      : "Reprint selected";
    bprintLastSig = ""; // force the next poll to redraw with fresh state
  });
// Track shift through mousedown — the change event itself loses it on
// some browsers when the label is what got clicked.
let bprintShift = false;
document
  .getElementById("bprint-run-list")
  .addEventListener("mousedown", (ev) => (bprintShift = ev.shiftKey), true);

document.getElementById("bprint-sel-none").addEventListener("click", () => {
  bprintSelected.clear();
  bprintLastSig = "";
  pollBatchPrint();
});

document
  .getElementById("bprint-reprint-sel")
  .addEventListener("click", async () => {
    if (!batch || !bprintSelected.size) return;
    const n = bprintSelected.size;
    if (
      !confirm(
        `Reprint ${n} selected label(s)?\n\nThe old copies are voided ` +
          `and their tag records unlinked - BIN THEM first (a voided ` +
          `label on a box answers sweeps as an unknown tag). Fresh ` +
          `replacements queue right away; the rest of the run is ` +
          `untouched.`
      )
    )
      return;
    const btn = document.getElementById("bprint-reprint-sel");
    btn.disabled = true;
    try {
      const res = await postJson(`/api/batches/${batch.id}/reprint-jobs`, {
        job_ids: [...bprintSelected],
        requested_by: operatorEl.value || null,
        confirmed: true,
      });
      bprintSelected.clear();
      bprintLastSig = "";
      batchSound("ok");
      setBatchResult(res.message, "ok");
      pollBatchPrint();
    } catch (err) {
      batchSound("err");
      setBatchResult(err.message, "err");
      btn.disabled = false;
    }
  });

// Labels this batch still has to print (pending + printing). null =
// not yet known this visit. pullBatch's follow-along reads it so a
// "pair" signal can never pull the screen off a LIVE print run.
let bprintOutstanding = null;

async function pollBatchPrint() {
  if (!batch) return;
  try {
    const [agent, jobs] = await Promise.all([
      apiJson("/api/print-agent/status"),
      apiJson(`/api/print-jobs?batch_id=${batch.id}&limit=200`),
    ]);
    bEl.printAgent.textContent = agent.fault
      ? `⚠ Printer FAULTED - ${agent.fault}. Labels wait (not lost) ` +
        "until the printer is fixed."
      : agent.online
        ? "Printer agent: online ✓ (warehouse PC)" +
          (agent.readback === "counter"
            ? " · prints confirmed by printer"
            : "") +
          (agent.realign_capable
            ? ""
            : " · running OLD code: the rip re-align fixes are inactive " +
              "until print_agent.py is updated and its task restarted")
        : "Printer agent: OFFLINE - is the warehouse PC on? Jobs stay queued.";
    // Voided/canceled labels are HISTORY, not part of the run's math —
    // counting them used to render nonsense like "Printed 2/4" after a
    // reprint.
    const live = jobs.jobs.filter(
      (j) => !["canceled", "voided"].includes(j.status)
    );
    const counts = { done: 0, error: 0, pending: 0, printing: 0 };
    live.forEach((j) => {
      counts[j.status] = (counts[j.status] || 0) + 1;
    });
    bprintOutstanding = counts.pending + counts.printing;
    const total = live.length;
    bEl.printStatus.textContent =
      `Printed ${counts.done}/${total}` +
      (counts.error ? ` - ${counts.error} FAILED` : "") +
      (counts.pending + counts.printing
        ? ` - ${counts.pending + counts.printing} in the queue…`
        : " ✓ (tick any bad ones below to reprint them)");
    renderBatchPrintRun(jobs.jobs);
  } catch (err) {
    /* transient; next tick retries */
  }
}

bEl.toPair.addEventListener("click", () => showBatchStage("pair"));

// Clear queue & reprint all (Nick, 2026-08-25): the printer ran out of
// wax mid-run, printed 46 blanks, and believed every job succeeded -
// the per-label reprint would have meant 46 clicks. Voids the whole
// batch's labels (auto-created tag records die with them) and queues a
// fresh full set in the same walking order. Print step only, and only
// before any pairing.
document
  .getElementById("batch-reprint-all")
  .addEventListener("click", async () => {
    if (!batch) return;
    if (
      !confirm(
        `Void ALL of this batch's labels and reprint the full set?\n\n` +
          `Every label queued for bin ${batch.bin_name} is voided - ` +
          `including ones the printer thinks it printed - and their ` +
          `tag records are unlinked. A fresh full set queues in the ` +
          `same order.\n\nBIN THE OLD STRIP first: a voided label ` +
          `applied to a box would answer sweeps as an unknown tag.`
      )
    )
      return;
    const btn = document.getElementById("batch-reprint-all");
    btn.disabled = true;
    try {
      const res = await postJson(
        `/api/batches/${batch.id}/reprint-all`,
        { requested_by: operatorEl.value || null, confirmed: true }
      );
      batchSound("ok");
      setBatchResult(res.message, "ok");
    } catch (err) {
      batchSound("err");
      setBatchResult(err.message, "err");
    } finally {
      btn.disabled = false;
    }
  });

// --- Stage 4: pair ----------------------------------------------------------
function matchBatchItem(code) {
  const low = code.toLowerCase();
  const hits = batchItems.filter(
    (i) =>
      i.resolved &&
      ((i.barcode && i.barcode.toLowerCase() === low) ||
        (i.sku && i.sku.toLowerCase() === low) ||
        (i.scanned_code && i.scanned_code.toLowerCase() === low))
  );
  // Twins sharing a barcode (SS TH10 and its open-box listing, both seeded
  // from the same bin) both match — but only one has labels waiting for
  // tags. A row with nothing printed can't be the thing being paired, so
  // it must never win the tie.
  const labels = (i) => (i.labels_total != null ? i.labels_total : i.qty_scanned);
  return (
    hits.find((i) => labels(i) > i.paired_count) ||
    hits.find((i) => labels(i) > 0) ||
    hits[0] ||
    (/^\d{5,12}$/.test(code)
      ? batchItems.find(
          (i) => i.resolved && i.serial_prefix === code.slice(0, 4)
        )
      : null)
  );
}

// Pairing is measured against the COLLECT step's count (labels_total) —
// collection is the source of truth for how many boxes are in the bin.
// Reprinting fewer/more labels never moves this target: change the count
// at Collect if the collected number itself was wrong (Nick, 2026-08-25).
// Skipped rows and bundles carry no labels of their own.
function pairLabelGoal(i) {
  if (i.skipped || i.kind === "bundle") return 0;
  return i.labels_total != null ? i.labels_total : i.qty_scanned;
}

function renderPairCard() {
  const summary = document.getElementById("bpair-summary");
  const target = batchItems.reduce((n, i) => n + pairLabelGoal(i), 0);
  const paired = batchItems.reduce((n, i) => n + i.paired_count, 0);
  summary.textContent = `${paired} of ${target} label(s) paired${
    target - paired > 0 ? ` · ${target - paired} to go` : " ✓"
  }`;

  const item = batchItems.find((i) => i.id === pairActiveItemId);
  bEl.pairCard.hidden = !item;
  if (!item) return;
  const goal = pairLabelGoal(item);
  bEl.pairActive.textContent = itemDisplayName(item);
  document.getElementById("bpair-norfid").textContent =
    item.rfid_incompatible
      ? "⊘ RFID flag ON - remove"
      : "⊘ Won't RFID scan";
  bEl.pairProgress.textContent =
    `${item.paired_count} of ${goal} label(s) paired · ` +
    `${Math.max(0, goal - item.paired_count)} remaining` +
    (item.printed_count != null && item.printed_count !== goal
      ? ` (${item.printed_count} label(s) printed)`
      : "");
  bEl.pairUndo.disabled = !pairHistory.length;
}

function renderPairItems() {
  bEl.pairItems.innerHTML = "";
  const rows = batchItems.filter((i) => i.resolved && i.qty_scanned > 0);
  // "Won't RFID scan" products sink to the bottom, greyed (Nick,
  // 2026-09-09): their tags never answer on the box, so past the
  // check step they are not pairing work. Clicking one still selects
  // it for a deliberate by-hand pair.
  rows.sort(
    (a, b) =>
      (a.rfid_incompatible ? 1 : 0) - (b.rfid_incompatible ? 1 : 0)
  );
  rows.forEach((item) => {
    const li = itemCard(item, "pair");
    if (item.rfid_incompatible) {
      li.classList.add("bcell--noscan");
      const note = document.createElement("div");
      note.className = "bcell__meta bcell__skipped";
      note.textContent = "⚠ Won't RFID scan - skipped for pairing";
      li.querySelector(".bcell__info").append(note);
    }
    li.addEventListener("click", () => {
      pairActiveItemId = item.id;
      renderPairItems();
      renderPairCard();
      bEl.pairInput.focus();
    });
    bEl.pairItems.append(li);
  });
}

bEl.pairInput.addEventListener("keydown", async (event) => {
  if (event.key !== "Enter") return;
  const code = bEl.pairInput.value.trim();
  bEl.pairInput.value = "";
  if (!code || !batch) return;

  // A barcode from this batch switches the active product…
  const item = matchBatchItem(code);
  if (item) {
    pairActiveItemId = item.id;
    renderPairItems();
    renderPairCard();
    batchSound("ok");
    setBatchResult(`Active product: ${itemDisplayName(item)}`, "ok");
    return;
  }
  // Barcode/serial-shaped scans that match nothing are NOT tags — saving
  // them as EPCs would pollute the tag table.
  if (/^\d{5,14}$/.test(code)) {
    batchSound("err");
    setBatchResult(
      `"${code}" looks like a barcode or serial but doesn't match a ` +
        `product in this batch.`,
      "err"
    );
    return;
  }
  // …anything else is an RFID tag for the active product.
  if (!pairActiveItemId) {
    setBatchResult(
      "Scan a product barcode from this batch first - then its tags.",
      "err"
    );
    return;
  }
  await batchPairTag(code);
  bEl.pairInput.focus();
});

// Pair one tag read to the active product. Shared by the wedge input
// above and the C72 LINK relay; the caller checks pairActiveItemId.
async function batchPairTag(code) {
  try {
    const data = await postJson(`/api/batches/${batch.id}/pair`, {
      epc: code,
      item_id: pairActiveItemId,
      created_by: operatorEl.value || null,
    });
    if (data.companion) {
      setBatchResult(
        data.message || "Companion label confirmed ✓ - not counted.",
        "ok"
      );
      return { ok: true, text: data.message || "companion confirmed" };
    }
    const idx = batchItems.findIndex((i) => i.id === data.item.id);
    if (idx >= 0) {
      const flags = batchItems[idx]._binMismatch;
      batchItems[idx] = data.item;
      batchItems[idx]._binMismatch = flags;
    }
    pairHistory.push({ epc: data.assignment.rfid_id, item_id: data.item.id });
    renderPairItems();
    renderPairCard();
    setBatchResult(
      data.assignment.suspect
        ? `Saved, but ${code} doesn't look like a normal 24-char EPC - ` +
            `probably a bad read. Re-scan it to be safe.`
        : `Tag paired → ${itemDisplayName(data.item)} ` +
            `(${data.item.paired_count}/${pairLabelGoal(data.item)}).`,
      data.assignment.suspect ? "err" : "ok"
    );
  } catch (err) {
    setBatchResult(err.message, "err");
  }
}

// --- Reprint label(s) -------------------------------------------------------
// The labels printed wrong — usually a preferred name saved onto the wrong
// line ("Telescopes Canada" fixed but the SKU line clobbered). Correct the
// saved name store-wide, void this product's labels in the batch, release
// any tags tied to them, and print a fresh set. The count entered here only
// decides how many stickers come out — the pair target stays the Collect
// step's count (pairLabelGoal), so printing 3 of 5 reads 0/5, not 0/3.
const STORE_HEADER = "Telescopes Canada";
// Defaults for the two boxes, captured when the dialog opens. Cancelling
// discards edits: every open re-reads the SAVED state, so the boxes show
// what they did before the first open, never a half-typed leftover.
let reprintDefaults = { top: STORE_HEADER, sku: "" };

// Approximate ZPL font-0 advance width, as a fraction of the font height.
// Same geometry as the print agent: 2.125in x 203dpi = 431 dots across.
const LABEL_PW = 431;
function zplTextDots(text, size) {
  const NARROW = "iIl1jft.,:;'|!()[] -";
  const WIDE = "MWmw@";
  let w = 0;
  for (const ch of text) {
    w += (NARROW.includes(ch) ? 0.35 : WIDE.includes(ch) ? 0.78 : 0.55) * size;
  }
  return w;
}

// Printed width of a Code 128 barcode - the print agent's model
// (_code128_width_dots / _code128_symbols). 33 alphanumeric chars is
// the confirmed max (Nick's test prints, 2026-09-08): 34+ run off the
// sticker edge. Subset-aware since 2026-09-09: digit RUNS inside a
// mixed code ("12345678-O", the open-box barcodes) pack into subset-C
// pairs - the flat one-symbol-per-char model overstated their width
// and the sticker centered the bars too far left.
function code128Dots(data, module) {
  const n = data.length;
  let i = 0;
  let symbols = 0;
  let subset = null;
  while (i < n) {
    let run = 0;
    while (i + run < n && data[i + run] >= "0" && data[i + run] <= "9")
      run++;
    const useC =
      (subset === null && run >= 4) ||
      (subset === "C" && run >= 2) ||
      (subset === "B" && (run >= 6 || (run >= 4 && i + run === n)));
    if (useC) {
      if (subset === "B") symbols++;
      subset = "C";
      const pairs = Math.floor(run / 2);
      symbols += pairs;
      i += pairs * 2;
    } else {
      if (subset === "C") symbols++;
      subset = "B";
      symbols++;
      i++;
    }
  }
  return (11 * (symbols + 2) + 13) * module;
}

// The sticker's centre (SKU) line: one line at font 30 while it fits,
// else TWO wrapped lines at the SAME big font - the barcode moves down
// to make room (2026-09-08, Nick: the first cut's tiny wrap font was
// unreadable). Text too wide even for two font-30 lines steps down
// just far enough (30 -> 28 -> ... floor 20), exactly like the
// sticker. Every label preview renders through this so preview and
// sticker always agree.
// Field calibration (Nick's SKU TEST 3, 2026-09-08): the printer's
// real font 0 runs ~13% wider than the width model, and ^FB loses a
// little capacity at each break. IDENTICAL constants live in
// print_agent.py (_sku_fits) - sticker and preview must always agree.
const SKU_WIDTH_FUDGE = 1.13;
const SKU_WRAP_LINE_RESERVE = 20;
const SKU_WRAP_MARGIN = 10;
const SKU_BREAK_CHARS = "-/ _.";
function skuFits(text, size, lines) {
  const cap =
    lines === 1
      ? LABEL_PW
      : lines * (LABEL_PW - 2 * SKU_WRAP_MARGIN - SKU_WRAP_LINE_RESERVE);
  return zplTextDots(text, size) * SKU_WIDTH_FUDGE <= cap;
}
function skuLineFits(text, size) {
  return (
    zplTextDots(text, size) * SKU_WIDTH_FUDGE <=
    LABEL_PW - 2 * SKU_WRAP_MARGIN
  );
}
// The chosen two-line break: an operator "|" wins outright, else the
// dash/space/slash nearest the middle (break AFTER the separator).
// Null when the text has no separator - ZPL auto-wrap handles those.
function skuSplit(text) {
  if (text.includes("|")) {
    const i = text.indexOf("|");
    const left = text.slice(0, i).trim();
    const right = text
      .slice(i + 1)
      .replace(/\|/g, " ")
      .trim();
    if (left && right) return [left, right];
  }
  const cuts = [];
  for (let i = 0; i < text.length - 1; i++)
    if (SKU_BREAK_CHARS.includes(text[i])) cuts.push(i + 1);
  if (!cuts.length) return null;
  let best = cuts[0];
  let bd = Infinity;
  for (const i of cuts) {
    const d = Math.abs(
      zplTextDots(text.slice(0, i), 30) - zplTextDots(text.slice(i), 30)
    );
    if (d < bd) {
      bd = d;
      best = i;
    }
  }
  const left = text.slice(0, best).replace(/\s+$/, "");
  const right = text.slice(best).replace(/^\s+/, "");
  return left && right ? [left, right] : null;
}

// Shared fit-warning line under the editable label previews (reprint,
// queue edit, product panel): same issues list, same non-blocking note.
function renderFitWarn(warnEl, top, skuLine, barcode) {
  const issues = labelFitIssues(top, skuLine, barcode);
  warnEl.hidden = !issues.length;
  warnEl.textContent = issues.length
    ? "⚠ " + issues.join("\n⚠ ") +
      "\nYou can still print - this is a warning, not a block."
    : "";
}

// One place for the preview header's size tiers (lg <= 26 chars, md <= 56,
// sm beyond) - the printer steps through the same thresholds. forceLg:
// the store header always renders large.
function setPreviewHeader(el, text, forceLg) {
  el.textContent = text;
  el.className =
    "label-preview__header " +
    (forceLg || text.length <= 26
      ? "label-preview__header--lg"
      : text.length <= 56
        ? "label-preview__header--md"
        : "label-preview__header--sm");
}

function renderSkuPreviewLine(elId, text) {
  const line = document.getElementById(elId);
  const t = (text || "").slice(0, 56);
  const manual = t.includes("|");
  const plain = t.replace(/\|/g, " ").replace(/\s+/g, " ").trim();
  const wraps = !!plain && (manual || !skuFits(plain, 30, 1));
  line.classList.toggle("label-preview__sku--wrap", wraps);
  if (!wraps) {
    line.textContent = plain || "—";
    line.style.fontSize = "";
    return;
  }
  const split = skuSplit(t);
  let f = 30;
  if (split) {
    while (f > 20 && !(skuLineFits(split[0], f) && skuLineFits(split[1], f)))
      f -= 2;
    line.textContent = split[0] + "\n" + split[1];
  } else {
    while (f > 20 && !skuFits(plain, f, 2)) f -= 2;
    line.textContent = plain;
  }
  // Preview scale is 272px for the sticker's 431 dots (~0.63); the
  // normal 14px line IS font 30 at that scale.
  line.style.fontSize = Math.round(f * 0.46) + "px";
}

// Mirrors the print agent's layout rules: the top zone holds at most two
// lines (font steps down 28/20/16 with length) and ends where the SKU
// line starts; the SKU line wraps to two smaller lines when it outgrows
// font 30 (so it no longer overprints - only the 56-char cap trims it).
function labelFitIssues(top, sku, barcode) {
  const issues = [];
  if (top && top !== STORE_HEADER) {
    if (top.length > 76)
      issues.push("Top line: cut off after 76 characters.");
    const size = top.length <= 26 ? 28 : top.length <= 56 ? 20 : 16;
    const lines = Math.max(1, Math.ceil(zplTextDots(top, size) / LABEL_PW));
    if (lines > 2)
      issues.push(
        "Top line: needs more than the two lines available - the text " +
          "will overprint itself."
      );
    else if (lines === 2 && size === 28)
      issues.push(
        "Top line: wraps onto a second line that lands ON the SKU line."
      );
  }
  if (sku && sku.length > 56)
    issues.push("SKU line: cut off after 56 characters.");
  if (sku && sku.includes("|")) {
    const parts = skuSplit(sku);
    if (!parts)
      issues.push('SKU line: the "|" break needs text on both sides.');
    else if (!(skuLineFits(parts[0], 20) && skuLineFits(parts[1], 20)))
      issues.push(
        "SKU line: one side of the | break is too wide even at the " +
          "smallest wrap font."
      );
  }
  if (barcode && code128Dots(barcode, 1) > LABEL_PW - 24)
    issues.push(
      "Barcode: too long for scannable bars (33 characters is the " +
        "printable max) - the bars will run off the sticker's edge."
    );
  return issues;
}

// The item behind the open reprint dialog, for the preview's barcode/bin.
let breprintItem = null;

function updateReprintFitWarn() {
  const top =
    document.getElementById("breprint-top").value.trim() || STORE_HEADER;
  const skuLine = document.getElementById("breprint-sku").value.trim();
  // Live sticker preview, same tiers the printer steps through.
  const el = document.getElementById("breprint-prev-header");
  setPreviewHeader(el, top, top === STORE_HEADER);
  renderSkuPreviewLine("breprint-prev-sku", skuLine);
  if (breprintItem) {
    document.getElementById("breprint-prev-bc").textContent =
      breprintItem.barcode || breprintItem.sku || "";
    document.getElementById("breprint-prev-bin").textContent =
      "BIN: " + (batch ? batch.bin_name : "—");
  }
  renderFitWarn(
    document.getElementById("breprint-fitwarn"),
    top,
    skuLine,
    breprintItem ? breprintItem.barcode || breprintItem.sku || "" : ""
  );
}

document.getElementById("bpair-reprint").addEventListener("click", async () => {
  const item = batchItems.find((i) => i.id === pairActiveItemId);
  if (!item || !batch) return;
  document.getElementById("breprint-title").textContent =
    itemDisplayName(item);
  // Default to the Collect count — how many labels the bin actually
  // needs. Printing a different number never moves the pair target.
  document.getElementById("breprint-count").value =
    item.labels_total ?? item.qty_scanned;
  document.getElementById("breprint-warn").textContent = item.paired_count
    ? `⚠ ${item.paired_count} tag(s) are already paired to the old labels. ` +
      `PEEL THOSE STICKERS OFF the boxes before printing - a leftover ` +
      `sticker answers sweeps alongside the new one. You'll be asked to ` +
      `confirm they're off.`
    : `The old printed labels become invalid - bin them so they never ` +
      `end up on a box.`;
  document.getElementById("breprint-msg").textContent = "";
  breprintItem = item;
  reprintDefaults = { top: STORE_HEADER, sku: item.sku || "" };
  // Prefill from what's SAVED, never from a previous unconfirmed edit.
  let top = STORE_HEADER;
  let skuLine = item.sku || "";
  try {
    const cur = await apiJson(
      `/api/label-names/${encodeURIComponent(item.sku || "")}`
    );
    if (cur.label_name && cur.placement !== "sku") top = cur.label_name;
    if (cur.sku_text) skuLine = cur.sku_text;
    else if (cur.label_name && (cur.placement === "sku" || cur.placement === "both"))
      skuLine = cur.label_name;
  } catch {
    /* no saved name — defaults stand */
  }
  document.getElementById("breprint-top").value = top;
  document.getElementById("breprint-sku").value = skuLine;
  updateReprintFitWarn();
  document.getElementById("breprint-overlay").hidden = false;
});

document.getElementById("breprint-top").addEventListener("input", updateReprintFitWarn);
document.getElementById("breprint-sku").addEventListener("input", updateReprintFitWarn);
document.getElementById("breprint-top-reset").addEventListener("click", () => {
  document.getElementById("breprint-top").value = reprintDefaults.top;
  updateReprintFitWarn();
});
document.getElementById("breprint-sku-reset").addEventListener("click", () => {
  document.getElementById("breprint-sku").value = reprintDefaults.sku;
  updateReprintFitWarn();
});

document
  .getElementById("breprint-cancel")
  .addEventListener("click", () => {
    document.getElementById("breprint-overlay").hidden = true;
  });

document.getElementById("breprint-go").addEventListener("click", async () => {
  const item = batchItems.find((i) => i.id === pairActiveItemId);
  if (!item || !batch) return;
  const count = parseInt(
    document.getElementById("breprint-count").value,
    10
  );
  if (!Number.isFinite(count) || count < 1) {
    document.getElementById("breprint-msg").textContent =
      "How many labels should print?";
    return;
  }
  if (
    item.paired_count &&
    !confirm(
      `${item.paired_count} tag(s) are paired to the old labels.\n\n` +
        `Have you peeled the old RFID stickers OFF the boxes?\n\n` +
        `OK = they're off, release the ties and reprint.`
    )
  )
    return;
  const btn = document.getElementById("breprint-go");
  btn.disabled = true;
  try {
    const res = await postJson(
      `/api/batches/${batch.id}/items/${item.id}/reprint-labels`,
      {
        count,
        top_text: document.getElementById("breprint-top").value.trim(),
        sku_line: document.getElementById("breprint-sku").value.trim(),
        created_by: operatorEl.value || null,
        old_stickers_removed: true,
      }
    );
    document.getElementById("breprint-overlay").hidden = true;
    pairHistory = [];
    await pullBatch(false);
    renderPairItems();
    renderPairCard();
    setBatchResult(res.message, "ok");
  } catch (err) {
    document.getElementById("breprint-msg").textContent = err.message;
  } finally {
    btn.disabled = false;
  }
});

// Won't-RFID-scan toggle from the pair card: per-PRODUCT and store-wide,
// because every box of these shares the same tag-killing design. Labels
// still print and pairing still counts; sweeps stop expecting an answer.
document.getElementById("bpair-norfid").addEventListener("click", async () => {
  const item = batchItems.find((i) => i.id === pairActiveItemId);
  if (!item || !item.sku || !batch) return;
  const want = !item.rfid_incompatible;
  if (
    want &&
    !confirm(
      `Flag ${itemDisplayName(item)} as "won't RFID scan"?\n\n` +
        `Labels still print and pairing still counts - but sweeps and ` +
        `Verify stop expecting its tags to answer. Applies to this ` +
        `product store-wide, and is logged.`
    )
  )
    return;
  try {
    await apiJson(
      `/api/products/${encodeURIComponent(item.sku)}/rfid-incompatible`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          incompatible: want,
          changed_by: operatorEl.value || null,
        }),
      }
    );
    batchItems.forEach((i) => {
      if ((i.sku || "").toUpperCase() === item.sku.toUpperCase())
        i.rfid_incompatible = want;
    });
    renderPairCard();
    setBatchResult(
      want
        ? `⊘ ${itemDisplayName(item)} flagged - sweeps won't expect it to answer.`
        : `Flag removed from ${itemDisplayName(item)}.`,
      "ok"
    );
  } catch (err) {
    setBatchResult(err.message, "err");
  }
});

bEl.pairUndo.addEventListener("click", async () => {
  const last = pairHistory.pop();
  if (!last || !batch) return;
  try {
    const data = await postJson(`/api/batches/${batch.id}/pair/undo`, last);
    const idx = batchItems.findIndex((i) => i.id === data.item.id);
    if (idx >= 0) Object.assign(batchItems[idx], data.item);
    renderPairItems();
    renderPairCard();
    setBatchResult(`Undid tag ${last.epc}.`, "ok");
  } catch (err) {
    setBatchResult(err.message, "err");
  }
  bEl.pairInput.focus();
});

// Release every tie this batch made — for when a shelf needs re-pairing
// from scratch (no reprinting, the labels are still good).
document.getElementById("bpair-reset").addEventListener("click", async () => {
  if (!batch) return;
  const paired = batchItems.reduce((n, i) => n + i.paired_count, 0);
  if (!paired) {
    setBatchResult("Nothing paired in this batch yet.", "err");
    return;
  }
  if (
    !confirm(
      `Release all ${paired} tag(s) paired in this batch?\n\nThe printed ` +
        `labels stay valid - you just re-scan them onto their products. ` +
        `Nothing in Shopify changes.`
    )
  )
    return;
  try {
    const res = await postJson(`/api/batches/${batch.id}/unpair-all`, {});
    pairHistory = [];
    pairActiveItemId = null;
    await pullBatch(false);
    renderPairItems();
    renderPairCard();
    setBatchResult(
      `${res.removed} tie(s) released - pair the shelf again.`,
      "ok"
    );
  } catch (err) {
    setBatchResult(err.message, "err");
  }
});

bEl.toVerify.addEventListener("click", () => showBatchStage("verify"));

// "Set to N" on a verify row: raise Shopify on-hand to the count the
// shelf walk physically found. One confirmation, server-guarded to
// increases only, logged with an Undo in History.
bEl.verifyReport.addEventListener("click", async (e) => {
  // Corrected counts from an expanded flagged row: scan count + already-
  // tagged count. LOCAL batch numbers only — nothing here touches
  // Shopify; the on-hand button stays the one explicit write.
  const saveBtn = e.target.closest(".bvx-save");
  if (saveBtn && batch) {
    const detail = saveBtn.closest("tr.bvx-detail");
    const qty = parseInt(detail.querySelector(".bvx-qty").value, 10);
    const tb = parseInt(detail.querySelector(".bvx-tb").value, 10);
    const id = parseInt(saveBtn.dataset.item, 10);
    if (isNaN(qty) || isNaN(tb) || qty < 0 || tb < 0) return;
    saveBtn.disabled = true;
    try {
      await postJson(`/api/batches/${batch.id}/items/${id}/qty`, { qty });
      await apiJson(`/api/batches/${batch.id}/items/${id}/tagged-before`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          count: tb,
          updated_by: operatorEl.value || null,
        }),
      });
      await runVerifyCheck(id);
      setBatchResult(
        "Counts corrected ✓ - batch records only. If Shopify's on-hand " +
          "should change too, use the row's Set-to button.",
        "ok"
      );
    } catch (err) {
      setBatchResult(err.message, "err");
      saveBtn.disabled = false;
    }
    return;
  }
  // "This batch physically handled the box(es) here" — a walked bin is a
  // deep manual check, so a disagreeing Shopify bin gets a one-tap fix
  // (the same audited bin write as everywhere else).
  const setBin = e.target.closest(".bvx-setbin");
  if (setBin && batch) {
    const sku = setBin.dataset.sku;
    const was = setBin.dataset.was || "nothing";
    if (
      !confirm(
        `Set the Shopify bin for ${sku} to ${batch.bin_name}?\n\n` +
          `Shopify currently says: ${was}. This is the normal audited ` +
          `bin write - Shopify, the bin map and this product's tags all ` +
          `follow, with a History entry.`
      )
    )
      return;
    setBin.disabled = true;
    try {
      await postJson("/api/bin-updates", {
        target: sku,
        bin: batch.bin_name,
        changed_by: operatorEl.value || null,
      });
      setBatchResult(
        `Shopify bin for ${sku} set to ${batch.bin_name} ✓`,
        "ok"
      );
      await runVerifyCheck(
        (setBin.closest("tr") || {}).dataset
          ? setBin.closest("tr").dataset.vrow || null
          : null
      );
    } catch (err) {
      setBatchResult(err.message, "err");
      setBin.disabled = false;
    }
    return;
  }
  // Presumed-sold cleanup: retire the unheard tag records whose
  // shortfall matched sales/on-hand. Local records only — Shopify is
  // never touched — and every EPC is undoable from History.
  // Manual retire from the expanded row (Nick, 2026-08-24): for tags
  // the operator has PHYSICALLY confirmed gone, even when sales or
  // on-hand don't fully back it. The confirmation makes them attest the
  // boxes are really absent and the tags aren't just dead on present
  // boxes (that's the replace-tag flow's job).
  const manualBtn = e.target.closest(".bvx-retire-manual");
  if (manualBtn && batch) {
    const operator = operatorEl.value;
    if (!operator) {
      alert("Pick who's scanning (top right) first.");
      return;
    }
    const epcs = (manualBtn.dataset.epcs || "").split(",").filter(Boolean);
    if (!epcs.length) return;
    if (
      !confirm(
        `Manually retire ${epcs.length} unheard tag record(s) for ` +
          `${manualBtn.dataset.sku} as presumed sold?\n\n` +
          `Only do this after physically checking the shelf:\n` +
          `- the box(es) really are NOT there (sold, moved, gone), and\n` +
          `- the tags aren't just dead or blocked on boxes still ` +
          `present. A dead tag on a present box goes through the check ` +
          `step's replace-tag flow instead.\n\n` +
          `Local records only, Shopify is not touched. The records ` +
          `stay as tombstones (a return is recognized and restorable), ` +
          `and every EPC is undoable from History.`
      )
    )
      return;
    manualBtn.disabled = true;
    try {
      await postJson("/api/assignments/retire", {
        epcs,
        kind: "presumed-sold",
        changed_by: operator,
        note: `manual verify retire, bin ${batch.bin_name}`,
      });
      setBatchResult(
        `${epcs.length} tag(s) manually retired ✓ (undo in History)`,
        "ok"
      );
      await runVerifyCheck(
        (manualBtn.closest("tr.bvx-detail") || {}).dataset
          ? manualBtn.closest("tr.bvx-detail").dataset.for || null
          : null
      );
    } catch (err) {
      manualBtn.disabled = false;
      setBatchResult(err.message, "err");
    }
    return;
  }
  const retireBtn = e.target.closest(".bvx-retire");
  if (retireBtn && batch) {
    const operator = operatorEl.value;
    if (!operator) {
      alert("Pick who's scanning (top right) first.");
      return;
    }
    const epcs = (retireBtn.dataset.epcs || "").split(",").filter(Boolean);
    if (!epcs.length) return;
    if (
      !confirm(
        `Retire ${epcs.length} tag record(s) for ${retireBtn.dataset.sku} ` +
          `as presumed sold?\n\nThe sweep never heard them and the ` +
          `shortfall matches the sales/on-hand numbers. Records move to ` +
          `the retired list (kept forever - returns recoverable), ` +
          `History-logged with Undo. Shopify is not touched.`
      )
    )
      return;
    retireBtn.disabled = true;
    try {
      await postJson("/api/assignments/retire", {
        epcs,
        kind: "presumed-sold",
        changed_by: operator,
        note: `verify sweep, bin ${batch.bin_name}`,
      });
      setBatchResult(
        `${epcs.length} tag(s) retired as presumed sold ✓ (undo in History)`,
        "ok"
      );
      await runVerifyCheck(
        (retireBtn.closest("tr") || {}).dataset
          ? retireBtn.closest("tr").dataset.vrow || null
          : null
      );
    } catch (err) {
      setBatchResult(err.message, "err");
      retireBtn.disabled = false;
    }
    return;
  }
  // Flagged rows expand into their explanation, like the Review inbox.
  const flagRow = e.target.closest("tr.bvx-flag");
  if (flagRow && !e.target.closest("a, button, input, label")) {
    const det = bEl.verifyReport.querySelector(
      `tr.bvx-detail[data-for="${flagRow.dataset.item}"]`
    );
    if (det) {
      det.hidden = !det.hidden;
      if (!det.hidden) updateBvxSum(det);
    }
    return;
  }
  // "Raise all": one confirmation listing every change, then each row's
  // update runs as its OWN write — separate API call, History entry and
  // Undo, exactly as if each button were pressed by hand.
  const allBtn = e.target.closest("#bverify-fixall");
  if (allBtn && batch) {
    const btns = [...bEl.verifyReport.querySelectorAll(".onhand-fix")];
    if (!btns.length) return;
    const lines = btns.map(
      (b) => `${b.dataset.sku}: ${b.dataset.exp} → ${b.dataset.qty}`
    );
    if (
      !confirm(
        `Raise Shopify ON-HAND for ${btns.length} product(s)?\n\n` +
          lines.join("\n") +
          `\n\nEach writes separately - every product gets its own ` +
          `History entry and Undo.`
      )
    )
      return;
    allBtn.disabled = true;
    let done = 0;
    const failed = [];
    for (const b of btns) {
      try {
        await postJson("/api/onhand-updates", {
          sku: b.dataset.sku,
          new_qty: parseInt(b.dataset.qty, 10),
          changed_by: operatorEl.value || null,
          confirmed: true,
          batch_id: batch.id,
          item_id: parseInt(b.dataset.item, 10) || null,
          sweep_at: verifySweepAt,
        });
        done++;
      } catch (err) {
        failed.push(`${b.dataset.sku}: ${err.message}`);
      }
    }
    await runVerifyCheck();
    setBatchResult(
      `${done} on-hand value(s) raised` +
        (failed.length
          ? ` · ${failed.length} FAILED - ${failed.join(" · ")}`
          : " ✓ (each has its own Undo in History)"),
      failed.length ? "err" : "ok"
    );
    return;
  }
  const btn = e.target.closest(".onhand-fix");
  if (!btn || !batch) return;
  const sku = btn.dataset.sku;
  const qty = parseInt(btn.dataset.qty, 10);
  if (
    !confirm(
      `Set Shopify ON-HAND for ${sku} to ${qty}?\n\n` +
        `Shopify expected ${btn.dataset.exp}; the shelf walk physically ` +
        `found ${qty}.\n\nThis WRITES the number to Shopify. Undo stays ` +
        `available in History.`
    )
  )
    return;
  btn.disabled = true;
  try {
    const res = await postJson("/api/onhand-updates", {
      sku,
      new_qty: qty,
      changed_by: operatorEl.value || null,
      confirmed: true,
      batch_id: batch.id,
      item_id: parseInt(btn.dataset.item, 10) || null,
      sweep_at: verifySweepAt,
    });
    setBatchResult(res.message, "ok");
    // Only this product's row re-checks and repaints (Nick, 2026-09-14).
    await runVerifyCheck(parseInt(btn.dataset.item, 10) || null);
  } catch (err) {
    btn.disabled = false;
    setBatchResult(err.message, "err");
  }
});

// Lowering: only rendered when the server's can_lower gate passed
// (recorded sales fully back the drop). One confirmed click lowers
// on-hand, retires the listed silent tags presumed-sold, and consumes
// the sales; one History undo reverses all three.
bEl.verifyReport.addEventListener("click", async (e) => {
  const btn = e.target.closest(".onhand-lower");
  if (!btn || !batch) return;
  const sku = btn.dataset.sku;
  const qty = parseInt(btn.dataset.qty, 10);
  const epcs = (btn.dataset.epcs || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  const unb = parseInt(btn.dataset.unbacked, 10) || 0;
  if (
    !confirm(
      `Set Shopify ON-HAND for ${sku} DOWN to ${qty}?\n\n` +
        (unb
          ? `⚠ ${unb} of the missing unit(s) have NO recorded sale - ` +
            `they are written off as shrinkage (allowed: this product ` +
            `completed a batch tagging before). `
          : `Recorded sales account for the missing unit(s). `) +
        `This lowers the count, retires ${epcs.length} silent tag(s) ` +
        `as presumed-sold, and consumes what sales cover.\n\n` +
        `One Undo in History reverses all of it.`
    )
  )
    return;
  btn.disabled = true;
  try {
    const res = await postJson("/api/onhand-updates/lower", {
      sku,
      bin_name: batch.bin_name,
      new_qty: qty,
      epcs,
      changed_by: operatorEl.value || null,
      confirmed: true,
      batch_id: batch.id,
      item_id: parseInt(btn.dataset.item, 10) || null,
      sweep_at: verifySweepAt,
    });
    setBatchResult(res.message, "ok");
    await runVerifyCheck(parseInt(btn.dataset.item, 10) || null);
  } catch (err) {
    btn.disabled = false;
    setBatchResult(err.message, "err");
  }
});

// --- Stage 5: verify --------------------------------------------------------
bEl.verifyInput.addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  const code = bEl.verifyInput.value.trim();
  bEl.verifyInput.value = "";
  if (!code) return;
  verifyEpcs.add(code.toUpperCase());
  bEl.verifyCount.textContent = `${verifyEpcs.size} unique tags collected.`;
});

// The C72 companion app sends its sweep to the server over Wi-Fi; this
// pulls the most recent one into the verify set — no Bluetooth, no wedge —
// then checks the bin straight away (pulling to not check was busywork).
document.getElementById("bverify-pull").addEventListener("click", async () => {
  try {
    const cap = await apiJson("/api/epc-captures/latest");
    const before = verifyEpcs.size;
    cap.epcs.forEach((e) => verifyEpcs.add(String(e).toUpperCase()));
    noteVerifySweep(cap.created_at);
    bEl.verifyCount.textContent = `${verifyEpcs.size} unique tags collected.`;
    setBatchResult(
      `Pulled sweep #${cap.id} from ${cap.device || "the C72"} ` +
        `(${cap.epc_count} tags, ${fmtWhen(cap.created_at)}) - ` +
        `${verifyEpcs.size - before} new. Checking…`,
      "ok"
    );
    await runVerifyCheck();
  } catch (err) {
    setBatchResult(err.message, "err");
  }
});

// With onlyItemId set, the check still runs in full but only that
// item's row (and its expandable detail) is swapped into the live
// table - the page, scroll, open panels and the other rows never move
// (Nick, 2026-09-14: raising a count kept re-painting the whole bin).
// The verdict/summary lines above the table refresh on the next full
// run; a per-product write only changes that product's story.
async function runVerifyCheck(onlyItemId = null) {
  if (!batch) return;
  const rep = await postJson(`/api/batches/${batch.id}/verify`, {
    epcs: [...verifyEpcs],
  });
  // Per-product agreement: boxes scanned == tags paired == tags detected.
  let boxesOk = true;
  let pairedOk = true;
  let detectedOk = true;
  let yellowCount = 0;
  const rows = rep.items
    .map((r) => {
      // "Won't RFID scan" products are expected silent: their detected
      // column reads n/a and never drags the verdict down.
      const na = r.rfid_incompatible;
      // Boxes already stickered before this batch (side trip, earlier
      // session): no scans or pairs happened HERE — that's expected —
      // but they are boxes on the shelf and their tags must answer the
      // sweep like anyone else's.
      const tb = r.tagged_before || 0;
      const boxes = r.qty_scanned + tb;
      const paired = r.paired_count === r.qty_scanned;
      // Accept EITHER this batch's own pairs alone (the already-tagged
      // boxes' tags may sit out of range) OR pairs + already-tagged
      // together. A count in between — one or two answering from across
      // the store rather than the whole bundle — or above is the only
      // case worth a flag (Nick, 2026-08-06).
      // Server verdict (2026-08-19): red only when THIS batch's chain
      // breaks (printed → paired → own tags heard); earlier tags going
      // quiet is yellow — sold or moved before this batch, never a
      // failure of the work just done.
      const red =
        r.state === "pairing-short" || r.state === "batch-silent";
      const yel = r.state === "prior-silent";
      const detected = !red;
      if (r.qty_scanned !== r.paired_count) boxesOk = false;
      if (!paired) pairedOk = false;
      if (red) detectedOk = false;
      if (yel) yellowCount++;
      // Shopify's expected count, with the shortfall/overage in brackets:
      // "6 (−2)" = expected 6, found 4. DISPLAY ONLY — nothing here (and
      // no click) ever writes a count back.
      const found = r.units_total ?? r.qty_scanned;
      let expCell = "—";
      if (r.expected_qty != null) {
        const diff = found - r.expected_qty;
        expCell =
          `${r.expected_qty}` +
          (diff
            ? ` <span class="bexp--off">(${diff > 0 ? "+" : "−"}${Math.abs(diff)})</span>`
            : "");
        // Corrections ride as small icons in the cell. Upward: finding
        // more boxes than Shopify knew is physical proof. Downward: only
        // when the server says recorded sales fully back the drop
        // (can_lower); the endpoint re-checks everything.
        if (diff > 0 && r.sku) {
          expCell += ` <button class="reset onhand-fix onhand-fix--icon" type="button"
            data-sku="${escapeHtml(r.sku)}" data-qty="${found}"
            data-exp="${r.expected_qty}" data-item="${r.item_id}"
            title="Set product count to ${found} (writes Shopify on-hand; confirmed, logged, undoable from History)">⇪</button>`;
        } else if (diff < 0 && r.sku && r.can_lower) {
          const epcs = (r.shelf && r.shelf.unheard_epcs) || [];
          const unb = r.lower_unbacked || 0;
          expCell += ` <button class="reset onhand-lower onhand-fix--icon" type="button"
            data-sku="${escapeHtml(r.sku)}" data-qty="${found}"
            data-item="${r.item_id}" data-unbacked="${unb}"
            data-epcs="${escapeHtml(epcs.slice(0, Math.max(0, -diff)).join(","))}"
            title="Set product count to ${found} (${
              unb
                ? `${unb} of the ${-diff} missing unit(s) have no recorded sale - written off as shrinkage; allowed because this product completed a tagging before`
                : `recorded sales account for the ${-diff} missing`
            }; lowers Shopify on-hand, retires the silent tags presumed-sold; one undo reverses all of it)">⇩</button>`;
        }
      }
      const flaggedRow = (red || yel || !paired) && !na;
      // Below-expected filter (Nick, 2026-09-02, the 8h11h6 case):
      // counted 1, every tag heard, but Shopify expects 2 - a clean
      // candidate for lowering the count. Deliberately EXCLUDES any
      // row with unheard tags or other flags: those are different
      // problems with their own flows.
      const lowClean =
        !na &&
        r.expected_qty != null &&
        found < r.expected_qty &&
        !red &&
        !yel &&
        paired &&
        !(r.shelf && (r.shelf.unheard_epcs || []).length);
      // A flagged row expands (like the Review inbox) into the item's
      // preview, what the sweep actually said, and the two counts whose
      // sum is checked against Shopify — corrected here, never written
      // anywhere automatically.
      const binsSaid = (r.detected_bins || [])
        .map((b) => `${escapeHtml(b.bin)} ×${b.count}`)
        .join(", ");
      const detail = flaggedRow
        ? `<tr class="bvx-detail" data-for="${r.item_id}" data-exp="${r.expected_qty ?? ""}" hidden><td colspan="6">
            <div class="bvx__wrap">
              ${r.image_url ? `<img class="bvx__img" src="${escapeHtml(r.image_url)}" alt="">` : ""}
              <div class="bvx__body">
                <div class="bvx__why">${
                  r.reason ? `${escapeHtml(r.reason)}. ` : ""
                }${
                  !paired && !r.reason
                    ? `${r.paired_count} tag(s) paired vs ${r.qty_scanned} box(es) scanned - finish pairing at the gun, or fix the scan count below. `
                    : ""
                }The sweep heard <b>${r.detected}</b> tag(s) of this product (${
                  r.detected_batch ?? 0
                } from this batch, ${r.detected_other ?? 0} earlier${
                  binsSaid ? `; records say: ${binsSaid}` : ""
                }); this batch printed ${r.printed_count ?? 0} and paired ${
                  r.paired_count
                }${tb ? `, with ${tb} marked already-tagged` : ""}.</div>
                <div class="bvx__inputs">
                  <label>New boxes scanned
                    <input type="number" min="0" max="500" class="bvx-qty" value="${r.qty_scanned}"></label>
                  <label>Already RFID-tagged
                    <input type="number" min="0" max="500" class="bvx-tb" value="${tb}"></label>
                  <input class="bvx__sum" type="text" readonly tabindex="-1">
                  <button class="reset bvx-save" type="button" data-item="${r.item_id}">Save counts</button>
                </div>${
                  r.shelf && (r.shelf.unheard_epcs || []).length
                    ? `<div class="bvx__manual">
                        <button class="reset bvx-retire-manual" type="button"
                          data-epcs="${escapeHtml(r.shelf.unheard_epcs.join(","))}"
                          data-sku="${escapeHtml(r.sku || "")}"
                          title="For tags you have PHYSICALLY confirmed are gone, even when sales or on-hand don't fully account for them">Retire ${(r.shelf.unheard_epcs || []).length} unheard tag(s) manually…</button>
                      </div>`
                    : ""
                }
              </div>
            </div>
          </td></tr>`
        : "";
      return `<tr data-vrow="${r.item_id}"${lowClean ? ' data-low="1"' : ""}${
        flaggedRow
          ? ` class="bvx-flag${yel && !red ? " bvx-flag--yel" : ""}" data-item="${r.item_id}" title="Click to review - what the sweep heard vs this batch's counts"`
          : ""
      }>
        <td>${productLink(r.product_title, r.shopify_product_id, r.sku)}${
          na
            ? ' <span class="noscan-chip" title="tag won\'t scan when on box - sweeps don\'t expect it to answer">⊘</span>'
            : ""
        }${
          // Presumed-sold cleanup: the shelf reconciliation matched the
          // shortfall to sales/on-hand, so the unheard records can be
          // retired right here. Only offered when the numbers agree
          // EXACTLY — a partial mismatch is check-step business.
          r.shelf &&
          r.shelf.presumed_sold > 0 &&
          (r.shelf.unheard_epcs || []).length === r.shelf.presumed_sold
            ? ` <button class="reset bvx-retire" type="button"
                data-epcs="${escapeHtml(r.shelf.unheard_epcs.join(","))}"
                data-sku="${escapeHtml(r.sku || "")}"
                title="${r.shelf.heard} of ${r.shelf.on_file} recorded tag(s) answered and the ${r.shelf.presumed_sold} missing match ${
                  r.shelf.basis === "sales"
                    ? "sales since tagging"
                    : "the live on-hand"
                } - retire them (local records only, undoable from History)">Retire ${r.shelf.presumed_sold} presumed sold</button>`
            : ""
        }${
          r.bin_differs && r.sku
            ? ` <button class="binfix bvx-setbin" type="button" data-sku="${escapeHtml(
                r.sku
              )}" data-was="${escapeHtml(
                r.bin_location || "nothing"
              )}" title="Shopify's bin for this product says ${escapeHtml(
                r.bin_location || "nothing"
              )}, but this batch physically handled it on ${escapeHtml(
                batch.bin_name
              )}. Click to write ${escapeHtml(
                batch.bin_name
              )} to Shopify (audited, undoable via History).">bin ⇢ ${escapeHtml(
                batch.bin_name
              )}</button>`
            : ""
        }</td>
        <td class="mono">${escapeHtml(r.sku || "—")}</td>
        <td class="num">${boxes}${
          tb ? `<div class="bexp--note" title="${r.qty_scanned} scanned this batch + ${tb} already tagged">(${r.qty_scanned} + ${tb})</div>` : ""
        }</td>
        <td class="num">${expCell}</td>
        <td class="num${
          red
            ? " bexp--off"
            : yel
              ? " bexp--warn"
              : !na && r.expected_qty != null && r.detected === r.expected_qty
                ? " bexp--ok"
                : ""
        }">${
          na
            ? (r.detected > 0 ? `${r.detected} ⊘` : "n/a")
            : !red && !yel && r.expected_qty != null &&
                r.detected === r.expected_qty
              ? `${r.detected} ✓`
              : r.detected
        }</td>
        <td>${
          na && paired
            ? "⊘"
            : red || !paired
              ? "⚠ ▸"
              : yel
                ? '<span class="bexp--warn" title="earlier tags silent - likely sold or moved before this batch">⚠ ▸</span>'
                : "✓"
        }</td>
      </tr>${detail}`;
    })
    .join("");

  const otherCount = rep.foreign.length + rep.unknown_epcs.length;
  const otherRows = [
    ...rep.foreign.map(
      (f) =>
        `<li>${escapeHtml(f.product_title || "?")} <span class="mono">${escapeHtml(f.epc)}</span>${
          f.bin_location ? " · bin " + escapeHtml(f.bin_location) : ""
        }</li>`
    ),
    ...rep.unknown_epcs.map(
      (e) => `<li>Unknown tag <span class="mono">${escapeHtml(e)}</span></li>`
    ),
  ].join("");

  // The verdict line states which of the three columns agree.
  const mismatches = [];
  if (!pairedOk) mismatches.push("tags paired ≠ boxes scanned");
  if (!detectedOk)
    mismatches.push("tags paired in THIS batch are missing from the sweep");
  const verdict = mismatches.length
    ? `<p class="result result--err">⚠ This batch's own chain (printed → paired → heard) does NOT hold - ${mismatches.join(
        " · "
      )}. Check the ⚠ rows.</p>`
    : `<p class="result result--ok">✓ Every label printed here was paired, and every tag paired here answered the sweep.</p>`;
  const yellowNote = yellowCount
    ? `<p class="result result--warn-soft">⚠ ${yellowCount} product(s) have EARLIER tags that stayed silent - likely sold or moved before this batch. Yellow rows; the retire buttons clean their records.</p>`
    : "";
  // Expected silence is stated out loud, not hidden inside a green tick:
  // flagged products were paired but no sweep will ever hear them.
  const naSilent = rep.items.filter(
    (r) => r.rfid_incompatible && r.paired_count > 0 && r.detected === 0
  ).length;
  const naNote = naSilent
    ? `<p class="result">⊘ ${naSilent} product(s) flagged "won't RFID scan" answered nothing, as expected - their tags are paired and counted; the sweep can't hear them on the box.</p>`
    : "";
  // The already-tagged exception is said out loud too: 0 scanned and 0
  // paired on those rows is CORRECT, not a miss — the boxes arrived with
  // stickers from an earlier session and only need to answer the sweep.
  const tbRows = rep.items.filter((r) => (r.tagged_before || 0) > 0);
  const tbNote = tbRows.length
    ? `<p class="result">✓ ${tbRows.length} product(s) had boxes already RFID tagged before this batch (side trip or earlier session) - 0 scans and 0 pairs there is expected; their tags are counted in Detected instead.</p>`
    : "";
  // Unresolved codes are a heads-up, never a blocker: completing simply
  // drops them (same as removing them by hand) — no Review task is filed.
  const unresolvedNote = (rep.unresolved_codes || []).length
    ? `<p class="result result--warn-soft">⚠ ${rep.unresolved_codes.length} unresolved barcode(s) still in this batch (${rep.unresolved_codes
        .map(escapeHtml)
        .join(", ")}) - they never matched a product. Completing drops them; nothing goes to Review. Link them at the Scan Station first if they matter.</p>`
    : "";

  // One button to press every eligible "Set to N" in turn — each write
  // stays its own API call and its own History row with its own Undo.
  const fixable = rep.items.filter(
    (r) =>
      r.sku &&
      r.expected_qty != null &&
      (r.units_total ?? r.qty_scanned) > r.expected_qty
  );
  const fixAll =
    fixable.length > 1
      ? `<div class="linkbox__actions u-mt8">
           <button class="reset" id="bverify-fixall" type="button"
             title="Runs each row's Set-to button in turn - every product gets its own confirmation summary line, History entry and Undo">
             Raise on-hand for all ${fixable.length} short products…</button>
         </div>`
      : "";

  // Tombstones that answered: a replaced/dead sticker still on a box
  // (the peel step was skipped) or a presumed-sold tag back in range
  // (probably a return). Named out loud, never lumped into "unknown".
  const retiredNote = (rep.retired_heard || []).length
    ? `<p class="result result--warn-soft">⚠ ${
        rep.retired_heard.length
      } retired tag(s) answered the sweep: ${rep.retired_heard
        .map(
          (t) =>
            `${escapeHtml(t.sku || t.product_title || "?")} <span class="mono">${escapeHtml(
              t.epc
            )}</span> - ${escapeHtml(t.message)}`
        )
        .join(" · ")}</p>`
    : "";

  // Surgical single-row swap (see the function comment): graft the
  // fresh row pair over the live one and leave everything else alone.
  if (onlyItemId != null) {
    const tmp = document.createElement("tbody");
    tmp.innerHTML = rows;
    const freshMain = tmp.querySelector(`tr[data-vrow="${onlyItemId}"]`);
    const liveMain = bEl.verifyReport.querySelector(
      `tr[data-vrow="${onlyItemId}"]`
    );
    if (freshMain && liveMain) {
      const freshDetail = tmp.querySelector(
        `tr.bvx-detail[data-for="${onlyItemId}"]`
      );
      const liveDetail = bEl.verifyReport.querySelector(
        `tr.bvx-detail[data-for="${onlyItemId}"]`
      );
      const detailWasOpen = liveDetail && !liveDetail.hidden;
      if (liveDetail) liveDetail.remove();
      liveMain.replaceWith(freshMain);
      if (freshDetail) {
        freshMain.after(freshDetail);
        if (detailWasOpen) {
          freshDetail.hidden = false;
          updateBvxSum(freshDetail);
        }
      }
      return;
    }
    if (liveMain && !freshMain) {
      // The item left the report entirely - drop its row(s).
      const liveDetail = bEl.verifyReport.querySelector(
        `tr.bvx-detail[data-for="${onlyItemId}"]`
      );
      if (liveDetail) liveDetail.remove();
      liveMain.remove();
      return;
    }
    // Row not on screen (first render, filter, anything odd): fall
    // through to the honest full repaint below.
  }

  const lowCount = (rows.match(/data-low="1"/g) || []).length;
  bEl.verifyReport.innerHTML = `
    ${verdict}${yellowNote}${retiredNote}${naNote}${tbNote}${unresolvedNote}
    <div class="inventory__scroll"><table class="inventory__table">
      <thead><tr><th>Product</th><th>SKU</th><th class="num" title="Boxes physically collected this batch (new + already tagged)">Counted</th><th class="num" title="Shopify on-hand for this shelf; brackets show counted-vs-expected">Expected</th><th class="num">Detected</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>${fixAll}
    ${
      lowCount
        ? `<div class="linkbox__actions u-mt8">
             <button class="reset" id="bverify-lowfilter" type="button"
               title="Products where every tag answered and the counts agree, but the shelf simply holds FEWER than Shopify expects - the ⇩ lower-count candidates. Rows with unheard tags are a different problem and stay out of this filter.">Show only below-expected (${lowCount})</button>
           </div>`
        : ""
    }
    ${
      otherCount
        ? `<div class="linkbox__actions u-mt8">
             <button class="reset" id="bverify-others" type="button">See other detected items (${otherCount})</button>
           </div>
           <ul class="recent__list" id="bverify-otherlist" hidden>${otherRows}</ul>`
        : ""
    }`;
  const othersBtn = document.getElementById("bverify-others");
  if (othersBtn)
    othersBtn.addEventListener("click", () => {
      const list = document.getElementById("bverify-otherlist");
      list.hidden = !list.hidden;
      othersBtn.textContent = list.hidden
        ? `See other detected items (${otherCount})`
        : "Hide other detected items";
    });
  // Below-expected filter (Nick, 2026-09-02): survives re-renders (every
  // sweep and count-save rebuilds this report).
  const lowBtn = document.getElementById("bverify-lowfilter");
  if (lowBtn) {
    lowBtn.addEventListener("click", () => {
      verifyLowOnly = !verifyLowOnly;
      applyVerifyLowFilter(lowCount);
    });
    applyVerifyLowFilter(lowCount);
  } else {
    verifyLowOnly = false;
  }
  return rep;
}

// When ON, only the clean below-expected rows show; flagged rows'
// expandable detail rows collapse with their parents.
let verifyLowOnly = false;

function applyVerifyLowFilter(lowCount) {
  const btn = document.getElementById("bverify-lowfilter");
  if (!btn) return;
  btn.textContent = verifyLowOnly
    ? "Show all items"
    : `Show only below-expected (${lowCount})`;
  bEl.verifyReport
    .querySelectorAll("tbody tr")
    .forEach((tr) => {
      if (tr.classList.contains("bvx-detail")) {
        // Detail rows manage their own hidden state when the filter is
        // off; the filter forces them closed while on.
        if (verifyLowOnly) tr.hidden = true;
        return;
      }
      tr.hidden = verifyLowOnly && tr.dataset.low !== "1";
    });
}

// "Check bin" is now a lookup: point the current sweep at ANY bin and see
// what it says — handy when a stray tag might belong to a neighbour.
bEl.verifyCheck.addEventListener("click", async () => {
  const name = prompt(
    "Check which bin against this sweep?",
    batch ? batch.bin_name : ""
  );
  if (name === null) return;
  const bin = name.trim();
  if (!bin) return;
  bEl.verifyCheck.disabled = true;
  try {
    const rep = await postJson(`/api/bins/${encodeURIComponent(bin)}/check`, {
      epcs: [...verifyEpcs],
    });
    const rows = rep.items
      .map(
        (r) => `<tr>
          <td>${escapeHtml(r.product_title || "")}</td>
          <td class="mono">${escapeHtml(r.sku || "—")}</td>
          <td class="num">${r.expected_qty ?? "—"}</td>
          <td class="num">${r.tags_on_file}</td>
          <td class="num${r.detected ? "" : " bexp--off"}">${r.detected}</td>
        </tr>`
      )
      .join("");
    bEl.verifyReport.innerHTML = `
      <p class="result">Bin <b>${escapeHtml(rep.bin)}</b> checked against ${rep.swept} swept tag(s) - ${rep.count} product(s) on file there.</p>
      <div class="inventory__scroll"><table class="inventory__table">
        <thead><tr><th>Product</th><th>SKU</th><th class="num">On hand</th><th class="num">Tags on file</th><th class="num">Detected</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="5" class="inventory__empty">Nothing on file for that bin.</td></tr>'}</tbody>
      </table></div>`;
  } catch (err) {
    setBatchResult(err.message, "err");
  } finally {
    bEl.verifyCheck.disabled = false;
  }
});

// Live sum in an expanded verify row: (new + already-tagged) vs Shopify.
function updateBvxSum(detail) {
  const q = parseInt(detail.querySelector(".bvx-qty").value, 10) || 0;
  const t = parseInt(detail.querySelector(".bvx-tb").value, 10) || 0;
  const exp = detail.dataset.exp;
  const sum = detail.querySelector(".bvx__sum");
  let text = `= ${q + t} box(es) total`;
  if (exp !== "") {
    const diff = q + t - parseInt(exp, 10);
    text += ` vs expected ${exp}${
      diff ? ` (${diff > 0 ? "+" : "−"}${Math.abs(diff)})` : " ✓"
    }`;
  }
  sum.value = text;
}

bEl.verifyReport.addEventListener("input", (e) => {
  const detail = e.target.closest("tr.bvx-detail");
  if (detail) updateBvxSum(detail);
});

bEl.complete.addEventListener("click", async () => {
  if (!batch) return;
  // RFID check before finishing: every scanned box should have a tag
  // paired ("entered into inventory using RFID"). Finishing short is
  // allowed, but only past an explicit are-you-sure with the shortfall.
  const unpaired = batchItems.filter(
    (i) => i.resolved && i.paired_count < i.qty_scanned
  );
  const missingBoxes = unpaired.reduce(
    (n, i) => n + (i.qty_scanned - i.paired_count),
    0
  );
  let msg = `Complete the batch for bin ${batch.bin_name}?`;
  if (unpaired.length) {
    const names = unpaired
      .slice(0, 6)
      .map(
        (i) =>
          `• ${i.product_title || i.sku || i.scanned_code}: ` +
          `${i.paired_count}/${i.qty_scanned} entered by RFID`
      )
      .join("\n");
    msg =
      `⚠ ${unpaired.length} product(s) - ${missingBoxes} box(es) - ` +
      `have NOT been entered into inventory with RFID tags yet:\n\n` +
      `${names}${unpaired.length > 6 ? "\n…" : ""}\n\n` +
      `Are you sure you want to finish? The missing ones will be filed ` +
      `in Review as incomplete pairing.`;
  }
  // Closing a bin without ever sweeping it means the tags were never
  // checked against the shelf — worth one more question.
  if (!batch.verified_at) {
    msg =
      `This bin has never been verified - no RFID sweep has been checked ` +
      `against it.\n\n${msg}`;
  }
  if (!confirm(msg)) return;
  bEl.complete.disabled = true;
  try {
    const data = await postJson(`/api/batches/${batch.id}/complete`, {
      created_by: operatorEl.value || null,
      finalize: true,
    });
    batch = null;
    batchItems = [];
    pairHistory = [];
    pairActiveItemId = null;
    stopBatchLive();
    enterBatchTab();
    setBatchResult(
      "Batch done ✓ - any count mismatch shows on the audit queue.",
      "ok"
    );
  } catch (err) {
    setBatchResult(err.message, "err");
  } finally {
    bEl.complete.disabled = false;
  }
});

// === Print queue tab ========================================================
// Stop printing (Nick, 2026-08-25): the printer is spewing - wax out,
// wrong labels, jam - and the run must halt NOW. Cancels every label
// still waiting; the agent's next claim comes back empty, so at most
// the handful already claimed still come out. Enabled only while
// something is queued or printing.
document
  .getElementById("printer-stop")
  .addEventListener("click", async () => {
    const waiting = ((queueData && queueData.jobs) || []).filter(
      (j) => j.status === "pending" || j.status === "printing"
    ).length;
    if (
      !confirm(
        `Stop printing?\n\n${waiting} label(s) are queued or coming out. ` +
          `Everything still waiting is canceled; at most the few already ` +
          `claimed by the printer finish. Reprint anything you need from ` +
          `the Queue or the batch's Print step.`
      )
    )
      return;
    const btn = document.getElementById("printer-stop");
    btn.disabled = true;
    try {
      const res = await postJson("/api/print-jobs/stop", {
        requested_by: operatorEl.value || null,
      });
      alert(res.message);
    } catch (err) {
      alert(err.message);
    }
    loadQueue();
  });

// Resume printing (Nick, 2026-08-26): the Stop button's inverse. A
// stopped label never printed and its EPC was never used, so its job
// simply returns to pending - same rows, same original ids, so the run
// comes back in EXACTLY its original order, ahead of anything queued
// since. Stop and Resume can loop forever; both are manual.
document
  .getElementById("printer-resume")
  .addEventListener("click", async () => {
    const n = (queueData && queueData.resumable_stopped) || 0;
    if (
      !confirm(
        `Resume printing?\n\n${n} stopped label(s) go back in the ` +
          `queue in their original order and print ahead of anything ` +
          `queued since. Make sure the printer is loaded and ready.`
      )
    )
      return;
    const btn = document.getElementById("printer-resume");
    btn.disabled = true;
    try {
      const res = await postJson("/api/print-jobs/resume", {
        requested_by: operatorEl.value || null,
      });
      alert(res.message);
    } catch (err) {
      alert(err.message);
    }
    loadQueue();
  });

// Re-align (Nick, 2026-08-25): a rip at the tear bar drags the liner
// forward a random amount, so the next two labels print off-center and a
// third feeds blank while the printer finds itself again. This queues a
// single feed-to-next-home (~PH) that the print agent sends BEFORE any
// printing - the media re-registers on the gap sensor at the cost of the
// one label the rip already disturbed. Inert until the warehouse PC's
// agent is restarted on the updated print_agent.py.
document
  .getElementById("printer-realign")
  .addEventListener("click", async () => {
    if (
      !confirm(
        `Feed the printer to the next label's start?\n\n` +
          `Use this right after ripping off labels, before the next ` +
          `print. The one label the rip already pulled comes out blank ` +
          `and re-aligned - instead of two off-center prints and a ` +
          `blank. Nothing is printed or encoded.\n\n` +
          `Needs the updated print agent running on the warehouse PC ` +
          `(restart its scheduled task once after this deploy).`
      )
    )
      return;
    try {
      await postJson("/api/printer-commands", {
        printer: selectedPrinter || null,
        kind: "feed",
        requested_by: operatorEl.value || null,
      });
      setResult(
        "Re-align queued ✓ - the printer feeds to the next label on " +
          "the agent's next poll (about 3 seconds).",
        "ok"
      );
    } catch (err) {
      alert(`Could not queue the re-align: ${err.message}`);
    }
  });

// Clear stuck jobs (Nick, 2026-09-01): the ZD220 can wedge silently -
// Windows keeps saying "printing" while labels pile up behind a stuck
// head. This tells the agent to delete EVERY job in its Windows queue;
// server-side they already read done, so reprints cover anything that
// never physically came out.
document
  .getElementById("printer-purge")
  .addEventListener("click", async () => {
    if (
      !confirm(
        "Clear every job stuck in the warehouse PC's Windows print " +
          "queue?\n\nUse this when the printer wedges (agent online " +
          "but nothing comes out). The labels in that queue are " +
          "DELETED - reprint anything that never came out from this " +
          "tab afterwards. Power-cycling the printer usually helps " +
          "too."
      )
    )
      return;
    try {
      await postJson("/api/printer-commands", {
        printer: selectedPrinter || null,
        kind: "purge",
        requested_by: operatorEl.value || null,
      });
      alert(
        "Clear queued ✓ - the agent empties the Windows queue on its " +
          "next poll (about 3 seconds). Reprint anything that never " +
          "physically printed."
      );
      setTimeout(loadQueue, 4000);
    } catch (err) {
      alert(`Could not queue the clear: ${err.message}`);
    }
  });

// Whole strip at once (Nick, 2026-09-16): ON holds a side trip's labels
// until the main bin's PRINT step, so the whole batch tears off as ONE
// strip (main bin first, then each side bin) instead of one print burst
// per bin. Server-stored - the gun and every terminal follow it.
document
  .getElementById("strip-mode")
  .addEventListener("change", async (e) => {
    const box = e.target;
    const want = box.checked;
    box.disabled = true;
    try {
      const res = await postJson("/api/print-strip-mode", {
        all_at_once: want,
        worker: operatorEl.value || null,
      });
      setResult(res.message, "ok");
    } catch (err) {
      box.checked = !want;
      alert(`Could not change the strip mode: ${err.message}`);
    } finally {
      box.disabled = false;
    }
  });

// Queue grouping (Nick, 2026-08-25): jobs collapse under their batch —
// a batch-tagging run expands to its flat job rows; a RECEIVING batch
// (TC-Planner "Print labels" or the desk flow) gets a second level, one
// sub-group per product, so a bad barcode/SKU/label can be dealt with
// one at a time without holding the rest of the shipment hostage.
// Loose jobs (Scan Station prints, single reprints) stay flat rows.
let queueData = null;
const queueOpen = new Set();

function queueJobRow(j, child) {
  const tr = document.createElement("tr");
  if (child) tr.className = "queue-child";
  const canCancel = j.status === "pending";
  const canReprint = ["done", "error", "canceled"].includes(j.status);
  // The empty qarrow spacer keeps job ids flush under the group rows'
  // ids (STYLEGUIDE rule 3) - same column, same left edge, no indent.
  tr.innerHTML = `
        <td class="mono"><span class="qarrow"></span>#${j.id}</td>
        <td>${
          j.sku
            ? `<span class="prodopen queue-prod">${escapeHtml(j.label_name || j.product_title || "")}</span>`
            : escapeHtml(j.label_name || j.product_title || "")
        }${
          j.variant_title ? ` <span class="inventory__variant">(${escapeHtml(j.variant_title)})</span>` : ""
        }</td>
        <td class="mono">${
          j.sku
            ? `<a href="#" class="queue-sku">${escapeHtml(j.sku)}</a>`
            : "—"
        }</td>
        <td class="queue-bin">${escapeHtml(j.bin_location || "—")}</td>
        <td class="mono">${j.batch_id ? "#" + j.batch_id : "—"}</td>
        <td>${escapeHtml(j.requested_by || "—")}</td>
        <td><span class="chip-status chip-status--${escapeHtml(j.status)}">${escapeHtml(j.status)}</span>${
          j.error ? ` <span class="recent__meta" title="${escapeHtml(j.error)}">ⓘ</span>` : ""
        }</td>
        <td class="recent__meta">${escapeHtml(fmtWhen(j.printed_at || j.created_at))}</td>
        <td>${canCancel ? '<button class="recent__unassign" data-act="edit">edit</button><button class="recent__unassign" data-act="cancel">cancel</button>' : ""}${
          canReprint ? '<button class="recent__unassign" data-act="reprint">reprint</button>' : ""
        }</td>`;
      // SKU and product name both open the product's own window.
      tr.querySelectorAll(".queue-sku, .queue-prod").forEach((a) =>
        a.addEventListener("click", (ev) => {
          ev.preventDefault();
          openProductHistory(j.sku);
        })
      );
      const cancelBtn = tr.querySelector('[data-act="cancel"]');
      if (cancelBtn)
        cancelBtn.addEventListener("click", async () => {
          try {
            await postJson(`/api/print-jobs/${j.id}/cancel`, {});
            loadQueue();
          } catch (err) {
            alert(err.message);
          }
        });
      const editBtn = tr.querySelector('[data-act="edit"]');
      if (editBtn)
        editBtn.addEventListener("click", () => openQueueLabelEdit(j));
      const reprintBtn = tr.querySelector('[data-act="reprint"]');
      if (reprintBtn)
        reprintBtn.addEventListener("click", async () => {
          if (!confirm(`Reprint one label for ${j.sku || j.product_title}? (New EPC - the damaged label's tag stays unassigned.)`)) return;
          try {
            await postJson("/api/print-jobs", {
              quantity: 1,
              shopify_variant_id: j.shopify_variant_id,
              shopify_product_id: j.shopify_product_id,
              product_title: j.product_title,
              variant_title: j.variant_title,
              sku: j.sku,
              barcode: j.barcode,
              bin_location: j.bin_location,
              label_name: j.label_name,
              label_placement: j.label_placement || null,
              label_sku: j.label_sku || null,
              requested_by: operatorEl.value || j.requested_by,
              printer: selectedPrinter || null,
            });
            loadQueue();
          } catch (err) {
            alert(err.message);
          }
        });
  return tr;
}

// --- Edit label (Queue tab) -----------------------------------------------
// One PENDING job's three printed lines, fixable before the agent claims
// it (Nick, 2026-09-15: a reprint queued with a stale "Box 1 of 3" note).
// Refresh re-derives the lines server-side from the product's saved name
// and the current box-set registry; Save applies the typed lines to THIS
// job only - store-wide fixes stay the Pair-step reprint dialog's job.
let qeditJob = null;

function qeditLinesFromJob(j) {
  const placement = j.label_placement || "header";
  const top =
    j.label_name && placement !== "sku" ? j.label_name : STORE_HEADER;
  let sku = j.sku || "";
  if (j.label_sku) sku = j.label_sku;
  else if (j.label_name && (placement === "sku" || placement === "both"))
    sku = j.label_name;
  return { top, sku, bin: j.bin_location || "" };
}

function updateQeditPreview() {
  if (!qeditJob) return;
  const top =
    document.getElementById("qedit-top").value.trim() || STORE_HEADER;
  const skuLine = document.getElementById("qedit-sku").value.trim();
  const el = document.getElementById("qedit-prev-header");
  setPreviewHeader(el, top, top === STORE_HEADER);
  renderSkuPreviewLine("qedit-prev-sku", skuLine);
  document.getElementById("qedit-prev-bc").textContent =
    qeditJob.barcode || qeditJob.sku || "";
  const bin = document.getElementById("qedit-bin").value.trim();
  document.getElementById("qedit-prev-bin").textContent =
    bin ? "BIN: " + bin : "";
  renderFitWarn(
    document.getElementById("qedit-fitwarn"),
    top, skuLine, qeditJob.barcode || qeditJob.sku || ""
  );
}

function paintQeditFields(j) {
  const lines = qeditLinesFromJob(j);
  document.getElementById("qedit-top").value = lines.top;
  document.getElementById("qedit-sku").value = lines.sku;
  document.getElementById("qedit-bin").value = lines.bin;
  updateQeditPreview();
}

function openQueueLabelEdit(j) {
  qeditJob = j;
  document.getElementById("qedit-title").textContent =
    `#${j.id} · ${j.sku || j.product_title || ""}`;
  document.getElementById("qedit-msg").textContent = "";
  paintQeditFields(j);
  document.getElementById("qedit-overlay").hidden = false;
}

["qedit-top", "qedit-sku", "qedit-bin"].forEach((id) =>
  document.getElementById(id).addEventListener("input", updateQeditPreview)
);
document.getElementById("qedit-top-reset").addEventListener("click", () => {
  document.getElementById("qedit-top").value = STORE_HEADER;
  updateQeditPreview();
});
document.getElementById("qedit-sku-reset").addEventListener("click", () => {
  document.getElementById("qedit-sku").value =
    qeditJob ? qeditJob.sku || "" : "";
  updateQeditPreview();
});
document.getElementById("qedit-cancel").addEventListener("click", () => {
  document.getElementById("qedit-overlay").hidden = true;
  qeditJob = null;
});
document.getElementById("qedit-refresh").addEventListener("click", async () => {
  if (!qeditJob) return;
  const btn = document.getElementById("qedit-refresh");
  btn.disabled = true;
  try {
    const res = await postJson(`/api/print-jobs/${qeditJob.id}/refresh`, {
      edited_by: operatorEl.value || null,
    });
    qeditJob = res.job;
    paintQeditFields(res.job);
    document.getElementById("qedit-msg").textContent = res.message;
  } catch (err) {
    document.getElementById("qedit-msg").textContent = err.message;
  } finally {
    btn.disabled = false;
  }
});
document.getElementById("qedit-save").addEventListener("click", async () => {
  if (!qeditJob) return;
  const btn = document.getElementById("qedit-save");
  btn.disabled = true;
  try {
    await postJson(`/api/print-jobs/${qeditJob.id}/edit`, {
      top_text:
        document.getElementById("qedit-top").value.trim() || STORE_HEADER,
      sku_line: document.getElementById("qedit-sku").value.trim(),
      bin_line: document.getElementById("qedit-bin").value.trim(),
      edited_by: operatorEl.value || null,
    });
    document.getElementById("qedit-overlay").hidden = true;
    qeditJob = null;
    loadQueue();
  } catch (err) {
    document.getElementById("qedit-msg").textContent = err.message;
  } finally {
    btn.disabled = false;
  }
});

// Status counts as aligned chips - printed / queued / FAILED / voided
// always in the Status column, so outliers pop while skimming.
function queueCountChips(jobs) {
  const c = {};
  jobs.forEach((j) => (c[j.status] = (c[j.status] || 0) + 1));
  const bit = (n, cls, word) =>
    n
      ? `<span class="chip-status chip-status--${cls}">${n} ${word}</span>`
      : "";
  return (
    bit(c.done, "done", "printed") +
    bit((c.pending || 0) + (c.printing || 0), "pending", "queued") +
    bit(c.error, "error", "FAILED") +
    bit((c.voided || 0) + (c.canceled || 0), "voided", "voided")
  );
}

// Columnar tier rows (Nick, 2026-09-09, STYLEGUIDE rules 1-3): a tier
// header carries the SAME columns as the job rows under it - id range
// under Job, the task under Product, counts under Status, the newest
// print's time under When - so the queue skims as columns at every
// level. Tiers are OUTLINED (rails + tint via qt0/qt1/qin0/qin1
// classes), never indented: indentation bumps columns out of line.
function queueGroupRow(key, tier, g) {
  const open = queueOpen.has(key);
  const tr = document.createElement("tr");
  tr.className = `queue-group qt${tier}` + (open ? " qopen" : "");
  const newest = g.jobs.reduce((a, b) => (b.id > a.id ? b : a), g.jobs[0]);
  tr.innerHTML = `
    <td class="mono"><span class="qarrow">${open ? "▾" : "▸"}</span>${queueIdRange(g.jobs)}</td>
    <td>${g.label}</td>
    <td class="mono">${g.sku ? escapeHtml(g.sku) : "—"}</td>
    <td class="queue-bin">${escapeHtml(g.bin || "—")}</td>
    <td class="mono">${g.batch ? "#" + g.batch : "—"}</td>
    <td>${escapeHtml(g.by || "—")}</td>
    <td class="qcounts">${queueCountChips(g.jobs)}</td>
    <td class="recent__meta">${escapeHtml(
      fmtWhen(newest.printed_at || newest.created_at)
    )}</td>
    <td></td>`;
  tr.addEventListener("click", () => {
    open ? queueOpen.delete(key) : queueOpen.add(key);
    renderQueue();
  });
  return tr;
}

function queueIdRange(jobs) {
  const ids = jobs.map((j) => j.id);
  const lo = Math.min(...ids);
  const hi = Math.max(...ids);
  return lo === hi ? `#${lo}` : `#${lo} - #${hi}`;
}

function renderQueue() {
  const body = document.getElementById("queue-body");
  if (!queueData) return;
  const data = queueData;
  if (!data.jobs.length) {
    body.innerHTML =
      '<tr><td colspan="9" class="inventory__empty">No print jobs yet.</td></tr>';
    return;
  }
  body.innerHTML = "";
  const infos = data.batches || {};
  // First pass: batch groups AND loose-job product groups, each placed
  // at its first (newest) appearance. Loose jobs of one product carry a
  // print_session token per barcode reset; jobs older than the token
  // fall back to adjacency runs (uninterrupted stretches in the queue).
  const order = [];
  const byBatch = new Map();
  const byProduct = new Map();
  let lastLoose = null; // {group, runKey} for the adjacency fallback
  for (const j of data.jobs) {
    if (j.batch_id) {
      let g = byBatch.get(j.batch_id);
      if (!g) {
        g = { id: j.batch_id, info: infos[j.batch_id] || {}, jobs: [] };
        byBatch.set(j.batch_id, g);
        order.push({ batch: g });
      }
      g.jobs.push(j);
      lastLoose = null;
      continue;
    }
    const pkey = (j.sku || j.product_title || "?").trim().toUpperCase();
    let g = byProduct.get(pkey);
    if (!g) {
      g = { key: pkey, jobs: [], sessions: [], bySession: new Map() };
      byProduct.set(pkey, g);
      order.push({ product: g });
    }
    g.jobs.push(j);
    let skey;
    if (j.print_session) {
      skey = `s:${j.print_session}`;
    } else if (lastLoose && lastLoose.group === g) {
      skey = lastLoose.runKey; // contiguous null-session run continues
    } else {
      skey = `r:${j.id}`;
    }
    if (!g.bySession.has(skey)) {
      g.bySession.set(skey, []);
      g.sessions.push({ key: skey, jobs: g.bySession.get(skey) });
    }
    g.bySession.get(skey).push(j);
    lastLoose = { group: g, runKey: j.print_session ? null : skey };
    if (j.print_session) lastLoose = null;
  }

  // Close each open task frame: every row after the header wears the
  // rails (qin0), the last row closes the outline (qend0).
  const flushFrame = (rows, open) => {
    if (open && rows.length > 1) {
      rows.forEach((r, i) => {
        if (i > 0) r.classList.add("qin0");
      });
      rows[rows.length - 1].classList.add("qend0");
    }
    rows.forEach((r) => body.append(r));
  };

  for (const entry of order) {
    if (entry.product) {
      const g = entry.product;
      // A lone label needs no ceremony.
      if (g.jobs.length === 1) {
        body.append(queueJobRow(g.jobs[0], false));
        continue;
      }
      const j0 = g.jobs[0];
      const key = `p|${g.key}`;
      const open = queueOpen.has(key);
      const rows = [
        queueGroupRow(key, 0, {
          jobs: g.jobs,
          label: escapeHtml(j0.product_title || g.key),
          sku: j0.sku,
          bin: j0.bin_location,
          batch: null,
          by: j0.requested_by,
        }),
      ];
      if (open) {
        if (g.sessions.length === 1) {
          // One print run — no sub level, straight to the labels.
          g.jobs.forEach((j) => rows.push(queueJobRow(j, true)));
        } else {
          for (const s of g.sessions) {
            const subKey = `${key}|${s.key}`;
            rows.push(
              queueGroupRow(subKey, 1, {
                jobs: s.jobs,
                label: `Print run · ${s.jobs.length} label(s)`,
                sku: j0.sku,
                bin: j0.bin_location,
                batch: null,
                by: s.jobs[0].requested_by,
              })
            );
            if (queueOpen.has(subKey)) {
              s.jobs.forEach((j) => {
                const r = queueJobRow(j, true);
                r.classList.add("qin1");
                rows.push(r);
              });
            }
          }
        }
      }
      flushFrame(rows, open);
      continue;
    }
    const g = entry.batch;
    const recv = g.info.kind === "receiving";
    const key = `b${g.id}`;
    const open = queueOpen.has(key);
    // The TASK is the identity (STYLEGUIDE rule 2); its number, bin
    // and people sit in their own columns like every other row.
    // Receiving batches carry their planner reference in created_by
    // ("TC-Planner · SO 123").
    const label = recv
      ? `📦 Receiving${
          g.info.created_by
            ? ` <span class="recent__meta">${escapeHtml(g.info.created_by)}</span>`
            : ""
        }`
      : "Batch tagging";
    const rows = [
      queueGroupRow(key, 0, {
        jobs: g.jobs,
        label,
        sku: null,
        // Receiving has no bin of its own (labels fan out to many);
        // its placeholder bin name would just read as noise here.
        bin: recv ? null : g.info.bin_name,
        batch: g.id,
        by: recv ? null : g.info.created_by,
      }),
    ];
    if (open) {
      if (!recv) {
        // Batch-tagging runs expand to the flat rows, exactly as before.
        g.jobs.forEach((j) => rows.push(queueJobRow(j, true)));
      } else {
        // Receiving: one sub-group per product, then the labels.
        const bySku = new Map();
        for (const j of g.jobs) {
          const sk = (j.sku || j.product_title || "?").trim();
          if (!bySku.has(sk)) bySku.set(sk, []);
          bySku.get(sk).push(j);
        }
        for (const [sk, jobs] of bySku) {
          const subKey = `${key}|${sk}`;
          rows.push(
            queueGroupRow(subKey, 1, {
              jobs,
              label: escapeHtml(jobs[0].product_title || sk),
              sku: sk,
              bin: jobs[0].bin_location,
              batch: g.id,
              by: jobs[0].requested_by,
            })
          );
          if (queueOpen.has(subKey)) {
            jobs.forEach((j) => {
              const r = queueJobRow(j, true);
              r.classList.add("qin1");
              rows.push(r);
            });
          }
        }
      }
    }
    flushFrame(rows, open);
  }
}

async function loadQueue() {
  const body = document.getElementById("queue-body");
  const pill = document.getElementById("agent-pill");
  try {
    const [agent, data] = await Promise.all([
      apiJson("/api/print-agent/status"),
      apiJson("/api/print-jobs?limit=200"),
    ]);
    // The printer's OWN fault report beats everything (v6 agents ask it
    // over USB): media out / head open / paused means labels are HELD,
    // not falsely done - the flawed-done era ends here (Nick, 2026-09-23).
    if (agent.fault) {
      pill.textContent =
        `⚠ Printer FAULTED - ${agent.fault}` +
        (agent.holding
          ? ` - ${agent.holding} label(s) held until it clears`
          : " - new labels will wait");
      pill.className = "pill pill--bad";
    } else if (agent.wedged) {
      // Wedged beats "online": the agent is fine but the PHYSICAL
      // printer stopped taking data - labels pile up in the Windows
      // queue while everything server-side reads done (Nick, 2026-09-01).
      const mins = Math.round((agent.win_oldest_seconds || 0) / 60);
      pill.textContent =
        `⚠ Printer WEDGED - ${agent.win_jobs} label(s) stuck in the ` +
        `Windows queue for ${mins}m. Power-cycle the printer, or ` +
        `Clear stuck jobs and reprint.`;
      pill.className = "pill pill--bad";
    } else {
      // readback "counter" = every done is confirmed by the printer's
      // own odometer; say so, it's the whole point of v6.
      const confirmed =
        agent.readback === "counter"
          ? " · prints confirmed by printer"
          : agent.readback === "status"
            ? " · printer status readback"
            : "";
      pill.textContent = agent.online
        ? "Printer agent: online ✓" +
          (agent.realign_capable
            ? (agent.agent_version ? ` · v${agent.agent_version}` : "") +
              confirmed
            : " · NEEDS UPDATE")
        : "Printer agent: offline";
      pill.className =
        "pill " +
        (agent.online
          ? agent.realign_capable
            ? "pill--ok"
            : "pill--warn"
          : "pill--bad");
    }
    // Clear-stuck-jobs is live only with a v4 agent polling (it's the
    // one who deletes the Windows jobs).
    const purgeBtn = document.getElementById("printer-purge");
    purgeBtn.disabled = !agent.purge_capable;
    if (!agent.purge_capable && agent.online && agent.realign_capable) {
      purgeBtn.title =
        "The warehouse PC's print agent is older than v4 - update it " +
        "(download /api/print-agent/script, replace print_agent.py, " +
        "restart the task) to clear Windows jobs from here.";
    }
    // The re-align button is honest about whether pressing it can do
    // anything: only an updated agent polls for commands.
    const realign = document.getElementById("printer-realign");
    realign.disabled = agent.online && !agent.realign_capable;
    realign.title = agent.realign_capable
      ? "Ripping labels can pull the liner forward, so the next prints " +
        "land off the sticker. This feeds the media to the NEXT label's " +
        "start (using the printer's gap sensor) before anything prints - " +
        "it consumes the one already-disturbed label instead of two " +
        "misprints and a blank. Nothing is printed or encoded."
      : "The warehouse PC's print agent is running OLD code - re-align " +
        "and the automatic backfeed fix do nothing until it's updated. " +
        "On that PC: download the current script from " +
        "/api/print-agent/script (open it with the station link), " +
        "replace print_agent.py, and restart the print agent's " +
        "scheduled task.";
    // Whole-strip toggle mirrors the server-stored setting (skip while
    // a flip of it is in flight).
    const stripBox = document.getElementById("strip-mode");
    if (!stripBox.disabled) stripBox.checked = !!agent.strip_at_once;
    // Stop printing is live only while something is actually queued or
    // coming out of the printer; otherwise it sits grayed (Nick,
    // 2026-08-25).
    const stopBtn = document.getElementById("printer-stop");
    stopBtn.disabled = !(data.jobs || []).some(
      (j) => j.status === "pending" || j.status === "printing"
    );
    // Resume is live only while a Stop press left labels behind whose
    // batch is still alive (server-counted, so the listing's limit
    // can't hide them) - Nick, 2026-08-26.
    document.getElementById("printer-resume").disabled =
      !(data.resumable_stopped > 0);
    queueData = data;
    renderQueue();
  } catch (err) {
    body.innerHTML =
      '<tr><td colspan="9" class="inventory__empty">Could not load the queue.</td></tr>';
    pill.textContent = "Printer agent: unknown";
    pill.className = "pill";
  }
}

// Orders sync: manual trigger for the daily 8 AM pull. Shares the
// "orders-sync" refresh kind with the server-side auto run, so this
// button animates mid-fill when the daily sync happens to be running.
refreshify("review-ordersync", "orders-sync", async () => {
  let outcome = null;
  try {
    const res = await postJson("/api/orders-sync/run", {});
    outcome = res.waiting_scope
      ? "Needs read_orders scope"
      : res.ok
        ? `Synced ✓ ${res.recorded || 0} new sale(s)`
        : "Sync failed";
  } catch (err) {
    outcome = "Sync failed";
  }
  renderOrderSyncNote();
  return outcome;
});

async function renderOrderSyncNote() {
  const note = document.getElementById("review-sync-note");
  try {
    const st = await apiJson("/api/orders-sync/status");
    const last = st.last_run;
    if (!last) {
      note.textContent =
        "Order sync hasn't run yet - it runs daily at 8 AM, or press " +
        "↻ Sync orders.";
      note.hidden = false;
      return;
    }
    if (last.waiting_scope) {
      note.textContent =
        "⚠ Order sync is waiting for the read_orders scope on the " +
        "Shopify app (Settings → Apps → Develop apps → Configuration). " +
        "Until then, sold boxes can't lower expected tag counts.";
      note.hidden = false;
      return;
    }
    note.textContent = last.ok
      ? `Order sync: last ran ${fmtAgo(last.at)} · ${last.orders ?? 0} ` +
        `fulfilled order(s) seen · ${last.recorded ?? 0} new sale(s) recorded`
      : `⚠ Order sync failed ${fmtAgo(last.at)}: ${last.error || "unknown"}`;
    note.hidden = false;
  } catch {
    note.hidden = true;
  }
}
// Looping ". .. ..." on a button while a slow refresh runs — proof of
// life, not progress. Returns a stop function that restores the label.
function startDots(el, base) {
  const prev = el.textContent;
  let n = 0;
  const timer = setInterval(() => {
    n = (n % 3) + 1;
    el.textContent = base + ".".repeat(n);
  }, 400);
  el.textContent = base + ".";
  return () => {
    clearInterval(timer);
    el.textContent = prev;
  };
}

// === Audits tab =============================================================
// Shopify on-hand vs RFID units, per product, summed per bin — biggest
// total mismatch first (the received-but-nowhere-to-be-found detector).
let auditData = null;
let auditShowUntagged = false;
let auditOpenBins = new Set();

function renderAuditBins() {
  const list = document.getElementById("audit-bins");
  const meta = document.getElementById("audit-meta");
  if (!auditData) return;
  const q = document
    .getElementById("audit-filter")
    .value.trim()
    .toLowerCase();
  const skipped =
    (auditData.skipped_bundles || 0) + (auditData.skipped_non_taggable || 0);
  meta.textContent =
    `(on-hand from Shopify ` +
    (auditData.onhand_age_minutes == null
      ? "- age unknown"
      : auditData.onhand_age_minutes < 60
        ? `${auditData.onhand_age_minutes} min ago`
        : `${Math.round(auditData.onhand_age_minutes / 60)} h ago`) +
    `${auditData.refreshing ? " · refreshing now…" : ""}` +
    (skipped
      ? ` · ${skipped} product(s) left out: ` +
        [
          auditData.skipped_bundles
            ? `${auditData.skipped_bundles} bundle(s)`
            : "",
          auditData.skipped_non_taggable
            ? `${auditData.skipped_non_taggable} non-taggable`
            : "",
        ]
          .filter(Boolean)
          .join(", ")
      : "") +
    `)`;
  // Default = bins that went through batch tagging to completion. A lone
  // Scan-Station tag or a carried-in stray must not promote a bin whose
  // score would be almost all never-tagged noise (the E6-1 lesson).
  document.getElementById("audit-untagged").textContent = auditShowUntagged
    ? "Show only batch-tagged bins"
    : `Show not-yet-tagged bins (${auditData.bin_count - auditData.done_bin_count})`;

  const due = auditData.bins.filter((b) => b.overdue && b.batch_done);
  if (due.length) {
    const worst = due[0];
    audSetCard(
      "ahc-drift", String(due.length),
      `audit these first - worst: ${worst.bin} (score ${worst.score})`,
      "warn"
    );
  } else {
    audSetCard(
      "ahc-drift", "0",
      `every tagged bin audited inside ${auditData.threshold_days || 14}d ✓`,
      "ok"
    );
  }

  const rows = auditData.bins.filter((b) => {
    if (!auditShowUntagged && !b.batch_done) return false;
    if (!q) return true;
    return (
      b.bin.toLowerCase().includes(q) ||
      (b.products || []).some((p) => (p.sku || "").toLowerCase().includes(q))
    );
  });
  list.innerHTML = "";
  if (!rows.length) {
    list.innerHTML = `<li class="recent__empty">${
      q
        ? "No bins match that."
        : "No batch-tagged bins yet - complete a batch first."
    }</li>`;
    return;
  }
  // The queue split (Nick, 2026-09-28): overdue bins first, then the
  // recently-audited half - each ordered by how loudly the paper
  // disagrees with the shelf's last physical truth.
  const overdueRows = rows.filter((b) => b.overdue);
  const freshRows = rows.filter((b) => !b.overdue);
  const addHeader = (text) => {
    const li = document.createElement("li");
    li.className = "u-block";
    li.innerHTML = `<div class="ph-day">${escapeHtml(text)}</div>`;
    list.append(li);
  };
  if (overdueRows.length) {
    addHeader(
      `Audit these first - last audited over ` +
      `${auditData.threshold_days || 14} day(s) ago (or never)`
    );
  }
  const renderRow = (b) => {
    const li = document.createElement("li");
    li.classList.add("u-block");
    const clean = b.score === 0;
    const open = auditOpenBins.has(b.bin);
    li.innerHTML =
      `<div class="auditrow${clean ? " auditrow--clean" : ""}">
         <span class="inventory__bin">${escapeHtml(b.bin)}</span>
         <span class="auditrow__num ${clean ? "auditrow__num--ok" : ""}" title="sum of |Shopify − RFID| across this bin's products">${
           clean ? "✓" : b.score
         }</span>
         <span class="binlist__count u-mlauto">${
           b.last_audited_at
             ? `audited ${fmtAgo(b.last_audited_at)}${b.last_audited_by ? " by " + escapeHtml(b.last_audited_by) : ""} · `
             : "never audited · "
         }${b.product_count} product(s)${
           clean
             ? " · all match"
             : ` · ${b.mismatched_count} mismatched`
         }${
           b.batch_done
             ? ""
             : b.tagged
               ? ` · NOT batch-tagged (${b.tagged_products} of ${b.product_count} have stray tags)`
               : " · not batch-tagged"
         }</span>
         <span class="auditrow__chev">${open ? "▾" : "▸"}</span>
       </div>` +
      (open
        ? `<div class="inventory__scroll inventory__scroll--inset"><table class="inventory__table">
             <thead><tr><th>Product</th><th>SKU</th><th class="num">Shopify</th><th class="num">RFID</th><th class="num">Diff</th></tr></thead>
             <tbody>${b.products
               .map(
                 (p) => `<tr>
                   <td>${escapeHtml(p.product_title || "")}${
                     p.rfid_incompatible
                       ? ' <span class="noscan-chip" title="tag won\'t scan when on box">⊘</span>'
                       : ""
                   }${
                     p.unlabelable
                       ? ' <span class="noscan-chip" title="un-labelable box: one location label, tags never counted - on-hand shown for reference">📦</span>'
                       : ""
                   }</td>
                   <td class="mono"><span class="skulink" data-sku="${escapeHtml(p.sku || "")}">${escapeHtml(p.sku || "—")}</span></td>
                   <td class="num">${p.on_hand == null ? "—" : p.on_hand}${
                     p.sold_unretired
                       ? ` <span class="bexp--note" title="Boxes sold on fulfilled orders whose tag is still on file - they raise the expected tag count until an audit marks them sold">(+${p.sold_unretired} sold)</span>`
                       : ""
                   }</td>
                   <td class="num">${p.unlabelable ? "—" : p.rfid_units}</td>
                   <td class="num${p.diff ? " bexp--off" : ""}">${
                     p.diff > 0 ? "+" + p.diff : p.diff
                   }</td>
                 </tr>`
               )
               .join("")}</tbody>
           </table></div>`
        : "");
    li.querySelector(".auditrow").addEventListener("click", () => {
      if (auditOpenBins.has(b.bin)) auditOpenBins.delete(b.bin);
      else auditOpenBins.add(b.bin);
      renderAuditBins();
    });
    li.querySelectorAll(".skulink").forEach((s) =>
      s.addEventListener("click", (ev) => {
        ev.stopPropagation();
        if (s.dataset.sku) openProductHistory(s.dataset.sku);
      })
    );
    list.append(li);
  };
  overdueRows.forEach(renderRow);
  if (freshRows.length) addHeader("Recently audited");
  freshRows.forEach(renderRow);
}

// === Bin audit: newest C72 sweep vs any bin =================================
// The Check-step story without a batch: per product, what Shopify expects,
// what's tagged here, what the sweep actually heard — plus strays and
// unknown tags. The check is read-only; the panel's two WRITES are both
// operator-confirmed — "Set to N" (Shopify on-hand, increase-only, undoable
// from History) and "Record as batch tagged" (local batch record only).
let binAudit = null; // { rep, cap } — kept so toggles re-render for free
let binAuditShowUntagged = false;
// Single-product audit (Nick, 2026-09-15): {sku, title, bin} narrows
// the whole report to one product; null = normal bin audit.
let binAuditProduct = null;

// The product row steps aside once the operator commits to a BIN audit
// (a bin typed + a sweep pulled, or the ◀ ▶ arrows); reopening the
// pane from the Audits hub brings it back.
function binAuditProdRowShow(show) {
  const row = document.getElementById("binaudit-prodrow");
  if (row) row.hidden = !show;
}

// One-tap jump from a Review bin-check card: land on the Audits tab with
// the bin loaded. If the newest C72 sweep is fresh the operator has
// clearly just walked the shelf, so the audit runs itself; a stale sweep
// would only produce a scary everything-is-missing report, so instead the
// panel says what to go do.
// The location's own latest saved audit sweep (Nick, 2026-09-28):
// every checked sweep is stamped with its bin/rack server-side, so
// opening a bin shows ITS last audit - marked stale when it's from
// another day, but still usable.
async function binLatestSweep(bin) {
  try {
    const d = await apiJson(
      `/api/bins/${encodeURIComponent(bin)}/sweeps?limit=1`
    );
    return (d.sweeps || [])[0] || null;
  } catch (err) {
    return null;
  }
}

function sweepIsStale(iso) {
  const d = tsDate(iso);
  if (Number.isNaN(d.getTime())) return false;
  const now = new Date();
  return (
    d.getFullYear() !== now.getFullYear() ||
    d.getMonth() !== now.getMonth() ||
    d.getDate() !== now.getDate()
  );
}

async function jumpToBinAudit(bin) {
  document.querySelector('.tabs__tab[data-tab="audits"]').click();
  audShowPane("binaudit");
  const binEl = document.getElementById("binaudit-bin");
  const out = document.getElementById("binaudit-report");
  binEl.value = bin;
  binEl.scrollIntoView({ behavior: "smooth", block: "center" });
  // A pinned sweep means "audit bin after bin with THIS sweep" - jump
  // straight to the check without re-asking (Nick, 2026-09-08).
  if (binAuditPinnedCap) {
    document.getElementById("binaudit-run").click();
    return;
  }
  // The bin's own last audit first - any age, honestly stale-marked.
  const own = await binLatestSweep(bin);
  if (own) {
    await runBinAudit(own);
    return;
  }
  try {
    const cap = await apiJson("/api/epc-captures/latest?pickable=1");
    const ageMs = Date.now() - tsDate(cap.created_at).getTime();
    if (Number.isFinite(ageMs) && ageMs <= 5 * 60000) {
      document.getElementById("binaudit-run").click();
      return;
    }
  } catch (err) {
    /* no sweeps anywhere - the expected list stands on its own */
  }
  await renderBinAuditExpected(bin);
}

// No sweep at all (Nick, round 11): opening a bin still shows every
// item the shelf is supposed to hold - the walk starts from the paper,
// not from a blank pane. A rack token lists each of its bins.
async function renderBinAuditExpected(bin) {
  const out = document.getElementById("binaudit-report");
  const up = (bin || "").trim().toUpperCase();
  out.innerHTML = `<p class="result">Loading what ${escapeHtml(up)} should hold…</p>`;
  // The instant-paint cache is slim (no product arrays) - pull the
  // live queue once when the per-product lists are missing.
  if (
    !auditData ||
    auditData._cached ||
    !(auditData.bins || []).some((b) => Array.isArray(b.products))
  ) {
    try {
      await loadAuditBins();
    } catch (err) {
      /* the not-found message below covers it */
    }
  }
  const isRack = !up.includes("-");
  const bins = ((auditData && auditData.bins) || []).filter((b) => {
    const n = (b.bin || "").toUpperCase();
    return n === up || (isRack && n.startsWith(up + "-"));
  });
  if (!bins.length) {
    out.innerHTML = `<p class="result">No products are mapped to
      ${escapeHtml(up)} and it has no saved sweep. Check the bin name,
      or sweep it on the C72's AUDIT tab and hit Run.</p>`;
    return;
  }
  const table = (b) => {
    const rows = (b.products || [])
      .map(
        (p) => `<tr>
          <td>${escapeHtml(p.product_title || "")}</td>
          <td class="mono"><span class="skulink" data-sku="${escapeHtml(p.sku || "")}">${escapeHtml(p.sku || "—")}</span></td>
          <td class="num">${p.on_hand == null ? "—" : p.on_hand}</td>
          <td class="num">${p.unlabelable ? "—" : p.rfid_units}</td>
          <td class="num${p.diff ? " bexp--off" : ""}">${
            p.diff > 0 ? "+" + p.diff : p.diff
          }</td>
        </tr>`
      )
      .join("");
    return `<div class="ba-expected">
      <div class="ba-expected__head"><b>${escapeHtml(b.bin)}</b>
        <span class="binlist__count">${
          b.last_audited_at
            ? `audited ${escapeHtml(fmtAgo(b.last_audited_at))}`
            : "never audited"
        } · ${b.product_count} product(s)${
          b.score === 0
            ? " · all match"
            : ` · ${b.mismatched_count} mismatched`
        }</span></div>
      ${
        rows
          ? `<div class="inventory__scroll inventory__scroll--inset"><table class="inventory__table">
              <thead><tr><th>Product</th><th>SKU</th><th class="num">Shopify</th><th class="num">RFID tags</th><th class="num">Diff</th></tr></thead>
              <tbody>${rows}</tbody></table></div>`
          : `<p class="result">Nothing is mapped to this bin.</p>`
      }
    </div>`;
  };
  out.innerHTML =
    `<p class="result">No sweep is saved for ${escapeHtml(up)} yet - this
      is what the shelf should hold. Sweep it on the C72's AUDIT tab,
      then hit Run to compare shelf against paper.</p>` +
    bins.map(table).join("");
  out.querySelectorAll(".skulink").forEach((s) =>
    s.addEventListener("click", () => {
      if (s.dataset.sku) openProductHistory(s.dataset.sku);
    })
  );
}

// Selected sweep (Nick, 2026-09-08; reworked 2026-09-14): pick a sweep
// once and audit bin after bin with it. The selection is a visible
// card, the ◀ ▶ arrows re-check each new bin with it automatically,
// and every (sweep, bin) result is kept on the page - stepping back to
// an already-checked bin re-renders instantly, no server round trip.
let binAuditPinnedCap = null;
const binAuditCache = new Map(); // "capId|BIN" -> report

function renderPinnedSweep() {
  // A pill in the pane's top bar (card layout 2026-09-28), created on
  // first use so the template stays clean.
  let pill = document.getElementById("binaudit-pinnedbar");
  if (!pill) {
    const top = document.querySelector("#apane-binaudit .ba-top");
    if (!top) return;
    pill = document.createElement("span");
    pill.id = "binaudit-pinnedbar";
    pill.className = "ba-sweep";
    top.append(pill);
  }
  if (!binAuditPinnedCap) {
    pill.hidden = true;
    return;
  }
  const c = binAuditPinnedCap;
  pill.hidden = false;
  pill.innerHTML =
    `\ud83d\udccc <b>#${escapeHtml(String(c.id))}</b> \u00b7 ` +
    `${c.epc_count} tag(s) \u00b7 ${escapeHtml(fmtAgo(c.created_at))} ` +
    `<button class="reset" id="binaudit-unpin" type="button"
       title="Every bin check uses this pinned sweep - unpin to go back to the newest">unpin</button>`;
  pill.querySelector("#binaudit-unpin").addEventListener("click", async () => {
    binAuditPinnedCap = null;
    renderPinnedSweep();
    document.getElementById("binaudit-run").click();
  });
}

async function runBinAudit(cap) {
  const binEl = document.getElementById("binaudit-bin");
  const out = document.getElementById("binaudit-report");
  const bin = binEl.value.trim();
  if (!bin) {
    out.innerHTML = `<p class="result result--err">Which bin? Type it first (e.g. D2-2, or a whole rack: D2).</p>`;
    binEl.focus();
    return;
  }
  // Page-local memory (Nick, 2026-09-14): a (sweep, bin) pair already
  // checked this session re-renders from the stored report instead of
  // asking the server again on every arrow press.
  binAuditProduct = null;
  binAuditProdRowShow(false);
  const key = String(cap.id) + "|" + bin.toUpperCase();
  const hit = binAuditCache.get(key);
  if (hit) {
    binAudit = { rep: hit, cap };
    binAuditShowUntagged = false;
    binAuditPinnedCap = cap;
    renderPinnedSweep();
    renderBinAudit();
    return;
  }
  out.innerHTML = `<p class="result">Checking…</p>`;
  try {
    // A single server-side sweep is named by id - the server reads its
    // EPCs itself, so bin-after-bin stepping never re-uploads
    // thousands of tags. Unions (ids joined with +) still send theirs.
    const single = /^\d+$/.test(String(cap.id));
    const rep = await postJson(
      `/api/bins/${encodeURIComponent(bin)}/check`,
      single
        ? { capture_id: parseInt(cap.id, 10) }
        : { epcs: cap.epcs }
    );
    binAuditCache.set(key, rep);
    binAudit = { rep, cap };
    binAuditShowUntagged = false;
    binAuditPinnedCap = cap;
    renderPinnedSweep();
    renderBinAudit();
  } catch (err) {
    out.innerHTML = `<p class="result result--err">${escapeHtml(err.message)}</p>`;
  }
}

// An explicit RUN always re-asks the server (a fix or mark-sold may
// have just changed the answer); only the ◀ ▶ arrows reuse the page's
// stored reports.
function binAuditCacheBust(bin) {
  const suffix = "|" + (bin || "").trim().toUpperCase();
  for (const k of [...binAuditCache.keys()]) {
    if (k.endsWith(suffix)) binAuditCache.delete(k);
  }
}

document.getElementById("binaudit-run").addEventListener("click", async () => {
  const out = document.getElementById("binaudit-report");
  const bin = document.getElementById("binaudit-bin").value.trim();
  binAuditCacheBust(bin);
  if (binAuditPinnedCap) {
    await runBinAudit(binAuditPinnedCap);
    return;
  }
  out.innerHTML = `<p class="result">Looking for ${escapeHtml(bin)}'s own sweep…</p>`;
  try {
    // This location's saved audit first; the global newest only when
    // the bin has no history of its own.
    const own = bin ? await binLatestSweep(bin) : null;
    const cap = own || (await apiJson("/api/epc-captures/latest?pickable=1"));
    await runBinAudit(cap);
  } catch (err) {
    if (bin) {
      // Nothing swept anywhere yet - show the expected list instead
      // of a dead end (round 11).
      await renderBinAuditExpected(bin);
      return;
    }
    out.innerHTML = `<p class="result result--err">${escapeHtml(err.message)}</p>`;
  }
});

// ---- single-product audit (Nick, 2026-09-15) ------------------------
// Same sweep, one product: look it up, check its HOME bin with the
// product force-included (skus extra), and render just its row - plus
// its ghosts, open-box prompts and never-paired labels.
async function runProductAudit(cap) {
  const codeEl = document.getElementById("binaudit-code");
  const out = document.getElementById("binaudit-report");
  const code = codeEl.value.trim();
  if (!code) {
    out.innerHTML = `<p class="result result--err">Which product? Type or scan its barcode or SKU first.</p>`;
    codeEl.focus();
    return;
  }
  out.innerHTML = `<p class="result">Looking up ${escapeHtml(code)}…</p>`;
  let p;
  try {
    p = await apiJson(
      `/api/products/by-barcode/${encodeURIComponent(code)}`
    );
  } catch (err) {
    out.innerHTML = `<p class="result result--err">No product found for ${escapeHtml(code)} (${escapeHtml(err.message)}).</p>`;
    return;
  }
  const sku = p.sku || code;
  const bin =
    p.bin_location && p.bin_location !== "No bin assigned"
      ? p.bin_location
      : "";
  if (!bin) {
    out.innerHTML = `<p class="result result--err">${escapeHtml(
      p.product_title || sku
    )} has no bin on file - a shelf check needs one. Set its bin from the product panel first.</p>`;
    return;
  }
  out.innerHTML = `<p class="result">Checking ${escapeHtml(sku)} at ${escapeHtml(bin)}…</p>`;
  try {
    const single = /^\d+$/.test(String(cap.id));
    const body = single
      ? { capture_id: parseInt(cap.id, 10) }
      : { epcs: cap.epcs };
    // Force-include the product even when the bin map misses it.
    body.skus = [sku];
    const rep = await postJson(
      `/api/bins/${encodeURIComponent(bin)}/check`,
      body
    );
    binAudit = { rep, cap };
    // The product must render even with zero tags in this bin.
    binAuditShowUntagged = true;
    binAuditProduct = { sku, title: p.product_title || sku, bin };
    binAuditPinnedCap = cap;
    renderPinnedSweep();
    renderBinAudit();
  } catch (err) {
    out.innerHTML = `<p class="result result--err">${escapeHtml(err.message)}</p>`;
  }
}

document
  .getElementById("binaudit-prodrun")
  .addEventListener("click", async () => {
    const out = document.getElementById("binaudit-report");
    if (binAuditPinnedCap) {
      await runProductAudit(binAuditPinnedCap);
      return;
    }
    out.innerHTML = `<p class="result">Pulling the latest sweep…</p>`;
    try {
      const cap = await apiJson("/api/epc-captures/latest?pickable=1");
      await runProductAudit(cap);
    } catch (err) {
      out.innerHTML = `<p class="result result--err">${escapeHtml(err.message)}</p>`;
    }
  });
document.getElementById("binaudit-code").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    document.getElementById("binaudit-prodrun").click();
  }
});

// --- Recent-sweep picker (Nick, 2026-09-01; card rework 2026-09-14;
// pages 2026-09-14): a good sweep shouldn't be lost because a newer
// one landed. Each row is a card - USE selects it for bin-after-bin
// checking, the tick boxes still combine several into one union
// check, and WRITE OFF dismisses the sweep's unpaired stickers from
// the locate list. Ten sweeps per page with the usual page buttons;
// "retire sold" moved to its own Audit packed orders pane.
const SWEEP_PAGE = 10;

// The standard windowed pager: ← [X-2] [X-1] [X] [X+1] [X+2] →, ends
// greyed, current page inert.
function sweepPagerHtml(page, total) {
  const pages = Math.max(1, Math.ceil((total || 0) / SWEEP_PAGE));
  if (pages <= 1) return "";
  const nums = [];
  for (
    let n = Math.max(1, page - 2);
    n <= Math.min(pages, page + 2);
    n++
  ) {
    nums.push(n);
  }
  return `<div class="ba-pager">
    <button class="reset ba-pager__btn" type="button"
      data-page="${page - 1}"${page <= 1 ? " disabled" : ""}
      title="Newer sweeps">←</button>
    ${nums
      .map(
        // The current page is inert but NOT greyed (Nick, 2026-09-14):
        // it keeps its accent so you can see where you are - the click
        // guard, not a disabled attribute, is what makes it a no-op.
        (n) => `<button class="reset ba-pager__btn${
          n === page ? " ba-pager__btn--cur" : ""
        }" type="button" data-page="${n}">${n}</button>`
      )
      .join("")}
    <button class="reset ba-pager__btn" type="button"
      data-page="${page + 1}"${page >= pages ? " disabled" : ""}
      title="Older sweeps">→</button>
  </div>`;
}

let sweepShown = SWEEP_PAGE;

function swUpdateFoot() {
  const box = document.getElementById("binaudit-sweeps");
  const ticked = [...box.querySelectorAll(".sw-row input:checked")];
  const tags = ticked.reduce(
    (a2, el) =>
      a2 + (parseInt(el.closest(".sw-row").dataset.n, 10) || 0),
    0
  );
  const lab = box.querySelector("#sw-ticked");
  if (lab) {
    lab.textContent = ticked.length
      ? `${ticked.length} ticked \u00b7 ${tags} tags combined`
      : "tick sweeps to combine them into one check";
  }
  const run = box.querySelector("#binaudit-runpicked");
  if (run) {
    run.disabled = !ticked.length;
    const bin = document.getElementById("binaudit-bin").value.trim();
    run.textContent = `Check ${bin || "\u2026"} with ${
      ticked.length || "ticked"
    } sweep(s)`;
  }
}

async function loadBinauditSweeps() {
  const box = document.getElementById("binaudit-sweeps");
  box.innerHTML = `<div class="sw-foot">Loading recent sweeps\u2026</div>`;
  try {
    const body = await apiJson(
      `/api/epc-captures?pickable=1&limit=${sweepShown}&offset=0`
    );
    if (!body.captures.length) {
      box.innerHTML = `<div class="sw-foot">No sweeps received yet - C72 SWEEP tab \u2192 SEND.</div>`;
      return;
    }
    const selId = binAuditPinnedCap ? String(binAuditPinnedCap.id) : null;
    const rows = body.captures
      .map((c) => {
        const ageMin = Math.max(
          0,
          Math.round((Date.now() - tsDate(c.created_at).getTime()) / 60000)
        );
        const sel = String(c.id) === selId;
        return `<div class="sw-row${sel ? " sw-row--sel" : ""}"
          data-cid="${c.id}" data-n="${c.epc_count}">
          <input type="checkbox" value="${c.id}"
            title="Tick several to combine them into one union check">
          <span class="sw-dot ${ageMin < 10 ? "sw-dot--fresh" : "sw-dot--old"}"
            title="${ageMin < 10 ? "fresh - under 10 minutes old" : "older sweep"}"></span>
          <span class="sw-row__id">#${c.id}</span>
          <div class="sw-row__main">${escapeHtml(c.device || "C72")} \u00b7 ${
            c.epc_count
          } tag(s)${
            c.note ? `<span class="sw-note">${escapeHtml(c.note)}</span>` : ""
          }
            <span class="sw-meta">${escapeHtml(fmtAgo(c.created_at))}${
              sel ? " \u00b7 pinned \u00b7 in use for this check" : ""
            }</span></div>
          <button class="reset sw-use" type="button"${sel ? " disabled" : ""}
            title="Pin this sweep - every bin check (and the \u25c0 \u25b6 arrows) uses it until replaced">${
              sel ? "\u2713 in use" : "Pin"
            }</button>
          <details class="sw-kebab"><summary title="More">\u22ef</summary>
            <div class="sw-menu">
              <button class="sw-writeoff" type="button"
                title="Blank-roll, broken and test stickers this sweep heard leave the unpaired locate list for good. Tags that belong to products are untouched. Undo in History.">Write off unpaired stickers\u2026</button>
            </div></details>
        </div>`;
      })
      .join("");
    const more = (body.total || 0) > body.captures.length;
    box.innerHTML = `
      <div class="sw-pop__head">Recent C72 sweeps
        <span class="sw-pop__note">newest is used automatically - pin one to walk bin after bin with it</span></div>
      ${
        binAuditPinnedCap
          ? `<div class="sw-pin">\ud83d\udccc Pinned: <b>#${binAuditPinnedCap.id}</b>
               - every bin check (and the \u25c0 \u25b6 arrows) uses this sweep
               <button class="sw-pin__unpin" type="button">unpin, use newest</button></div>`
          : ""
      }
      ${rows}
      <div class="sw-foot"><span id="sw-ticked"></span>
        ${
          more
            ? `<button class="reset sw-older" type="button">Show older \u25be</button>`
            : ""
        }
        <button class="print__btn" id="binaudit-runpicked" type="button" disabled></button>
      </div>`;
    swUpdateFoot();
  } catch (err) {
    box.innerHTML = `<div class="sw-foot">${escapeHtml(err.message)}</div>`;
  }
}

document
  .getElementById("binaudit-sweeps")
  .addEventListener("change", swUpdateFoot);

document.getElementById("binaudit-pick").addEventListener("click", async () => {
  const box = document.getElementById("binaudit-sweeps");
  if (!box.hidden) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  sweepShown = SWEEP_PAGE;
  loadBinauditSweeps();
});

document
  .getElementById("binaudit-sweeps")
  .addEventListener("click", async (e) => {
    const box = document.getElementById("binaudit-sweeps");
    const useBtn = e.target.closest(".sw-use");
    if (useBtn) {
      const id = parseInt(useBtn.closest(".sw-row").dataset.cid, 10);
      useBtn.disabled = true;
      try {
        const cap = await apiJson(`/api/epc-captures/${id}`);
        box.hidden = true;
        await runBinAudit(cap);
      } catch (err) {
        alert(err.message);
      } finally {
        useBtn.disabled = false;
      }
      return;
    }
    const older = e.target.closest(".sw-older");
    if (older) {
      sweepShown += SWEEP_PAGE;
      loadBinauditSweeps();
      return;
    }
    const unpin = e.target.closest(".sw-pin__unpin");
    if (unpin) {
      binAuditPinnedCap = null;
      renderPinnedSweep();
      loadBinauditSweeps();
      return;
    }
    const woBtn = e.target.closest(".sw-writeoff");
    if (woBtn) {
      const id = parseInt(woBtn.closest(".sw-row").dataset.cid, 10);
      if (
        !confirm(
          `Write off sweep #${id}'s unpaired stickers?\n\nEvery tag it ` +
            `heard that belongs to NOTHING leaves the unpaired locate ` +
            `list and stays ignored on future sweeps. Tags that belong ` +
            `to products are untouched. Undo lives in History.`
        )
      )
        return;
      woBtn.disabled = true;
      try {
        const res = await postJson("/api/epcs/ignore-heard", {
          capture_id: id,
          dismissed_by: operatorEl.value || null,
        });
        alert(res.message);
      } catch (err) {
        alert(err.message);
      } finally {
        woBtn.disabled = false;
      }
      return;
    }
    if (!e.target.closest("#binaudit-runpicked")) return;
    const ids = [...box.querySelectorAll("input:checked")].map((i) =>
      parseInt(i.value, 10)
    );
    if (!ids.length) {
      alert("Tick at least one sweep.");
      return;
    }
    const out = document.getElementById("binaudit-report");
    out.innerHTML = `<p class="result">Combining ${ids.length} sweep(s)…</p>`;
    try {
      const epcs = new Set();
      let newest = null;
      let oldest = null;
      for (const id of ids) {
        const cap = await apiJson(`/api/epc-captures/${id}`);
        (cap.epcs || []).forEach((x) => epcs.add(String(x).toUpperCase()));
        if (!newest || cap.id > newest.id) newest = cap;
        if (!oldest || cap.id < oldest.id) oldest = cap;
      }
      box.hidden = true;
      await runBinAudit({
        id: ids.length === 1 ? String(ids[0]) : ids.join("+"),
        device: newest.device,
        created_at: newest.created_at,
        // The union is only as fresh as its OLDEST member - the
        // stale-sweep guard judges stock writes by this (Nick,
        // 2026-09-09, the ASI676MC).
        oldest_at: oldest.created_at,
        epc_count: epcs.size,
        epcs: [...epcs],
      });
    } catch (err) {
      out.innerHTML = `<p class="result result--err">${escapeHtml(err.message)}</p>`;
    }
  });

// === Audit packed orders (Nick, 2026-09-14; reworked same day) ===========
// A packed-orders sweep isn't bound to any bin, so retiring it as sold
// is its OWN audit. Entering it re-syncs fulfilled orders, every small
// sweep (under 20 tags - packing-pile sized) is auto-checked against
// them, and the CHECK window shows the product previews before
// anything retires. Spending a sweep stamps it: it can only be
// retired ONCE, and the History event's undo un-spends it.
let packedPageNow = 1;
let packedSyncedAt = 0;
const PACKED_SWEEP_MAX_UI = 20;

async function packedSyncOrders(force) {
  // The verdicts are only as fresh as the sold ledger - entering the
  // audit runs the fulfilled-orders sync (incremental after its first
  // run of the day), throttled unless a row's ↻ forces it.
  if (!force && Date.now() - packedSyncedAt < 300000) return;
  const note = document.getElementById("packed-sync-note");
  if (note) {
    note.hidden = false;
    note.textContent = "Syncing fulfilled orders…";
  }
  try {
    await postJson("/api/orders-sync/run", {});
    packedSyncedAt = Date.now();
    if (note) note.textContent = "Fulfilled orders synced ✓";
  } catch (err) {
    if (note) note.textContent = "Order sync failed: " + err.message;
  }
  if (note) setTimeout(() => (note.hidden = true), 2500);
}

async function packedOpen() {
  document.getElementById("packed-report").innerHTML = "";
  await loadPackedSweeps(1, true);
  await packedSyncOrders(false);
  packedClassifyPage();
}

function packedRowHtml(c) {
  const info = `<span class="ba-sweeprow__main">#${c.id} · ${escapeHtml(
    c.device || "C72"
  )}${c.note ? " · " + escapeHtml(c.note) : ""}
      <span class="ba-sweeprow__meta">${
        c.epc_count
      } tag(s) · ${escapeHtml(fmtWhen(c.created_at))}</span>
    </span>`;
  // A spent sweep shows its stamp and nothing else (Nick: a sweep is
  // retired once).
  if (c.packed_retired_at) {
    return `<div class="ba-sweeprow ba-sweeprow--spent" data-cid="${c.id}">
      ${info}
      <span class="packed-tag packed-tag--spent">Swept products retired on ${escapeHtml(
        fmtWhen(c.packed_retired_at)
      )} by ${escapeHtml(c.packed_retired_by || "?")}</span>
    </div>`;
  }
  return `<div class="ba-sweeprow" data-cid="${c.id}">
    ${info}
    <span class="packed-tag packed-tag--wait" data-cid="${c.id}">checking against orders…</span>
    <button class="reset packed-sync rfbtn" type="button" data-cid="${c.id}"
      title="Re-sync fulfilled orders and re-check this sweep"><span class="rf-glyph">↻</span></button>
    <button class="reset ba-sweeprow__use packed-check" type="button" data-cid="${c.id}"
      title="Open the check window: which heard tags fulfilled orders cover, product by product - retiring is confirmed in there">CHECK AGAINST ORDERS…</button>
  </div>`;
}

async function loadPackedSweeps(page, skipClassify) {
  packedPageNow = page;
  const box = document.getElementById("packed-sweeps");
  box.innerHTML = `<p class="result">Loading recent sweeps…</p>`;
  try {
    const body = await apiJson(
      `/api/epc-captures?pickable=1&limit=${SWEEP_PAGE}&offset=${
        (page - 1) * SWEEP_PAGE
      }`
    );
    if (!body.captures.length && page > 1) {
      loadPackedSweeps(1, skipClassify);
      return;
    }
    if (!body.captures.length) {
      box.innerHTML = `<p class="result">No sweeps received yet - sweep the packed boxes on the C72 (SWEEP tab), SEND, then reopen this audit.</p>`;
      return;
    }
    box.innerHTML =
      `<p class="linkbox__text" id="packed-sync-note" hidden></p>` +
      body.captures.map(packedRowHtml).join("") +
      sweepPagerHtml(page, body.total);
    if (!skipClassify) packedClassifyPage();
  } catch (err) {
    box.innerHTML = `<p class="result result--err">${escapeHtml(err.message)}</p>`;
  }
}

function packedPaintTag(el, s) {
  if (!el) return;
  if (s.big) {
    el.textContent =
      `${PACKED_SWEEP_MAX_UI}+ tags - looks like a shelf sweep, ` +
      `not a packing pile`;
    el.className = "packed-tag packed-tag--na";
    return;
  }
  el.textContent = s.label || "?";
  el.className = "packed-tag packed-tag--" + (s.verdict || "na");
}

async function packedClassifyPage(onlyCid) {
  const box = document.getElementById("packed-sweeps");
  const ids = [
    ...box.querySelectorAll(".ba-sweeprow:not(.ba-sweeprow--spent)"),
  ]
    .map((r) => parseInt(r.dataset.cid, 10))
    .filter((n) => Number.isFinite(n))
    .filter((n) => onlyCid == null || n === onlyCid);
  if (!ids.length) return;
  try {
    const res = await postJson("/api/epcs/packed-classify", {
      capture_ids: ids,
    });
    for (const [cid, s] of Object.entries(res.sweeps || {})) {
      const el = box.querySelector(`.packed-tag[data-cid="${cid}"]`);
      if (s.retired_at) {
        // Spent since render (another terminal) - redraw the page.
        await loadPackedSweeps(packedPageNow, true);
        packedClassifyPage();
        return;
      }
      packedPaintTag(el, s);
    }
  } catch (err) {
    ids.forEach((cid) => {
      const el = box.querySelector(`.packed-tag[data-cid="${cid}"]`);
      if (el) {
        el.textContent = "check failed - ↻ to retry";
        el.className = "packed-tag packed-tag--na";
      }
    });
  }
}

// The check window: product previews + exactly what would retire,
// with the retire button INSIDE it (no plain-text alert dance).
async function packedCheckWindow(cid) {
  const operator = operatorEl.value;
  if (!operator) {
    alert("Pick who's scanning (top right) first.");
    return;
  }
  let plan;
  try {
    plan = await postJson("/api/epcs/retire-sold", {
      capture_id: cid,
      worker: operator,
      preview: true,
    });
  } catch (err) {
    document.getElementById(
      "packed-report"
    ).innerHTML = `<p class="result result--err">${escapeHtml(err.message)}</p>`;
    return;
  }
  const { wrap, box } = mlOverlay(`Sweep #${cid} vs fulfilled orders`);
  const sum = document.createElement("p");
  sum.className = "linkbox__text";
  sum.textContent =
    `${plan.label}. ${plan.heard} tag(s) heard: ${plan.owned} in the ` +
    `system, ${plan.unowned} without a label yet (expected), ` +
    `${plan.already_retired} already retired.`;
  box.appendChild(sum);
  const rows = plan.plan || [];
  if (!rows.length) {
    const none = document.createElement("p");
    none.className = "linkbox__text";
    none.textContent =
      "No tagged products in this sweep - nothing to retire.";
    box.appendChild(none);
  }
  for (const p of rows) {
    const card = document.createElement("div");
    card.className = "mlrow";
    const img = document.createElement("img");
    img.src = p.image_url || "";
    img.alt = "";
    img.className = p.image_url
      ? "mlrow__img"
      : "mlrow__img mlrow__img--none";
    card.appendChild(img);
    const col = document.createElement("div");
    col.className = "mlrow__main";
    const nm = document.createElement("div");
    nm.className = "mlrow__name";
    nm.textContent = p.product_title || p.sku || "(unknown)";
    col.appendChild(nm);
    const meta = document.createElement("div");
    meta.className = "mlrow__meta";
    meta.textContent =
      `SKU ${p.sku || "?"} · ${p.retire} of ${p.heard} heard covered ` +
      `by fulfilled sales` +
      (p.same_day
        ? " · fulfilled the same day ✓"
        : p.newest_fulfilled_at
          ? ` · newest sale ${fmtWhen(p.newest_fulfilled_at)}`
          : " · no unretired sale on file") +
      (p.skipped ? ` · ${p.skipped} NOT covered - stays live` : "");
    col.appendChild(meta);
    card.appendChild(col);
    if (p.skipped && !p.retire) card.classList.add("mlrow--dim");
    box.appendChild(card);
  }
  const actions = document.createElement("div");
  actions.className = "linkbox__actions linkbox__actions--end u-mt10";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "reset";
  cancel.textContent = "Cancel";
  cancel.addEventListener("click", () => wrap.remove());
  actions.appendChild(cancel);
  const go = document.createElement("button");
  go.type = "button";
  go.className = "reset packed-go";
  go.textContent = plan.retire_total
    ? `RETIRE ${plan.retire_total} TAG(S) AS SOLD`
    : "Nothing retirable";
  go.disabled = !plan.retire_total;
  go.addEventListener("click", async () => {
    go.disabled = true;
    try {
      const res = await postJson("/api/epcs/retire-sold", {
        capture_id: cid,
        worker: operator,
      });
      wrap.remove();
      document.getElementById("packed-report").innerHTML =
        `<p class="result result--ok">${escapeHtml(res.message)} ` +
        `Undo lives in History.</p>` +
        (res.plan || [])
          .filter((p) => p.retire || p.skipped)
          .map(
            (p) => `<p class="result">· ${escapeHtml(
              p.sku || p.product_title || "?"
            )}: ${p.retire} retired sold${
              p.skipped
                ? `, ${p.skipped} stayed live (no covering sale yet)`
                : ""
            }</p>`
          )
          .join("");
      await loadPackedSweeps(packedPageNow, true);
      packedClassifyPage();
    } catch (err) {
      go.disabled = false;
      alert(err.message);
    }
  });
  actions.appendChild(go);
  box.appendChild(actions);
}

document
  .getElementById("packed-sweeps")
  .addEventListener("click", async (e) => {
    const pgBtn = e.target.closest(".ba-pager__btn");
    if (pgBtn) {
      if (
        pgBtn.disabled ||
        pgBtn.classList.contains("ba-pager__btn--cur")
      )
        return;
      loadPackedSweeps(parseInt(pgBtn.dataset.page, 10) || 1);
      return;
    }
    const syncBtn = e.target.closest(".packed-sync");
    if (syncBtn) {
      const cid = parseInt(syncBtn.dataset.cid, 10);
      spinRefresh(syncBtn, async () => {
        await packedSyncOrders(true);
        await packedClassifyPage(cid);
      });
      return;
    }
    const ckBtn = e.target.closest(".packed-check");
    if (ckBtn) {
      packedCheckWindow(parseInt(ckBtn.dataset.cid, 10));
      return;
    }
  });

// The rfbtn family's spinner variant (Nick, 2026-09-14): same class
// look, NO estimated-time text - the glyph just spins until done.
async function spinRefresh(btn, run) {
  if (btn.dataset.rfRunning) return;
  btn.dataset.rfRunning = "1";
  btn.classList.add("rfbtn--spin");
  try {
    await run();
  } finally {
    btn.classList.remove("rfbtn--spin");
    delete btn.dataset.rfRunning;
  }
}



// --- Bin arrows (Nick, 2026-09-01): several bins in a row is the normal
// walk, so ◀ ▶ step the picker through every known bin in natural order
// (E1-1 … F2-1 … F10-1). With a RACK typed (no dash), they step racks.
let binNamesCache = null;
async function binNames() {
  if (binNamesCache) return binNamesCache;
  const body = await apiJson("/api/bins/names");
  binNamesCache = body.bins || [];
  return binNamesCache;
}

async function binAuditStep(dir) {
  const binEl = document.getElementById("binaudit-bin");
  try {
    const bins = await binNames();
    if (!bins.length) return;
    const cur = binEl.value.trim().toUpperCase();
    let list = bins;
    if (cur && !cur.includes("-")) {
      // Rack mode: distinct rack prefixes, same order as the bins.
      list = [...new Set(bins.map((b) => b.split("-")[0].toUpperCase()))];
    }
    let idx = list.findIndex((b) => b.toUpperCase() === cur);
    if (idx < 0) {
      // Unknown or empty: start at the ends so the first press lands
      // on the first (▶) or last (◀) real bin.
      idx = dir > 0 ? -1 : list.length;
    }
    idx = (idx + dir + list.length) % list.length;
    binEl.value = list[idx];
    // With a sweep selected the arrows ARE the audit walk (Nick,
    // 2026-09-14): stepping checks the new bin immediately - from the
    // page's stored report when this pair was already checked.
    if (binAuditPinnedCap) {
      await runBinAudit(binAuditPinnedCap);
    }
  } catch (err) {
    /* the arrows are a convenience - typing still works */
  }
}

document
  .getElementById("binaudit-prev")
  .addEventListener("click", () => binAuditStep(-1));
document
  .getElementById("binaudit-next")
  .addEventListener("click", () => binAuditStep(1));

// The pickup orders backing a "ready for pickup" explanation, named so
// the operator can check the desk (capped - the chip stays a chip).
function binAuditPickupNote(r) {
  const names = (r.pickup_orders || []).slice(0, 3);
  return names.length ? ` (${names.join(", ")})` : "";
}

// One audit item scored for display: its warning chips and whether it
// counts as untagged. Shared by the full render AND the single-row
// refresh (Nick, 2026-09-14: a write must not repaint the page).
// Verdict flags with their RECOMMENDED ACTION attached (approved
// preview, 2026-09-28): every problem line carries the fix that
// resolves it, vertically stacked. Tone ranks the card red -> yellow
// -> green like everywhere else.
function binAuditScoreRow(r) {
  const flags = [];
  const silent = r.tags_here - r.detected;
  const sold = r.sold_unretired || 0;
  const unav = r.unavailable || 0;
  const pickup = r.pickup_pending || 0;
  const exp = r.expected_qty != null ? r.expected_qty + unav : null;
  const det = r.detected_units;
  const skuA = escapeHtml(r.sku || "");
  const silEpcs = r.silent_epcs || [];
  const untagged = r.tags_here === 0 && r.detected === 0;
  let tone = "ok";
  const bump = (t) => {
    if (t === "bad" || tone === "bad") tone = "bad";
    else if (t === "warn") tone = "warn";
  };
  const flag = (cls, txt, acts, hint) =>
    flags.push({ cls, txt, acts: acts || "", hint: hint || "" });
  const locateBtn = (epcs, label) =>
    r.sku
      ? `<button class="reset binaudit-locate" type="button"
           data-sku="${skuA}" data-title="${escapeHtml(r.product_title || "")}"
           data-epcs="${escapeHtml(epcs.join(","))}"
           title="Queue the silent tag(s) on the C72 locate list - hunt them before deciding they're gone">${label || "Locate"}</button>`
      : "";
  const soldBtn = (epcs, label) =>
    r.sku
      ? `<button class="ba-act binaudit-marksold" type="button"
           data-sku="${skuA}" data-epcs="${escapeHtml(epcs.join(","))}"
           title="These boxes shipped on fulfilled orders - remove their tag record(s) and retire the sale(s) in the ledger. History-logged; Shopify untouched.">${label}</button>`
      : "";

  if (r.rfid_incompatible) {
    flag("na", "\u2298 won't scan on box - count by hand");
  }
  if (silent > 0 && !r.rfid_incompatible) {
    const cleanGhostCase =
      silent > sold &&
      r.expected_qty != null &&
      det === r.expected_qty &&
      r.detected > 0 &&
      silEpcs.length > 0;
    if (sold > 0 && silent <= sold && r.sales_agree !== false) {
      flag(
        "ok",
        `<b>Sales agree.</b> ${silent} silent, ${sold} sold since the ` +
          `last audit - the shipped box${silent === 1 ? "" : "es"} ` +
          `explain the silence.`,
        soldBtn(silEpcs, `\u2713 Mark ${silent} sold`)
      );
      bump("warn");
    } else if (sold > 0 && silent <= sold) {
      flag(
        "warn",
        `<b>${silent} silent, ${sold} sold - times don't line up.</b> ` +
          `Might be a missing, misplaced or mislabeled product.`,
        soldBtn(silEpcs, `Mark ${silent} sold`) + locateBtn(silEpcs),
        "A silent tag was heard AFTER the sale that would explain it."
      );
      bump("warn");
    } else if (sold > 0 && silent - sold <= pickup) {
      flag(
        "ok",
        `<b>${silent} silent.</b> ${sold} sold + ` +
          `${silent - sold} ready for pickup cover it${binAuditPickupNote(r)}.`
      );
    } else if (sold === 0 && silent <= unav) {
      flag(
        "ok",
        `<b>${silent} silent.</b> Likely the set-aside/unavailable ` +
          `unit${silent === 1 ? "" : "s"} - expected, not a fault.`
      );
    } else if (sold === 0 && silent <= unav + pickup && pickup > 0) {
      flag(
        "ok",
        `<b>${silent} silent.</b> Ready for pickup, not collected ` +
          `yet${binAuditPickupNote(r)}.`
      );
    } else if (cleanGhostCase) {
      flag(
        "warn",
        `<b>Shelf reads exactly right, but ${silent} silent record(s) ` +
          `linger.</b> Usually stickers replaced without unlinking.`,
        `<button class="ba-act binaudit-cleanghosts" type="button"
           data-sku="${skuA}" data-epcs="${escapeHtml(silEpcs.join(","))}"
           title="Recorded sales cover the oldest ones (presumed sold); the rest retire as replaced. History-logged, each restorable; Shopify untouched.">Clean up ${silent} ghost tag(s)\u2026</button>`
      );
      bump("warn");
    } else {
      const unexplained = silEpcs.slice(sold);
      flag(
        "bad",
        `<b>${silent} silent - ${
          sold ? `only ${sold} sold` : "no sales explain it"
        }.</b> Might be a missing, misplaced or mislabeled product.`,
        (r.sku && unexplained.length
          ? `<button class="ba-act binaudit-unpairprint" type="button"
               data-sku="${skuA}" data-epcs="${escapeHtml(unexplained.join(","))}"
               title="Removes the silent tag record(s) AND queues the same number of replacement labels to pair - one step. Pairing never changes Shopify on-hand.">\u21bb Unpair + print replacement</button>`
          : "") + locateBtn(silEpcs),
        "Check the shelf first - re-sweep behind the boxes before " +
          "deciding the sticker is gone."
      );
      bump("bad");
    }
  }
  if ((r.ghosts || []).length) {
    const gEpcs = r.ghosts.map((g) => g.epc);
    flag(
      "warn",
      `<b>${r.ghosts.length} retired tag(s) answered - box still ` +
        `here.</b> Marked sold, but it never left.`,
      `<button class="ba-act binaudit-unretire" type="button"
         data-sku="${skuA}" data-epcs="${escapeHtml(gEpcs.join(","))}"
         title="Makes the record(s) live again - the box never left. History-logged.">Un-retire ${r.ghosts.length} tag(s)</button>`
    );
    bump("warn");
  }
  if (r.finds_open > 0) {
    flag(
      "warn",
      `${r.finds_open} tagless box(es) scanned on a walk - labels ` +
        `not printed yet (the C72 banner prints them).`
    );
    bump("warn");
  }
  if (r.finds_printed > 0) {
    flag("warn", `${r.finds_printed} label(s) printed, not yet paired.`);
    bump("warn");
  }
  // The shelf carries more stock than tag records: some boxes never
  // got a sticker. Print exactly the missing labels - the neutral
  // path (the W9177 lesson: pairing never moves on-hand).
  if (exp != null && !untagged && r.sku && r.units_here < exp) {
    const kMiss = exp - r.units_here;
    flag(
      "warn",
      `<b>${kMiss} box${kMiss === 1 ? "" : "es"} never got a label.</b>`,
      `<button class="reset binaudit-printlabels" type="button"
         data-sku="${skuA}" data-n="${kMiss}"
         title="Queue plain labels on the warehouse printer (home bin on each). Pairing them never changes Shopify on-hand.">\ud83c\udff7 Print ${kMiss} label${kMiss === 1 ? "" : "s"}</button>`,
      "Pairing a printed label never changes Shopify on-hand."
    );
    bump("warn");
  }
  if (r.in_range === false) {
    const lo = (r.range_lo != null ? r.range_lo : r.expected_qty) + unav;
    const hi = (r.range_hi != null ? r.range_hi : r.expected_qty) + unav;
    flag(
      "warn",
      `<b>Heard ${det} unit(s) - outside the expected ` +
        `${lo === hi ? lo : `${lo}\u2013${hi}`}.</b>`,
      exp != null && det > exp && r.sku
        ? `<button class="reset binaudit-fix" type="button"
             data-sku="${skuA}" data-qty="${det}" data-exp="${exp}"
             title="The sweep physically heard ${det} unit(s) - write that count to Shopify on-hand. Confirmed, logged, undoable from History.">Set stock to ${det}</button>`
        : "",
      "Re-scan the shelf thoroughly first (behind the boxes too); " +
        "if the number is real, write it."
    );
    bump("warn");
  } else if (exp != null && det > exp && r.sku) {
    flag(
      "warn",
      `<b>Shelf physically holds ${det} - more than Shopify's ${exp}.</b>`,
      `<button class="reset binaudit-fix" type="button"
         data-sku="${skuA}" data-qty="${det}" data-exp="${exp}"
         title="Write the heard count to Shopify on-hand. Confirmed, logged, undoable from History.">Set stock to ${det}</button>`
    );
    bump("warn");
  }
  if (untagged) {
    flag("na", "no tags on file here yet - not in the RFID system");
  }
  return { r, flags, untagged, tone };
}

// One scored product as a CARD (approved preview, 2026-09-28):
// severity edge, plain numbers, flag rows with their fixes, the
// silent-tag drawer, and the on-hand stepper. data-rowsku still lets
// a write repaint just this card.
function binAuditRowHtml({ r, flags, untagged, tone }) {
  const unav = r.unavailable || 0;
  const sold = r.sold_unretired || 0;
  const exp = r.expected_qty != null ? r.expected_qty + unav : null;
  const silent = r.tags_here - r.detected;
  const edge = untagged ? "" : ` pcr--${tone}`;
  let expCell = "\u2014";
  let expTitle = "";
  if (exp != null) {
    const lo = (r.range_lo != null
      ? r.range_lo
      : Math.min(r.expected_qty, r.units_here - sold)) + unav;
    const hi = (r.range_hi != null
      ? r.range_hi
      : Math.max(r.expected_qty, r.units_here - sold)) + unav;
    expCell = lo === hi ? String(lo) : `${lo}\u2013${hi}`;
    expTitle =
      `Shopify carries ${exp}` +
      (unav ? ` (incl. ${unav} unavailable)` : "") +
      (sold ? `; ${sold} sold since the last audit` : "") +
      `. The shelf should hold somewhere in this range.`;
  }
  const tagsNote = (units, tags) =>
    units !== tags ? ` (${tags} tag${tags === 1 ? "" : "s"})` : "";
  const seenTone =
    r.in_range === false || (silent > 0 && tone === "bad")
      ? " pcr__num--bad"
      : flags.length === 0 && r.detected > 0
        ? " pcr__num--ok"
        : "";
  const flagRows = flags
    .map(
      (f) => `<div class="flagrow flagrow--${f.cls}">
        <span class="flagrow__txt">${f.txt}${
          f.hint ? `<span class="flagrow__hint">${f.hint}</span>` : ""
        }</span>${f.acts}</div>`
    )
    .join("");
  // The silent-tag drawer: each missing tag with its last hearing and
  // its own Unpair / Sold / Locate.
  const tagLines = (r.silent_tags || []).slice(0, 12)
    .map((t) => {
      const e = escapeHtml(t.epc || "");
      return `<div class="tagline">
        <span class="tagline__epc" title="${e}">\u2026${escapeHtml(
          (t.epc || "").slice(-6)
        )}</span>
        <span class="tagline__when">${
          t.last_heard_at
            ? "last heard " + escapeHtml(fmtAgo(t.last_heard_at))
            : "never heard on a sweep"
        }</span>
        <span class="tagline__spacer"></span>
        <button class="reset binaudit-unpair" type="button"
          data-sku="${escapeHtml(r.sku || "")}" data-epc="${e}"
          title="Remove this tag record - the sticker is gone or belongs to another box. History-logged as Tag Unlinked.">Unpair</button>
        <button class="reset binaudit-marksold" type="button"
          data-sku="${escapeHtml(r.sku || "")}" data-epcs="${e}"
          title="Retire this one tag as sold against a recorded sale">Sold</button>
        <button class="reset binaudit-locate" type="button"
          data-sku="${escapeHtml(r.sku || "")}"
          data-title="${escapeHtml(r.product_title || "")}" data-epcs="${e}"
          title="Hunt this exact tag on the C72">Locate</button>
      </div>`;
    })
    .join("");
  const drawer =
    tagLines && r.sku
      ? `<details class="pcr__tags"><summary>${silent} silent tag(s) \u25be</summary>
          ${tagLines}${
            (r.silent_tags || []).length > 12
              ? `<div class="tagline"><span class="tagline__when">+${
                  r.silent_tags.length - 12
                } more - work the list from the top</span></div>`
              : ""
          }</details>`
      : "";
  const stepper =
    exp != null && r.sku && !r.unlabelable
      ? `<span class="steplab">Shopify on-hand</span>
         <span class="stepper" data-sku="${escapeHtml(r.sku)}"
               data-base="${exp}" data-canlower="${r.can_lower ? 1 : 0}"
               data-sold="${sold}" data-pickup="${r.pickup_pending || 0}"
               data-epcs="${escapeHtml((r.silent_epcs || []).join(","))}">
           <button type="button" class="ba-step-dn"
             title="Count the shelf by hand, then lower to the true number - sales cover what they can, the rest writes off as shrinkage (first-tagging ban enforced server-side)">\u2212</button>
           <span class="stepper__val">${exp}</span>
           <button type="button" class="ba-step-up"
             title="Count the shelf by hand, then raise to the true number">+</button>
         </span>
         <button class="ba-act ba-apply" type="button" hidden
           title="Writes the new number to Shopify - confirmed, logged, one Undo in History">Apply</button>`
      : "";
  return `<div class="pcardrow${edge}" data-rowsku="${escapeHtml(
    (r.sku || "").toUpperCase()
  )}">
    ${
      r.image_url
        ? `<img class="pcr__img" src="${escapeHtml(r.image_url)}" alt="">`
        : `<span class="pcr__imgbox">\ud83d\udce6</span>`
    }
    <div class="pcr__main">
      <div class="pcr__title">${
        r.sku
          ? `<span class="prodopen" data-sku="${escapeHtml(r.sku)}"
               title="Open this product - label editor, RFID flag, full history">${escapeHtml(
                 r.product_title || "(unknown)"
               )}</span>`
          : escapeHtml(r.product_title || "(unknown)")
      }${
        r.variant_title ? ` (${escapeHtml(r.variant_title)})` : ""
      } <span class="pcr__sku">\u00b7 ${escapeHtml(r.sku || "\u2014")}</span></div>
      <div class="pcr__nums">
        <div class="pcr__num" title="${escapeHtml(expTitle)}"><b>${expCell}</b><span>expected</span></div>
        <div class="pcr__num" title="Units whose tag records say this bin${tagsNote(
          r.units_here, r.tags_here
        )}"><b>${r.units_here}</b><span>tagged here</span></div>
        <div class="pcr__num${seenTone}" title="Units whose tags answered this sweep${tagsNote(
          r.detected_units, r.detected
        )}"><b>${r.detected_units}</b><span>seen</span></div>
      </div>
      ${flagRows ? `<div class="pcr__flags">${flagRows}</div>` : ""}
      ${drawer}
      ${
        stepper
          ? `<div class="pcr__acts">${stepper}</div>`
          : ""
      }
    </div>
    ${
      !flags.length && !untagged
        ? `<span class="pcr__done">\u2713 all match</span>`
        : ""
    }
  </div>`;
}

// Surgical row refresh (Nick, 2026-09-14): after a per-product write
// the page STAYS - the check re-runs quietly and only the changed
// product's row repaints when the answer returns. Scroll position,
// open panels and the rest of the report never move.
async function binAuditRefreshRow(sku) {
  if (!binAudit || !sku) {
    document.getElementById("binaudit-run").click();
    return;
  }
  const { rep, cap } = binAudit;
  const bin = rep.bin;
  try {
    const single = /^\d+$/.test(String(cap.id));
    const body = single
      ? { capture_id: parseInt(cap.id, 10) }
      : { epcs: cap.epcs };
    if (binAuditProduct) body.skus = [binAuditProduct.sku];
    const fresh = await postJson(
      `/api/bins/${encodeURIComponent(bin)}/check`, body
    );
    binAudit = { rep: fresh, cap };
    if (!binAuditProduct) {
      binAuditCache.set(
        String(cap.id) + "|" + bin.toUpperCase(), fresh);
    }
    const up = sku.toUpperCase();
    const card = document.querySelector(
      `#binaudit-report [data-rowsku="${CSS.escape(up)}"]`
    );
    if (!card) {
      renderBinAudit();
      return;
    }
    const item = (fresh.items || []).find(
      (r) => (r.sku || "").toUpperCase() === up
    );
    const inStory =
      item &&
      ((item.expected_qty || 0) > 0 ||
        item.tags_here > 0 ||
        item.detected > 0);
    if (!inStory) {
      card.remove();
      return;
    }
    const scored = binAuditScoreRow(item);
    if (scored.untagged && !binAuditShowUntagged) {
      card.remove();
      return;
    }
    card.outerHTML = binAuditRowHtml(scored);
  } catch (err) {
    // Rather a full honest repaint than a stale row.
    document.getElementById("binaudit-run").click();
  }
}

function renderBinAudit() {
  const out = document.getElementById("binaudit-report");
  if (!binAudit) return;
  const { rep, cap } = binAudit;
  // Product mode: the whole report narrows to the one audited SKU -
  // bin-level noise (other products, strays, batch-tagged state) is
  // someone else's story.
  const pm = binAuditProduct;
  const pmSku = pm ? pm.sku.toUpperCase() : null;

  const rank = { bad: 0, warn: 1, ok: 2 };
  const scored = rep.items
    .filter((r) =>
      pm
        ? (r.sku || "").toUpperCase() === pmSku
        : (r.expected_qty || 0) > 0 || r.tags_here > 0 || r.detected > 0
    )
    .map(binAuditScoreRow)
    .sort(
      (a, b) =>
        a.untagged - b.untagged ||
        rank[a.tone] - rank[b.tone] ||
        String(a.r.product_title).localeCompare(String(b.r.product_title))
    );
  const untaggedCount = scored.filter((s) => s.untagged).length;
  const shown = binAuditShowUntagged
    ? scored
    : scored.filter((s) => !s.untagged);
  const flaggedCount = shown.filter(
    (s) => !s.untagged && s.tone !== "ok"
  ).length;
  const okCount = shown.filter(
    (s) => !s.untagged && s.tone === "ok"
  ).length;

  const cells = shown.map(binAuditRowHtml).join("");
  const strays = (pm ? [] : rep.foreign)
    .map(
      (f) =>
        `<li>${
          f.sku
            ? `<span class="prodopen" data-sku="${escapeHtml(f.sku)}">${escapeHtml(f.product_title || "?")}</span>`
            : escapeHtml(f.product_title || "?")
        } <span class="mono">${escapeHtml(f.sku || "")}</span>${
          f.bin_location ? " \u00b7 recorded at " + escapeHtml(f.bin_location) : ""
        } <span class="mono">${escapeHtml(f.epc)}</span> - neighbour noise on a big antenna</li>`
    )
    .join("");
  const unknowns = (pm ? [] : rep.unknown_epcs)
    .map((e) => `<li>Unknown tag <span class="mono">${escapeHtml(e)}</span> - not linked to any product</li>`)
    .join("");
  const owedLabels = (rep.printed_labels_heard || [])
    .filter((l) => !pm || (l.sku || "").toUpperCase() === pmSku)
    .map(
      (l) =>
        `<li>\u26a0 Printed label for ${
          l.sku
            ? `<span class="prodopen" data-sku="${escapeHtml(l.sku)}">${escapeHtml(l.product_title || l.sku)}</span>`
            : escapeHtml(l.product_title || "?")
        } answered but was never PAIRED - pair it, then sweep again.
        <span class="mono">${escapeHtml(l.epc)}</span> (job #${l.job_id})</li>`
    )
    .join("");
  const strayGhosts = (rep.stray_ghosts || [])
    .filter((g) => !pm || (g.sku || "").toUpperCase() === pmSku)
    .map(
      (g) =>
        `<li>Retired tag (${escapeHtml(g.kind)}) of ${
          g.sku
            ? `<span class="prodopen" data-sku="${escapeHtml(g.sku)}">${escapeHtml(g.product_title || g.sku)}</span>`
            : escapeHtml(g.product_title || "?")
        } answered here <span class="mono">${escapeHtml(g.epc)}</span></li>`
    )
    .join("");
  // Open-box return prompts (Nick, 2026-09-15): a heard presumed-sold
  // tag with a return watch on file asks the real question here.
  const obxGhosts = [];
  (rep.items || []).forEach((it) => {
    if (pm && (it.sku || "").toUpperCase() !== pmSku) return;
    (it.ghosts || []).forEach((g) => {
      if (g.openbox_return_id) obxGhosts.push(g);
    });
  });
  (rep.stray_ghosts || []).forEach((g) => {
    if (pm && (g.sku || "").toUpperCase() !== pmSku) return;
    if (g.openbox_return_id) obxGhosts.push(g);
  });
  const obxBlock = obxGhosts.length
    ? `<div class="obxprompt u-mt14">
         <div class="recent__head"><h2>Open-box return? (${obxGhosts.length})</h2></div>
         ${obxGhosts
           .map(
             (g) => `<div class="obxprompt__row">
               <div>Tag <span class="mono">\u2026${escapeHtml((g.epc || "").slice(-6))}</span> of
                 <span class="prodopen" data-sku="${escapeHtml(g.sku || "")}">${escapeHtml(g.product_title || g.sku || "?")}</span>
                 was retired as SOLD, and an open-box return of it is on file.
                 Is the box this tag is on the open-box unit (${escapeHtml(g.openbox_sku || "")})?</div>
               <div class="obxprompt__btns">
                 <button class="reset binaudit-obx" data-answer="yes" data-ret="${g.openbox_return_id}" data-epc="${escapeHtml(g.epc)}" type="button">Yes - it's the open-box unit</button>
                 <button class="reset binaudit-obx" data-answer="no" data-ret="${g.openbox_return_id}" data-epc="${escapeHtml(g.epc)}" type="button">No - stray sticker</button>
               </div>
             </div>`
           )
           .join("")}
       </div>`
    : "";

  const extrasCount = pm
    ? (rep.stray_ghosts || []).filter(
        (g) => (g.sku || "").toUpperCase() === pmSku
      ).length +
      (rep.printed_labels_heard || []).filter(
        (l) => (l.sku || "").toUpperCase() === pmSku
      ).length
    : rep.foreign.length +
      rep.unknown_epcs.length +
      (rep.stray_ghosts || []).length +
      (rep.printed_labels_heard || []).length;
  const extras =
    strays || unknowns || strayGhosts || owedLabels
      ? `<details class="ba-extra"><summary>${
          pm ? "Also heard of this product" : "Also heard on this shelf"
        } (${extrasCount})</summary>
         <ul>${owedLabels}${strayGhosts}${strays}${unknowns}</ul></details>`
      : "";

  out.innerHTML = `
    <div class="ba-sweep u-mb10">checked against <b>sweep #${escapeHtml(
      String(cap.id)
    )}</b> \u00b7 ${escapeHtml(cap.device || "C72")} \u00b7 ${
      cap.epc_count
    } tag(s) \u00b7 ${escapeHtml(fmtWhen(cap.created_at))}${
      sweepIsStale(cap.created_at)
        ? ` <span class="ba-stale">\u26a0 from another day - still usable; re-sweep for fresh truth</span>`
        : ""
    }${
      pm
        ? ` \u00b7 checked for <b>${escapeHtml(pm.title)}</b> at ${escapeHtml(rep.bin)}`
        : rep.rack
          ? ` \u00b7 whole rack: ${(rep.bins_covered || [])
              .map((b) => escapeHtml(b.toUpperCase()))
              .join(", ")}`
          : ""
    }</div>
    <div class="ba-summary">
      <div class="ba-stat"><b>${shown.length}</b><span>products</span></div>
      <div class="ba-stat ba-stat--ok"><b>${okCount}</b><span>all match</span></div>
      <div class="ba-stat${flaggedCount ? " ba-stat--bad" : ""}"><b>${flaggedCount}</b><span>flagged</span></div>
      <div class="ba-stat"><b>${extrasCount}</b><span>strays heard</span></div>
      <span class="ba-summary__spacer"></span>
      ${
        pm
          ? ""
          : `<button class="print__btn" id="binaudit-complete" type="button"
               title="Sign this audit off: the sweep's per-product heard counts become this bin's new anchor - the audit queue and the expected ranges walk forward from here.">\u2713 Audit complete - record it</button>`
      }
    </div>
    ${
      pm
        ? ""
        : rep.rack
        ? `<p class="result">${
            (rep.bins_batch_done || []).length ===
            (rep.bins_covered || []).length
              ? "\u2713 Every bin on this rack is recorded as batch tagged."
              : `Batch tagged so far: ${
                  (rep.bins_batch_done || [])
                    .map((b) => escapeHtml(b.toUpperCase()))
                    .join(", ") || "none"
                } - the rest of the rack doesn't count as tagged yet.`
          }</p>`
        : ""
    }
    ${
      !pm && (rep.covered_bundles || []).length
        ? `<p class="result">&#128230; ${rep.covered_bundles.length} bundle
           listing(s) here are covered by their components - no tags of
           their own to hear: ${rep.covered_bundles
             .map(
               (cb) =>
                 `<b>${escapeHtml(cb.sku)}</b> (= ${(cb.contents || [])
                   .map((c) => `${c.qty}× ${escapeHtml(c.component_sku)}`)
                   .join(" + ")})`
             )
             .join(", ")}.</p>`
        : ""
    }
    ${
      pm || rep.rack
        ? ""
        : rep.batch_done
        ? `<p class="result result--ok">✓ Recorded as batch tagged -
           batch #${rep.batch_done_id}${
             rep.batch_done_at
               ? `, finished ${escapeHtml(fmtWhen(rep.batch_done_at))}`
               : ""
           }.</p>`
        : `<p class="result result--warn-soft">This bin has no completed batch -
           it doesn't count as tagged. If the shelf really is fully tagged (a
           batch abandoned after every tag was paired), you can record it:
           <button class="reset" id="binaudit-marktagged" type="button"
             title="Records the bin as batch tagged from the tags already on file - tags nothing, prints nothing, writes nothing to Shopify">Record ${escapeHtml(rep.bin)} as batch tagged\u2026</button></p>`
    }
    ${
      cells ||
      `<p class="result">${
        pm
          ? `${escapeHtml(pm.sku)} did not come back in this check.`
          : untaggedCount
          ? "Nothing on this shelf is tagged yet."
          : `Nothing expected or tagged in ${escapeHtml(rep.bin)}.`
      }</p>`
    }
    ${
      !pm && untaggedCount
        ? `<div class="linkbox__actions u-mt8">
             <button class="reset" id="binaudit-toggle" type="button">${
               binAuditShowUntagged ? "Hide" : "Show"
             } ${untaggedCount} product(s) with no tags here</button>
           </div>`
        : ""
    }
    ${obxBlock}
    ${extras ||
      (pm
        ? `<p class="result">Run a bin audit of ${escapeHtml(rep.bin)} for the shelf's full story - strays and unknown tags included.</p>`
        : `<p class="result">No stray or unknown tags in the sweep.</p>`)}`;
}

// Full quiet re-check + repaint, for answers that change more than one
// row (the open-box prompts touch strays AND a product's ghost list).
async function binAuditRefetchAll() {
  if (!binAudit) return;
  const { rep, cap } = binAudit;
  const single = /^\d+$/.test(String(cap.id));
  const body = single
    ? { capture_id: parseInt(cap.id, 10) }
    : { epcs: cap.epcs };
  // Product mode keeps its product force-included across re-checks.
  if (binAuditProduct) body.skus = [binAuditProduct.sku];
  const fresh = await postJson(
    `/api/bins/${encodeURIComponent(rep.bin)}/check`,
    body
  );
  binAudit = { rep: fresh, cap };
  if (!binAuditProduct) {
    binAuditCache.set(
      String(cap.id) + "|" + rep.bin.toUpperCase(), fresh);
  }
  renderBinAudit();
}

// Queue N plain labels for a product (the neutral path - pairing a
// printed label never changes Shopify on-hand). Product resolved by
// SKU through the same rescue chain the station uses.
async function binAuditQueueLabels(sku, n) {
  const p = await apiJson(
    `/api/products/by-barcode/${encodeURIComponent(sku)}`
  );
  await postJson("/api/print-jobs", {
    quantity: n,
    shopify_variant_id: p.shopify_variant_id,
    shopify_product_id: p.shopify_product_id || null,
    product_title: p.product_title || sku,
    variant_title: p.variant_title || null,
    sku: p.sku || sku,
    barcode: p.barcode || null,
    bin_location:
      p.bin_location && p.bin_location !== "No bin assigned"
        ? p.bin_location
        : (binAudit && binAudit.rep.bin) || null,
    requested_by: operatorEl.value || null,
  });
}

// One delegated handler for the whole panel — the report re-renders on
// every toggle and every write, so per-element listeners would go stale.
document
  .getElementById("binaudit-report")
  .addEventListener("click", async (e) => {
    const open = e.target.closest(".prodopen, .skulink");
    if (open && open.dataset.sku) {
      openProductHistory(open.dataset.sku);
      return;
    }
    if (e.target.closest("#binaudit-toggle")) {
      binAuditShowUntagged = !binAuditShowUntagged;
      renderBinAudit();
      return;
    }
    // Increase-only on-hand write, same contract as the verify table's
    // button: confirmed, logged, undoable from History.
    const fix = e.target.closest(".binaudit-fix");
    if (fix) {
      const sku = fix.dataset.sku;
      const qty = parseInt(fix.dataset.qty, 10);
      if (
        !confirm(
          `Set Shopify ON-HAND for ${sku} to ${qty}?\n\n` +
            `Shopify expects ${fix.dataset.exp}; this bin holds ${qty} ` +
            `tagged unit(s).\n\nThis WRITES the number to Shopify. Undo ` +
            `stays available in History.`
        )
      )
        return;
      fix.disabled = true;
      try {
        const res = await postJson("/api/onhand-updates", {
          sku,
          new_qty: qty,
          changed_by: operatorEl.value || null,
          confirmed: true,
          // The count came from THIS audit's sweep - the server
          // refuses if Shopify stock moved after it (stale-sweep
          // guard, Nick 2026-09-09).
          sweep_at:
            (binAudit &&
              binAudit.cap &&
              (binAudit.cap.oldest_at || binAudit.cap.created_at)) ||
            null,
        });
        alert(res.message);
        // Only this product's row re-checks and repaints - the page,
        // scroll and the rest of the report stay put (Nick, 2026-09-14).
        await binAuditRefreshRow(sku);
      } catch (err) {
        alert(err.message);
        fix.disabled = false;
      }
      return;
    }
    // Lowering from an audit (Nick, 2026-09-15): the shelf answered
    // with fewer units than Shopify carries. Retires the silent tags
    // presumed-sold, consumes what recorded sales cover, writes the
    // rest off as shrinkage - allowed past the sales only for products
    // that completed a batch tagging before (the endpoint enforces it).
    const low = e.target.closest(".binaudit-lower");
    if (low) {
      const sku = low.dataset.sku;
      const qty = parseInt(low.dataset.qty, 10);
      const drop = parseInt(low.dataset.drop, 10) || 0;
      const unbacked = parseInt(low.dataset.unbacked, 10) || 0;
      const epcs = (low.dataset.epcs || "").split(",").filter(Boolean);
      const heardNone = (parseInt(low.dataset.detected, 10) || 0) === 0;
      if (
        !confirm(
          `Set Shopify ON-HAND for ${sku} DOWN to ${qty}?\n\n` +
            `This retires ${epcs.length} silent tag(s) as ` +
            `presumed-sold` +
            (unbacked
              ? ` - ⚠ ${unbacked} of the ${drop} missing unit(s) have ` +
                `NO recorded sale and are written off as shrinkage`
              : ` - recorded sales account for the missing unit(s)`) +
            `.\n\n` +
            (heardNone
              ? `⚠ NOTHING of this product answered the sweep - be ` +
                `sure this shelf was really swept before lowering.\n\n`
              : ``) +
            `One Undo in History reverses all of it.`
        )
      )
        return;
      low.disabled = true;
      try {
        const res = await postJson("/api/onhand-updates/lower", {
          sku,
          bin_name: (binAudit && binAudit.rep.bin) || "",
          new_qty: qty,
          epcs,
          changed_by: operatorEl.value || null,
          confirmed: true,
          sweep_at:
            (binAudit &&
              binAudit.cap &&
              (binAudit.cap.oldest_at || binAudit.cap.created_at)) ||
            null,
        });
        alert(res.message);
        await binAuditRefreshRow(sku);
      } catch (err) {
        alert(err.message);
        low.disabled = false;
      }
      return;
    }
    // Sold-tag retirement: the sweep missed exactly the boxes the sold
    // ledger says shipped. Local records only — Shopify's on-hand
    // already dropped when those orders fulfilled.
    const soldBtn = e.target.closest(".binaudit-marksold");
    if (soldBtn) {
      const sku = soldBtn.dataset.sku;
      const epcs = (soldBtn.dataset.epcs || "").split(",").filter(Boolean);
      const operator = operatorEl.value;
      if (!operator) {
        alert("Pick who's scanning (top right) first.");
        return;
      }
      if (
        !confirm(
          `Mark ${epcs.length} tag(s) of ${sku} as SOLD?\n\n` +
            `The sweep didn't hear them, and fulfilled orders account ` +
            `for the missing boxes. Their tag records are removed ` +
            `(History-logged as Tag Sold) and the sales are retired in ` +
            `the ledger. Shopify is not touched.`
        )
      )
        return;
      soldBtn.disabled = true;
      try {
        const res = await postJson("/api/assignments/mark-sold", {
          sku,
          epcs,
          changed_by: operator,
        });
        alert(
          `${res.removed_tags} tag(s) marked sold - ` +
            `${res.retired_against_orders} unit(s) retired against orders.`
        );
        await binAuditRefreshRow(sku);
      } catch (err) {
        alert(err.message);
        soldBtn.disabled = false;
      }
      return;
    }
    // Silent tags -> the C72 locate list (Nick, 2026-09-28): hunt
    // before concluding. Merges with whatever the queue already holds
    // for the SKU, same as the product card's per-box Locate.
    const locBtn = e.target.closest(".binaudit-locate");
    if (locBtn) {
      const sku = locBtn.dataset.sku;
      const epcs = (locBtn.dataset.epcs || "").split(",").filter(Boolean);
      locBtn.disabled = true;
      try {
        const q = await apiJson("/api/locate-queue").catch(() => null);
        const mine = q && (q.entries || []).find(
          (x) => (x.sku || "").toUpperCase() === (sku || "").toUpperCase());
        const merged = new Set(mine ? mine.epcs || [] : []);
        epcs.forEach((x) => merged.add(x));
        const r = await postJson("/api/locate-queue", {
          sku,
          label: locBtn.dataset.title || sku,
          worker: operatorEl.value || null,
          epcs: Array.from(merged),
        });
        locBtn.textContent = r.expanded
          ? "COMPONENTS QUEUED ✓"
          : "ON THE LOCATE LIST ✓";
        if (r.message) locBtn.title = r.message;
      } catch (err) {
        alert(err.message);
        locBtn.disabled = false;
      }
      return;
    }
    // Open-box return prompt (Nick, 2026-09-15): YES adopts the old
    // tag for the -O twin (or says peel it, when a fresh open-box
    // label already paired); NO stops asking about that EPC.
    const obxAns = e.target.closest(".binaudit-obx");
    if (obxAns) {
      const yes = obxAns.dataset.answer === "yes";
      if (
        yes &&
        !confirm(
          `Confirm: the box wearing tag …${obxAns.dataset.epc.slice(-6)} ` +
            `is the open-box unit?\n\nIf its fresh open-box label is ` +
            `already on, you'll be told to peel the old sticker; ` +
            `otherwise the old tag becomes the open-box product's ` +
            `live tag (no reprint needed).`
        )
      )
        return;
      obxAns.disabled = true;
      try {
        const res = await postJson(
          `/api/openbox-returns/${obxAns.dataset.ret}/resolve`,
          {
            answer: yes ? "yes" : "no",
            epc: obxAns.dataset.epc,
            bin_location: (binAudit && binAudit.rep.bin) || null,
            resolved_by: operatorEl.value || null,
          }
        );
        alert(res.message);
        await binAuditRefetchAll();
      } catch (err) {
        alert(err.message);
        obxAns.disabled = false;
      }
      return;
    }
    // Ghost cleanup: preview the sold/replaced split first, then apply
    // (Nick, 2026-09-14 - the bracket flow, one guided click).
    const ghostBtn = e.target.closest(".binaudit-cleanghosts");
    if (ghostBtn) {
      const sku = ghostBtn.dataset.sku;
      const epcs = (ghostBtn.dataset.epcs || "").split(",").filter(Boolean);
      const operator = operatorEl.value;
      if (!operator) {
        alert("Pick who's scanning (top right) first.");
        return;
      }
      ghostBtn.disabled = true;
      try {
        const plan = await postJson("/api/assignments/cleanup-silent", {
          sku,
          epcs,
          worker: operator,
          preview: true,
        });
        const ok = confirm(
          `Clean up ${epcs.length} ghost tag(s) of ${sku}?\n\n` +
            `The sweep heard exactly what Shopify expects, so these ` +
            `silent records are leftovers (usually stickers replaced ` +
            `without unlinking the old tag).\n\n` +
            `· ${plan.presumed_sold.length} oldest record(s) retire ` +
            `PRESUMED SOLD - recorded sales cover them\n` +
            `· ${plan.replaced.length} retire as REPLACED - their box ` +
            `wears a newer sticker\n\n` +
            `History-logged, each tag restorable. Shopify untouched.`
        );
        if (!ok) {
          ghostBtn.disabled = false;
          return;
        }
        const res = await postJson("/api/assignments/cleanup-silent", {
          sku,
          epcs,
          worker: operator,
        });
        alert(res.message);
        await binAuditRefreshRow(sku);
      } catch (err) {
        alert(err.message);
        ghostBtn.disabled = false;
      }
      return;
    }
    // Unpair ONE silent tag (drawer row): the sticker is gone or on
    // another box - the record goes, History keeps the receipt.
    const unpair = e.target.closest(".binaudit-unpair");
    if (unpair) {
      const epc = unpair.dataset.epc;
      const sku = unpair.dataset.sku;
      if (
        !confirm(
          `Unpair tag \u2026${epc.slice(-6)} from ${sku}?\n\n` +
            `The tag record is removed - use this when the sticker is ` +
            `gone or belongs to another box. Shopify is not touched; ` +
            `History logs it as Tag Unlinked.`
        )
      )
        return;
      unpair.disabled = true;
      try {
        await apiJson(
          `/api/rfid-assignments/${encodeURIComponent(epc)}?by=` +
            encodeURIComponent(operatorEl.value || ""),
          { method: "DELETE" }
        );
        await binAuditRefreshRow(sku);
      } catch (err) {
        alert(err.message);
        unpair.disabled = false;
      }
      return;
    }
    // Unpair + print replacement (Nick, 2026-09-28, the approved
    // preview): one step for the "missing, misplaced or mislabeled"
    // verdict - the dead records go and the same number of fresh
    // labels queue for pairing. Pairing never moves on-hand.
    const upp = e.target.closest(".binaudit-unpairprint");
    if (upp) {
      const sku = upp.dataset.sku;
      const epcs = (upp.dataset.epcs || "").split(",").filter(Boolean);
      if (
        !confirm(
          `Unpair ${epcs.length} silent tag(s) of ${sku} and queue ` +
            `${epcs.length} replacement label(s)?\n\n` +
            `Check the shelf first - re-sweep behind the boxes. If the ` +
            `boxes really are here with dead or missing stickers, this ` +
            `removes the old record(s) and prints fresh labels to ` +
            `pair. Shopify on-hand is NOT touched; each unpair is ` +
            `History-logged.`
        )
      )
        return;
      upp.disabled = true;
      try {
        for (const epc of epcs) {
          await apiJson(
            `/api/rfid-assignments/${encodeURIComponent(epc)}?by=` +
              encodeURIComponent(operatorEl.value || ""),
            { method: "DELETE" }
          );
        }
        await binAuditQueueLabels(sku, epcs.length);
        alert(
          `${epcs.length} tag record(s) unpaired and ${epcs.length} ` +
            `label(s) queued on the warehouse printer.\n\nStick and ` +
            `pair them (Scan station, or the C72's pair mode) - the ` +
            `count stays put.`
        );
        await binAuditRefreshRow(sku);
      } catch (err) {
        alert(err.message);
        upp.disabled = false;
      }
      return;
    }
    // Plain "print N labels" (the W9177 case): boxes with more stock
    // than tag records get stickers with NO receiving side effects.
    const plab = e.target.closest(".binaudit-printlabels");
    if (plab) {
      const sku = plab.dataset.sku;
      const def = parseInt(plab.dataset.n, 10) || 1;
      const raw = prompt(
        `How many labels for ${sku}?\n\nThey queue on the warehouse ` +
          `printer (home bin on each). Pairing them never changes ` +
          `Shopify on-hand.`,
        String(def)
      );
      if (raw === null) return;
      const n = parseInt(raw, 10);
      if (!Number.isFinite(n) || n < 1 || n > 100) {
        alert("Type a label count from 1 to 100.");
        return;
      }
      plab.disabled = true;
      try {
        await binAuditQueueLabels(sku, n);
        alert(
          `${n} label(s) queued for ${sku}. Stick them on, then pair ` +
            `(Scan station or the C72) - the count stays put.`
        );
      } catch (err) {
        alert(err.message);
      }
      plab.disabled = false;
      return;
    }
    // Un-retire: the marked-sold tag answered - the box never left.
    const unret = e.target.closest(".binaudit-unretire");
    if (unret) {
      const sku = unret.dataset.sku;
      const epcs = (unret.dataset.epcs || "").split(",").filter(Boolean);
      if (
        !confirm(
          `Un-retire ${epcs.length} tag(s) of ${sku}?\n\nThey were ` +
            `marked sold but they ANSWERED this sweep - the box is ` +
            `still on the shelf. The record(s) become live again. ` +
            `History-logged.`
        )
      )
        return;
      unret.disabled = true;
      try {
        await postJson("/api/assignments/unretire", {
          epcs,
          changed_by: operatorEl.value || null,
        });
        await binAuditRefreshRow(sku);
      } catch (err) {
        alert(err.message);
        unret.disabled = false;
      }
      return;
    }
    // The on-hand stepper: +/- pick the number, Apply writes it.
    // Raises go through the normal confirmed write; lowers through the
    // guarded lower (silent tags retire with it, first-tagging ban
    // enforced server-side).
    const stepBtn = e.target.closest(".ba-step-dn, .ba-step-up");
    if (stepBtn) {
      const wrap = stepBtn.closest(".stepper");
      const val = wrap.querySelector(".stepper__val");
      const base = parseInt(wrap.dataset.base, 10);
      let n =
        parseInt(val.textContent, 10) +
        (stepBtn.classList.contains("ba-step-up") ? 1 : -1);
      if (n < 0) n = 0;
      val.textContent = n;
      val.classList.toggle("stepper__val--dirty", n !== base);
      const apply = wrap.parentElement.querySelector(".ba-apply");
      if (apply) {
        apply.hidden = n === base;
        apply.textContent = `Apply ${n}`;
      }
      return;
    }
    const applyBtn = e.target.closest(".ba-apply");
    if (applyBtn) {
      const wrap = applyBtn.parentElement.querySelector(".stepper");
      const sku = wrap.dataset.sku;
      const base = parseInt(wrap.dataset.base, 10);
      const n = parseInt(
        wrap.querySelector(".stepper__val").textContent, 10
      );
      if (!Number.isFinite(n) || n === base) return;
      const sweepAt =
        (binAudit &&
          binAudit.cap &&
          (binAudit.cap.oldest_at || binAudit.cap.created_at)) ||
        null;
      applyBtn.disabled = true;
      try {
        if (n > base) {
          if (
            !confirm(
              `Set Shopify ON-HAND for ${sku} to ${n}?\n\nShopify ` +
                `carries ${base}. This WRITES the number to Shopify. ` +
                `Undo stays available in History.`
            )
          ) {
            applyBtn.disabled = false;
            return;
          }
          const res = await postJson("/api/onhand-updates", {
            sku,
            new_qty: n,
            changed_by: operatorEl.value || null,
            confirmed: true,
            sweep_at: sweepAt,
          });
          alert(res.message);
        } else {
          const drop = base - n;
          const epcs = (wrap.dataset.epcs || "")
            .split(",")
            .filter(Boolean)
            .slice(0, drop);
          const canLower = wrap.dataset.canlower === "1";
          // Pickup guard (F9168A): a shortfall that open local-pickup
          // orders explain is NOT shrinkage - the coming fulfillment
          // drops on-hand by itself, so lowering now would double-drop.
          const sold = parseInt(wrap.dataset.sold, 10) || 0;
          const pickup = parseInt(wrap.dataset.pickup, 10) || 0;
          const pickupExplains =
            pickup > 0 && drop > sold && drop <= sold + pickup;
          if (
            !confirm(
              `Set Shopify ON-HAND for ${sku} DOWN to ${n}?\n\n` +
                `${epcs.length} silent tag(s) retire presumed-sold ` +
                `with it - sales cover what they can, the rest writes ` +
                `off as shrinkage.` +
                (pickupExplains
                  ? `\n\n\u26a0 Open local-pickup orders explain this ` +
                    `shortfall - the staged boxes drop on-hand by ` +
                    `themselves when they're collected. Lowering now ` +
                    `would DOUBLE-DROP the count.`
                  : ``) +
                (canLower
                  ? ""
                  : `\n\n\u26a0 The server may refuse this: a product ` +
                    `that never completed a batch tagging only lowers ` +
                    `as far as recorded sales cover.`) +
                `\n\nOne Undo in History reverses all of it.`
            )
          ) {
            applyBtn.disabled = false;
            return;
          }
          const res = await postJson("/api/onhand-updates/lower", {
            sku,
            bin_name: (binAudit && binAudit.rep.bin) || "",
            new_qty: n,
            epcs,
            changed_by: operatorEl.value || null,
            confirmed: true,
            sweep_at: sweepAt,
          });
          alert(res.message);
        }
        await binAuditRefreshRow(sku);
      } catch (err) {
        alert(err.message);
        applyBtn.disabled = false;
      }
      return;
    }
    // Audit sign-off (Nick, 2026-09-28): writes the BinAudit anchor.
    const done = e.target.closest("#binaudit-complete");
    if (done && binAudit) {
      const bin = binAudit.rep.bin;
      const cap = binAudit.cap;
      done.disabled = true;
      try {
        const single = /^\d+$/.test(String(cap.id));
        const res = await postJson(
          `/api/bins/${encodeURIComponent(bin)}/audit-complete`,
          {
            ...(single
              ? { capture_id: parseInt(cap.id, 10) }
              : { epcs: cap.epcs || [] }),
            worker: operatorEl.value || null,
          }
        );
        done.textContent = "✓ Audit recorded";
        done.title = res.message || "Recorded.";
        loadAuditBins();
        // The sign-off just ticked this bin in any open walk session
        // (server-side) - redraw the cards to show it.
        loadAuditSessions();
      } catch (err) {
        alert(err.message);
        done.disabled = false;
      }
      return;
    }
    const mark = e.target.closest("#binaudit-marktagged");
    if (mark && binAudit) {
      const bin = binAudit.rep.bin;
      mark.disabled = true;
      try {
        // Unconfirmed first: the server answers 409 with the exact
        // consequence text, which becomes the confirmation.
        await postJson(`/api/bins/${encodeURIComponent(bin)}/mark-tagged`, {
          created_by: operatorEl.value || null,
        });
      } catch (err) {
        if (!/Confirm to record it/.test(err.message)) {
          alert(err.message);
          mark.disabled = false;
          return;
        }
        if (!confirm(err.message)) {
          mark.disabled = false;
          return;
        }
        try {
          const res = await postJson(
            `/api/bins/${encodeURIComponent(bin)}/mark-tagged`,
            { created_by: operatorEl.value || null, confirmed: true }
          );
          alert(res.message);
          document.getElementById("binaudit-run").click();
          loadAuditBins();
        } catch (err2) {
          alert(err2.message);
          mark.disabled = false;
        }
      }
    }
  });

async function loadAuditBins() {
  const list = document.getElementById("audit-bins");
  list.innerHTML = '<li class="recent__empty">Comparing…</li>';
  try {
    auditData = await apiJson("/api/audit/bins");
    renderAuditBins();
    renderAuditReco();
    // Slim copy for the next visit's instant paint (no per-product
    // arrays - the reco cards and session pills don't need them).
    try {
      localStorage.setItem("audbins_cache", JSON.stringify({
        ts: Date.now(),
        threshold_days: auditData.threshold_days,
        bins: auditData.bins.map((b) => ({
          bin: b.bin, score: b.score, overdue: b.overdue,
          batch_done: b.batch_done,
          last_audited_at: b.last_audited_at,
          last_audited_by: b.last_audited_by,
          mismatched_count: b.mismatched_count,
          product_count: b.product_count,
        })),
      }));
    } catch (e) { /* storage blocked */ }
    // Open sessions carry live bin pills from the same data.
    if (audSessions.length) renderAuditSessions();
  } catch (err) {
    list.innerHTML = `<li class="recent__empty">${escapeHtml(err.message)}</li>`;
  }
}

document.getElementById("audit-untagged").addEventListener("click", () => {
  auditShowUntagged = !auditShowUntagged;
  renderAuditBins();
});
document
  .getElementById("audit-reload")
  .addEventListener("click", async (ev) => {
    const stopDots = startDots(ev.currentTarget, "Reloading");
    try {
      await loadAuditBins();
    } finally {
      stopDots();
    }
  });
let auditFilterTimer;
document.getElementById("audit-filter").addEventListener("input", () => {
  clearTimeout(auditFilterTimer);
  auditFilterTimer = setTimeout(renderAuditBins, 150);
});
// Full re-read of bins + on-hand from Shopify (~a minute in the
// background), then the list reloads itself when the walk finishes.
refreshify("audit-refresh", "audit-onhand-pull", async () => {
  try {
    await postJson("/api/bin-map/refresh", {});
    for (let i = 0; i < 40; i++) {
      await new Promise((r) => setTimeout(r, 3000));
      const s = await apiJson("/api/bin-map/status");
      if (!s.refreshing) break;
    }
    loadAuditBins();
  } catch (err) {
    setResult(err.message, "err");
  }
});

function audPaintFromCache() {
  // Sessions + recommended racks draw from the last visit's data
  // BEFORE any network answers; the live loads overwrite in place
  // and the freshness tag flips green when they land.
  try {
    if (!auditData) {
      const raw = JSON.parse(localStorage.getItem("audbins_cache") || "null");
      if (raw && raw.bins) {
        auditData = { threshold_days: raw.threshold_days, bins: raw.bins,
                      _cached: true };
        renderAuditReco();
      }
    }
    if (!audSessions.length) {
      const raw = JSON.parse(
        localStorage.getItem("audsess_cache") || "null");
      if (raw && Array.isArray(raw.sessions)) {
        audSessions = raw.sessions;
        renderAuditSessions();
      }
    }
  } catch (e) { /* cache is decoration */ }
}

async function loadAudits() {
  // Last-known card numbers paint instantly (yellow "last refreshed"
  // tag); the tag flips green once every live load below has landed.
  audRestoreCards();
  audPaintFromCache();
  const slowLoads = [
    loadOneleft(),
    loadAuditBins(),
    loadAuditSessions(),
    loadUnavailable(),
    loadPacking(),
  ];
  Promise.allSettled(slowLoads).then(() =>
    setFreshTag("audhub-fresh", true)
  );
}

// Unavailable stock (Nick, 2026-09-08): every set-aside in one place -
// how many, when and by whom (from the unavailable-move History; admin
// moves have no local record and say so), and the live Staff Comments.
async function loadUnavailable() {
  const list = document.getElementById("unavail-list");
  try {
    const d = await apiJson("/api/audit/unavailable");
    audSetCard(
      "ahc-unavail",
      String(d.count),
      d.count
        ? `${d.total_units} unit(s) set aside`
        : "nothing set aside ✓",
      d.count ? "warn" : "ok"
    );
    document.getElementById("unavail-meta").textContent =
      d.count && !d.comments_live
        ? "(Staff Comments could not be fetched - showing the rest)"
        : `(${d.count})`;
    list.innerHTML = d.count
      ? ""
      : '<li class="recent__empty">No products have unavailable stock.</li>';
    d.items.forEach((g) => {
      const li = document.createElement("li");
      const bucket = g.bucket
        ? ` into ${g.bucket.replace(/_/g, " ")}`
        : "";
      // Date provenance: our own History (who + when), Shopify's
      // per-bucket last-change stamp (when only), or beyond even
      // Shopify's memory - the oldest of all, so it sorts first.
      const when = g.set_at
        ? g.set_source === "shopify"
          ? `set aside ${fmtAgo(g.set_at)}${bucket} - from Shopify's bucket history (${fmtWhen(g.set_at)})`
          : `set aside ${fmtAgo(g.set_at)}` +
            (g.set_by ? ` by ${g.set_by}` : "") +
            bucket +
            ` (${fmtWhen(g.set_at)})`
        : "set aside 6+ months ago (beyond Shopify's history)";
      // Return-to-available: only when tag records AND the latest
      // sweep both cover the FULL on-hand - the "unavailable" units
      // are demonstrably on the shelf like everything else.
      const evidence =
        g.tag_units != null
          ? `${g.tag_units} tag(s) on file · ${g.heard_units} heard on the last sweep · on-hand ${g.on_hand_total}`
          : "";
      li.innerHTML = `
        <span class="inventory__bin">${escapeHtml((g.bins || []).join(", ") || "—")}</span>
        <span class="recent__prod">
          <b><span class="prodopen" data-sku="${escapeHtml(g.sku || "")}" title="Open this product - label editor, flags, full history">${escapeHtml(g.product_title || g.sku || "?")}</span></b>
          <span class="mono u-dim75"> ${escapeHtml(g.sku || "")}</span>
          <div class="olrow__sub">${g.unavailable} unavailable · ${g.effective_qty} sellable on the shelf · ${escapeHtml(when)}${
            evidence
              ? `<div class="u-dim75 u-mt2">${escapeHtml(evidence)}</div>`
              : ""
          }${
            g.staff_comments
              ? `<div class="unavail__comment">${escapeHtml(g.staff_comments)}</div>`
              : `<div class="u-dim60 u-mt2">no staff comment</div>`
          }${
            g.return_ok
              ? `<div class="u-mt5"><button class="reset unavail-return" type="button"
                   data-sku="${escapeHtml(g.sku || "")}" data-qty="${g.unavailable}"
                   title="Every unit - the set-aside included - is tagged AND answered the last sweep, so nothing is actually missing. Moves the unavailable unit(s) back to available in Shopify (on-hand total unchanged). Confirmed, History-logged.">RETURN ${g.unavailable} TO AVAILABLE</button></div>`
              : ""
          }</div>
        </span>
        <span class="audit-mm" title="units in the Unavailable bucket">${g.unavailable}</span>`;
      const open = li.querySelector(".prodopen");
      if (open)
        open.addEventListener("click", () => {
          if (open.dataset.sku) openProductHistory(open.dataset.sku);
        });
      const ret = li.querySelector(".unavail-return");
      if (ret)
        ret.addEventListener("click", async () => {
          const sku = ret.dataset.sku;
          const qty = parseInt(ret.dataset.qty, 10);
          if (
            !confirm(
              `Return ${qty} unit(s) of ${sku} to AVAILABLE?\n\n` +
                `All ${g.on_hand_total} unit(s) are tagged and answered ` +
                `the last sweep - the set-aside is sitting on the shelf ` +
                `like everything else.\n\nThis moves the unit(s) out of ` +
                `the unavailable bucket(s) in Shopify; on-hand total ` +
                `stays the same, sellable goes up by ${qty}. ` +
                `History-logged.`
            )
          )
            return;
          ret.disabled = true;
          try {
            const r = await postJson(
              `/api/products/${encodeURIComponent(sku)}/unavailable-move`,
              {
                bucket: "auto",
                direction: "out",
                qty,
                confirmed: true,
                changed_by: operatorEl.value || null,
              }
            );
            alert(r.message);
            loadUnavailable();
          } catch (err) {
            alert(err.message);
            ret.disabled = false;
          }
        });
      list.append(li);
    });
  } catch (err) {
    audSetCard("ahc-unavail", "!", "could not load", "bad");
    list.innerHTML = `<li class="recent__empty">${escapeHtml(err.message)}</li>`;
  }
}

// === Packing scans (phase 6, 2026-09-28) ====================================
// The desk list: what got scanned while packing, allocated against
// ShipStation's awaiting-shipment orders. Live-ish: reloads on open and
// every 10s while the pane is visible; rows flip to shipped server-side.
const PACK_CHIPS = {
  allocated: ["chip--ok", "packed"],
  shipped: ["chip--ok", "shipped ✓"],
  duplicate: ["chip--warn", "duplicate?"],
  "not-in-shipping": ["chip--warn", "not in shipping"],
  unknown: ["chip--bad", "unknown code"],
};
let packTimer = null;

async function loadPacking() {
  const list = document.getElementById("pack-list");
  const meta = document.getElementById("pack-meta");
  try {
    const d = await apiJson("/api/packing/scans");
    const c = d.counts || {};
    const warn = (c.duplicate || 0) + (c["not-in-shipping"] || 0)
      + (c.unknown || 0);
    audSetCard(
      "ahc-pack",
      String((c.allocated || 0) + (c.shipped || 0)),
      d.shipstation
        ? `${c.shipped || 0} shipped · ${c.allocated || 0} awaiting`
          + (warn ? ` · ${warn} ⚠` : "")
        : "ShipStation isn't configured",
      warn ? "warn" : (c.allocated || c.shipped) ? "ok" : null
    );
    meta.textContent = d.shipstation
      ? `${c.shipped || 0} shipped · ${c.allocated || 0} awaiting`
        + (warn ? ` · ${warn} warning(s)` : "")
      : "⚠ ShipStation isn't configured - every scan lands as "
        + "'not in shipping'.";
    list.innerHTML = (d.scans || []).length
      ? ""
      : '<li class="recent__empty">Nothing scanned yet - the list fills as boxes get packed.</li>';
    (d.scans || []).forEach((r) => {
      const [cls, label] = PACK_CHIPS[r.status] || ["chip--na", r.status];
      const li = document.createElement("li");
      li.className = "recent__item";
      li.innerHTML =
        `<span class="binaudit-chip ${cls}">${escapeHtml(label)}</span>` +
        `<span class="recent__prod"><b>${escapeHtml(r.product_title || r.sku || r.code)}</b>` +
        (r.sku ? ` <span class="mono">${escapeHtml(r.sku)}</span>` : "") +
        (r.order_number ? ` · Order #${escapeHtml(r.order_number)}` : "") +
        (r.epc ? "" : ' <span class="dim" title="Barcode scan - no tag link, so this row cannot retire a tag when it ships">no tag</span>') +
        "</span>" +
        `<span class="recent__meta recent__when">${escapeHtml(fmtAgo(r.scanned_at))}</span>` +
        (r.status !== "shipped"
          ? `<button class="reset pack-rm" type="button" data-id="${r.id}" title="Remove this mis-scan">✕</button>`
          : "");
      list.append(li);
    });
    list.querySelectorAll(".pack-rm").forEach((b) =>
      b.addEventListener("click", async () => {
        b.disabled = true;
        try {
          await apiJson(`/api/packing/scans/${b.dataset.id}`,
                        { method: "DELETE" });
          loadPacking();
        } catch (err) {
          alert(err.message);
          b.disabled = false;
        }
      })
    );
  } catch (err) {
    audSetCard("ahc-pack", "!", "could not load", "bad");
    meta.textContent = err.message;
  }
}

document
  .querySelector('#tab-audits .tile[data-pane="packing"]')
  .addEventListener("click", () => {
    loadPacking();
    clearInterval(packTimer);
    packTimer = setInterval(() => {
      const pane = document.getElementById("apane-packing");
      if (pane && !pane.hidden && !document.hidden) loadPacking();
      else { clearInterval(packTimer); packTimer = null; }
    }, 10000);
    setTimeout(() => document.getElementById("pack-scan").focus(), 50);
  });

document.getElementById("pack-scan").addEventListener("keydown", async (e) => {
  if (e.key !== "Enter") return;
  const input = e.target;
  const code = input.value.trim();
  if (!code) return;
  input.value = "";
  try {
    const res = await postJson("/api/packing/scans", {
      code,
      worker: operatorEl.value || null,
    });
    document.getElementById("pack-meta").textContent = res.message;
    loadPacking();
  } catch (err) {
    document.getElementById("pack-meta").textContent = err.message;
  }
});

// === 1-left stock checks (Audits tab) =======================================
// The dashboard's verification queue joined against RFID evidence. The
// server does all the judging; this block only renders and relays clicks.
let olData = null;
let olAnsweredOnly = false;

const OL_VERDICTS = {
  confirmable: [
    "chip--ok",
    "RFID answers this",
    "A bin walk-scan or batch count since the check was raised covers the claimed stock - auto-clear will take it, or confirm it yourself",
  ],
  discrepancy: [
    "chip--bad",
    "Shopify 0, RFID sees stock",
    "Shopify now says none on hand but RFID evidence found stock after the check was raised - walk this one, something disagrees",
  ],
  "zero-claim": [
    "chip--warn",
    "now 0 - walk it",
    "Shopify has dropped to 0 since the check was raised; RFID can't prove an absence, so a human walk settles it",
  ],
  requeued: [
    "chip--warn",
    "re-queued - walk it",
    "An operator put this back on the queue after it was cleared, so it stays for a human until NEW evidence shows up",
  ],
  "needs-walk": [
    "chip--na",
    "needs a walk",
    "No (or not enough) RFID evidence since the check was raised",
  ],
};

function olVerdictChip(v) {
  const [cls, label, tip] = OL_VERDICTS[v] || OL_VERDICTS["needs-walk"];
  return `<span class="binaudit-chip ${cls}" title="${escapeHtml(tip)}">${escapeHtml(label)}</span>`;
}

async function loadOneleft() {
  const list = document.getElementById("ol-list");
  list.innerHTML = '<li class="recent__empty">Loading…</li>';
  try {
    olData = await apiJson("/api/oneleft/board");
  } catch (err) {
    olData = null;
    list.innerHTML = `<li class="recent__empty">Could not load: ${escapeHtml(err.message)}</li>`;
    return;
  }
  renderOneleft();
  renderAuditReco();
}

function renderOneleft() {
  if (!olData) return;
  const list = document.getElementById("ol-list");
  const status = document.getElementById("ol-status");
  const meta = document.getElementById("ol-meta");
  const autoBtn = document.getElementById("ol-auto");
  const scanBtn = document.getElementById("ol-scan");
  const canWrite = olData.mode === "confirm";

  autoBtn.textContent = `Auto-clear: ${olData.auto ? "ON" : "OFF"}`;
  autoBtn.disabled = !canWrite;
  scanBtn.disabled = !canWrite;
  document.getElementById("ol-answered").textContent = olAnsweredOnly
    ? "Show all checks"
    : "Show RFID-answered only";

  if (!olData.configured) {
    meta.textContent = "";
    status.textContent =
      "The dashboard bridge is off (ONELEFT_MODE app setting). The " +
      "1-left queue can't be read from here until it's enabled.";
    list.innerHTML = "";
    audSetCard("ahc-checks", "–", "bridge off", null);
    renderOneleftReceipts();
    return;
  }
  if (!olData.ok) {
    meta.textContent = "";
    status.textContent = `The dashboard didn't answer: ${olData.error || "unknown error"}. Nothing is broken here - reload to retry.`;
    list.innerHTML = "";
    audSetCard("ahc-checks", "!", "dashboard didn't answer", "bad");
    renderOneleftReceipts();
    return;
  }

  const v = olData.verdicts || {};
  meta.textContent =
    `(${olData.count} pending · ${v.confirmable || 0} answered by RFID` +
    (canWrite ? "" : " · read-only mode") + ")";
  status.textContent = "";
  audSetCard(
    "ahc-checks",
    String(olData.count),
    `${v.confirmable || 0} answered by RFID`,
    v.confirmable ? "ok" : null
  );

  const needle = document.getElementById("ol-filter").value.trim().toLowerCase();
  const rank = {
    confirmable: 0,
    discrepancy: 1,
    requeued: 2,
    "zero-claim": 3,
    "needs-walk": 4,
  };
  let rows = olData.items
    .filter((r) => !olAnsweredOnly || r.verdict === "confirmable")
    .filter(
      (r) =>
        !needle ||
        [r.sku, r.product_title, r.vendor, r.bin]
          .join(" ")
          .toLowerCase()
          .includes(needle)
    )
    .sort(
      (a, b) =>
        (rank[a.verdict] ?? 9) - (rank[b.verdict] ?? 9) ||
        String(a.detected_date || "").localeCompare(
          String(b.detected_date || "")
        )
    );
  const total = rows.length;
  rows = rows.slice(0, 150);

  list.innerHTML = total
    ? ""
    : '<li class="recent__empty">Nothing matches.</li>';
  rows.forEach((r) => {
    const li = document.createElement("li");
    li.className = "olrow";
    const sub = [
      r.vendor,
      r.bin ? `bin ${r.bin}` : "no bin",
      r.claimed == null ? "claims ?" : `claims ${r.claimed}`,
      `${r.tag_count} tag(s) on file`,
      `raised ${fmtAgo(r.detected_date)}`,
    ]
      .filter(Boolean)
      .join(" · ");
    const evidence = (r.evidence || []).join("; ");
    li.innerHTML = `
      ${olVerdictChip(r.verdict)}
      <div class="olrow__main">
        <span class="binlist__name ol-sku" data-sku="${escapeHtml(r.sku)}"
              title="Open this product's panel">${escapeHtml(r.sku)}</span>
        <span class="olrow__title">${escapeHtml(r.product_title || "")}</span>
        <div class="olrow__sub" title="${escapeHtml(sub + (evidence ? " · " + evidence : ""))}">
          ${escapeHtml(sub)}${evidence ? ` · <b>${escapeHtml(evidence)}</b>` : ""}
        </div>
      </div>
      ${canWrite ? `<button class="binlist__go ol-confirm" type="button" data-sku="${escapeHtml(r.sku)}"
        data-title="${escapeHtml(r.product_title || "")}"
        data-bin="${escapeHtml(r.bin || "")}"
        title="Open the confirm window - live stock breakdown + the count box (prefilled with on-hand; confirming makes your number THE on-hand). Undoable with re-queue.">Confirm ✓</button>` : ""}`;
    list.append(li);
  });
  if (total > rows.length) {
    const li = document.createElement("li");
    li.className = "recent__empty";
    li.textContent = `…and ${total - rows.length} more - narrow with the filter.`;
    list.append(li);
  }
  renderOneleftReceipts();
}

function renderOneleftReceipts() {
  const list = document.getElementById("ol-receipts");
  const receipts = (olData && olData.receipts) || [];
  const canWrite = olData && olData.mode === "confirm";
  list.innerHTML = receipts.length
    ? ""
    : '<li class="recent__empty">No 1-left actions taken from here yet.</li>';
  receipts.forEach((r) => {
    const li = document.createElement("li");
    li.className = "olrow" + (r.ok ? " olrow--done" : "");
    const what =
      r.action === "requeue"
        ? "re-queued on the dashboard"
        : r.action === "manual"
          ? `confirmed on the dashboard (as ${r.employee || "?"})`
          : `auto-cleared (as ${r.employee || "?"}) - evidence ${r.evidence_units} vs claimed ${r.claimed == null ? "?" : r.claimed}`;
    li.innerHTML = `
      <span class="binaudit-chip ${r.ok ? "chip--ok" : "chip--bad"}">${r.ok ? "done" : "FAILED"}</span>
      <div class="olrow__main">
        <span class="binlist__name ol-sku" data-sku="${escapeHtml(r.sku)}">${escapeHtml(r.sku)}</span>
        <span class="olrow__title">${escapeHtml(what)}</span>
        <div class="olrow__sub" title="${escapeHtml(r.evidence || "")}">
          ${escapeHtml([r.operator, fmtAgo(r.created_at), r.error].filter(Boolean).join(" · "))}
        </div>
      </div>
      ${
        canWrite && r.ok && r.action !== "requeue"
          ? `<button class="binlist__go ol-requeue" type="button" data-sku="${escapeHtml(r.sku)}"
              title="Undo: put this SKU back on the dashboard's pending queue">Re-queue</button>`
          : ""
      }`;
    list.append(li);
  });
}

document.getElementById("ol-list").addEventListener("click", olRowClick);
document.getElementById("ol-receipts").addEventListener("click", olRowClick);

async function olRowClick(e) {
  const sku = e.target.closest(".ol-sku");
  if (sku) {
    openProductHistory(sku.dataset.sku);
    return;
  }
  const confirmBtn = e.target.closest(".ol-confirm");
  if (confirmBtn) {
    openOlConfirm(
      confirmBtn.dataset.sku,
      confirmBtn.dataset.title || "",
      confirmBtn.dataset.bin || ""
    );
    return;
  }
  const requeueBtn = e.target.closest(".ol-requeue");
  if (requeueBtn) {
    requeueBtn.disabled = true;
    try {
      await postJson("/api/oneleft/requeue", {
        sku: requeueBtn.dataset.sku,
        worker: operatorEl.value || null,
      });
    } catch (err) {
      alert(err.message);
    }
    loadOneleft();
  }
}

// --- 1-left confirm window ---------------------------------------------------
// Like the inventory-check window: live tiles (Unavailable when present,
// Committed, Available, On-hand) plus the count box, prefilled with the
// live on-hand. THE NUMBER IS THE ON-HAND (Nick, 2026-09-09): equal (the
// prefill) just confirms the known number; higher runs the audited raise;
// lower runs the sales-guarded lower - if the guard refuses, the check
// still confirms and the discrepancy is filed for Review.
let olcSku = null;
let olcOnHand = null;
let olcBin = null;

async function openOlConfirm(sku, title, bin) {
  olcSku = sku;
  olcOnHand = null;
  olcBin = (bin || "").trim() || null;
  document.getElementById("olc-title").textContent =
    `Confirm stock check - ${sku}`;
  document.getElementById("olc-product").textContent = title || "";
  document.getElementById("olc-stats").innerHTML =
    '<div class="rvw-stat"><div class="rvw-stat__l">Loading…</div><div class="rvw-stat__n">…</div></div>';
  document.getElementById("olc-live").textContent = "";
  document.getElementById("olc-note").textContent = "";
  document.getElementById("olc-count").value = "";
  document.getElementById("olconfirm-overlay").hidden = false;
  try {
    const st = await apiJson(`/api/oneleft/stock/${encodeURIComponent(sku)}`);
    if (!st.ok) throw new Error(st.error || "no answer");
    olcOnHand = st.on_hand;
    document.getElementById("olc-stats").innerHTML =
      (st.unavailable
        ? `<div class="rvw-stat"><div class="rvw-stat__l">Unavailable</div><div class="rvw-stat__n">${st.unavailable}</div></div>`
        : "") +
      `<div class="rvw-stat"><div class="rvw-stat__l">Committed</div><div class="rvw-stat__n">${st.committed}</div></div>
       <div class="rvw-stat"><div class="rvw-stat__l">Available</div><div class="rvw-stat__n">${st.available}</div></div>
       <div class="rvw-stat rvw-stat--live"><div class="rvw-stat__l">On-hand</div><div class="rvw-stat__n">${st.on_hand}</div></div>`;
    document.getElementById("olc-count").value = st.on_hand;
  } catch (err) {
    document.getElementById("olc-stats").innerHTML = "";
    document.getElementById("olc-live").textContent =
      `Live stock unavailable right now (${err.message}) - you can still ` +
      `confirm without a count.`;
  }
}

document.getElementById("olc-cancel").addEventListener("click", () => {
  document.getElementById("olconfirm-overlay").hidden = true;
});
document.getElementById("olconfirm-overlay").addEventListener("click", (e) => {
  if (e.target === e.currentTarget) e.currentTarget.hidden = true;
});
document.getElementById("olc-go").addEventListener("click", async () => {
  if (!olcSku) return;
  const operator = operatorEl.value;
  if (!operator) {
    alert("Pick who's scanning (top right) first.");
    return;
  }
  const raw = document.getElementById("olc-count").value.trim();
  const counted = raw === "" ? null : Number(raw);
  const btn = document.getElementById("olc-go");
  btn.disabled = true;
  try {
    // The typed number IS the on-hand (Nick, 2026-09-09). Higher: the
    // audited raise. Lower: the sales-guarded lower - refused when
    // recorded sales don't cover the drop, in which case the check
    // still confirms and the discrepancy is filed for Review.
    if (counted != null && olcOnHand != null && counted > olcOnHand) {
      if (
        confirm(
          `You counted ${counted} but Shopify on-hand is ${olcOnHand}.\n\n` +
            `Write on-hand ${olcOnHand} → ${counted} to Shopify? ` +
            `Confirmed, logged, undoable from History. (Cancel keeps ` +
            `Shopify as is - the check still confirms.)`
        )
      ) {
        await postJson("/api/onhand-updates", {
          sku: olcSku,
          new_qty: counted,
          confirmed: true,
          changed_by: operator,
        });
      }
    } else if (counted != null && olcOnHand != null && counted < olcOnHand) {
      if (
        confirm(
          `You counted ${counted} but Shopify on-hand is ${olcOnHand}.\n\n` +
            `Lower on-hand ${olcOnHand} → ${counted}? Allowed only when ` +
            `recorded sales cover the drop (audited, undoable from ` +
            `History). Cancel confirms the check without touching stock.`
        )
      ) {
        try {
          await postJson("/api/onhand-updates/lower", {
            sku: olcSku,
            bin_name: olcBin || "?",
            new_qty: counted,
            confirmed: true,
            changed_by: operator,
          });
        } catch (err) {
          alert(
            `Couldn't lower on-hand: ${err.message}\n\nThe check still ` +
              `confirms - the discrepancy is filed for Review instead.`
          );
        }
      }
    }
    await postJson("/api/oneleft/confirm", {
      sku: olcSku,
      worker: operator,
      counted,
    });
    document.getElementById("olconfirm-overlay").hidden = true;
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
  }
  loadOneleft();
});

document.getElementById("ol-auto").addEventListener("click", async () => {
  if (!olData) return;
  const next = !olData.auto;
  if (
    !next &&
    !window.confirm(
      "Pause auto-clear? Checks the RFID evidence answers will pile up " +
        "on the dashboard until it's back on."
    )
  )
    return;
  try {
    await postJson("/api/oneleft/auto", {
      on: next,
      worker: operatorEl.value || null,
    });
  } catch (err) {
    alert(err.message);
  }
  loadOneleft();
});

refreshify("ol-scan", "oneleft-scan", async () => {
  let outcome;
  try {
    const res = await postJson("/api/oneleft/scan", {
      worker: operatorEl.value || null,
    });
    const n = (res.confirmed || []).length;
    outcome = res.ran
      ? n
        ? `Cleared ${n} ✓`
        : "Nothing to clear"
      : "Auto is paused";
  } catch (err) {
    outcome = "Failed";
    alert(err.message);
  }
  loadOneleft();
  return outcome;
});

document.getElementById("ol-answered").addEventListener("click", () => {
  olAnsweredOnly = !olAnsweredOnly;
  renderOneleft();
});

refreshify("ol-reload", "oneleft-board", () => loadOneleft());

let olFilterTimer;
document.getElementById("ol-filter").addEventListener("input", () => {
  clearTimeout(olFilterTimer);
  olFilterTimer = setTimeout(renderOneleft, 150);
});

// === Audit hub ==============================================================
// The stat cards are the navigation: one tool pane on screen at a time,
// with the sessions index on the landing. Tool internals keep their ids.

function audShowPane(name) {
  document.getElementById("audit-hub").hidden = name !== null;
  document.querySelectorAll("#tab-audits .apane").forEach((p) => {
    p.hidden = p.id !== `apane-${name}`;
  });
  // Reopening the audit pane offers the product box again.
  if (name === "binaudit") binAuditProdRowShow(true);
}

document.querySelectorAll("#tab-audits .tiles--aud .tile[data-pane]").forEach((card) => {
  card.addEventListener("click", () => audShowPane(card.dataset.pane));
});
document.querySelectorAll("#tab-audits .apane-back").forEach((btn) => {
  btn.addEventListener("click", () => {
    audShowPane(null);
    loadAuditSessions();
  });
});

function audSetCard(prefix, num, sub, tone) {
  const numEl = document.getElementById(`${prefix}-num`);
  const subEl = document.getElementById(`${prefix}-sub`);
  if (!numEl) return;
  numEl.textContent = num;
  subEl.textContent = sub;
  subEl.className = "audcard__sub" + (tone ? ` audcard__sub--${tone}` : "");
  // Persist real values so the next visit paints them instantly under
  // the yellow "last refreshed" tag (Nick, 2026-09-08). Placeholders
  // ("–", "!") never overwrite a saved number.
  if (num !== "–" && num !== "!" && num !== "…") {
    try {
      const saved = JSON.parse(localStorage.getItem("audcards") || "{}");
      saved[prefix] = { num, sub, tone: tone || null };
      saved.__at = Date.now();
      localStorage.setItem("audcards", JSON.stringify(saved));
    } catch (e) {
      /* storage blocked - instant paint just won't happen */
    }
  }
}

// Paint the last-known card values immediately; live loads overwrite
// them and flip the tag green when the slowest one lands.
function audRestoreCards() {
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem("audcards") || "null");
  } catch (e) {
    saved = null;
  }
  if (!saved || !saved.__at) return false;
  for (const [prefix, c] of Object.entries(saved)) {
    if (prefix === "__at" || !c || typeof c !== "object") continue;
    const numEl = document.getElementById(`${prefix}-num`);
    const subEl = document.getElementById(`${prefix}-sub`);
    if (!numEl) continue;
    numEl.textContent = c.num;
    subEl.textContent = c.sub;
    subEl.className =
      "audcard__sub" + (c.tone ? ` audcard__sub--${c.tone}` : "");
  }
  setFreshTag("audhub-fresh", false, Date.now() - saved.__at);
  return true;
}

// === Audit sessions =========================================================
// Overhauled 2026-09-28 (approved preview): recommended racks up top
// (the scored queue rolled up per rack, overdue first), open sessions
// as cards with live bin strips, finished audits behind the icon.
let audSessions = [];
let audSessDone = [];
let audSessShowDone = false;
let audSessOpenId = null;

function audBinInfo(bin) {
  if (!auditData) return null;
  const up = (bin || "").toUpperCase();
  return (
    auditData.bins.find((b) => (b.bin || "").toUpperCase() === up) || null
  );
}

function audBinPillHtml(i) {
  const b = audBinInfo(i.key);
  const score = b ? b.score : null;
  const cls = i.done
    ? "binpill--ok"
    : score == null
      ? ""
      : score >= 5
        ? "binpill--bad"
        : score > 0
          ? "binpill--warn"
          : "binpill--ok";
  const mark = i.done
    ? "\u2713"
    : score == null
      ? "\u00b7"
      : score === 0
        ? "\u2713"
        : String(score);
  return `<button class="binpill ${cls} aud-binjump" type="button"
    data-bin="${escapeHtml(i.key)}"
    title="Open the bin audit with ${escapeHtml(i.key)} loaded">${escapeHtml(
      i.key
    )} <i>${mark}</i></button>`;
}

// The In-progress card IS the walk's view (Nick, 2026-09-28 round 11):
// the rack ring, drift facts and worst-open-bin note sit on the card
// with Finish/Abandon under them, the progress bar and bin chips move
// right to make room, and Resume opens the bin audit on the first bin
// still standing. The old Rack/Bins detail toggle is gone; finished
// walks reuse this card read-only.
function audSessCardHtml(sn, readonly) {
  const pct = sn.total ? Math.round((sn.done / sn.total) * 100) : 0;
  const next = (sn.items || []).find((i) => !i.done) || null;
  let drift = 0;
  let mism = 0;
  let known = 0;
  let worst = null;
  (sn.items || []).forEach((i) => {
    const b = audBinInfo(i.key);
    if (!b) return;
    known++;
    drift += b.score;
    mism += b.mismatched_count;
    if (!i.done && (!worst || b.score > worst.b.score)) {
      worst = { key: i.key, b };
    }
  });
  const C = 2 * Math.PI * 36;
  const ring = `<div class="ring ring--sm"><svg width="88" height="88" viewBox="0 0 88 88">
      <circle class="ring__trk" cx="44" cy="44" r="36" fill="none" stroke-width="9"/>
      <circle class="ring__val" cx="44" cy="44" r="36" fill="none" stroke-width="9"
        stroke-dasharray="${C.toFixed(1)}" stroke-dashoffset="${(
          C * (1 - pct / 100)
        ).toFixed(1)}"/></svg>
    <div class="ring__pct">${pct}%</div></div>`;
  const facts = `<div class="rackview__facts">
      <div><span class="rackview__lab">Bins</span><br>${sn.done} of ${sn.total} walked</div>
      <div><span class="rackview__lab">Drift</span><br>${
        known
          ? `score ${drift} across ${mism} mismatched product(s)`
          : "open the Audit queue once for live scores"
      }</div>
      ${
        worst && worst.b.score > 0
          ? `<div class="rackview__worst">Worst open bin: <b>${escapeHtml(
              worst.key
            )}</b> - score ${worst.b.score}${
              worst.b.last_audited_at
                ? `, audited ${escapeHtml(fmtAgo(worst.b.last_audited_at))}`
                : ", never audited"
            }.</div>`
          : !readonly && !next
            ? `<div class="rackview__worst rackview__worst--ok">\u2713 Every bin walked - finish the audit.</div>`
            : ""
      }</div>`;
  const strip = (sn.items || []).length
    ? `<div class="binstrip">${sn.items.map(audBinPillHtml).join("")}</div>`
    : "";
  const acts = readonly
    ? ""
    : `<div class="sess__acts">
         <button class="reset audsess-finish" type="button" data-sid="${sn.id}">Finish audit</button>
         <button class="reset audsess-abandon" type="button" data-sid="${sn.id}">Abandon</button>
       </div>`;
  return `
    <div class="sess__row">
      <span class="sess__name">${escapeHtml(sn.name)}</span>
      <span class="sess__meta">bin walk \u00b7 ${
        readonly
          ? escapeHtml(sn.status) +
            " " +
            escapeHtml(fmtAgo(sn.finished_at || sn.created_at))
          : `started ${escapeHtml(fmtAgo(sn.created_at))}${
              sn.created_by ? " by " + escapeHtml(sn.created_by) : ""
            }`
      }</span>
      <span class="sess__grow"></span>
      ${
        !readonly && next
          ? `<button class="print__btn audsess-resume" type="button" data-sid="${sn.id}"
               title="Open the bin audit on ${escapeHtml(next.key)}, the first bin not walked yet">Resume</button>`
          : ""
      }
    </div>
    <div class="sess__body">
      <div class="sess__left">
        <div class="sess__rackview">${ring}${facts}</div>
        ${acts}
      </div>
      <div class="sess__side">
        <div class="audsess__bar"><div class="audsess__fill" style="width:${pct}%"></div></div>
        <div class="audsess__nums"><span>${sn.done} of ${sn.total} walked</span><span>${pct}%</span></div>
        ${strip}
      </div>
    </div>`;
}

function renderAuditSessions() {
  const list = document.getElementById("audsess-list");
  document.getElementById("audsess-meta").textContent = audSessions.length
    ? `(${audSessions.length} open)`
    : "";
  const lab = document.getElementById("audsess-openlab");
  if (lab) lab.hidden = !audSessions.length;
  list.innerHTML = "";
  audSessions.forEach((sn) => {
    const card = document.createElement("div");
    card.className = "sess";
    if (sn.kind === "bins") {
      card.classList.add("sess--walk");
      card.innerHTML = audSessCardHtml(sn, false);
    } else {
      // 1-left walks keep the compact card: their tick-list still
      // lives behind Resume.
      const pct = sn.total ? Math.round((sn.done / sn.total) * 100) : 0;
      card.innerHTML = `
        <div class="sess__row">
          <span class="sess__name audsess-open" data-sid="${sn.id}">${escapeHtml(sn.name)}</span>
          <span class="sess__meta">${sn.total} check(s) \u00b7 started ${escapeHtml(
            fmtAgo(sn.created_at)
          )}${sn.created_by ? " by " + escapeHtml(sn.created_by) : ""}</span>
          <span class="sess__grow"></span>
          <button class="print__btn audsess-open" type="button" data-sid="${sn.id}">Resume</button>
        </div>
        <div class="audsess__bar"><div class="audsess__fill" style="width:${pct}%"></div></div>
        <div class="audsess__nums"><span>${sn.done} of ${sn.total} done</span><span>${pct}%</span></div>`;
    }
    list.append(card);
  });
}

async function loadAuditSessions() {
  const list = document.getElementById("audsess-list");
  try {
    const data = await apiJson("/api/audit-sessions?status=open");
    audSessions = data.sessions;
  } catch (err) {
    list.innerHTML = `<div class="pcard__note">Could not load sessions: ${escapeHtml(err.message)}</div>`;
    return;
  }
  renderAuditSessions();
  // Instant paint next visit (Nick, 2026-09-28: "show up quickly") -
  // the slim list is enough to draw the cards before the network
  // answers; the freshness tag already says when numbers are stale.
  try {
    localStorage.setItem("audsess_cache", JSON.stringify({
      ts: Date.now(),
      sessions: audSessions.map((sn) => ({
        id: sn.id, name: sn.name, kind: sn.kind, status: sn.status,
        total: sn.total, done: sn.done, created_at: sn.created_at,
        created_by: sn.created_by,
        items: (sn.items || []).map((i) => ({
          id: i.id, key: i.key, done: i.done, done_by: i.done_by,
        })),
      })),
    }));
  } catch (e) { /* storage blocked - instant paint just won't happen */ }
  // The open session detail refreshes from the same fetch.
  if (audSessOpenId !== null) {
    const open = audSessions.find((x) => x.id === audSessOpenId);
    if (open) renderAuditSessionDetail(open);
  }
  loadAuditSessionsDone();
}

async function loadAuditSessionsDone() {
  try {
    const data = await apiJson("/api/audit-sessions?status=done");
    audSessDone = data.sessions;
  } catch (err) {
    audSessDone = [];
  }
  const cnt = document.getElementById("audsess-fincount");
  if (cnt) cnt.textContent = String(audSessDone.length);
  renderAuditSessionsDone();
}

function renderAuditSessionsDone() {
  const wrap = document.getElementById("audsess-finished");
  const listEl = document.getElementById("audsess-finlist");
  if (!wrap || !listEl) return;
  wrap.hidden = !audSessShowDone;
  document
    .getElementById("audsess-toggle")
    .classList.toggle("iconbtn--on", audSessShowDone);
  if (!audSessShowDone) return;
  listEl.innerHTML = audSessDone.length
    ? ""
    : `<div class="fin"><span class="fin__meta">No finished audits yet.</span></div>`;
  audSessDone.forEach((sn) => {
    const div = document.createElement("div");
    div.className = "fin";
    div.innerHTML = `<span class="fin__ok">\u2713</span> ${escapeHtml(sn.name)}
      <span class="fin__meta">${sn.done} of ${sn.total} \u00b7 ${escapeHtml(
        sn.status
      )} ${escapeHtml(fmtAgo(sn.finished_at || sn.created_at))}${
        sn.created_by ? " \u00b7 by " + escapeHtml(sn.created_by) : ""
      }</span>
      <button class="reset fin__view audsess-open" type="button" data-sid="${sn.id}">View</button>`;
    listEl.append(div);
  });
}

// ---- recommended racks -----------------------------------------------
// The scored queue rolled up per rack: overdue past the threshold
// first, then the rest, both by summed drift. Open 1-left checks ride
// as a chip when their bin resolves to the rack.
function audRackAgg() {
  if (!auditData) return null;
  const racks = new Map();
  for (const b of auditData.bins) {
    if (!b.batch_done) continue;
    const rack = (b.bin.split("-")[0] || b.bin).toUpperCase();
    let r = racks.get(rack);
    if (!r) {
      r = {
        rack,
        score: 0,
        bins: [],
        mismatched: 0,
        overdue: false,
        last: undefined,
        anyNever: false,
        worst: null,
      };
      racks.set(rack, r);
    }
    r.score += b.score;
    r.mismatched += b.mismatched_count;
    r.bins.push(b);
    r.overdue = r.overdue || b.overdue;
    if (!b.last_audited_at) r.anyNever = true;
    else if (
      r.last === undefined ||
      tsDate(b.last_audited_at) < tsDate(r.last)
    ) {
      r.last = b.last_audited_at;
    }
    if (!r.worst || b.score > r.worst.score) r.worst = b;
  }
  return [...racks.values()];
}

function audRackCardHtml(r, overdue, checks) {
  const nChecks = checks[r.rack] || 0;
  const scoreCls =
    r.score === 0
      ? "rack__score--ok"
      : overdue || r.score >= 5
        ? "rack__score--bad"
        : "rack__score--warn";
  const when =
    r.anyNever && r.last === undefined
      ? "<b>Never audited</b>"
      : r.anyNever
        ? "<b>Some bins never audited</b>"
        : `Oldest audit <b>${escapeHtml(fmtAgo(r.last))}</b>`;
  const pills = r.bins
    .slice()
    .sort((a, b2) => b2.score - a.score)
    .map((b) =>
      audBinPillHtml({ key: b.bin, done: false })
    )
    .join("");
  return `<div class="rack" data-rack="${escapeHtml(r.rack)}">
    <div class="rack__top"><span class="rack__name">${escapeHtml(r.rack)}</span>
      <span class="rack__score ${scoreCls}">${
        r.score === 0 ? "all match \u2713" : "score " + r.score
      }</span></div>
    <div class="rack__meta">${when} \u00b7 ${r.bins.length} bin(s) \u00b7 ${
      r.mismatched
    } mismatched product(s)</div>
    ${
      nChecks || (r.worst && r.worst.score > 0)
        ? `<div class="rack__chips">${
            nChecks
              ? `<span class="rack__chip rack__chip--warn">${nChecks} stock check(s) open</span>`
              : ""
          }${
            r.worst && r.worst.score > 0
              ? `<span class="rack__chip">worst bin ${escapeHtml(r.worst.bin)}</span>`
              : ""
          }</div>`
        : ""
    }
    <div class="rack__foot">
      <button class="${overdue ? "print__btn" : "reset"} aud-reco-start"
        type="button" data-rack="${escapeHtml(r.rack)}"
        title="Start a walk-scan session covering every bin on this rack">Start audit</button>
      <span class="rack__hint">tap card for bins</span>
    </div>
    <div class="rack__bins"><div><div class="binstrip">${pills}</div></div></div>
  </div>`;
}

function renderAuditReco() {
  const el = document.getElementById("aud-reco");
  if (!el) return;
  const racks = audRackAgg();
  if (!racks) {
    el.innerHTML = "";
    return;
  }
  const checks = {};
  if (olData && Array.isArray(olData.items)) {
    for (const it of olData.items) {
      const bin = (it.bin || "").trim();
      if (!bin) continue;
      const rack = bin.split("-")[0].toUpperCase();
      checks[rack] = (checks[rack] || 0) + 1;
    }
  }
  const over = racks
    .filter((r) => r.overdue)
    .sort((a, b) => b.score - a.score);
  const fresh = racks
    .filter((r) => !r.overdue)
    .sort((a, b) => b.score - a.score);
  const thr = auditData.threshold_days || 14;
  el.innerHTML =
    (over.length
      ? `<div class="grouplab"><span class="grouplab__dot grouplab__dot--bad"></span>
           Recommended - last audited over ${thr} days ago (or never), biggest drift first</div>
         <div class="rackrow">${over
           .slice(0, 6)
           .map((r) => audRackCardHtml(r, true, checks))
           .join("")}</div>`
      : "") +
    (fresh.length
      ? `<div class="grouplab"><span class="grouplab__dot grouplab__dot--ok"></span>
           Up to date - audited inside ${thr} days, biggest drift first</div>
         <div class="rackrow">${fresh
           .slice(0, 6)
           .map((r) => audRackCardHtml(r, false, checks))
           .join("")}</div>`
      : "") +
    (over.length > 6 || fresh.length > 6
      ? `<p class="result">${over.length + fresh.length} rack(s) total - the Audit queue tile lists every bin, scored.</p>`
      : "");
}

async function audStartRackAudit(rack, btn) {
  btn.disabled = true;
  try {
    const res = await postJson("/api/audit-sessions", {
      kind: "bins",
      rack,
      name: `Rack ${rack}`,
      worker: operatorEl.value || null,
    });
    await loadAuditSessions();
    const sn =
      res && res.items
        ? res
        : audSessions.find(
            (x) => x.status === "open" && x.name === `Rack ${rack}`
          );
    // Straight into the walk (round 11): the first bin's audit opens
    // with its expected list, sweep or no sweep.
    const next = sn && (sn.items || []).find((i) => !i.done);
    if (next) jumpToBinAudit(next.key);
  } catch (err) {
    alert(err.message);
  }
  btn.disabled = false;
}

document.getElementById("aud-reco").addEventListener("click", (e) => {
  const start = e.target.closest(".aud-reco-start");
  if (start) {
    audStartRackAudit(start.dataset.rack, start);
    return;
  }
  const pill = e.target.closest(".aud-binjump");
  if (pill) {
    jumpToBinAudit(pill.dataset.bin);
    return;
  }
  const card = e.target.closest(".rack");
  if (card) card.classList.toggle("rack--open");
});

document.getElementById("aud-onebin-go").addEventListener("click", () => {
  const v = document.getElementById("aud-onebin").value.trim();
  if (v) jumpToBinAudit(v.toUpperCase());
});
document.getElementById("aud-onebin").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    document.getElementById("aud-onebin-go").click();
  }
});

function renderAuditSessionDetail(sn) {
  audSessOpenId = sn.id;
  const el = document.getElementById("audsess-detail");
  const pct = sn.total ? Math.round((sn.done / sn.total) * 100) : 0;
  const openCount = sn.total - sn.done;
  const actions =
    sn.status === "open"
      ? `<div class="linkbox__actions u-mt14">
           <button class="reset" id="audsess-finish" type="button">Finish audit</button>
           <button class="reset" id="audsess-abandon" type="button">Abandon</button>
         </div>`
      : "";
  if (sn.kind === "bins") {
    // Only finished walks land here (round 11): the In-progress card
    // on the hub carries the live view. This is the read-only record.
    el.innerHTML = `<div class="sess sess--walk">${audSessCardHtml(sn, true)}</div>`;
    return;
  }
  const rows = (sn.items || [])
    .map((i) => {
      const doneBtn =
        sn.status === "open"
          ? `<button class="binlist__go audsess-done" type="button"
               data-item="${i.id}" data-done="${i.done ? 1 : 0}"
               title="${i.done ? "Un-tick this item" : "Mark this item accounted for"}">${
                 i.done ? "\u2713 done" : "mark done"
               }</button>`
          : i.done
            ? `<span class="binlist__check">\u2713</span>`
            : "";
      const sub = [
        i.done && i.done_by ? `by ${i.done_by}` : "",
        i.done && i.done_at ? fmtAgo(i.done_at) : "",
        i.note || "",
      ]
        .filter(Boolean)
        .join(" \u00b7 ");
      return `
        <li class="olrow${i.done ? " olrow--done" : ""}">
          <div class="olrow__main">
            <span class="binlist__name ol-sku"
                  data-sku="${escapeHtml(i.key)}">${escapeHtml(i.key)}</span>
            <span class="olrow__title">${escapeHtml(i.label || "")}</span>
            ${sub ? `<div class="olrow__sub">${escapeHtml(sub)}</div>` : ""}
          </div>
          ${doneBtn}
        </li>`;
    })
    .join("");
  el.innerHTML = `
    <div class="sessdetail__head"><h2>${escapeHtml(sn.name)}</h2>
      <span class="recent__note">1-left checks \u00b7 ${escapeHtml(sn.status)}</span></div>
    <div class="audsess__bar u-maxw420"><div class="audsess__fill" style="width:${pct}%"></div></div>
    <div class="audsess__nums u-maxw420"><span>${sn.done} of ${sn.total} done</span><span>${pct}%</span></div>
    <p class="linkbox__text u-maxw70ch">Items tick themselves when their 1-left check clears (auto or manual confirm); anything left needs a walk.</p>
    ${actions}
    <ul class="recent__list binlist u-maxh480">${rows}</ul>`;

  const finish = document.getElementById("audsess-finish");
  if (finish)
    finish.addEventListener("click", async () => {
      if (
        openCount > 0 &&
        !window.confirm(
          `${openCount} item(s) are still open - finish anyway?`
        )
      )
        return;
      try {
        await postJson(`/api/audit-sessions/${sn.id}/finish`, {
          worker: operatorEl.value || null,
        });
        audSessOpenId = null;
        audShowPane(null);
        loadAuditSessions();
      } catch (err) {
        alert(err.message);
      }
    });
  const abandon = document.getElementById("audsess-abandon");
  if (abandon)
    abandon.addEventListener("click", async () => {
      if (!window.confirm("Abandon this audit? Its ticks are kept for the record."))
        return;
      try {
        await postJson(`/api/audit-sessions/${sn.id}/abandon`, {
          worker: operatorEl.value || null,
        });
        audSessOpenId = null;
        audShowPane(null);
        loadAuditSessions();
      } catch (err) {
        alert(err.message);
      }
    });
}

// Finish/Abandon straight from the In-progress card (round 11).
async function audSessClose(sid, verb, btn) {
  const sn = audSessions.find((x) => x.id === sid);
  if (!sn) return;
  const left = sn.total - sn.done;
  const msg =
    verb === "finish"
      ? left > 0
        ? `${left} bin(s) are still open - finish anyway?`
        : null
      : "Abandon this audit? Its ticks are kept for the record.";
  if (msg && !window.confirm(msg)) return;
  btn.disabled = true;
  try {
    await postJson(`/api/audit-sessions/${sid}/${verb}`, {
      worker: operatorEl.value || null,
    });
    if (audSessOpenId === sid) audSessOpenId = null;
    loadAuditSessions();
  } catch (err) {
    alert(err.message);
    btn.disabled = false;
  }
}

document.getElementById("audsess-list").addEventListener("click", (e) => {
  const pill = e.target.closest(".aud-binjump");
  if (pill) {
    jumpToBinAudit(pill.dataset.bin);
    return;
  }
  // Resume = the first bin in the list not walked yet (Nick, round 11).
  const res = e.target.closest(".audsess-resume");
  if (res) {
    const sn = audSessions.find((x) => x.id === Number(res.dataset.sid));
    const next = sn && (sn.items || []).find((i) => !i.done);
    if (next) jumpToBinAudit(next.key);
    return;
  }
  const fin = e.target.closest(".audsess-finish");
  if (fin) {
    audSessClose(Number(fin.dataset.sid), "finish", fin);
    return;
  }
  const ab = e.target.closest(".audsess-abandon");
  if (ab) {
    audSessClose(Number(ab.dataset.sid), "abandon", ab);
    return;
  }
  const open = e.target.closest(".audsess-open");
  if (!open) return;
  const sn = audSessions
    .concat(audSessDone)
    .find((x) => x.id === Number(open.dataset.sid));
  if (!sn) return;
  renderAuditSessionDetail(sn);
  audShowPane("session");
});

document.getElementById("audsess-finlist").addEventListener("click", (e) => {
  const open = e.target.closest(".audsess-open");
  if (!open) return;
  const sn = audSessDone.find((x) => x.id === Number(open.dataset.sid));
  if (!sn) return;
  renderAuditSessionDetail(sn);
  audShowPane("session");
});

document.getElementById("audsess-detail").addEventListener("click", async (e) => {
  const sku = e.target.closest(".ol-sku");
  if (sku) {
    openProductHistory(sku.dataset.sku);
    return;
  }
  const pill = e.target.closest(".aud-binjump");
  if (pill) {
    jumpToBinAudit(pill.dataset.bin);
    return;
  }
  const done = e.target.closest(".audsess-done");
  if (done && audSessOpenId !== null) {
    done.disabled = true;
    try {
      const s = await postJson(
        `/api/audit-sessions/${audSessOpenId}/items/${done.dataset.item}/done`,
        { done: done.dataset.done !== "1", worker: operatorEl.value || null }
      );
      renderAuditSessionDetail(s);
    } catch (err) {
      alert(err.message);
      done.disabled = false;
    }
  }
});

document.getElementById("audsess-toggle").addEventListener("click", () => {
  audSessShowDone = !audSessShowDone;
  renderAuditSessionsDone();
});

const audsessKindEl = document.getElementById("audsess-kind");
const audsessScopeEl = document.getElementById("audsess-scope");
// Packed-orders audits have no scope to type (Nick, 2026-09-14: the
// sweep IS the scope) - the input box leaves with that selection.
function syncAudsessKind() {
  const kind = audsessKindEl.value;
  audsessScopeEl.hidden = kind === "packed";
  audsessScopeEl.placeholder = kind === "bins"
    ? "Bins or rack prefix, e.g. I1"
    : "Vendor (blank = the whole queue)";
}
audsessKindEl.addEventListener("change", syncAudsessKind);
document.getElementById("audsess-newbtn").addEventListener("click", () => {
  const form = document.getElementById("audsess-new");
  form.hidden = !form.hidden;
  syncAudsessKind();
  if (!form.hidden && !audsessScopeEl.hidden) audsessScopeEl.focus();
});
document.getElementById("audsess-cancel").addEventListener("click", () => {
  document.getElementById("audsess-new").hidden = true;
});
document.getElementById("audsess-create").addEventListener("click", async (ev) => {
  const kind = audsessKindEl.value;
  const scope = audsessScopeEl.value.trim();
  // Packed orders is a one-shot audit, not a tracked session: Start
  // opens its pane with the recent sweeps ready to pick.
  if (kind === "packed") {
    document.getElementById("audsess-new").hidden = true;
    audShowPane("packed");
    packedOpen();
    return;
  }
  // No naming step (Nick, 2026-09-08): the audit IS its scope, so the
  // name derives from what the user specified - rack, bins or vendor.
  const payload = { kind, worker: operatorEl.value || null };
  if (kind === "bins") {
    // Tokens with a dash are bins ("I1-3"); a bare token is a rack prefix.
    const tokens = scope.split(",").map((t) => t.trim()).filter(Boolean);
    payload.bins = tokens.filter((t) => t.includes("-"));
    const rack = tokens.find((t) => !t.includes("-"));
    if (rack) payload.rack = rack.toUpperCase();
    if (!payload.bins.length && !payload.rack) {
      alert("Name at least one bin, or a rack prefix like I1.");
      return;
    }
    payload.name = payload.rack
      ? `Rack ${payload.rack}` +
        (payload.bins.length ? ` + ${payload.bins.join(", ")}` : "")
      : payload.bins.length === 1
        ? `Bin ${payload.bins[0]}`
        : `Bins ${payload.bins.join(", ")}`;
  } else {
    if (scope) payload.vendor = scope;
    payload.name = scope ? `1-left: ${scope}` : "1-left checks";
  }
  ev.currentTarget.disabled = true;
  try {
    const s = await postJson("/api/audit-sessions", payload);
    document.getElementById("audsess-new").hidden = true;
    audsessScopeEl.value = "";
    audSessShowDone = false;
    await loadAuditSessions();
    if (s.kind === "bins") {
      // Straight into the walk (round 11): first bin, expected list
      // ready even before any sweep.
      const next = (s.items || []).find((i) => !i.done);
      if (next) jumpToBinAudit(next.key);
    } else {
      renderAuditSessionDetail(s);
      audShowPane("session");
    }
  } catch (err) {
    alert(err.message);
  }
  ev.currentTarget && (ev.currentTarget.disabled = false);
  document.getElementById("audsess-create").disabled = false;
});

// === History tab ============================================================
let historyEvents = [];

async function loadHistory() {
  const body = document.getElementById("hist-body");
  try {
    const { events } = await apiJson("/api/history?limit=200");
    historyEvents = events;
    renderHistory();
  } catch (err) {
    body.innerHTML =
      '<tr><td colspan="7" class="inventory__empty">Could not load history.</td></tr>';
  }
}

function renderHistory() {
  const body = document.getElementById("hist-body");
  const q = document.getElementById("hist-search").value.trim().toLowerCase();
  const rows = q
    ? historyEvents.filter((e) =>
        [e.type, e.worker, e.sku, e.title, e.detail]
          .filter(Boolean)
          .some((v) => String(v).toLowerCase().includes(q))
      )
    : historyEvents;
  if (!rows.length) {
    body.innerHTML =
      '<tr><td colspan="7" class="inventory__empty">No events yet.</td></tr>';
    return;
  }
  body.innerHTML = rows
    .map((e, i) => {
      // Sweep events fold their EPCs behind an expander: the row reads
      // "4 × RFID tag", the tags themselves are one click away.
      const exp =
        e.epcs && e.epcs.length
          ? ` <a href="#" class="hist-exp" data-idx="${i}" data-n="${e.epcs.length}">▸ show EPCs</a>`
          : "";
      const sub =
        e.epcs && e.epcs.length
          ? `<tr class="hist-epcrow" data-for="${i}" hidden><td colspan="7"><div class="hist-epclist hist-epclist--grid">${e.epcs
              .map((x) => `<div class="mono">${escapeHtml(x || "?")}</div>`)
              .join("")}</div></td></tr>`
          : "";
      return `<tr>
      <td class="recent__meta u-nowrap">${escapeHtml(fmtWhen(e.at))}</td>
      <td>${evChip(e.type)}</td>
      <td>${escapeHtml(e.worker || "—")}</td>
      <td class="mono">${
        e.sku
          ? `<a href="#" class="hist-sku" data-sku="${escapeHtml(e.sku)}">${escapeHtml(e.sku)}</a>`
          : "—"
      }</td>
      <td>${
        e.sku && e.title
          ? `<span class="prodopen hist-prod" data-sku="${escapeHtml(e.sku)}">${escapeHtml(e.title)}</span>`
          : escapeHtml(e.title || "—")
      }</td>
      <td class="recent__meta">${escapeHtml(e.detail || "")}${exp}</td>
      <td>${
        e.undo
          ? `<button class="reset hist-undo" data-idx="${i}" type="button">Undo</button>`
          : ""
      }</td>
    </tr>${sub}`;
    })
    .join("");
  body.querySelectorAll(".hist-undo").forEach((btn) => {
    btn.addEventListener("click", () => undoHistoryEvent(rows[+btn.dataset.idx], btn));
  });
  body.querySelectorAll(".hist-exp").forEach((a) => {
    a.addEventListener("click", (ev) => {
      ev.preventDefault();
      const sub = body.querySelector(
        `tr.hist-epcrow[data-for="${a.dataset.idx}"]`
      );
      if (!sub) return;
      sub.hidden = !sub.hidden;
      a.textContent = sub.hidden ? "▸ show EPCs" : "▾ hide EPCs";
    });
  });
  body.querySelectorAll(".hist-sku, .hist-prod").forEach((a) => {
    a.addEventListener("click", (ev) => {
      ev.preventDefault();
      openProductHistory(a.dataset.sku);
    });
  });
}

// --- Per-product history: the full paper trail for one SKU/barcode, each
// event marked whether it touched Shopify or only this system. Counts are
// observations — nothing here writes stock numbers anywhere. Opens as a
// modal so it works from History AND Print queue; serialized products can
// edit their preferred label name here, and any product can print labels.
let phistData = null;

// The product panel's prints get their own session per open.
let phistPrintSession = null;

// Inline bin edit in the parent product window (Nick, 2026-09-01):
// click the bin, type, Enter saves (Escape cancels) - through the same
// audited /api/bin-updates every other bin write uses.
function phistEditBin(sku, span) {
  const inp = document.createElement("input");
  inp.className = "product__bin-input";
  inp.type = "text";
  inp.maxLength = 100;
  inp.autocomplete = "off";
  inp.spellcheck = false;
  inp.value = span.textContent === "—" ? "" : span.textContent;
  let closed = false;
  const done = async (commit) => {
    if (closed) return;
    closed = true;
    const v = inp.value.trim();
    inp.replaceWith(span);
    if (!commit || !v || v === span.textContent) return;
    try {
      await postJson("/api/bin-updates", {
        target: sku,
        bin: v,
        changed_by: operatorEl.value || null,
      });
      span.textContent = v;
      document.getElementById("phist-msg").textContent =
        `Bin set to ${v} ✓ - Shopify and the local records moved ` +
        `together. Undo lives in History.`;
    } catch (err) {
      alert(err.message);
    }
  };
  inp.addEventListener("keydown", (e) => {
    if (e.key === "Enter") done(true);
    else if (e.key === "Escape") done(false);
  });
  inp.addEventListener("blur", () => done(true));
  span.replaceWith(inp);
  inp.focus();
  inp.select();
}

async function openProductHistory(term) {
  phistPrintSession = makePrintSession();
  const overlay = document.getElementById("phist-overlay");
  const body = document.getElementById("phist-body");
  const termBox = document.getElementById("phist-term");
  if (termBox) termBox.value = term;
  overlay.hidden = false;
  phistData = null;
  document.getElementById("phist-msg").textContent = "";
  document.getElementById("phist-serial").hidden = true;
  body.innerHTML =
    '<tr><td colspan="5" class="inventory__empty">Loading…</td></tr>';
  try {
    const data = await apiJson(
      `/api/product-history?term=${encodeURIComponent(term)}`
    );
    phistData = data;
    const p = data.product;
    // Preferred-name editor for EVERY cataloged product: serialized brands
    // write through their serial record; everything else uses the per-SKU
    // label-name store. Blank = standard "Telescopes Canada" header.
    if (p) {
      document.getElementById("phist-serial").hidden = false;
      // Two-box prefill from what's SAVED — cancelling any edit leaves no
      // trace. Serialized products edit the top line through their serial
      // record and keep a standard SKU line, so that box locks for them.
      const serial = !!data.serial_prefix;
      phistDefaults = { top: STORE_HEADER, sku: data.sku || "" };
      let top = STORE_HEADER;
      let skuLine = data.sku || "";
      if (serial) {
        top = phistEffectiveName() || data.serial_label || "";
      } else if (data.custom_label) {
        if (data.custom_placement !== "sku") top = data.custom_label;
        if (data.custom_sku_text) skuLine = data.custom_sku_text;
        else if (
          data.custom_placement === "sku" ||
          data.custom_placement === "both"
        )
          skuLine = data.custom_label;
      }
      document.getElementById("phist-top").value = top;
      const skuBox = document.getElementById("phist-skuline");
      skuBox.value = skuLine;
      skuBox.disabled = serial;
      document.getElementById("phist-top-reset").hidden = serial;
      document.getElementById("phist-skuline-reset").hidden = serial;
      document.getElementById("phist-label-hint").textContent = serial
        ? "Serialized product - the top line is its item name, printed " +
          "on every label including Scan Station auto-prints. The SKU " +
          "line stays standard:"
        : "Edit the two label lines - saved store-wide, every future " +
          "print uses them. ✕ resets a line to its default:";
      updateLabelPreview();
    }
    renderNoScan(!!data.rfid_incompatible);
    renderNonTaggable(!!data.non_taggable);
    renderUnlabelable(!!data.unlabelable_box);
    renderMislabel(!!data.mislabel_flag);
    renderVendorRow();
    renderBundleRow();
    renderLocateRow();
    renderAliasesRow();
    // Multi-box/bundle standing. Only shown when an answer was actually
    // saved — an auto-detected product has nothing to undo.
    const kindBox = document.getElementById("phist-kind");
    const pk = data.product_kind;
    kindBox.hidden = !pk;
    if (pk) {
      const who = pk.updated_by ? ` by ${pk.updated_by}` : "";
      const when = pk.updated_at
        ? ` on ${tsDate(pk.updated_at).toLocaleString()}`
        : "";
      kindBox.classList.toggle("kindrow--bundle", pk.kind === "bundle");
      document.getElementById("phist-kind-what").textContent = pk.excluded
        ? `Dropped from the RFID system${who}${when} - it isn't seeded into ` +
          `new batches and never gets a label.`
        : pk.kind === "bundle"
          ? `Marked as a bundle${who}${when} - no labels print for it; its ` +
            `component products carry the tags.`
          : `Marked as a multi-box product${who}${when} - one label per box.`;
    }
    document.getElementById("phist-print").disabled = !p;
    // The title links to the product's Shopify admin page (Nick,
    // 2026-08-26) - every preview across the tabs opens through this
    // parent window, so they all get it.
    const titleEl = document.getElementById("phist-title");
    const titleText = p
      ? p.product_title + (p.variant_title ? ` (${p.variant_title})` : "")
      : `(not in the catalog) ${term}`;
    if (p && p.admin_url) {
      titleEl.innerHTML = "";
      const a = document.createElement("a");
      a.href = p.admin_url;
      a.target = "_blank";
      a.rel = "noopener";
      a.className = "phist-titlelink";
      a.title = "Open this product in Shopify admin";
      a.textContent = titleText;
      titleEl.append(a);
    } else {
      titleEl.textContent = titleText;
    }
    // The bin is editable RIGHT HERE (Nick, 2026-09-01) - same
    // click-to-edit the Scan Station card has, no Edit Product detour.
    // Writes through the audited /api/bin-updates (Shopify + local
    // records + open-batch snapshots in one commit).
    const metaEl = document.getElementById("phist-meta");
    metaEl.innerHTML = "";
    metaEl.append(
      document.createTextNode(
        `SKU: ${data.sku || "—"} · Barcode: ${data.barcode || "—"}`
      )
    );
    if (p) {
      metaEl.append(document.createTextNode(" · Bin: "));
      const binSpan = document.createElement("span");
      binSpan.className = "product__bin product__bin--edit";
      binSpan.title =
        "Click to set the bin - writes to Shopify and moves the " +
        "local records, no Edit Product needed";
      binSpan.textContent = p.bin_location || "—";
      const binSku = data.sku || p.sku;
      if (binSku) {
        binSpan.addEventListener("click", () =>
          phistEditBin(binSku, binSpan)
        );
      }
      metaEl.append(binSpan);
    }
    metaEl.append(
      document.createTextNode(
        ` · ${data.tag_count} tag(s) on file` +
          (data.on_hand != null ? ` · on-hand ${data.on_hand}` : "")
      )
    );
    // Multi-box units (Nick, 2026-09-02, the S11740): the durable mark
    // lives here - one unit, several cartons, one counting tag. Click
    // to set the box count and each box's own bin; labels then print
    // "BOX X OF Y" with that box's bin, and audits recognize the extra
    // cartons instead of flagging them as untagged stock.
    renderPhistTags(data, term);
    const img = document.getElementById("phist-img");
    if (data.image_url) {
      img.src = data.image_url;
      img.hidden = false;
    } else {
      img.hidden = true;
      img.removeAttribute("src");
    }
    if (!data.events.length) {
      body.innerHTML =
        '<tr><td colspan="5" class="inventory__empty">No recorded events for this product yet.</td></tr>';
      return;
    }
    // Multi-tag events expand into a FULL-WIDTH sub-row (colspan) —
    // opening one never resizes the table's columns, and the EPC list
    // spreads across all the empty space instead of squeezing into the
    // Detail column (Nick, 2026-08-18).
    body.innerHTML = data.events
      .map((e, i) => {
        const hasEpcs = e.epcs && e.epcs.length;
        const detailText = hasEpcs
          ? String(e.detail || "")
              .replace(/^\d+\s*×\s*RFID tag(\s*\(sweep\))?/, "")
              .replace(/^\s*·\s*/, "")
          : e.detail || "";
        const exp = hasEpcs
          ? `<a href="#" class="phist-exp" data-idx="${i}">▸ ${e.epcs.length}× EPC tags</a>${detailText ? " · " : ""}`
          : "";
        const sub = hasEpcs
          ? `<tr class="phist-epcrow" data-for="${i}" hidden><td colspan="5"><div class="hist-epclist hist-epclist--grid">${e.epcs
              .map((x) => `<div class="mono">${escapeHtml(x || "?")}</div>`)
              .join("")}</div></td></tr>`
          : "";
        return `<tr>
        <td class="recent__meta u-nowrap">${escapeHtml(fmtWhen(e.at))}</td>
        <td>${evChip(e.type)}</td>
        <td>${escapeHtml(e.worker || "—")}</td>
        <td class="recent__meta">${exp}${escapeHtml(detailText)}</td>
        <td>${
          e.shopify
            ? '<span class="chip-status chip-status--done" title="This event wrote to (or read from) the live Shopify store">Shopify ✓</span>'
            : '<span class="chip-status chip-status--pending" title="This event only touched the RFID system\'s own records - nothing in Shopify changed">RFID only</span>'
        }</td>
      </tr>${sub}`;
      })
      .join("");
    body.querySelectorAll(".phist-exp").forEach((a) => {
      a.addEventListener("click", (ev) => {
        ev.preventDefault();
        const sub = body.querySelector(
          `tr.phist-epcrow[data-for="${a.dataset.idx}"]`
        );
        if (!sub) return;
        sub.hidden = !sub.hidden;
        a.textContent = (sub.hidden ? "▸" : "▾") + a.textContent.slice(1);
      });
    });
  } catch (err) {
    body.innerHTML = `<tr><td colspan="5" class="inventory__empty">${escapeHtml(err.message)}</td></tr>`;
  }
}

// Live tag list with a manual unpair per row — for the tag that fell off
// or never read and whose sticker is gone, so there's nothing to scan and
// (with a single unit) no audit to run (Nick, 2026-08-25). Retires the
// record as dead: tombstone kept, History row with one-click undo,
// Shopify never touched.
function renderPhistTags(data, term) {
  const wrap = document.getElementById("phist-tagswrap");
  const toggle = document.getElementById("phist-tags-toggle");
  const list = document.getElementById("phist-tags");
  const tags = data.tags || [];
  const sold = data.sold_tags || [];
  wrap.hidden = !tags.length && !sold.length;
  list.hidden = true;
  if (!tags.length && !sold.length) return;
  toggle.textContent =
    `▸ ${tags.length} live tag(s)` +
    (sold.length ? ` · ${sold.length} presumed sold` : "") +
    ": view or unpair";
  toggle.onclick = (ev) => {
    ev.preventDefault();
    list.hidden = !list.hidden;
    toggle.textContent =
      (list.hidden ? "▸" : "▾") + toggle.textContent.slice(1);
  };
  const fmtDay = (iso) =>
    iso
      ? tsDate(iso).toLocaleDateString(undefined, { dateStyle: "medium" })
      : "unknown date";
  list.innerHTML = tags
    .map((t) => {
      const meta =
        `${t.bin || "no bin"} · paired ${fmtDay(t.assigned_at)}` +
        (t.assigned_by ? ` by ${t.assigned_by}` : "") +
        (t.case_units ? ` · case of ${t.case_units}` : "");
      return `<div class="phist-tagrow">
        <span class="mono">${escapeHtml(t.epc)}</span>
        <span class="recent__meta">${escapeHtml(meta)}</span>
        <button class="reset phist-unpair" type="button"
          data-epc="${escapeHtml(t.epc)}"
          title="The sticker is gone or dead and can't be scanned. Retires this tag record (undo in History)">Unpair…</button>
      </div>`;
    })
    .join("")
    // Presumed-sold tombstones close the timeline (Nick, 2026-08-26):
    // nothing to unpair - the box left with the tag on it.
    + sold
      .map(
        (t) => `<div class="phist-tagrow phist-tagrow--sold">
        <span class="mono">${escapeHtml(t.epc)}</span>
        <span class="recent__meta">${escapeHtml(
          `${t.bin || "no bin"} · presumed sold ${fmtDay(t.retired_at)}` +
            (t.retired_by ? ` by ${t.retired_by}` : "")
        )}</span>
      </div>`
      )
      .join("");
  list.querySelectorAll(".phist-unpair").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const epc = btn.dataset.epc;
      if (
        !confirm(
          `Unpair tag ${epc}?\n\n` +
            `Only do this when the sticker is physically gone or dead - ` +
            `it fell off, was damaged, or never reads - so there is ` +
            `nothing left to scan. If a dead tag is still ON the box, ` +
            `use the batch check step's replace-tag flow instead so the ` +
            `box gets a fresh label.\n\n` +
            `The record is retired with a permanent tombstone (a future ` +
            `sweep hearing this EPC will name it). Shopify is not ` +
            `touched; the box counts as an untagged unit until a future ` +
            `batch re-tags it. Undo lives in History.`
        )
      )
        return;
      btn.disabled = true;
      try {
        await postJson("/api/assignments/retire", {
          epcs: [epc],
          kind: "dead",
          changed_by: operatorEl.value || null,
          note: "manual unpair, Inventory tab",
        });
        await openProductHistory(term);
        loadInventory();
      } catch (err) {
        alert(`Unpair failed: ${err.message}`);
        btn.disabled = false;
      }
    });
  });
}

document.getElementById("phist-open").addEventListener("click", () => {
  const term = document.getElementById("phist-term").value.trim();
  if (term) openProductHistory(term);
});
document.getElementById("phist-term").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const term = e.target.value.trim();
    if (term) openProductHistory(term);
  }
});
function closePhist() {
  // A docked edit window goes home first (closeLinkbox handles the move).
  const dock = document.getElementById("phist-editdock");
  if (dock && !dock.hidden) closeLinkbox();
  document.getElementById("phist-overlay").hidden = true;
}
document.getElementById("phist-close").addEventListener("click", closePhist);
document.getElementById("phist-overlay").addEventListener("click", (e) => {
  if (e.target.id === "phist-overlay") closePhist();
});
document.getElementById("phist-edit").addEventListener("click", phistOpenEdit);

// Defaults for the panel's two label boxes, captured per product on open.
let phistDefaults = { top: "Telescopes Canada", sku: "" };

function phistEffectiveName() {
  if (!phistData) return null;
  if (phistData.serial_prefix)
    return phistData.serial_label_saved ? phistData.serial_label : null;
  return phistData.custom_label;
}

// Miniature sticker mirrors the agent's real layout, including the
// smaller font tiers long names trigger — plus the same fit check the
// reprint dialog runs, so bad text is flagged (never blocked) here too.
function updateLabelPreview() {
  if (!phistData) return;
  const p = phistData.product || {};
  const top =
    document.getElementById("phist-top").value.trim() || STORE_HEADER;
  const skuLine =
    document.getElementById("phist-skuline").value.trim() ||
    phistDefaults.sku;
  const el = document.getElementById("phist-prev-header");
  setPreviewHeader(el, top, top === STORE_HEADER);
  renderSkuPreviewLine("phist-prev-sku", skuLine);
  document.getElementById("phist-prev-bc").textContent =
    p.barcode || p.sku || phistData.barcode || "";
  document.getElementById("phist-prev-bin").textContent =
    "BIN: " + (p.bin_location || "—");
  renderFitWarn(
    document.getElementById("phist-fitwarn"),
    top, skuLine, p.barcode || p.sku || phistData.barcode || ""
  );
}

document
  .getElementById("phist-top")
  .addEventListener("input", updateLabelPreview);
document
  .getElementById("phist-skuline")
  .addEventListener("input", updateLabelPreview);
document.getElementById("phist-top-reset").addEventListener("click", () => {
  document.getElementById("phist-top").value = phistDefaults.top;
  updateLabelPreview();
});
document
  .getElementById("phist-skuline-reset")
  .addEventListener("click", () => {
    document.getElementById("phist-skuline").value = phistDefaults.sku;
    updateLabelPreview();
  });

// The flag chips at the top of the window: any product preview built on
// this panel shows its active flags right under SKU/Barcode (Nick,
// 2026-08-26), matching the highlighted buttons in the Flags group.
function renderFlagChips() {
  const box = document.getElementById("phist-flagchips");
  if (!phistData) {
    box.hidden = true;
    return;
  }
  const chips = [];
  if (phistData.rfid_incompatible)
    chips.push(
      `<span class="flagchip" title="Sweeps don't expect this product's tags to answer while on the box">⊘ won't RFID scan</span>`
    );
  if (phistData.non_taggable)
    chips.push(
      `<span class="flagchip" title="Outside the RFID system: no batches, no labels, audits skip it">🚫 non-taggable</span>`
    );
  if (phistData.unlabelable_box)
    chips.push(
      `<span class="flagchip" title="Assorted box: ONE location label on the box, tags never counted, on-hand stays real and updatable">📦 un-labelable box</span>`
    );
  if (phistData.mislabel_flag)
    chips.push(
      `<span class="flagchip" title="Previous vendor labels are known to carry the wrong barcode - every scan warns to check the physical product">🏷 vendor mis-label</span>`
    );
  box.innerHTML = chips.join("");
  box.hidden = !chips.length;
}

// Won't-RFID-scan flag: add OR remove, always offered for a cataloged
// product. Labels still print and pairing still counts — sweeps just stop
// expecting an answer. Every flip is logged.
function renderNoScan(flagged) {
  const row = document.getElementById("phist-norfid");
  if (!phistData || !phistData.product || !phistData.sku) {
    row.hidden = true;
    renderFlagChips();
    return;
  }
  row.hidden = false;
  phistData.rfid_incompatible = flagged;
  // State + explanation live in the button's hover text now — the row
  // itself stays one compact line (Nick, 2026-08-18).
  const btn = document.getElementById("phist-norfid-btn");
  btn.classList.toggle("optflag--on", flagged);
  btn.textContent = flagged ? "⊘ Remove won't-scan flag" : "Flag: won't RFID scan";
  btn.title = flagged
    ? "Flagged: this product's tag won't scan while on the box, so " +
      "sweeps and Verify don't expect it to answer. Click to remove " +
      "the flag."
    : "Sweeps currently expect this product's tags to answer. If a tag " +
      "reads fine in hand but never on the box, flag it so sweeps stop " +
      "counting it as missing.";
  updateFlagGroupVisibility();
  renderFlagChips();
}

// One rule for the whole Flags fieldset: visible while ANY flag row is.
function updateFlagGroupVisibility() {
  document.getElementById("phist-flags").hidden = [
    "phist-norfid", "phist-notag", "phist-unbox", "phist-mislabel",
  ].every((id) => document.getElementById(id).hidden);
}

// Bundle contents on the product panel — the standing record behind
// "63 W9184B covers the bundle-of-10 and bundle-of-5 listings". Defined
// bundles leave batch collect's countable list; the could-not-scan desk
// flow offers their components.
async function renderBundleRow() {
  const row = document.getElementById("phist-bundle");
  if (!phistData || !phistData.sku) {
    row.hidden = true;
    return;
  }
  row.hidden = false;
  const btn = document.getElementById("phist-bundle-btn");
  btn.textContent = "…";
  try {
    const r = await apiJson(
      `/api/bundle-contents?sku=${encodeURIComponent(phistData.sku)}`
    );
    const contents = r.contents || [];
    phistData.bundle_contents = contents;
    const importBtn = document.getElementById("phist-bundle-import");
    if (contents.length) {
      const parts = contents
        .map((c) => `${c.qty}× ${c.component_sku}`)
        .join(" + ");
      btn.textContent = "📦 Edit contents…";
      btn.title =
        `Defined bundle: one unit = ${parts}. Batch collect counts the ` +
        `components instead of this SKU. Click to edit or clear.`;
      importBtn.hidden = true;
    } else {
      btn.textContent = "📦 Define by hand…";
      btn.title =
        "Sold as a bundle of other products? Define what one unit " +
        "contains and batch collect stops counting it separately - the " +
        "components carry the tags.";
      importBtn.hidden = false;
    }
  } catch (err) {
    row.hidden = true;
  }
}

document
  .getElementById("phist-bundle-import")
  .addEventListener("click", async () => {
    if (!phistData || !phistData.sku) return;
    const msg = document.getElementById("phist-msg");
    const btn = document.getElementById("phist-bundle-import");
    btn.disabled = true;
    msg.textContent = "Asking Shopify for the bundle's components…";
    try {
      const r = await postJson("/api/bundle-contents/import", {
        sku: phistData.sku,
        updated_by: operatorEl.value || null,
      });
      msg.textContent = r.message + " (imported from Shopify)";
      renderBundleRow();
    } catch (err) {
      msg.textContent = err.message;
    } finally {
      btn.disabled = false;
    }
  });

document
  .getElementById("phist-bundle-btn")
  .addEventListener("click", async () => {
    if (!phistData || !phistData.sku) return;
    const existing = (phistData.bundle_contents || [])
      .map((c) => `${c.component_sku} x ${c.qty}`)
      .join(", ");
    const raw = prompt(
      `What does ONE unit of ${phistData.sku} contain?\n\n` +
        `Write each piece as SKU x QTY, separated by commas - e.g.\n` +
        `W9184B x 10   or   51701-1 x 3, 51701-2 x 1\n\n` +
        `(Leave empty and press OK to clear - the bundle becomes ` +
        `countable again.)`,
      existing
    );
    if (raw === null) return;
    const contents = [];
    for (const part of raw.split(",")) {
      if (!part.trim()) continue;
      const m = /^(.+?)\s*[x×]\s*(\d+)$/i.exec(part.trim());
      if (!m) {
        alert(
          `Couldn't read "${part.trim()}" - write each piece as SKU x QTY.`
        );
        return;
      }
      contents.push({ component_sku: m[1].trim(), qty: Number(m[2]) });
    }
    const msg = document.getElementById("phist-msg");
    try {
      const r = await postJson("/api/bundle-contents", {
        bundle_sku: phistData.sku,
        contents,
        updated_by: operatorEl.value || null,
      });
      msg.textContent = r.message;
      renderBundleRow();
    } catch (err) {
      msg.textContent = err.message;
    }
  });

document
  .getElementById("phist-norfid-btn")
  .addEventListener("click", async () => {
    if (!phistData || !phistData.sku) return;
    const want = !phistData.rfid_incompatible;
    const msg = document.getElementById("phist-msg");
    // Every Can't Scan option confirms with its full meaning before
    // applying (Nick, 2026-09-08) - each points at a different flag.
    if (
      want &&
      !confirm(
        `Flag ${phistData.sku} as WON'T RFID SCAN?\n\n` +
          `For products whose tag reads fine in hand but never while ` +
          `on the box (foil bags, dense glass):\n` +
          `- Labels still print and pairing still counts every unit.\n` +
          `- Sweeps and Verify just stop expecting the tags to ` +
          `answer, so they're never reported missing.\n\n` +
          `Undo any time with this same button (History keeps the ` +
          `record).`
      )
    )
      return;
    try {
      await apiJson(
        `/api/products/${encodeURIComponent(phistData.sku)}/rfid-incompatible`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            incompatible: want,
            changed_by: operatorEl.value || null,
          }),
        }
      );
      renderNoScan(want);
      msg.textContent = want
        ? "Flagged ⊘ - logged; sweeps stop expecting this product to answer."
        : "Flag removed ✓ - logged; sweeps expect it again.";
    } catch (err) {
      msg.textContent = err.message;
    }
  });

// Non-taggable: the thumbscrew bin. Stronger than the won't-scan flag -
// the product leaves the RFID system entirely (never seeded into
// batches, never labelled, skipped by audits). One hand-paired tag can
// still act as a bag marker so Locate finds the container.
function renderNonTaggable(flagged) {
  const row = document.getElementById("phist-notag");
  if (!phistData || !phistData.sku) {
    row.hidden = true;
    renderFlagChips();
    return;
  }
  row.hidden = false;
  phistData.non_taggable = flagged;
  const btn = document.getElementById("phist-notag-btn");
  btn.classList.toggle("optflag--on", flagged);
  btn.textContent = flagged
    ? "🚫 Put back in the RFID system"
    : "Flag: non-taggable";
  btn.title = flagged
    ? "Marked non-taggable: not seeded into batches, no labels, audits " +
      "skip it. Click to bring it back into the RFID system."
    : "For products not worth individual tags (a bin of 500 loose " +
      "thumbscrews): drops it from batches, labels and audits. You can " +
      "still pair ONE tag by hand as a bag marker and find it with " +
      "Locate.";
  updateFlagGroupVisibility();
  renderFlagChips();
}

// Box of Un-Labelable Product (Nick, 2026-09-08): the CR2032 assorted
// box. Between won't-scan and non-taggable in strength - the BOX gets
// ONE label + a bin for location/clarity, on-hand stays real and
// updatable, but per-unit tags never count anywhere.
function renderUnlabelable(flagged) {
  const row = document.getElementById("phist-unbox");
  const printRow = document.getElementById("phist-unbox-print");
  if (!phistData || !phistData.sku) {
    row.hidden = true;
    printRow.hidden = true;
    renderFlagChips();
    return;
  }
  row.hidden = false;
  printRow.hidden = !flagged;
  phistData.unlabelable_box = flagged;
  const btn = document.getElementById("phist-unbox-btn");
  btn.classList.toggle("optflag--on", flagged);
  btn.textContent = flagged
    ? "📦 Remove un-labelable box flag"
    : "Flag: box of un-labelable product";
  btn.title = flagged
    ? "Flagged as an un-labelable box: ONE label on the box for " +
      "location, tags never counted, on-hand still shows and can be " +
      "updated. Click to return it to normal per-unit tagging."
    : "An assorted box of product (100 loose CR2032s): the box itself " +
      "gets a label and a bin, but individual tags are never counted. " +
      "On-hand stays real and updatable.";
  updateFlagGroupVisibility();
  renderFlagChips();
}

document
  .getElementById("phist-unbox-btn")
  .addEventListener("click", async () => {
    if (!phistData || !phistData.sku) return;
    const want = !phistData.unlabelable_box;
    const msg = document.getElementById("phist-msg");
    if (
      want &&
      !confirm(
        `Mark ${phistData.sku} as a BOX OF UN-LABELABLE PRODUCT?\n\n` +
          `For an assorted box (100 loose CR2032 batteries, a bin of ` +
          `thumbscrews):\n` +
          `- The BOX gets ONE label and a bin, for location and ` +
          `clarity. Print it from the button that appears below.\n` +
          `- On-hand still shows everywhere and can still be raised ` +
          `or corrected.\n` +
          `- Individual RFID tags are NEVER counted: no batch ` +
          `collect, no per-unit labels, no tags-vs-on-hand math.\n\n` +
          `Undo any time with this same button (History keeps the ` +
          `record).`
      )
    )
      return;
    try {
      const r = await apiJson(
        `/api/products/${encodeURIComponent(phistData.sku)}/unlabelable-box`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            flagged: want,
            changed_by: operatorEl.value || null,
          }),
        }
      );
      renderUnlabelable(want);
      if (want) phistData.non_taggable = false;
      renderNonTaggable(!!phistData.non_taggable);
      msg.textContent = r.message;
    } catch (err) {
      msg.textContent = err.message;
    }
  });

document
  .getElementById("phist-unbox-print-btn")
  .addEventListener("click", async () => {
    if (!phistData || !phistData.sku) return;
    const msg = document.getElementById("phist-msg");
    if (
      !confirm(
        `Print ${phistData.sku}'s ONE box label?\n\n` +
          `It queues like any label (home bin printed on it) - pair ` +
          `it to the box as usual, and the tag marks the location ` +
          `without ever being counted.`
      )
    )
      return;
    try {
      const r = await apiJson(
        `/api/products/${encodeURIComponent(phistData.sku)}/box-label`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ changed_by: operatorEl.value || null }),
        }
      );
      msg.textContent = r.message;
    } catch (err) {
      msg.textContent = err.message;
    }
  });

// Vendor mis-label warning (Nick, 2026-09-08): the vendor printed the
// WRONG barcode on this product's boxes (EXOS2CWB5's barcode on
// EXOS2CW 10lb), so a scan can resolve to the wrong variant. Flagged
// products warn LOUDLY on every scan, everywhere - the warning rides
// the scan-note channel, so the C72 shows it too with no app update.
function renderMislabel(flagged) {
  const row = document.getElementById("phist-mislabel");
  if (!phistData || !phistData.sku) {
    row.hidden = true;
    renderFlagChips();
    return;
  }
  row.hidden = false;
  phistData.mislabel_flag = flagged;
  const btn = document.getElementById("phist-mislabel-btn");
  btn.classList.toggle("optflag--on", flagged);
  btn.textContent = flagged
    ? "🏷 Mis-label warning ON - manage…"
    : "Flag: vendor labels mis-labeled";
  btn.title = flagged
    ? "Flagged: previous vendor labels for this product are known to " +
      "carry the wrong barcode - every scan warns and (with products " +
      "listed) offers the which-is-it picker. Click to manage the " +
      "picker list or remove the warning."
    : "The vendor printed another product's barcode on this one's " +
      "boxes? Flag it and every scan (Scan Station AND the C72) warns " +
      "to check the physical product; add the products the label " +
      "might actually be and scans offer a picker.";
  renderFlagChips();
}

document
  .getElementById("phist-mislabel-btn")
  .addEventListener("click", async () => {
    if (!phistData || !phistData.sku) return;
    const msg = document.getElementById("phist-msg");
    if (phistData.mislabel_flag) {
      // Already on: manage the picker list (removal lives in there).
      openMislabelManager(phistData.sku);
      return;
    }
    if (
      !confirm(
        `Flag ${phistData.sku} as VENDOR MIS-LABELED?\n\n` +
          `For products whose previous vendor labels carry the WRONG ` +
          `barcode:\n` +
          `- Every scan (Scan Station AND the C72) warns to check the ` +
          `physical product.\n` +
          `- Add the products the label might actually be, and scans ` +
          `offer a picker with product previews instead of trusting ` +
          `the barcode.\n\nThe manager window opens next so you can ` +
          `add those products.`
      )
    )
      return;
    try {
      const r = await apiJson(
        `/api/products/${encodeURIComponent(phistData.sku)}/mislabel-flag`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            flagged: true,
            changed_by: operatorEl.value || null,
          }),
        }
      );
      renderMislabel(true);
      msg.textContent = r.message;
      openMislabelManager(phistData.sku);
    } catch (err) {
      msg.textContent = err.message;
    }
  });

// --- Mis-label overlay windows (manager + scan-time picker) ----------------
// Self-contained overlay DOM: nothing in index.html to keep in sync.

function mlOverlay(titleText) {
  // The site's own modal shell (same as the event-colour editor), so
  // these windows match the rest of the terminal (Nick, 2026-09-08).
  const wrap = document.createElement("div");
  wrap.className = "phist-overlay";
  const box = document.createElement("section");
  box.className = "linkbox serialbox phist-modal phist-modal--w640";
  const h = document.createElement("div");
  h.className = "linkbox__title";
  h.textContent = titleText;
  box.appendChild(h);
  wrap.appendChild(box);
  wrap.addEventListener("click", (e) => {
    if (e.target === wrap) wrap.remove();
  });
  document.body.appendChild(wrap);
  return { wrap, box };
}

function mlProductCard(opt, actionLabel, onAction, onRemove) {
  const card = document.createElement("div");
  card.className = "mlrow";
  const img = document.createElement("img");
  img.src = opt.image_url || "";
  img.alt = "";
  img.className = opt.image_url ? "mlrow__img" : "mlrow__img mlrow__img--none";
  card.appendChild(img);
  const col = document.createElement("div");
  col.className = "mlrow__main";
  const nm = document.createElement("div");
  nm.textContent = opt.product_title || opt.sku;
  nm.className = "mlrow__name";
  col.appendChild(nm);
  const meta = document.createElement("div");
  meta.className = "mlrow__meta";
  meta.textContent =
    `SKU ${opt.sku}` +
    (opt.barcode ? ` · barcode ${opt.barcode}` : "") +
    (opt.bin_location ? ` · bin ${opt.bin_location}` : "") +
    (opt.flagged ? " · the flagged product" : "");
  col.appendChild(meta);
  card.appendChild(col);
  if (onAction) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "reset";
    btn.textContent = actionLabel;
    btn.addEventListener("click", onAction);
    card.appendChild(btn);
  }
  if (onRemove) {
    const x = document.createElement("button");
    x.type = "button";
    x.className = "reset";
    x.textContent = "✕";
    x.title = "Remove from the picker list";
    x.addEventListener("click", onRemove);
    card.appendChild(x);
  }
  return card;
}

// The flag's manager: the picker list with previews, add-by-code, and
// the remove-warning-entirely button (Nick, 2026-09-08).
async function openMislabelManager(sku) {
  const { wrap, box } = mlOverlay(`Mis-label picker list for ${sku}`);
  const intro = document.createElement("p");
  intro.className = "linkbox__text";
  intro.textContent =
    "When a scan resolves to this product, these are offered as " +
    '"which product is this really?". Add every product the ' +
    "vendor's label might actually be.";
  box.appendChild(intro);
  const list = document.createElement("div");
  box.appendChild(list);

  const addRow = document.createElement("div");
  addRow.className = "linkbox__form u-my10";
  const input = document.createElement("input");
  input.className = "linkbox__input";
  input.placeholder = "Barcode or SKU of another product…";
  const addBtn = document.createElement("button");
  addBtn.type = "button";
  addBtn.className = "reset";
  addBtn.textContent = "Add product";
  addRow.appendChild(input);
  addRow.appendChild(addBtn);
  box.appendChild(addRow);

  const foot = document.createElement("div");
  foot.className = "linkbox__actions linkbox__actions--split";
  const unflagBtn = document.createElement("button");
  unflagBtn.type = "button";
  unflagBtn.className = "reset";
  unflagBtn.textContent = "Remove warning entirely";
  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "reset";
  closeBtn.textContent = "Close";
  closeBtn.addEventListener("click", () => wrap.remove());
  foot.appendChild(unflagBtn);
  foot.appendChild(closeBtn);
  box.appendChild(foot);

  async function refresh() {
    let d;
    try {
      d = await apiJson(
        `/api/products/${encodeURIComponent(sku)}/mislabel-flag`
      );
    } catch (err) {
      list.textContent = err.message;
      return;
    }
    list.innerHTML = "";
    const opts = (d.options || []).filter((o) => !o.flagged);
    if (!opts.length) {
      const empty = document.createElement("p");
      empty.className = "linkbox__text";
      empty.textContent =
        "No products listed yet - scans show the text warning only. " +
        "Add the product(s) this label might actually be to turn on " +
        "the picker.";
      list.appendChild(empty);
    }
    opts.forEach((o) => {
      list.appendChild(
        mlProductCard(o, null, null, async () => {
          try {
            await apiJson(
              `/api/products/${encodeURIComponent(sku)}` +
                `/mislabel-alternates/${encodeURIComponent(o.sku)}` +
                `?by=${encodeURIComponent(operatorEl.value || "")}`,
              { method: "DELETE" }
            );
            refresh();
          } catch (err) {
            alert(err.message);
          }
        })
      );
    });
  }

  addBtn.addEventListener("click", async () => {
    const code = input.value.trim();
    if (!code) return;
    addBtn.disabled = true;
    try {
      await apiJson(
        `/api/products/${encodeURIComponent(sku)}/mislabel-alternates`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            code,
            changed_by: operatorEl.value || null,
          }),
        }
      );
      input.value = "";
      refresh();
    } catch (err) {
      alert(err.message);
    } finally {
      addBtn.disabled = false;
    }
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") addBtn.click();
  });

  unflagBtn.addEventListener("click", async () => {
    if (
      !confirm(
        `Remove the mis-label warning from ${sku}?\n\nScans stop ` +
          `warning and the picker list is forgotten.`
      )
    )
      return;
    try {
      await apiJson(
        `/api/products/${encodeURIComponent(sku)}/mislabel-flag`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            flagged: false,
            changed_by: operatorEl.value || null,
          }),
        }
      );
      if (phistData && phistData.sku === sku) renderMislabel(false);
      wrap.remove();
    } catch (err) {
      alert(err.message);
    }
  });

  refresh();
}

// Scan-time picker: a mis-label-flagged product with listed options
// asks "which product is this really?" before the station accepts it.
function openMislabelPicker(product, onPick) {
  const { wrap, box } = mlOverlay("Vendor mis-label: which product is this?");
  const intro = document.createElement("p");
  intro.className = "linkbox__text";
  intro.textContent =
    "Previous vendor labels for this product are known to carry the " +
    "wrong barcode. Check the physical box and pick what's actually " +
    "in your hand:";
  box.appendChild(intro);
  (product.mislabel_options || []).forEach((o) => {
    box.appendChild(
      mlProductCard(o, "It's this one", () => {
        wrap.remove();
        onPick(o);
      })
    );
  });
  const addBtn = document.createElement("button");
  addBtn.type = "button";
  addBtn.className = "reset u-mt6";
  addBtn.textContent = "It's a different product - add it to this list…";
  addBtn.addEventListener("click", async () => {
    const code = prompt(
      "Barcode or SKU of the product actually in your hand:"
    );
    if (!code || !code.trim()) return;
    try {
      const r = await apiJson(
        `/api/products/${encodeURIComponent(product.sku)}` +
          `/mislabel-alternates`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            code: code.trim(),
            changed_by: operatorEl.value || null,
          }),
        }
      );
      wrap.remove();
      onPick(
        (r.options || []).find(
          (o) => o.sku.toUpperCase() === r.added.sku.toUpperCase()
        ) || { sku: r.added.sku }
      );
    } catch (err) {
      alert(err.message);
    }
  });
  box.appendChild(addBtn);
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "reset mlpick__cancel";
  cancel.textContent = "Cancel scan";
  cancel.addEventListener("click", () => wrap.remove());
  box.appendChild(cancel);
}

// Change vendor (Nick, 2026-08-26): a PRODUCT-level Shopify write, so
// every variant changes brand together. Audited like the SKU/barcode
// overwrites; reverse by running it again with the old name.
function renderVendorRow() {
  const row = document.getElementById("phist-vendor");
  if (!phistData || !phistData.product || !phistData.sku) {
    row.hidden = true;
    return;
  }
  row.hidden = false;
  const btn = document.getElementById("phist-vendor-btn");
  btn.textContent = phistData.vendor
    ? `🏷 Vendor: ${phistData.vendor} - change…`
    : "🏷 Set vendor…";
  btn.title =
    "Writes a new vendor (brand) to the product in Shopify - every " +
    "variant follows. Logged to History.";
}

document
  .getElementById("phist-vendor-btn")
  .addEventListener("click", async () => {
    if (!phistData || !phistData.sku) return;
    const msg = document.getElementById("phist-msg");
    const current = phistData.vendor || "";
    const raw = prompt(
      `Vendor for ${phistData.sku}\n\nThis writes to the product in ` +
        `Shopify - every variant of the product changes brand together. ` +
        `Logged to History; run it again with the old name to reverse.`,
      current
    );
    if (raw === null) return;
    const vendor = raw.trim();
    if (!vendor || vendor === current) return;
    if (
      !confirm(
        `Set the vendor of ${phistData.sku} to "${vendor}"` +
          (current ? ` (currently "${current}")` : "") +
          `?\n\nThis WRITES to Shopify.`
      )
    )
      return;
    try {
      const res = await postJson("/api/vendor-overwrites", {
        target: phistData.sku,
        new_vendor: vendor,
        changed_by: operatorEl.value || null,
        confirmed: true,
      });
      phistData.vendor = vendor;
      renderVendorRow();
      msg.textContent = res.message;
    } catch (err) {
      msg.textContent = err.message;
    }
  });

document
  .getElementById("phist-notag-btn")
  .addEventListener("click", async () => {
    if (!phistData || !phistData.sku) return;
    const want = !phistData.non_taggable;
    const msg = document.getElementById("phist-msg");
    if (
      want &&
      !confirm(
        `Mark ${phistData.sku} as non-taggable?\n\n` +
          `It leaves the RFID system: never seeded into batch tagging, ` +
          `no labels print for it, and the audit tab skips it. ` +
          `Optionally pair ONE tag to it by hand as a bag marker - ` +
          `Locate can find the container, and the marker counts as ` +
          `nothing.\n\nUndo any time with this same button (History ` +
          `keeps the record).`
      )
    )
      return;
    try {
      await apiJson(
        `/api/products/${encodeURIComponent(phistData.sku)}/non-taggable`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            non_taggable: want,
            changed_by: operatorEl.value || null,
          }),
        }
      );
      renderNonTaggable(want);
      if (want) {
        // The two kinds share one standing - flipping this one on
        // switches the other off (the server stores a single row).
        phistData.unlabelable_box = false;
        renderUnlabelable(false);
      }
      msg.textContent = want
        ? "Marked non-taggable 🚫 - logged; batches and audits skip it now."
        : "Back in the RFID system ✓ - logged; batches and audits count it again.";
    } catch (err) {
      msg.textContent = err.message;
    }
  });

// --- Linked barcodes (Nick, 2026-09-08, the S11810-1 mis-link) --------------
// Every alias in one place: what code points at what product, who
// linked it and when, and the unlink. The row shows THIS product's
// links; the manager can widen to the whole store.
async function renderAliasesRow() {
  const row = document.getElementById("phist-aliases");
  if (!phistData || !phistData.sku) {
    row.hidden = true;
    return;
  }
  row.hidden = false;
  const btn = document.getElementById("phist-aliases-btn");
  btn.textContent = "🔗 Linked barcodes…";
  btn.title =
    "Foreign codes linked to resolve to this product (and store-wide) " +
    "- who linked each, when, and the unlink.";
  try {
    const d = await apiJson(
      `/api/barcode-aliases?sku=${encodeURIComponent(phistData.sku)}`
    );
    btn.textContent = d.count
      ? `🔗 Linked barcodes (${d.count})…`
      : "🔗 Linked barcodes…";
  } catch (err) {
    /* count is decoration - the button still opens the manager */
  }
}

document
  .getElementById("phist-aliases-btn")
  .addEventListener("click", () => {
    if (phistData && phistData.sku) openAliasManager(phistData.sku);
  });

function openAliasManager(sku) {
  const { wrap, box } = mlOverlay(
    sku ? `Linked barcodes for ${sku}` : "Every linked barcode"
  );
  const intro = document.createElement("p");
  intro.className = "linkbox__text";
  intro.textContent =
    "A linked (aliased) code resolves to its product on every scan - " +
    "and WINS over box-set parts and other lookups, so a wrong link " +
    "shadows everything. Unlinking is History-logged.";
  box.appendChild(intro);
  const list = document.createElement("div");
  box.appendChild(list);
  const foot = document.createElement("div");
  foot.className = "linkbox__actions linkbox__actions--split";
  const scopeBtn = document.createElement("button");
  scopeBtn.type = "button";
  scopeBtn.className = "reset";
  scopeBtn.textContent = sku
    ? "Show every product's links"
    : "Close";
  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "reset";
  closeBtn.textContent = "Close";
  closeBtn.addEventListener("click", () => wrap.remove());
  foot.appendChild(scopeBtn);
  if (sku) foot.appendChild(closeBtn);
  box.appendChild(foot);
  scopeBtn.addEventListener("click", () => {
    wrap.remove();
    if (sku) openAliasManager(null);
  });

  async function refresh() {
    let d;
    try {
      d = await apiJson(
        "/api/barcode-aliases" +
          (sku ? `?sku=${encodeURIComponent(sku)}` : "")
      );
    } catch (err) {
      list.textContent = err.message;
      return;
    }
    list.innerHTML = "";
    if (!d.count) {
      const empty = document.createElement("p");
      empty.className = "linkbox__text";
      empty.textContent = sku
        ? "No barcodes are linked to this product."
        : "No linked barcodes anywhere.";
      list.appendChild(empty);
      return;
    }
    d.aliases.forEach((a) => {
      const row = document.createElement("div");
      row.className = "mlrow";
      const col = document.createElement("div");
      col.className = "mlrow__main";
      const kindNote =
        a.kind === "label"
          ? " · from a saved label line (auto-replaced on edits)"
          : a.kind === "nickname"
            ? " · vendor nickname"
            : "";
      col.innerHTML =
        `<b class="mono">${escapeHtml(a.alias_barcode)}</b> → ` +
        `${escapeHtml(a.product_title || a.sku || a.barcode || "?")} ` +
        `<span class="mono u-dim75">${escapeHtml(a.sku || "")}</span>` +
        `<div class="mlrow__meta">linked ${a.created_at ? fmtAgo(a.created_at) : "—"}` +
        `${a.created_by ? " by " + escapeHtml(a.created_by) : ""}` +
        `${a.created_at ? " (" + escapeHtml(fmtWhen(a.created_at)) + ")" : ""}` +
        `${kindNote}</div>`;
      row.appendChild(col);
      const unlink = document.createElement("button");
      unlink.type = "button";
      unlink.className = "reset";
      unlink.textContent = "Unlink";
      unlink.addEventListener("click", async () => {
        if (
          !confirm(
            `Unlink ${a.alias_barcode} from ` +
              `${a.product_title || a.sku}?\n\nScanning it stops ` +
              `resolving to this product (History keeps the receipt).`
          )
        )
          return;
        try {
          const res = await apiFetch(
            `/api/barcode-aliases/${encodeURIComponent(a.alias_barcode)}` +
              `?by=${encodeURIComponent(operatorEl.value || "")}`,
            { method: "DELETE" }
          );
          if (!res.ok && res.status !== 204) {
            const body = await res.json().catch(() => ({}));
            throw new Error(body.detail || `HTTP ${res.status}`);
          }
          refresh();
          renderAliasesRow();
        } catch (err) {
          alert(err.message);
        }
      });
      row.appendChild(unlink);
      list.appendChild(row);
    });
  }
  refresh();
}

// --- C72 locate list: queue this product for a physical tag hunt. The
// gun's LOCATE tab pulls the same list, so nobody types a 24-hex EPC.
// The row shows current standing; the button flips it (add <-> remove).
async function renderLocateRow() {
  const row = document.getElementById("phist-locate");
  if (!phistData || !phistData.sku) {
    row.hidden = true;
    return;
  }
  row.hidden = false;
  const btn = document.getElementById("phist-locate-btn");
  btn.textContent = "…";
  try {
    const r = await apiJson("/api/locate-queue");
    const mine = (r.entries || []).find(
      (e) => e.sku.toUpperCase() === phistData.sku.toUpperCase()
    );
    phistData.locate_entry = mine || null;
    if (mine) {
      btn.textContent = "📡 Remove from locate list";
      btn.title =
        `On the C72 locate list` +
        (mine.added_by ? ` (added by ${mine.added_by})` : "") +
        ` - pick it on the gun's LOCATE tab to hunt its ` +
        `${mine.tag_count} tag(s). Click to take it off the list.`;
    } else {
      btn.textContent = "📡 Send to C72 locate list";
      btn.title =
        "Need to physically find this product's tags on the shelf? This " +
        "queues it on the gun's LOCATE tab - no EPC typing on the C72.";
    }
  } catch (err) {
    row.hidden = true;
  }
}

document
  .getElementById("phist-locate-btn")
  .addEventListener("click", async () => {
    if (!phistData || !phistData.sku) return;
    const msg = document.getElementById("phist-msg");
    const mine = phistData.locate_entry;
    try {
      if (mine) {
        await apiJson(
          `/api/locate-queue/${mine.id}?worker=${encodeURIComponent(
            operatorEl.value || ""
          )}`,
          { method: "DELETE" }
        );
        msg.textContent = "Taken off the locate list ✓";
      } else {
        const title = phistData.product
          ? phistData.product.product_title
          : null;
        const r = await postJson("/api/locate-queue", {
          sku: phistData.sku,
          label: title,
          worker: operatorEl.value || null,
        });
        msg.textContent = r.message
          ? r.message + " Open LOCATE on the C72 and tap LIST."
          : "On the locate list ✓ - open LOCATE on the C72 and tap LIST.";
      }
      renderLocateRow();
    } catch (err) {
      msg.textContent = err.message;
    }
  });

// The Review-tab window over the same list: everything queued, with
// where the tags think they live, and per-row remove.
async function renderLocateOverlay() {
  const list = document.getElementById("locq-list");
  list.innerHTML = '<li class="inventory__empty">Loading…</li>';
  try {
    const r = await apiJson("/api/locate-queue");
    const entries = r.entries || [];
    if (!entries.length) {
      list.innerHTML =
        '<li class="inventory__empty">Nothing queued - use "Send to C72 ' +
        "locate list\" on any product's panel.</li>";
      return;
    }
    list.innerHTML = entries
      .map((e) =>
        e.epc_hunt
          ? `<li class="recent__item recent__item--row">
        <div class="u-grow">
          <b>${escapeHtml(e.label || "Unlinked stickers heard on sweeps")}</b>
          <div class="binlabel">${e.tag_count} sticker(s) heard on sweeps with no product linked - hunt them from the C72's Locate list, pair or retire each one found</div>
          <div class="binlabel">${(e.epcs || [])
            .slice(0, 8)
            .map((p) => "…" + escapeHtml(p.slice(-6)))
            .join(", ")}${(e.epcs || []).length > 8 ? "…" : ""}</div>
        </div>
        <button class="reset" data-locq-rm="${e.id}" type="button"
                title="Remove from the locate list">✕</button>
      </li>`
          : `<li class="recent__item recent__item--row">
        <div class="u-grow">
          <a href="#" class="hist-sku" data-sku="${escapeHtml(e.sku)}"><b>${escapeHtml(e.sku)}</b></a>
          ${e.label ? ` <span class="binlabel">${escapeHtml(e.label)}</span>` : ""}
          <div class="binlabel">${e.tag_count} tag(s)${
            e.bins.length ? ` · tags say: ${e.bins.map(escapeHtml).join(", ")}` : ""
          }${e.added_by ? ` · added by ${escapeHtml(e.added_by)}` : ""}${
            e.created_at ? ` · ${fmtWhen(e.created_at)}` : ""
          }</div>
        </div>
        <button class="reset" data-locq-rm="${e.id}" type="button"
                title="Remove from the locate list">✕</button>
      </li>`
      )
      .join("");
    list.querySelectorAll("[data-locq-rm]").forEach((b) => {
      b.addEventListener("click", async () => {
        b.disabled = true;
        try {
          await apiJson(
            `/api/locate-queue/${b.dataset.locqRm}?worker=${encodeURIComponent(
              operatorEl.value || ""
            )}`,
            { method: "DELETE" }
          );
          renderLocateOverlay();
        } catch (err) {
          document.getElementById("locq-msg").textContent = err.message;
          b.disabled = false;
        }
      });
    });
    list.querySelectorAll(".hist-sku").forEach((a) => {
      a.addEventListener("click", (ev) => {
        ev.preventDefault();
        document.getElementById("locq-overlay").hidden = true;
        openProductHistory(a.dataset.sku);
      });
    });
  } catch (err) {
    list.innerHTML = `<li class="inventory__empty">${escapeHtml(err.message)}</li>`;
  }
}

document.getElementById("review-locate-btn").addEventListener("click", () => {
  document.getElementById("locq-msg").textContent = "";
  document.getElementById("locq-overlay").hidden = false;
  renderLocateOverlay();
});
document.getElementById("locq-close").addEventListener("click", () => {
  document.getElementById("locq-overlay").hidden = true;
});
document.getElementById("locq-overlay").addEventListener("click", (e) => {
  if (e.target.id === "locq-overlay")
    document.getElementById("locq-overlay").hidden = true;
});

// Label save — serialized products write the top line through their
// serial record (Scan Station auto-prints use it too); everything else
// saves both lines to the per-SKU label store. Lines left at their
// defaults mean "standard label".
document.getElementById("phist-label-save").addEventListener("click", async () => {
  if (!phistData) return;
  const msg = document.getElementById("phist-msg");
  const top = document.getElementById("phist-top").value.trim();
  const skuLine = document.getElementById("phist-skuline").value.trim();
  try {
    if (phistData.serial_prefix) {
      if (!top || top === STORE_HEADER) {
        msg.textContent =
          "Serialized products need a name - shorten it instead of clearing.";
        return;
      }
      await apiJson(
        `/api/serial-prefixes/${encodeURIComponent(phistData.serial_prefix)}/label`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ label_name: top }),
        }
      );
      phistData.serial_label = top;
      phistData.serial_label_saved = true;
    } else {
      const res = await apiJson(
        `/api/label-names/${encodeURIComponent(phistData.sku)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            top_text: top || STORE_HEADER,
            sku_line: skuLine || phistDefaults.sku,
            updated_by: operatorEl.value || null,
          }),
        }
      );
      phistData.custom_label = res.label_name;
      phistData.custom_placement = res.placement || "header";
      phistData.custom_sku_text = res.sku_text || null;
    }
    updateLabelPreview();
    msg.textContent = "Label saved ✓ - new prints use it.";
  } catch (err) {
    msg.textContent = err.message;
  }
});

// Print fresh labels for this product right from the panel (each gets a
// new EPC; they land in the Print queue like any other job).
document.getElementById("phist-print").addEventListener("click", async () => {
  const msg = document.getElementById("phist-msg");
  if (!phistData || !phistData.product) return;
  const operator = requireOperator();
  if (!operator) {
    msg.textContent = "Pick who's scanning (top right) first.";
    return;
  }
  const qty = Math.max(
    1,
    Math.min(50, Number(document.getElementById("phist-qty").value) || 1)
  );
  const p = phistData.product;
  const btn = document.getElementById("phist-print");
  btn.disabled = true;
  msg.textContent = "Queueing…";
  try {
    const res = await apiFetch("/api/print-jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        quantity: qty,
        shopify_variant_id: p.shopify_variant_id,
        shopify_product_id: p.shopify_product_id,
        product_title: p.product_title,
        variant_title: p.variant_title,
        sku: p.sku,
        barcode: p.barcode,
        bin_location: p.bin_location,
        label_name: phistEffectiveName(),
        label_placement: phistEffectiveName()
          ? phistData.serial_prefix
            ? "header"
            : phistData.custom_placement || "header"
          : null,
        // Two-line customs: the centre line rides along too, else a
        // saved SKU line silently reverts to the plain SKU on print.
        label_sku: phistData.serial_prefix
          ? null
          : phistData.custom_sku_text || null,
        requested_by: operator,
        printer: selectedPrinter || null,
        print_session: phistPrintSession,
      }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      msg.textContent = body.detail || "Queueing failed.";
    } else {
      msg.textContent = `${qty} label(s) queued ✓ - collect at the printer (Print queue tab tracks them).`;
    }
  } catch (err) {
    msg.textContent = err.message;
  } finally {
    btn.disabled = false;
  }
});

document
  .getElementById("phist-kind-restore")
  .addEventListener("click", async () => {
    if (!phistData || !phistData.sku) return;
    const pk = phistData.product_kind || {};
    if (
      !confirm(
        pk.excluded
          ? `Put "${phistData.sku}" back into the RFID system?\n\nIt will ` +
            `be seeded into new batches again and detected automatically.`
          : `Clear the manual setting for "${phistData.sku}"?\n\nIt goes ` +
            `back to being detected automatically from its title and SKU.`
      )
    )
      return;
    const msg = document.getElementById("phist-msg");
    try {
      const res = await postJson("/api/product-kinds", {
        sku: phistData.sku,
        kind: null,
        updated_by: operatorEl.value.trim() || null,
      });
      // Reload first — it clears the message slot — then say what happened.
      await openProductHistory(phistData.sku);
      msg.textContent = res.message;
    } catch (err) {
      msg.textContent = err.message;
    }
  });

// Undoable events carry an `undo` descriptor from the server. Today that's
// barcode links (alias rows are live, so deleting one IS the undo — the
// scanned code simply stops resolving to that product).
// An old undo deserves a second look (Nick, 2026-09-14): anything
// pressed more than a day after the event asks first, naming the age.
function undoAgeConfirmed(e) {
  const at = e && e.at ? tsDate(e.at).getTime() : NaN;
  if (!Number.isFinite(at)) return true;
  const ms = Date.now() - at;
  if (ms <= 86400000) return true;
  const days = Math.floor(ms / 86400000);
  let span;
  if (days >= 365) {
    const y = Math.floor(days / 365);
    span = `${y} year${y === 1 ? "" : "s"}`;
  } else if (days >= 30) {
    const m = Math.floor(days / 30);
    span = `${m} month${m === 1 ? "" : "s"}`;
  } else if (days >= 7) {
    const w = Math.floor(days / 7);
    span = `${w} week${w === 1 ? "" : "s"}`;
  } else {
    span = `${days} day${days === 1 ? "" : "s"}`;
  }
  return confirm(
    `This event happened ${span} ago. Undo it anyway?\n\n` +
      `A lot can change in ${span} - make sure nothing since ` +
      `depends on it.`
  );
}

async function undoHistoryEvent(e, btn) {
  if (!e || !e.undo) return;
  if (!undoAgeConfirmed(e)) return;
  // Resolved/dismissed review tasks: undo = reopen — the task returns to
  // the Review inbox and this resolution entry leaves History (the task
  // is simply open again, as if never closed).
  // Multi-box sets: undo removes the set's part records - the Shopify
  // draft listings stay (Nick, 2026-09-08). The builder recreates it
  // in seconds if that was a mistake.
  // Sold-before-label dismissal: the row rejoins the receiving list
  // and its label dismissals are removed (Nick, 2026-09-09).
  if (e.undo.kind === "receiving-dismiss") {
    if (
      !confirm(
        "Undo the sold-before-label dismissal?\n\nThe product rejoins " +
          "the receiving list and its printed labels count as owed " +
          "again. Nothing else changes."
      )
    )
      return;
    btn.disabled = true;
    try {
      await postJson(
        `/api/batches/${e.undo.batch_id}/items/${e.undo.item_id}` +
          `/dismiss-sold/undo`,
        { worker: operatorEl.value || null }
      );
      await loadHistory();
    } catch (err) {
      btn.disabled = false;
      alert(err.message);
    }
    return;
  }
  // Locate Assigned Tag (Nick, 2026-09-09): unlink the sticker the
  // Unpaired Tags hunt paired, and give back the receiving label
  // instance it consumed.
  if (e.undo.kind === "locate-pair") {
    if (
      !confirm(
        `Unlink tag …${(e.undo.epc || "").slice(-6)}?\n\nThe sticker ` +
          `goes back to being unpaired (the hunt will hear it again)` +
          (e.undo.item_id
            ? `, and the receiving label it consumed is owed again.`
            : `.`)
      )
    )
      return;
    btn.disabled = true;
    try {
      await postJson("/api/locate/pair-unlinked/undo", {
        epc: e.undo.epc,
        item_id: e.undo.item_id || null,
        worker: operatorEl.value || null,
      });
      await loadHistory();
    } catch (err) {
      btn.disabled = false;
      alert(err.message);
    }
    return;
  }
  // Packed-orders retire (Nick, 2026-09-14): the whole sweep's tags
  // come back live, ledger units handed back, the sweep un-spent.
  if (e.undo.kind === "packed-retire") {
    if (
      !confirm(
        "Undo this packed-orders retire?\n\nEvery tag it retired " +
          "comes back live, the sold-ledger units are handed back, " +
          "and the sweep can be used again."
      )
    )
      return;
    btn.disabled = true;
    try {
      await postJson("/api/epcs/retire-sold/undo", {
        capture_id: e.undo.capture_id,
        worker: operatorEl.value || null,
      });
      await loadHistory();
    } catch (err) {
      btn.disabled = false;
      alert(err.message);
    }
    return;
  }
  // Unpaired write-off (Nick, 2026-09-14): give the whole sweep's
  // dismissals back - the stickers rejoin the unpaired list on the
  // next sweep that hears them.
  if (e.undo.kind === "unpaired-ignore") {
    if (
      !confirm(
        "Undo this write-off?\n\nEvery sticker it dismissed rejoins " +
          "the unpaired locate list on the next sweep that hears it."
      )
    )
      return;
    btn.disabled = true;
    try {
      await postJson("/api/epcs/ignore-heard/undo", {
        marker: e.undo.marker,
        worker: operatorEl.value || null,
      });
      await loadHistory();
    } catch (err) {
      btn.disabled = false;
      alert(err.message);
    }
    return;
  }
  // Bin writes: undo puts the OLD bin back through the normal audited
  // endpoint — a new History entry, nothing erased.
  if (e.undo.kind === "bin") {
    const operator = operatorEl.value;
    if (!operator) {
      alert("Pick who's scanning (top right) first.");
      return;
    }
    if (
      !confirm(
        `Put ${e.undo.sku} back to bin ${e.undo.old_bin}?\n\n` +
          `This write set it to ${e.undo.new_bin}. Undo is the normal ` +
          `audited bin update - Shopify, the bin map and the product's ` +
          `tags all follow, with a new History entry.`
      )
    )
      return;
    btn.disabled = true;
    try {
      await postJson("/api/bin-updates", {
        target: e.undo.sku,
        bin: e.undo.old_bin,
        changed_by: operator,
      });
      await loadHistory();
    } catch (err) {
      btn.disabled = false;
      alert(err.message);
    }
    return;
  }
  // Retired tags: undo moves the record straight back from the retired
  // table to the active one (a return, a mis-click, a sweep that lied).
  // Grouped events (a sweep cleanup, the sold-out button) restore the
  // whole set in one call; re-used EPCs are skipped server-side.
  if (e.undo.kind === "tag-retired") {
    const operator = operatorEl.value;
    if (!operator) {
      alert("Pick who's scanning (top right) first.");
      return;
    }
    const epcs = e.undo.epcs || [e.undo.epc];
    if (
      !confirm(
        (epcs.length === 1
          ? `Restore tag ${epcs[0]}?`
          : `Restore all ${epcs.length} retired tags?`) +
          `\n\n${e.sku || ""} - the record${epcs.length === 1 ? " moves" : "s move"} ` +
          `back to the active tags, exactly as before retirement.`
      )
    )
      return;
    btn.disabled = true;
    try {
      await postJson("/api/assignments/unretire", {
        epcs,
        changed_by: operator,
      });
      await loadHistory();
    } catch (err) {
      btn.disabled = false;
      alert(err.message);
    }
    return;
  }
  // Backorder notes: "undo" clears the note by hand — the expected
  // count drops back and the daily check may flag the SKU again.
  if (e.undo.kind === "on-hand" || e.undo.kind === "on-hand-lower") {
    // A lowering's undo also restores the retired tags and the consumed
    // sales; the endpoint's 409 text describes exactly what will happen.
    const path =
      e.undo.kind === "on-hand-lower"
        ? `/api/onhand-updates/${e.undo.change_id}/undo-lower`
        : `/api/onhand-updates/${e.undo.change_id}/undo`;
    btn.disabled = true;
    try {
      await postJson(path, {
        changed_by: operatorEl.value || null,
      });
      await loadHistory();
    } catch (err) {
      const msg = String(err.message || "");
      if (!msg.includes("Confirm to write it")) {
        btn.disabled = false;
        alert(msg);
        return;
      }
      if (!confirm(msg)) {
        btn.disabled = false;
        return;
      }
      try {
        const res = await postJson(path, {
          changed_by: operatorEl.value || null,
          confirmed: true,
        });
        await loadHistory();
        alert(res.message);
      } catch (err2) {
        btn.disabled = false;
        alert(err2.message);
      }
    }
    return;
  }
  // Assigned Tag events: undo releases the tags, keeping a full snapshot
  // so the release can itself be undone (re-apply). Manual both ways -
  // the loop is endless by design and never spins on its own.
  if (e.undo.kind === "tag-assign") {
    const operator = operatorEl.value;
    if (!operator) {
      alert("Pick who's scanning (top right) first.");
      return;
    }
    const n = (e.undo.epcs || []).length;
    if (
      !confirm(
        `Release ${n} tag(s) from ${e.sku || e.title || "this product"}?\n\n` +
          `The product stops being tied to ${n === 1 ? "that label" : "those labels"}. ` +
          `Nothing in Shopify changes. Undo lives in History: the ` +
          `Released Tag entry re-applies them exactly as they were, ` +
          `original pairing date included.`
      )
    )
      return;
    btn.disabled = true;
    try {
      const res = await postJson("/api/tags/release", {
        epcs: e.undo.epcs,
        sku: e.undo.sku || null,
        by: operator,
      });
      await loadHistory();
      alert(res.message);
    } catch (err) {
      btn.disabled = false;
      alert(err.message);
    }
    return;
  }
  // Released Tag events: undo re-applies the tags from their snapshots.
  if (e.undo.kind === "tag-release") {
    const operator = operatorEl.value;
    if (!operator) {
      alert("Pick who's scanning (top right) first.");
      return;
    }
    const n = (e.undo.epcs || []).length;
    if (
      !confirm(
        `Re-apply ${n} tag(s) to ${e.sku || e.title || "this product"}?\n\n` +
          `Each assignment comes back exactly as it was before the ` +
          `release - product, bin and original pairing date included. ` +
          `Undo lives in History: the Assigned Tag entry releases them ` +
          `again.`
      )
    )
      return;
    btn.disabled = true;
    try {
      const res = await postJson("/api/tags/reapply", {
        epcs: e.undo.epcs,
        sku: e.undo.sku || null,
        by: operator,
      });
      await loadHistory();
      alert(res.message);
    } catch (err) {
      btn.disabled = false;
      alert(err.message);
    }
    return;
  }
  // Batch events: release every tag tie that batch created, in one go.
  if (e.undo.kind === "batch-ties") {
    if (
      !confirm(
        `Release all ${e.undo.ties} tag tie(s) from batch #${e.undo.batch_id} ` +
          `(${e.title})?\n\nThe products stop being tied to those labels. ` +
          `Nothing in Shopify changes, and the labels themselves stay valid.`
      )
    )
      return;
    btn.disabled = true;
    try {
      const res = await postJson(
        `/api/batches/${e.undo.batch_id}/unpair-all`,
        {}
      );
      await loadHistory();
      alert(
        `${res.removed} tie(s) released` +
          (res.legacy
            ? ` (${res.legacy} of them paired before batches tracked their own ties).`
            : ".")
      );
    } catch (err) {
      btn.disabled = false;
      alert(err.message);
    }
    return;
  }
  // Multi-box/bundle decisions: undo means handing the product back to
  // automatic detection, which also un-drops it if it was excluded.
  if (e.undo.kind === "product-kind") {
    const what = e.undo.excluded
      ? `Put "${e.sku}" back into the RFID system?\n\nIt will be seeded ` +
        `into new batches again and detected automatically.`
      : `Clear the manual multi-box/bundle setting for "${e.sku}"?\n\n` +
        `It goes back to being detected automatically from its title ` +
        `and SKU.`;
    if (!confirm(what)) return;
    btn.disabled = true;
    try {
      const res = await postJson("/api/product-kinds", {
        sku: e.undo.sku,
        kind: null,
        updated_by: operatorEl.value.trim() || null,
      });
      await loadHistory();
      alert(res.message);
    } catch (err) {
      btn.disabled = false;
      alert(err.message);
    }
    return;
  }
  if (e.undo.kind !== "barcode-alias") return;
  const alias = e.undo.alias_barcode;
  const target = e.sku || e.title || "that product";
  if (
    !confirm(
      `Undo this barcode link?\n\n${alias} → ${target}\n\nThe scanned ` +
        `barcode will stop resolving to this product. You can re-link it ` +
        `(to the right product) at the Scan Station.`
    )
  )
    return;
  btn.disabled = true;
  const res = await apiFetch(
    `/api/barcode-aliases/${encodeURIComponent(alias)}`,
    { method: "DELETE" }
  );
  if (res.ok || res.status === 404) {
    await loadHistory();
  } else {
    btn.disabled = false;
    alert("Could not undo that link - try again.");
  }
}

let histSearchTimer;
document.getElementById("hist-search").addEventListener("input", () => {
  clearTimeout(histSearchTimer);
  histSearchTimer = setTimeout(renderHistory, 150);
});

// Boot
resetStation();
loadRecent();
loadRefreshStats();

// === Shipment sort (Nick, 2026-08-31) ======================================
// A mixed delivery gets scanned box by box; each scan asks the planner
// which OPEN stock orders still expect that product and buckets the box
// into the oldest order with capacity left. Boxes nothing expects land
// in an "unexplained" list. READ-ONLY end to end: the hand-off buttons
// open each bucket in the TC-Planner pre-filled, and saving, updating
// stock and printing all stay over there (manual by request). The pile
// survives reloads in localStorage until cleared.
let sortShipRows = {};
let sortShipSeq = [];
let sortShipPlannerUrl = null;
let sortShipBusy = false;
// Component-set definitions (Nick, 2026-08-31: Buckeye's S30 Pro set,
// the NexStar bracket + tripod-clip combo): a SET's sku plus the
// component skus whose boxes each count toward one set unit. Kept in
// their own localStorage key so Clear pile never forgets them - future
// shipments auto-group. RFID-side only; the planner never changes.
let sortShipDefs = {};
// Session cache of the SET product's open orders, keyed like defs.
let sortShipSetOrders = {};
let sortShipSelect = false;
let sortShipSelected = new Set();

function sortShipLoadDefs() {
  try {
    sortShipDefs = JSON.parse(
      localStorage.getItem("sortship_bundle_defs") || "{}"
    ) || {};
  } catch (err) {
    sortShipDefs = {};
  }
}

function sortShipSaveDefs() {
  try {
    localStorage.setItem(
      "sortship_bundle_defs", JSON.stringify(sortShipDefs)
    );
  } catch (err) {
    /* per-session fallback is fine */
  }
}

function sortShipComponentDefKey(value) {
  // A component is recognised by its catalog SKU when it has one, OR
  // by its raw scanned label - the S30 set's boxes have NO products of
  // their own yet (Nick, 2026-08-31), and if a label is later linked
  // to a real product, both spellings keep matching.
  const up = (value || "").trim().toUpperCase();
  if (!up) return null;
  for (const [defKey, def] of Object.entries(sortShipDefs)) {
    if (
      (def.components || []).some(
        (c) =>
          (c.sku || "").toUpperCase() === up ||
          (c.label || "").toUpperCase() === up ||
          (c.key || "").toUpperCase() === up
      )
    )
      return defKey;
  }
  return null;
}

function sortShipMemberRow(c) {
  return (
    sortShipRows[(c.key || c.sku || c.label || "").toUpperCase()] || null
  );
}

function sortShipSave() {
  try {
    localStorage.setItem(
      "sortship_pile",
      JSON.stringify({
        rows: sortShipRows,
        seq: sortShipSeq,
        setOrders: sortShipSetOrders,
      })
    );
  } catch (err) {
    /* per-session fallback is fine */
  }
}

function sortShipRestore() {
  sortShipLoadDefs();
  try {
    const raw = localStorage.getItem("sortship_pile");
    if (!raw) return;
    const data = JSON.parse(raw);
    if (data && data.rows && data.seq) {
      sortShipRows = data.rows;
      sortShipSeq = data.seq;
      sortShipSetOrders = data.setOrders || {};
    }
  } catch (err) {
    /* corrupted draft - start clean */
  }
}

async function sortShipFetchSetOrders(defKey) {
  if (sortShipSetOrders[defKey]) return sortShipSetOrders[defKey];
  const def = sortShipDefs[defKey];
  if (!def) return [];
  let orders = [];
  try {
    const oo = await apiJson(
      `/api/planner/on-order/${encodeURIComponent(def.setSku)}` +
        `?operator=${encodeURIComponent(operatorEl.value || "")}`
    );
    if (oo.ok) {
      orders = (oo.orders || [])
        .filter((o) => (o.remaining || 0) > 0)
        .sort(
          (a, b) =>
            String(a.expected_date || "9999").localeCompare(
              String(b.expected_date || "9999")
            ) || a.order_id - b.order_id
        );
    }
  } catch (err) {
    /* no orders - the set lands in unexplained */
  }
  sortShipSetOrders[defKey] = orders;
  return orders;
}

function setSortShipStatus(text) {
  document.getElementById("sortship-status").textContent = text || "";
}

// Returns {ok, text} so the LINK relay can answer the gun; the wedge
// input ignores the return value.
async function sortShipScan(code) {
  const term = (code || "").trim();
  if (!term || sortShipBusy) {
    return { ok: false, text: "Scan skipped - the sorter was busy." };
  }
  sortShipBusy = true;
  try {
    let product = null;
    let matchNote = null;
    let ambiguous = null;
    let suggestion = null;
    try {
      product = await apiJson(
        `/api/products/by-barcode/${encodeURIComponent(term)}`
      );
    } catch (err) {
      // Vendor box labels are often the maker's own item string, not
      // our SKU or barcode (Nick, 2026-08-31, the Buckeye shipment):
      // second try folds separators and matches sku, barcode, and
      // VARIANT names - unique hits only, ambiguity stays human, and a
      // token-level near-miss comes back as a SUGGESTION with resolve
      // buttons instead of a silent guess.
      try {
        const m = await apiJson(
          `/api/products/label-match/${encodeURIComponent(term)}`
        );
        if (m.ok) {
          product = m.product;
          matchNote = `matched by ${m.matched_by} "${m.matched_value}"`;
        } else if (m.suggestion) {
          suggestion = m.suggestion;
        } else if (m.ambiguous) {
          ambiguous =
            "could be " +
            m.candidates
              .map((c) => c.sku || c.product_title)
              .slice(0, 3)
              .join(" or ") +
            " - pick by hand in the planner";
        }
      } catch (err2) {
        /* genuinely unknown - lands in unexplained below */
      }
    }
    const key = ((product && product.sku) || term).toUpperCase();
    // A component of a defined SET never sorts on its own: its scans
    // tally toward one set unit (Nick, 2026-08-31). Match by resolved
    // SKU and by the raw label, so product-less components group too.
    const bundleKey =
      (product ? sortShipComponentDefKey(product.sku) : null) ||
      sortShipComponentDefKey(term);
    if (bundleKey) await sortShipFetchSetOrders(bundleKey);
    let row = sortShipRows[key];
    if (!row) {
      row = {
        key,
        term,
        sku: product ? product.sku : null,
        title: product ? product.product_title : term,
        image_url: product ? product.image_url : null,
        matchNote,
        suggestion,
        bundleKey,
        orders: [],
        alloc: {},
        unexplained: 0,
        scanned: 0,
        reason: product
          ? null
          : suggestion
            ? `looks like ${suggestion.sku} ("${suggestion.product_title}") - link or overwrite below to teach the system`
            : ambiguous ||
              "unknown product - fix the barcode or link it at the Scan Station",
      };
      if (product && product.sku) {
        try {
          const oo = await apiJson(
            `/api/planner/on-order/${encodeURIComponent(product.sku)}` +
              `?operator=${encodeURIComponent(operatorEl.value || "")}`
          );
          if (oo.ok) {
            row.orders = (oo.orders || [])
              .filter((o) => (o.remaining || 0) > 0)
              .sort(
                (a, b) =>
                  String(a.expected_date || "9999").localeCompare(
                    String(b.expected_date || "9999")
                  ) || a.order_id - b.order_id
              );
          }
          if (!row.orders.length && !row.reason) {
            row.reason = "no open stock order expects this product";
          }
        } catch (err) {
          row.reason = "planner lookup failed - counted as unexplained";
        }
      }
      sortShipRows[key] = row;
      sortShipSeq.push(key);
    }
    if (row.bundleKey && sortShipDefs[row.bundleKey]) {
      // Component tally only - the SET allocates as one product.
      row.scanned += 1;
      sortShipSave();
      renderSortShip();
      const def = sortShipDefs[row.bundleKey];
      const units = sortShipBundleUnits(row.bundleKey);
      const msg =
        `${row.title}: +1 component of ${def.setSku} - set count now ` +
        `${units}.`;
      setSortShipStatus(msg);
      return { ok: true, text: msg };
    }
    const target = row.orders.find(
      (o) => (row.alloc[o.order_id] || 0) < o.remaining
    );
    if (target) {
      row.alloc[target.order_id] = (row.alloc[target.order_id] || 0) + 1;
    } else {
      row.unexplained += 1;
    }
    row.scanned += 1;
    sortShipSave();
    renderSortShip();
    const msg = target
      ? `${row.title}: +1 to SO ${
          target.reference_number != null
            ? target.reference_number
            : target.order_id
        } (${row.alloc[target.order_id]} of ${target.remaining} expected)`
      : `${row.title}: +1 unexplained` +
          (row.reason ? ` - ${row.reason}` : "");
    setSortShipStatus(msg);
    return { ok: !!target, text: msg };
  } finally {
    sortShipBusy = false;
  }
}

function sortShipBundleUnits(defKey) {
  const def = sortShipDefs[defKey];
  if (!def || !(def.components || []).length) return 0;
  let units = Infinity;
  for (const c of def.components) {
    const row = sortShipMemberRow(c);
    units = Math.min(units, row ? row.scanned : 0);
  }
  return Number.isFinite(units) ? units : 0;
}

function sortShipGroups() {
  const orders = new Map();
  const unexplained = [];
  for (const key of sortShipSeq) {
    const row = sortShipRows[key];
    if (!row) continue;
    // Set components render inside their bundle block, never alone.
    if (row.bundleKey && sortShipDefs[row.bundleKey]) continue;
    for (const o of row.orders) {
      const n = row.alloc[o.order_id] || 0;
      if (!n) continue;
      if (!orders.has(o.order_id)) {
        orders.set(o.order_id, { meta: o, entries: [] });
      }
      orders.get(o.order_id).entries.push({ row, n, meta: o });
    }
    if (row.unexplained > 0) unexplained.push(row);
  }
  // Bundles: one set unit per full component sweep. A bundle lives in
  // exactly ONE bucket (Nick, 2026-08-31: never spread across lists) -
  // the oldest order with capacity takes what it can, any excess is
  // noted on the block rather than spilling to a second bucket.
  const bundles = [];
  for (const [defKey, def] of Object.entries(sortShipDefs)) {
    const members = (def.components || []).map((c) => ({
      comp: c,
      row: sortShipMemberRow(c),
    }));
    if (!members.some((m) => m.row && m.row.scanned > 0)) continue;
    const units = sortShipBundleUnits(defKey);
    const ordersList = sortShipSetOrders[defKey] || [];
    const target = ordersList.find((o) => (o.remaining || 0) > 0) || null;
    const alloc = target ? Math.min(units, target.remaining) : 0;
    bundles.push({
      defKey,
      def,
      members,
      units,
      target,
      alloc,
      leftover: units - alloc,
    });
    if (target && !orders.has(target.order_id)) {
      orders.set(target.order_id, { meta: target, entries: [] });
    }
  }
  return { orders, unexplained, bundles };
}

function renderSortShip() {
  const host = document.getElementById("sortship-groups");
  if (!host) return;
  const { orders, unexplained, bundles } = sortShipGroups();
  const rowHtml = (row, n, act) => `
    <div class="sortrow">
      ${
        sortShipSelect && !row.bundleKey
          ? `<input type="checkbox" class="sortsel" data-sel="${escapeHtml(row.key)}" ${
              sortShipSelected.has(row.key) ? "checked" : ""
            } />`
          : ""
      }
      ${
        row.image_url
          ? `<img class="bcell__img" src="${escapeHtml(row.image_url)}" alt="" loading="lazy" />`
          : `<span class="bcell__img bcell__img--empty"></span>`
      }
      <span class="sortrow__name">${escapeHtml(row.title)}${
        row.sku ? ` <span class="mono">· ${escapeHtml(row.sku)}</span>` : ""
      }${
        row.matchNote
          ? ` <span class="recent__note" title="The scanned label didn't equal the SKU or barcode - this is how it was recognised. Double-check it's the right product.">· ${escapeHtml(row.matchNote)}</span>`
          : ""
      }</span>
      <span class="sortrow__n">× ${n}</span>
      <button class="reset sortrow__minus" type="button" title="Mis-scan: remove one"
        data-key="${escapeHtml(row.key)}" data-act="${act}">−</button>
    </div>`;
  // The bundle block (Nick, 2026-08-31): components looped in one
  // outline, each with its own count; the SET count sits centered
  // beside them.
  const bundleHtml = (b) => `
    <div class="sortbundle">
      <div class="sortbundle__members">
        ${b.members
          .map((m) =>
            m.row
              ? rowHtml(m.row, m.row.scanned, "bun")
              : `<div class="sortrow sortrow--ghost">
                  <span class="bcell__img bcell__img--empty"></span>
                  <span class="sortrow__name">${escapeHtml(
                    m.comp.title || m.comp.sku || m.comp.label || "?"
                  )} <span class="mono">· ${escapeHtml(
                    m.comp.sku || m.comp.label || ""
                  )}</span></span>
                  <span class="sortrow__n">× 0</span>
                </div>`
          )
          .join("")}
      </div>
      <div class="sortbundle__side">
        <div class="sortbundle__count">× ${b.units}</div>
        <div class="mono sortbundle__sku">${escapeHtml(b.def.setSku)}</div>
        ${
          b.leftover > 0
            ? `<div class="recent__note">+${b.leftover} beyond the order</div>`
            : ""
        }
        <button class="reset" type="button" data-unbundle="${escapeHtml(b.defKey)}"
          title="Dissolves this set definition: the component scans re-sort as their own products, and future scans stop grouping.">Unbundle</button>
      </div>
    </div>`;
  let html = "";
  for (const [orderId, g] of orders) {
    const groupBundles = bundles.filter(
      (b) => b.target && b.target.order_id === orderId
    );
    const units =
      g.entries.reduce((s, e) => s + e.n, 0) +
      groupBundles.reduce((s, b) => s + b.alloc, 0);
    const ref =
      g.meta.reference_number != null ? g.meta.reference_number : orderId;
    html += `
      <div class="sortgroup">
        <div class="sortgroup__head">
          <span class="sortgroup__title">SO ${escapeHtml(String(ref))}${
            g.meta.vendor ? ` · ${escapeHtml(g.meta.vendor)}` : ""
          }${
            g.meta.expected_date
              ? ` <span class="recent__note">expected ${escapeHtml(g.meta.expected_date)}</span>`
              : ""
          }</span>
          <span class="recent__note">${units} unit(s)</span>
          <button class="print__btn sortgroup__go" type="button" data-order="${orderId}"
            title="Opens this stock order in the TC-Planner with these To-receive counts pre-filled. Review there, then Save / Update stock / Print labels - nothing is saved from here.">Open in TC-Planner (pre-filled)</button>
        </div>
        ${g.entries.map((e) => rowHtml(e.row, e.n, String(orderId))).join("")}
        ${groupBundles.map(bundleHtml).join("")}
      </div>`;
  }
  const strayBundles = bundles.filter((b) => !b.target);
  if (unexplained.length || strayBundles.length) {
    html += `
      <div class="sortgroup sortgroup--warn">
        <div class="sortgroup__head">
          <span class="sortgroup__title">⚠ No order explains these</span>
          <span class="recent__note">${
            unexplained.reduce((s, r) => s + r.unexplained, 0) +
            strayBundles.reduce((s, b) => s + b.units, 0)
          } unit(s)</span>
        </div>
        ${strayBundles.map(bundleHtml).join("")}
        ${unexplained
          .map(
            (row) =>
              rowHtml(row, row.unexplained, "unx") +
              (row.reason
                ? `<div class="recent__meta sortrow__why">${escapeHtml(row.reason)}</div>`
                : "") +
              (row.suggestion
                ? `<div class="sortrow__fix">
                    <button class="reset" type="button" data-fix="link" data-key="${escapeHtml(row.key)}"
                      title="Saves the scanned label as a lookup ALIAS for this product (Shopify untouched) - every future scan of it resolves. The pile re-sorts this row right away.">🔗 Link "${escapeHtml(row.term || row.key)}" to ${escapeHtml(row.suggestion.sku)}</button>
                    <button class="reset" type="button" data-fix="overwrite" data-key="${escapeHtml(row.key)}"
                      title="REPLACES this product's barcode in Shopify with the scanned label. Its current barcode is dropped (a clean replaced barcode is not auto-linked). Prefer Link unless the stored barcode is wrong.">✎ Set as its Shopify barcode</button>
                  </div>`
                : "")
          )
          .join("")}
      </div>`;
  }
  host.innerHTML =
    html ||
    `<p class="recent__empty">Nothing scanned yet - scan the first box.</p>`;
  host.querySelectorAll(".sortrow__minus").forEach((btn) =>
    btn.addEventListener("click", () => {
      const row = sortShipRows[btn.dataset.key];
      if (!row) return;
      if (btn.dataset.act === "unx") {
        row.unexplained = Math.max(0, row.unexplained - 1);
      } else if (btn.dataset.act !== "bun") {
        const oid = Number(btn.dataset.act);
        row.alloc[oid] = Math.max(0, (row.alloc[oid] || 0) - 1);
      }
      row.scanned = Math.max(0, row.scanned - 1);
      if (row.scanned === 0 && !row.bundleKey) {
        delete sortShipRows[row.key];
        sortShipSeq = sortShipSeq.filter((k) => k !== row.key);
      }
      sortShipSave();
      renderSortShip();
    })
  );
  host.querySelectorAll(".sortsel").forEach((cb) =>
    cb.addEventListener("change", () => {
      if (cb.checked) sortShipSelected.add(cb.dataset.sel);
      else sortShipSelected.delete(cb.dataset.sel);
      const n = sortShipSelected.size;
      document.getElementById("sortship-selcount").textContent =
        `${n} selected`;
      document.getElementById("sortship-sellink").disabled = n < 2;
    })
  );
  host.querySelectorAll("[data-unbundle]").forEach((btn) =>
    btn.addEventListener("click", () =>
      sortShipUnbundle(btn.dataset.unbundle)
    )
  );
  host.querySelectorAll(".sortgroup__go").forEach((btn) =>
    btn.addEventListener("click", () =>
      sortShipOpenPlanner(Number(btn.dataset.order))
    )
  );
  host.querySelectorAll("[data-fix]").forEach((btn) =>
    btn.addEventListener("click", () =>
      sortShipResolveSuggestion(btn.dataset.key, btn.dataset.fix)
    )
  );
}

// The RigelQF-Synta flow (Nick, 2026-08-31): a near-miss suggestion is
// resolved by teaching the system - link the scanned label as an alias
// (local, Shopify untouched) or write it as the product's barcode in
// Shopify. Either way the row re-scans itself and re-sorts into its
// order bucket.
async function sortShipResolveSuggestion(key, how) {
  const row = sortShipRows[key];
  if (!row || !row.suggestion) return;
  const operator = operatorEl.value;
  if (!operator) {
    alert("Pick who's scanning (top right) first.");
    return;
  }
  const term = row.term || row.key;
  const sug = row.suggestion;
  try {
    if (how === "link") {
      if (
        !confirm(
          `Link "${term}" to ${sug.sku}?\n\nIt becomes a lookup alias - ` +
            `every future scan of that label resolves to ` +
            `${sug.product_title}. Shopify is not touched.`
        )
      )
        return;
      await postJson("/api/barcode-aliases", {
        alias_barcode: term,
        target: sug.sku,
        created_by: operator,
      });
    } else {
      if (
        !confirm(
          `Write "${term}" to Shopify as the BARCODE of ${sug.sku}?\n\n` +
            `Its current barcode (${sug.barcode || "none"}) is replaced ` +
            `and NOT auto-linked (it's a clean value). Prefer Link ` +
            `unless the stored barcode is wrong. Logged and visible in ` +
            `History.`
        )
      )
        return;
      await postJson("/api/barcode-overwrites", {
        target: sug.sku,
        new_barcode: term,
        confirmed: true,
        changed_by: operator,
      });
    }
    // Re-sort the row through the normal path - the label now resolves.
    const n = row.scanned;
    delete sortShipRows[key];
    sortShipSeq = sortShipSeq.filter((k) => k !== key);
    sortShipSave();
    for (let i = 0; i < n; i++) {
      await sortShipScan(term);
    }
    setSortShipStatus(
      `${term} now resolves to ${sug.sku} - ${n} scan(s) re-sorted.`
    );
  } catch (err) {
    alert(err.message);
  }
}

function sortShipOpenPlanner(orderId) {
  const { orders, bundles } = sortShipGroups();
  const g = orders.get(orderId);
  if (!g) return;
  const items = g.entries
    .filter((e) => e.row.sku)
    .map((e) => ({ sku: e.row.sku, qty: e.n }));
  // Bundles hand off as the SET product - components never reach the
  // planner (Nick, 2026-08-31: the planner stays untouched).
  for (const b of bundles) {
    if (b.target && b.target.order_id === orderId && b.alloc > 0) {
      items.push({ sku: b.def.setSku, qty: b.alloc });
    }
  }
  const payload = { order_id: orderId, items };
  if (!sortShipPlannerUrl) {
    alert(
      "The planner bridge doesn't report its address - open the " +
        "planner by hand and type the counts."
    );
    return;
  }
  const b64 = btoa(unescape(encodeURIComponent(JSON.stringify(payload))))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  const base = sortShipPlannerUrl.replace(/\/+$/, "");
  window.open(`${base}/#receive=${b64}`, "_blank", "noopener");
  const ref =
    g.meta.reference_number != null ? g.meta.reference_number : orderId;
  setSortShipStatus(
    `Opened SO ${ref} in the planner - review, Save, Update stock, ` +
      `Print labels over there. The pile here stays until you Clear it.`
  );
}

// === Receive entire shipment (Nick, 2026-09-01) =============================
// RFID-first receiving: load a whole stock order, print every remaining
// label up front, pair what physically arrived, hold the leftovers on a
// vendor strip, and hand the PAIRED counts to the planner pre-filled
// (its Print labels grays out there - the labels already exist).

async function plannerAppUrl() {
  if (sortShipPlannerUrl) return sortShipPlannerUrl;
  try {
    const st = await apiJson("/api/planner/status");
    sortShipPlannerUrl = st.app_url || null;
  } catch (err) {
    /* caller reports */
  }
  return sortShipPlannerUrl;
}

async function openPlannerReceive(orderId, items) {
  const base = await plannerAppUrl();
  if (!base) {
    alert(
      "The planner bridge doesn't report its address - open the " +
        "planner by hand and type the counts."
    );
    return false;
  }
  const payload = { order_id: orderId, items };
  const b64 = btoa(unescape(encodeURIComponent(JSON.stringify(payload))))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  window.open(
    `${base.replace(/\/+$/, "")}/#receive=${b64}`,
    "_blank",
    "noopener"
  );
  return true;
}

function fsStatus(msg, err) {
  const el = document.getElementById("fullship-status");
  el.textContent = msg || "";
  el.classList.toggle("fs-status--err", !!err);
}

document.getElementById("fullship-open").addEventListener("click", async () => {
  document.getElementById("fullship").hidden = false;
  document.getElementById("fullship-preview").innerHTML = "";
  document.getElementById("fullship-orders").hidden = false;
  fsStatus("Loading open stock orders…");
  document.getElementById("fullship-order").focus();
  try {
    const body = await apiJson("/api/receiving/orders");
    const box = document.getElementById("fullship-orders");
    if (!body.ok) {
      fsStatus(
        "⚠ The planner bridge isn't answering - type the SO number " +
          "once it's back.", true
      );
      box.innerHTML = "";
      return;
    }
    fsStatus("Pick an open order, or type its SO number.");
    box.innerHTML = (body.orders || [])
      .map(
        (o) => `<button class="reset fullship-orderrow" type="button"
          data-ref="${escapeHtml(String(o.reference_number ?? o.order_id))}"
          data-batch="${o.already_printed && o.batch_id ? o.batch_id : ""}">
          SO ${escapeHtml(String(o.reference_number ?? o.order_id))} ·
          ${escapeHtml(o.vendor || "?")}${
            o.expected_date
              ? ` · expected ${escapeHtml(o.expected_date)}`
              : ""
          }${o.already_printed ? " · ✓ already printed" : ""}
        </button>`
      )
      .join("") || `<p class="linkbox__text">No open stock orders.</p>`;
  } catch (err) {
    fsStatus(err.message, true);
  }
});

document.getElementById("fullship-exit").addEventListener("click", () => {
  document.getElementById("fullship").hidden = true;
});

document
  .getElementById("fullship-orders")
  .addEventListener("click", (e) => {
    const row = e.target.closest(".fullship-orderrow");
    if (!row) return;
    document.getElementById("fullship-order").value = row.dataset.ref;
    fullshipLoad();
  });

async function fullshipLoad() {
  const ref = document.getElementById("fullship-order").value.trim();
  const out = document.getElementById("fullship-preview");
  if (!ref) {
    fsStatus("Type the SO number first.", true);
    return;
  }
  fsStatus(`Loading SO ${ref}…`);
  out.innerHTML = "";
  try {
    const body = await apiJson(
      `/api/receiving/orders/${encodeURIComponent(ref)}`
    );
    const o = body.order;
    if (body.receipt && body.receipt.settled_at) {
      // Already received: the panel becomes the road BACK to the
      // planner instead of a dead end (Nick, 2026-09-01, SO 946).
      const updated = !!body.receipt.stock_updated_at;
      document.getElementById("fullship-orders").hidden = true;
      out.innerHTML = `
        <div class="linkbox__actions u-mb6">
          <button class="reset" id="fullship-back" type="button">← All open orders</button>
        </div>
        <p class="result ${updated ? "result--ok" : ""}">SO ${escapeHtml(
          String(o.reference_number)
        )} was received via Receive entire shipment (batch
        #${body.receipt.batch_id})${
          updated
            ? " and Shopify stock is updated ✓ - nothing left to do."
            : " - Shopify stock is NOT updated yet."
        }</p>
        ${
          updated
            ? ""
            : `<div class="linkbox__actions">
                <button class="print__btn" id="fullship-continue" type="button"
                  data-oid="${o.order_id}">➡ Continue to TC-Planner (pre-filled)</button>
              </div>`
        }`;
      fsStatus("");
      return;
    }
    // The loaded order REPLACES the order list (Nick, 2026-09-01) -
    // one thing on screen at a time.
    document.getElementById("fullship-orders").hidden = true;
    const rows = body.items
      .map((l) => {
        const notes = [];
        if (l.flag) notes.push(`⚠ ${l.flag}`);
        if (l.held)
          notes.push(
            `🏷 take ${Math.min(l.held.count, l.remaining)} from the ` +
              `${l.held.where} instead of printing`
          );
        return `<tr>
          <td>${escapeHtml(l.product_title || l.title || l.sku || "?")}${
            l.nickname
              ? ` <span class="vendorname">(${escapeHtml(l.nickname)})</span>`
              : ""
          }</td>
          <td class="mono">${escapeHtml(l.sku || "—")}</td>
          <td class="num">${l.remaining}</td>
          <td>${notes.map((n) => escapeHtml(n)).join("<br>") || "✓"}</td>
        </tr>`;
      })
      .join("");
    const printable = body.items.filter((l) => !l.flag);
    const flaggedLines = body.items.filter((l) => l.flag);
    const labelGuess = printable.reduce(
      (n, l) =>
        n + Math.max(0, l.remaining - (l.held ? l.held.count : 0)),
      0
    );
    // The verdict, up front (Nick, 2026-09-01): every line was checked
    // against the system - say plainly whether the check passed.
    const verdict = flaggedLines.length
      ? `<p class="result result--err">⚠ Check found problems: ` +
        `${flaggedLines.length} of ${body.items.length} line(s) are ` +
        `flagged below. Flagged lines print NOTHING until fixed - ` +
        `the other ${printable.length} print normally.</p>`
      : `<p class="result result--ok">✓ Check passed - all ` +
        `${body.items.length} line(s) match products in the system ` +
        `and can print.</p>`;
    out.innerHTML = `
      <div class="linkbox__actions u-mb6">
        <button class="reset" id="fullship-back" type="button">← All open orders</button>
      </div>
      ${verdict}
      <div class="inventory__scroll"><table class="inventory__table">
        <thead><tr><th>Product</th><th>SKU</th>
          <th class="num" title="Units still owed on this order - already-received units don't reload">Remaining</th>
          <th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
      <div class="linkbox__actions u-mt8">
        <button class="print__btn" id="fullship-go" type="button"
          data-ref="${escapeHtml(ref)}">
          Print ~${labelGuess} label(s) &amp; start receiving
        </button>
      </div>`;
    fsStatus(
      `SO ${o.reference_number} · ${o.vendor || "?"} - ` +
        `${body.items.length} line(s) remaining.`
    );
  } catch (err) {
    fsStatus(err.message, true);
  }
}

document
  .getElementById("fullship-load")
  .addEventListener("click", fullshipLoad);
document
  .getElementById("fullship-order")
  .addEventListener("keydown", (e) => {
    if (e.key === "Enter") fullshipLoad();
  });

document
  .getElementById("fullship-preview")
  .addEventListener("click", async (e) => {
    if (e.target.closest("#fullship-back")) {
      document.getElementById("fullship-preview").innerHTML = "";
      document.getElementById("fullship-orders").hidden = false;
      fsStatus("Pick an open order, or type its SO number.");
      return;
    }
    const cont = e.target.closest("#fullship-continue");
    if (cont) {
      cont.disabled = true;
      try {
        const st = await apiJson(
          `/api/receiving/order-status/${cont.dataset.oid}`
        );
        if (!st.printed || !st.planner) {
          alert("No receipt on file for that order any more.");
          return;
        }
        await openPlannerReceive(st.planner.order_id, st.planner.items);
      } catch (err) {
        alert(err.message);
      } finally {
        cont.disabled = false;
      }
      return;
    }
    const go = e.target.closest("#fullship-go");
    if (!go) return;
    if (!operatorEl.value) {
      alert("Pick who's scanning (top right) first.");
      return;
    }
    go.disabled = true;
    try {
      const res = await postJson("/api/receiving/full-shipment", {
        order: go.dataset.ref,
        requested_by: operatorEl.value,
      });
      alert(res.message);
      document.getElementById("fullship").hidden = true;
      resumeBatch(res.batch.id);
    } catch (err) {
      go.disabled = false;
      alert(err.message);
    }
  });

// --- the settle flow (2026-09-28: held strips are GONE - unused -------
// labels get peeled and discarded like the blank roll; a discarded
// label that is ever heard again can be written off from the audit)
document
  .getElementById("recv-settle")
  .addEventListener("click", async () => {
    if (!batch) return;
    if (!operatorEl.value) {
      alert("Pick who's scanning (top right) first.");
      return;
    }
    try {
      const res = await postJson(
        `/api/batches/${batch.id}/settle-shipment`,
        { created_by: operatorEl.value }
      );
      if (res.total_unpaired === 0) {
        // Everything paired: straight to the planner, pre-filled. The
        // batch stays open and closes itself when the planner saves.
        await openPlannerReceive(res.planner.order_id, res.planner.items);
        setBatchResult(
          "Order opened pre-filled in TC-Planner - Save the receive, " +
            "then Update stock. This batch closes itself when the " +
            "planner saves.",
          "ok"
        );
        return;
      }
      const lines = res.unpaired
        .map((u) => `  ${u.product_title || u.sku} (${u.sku}): ${u.count}`)
        .join("\n");
      alert(
        `${res.total_unpaired} label(s) printed but never found a box - ` +
          "the order listed products that didn't actually ship.\n\n" +
          lines +
          "\n\nPeel them off the liner and DISCARD them - unapplied " +
          "labels aren't kept. The 1-hour Shopify-update clock is " +
          "running; finish the receive in TC-Planner."
      );
      await openPlannerReceive(res.planner.order_id, res.planner.items);
      // The batch STAYS OPEN until the planner saves - locally, back
      // to the bin board; the open list keeps showing it until then.
      batch = null;
      batchItems = [];
      stopBatchLive();
      enterBatchTab();
      setBatchResult(
        "Shipment settled - finish the receive in TC-Planner (the " +
          "batch closes itself when the planner saves).",
        "ok"
      );
    } catch (err) {
      alert(err.message);
    }
  });

document.getElementById("sortship-open").addEventListener("click", async () => {
  document.getElementById("sortship").hidden = false;
  sortShipRestore();
  renderSortShip();
  if (sortShipSeq.length) {
    setSortShipStatus("Picked up the pile from last time - Clear pile starts fresh.");
  }
  try {
    const st = await apiJson("/api/planner/status");
    sortShipPlannerUrl = st.app_url || null;
    if (!st.configured || !st.ok) {
      setSortShipStatus(
        "⚠ The planner bridge isn't answering - every scan will land " +
          "in 'no order explains these' until it's back."
      );
    }
  } catch (err) {
    /* status is decoration */
  }
  document.getElementById("sortship-scan").focus();
});

document.getElementById("sortship-exit").addEventListener("click", () => {
  document.getElementById("sortship").hidden = true;
});


document.getElementById("sortship-clear").addEventListener("click", () => {
  if (
    sortShipSeq.length &&
    !confirm(
      "Clear the scanned pile?\n\nNothing was saved anywhere - this " +
        "just empties the lists here."
    )
  )
    return;
  sortShipRows = {};
  sortShipSeq = [];
  try {
    localStorage.removeItem("sortship_pile");
  } catch (err) {
    /* fine */
  }
  renderSortShip();
  setSortShipStatus("");
  document.getElementById("sortship-scan").focus();
});

document.getElementById("sortship-scan").addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  const val = e.target.value;
  e.target.value = "";
  sortShipScan(val);
});

// --- Component bundles (Nick, 2026-08-31) -----------------------------------
function sortShipExitSelect() {
  sortShipSelect = false;
  sortShipSelected = new Set();
  document.getElementById("sortship-selbar").hidden = true;
  document.getElementById("sortship-bundle").textContent =
    "🧺 Bundle components…";
  renderSortShip();
}

document.getElementById("sortship-bundle").addEventListener("click", () => {
  if (sortShipSelect) {
    sortShipExitSelect();
    return;
  }
  sortShipSelect = true;
  sortShipSelected = new Set();
  document.getElementById("sortship-selbar").hidden = false;
  document.getElementById("sortship-selcount").textContent = "0 selected";
  document.getElementById("sortship-sellink").disabled = true;
  document.getElementById("sortship-bundle").textContent =
    "🧺 Picking components…";
  renderSortShip();
  setSortShipStatus(
    "Tick the component rows that arrive together as ONE listed set, " +
      "then press Link selected."
  );
});

document
  .getElementById("sortship-selcancel")
  .addEventListener("click", sortShipExitSelect);

document
  .getElementById("sortship-sellink")
  .addEventListener("click", () => sortShipCreateBundle());

async function sortShipCreateBundle() {
  // Product-less components are welcome (Nick, 2026-08-31: the S30
  // boxes have no listings of their own) - the raw label is identity
  // enough, and a later product link keeps matching.
  const keys = [...sortShipSelected].filter(
    (k) => sortShipRows[k] && !sortShipRows[k].bundleKey
  );
  if (keys.length < 2) {
    alert("Pick at least two component rows.");
    return;
  }
  const label = prompt(
    "Which SET do these boxes belong to?\n\nScan or type the set's SKU " +
      "or label (for example S30Pro-Set):"
  );
  if (!label || !label.trim()) return;
  let setProduct = null;
  try {
    setProduct = await apiJson(
      `/api/products/by-barcode/${encodeURIComponent(label.trim())}`
    );
  } catch (err) {
    try {
      const m = await apiJson(
        `/api/products/label-match/${encodeURIComponent(label.trim())}`
      );
      if (m.ok) setProduct = m.product;
      else if (
        m.suggestion &&
        confirm(
          `Did you mean ${m.suggestion.sku} ` +
            `("${m.suggestion.product_title}")?`
        )
      ) {
        setProduct = m.suggestion;
      }
    } catch (err2) {
      /* falls through to not-found */
    }
  }
  if (!setProduct || !setProduct.sku) {
    alert(`No product found for "${label}".`);
    return;
  }
  const defKey = setProduct.sku.trim().toUpperCase();
  if (
    sortShipDefs[defKey] &&
    !confirm(
      `A set definition for ${setProduct.sku} already exists - replace it?`
    )
  )
    return;
  sortShipDefs[defKey] = {
    setSku: setProduct.sku,
    setTitle: setProduct.product_title || setProduct.sku,
    components: keys.map((k) => ({
      key: sortShipRows[k].key,
      sku: sortShipRows[k].sku,
      label: sortShipRows[k].term || sortShipRows[k].key,
      title: sortShipRows[k].title,
    })),
  };
  sortShipSaveDefs();
  for (const k of keys) {
    const r = sortShipRows[k];
    r.bundleKey = defKey;
    r.alloc = {};
    r.unexplained = 0;
  }
  delete sortShipSetOrders[defKey];
  await sortShipFetchSetOrders(defKey);
  sortShipSave();
  sortShipExitSelect();
  setSortShipStatus(
    `${keys.length} component(s) linked into ${setProduct.sku} - a full ` +
      `sweep of them counts one set. Remembered for future shipments; ` +
      `Unbundle forgets it.`
  );
}

async function sortShipUnbundle(defKey) {
  const def = sortShipDefs[defKey];
  if (!def) return;
  if (
    !confirm(
      `Unbundle ${def.setSku}?\n\nThe component scans re-sort as their ` +
        `own products, and future scans stop grouping.`
    )
  )
    return;
  delete sortShipDefs[defKey];
  sortShipSaveDefs();
  delete sortShipSetOrders[defKey];
  const members = [];
  for (const c of def.components || []) {
    const row = sortShipMemberRow(c);
    if (row) {
      members.push({ term: row.term || row.key, n: row.scanned, key: row.key });
    }
  }
  for (const m of members) {
    delete sortShipRows[m.key];
    sortShipSeq = sortShipSeq.filter((k) => k !== m.key);
  }
  sortShipSave();
  for (const m of members) {
    for (let i = 0; i < m.n; i++) {
      await sortShipScan(m.term);
    }
  }
  renderSortShip();
  setSortShipStatus(
    `${def.setSku} unbundled - ${members.length} product(s) re-sorted.`
  );
}

// ======================= Home landing page (2026-09-24) =======================
// Navigation layer only: the tiles route to the features exactly as they
// are, the sidebar wears the same .tabs__tab/data-tab contract the old
// top bar did, and the product card is read-and-jump (edits keep living
// in Scan station until each feature's own redesign pass).

const homeEls = {
  layout: document.getElementById("layout"),
  sideToggle: document.getElementById("side-toggle"),
  agentChip: document.getElementById("agent-chip"),
  resume: document.getElementById("resume-card"),
  resumeWhat: document.getElementById("resume-what"),
  resumeMeta: document.getElementById("resume-meta"),
  resumeGo: document.getElementById("resume-go"),
  lookup: document.getElementById("home-lookup"),
  drop: document.getElementById("home-drop"),
  pcard: document.getElementById("pcard"),
  pcardImg: document.getElementById("pcard-img"),
  pcardName: document.getElementById("pcard-name"),
  pcardCodes: document.getElementById("pcard-codes"),
  pcardChips: document.getElementById("pcard-chips"),
  badgeReceive: document.getElementById("tile-badge-receive"),
  badgeChecks: document.getElementById("tile-badge-checks"),
  badgeRfid: document.getElementById("tile-badge-rfid"),
};

function goTab(name) {
  const btn = document.querySelector(`.tabs__tab[data-tab="${name}"]`);
  if (btn) btn.click();
}

// --- sidebar: collapsed = icon rail, remembered per device -----------------
if (localStorage.getItem("sideRail") === "1") {
  homeEls.layout.classList.add("layout--rail");
}
homeEls.sideToggle.addEventListener("click", () => {
  const rail = homeEls.layout.classList.toggle("layout--rail");
  localStorage.setItem("sideRail", rail ? "1" : "0");
});

// --- printer chip: agent heartbeat + the Zebra's own status register --------
function homeAgo(seconds) {
  if (seconds == null) return "";
  if (seconds < 90) return "just now";
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}

async function refreshAgentChip() {
  try {
    const res = await apiFetch("/api/print-agent/status");
    if (!res.ok) throw new Error("status " + res.status);
    const s = await res.json();
    let cls = "reset pill agent-chip";
    let text;
    if (!s.online) {
      cls += " pill--bad";
      const seen = homeAgo(s.last_seen_seconds);
      text = "Print agent offline" + (seen ? ` - last seen ${seen}` : "");
    } else if (s.fault) {
      cls += " pill--bad";
      text = `Printer fault: ${s.fault}`;
    } else if (s.wedged) {
      cls += " pill--bad";
      text = "Printer wedged - labels stuck in its queue";
    } else if ((s.holding || 0) > 0) {
      cls += " pill--warn";
      text = `${s.holding} label(s) held - printer busy`;
    } else if ((s.win_jobs || 0) > 0) {
      cls += " pill--warn";
      text = `Printing - ${s.win_jobs} in queue`;
    } else if (s.agent_last_error
               && /createfile|usb|open|handle|unreachable/i.test(
                    s.agent_last_error)) {
      // the AGENT answers but the printer itself does not
      cls += " pill--bad";
      text = "Printer offline";
    } else {
      cls += " pill--ok";
      text = "Printer online";
    }
    homeEls.agentChip.className = cls;
    homeEls.agentChip.textContent = text;
    homeEls.agentChip.title = "Printer status and options";
    homeEls.agentChip.hidden = false;
  } catch (err) {
    homeEls.agentChip.hidden = true;
  }
}
homeEls.agentChip.addEventListener("click", () => openPrinterPicker(null));
setInterval(refreshAgentChip, 60000);
refreshAgentChip();

// --- home data: resume card + tile badges -----------------------------------
async function loadHome() {
  homeLoadSuggest();
  refreshAgentChip();
  const [batches, queue, board] = await Promise.all([
    apiFetch("/api/batches?status=open&limit=10")
      .then((r) => (r.ok ? r.json() : null)).catch(() => null),
    apiFetch("/api/audit/bins")
      .then((r) => (r.ok ? r.json() : null)).catch(() => null),
    apiFetch("/api/oneleft/board")
      .then((r) => (r.ok ? r.json() : null)).catch(() => null),
  ]);
  const rows = (batches && batches.batches) || [];
  const recv = rows.filter((b) => b.kind === "receiving");
  homeEls.badgeReceive.hidden = recv.length === 0;
  homeEls.badgeReceive.textContent =
    recv.length === 1 ? "1 open" : `${recv.length} open`;
  const taskN = (queue && queue.overdue_count) || 0;
  homeEls.badgeRfid.hidden = !taskN;
  homeEls.badgeRfid.textContent =
    taskN === 1 ? "1 bin due" : `${taskN} bins due`;
  const checkN = (board && board.ok && board.count) || 0;
  homeEls.badgeChecks.hidden = !checkN;
  homeEls.badgeChecks.textContent =
    checkN === 1 ? "1 check" : `${checkN} checks`;
  // The resume card always says SOMETHING: the open task, the waiting
  // work, or an honest all-clear (Nick, 2026-09-24).
  const b = rows[0];
  homeEls.resume.hidden = false;
  homeEls.resumeWhat.classList.remove("resume__what--ok");
  if (b) {
    homeEls.resumeWhat.textContent =
      b.kind === "receiving"
        ? `Receiving batch #${b.id}`
        : `Bin ${b.bin_name} (batch #${b.id})`;
    const bits = [];
    if (b.boxes) bits.push(`${b.paired || 0} of ${b.boxes} paired`);
    else if (b.products) bits.push(`${b.products} product(s)`);
    if (b.created_by) bits.push(b.created_by);
    homeEls.resumeMeta.textContent = bits.join(" \u00b7 ");
    homeEls.resumeGo.dataset.batchId = String(b.id);
    homeEls.resumeGo.hidden = false;
  } else if (taskN + checkN > 0) {
    homeEls.resumeWhat.textContent = "Nothing to resume";
    homeEls.resumeMeta.textContent = [
      checkN ? `${checkN} inventory check${checkN === 1 ? "" : "s"} waiting` : "",
      taskN ? `${taskN} bin audit${taskN === 1 ? "" : "s"} due` : "",
    ].filter(Boolean).join(" \u00b7 ");
    homeEls.resumeGo.hidden = true;
  } else {
    homeEls.resumeWhat.textContent = "\u2713 All clear - nothing to pick up";
    homeEls.resumeWhat.classList.add("resume__what--ok");
    homeEls.resumeMeta.textContent = "";
    homeEls.resumeGo.hidden = true;
  }
  if (homeEls.pcard.hidden) homeEls.lookup.focus();
}

homeEls.resumeGo.addEventListener("click", () => {
  const id = parseInt(homeEls.resumeGo.dataset.batchId || "", 10);
  goTab("batch");
  if (id) resumeBatch(id);
});

// --- tiles route to today's features ---------------------------------------
document.querySelectorAll("#tab-home [data-go]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const go = btn.dataset.go;
    if (go === "find") {
      homeEls.lookup.focus();
      return;
    }
    goTab(go);
  });
});

// --- typeahead: catalog cached once per session; digits = a wedge -----------
let homeSuggest = null;
let homeSuggestLoading = false;
let homeDropHot = -1;

async function homeLoadSuggest() {
  if (homeSuggest || homeSuggestLoading) return;
  homeSuggestLoading = true;
  try {
    const res = await apiFetch("/api/products/suggest");
    if (res.ok) homeSuggest = (await res.json()).products || [];
  } catch (err) {
    // suggestions are a nicety - lookups still work without them
  } finally {
    homeSuggestLoading = false;
  }
}

function homeHideDrop() {
  homeEls.drop.hidden = true;
  homeEls.drop.innerHTML = "";
  homeDropHot = -1;
}

function homeRenderDrop(hits) {
  homeEls.drop.innerHTML = "";
  hits.forEach((p) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "lookup__opt";
    const thumb = p[3]
      ? `<span class="thumb"><img src="${escapeHtml(p[3])}" alt="" loading="lazy" /></span>`
      : '<span class="thumb">\u{1F4E6}</span>';
    b.innerHTML =
      thumb +
      `<span class="t"><b>${escapeHtml(p[1] || p[0])}</b>` +
      `<span>${escapeHtml(p[0])}</span></span>`;
    b.addEventListener("click", () => {
      homeHideDrop();
      homeEls.lookup.value = "";
      openProductCard(p[0]);
    });
    homeEls.drop.appendChild(b);
  });
  homeEls.drop.hidden = hits.length === 0;
  homeDropHot = -1;
}

homeEls.lookup.addEventListener("input", () => {
  homeLookBrowsing = false;
  homeLookHistIdx = -1;
  const q = homeEls.lookup.value.trim();
  // A wedge scan is a burst of digits that ends in Enter - digits never
  // open the list, they resolve on the Enter.
  if (q.length < 2 || /^\d+$/.test(q) || !homeSuggest) {
    homeHideDrop();
    return;
  }
  const ql = q.toUpperCase();
  const hits = homeSuggest
    .filter((p) =>
      (p[0] || "").toUpperCase().includes(ql) ||
      (p[1] || "").toUpperCase().includes(ql)
    )
    .slice(0, 8);
  homeRenderDrop(hits);
});

// Recent-lookup history in the search box (Nick, 2026-09-24): Up
// walks earlier lookups exactly like the Scan station box, Down walks
// back toward the draft, and each entry previews its product in the
// dropdown when the catalog knows it.
let homeLookHist = [];
try {
  homeLookHist = JSON.parse(localStorage.getItem("homeLookupHist")) || [];
} catch (err) { homeLookHist = []; }
let homeLookHistIdx = -1;
let homeLookDraft = "";
let homeLookBrowsing = false;

function homeRememberLookup(term) {
  const t = (term || "").trim();
  if (!t) return;
  homeLookHist = [t, ...homeLookHist.filter((x) => x !== t)].slice(0, 10);
  localStorage.setItem("homeLookupHist", JSON.stringify(homeLookHist));
  homeLookHistIdx = -1;
  homeLookBrowsing = false;
}

function homeShowHistPreview(term) {
  const tl = term.toUpperCase();
  const hit = (homeSuggest || []).find(
    (x) => (x[0] || "").toUpperCase() === tl
        || (x[2] || "").toUpperCase() === tl
  );
  if (hit) homeRenderDrop([hit]);
  else homeHideDrop();
}

homeEls.lookup.addEventListener("keydown", (event) => {
  const opts = Array.from(homeEls.drop.querySelectorAll(".lookup__opt"));
  if (event.key === "ArrowUp") {
    event.preventDefault();
    if (!homeLookBrowsing && opts.length > 1 && homeDropHot < opts.length - 1
        && homeEls.lookup.value.trim() && homeDropHot >= 0) {
      homeDropHot = Math.max(homeDropHot - 1, 0);
      opts.forEach((o, i) =>
        o.classList.toggle("lookup__opt--hot", i === homeDropHot));
      return;
    }
    if (!homeLookHist.length) return;
    if (!homeLookBrowsing) {
      homeLookDraft = homeEls.lookup.value;
      homeLookBrowsing = true;
      homeLookHistIdx = -1;
    }
    homeLookHistIdx = Math.min(homeLookHistIdx + 1, homeLookHist.length - 1);
    homeEls.lookup.value = homeLookHist[homeLookHistIdx];
    homeEls.lookup.select();
    homeShowHistPreview(homeEls.lookup.value);
    return;
  }
  if (event.key === "ArrowDown") {
    event.preventDefault();
    if (homeLookBrowsing) {
      homeLookHistIdx -= 1;
      if (homeLookHistIdx < 0) {
        homeLookBrowsing = false;
        homeEls.lookup.value = homeLookDraft;
        homeHideDrop();
        return;
      }
      homeEls.lookup.value = homeLookHist[homeLookHistIdx];
      homeEls.lookup.select();
      homeShowHistPreview(homeEls.lookup.value);
      return;
    }
    if (opts.length) {
      homeDropHot = Math.min(homeDropHot + 1, opts.length - 1);
      opts.forEach((o, i) =>
        o.classList.toggle("lookup__opt--hot", i === homeDropHot));
    }
    return;
  }
  if (event.key === "Escape") {
    homeHideDrop();
    homeLookBrowsing = false;
    homeLookHistIdx = -1;
    return;
  }
  if (event.key !== "Enter") return;
  event.preventDefault();
  if (!homeLookBrowsing && homeDropHot >= 0 && opts[homeDropHot]) {
    opts[homeDropHot].click();
    return;
  }
  const term = homeEls.lookup.value.trim();
  if (!term) return;
  homeHideDrop();
  homeEls.lookup.value = "";
  homeLookBrowsing = false;
  openProductCard(term);
});
document.addEventListener("click", (event) => {
  if (!homeEls.drop.hidden && !homeEls.drop.contains(event.target)
      && event.target !== homeEls.lookup) {
    homeHideDrop();
  }
});

// --- the product preview card ----------------------------------------------
// Round 3 (Nick, 2026-09-24): structured identity column with On hand /
// Tags stat cells, filterable history, open-box boxes folded in, a
// leaner Shopify-info pane with live buckets, and the four-box label
// editor with a TRUE print preview (real Code 128 bars, the agent's own
// wrap arithmetic - print_agent.build_zpl ported line for line).

let pcardState = null;

document.getElementById("pcard-close").addEventListener("click", () => {
  homeEls.pcard.hidden = true;
  pcardState = null;
  homeEls.lookup.focus();
});

function pcardShowTab(name) {
  document.querySelectorAll(".pcard__tabbtn").forEach((b) =>
    b.classList.toggle("pcard__tabbtn--active", b.dataset.ptab === name));
  ["history", "tags", "shopify", "rfid"].forEach((n) => {
    document.getElementById(`pcard-pane-${n}`).hidden = n !== name;
  });
  const fslot = document.getElementById("pcard-filter-slot");
  if (fslot) fslot.style.display = name === "history" ? "" : "none";
  if (name === "shopify") pcardEnsureShopify();
  if (name === "rfid") pcardEnsureLabelEditor();
}
document.querySelectorAll(".pcard__tabbtn").forEach((btn) => {
  btn.addEventListener("click", () => pcardShowTab(btn.dataset.ptab));
});

function pcardPane(name) {
  return document.getElementById(`pcard-pane-${name}`);
}

function pcardWhen(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString([], {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

function pcardOpenInScan(term) {
  goTab("scan");
  stationBarcodeScan(term);
}

// ---------- identity column ----------
function pcardRenderIdentity() {
  const st = pcardState;
  const p = st.product;
  homeEls.pcardName.textContent =
    (p.product_title || st.sku || st.term) +
    (p.variant_title && p.variant_title !== "Default Title"
      ? ` - ${p.variant_title}` : "");
  homeEls.pcardImg.innerHTML = "";
  if (p.image_url) {
    const img = document.createElement("img");
    img.src = p.image_url;
    img.alt = "";
    homeEls.pcardImg.appendChild(img);
  } else {
    homeEls.pcardImg.textContent = "\u{1F4E6}";
  }
  const rows = [];
  if (st.sku) {
    rows.push(
      `<div class="coderow"><label>SKU</label><span>${escapeHtml(st.sku)}</span></div>`
    );
  }
  if (st.barcode) {
    rows.push(
      `<div class="coderow"><label>Barcode</label><span>${escapeHtml(st.barcode)}</span></div>`
    );
  }
  rows.push(
    `<div class="coderow"><label>Bin</label>` +
    `<button class="reset pchip pchip--btn" id="pcard-bin-chip" type="button" ` +
    `title="Change this product\u2019s bin (writes to Shopify)">` +
    `${p.bin_location ? escapeHtml(p.bin_location) : "set a bin"}</button>` +
    `</div>`
  );
  homeEls.pcardCodes.innerHTML = rows.join("");
  document.getElementById("pcard-bin-chip")
    .addEventListener("click", pcardEditBin);
  // stat cells fill as their numbers arrive
  document.getElementById("pcard-onhand").hidden = true;
  document.getElementById("pcard-tagcount").hidden = true;
  homeEls.pcardChips.innerHTML = "";
}

function pcardRenderStats() {
  const st = pcardState;
  if (st.adminUrl) {
    const nameText = homeEls.pcardName.textContent;
    homeEls.pcardName.innerHTML =
      `<a class="pcard__namelink" href="${escapeHtml(st.adminUrl)}" ` +
      `target="_blank" rel="noopener" ` +
      `title="Open this product in Shopify admin">${escapeHtml(nameText)}</a>`;
  }
  const onhandBtn = document.getElementById("pcard-onhand");
  const tagsBtn = document.getElementById("pcard-tagcount");
  const onHand = st.tags ? st.tags.on_hand : null;
  if (onHand != null) {
    document.getElementById("pcard-onhand-num").textContent = onHand;
    onhandBtn.hidden = false;
  }
  const tagRows = (st.tags && st.tags.assignments) || [];
  document.getElementById("pcard-tagcount-num").textContent = tagRows.length;
  tagsBtn.hidden = false;
  // "Set on-hand to the tags number" (Nick, 2026-09-28): one confirmed
  // write that aligns Shopify with the tag records - shown only when
  // the two disagree. Case tags count their units.
  const setBtn = document.getElementById("pcard-onhand-set");
  if (setBtn) {
    const tagUnits = tagRows.reduce(
      (a, t) => a + (t.case_units || 1), 0
    );
    const show =
      onHand != null && st.sku && tagUnits !== onHand;
    setBtn.hidden = !show;
    if (show) {
      setBtn.textContent = `Set to ${tagUnits} (tags)`;
      setBtn.title =
        `Write Shopify on-hand to ${tagUnits} - the units the tag ` +
        `records carry. Confirmed, History-logged, one Undo.` +
        (tagUnits < onHand
          ? " Lowering runs through the guarded path (sales-backed, " +
            "or a previously batch-tagged product)."
          : "");
      setBtn.dataset.qty = String(tagUnits);
      setBtn.dataset.cur = String(onHand);
    }
  }
  const chips = [];
  if (st.tags && st.tags.rfid_incompatible) {
    chips.push('<span class="pchip pchip--warn">Won’t RFID scan</span>');
  }
  homeEls.pcardChips.innerHTML = chips.join("");
}
document.getElementById("pcard-onhand").addEventListener("click", () => {
  const url = pcardState && pcardState.adminUrl;
  if (url) window.open(url, "_blank", "noopener");
});
document
  .getElementById("pcard-onhand-set")
  .addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const st = pcardState;
    if (!st || !st.sku) return;
    const qty = parseInt(btn.dataset.qty, 10);
    const cur = parseInt(btn.dataset.cur, 10);
    if (!Number.isFinite(qty) || qty === cur) return;
    const lowering = qty < cur;
    if (
      !confirm(
        `Set Shopify ON-HAND for ${st.sku} to ${qty}?\n\n` +
          `Shopify carries ${cur}; the tag records carry ${qty} ` +
          `unit(s).` +
          (lowering
            ? `\n\nLowering runs through the guarded path - sales ` +
              `cover what they can, and a product that never ` +
              `completed a batch tagging only lowers as far as ` +
              `recorded sales cover (the server enforces it).`
            : ``) +
          `\n\nThis WRITES the number to Shopify. Undo stays ` +
          `available in History.`
      )
    )
      return;
    btn.disabled = true;
    try {
      const res = lowering
        ? await postJson("/api/onhand-updates/lower", {
            sku: st.sku,
            bin_name:
              (st.product && st.product.bin_location) || "unknown",
            new_qty: qty,
            epcs: [],
            changed_by: operatorEl.value || null,
            confirmed: true,
          })
        : await postJson("/api/onhand-updates", {
            sku: st.sku,
            new_qty: qty,
            changed_by: operatorEl.value || null,
            confirmed: true,
          });
      alert(res.message);
      document.getElementById("pcard-onhand-num").textContent = qty;
      btn.dataset.cur = String(qty);
      btn.hidden = true;
      if (st.tags) st.tags.on_hand = qty;
    } catch (err) {
      alert(err.message);
    }
    btn.disabled = false;
  });
document.getElementById("pcard-tagcount").addEventListener("click", () =>
  pcardShowTab("tags"));

// ---------- history pane: style B (Nick, 2026-09-24, round 4) ----------
// Day groups; fixed-width COLOURED family chips; fixed-width right
// column showing who + the chain's EARLIEST time only; stock-order
// numbers move off the right column into the description; per-type
// chains (gap <= 60 min, interleaving never breaks, anchored at the
// earliest event, chronological within a day); the filter lives in the
// card's tab strip with a Show-all row, styled fully-clickable
// checkbox rows, and per-device DEFAULT visibility set in Settings.
let pcardHistFilters = new Set();
const PCARD_CHAIN_MS = 60 * 60 * 1000;
const HIST_HIDDEN_KEY = "histHiddenTypes";
const HIST_HIDDEN_FACTORY = ["shopify-bin-read"];

function histHiddenDefaults() {
  try {
    const raw = JSON.parse(localStorage.getItem(HIST_HIDDEN_KEY));
    if (Array.isArray(raw)) return new Set(raw);
  } catch (err) { /* fall through to factory */ }
  return new Set(HIST_HIDDEN_FACTORY);
}

function pcardFamily(type) {
  const t = (type || "").toLowerCase();
  if (t.startsWith("tag") || t.includes("-tag") || t === "not-our-tag")
    return "TAGS";
  if (t.includes("print") || t.includes("label")) return "LABELS";
  if (t === "order-sold" || t.includes("sold") || t.includes("ship"))
    return "SOLD";
  if (t.includes("on-hand") || t.includes("count") || t.includes("recount")
      || t.includes("backorder") || t.includes("ledger")) return "STOCK";
  if (t.startsWith("bin") || t.includes("rebinned")
      || t === "shopify-bin-read") return "BIN";
  if (t.startsWith("review")) return "REVIEW";
  if (t.includes("barcode") || t.includes("sku") || t.includes("draft")
      || t.includes("vendor") || t.includes("alias")) return "PRODUCT";
  return "OTHER";
}

function pcardDayKey(d) {
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}
function pcardDayTitle(d) {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const that = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diff = Math.round((today - that) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return d.toLocaleDateString([], {
    month: "short", day: "numeric",
    year: d.getFullYear() === now.getFullYear() ? undefined : "numeric",
  });
}
function pcardClock(d) {
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function pcardBuildChains(events) {
  const evs = events
    .map((ev) => ({ ev, t: new Date(ev.at || 0) }))
    .filter((x) => !Number.isNaN(x.t.getTime()))
    .sort((a, b) => a.t - b.t);
  const lastByType = {};
  const chains = [];
  for (const x of evs) {
    const prev = lastByType[x.ev.type];
    if (prev && x.t - prev.lastT <= PCARD_CHAIN_MS) {
      prev.items.push(x);
      prev.lastT = x.t;
    } else {
      const c = { type: x.ev.type, items: [x], firstT: x.t, lastT: x.t };
      chains.push(c);
      lastByType[x.ev.type] = c;
    }
  }
  return chains;
}

function pcardChainText(c) {
  const meta = EVENT_META[c.type] || [c.type, "var(--ink-dim)"];
  if (c.items.length === 1) {
    const ev = c.items[0].ev;
    return { title: meta[0], desc: ev.detail || "" };
  }
  if (c.type === "order-sold") {
    const qty = c.items.reduce((s, x) => s + (x.ev.qty || 1), 0);
    return {
      title: `Sold ${qty}`,
      desc: `${c.items.length} orders, ` +
        `${pcardClock(c.firstT)} to ${pcardClock(c.lastT)}`,
    };
  }
  return {
    title: `${meta[0]} ×${c.items.length}`,
    desc: `${pcardClock(c.firstT)} to ${pcardClock(c.lastT)}`,
  };
}

// "TC-Planner · SO 968" in the worker slot: the SO number belongs in
// the description, the planner's name stays beside the date.
function pcardSplitWorker(worker) {
  const w = (worker || "").trim();
  const m = w.match(/SO[\s#-]*\d+/i);
  if (!m) return { who: w, so: "" };
  // Receiving batches pack "TC-Planner · SO 943, SO 965 · Svbony" into
  // the worker slot. The SO lands in the description, so the right
  // column keeps ONLY who did it (Nick, 2026-09-24): the part before
  // the first separator, with any SO tokens scrubbed out of it.
  const who = w.split("·")[0]
    .replace(/SO[\s#-]*\d+[,\s]*/gi, "")
    .replace(/^[\s·.,-]+|[\s·.,-]+$/g, "");
  return { who, so: m[0].replace(/\s+/g, " ") };
}

function pcardVisibleTypes(allTypes) {
  if (pcardHistFilters.size) return new Set(pcardHistFilters);
  const hidden = histHiddenDefaults();
  return new Set(allTypes.filter((t) => !hidden.has(t)));
}

function pcardRenderHistFilter(types) {
  const slot = document.getElementById("pcard-filter-slot");
  const active = pcardHistFilters;
  const allRow =
    `<label class="pfilter__row pfilter__row--all${active.size ? "" : " on"}">` +
    `<span class="cb">${active.size ? "" : "✓"}</span>` +
    "<b>Show all</b></label>";
  const rows = types.map((tp) => {
    const meta = EVENT_META[tp] || [tp, "var(--ink-dim)"];
    const on = active.has(tp);
    return (
      `<label class="pfilter__row${on ? " on" : ""}" data-ftype="${escapeHtml(tp)}">` +
      `<span class="cb">${on ? "✓" : ""}</span>` +
      `<span class="pfilter__dot" style="background:${meta[1]}"></span>` +
      `<span style="color:${meta[1]}">${escapeHtml(meta[0])}</span></label>`
    );
  });
  slot.innerHTML =
    `<details class="pfilter"><summary>Filter${active.size ? ` (${active.size})` : ""} ▾</summary>` +
    `<div class="pfilter__panel">${allRow}${rows.join("")}</div></details>`;
  const det = slot.querySelector(".pfilter");
  slot.querySelectorAll(".pfilter__row").forEach((row) => {
    row.addEventListener("click", (e) => {
      e.preventDefault();
      const tp = row.dataset.ftype;
      if (!tp) pcardHistFilters.clear();
      else if (pcardHistFilters.has(tp)) pcardHistFilters.delete(tp);
      else pcardHistFilters.add(tp);
      pcardRenderHistory();
      document.getElementById("pcard-filter-slot")
        .querySelector(".pfilter").open = true;
    });
  });
  det.open = false;
}

function pcardRenderHistory() {
  const st = pcardState;
  const pane = pcardPane("history");
  const events = (st.hist && st.hist.events) || [];
  const slot = document.getElementById("pcard-filter-slot");
  if (!events.length) {
    pane.innerHTML =
      '<div class="pcard__note">No recorded events yet.</div>';
    slot.innerHTML = "";
    return;
  }
  const types = [];
  const seenT = new Set();
  events.forEach((ev) => {
    if (!seenT.has(ev.type)) { seenT.add(ev.type); types.push(ev.type); }
  });
  const visible = pcardVisibleTypes(types);
  const shown = events.filter((ev) => visible.has(ev.type));
  const chains = pcardBuildChains(shown);
  const days = new Map();
  chains.forEach((c) => {
    const k = pcardDayKey(c.firstT);
    if (!days.has(k)) days.set(k, { d: c.firstT, chains: [] });
    days.get(k).chains.push(c);
  });
  const dayList = Array.from(days.values()).sort((a, b) => b.d - a.d);
  let html = "";
  let idx = 0;
  const chainRefs = [];
  for (const day of dayList) {
    html += `<div class="ph-day">${escapeHtml(pcardDayTitle(day.d))}</div>`;
    day.chains.sort((a, b) => a.firstT - b.firstT);
    for (const c of day.chains) {
      const meta = EVENT_META[c.type] || [c.type, "var(--ink-dim)"];
      const t = pcardChainText(c);
      const time = pcardClock(c.firstT);
      const wk = c.items.length === 1
        ? pcardSplitWorker(c.items[0].ev.worker) : { who: "", so: "" };
      let desc = t.desc || "";
      if (wk.so) desc = desc ? `${desc} · ${wk.so}` : wk.so;
      const expandable = c.items.length > 1;
      chainRefs[idx] = c;
      html +=
        `<div class="ph-row${expandable ? " ph-row--x" : ""}" data-chain="${idx}" style="border-left-color:${meta[1]}">` +
        `<span class="ph-tag" style="background:${meta[1]}26;color:${meta[1]};border-color:transparent">${escapeHtml(pcardFamily(c.type))}</span>` +
        `<span class="ph-txt"><b>${escapeHtml(t.title)}</b>` +
        (desc ? ` <span class="ph-desc">${escapeHtml(desc)}</span>` : "") +
        "</span>" +
        `<span class="ph-right">${escapeHtml([wk.who, time].filter(Boolean).join(" · "))}` +
        (expandable ? ' <span class="ph-caret">▾</span>' : "") +
        "</span></div>" +
        (expandable ? `<div class="ph-sub" data-sub="${idx}" hidden></div>` : "");
      idx++;
    }
  }
  pane.innerHTML =
    html || '<div class="pcard__note">Nothing to show - the filter (or the Settings defaults) hides every event here.</div>';
  pcardRenderHistFilter(types);
  pane.querySelectorAll(".ph-row--x").forEach((row) => {
    row.addEventListener("click", () => {
      const c = chainRefs[parseInt(row.dataset.chain, 10)];
      const sub = pane.querySelector(`[data-sub="${row.dataset.chain}"]`);
      if (!sub.hidden) { sub.hidden = true; return; }
      if (!sub.innerHTML) {
        sub.innerHTML = c.items.map((x) => {
          const ev = x.ev;
          if (ev.type === "order-sold") {
            const link = ev.order_admin_url;
            const inner =
              `<b>Order ${escapeHtml(String(ev.order_name || "?"))}</b>` +
              `<span class="pchip">${ev.qty || 1} unit${(ev.qty || 1) === 1 ? "" : "s"}</span>` +
              `<span class="dim">${escapeHtml([pcardClock(x.t), ev.source].filter(Boolean).join(" · "))}</span>` +
              (link ? '<span class="ph-go">Open in Shopify admin ↗</span>' : "");
            return link
              ? `<button class="ph-order" type="button" data-url="${escapeHtml(link)}">${inner}</button>`
              : `<div class="ph-order">${inner}</div>`;
          }
          return `<div class="ph-order"><span class="dim">${escapeHtml(pcardClock(x.t))}</span>` +
            `<span>${escapeHtml(ev.detail || "")}</span>` +
            (ev.worker ? `<span class="dim">${escapeHtml(ev.worker)}</span>` : "") +
            "</div>";
        }).join("");
        sub.querySelectorAll("button.ph-order").forEach((b) =>
          b.addEventListener("click", (e) => {
            e.stopPropagation();
            window.open(b.dataset.url, "_blank", "noopener");
          }));
      }
      sub.hidden = false;
    });
  });
}

// ---------- boxes & tags pane (open-box twin folded in) ----------
// Condition chip removed (Nick, 2026-09-28); the paired line says who
// paired the tag and through what work (Manually scanned, Receiving,
// Batch tagging, Printed label - server-derived per tag).
function pcardTagRow(t) {
  const loc = t.bin_location
    ? `<span class="pchip">${escapeHtml(t.bin_location)}</span>`
    : '<span class="dim">no bin</span>';
  const extra = [];
  if (t.case_units && t.case_units > 1) extra.push(`case of ${t.case_units}`);
  if (t.assigned_at) {
    extra.push(
      "paired " + pcardWhen(t.assigned_at) +
      (t.assigned_by ? " by " + t.assigned_by : "") +
      (t.source ? " · " + t.source : "")
    );
  } else if (t.source) {
    extra.push(t.source);
  }
  return (
    '<div class="prow">' +
    `<span class="epc">${escapeHtml(t.rfid_id)}</span>` +
    loc +
    `<span class="dim">${escapeHtml(extra.join(" · "))}</span>` +
    `<button class="prow__locate" type="button" ` +
    `data-epc="${escapeHtml(t.rfid_id || "")}" ` +
    `data-lsku="${escapeHtml(t.sku || "")}" ` +
    `title="Queue this sticker on the C72 locate list (open LOCATE on the gun and tap LIST)">Locate</button>` +
    `<button class="prow__unpair" type="button" ` +
    `data-epc="${escapeHtml(t.rfid_id || "")}" ` +
    `data-usku="${escapeHtml(t.sku || "")}" ` +
    `title="Remove this tag record - the sticker is gone, damaged, or on the wrong box. History-logged as Tag Unlinked; Shopify untouched.">Unpair</button>` +
    "</div>"
  );
}

function pcardRenderTags() {
  const st = pcardState;
  const pane = pcardPane("tags");
  const rows = (st.tags && st.tags.assignments) || [];
  const obRows = (st.ob && st.ob.assignments) || [];
  let html = rows.length
    ? rows.map((t) => pcardTagRow(t)).join("")
    : '<div class="pcard__note">No live tags on file for this product.</div>';
  if (obRows.length) {
    html +=
      `<div class="pdivider">Open box (${escapeHtml(st.obSku)})</div>` +
      obRows.map((t) => pcardTagRow(t)).join("");
  }
  pane.innerHTML = html;
  // Un-bundling (Nick, 2026-09-28): bundles are moving to per-
  // component SKUs. A product with defined bundle contents offers the
  // split here: confirm the contents and the box count, the bundle's
  // tags retire as "unbundled", and component labels print.
  (async () => {
    if (!st.sku) return;
    const bc = await apiFetch(
      `/api/bundle-contents?sku=${encodeURIComponent(st.sku)}`
    ).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    if (pcardState !== st) return;
    const contents = (bc && bc.contents) || [];
    if (!contents.length) return;
    const box = document.createElement("div");
    box.className = "unbundle";
    box.innerHTML =
      '<div class="pdivider">Un-bundle into components</div>' +
      '<div class="pcard__note">Confirm what one bundle box holds, say ' +
      "how many bundle boxes there are, and this retires the bundle's " +
      "tags (peel the stickers) and prints labels for every component " +
      "box.</div>" +
      contents.map((c, i) =>
        `<div class="unbundle__row"><span class="mono">${escapeHtml(c.component_sku)}</span>` +
        `<input type="number" min="0" max="50" value="${c.qty || 1}" data-ub="${i}" /> per bundle</div>`
      ).join("") +
      '<div class="unbundle__row">Bundle boxes on hand: ' +
      '<input type="number" min="1" max="100" value="1" id="unbundle-units" /></div>' +
      '<button class="reset" id="unbundle-go" type="button">Un-bundle…</button>' +
      '<span class="pcard__note" id="unbundle-msg"></span>';
    pane.append(box);
    box.querySelector("#unbundle-go").addEventListener("click", async () => {
      const msg = box.querySelector("#unbundle-msg");
      const units = Math.max(1, parseInt(
        box.querySelector("#unbundle-units").value, 10) || 1);
      const picked = contents
        .map((c, i) => ({
          sku: c.component_sku,
          qty: Math.max(0, parseInt(
            box.querySelector(`[data-ub="${i}"]`).value, 10) || 0),
        }))
        .filter((c) => c.qty > 0);
      if (!picked.length) {
        msg.textContent = "Nothing to print - every component is 0.";
        return;
      }
      const body = {
        units,
        contents: picked,
        printer: (typeof selectedPrinter !== "undefined" && selectedPrinter) || null,
        worker: operatorEl.value || null,
      };
      try {
        let res = await apiFetch(
          `/api/bundles/${encodeURIComponent(st.sku)}/unbundle`,
          { method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body) }
        );
        if (res.status === 409) {
          const q = (await res.json()).detail;
          if (!confirm(q)) return;
          res = await apiFetch(
            `/api/bundles/${encodeURIComponent(st.sku)}/unbundle`,
            { method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ ...body, confirmed: true }) }
          );
        }
        const d = await res.json();
        if (!res.ok) throw new Error(d.detail || res.status);
        msg.textContent = d.message;
      } catch (err) {
        msg.textContent = err.message;
      }
    });
  })();
  // Locate (Nick, 2026-09-24): each sticker rides the existing C72
  // locate queue. Re-queuing a SKU replaces its specific-EPC list, so
  // the click merges this EPC with whatever the queue already holds.
  pane.querySelectorAll(".prow__locate").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const sku = btn.dataset.lsku || st.sku;
      if (!sku) return;
      btn.disabled = true;
      try {
        const q = await apiJson("/api/locate-queue").catch(() => null);
        const mine = q && (q.entries || []).find(
          (e) => (e.sku || "").toUpperCase() === sku.toUpperCase());
        const epcs = new Set(mine ? mine.epcs || [] : []);
        epcs.add(btn.dataset.epc);
        await postJson("/api/locate-queue", {
          sku,
          label: (st.product && st.product.product_title) || sku,
          worker: operatorEl.value || null,
          epcs: Array.from(epcs),
        });
        btn.textContent = "Queued ✓";
        btn.classList.add("prow__locate--on");
        btn.title = "On the C72 locate list - open LOCATE on the gun and tap LIST.";
      } catch (err) {
        btn.textContent = "Failed";
        btn.disabled = false;
      }
    });
  });
  // Unpair (Nick, 2026-09-28): the button opens a small note popover
  // under the row - an optional note (rides the History event), with
  // Cancel / Submit below the box.
  pane.querySelectorAll(".prow__unpair").forEach((btn) => {
    btn.addEventListener("click", () => {
      const row = btn.closest(".prow");
      const open = row.nextElementSibling;
      if (open && open.classList.contains("unpair-pop")) {
        open.remove();
        return;
      }
      pane.querySelectorAll(".unpair-pop").forEach((p) => p.remove());
      const pop = document.createElement("div");
      pop.className = "unpair-pop";
      pop.innerHTML =
        `<input type="text" maxlength="200" placeholder="Optional Note"
           class="unpair-pop__note" />
         <div class="unpair-pop__btns">
           <button class="reset unpair-pop__cancel" type="button">Cancel</button>
           <button class="reset unpair-pop__go" type="button">Submit</button>
         </div>`;
      row.after(pop);
      const noteEl = pop.querySelector(".unpair-pop__note");
      noteEl.focus();
      pop.querySelector(".unpair-pop__cancel")
        .addEventListener("click", () => pop.remove());
      pop.querySelector(".unpair-pop__go")
        .addEventListener("click", async () => {
          const go = pop.querySelector(".unpair-pop__go");
          go.disabled = true;
          try {
            await apiJson(
              `/api/rfid-assignments/${encodeURIComponent(btn.dataset.epc)}` +
                `?by=${encodeURIComponent(operatorEl.value || "")}` +
                `&note=${encodeURIComponent(noteEl.value.trim())}`,
              { method: "DELETE" }
            );
            // Fresh tag list, quietly - the pane repaints itself.
            const qs = st.sku
              ? "sku=" + encodeURIComponent(st.sku)
              : "barcode=" + encodeURIComponent(st.barcode || st.term);
            const [fresh, freshOb] = await Promise.all([
              apiFetch(`/api/products/tags?${qs}`)
                .then((r) => (r.ok ? r.json() : null)).catch(() => null),
              st.ob
                ? apiFetch(
                    `/api/products/tags?light=1&sku=${encodeURIComponent(st.obSku)}`
                  ).then((r) => (r.ok ? r.json() : null)).catch(() => null)
                : null,
            ]);
            if (pcardState !== st) return;
            if (fresh) st.tags = fresh;
            st.ob =
              freshOb && (freshOb.assignments || []).length ? freshOb : null;
            pcardRenderStats();
            pcardRenderTags();
          } catch (err) {
            go.disabled = false;
            alert("Unpair failed: " + err.message);
          }
        });
    });
  });
}

// ---------- shopify info pane (v3: hover-diff tiles + Chart.js) ----------
function pcardChangeDay(iso) {
  const d = new Date(iso || 0);
  return Number.isNaN(d.getTime()) ? null : d;
}

// Each change row reads kind first ("Sold"), then the cause ("Order
// #50950"), then units near the middle (Nick, 2026-09-24).
const PSHIP_KINDS = {
  sold: "Sold",
  received: "Received",
  manual: "Manually adjusted",
};

function pshipCause(ch) {
  const who = String(ch.who || "").trim();
  if (ch.kind === "sold") {
    return who.startsWith("#") ? `Order ${who}` : `Order ${who || "?"}`;
  }
  if (ch.kind === "received") {
    // who arrives as "SO 943" or "SO 943 · Svbony" - keep the number.
    const n = who.replace(/^SO\s*/i, "").split("·")[0].trim();
    return `Stock Order ${n || "?"}`;
  }
  if (ch.kind === "manual") return who || "operator";
  return who || "";
}

let pshipChart = null; // the one live Chart.js instance for the pane

function pshipDrawChart(st) {
  const canvas = document.getElementById("pship-chart");
  const note = document.getElementById("pship-chartnote");
  if (!canvas) return;
  const daily = (st.salesDaily || []);
  const graph = (document.getElementById("pship-graph") || {}).value || "sold";
  const bucket = (document.getElementById("pship-bucket") || {}).value || "week";
  const dur = (document.getElementById("pship-duration") || {}).value || "3m";
  if (pshipChart) { pshipChart.destroy(); pshipChart = null; }
  if (typeof Chart === "undefined") {
    if (note) note.textContent = "Graphs need /static/vendor/chart.umd.min.js (blocked or missing).";
    return;
  }
  // ---- window ----
  const now = new Date();
  let from = null;
  if (dur === "4w") from = new Date(now - 28 * 864e5);
  else if (dur === "3m") from = new Date(now - 91 * 864e5);
  else if (dur === "ytd") from = new Date(now.getFullYear(), 0, 1);
  else if (dur === "1y") from = new Date(now - 365 * 864e5);
  else if (dur === "5y") from = new Date(now - 5 * 365 * 864e5);
  const rows = daily
    .map(([d, u]) => [new Date(d + "T12:00:00"), u])
    .filter(([d]) => !from || d >= from);
  // ---- bucket keys (day / ISO week / month), zero-filled across the span
  const keyOf = (d) => {
    if (bucket === "day") return d.toISOString().slice(0, 10);
    if (bucket === "month") return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    const w = new Date(d);
    w.setDate(w.getDate() - ((w.getDay() + 6) % 7)); // back to Monday
    return w.toISOString().slice(0, 10);
  };
  const stepFwd = (d) => {
    const n = new Date(d);
    if (bucket === "day") n.setDate(n.getDate() + 1);
    else if (bucket === "month") n.setMonth(n.getMonth() + 1);
    else n.setDate(n.getDate() + 7);
    return n;
  };
  const sums = new Map();
  rows.forEach(([d, u]) => {
    const k = keyOf(d);
    sums.set(k, (sums.get(k) || 0) + u);
  });
  const start = from || (rows.length ? rows[0][0] : new Date(now - 91 * 864e5));
  const labels = [];
  const data = [];
  let cum = 0;
  for (let d = new Date(start); d <= now && labels.length < 400; d = stepFwd(d)) {
    const k = keyOf(d);
    if (labels.length && labels[labels.length - 1].k === k) continue;
    const v = sums.get(k) || 0;
    cum += v;
    labels.push({ k, d: new Date(d) });
    data.push(graph === "cumulative" ? cum : v);
  }
  if (note) note.textContent = "";
  const css = getComputedStyle(document.documentElement);
  const accent = (css.getPropertyValue("--accent") || "#005bd3").trim();
  const dim = (css.getPropertyValue("--ink-dim") || "#6d7175").trim();
  const line = (css.getPropertyValue("--line") || "#e1e3e5").trim();
  const fmt = (l) => {
    const d = l.d;
    if (bucket === "month") {
      return d.toLocaleDateString(undefined, { month: "short", year: "2-digit" });
    }
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  };
  pshipChart = new Chart(canvas, {
    type: graph === "cumulative" ? "line" : "bar",
    data: {
      labels: labels.map(fmt),
      datasets: [{
        data,
        backgroundColor: graph === "cumulative" ? accent + "22" : accent,
        borderColor: accent,
        borderWidth: graph === "cumulative" ? 2 : 0,
        fill: graph === "cumulative",
        pointRadius: 0,
        tension: 0.25,
        borderRadius: 2,
        maxBarThickness: 26,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 200 },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (c) => `${c.parsed.y} unit${c.parsed.y === 1 ? "" : "s"}` +
              (graph === "cumulative" ? " total" : " sold"),
          },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { color: dim, font: { size: 10 }, maxTicksLimit: 9,
                   maxRotation: 0 },
        },
        y: {
          beginAtZero: true,
          grid: { color: line + "80" },
          ticks: { color: dim, font: { size: 10 }, precision: 0 },
        },
      },
    },
  });
}

async function pcardEnsureShopify() {
  const st = pcardState;
  if (!st || st.shopifyLoaded) return;
  st.shopifyLoaded = true;
  const pane = pcardPane("shopify");
  pane.innerHTML = '<div class="pcard__note">Loading live numbers…</div>';
  const bd = st.sku
    ? await apiFetch(`/api/products/${encodeURIComponent(st.sku)}/stock-breakdown`)
        .then((r) => (r.ok ? r.json() : null)).catch(() => null)
    : null;
  if (pcardState !== st) return;
  const b = (bd && bd.breakdown) || null;
  const BUCKETS = [
    ["available", "Available"], ["committed", "Committed"],
    ["on_hand", "On hand"], ["unavailable", "Unavailable"],
  ];

  // ----- left column: bucket tiles, vendor, Inventory Changes -----
  let left = "";
  if (b) {
    left += '<div class="pinforow" id="pship-tiles">' + BUCKETS.map(
      ([k, label]) =>
        `<div class="pstat"><span class="pstat__num" data-bucket="${k}">` +
        `${b[k] != null ? b[k] : "-"}</span>` +
        `<span class="pstat__lbl">${label}</span></div>`
    ).join("") + "</div>";
  } else {
    left +=
      '<div class="pcard__note">Live stock buckets unavailable' +
      (bd && bd.breakdown_error ? ` (${escapeHtml(bd.breakdown_error)})` : "") +
      ".</div>";
  }
  if (bd && bd.vendor) {
    left += `<div class="pship__vendor">Vendor · <b>${escapeHtml(bd.vendor)}</b></div>`;
  }
  const TINTS = { sold: "ok", received: "ok", manual: "warn" };
  // Unavailable-bucket moves stay out of the table (Nick, 2026-09-24) -
  // they still shape the hover estimates server-side.
  const changes = ((bd && bd.inventory_changes) || [])
    .filter((ch) => ch.kind !== "unavailable");
  left += '<div class="pdivider">Inventory changes</div>';
  if (!changes.length) {
    left += '<div class="pcard__note">No recorded stock movements yet ' +
      "(direct Shopify-admin edits leave no trail here).</div>";
  } else {
    let lastDay = "";
    left += '<div class="pship__list" id="pship-list">';
    changes.slice(0, 60).forEach((ch, i) => {
      const d = pcardChangeDay(ch.at);
      const dayKey = d ? pcardDayKey(d) : "?";
      if (dayKey !== lastDay) {
        lastDay = dayKey;
        left += `<div class="ph-day">${escapeHtml(d ? pcardDayTitle(d) : "Unknown date")}</div>`;
      }
      const units = ch.units > 0 ? `+${ch.units}` : String(ch.units);
      left +=
        `<div class="pship__row pship__row--${TINTS[ch.kind] || ""}" data-chg="${i}">` +
        `<span class="pship__kind">${escapeHtml(PSHIP_KINDS[ch.kind] || ch.kind)}</span>` +
        `<span class="pship__cause">${escapeHtml(pshipCause(ch))}</span>` +
        `<span class="pship__units">${escapeHtml(units)}</span>` +
        `<span class="pship__time">${d ? escapeHtml(pcardClock(d)) : ""}</span>` +
        "</div>";
    });
    left += "</div>";
  }

  // ----- right column: the graph + its dropdowns (the "Sales" control
  // row lives at the BOTTOM, under the chart) -----
  const daily = (bd && bd.sales && bd.sales.daily) || [];
  st.salesDaily = daily;
  let right;
  if (!daily.length) {
    right = '<div class="pcard__note">No recorded sales for this product yet.</div>';
  } else {
    right =
      '<div class="pship__chartbox"><canvas id="pship-chart"></canvas></div>' +
      '<div class="pcard__note" id="pship-chartnote"></div>' +
      '<div class="pship__ctl">' +
      '<select id="pship-graph" title="Which graph">' +
      '<option value="sold">Units sold</option>' +
      '<option value="cumulative">Cumulative sold</option>' +
      "</select>" +
      '<select id="pship-bucket" title="Bucket size">' +
      '<option value="day">Per day</option>' +
      '<option value="week" selected>Per week</option>' +
      '<option value="month">Per month</option>' +
      "</select>" +
      '<select id="pship-duration" title="How far back">' +
      '<option value="4w">4 weeks</option>' +
      '<option value="3m" selected>3 months</option>' +
      '<option value="ytd">Year to date</option>' +
      '<option value="1y">1 year</option>' +
      '<option value="5y">5 years</option>' +
      '<option value="all">All time</option>' +
      "</select>" +
      "</div>";
  }
  pane.innerHTML =
    `<div class="pship"><div class="pship__left">${left}</div>` +
    `<div class="pship__right">${right}</div></div>`;

  // Hovering a change re-paints the tiles as "before → after", red on a
  // drop, green on a rise, plain when that bucket did not move.
  const tiles = document.getElementById("pship-tiles");
  if (tiles) {
    const restore = () => {
      tiles.querySelectorAll(".pstat__num").forEach((el) => {
        el.classList.remove("pstat__num--diff", "pstat__num--down",
                            "pstat__num--up");
        const k = el.dataset.bucket;
        el.textContent = b && b[k] != null ? b[k] : "-";
      });
    };
    pane.querySelectorAll(".pship__row[data-chg]").forEach((row) => {
      const ch = changes[parseInt(row.dataset.chg, 10)];
      if (!ch || !ch.before || !ch.after) return;
      row.addEventListener("mouseenter", () => {
        tiles.querySelectorAll(".pstat__num").forEach((el) => {
          const k = el.dataset.bucket;
          const was = ch.before[k];
          const is = ch.after[k];
          el.classList.remove("pstat__num--diff", "pstat__num--down",
                              "pstat__num--up");
          if (was == null || is == null || was === is) {
            el.textContent = is != null ? is : (b && b[k] != null ? b[k] : "-");
            return;
          }
          el.textContent = `${was} → ${is}`;
          el.classList.add("pstat__num--diff",
                           is < was ? "pstat__num--down" : "pstat__num--up");
        });
      });
      row.addEventListener("mouseleave", restore);
    });
  }
  ["pship-graph", "pship-bucket", "pship-duration"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("change", () => pshipDrawChart(st));
  });
  pshipDrawChart(st);
}

// ---------- the four-box label editor + TRUE print preview ----------
// Real Code 128: pattern table (bar/space module widths per symbol).
const C128 = ("212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 " +
  "221312 231212 112232 122132 122231 113222 123122 123221 223211 221132 " +
  "221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 " +
  "212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 " +
  "231113 231311 112133 112331 132131 113123 113321 133121 313121 211331 " +
  "231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 " +
  "314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 " +
  "112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 " +
  "111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 " +
  "214121 412121 111143 111341 131141 114113 114311 411113 411311 113141 " +
  "114131 311141 411131 211412 211214 211232 2331112").split(" ");

function code128Encode(data) {
  // Same subset walk as code128Dots (the width model) so bars and the
  // centering arithmetic can never disagree.
  const vals = [];
  let subset = null;
  let i = 0;
  const n = data.length;
  while (i < n) {
    let run = 0;
    while (i + run < n && data[i + run] >= "0" && data[i + run] <= "9") run++;
    const useC =
      (subset === null && run >= 4) ||
      (subset === "C" && run >= 2) ||
      (subset === "B" && (run >= 6 || (run >= 4 && i + run === n)));
    if (useC) {
      if (subset === null) vals.push(105);
      else if (subset === "B") vals.push(99);
      subset = "C";
      const pairs = Math.floor(run / 2);
      for (let k = 0; k < pairs; k++) {
        vals.push(parseInt(data.slice(i + k * 2, i + k * 2 + 2), 10));
      }
      i += pairs * 2;
    } else {
      if (subset === null) vals.push(104);
      else if (subset === "C") vals.push(100);
      subset = "B";
      const code = data.charCodeAt(i) - 32;
      vals.push(code >= 0 && code < 95 ? code : 0);
      i++;
    }
  }
  if (!vals.length) vals.push(104);
  let ck = vals[0];
  for (let k = 1; k < vals.length; k++) ck += vals[k] * k;
  vals.push(ck % 103);
  return vals;
}

function code128Svg(data, module, x, y, height) {
  const vals = code128Encode(data);
  let bars = "";
  let cx = x;
  const draw = (pattern) => {
    for (let k = 0; k < pattern.length; k++) {
      const w = parseInt(pattern[k], 10) * module;
      if (k % 2 === 0) {
        bars += `<rect x="${cx}" y="${y}" width="${w}" height="${height}" fill="#111"/>`;
      }
      cx += w;
    }
  };
  vals.forEach((v) => draw(C128[v]));
  draw(C128[106]); // stop
  return bars;
}

const LABEL_LL = 253; // 1.25in x 203dpi, matching LABEL_PW = 431

function labelSvg(header, centre, barcode, binText, otherBins, dirtyParts) {
  // print_agent.build_zpl, ported: same fonts, same wrap decisions.
  // Each section wraps in a <g data-part> so the editor can focus the
  // matching input from a click on the label itself.
  const esc = escapeHtml;
  const T = (x, y, size, text, anchor = "middle") =>
    `<text x="${x}" y="${y + size * 0.78}" font-size="${size * 0.94}" ` +
    `font-family="'Arial Narrow','Roboto Condensed',Arial,sans-serif" ` +
    `text-anchor="${anchor}" fill="#111">${esc(text)}</text>`;
  const seg = { header: "", desc: "", barcode: "", bin: "" };
  const warns = [];
  // header
  const h = (header || "").trim();
  if (!h || h === STORE_HEADER) {
    seg.header = T(LABEL_PW / 2, 10, 34, STORE_HEADER);
  } else {
    const size = h.length <= 26 ? 28 : h.length <= 56 ? 20 : 16;
    if (zplTextDots(h, size) * SKU_WIDTH_FUDGE <= LABEL_PW) {
      seg.header = T(LABEL_PW / 2, 4 + (size > 20 ? 6 : 8), size, h);
    } else {
      const cut = skuSplit(h);
      const [l1, l2] = cut || [h.slice(0, Math.ceil(h.length / 2)), h.slice(Math.ceil(h.length / 2))];
      seg.header = T(LABEL_PW / 2, 2, size, l1) + T(LABEL_PW / 2, 2 + size + 2, size, l2);
      if (zplTextDots(l1, size) * SKU_WIDTH_FUDGE > LABEL_PW ||
          zplTextDots(l2, size) * SKU_WIDTH_FUDGE > LABEL_PW) {
        warns.push("The header is long - it may clip on the sticker.");
      }
    }
  }
  // centre line (the agent's exact wrap ladder)
  let c = (centre || "").slice(0, 56);
  const manualBreak = c.includes("|");
  const plain = c.replace(/\|/g, " ").split(/\s+/).join(" ");
  const wrapped = manualBreak || !skuFits(plain, 30, 1);
  if (!wrapped) {
    seg.desc = T(LABEL_PW / 2, 52, 30, plain);
  } else {
    const split = skuSplit(c);
    let f = 30;
    if (split) {
      while (f > 20 && !(skuLineFits(split[0], f) && skuLineFits(split[1], f))) f -= 2;
      seg.desc = T(LABEL_PW / 2, 52, f, split[0]) +
                 T(LABEL_PW / 2, 52 + f + 2, f, split[1]);
    } else {
      while (f > 20 && !skuFits(plain, f, 2)) f -= 2;
      const mid = Math.ceil(plain.length / 2);
      seg.desc = T(LABEL_PW / 2, 52, f, plain.slice(0, mid)) +
                 T(LABEL_PW / 2, 52 + f + 2, f, plain.slice(mid));
    }
    if (f <= 20 && !skuFits(plain, 20, 2)) {
      warns.push("The description is very long - the sticker may clip it.");
    }
  }
  // barcode block
  const code = (barcode || "").trim();
  if (code) {
    let module = 2;
    let width = code128Dots(code, module);
    if (width > LABEL_PW - 24) {
      module = 1;
      width = code128Dots(code, module);
    }
    if (width > LABEL_PW - 24) {
      warns.push("The barcode is too long for the sticker width (33 characters is the confirmed max).");
    }
    const geo = wrapped
      ? { by: 118, bh: 56, cy: 178, cf: 18 }
      : { by: 88, bh: 72, cy: 164, cf: 20 };
    const bx = Math.max(2, Math.floor((LABEL_PW - width) / 2));
    seg.barcode = code128Svg(code, module, bx, geo.by, geo.bh) +
                  T(LABEL_PW / 2, geo.cy, geo.cf, code);
  }
  // bin line
  const bt = (binText || "-").trim() || "-";
  const others = (otherBins || "").trim();
  if (others) {
    seg.bin = T(LABEL_PW / 2, LABEL_LL - 52, 22, `BIN: ${bt}. Other: ${others.slice(0, 60)}`);
  } else {
    seg.bin = T(LABEL_PW / 2, LABEL_LL - 45, 30, `BIN: ${bt}`);
  }
  // Unsaved edits show AMBER on the sticker preview and go black the
  // moment they save (Nick, 2026-09-24).
  const dirty = dirtyParts || {};
  for (const part of ["header", "desc", "barcode", "bin"]) {
    if (dirty[part]) {
      seg[part] = seg[part].replace(/fill="#111"/g, 'fill="#c78500"');
    }
  }
  const svg =
    `<svg viewBox="0 0 ${LABEL_PW} ${LABEL_LL}" xmlns="http://www.w3.org/2000/svg">` +
    `<rect x="0" y="0" width="${LABEL_PW}" height="${LABEL_LL}" fill="#fff"/>` +
    `<g data-part="header">${seg.header}</g>` +
    `<g data-part="desc">${seg.desc}</g>` +
    `<g data-part="barcode">${seg.barcode}</g>` +
    `<g data-part="bin">${seg.bin}</g>` +
    "</svg>";
  return { svg, warns };
}

async function pcardEnsureLabelEditor() {
  const st = pcardState;
  if (!st || st.rfidLoaded) return;
  st.rfidLoaded = true;
  const pane = pcardPane("rfid");
  pane.innerHTML = '<div class="pcard__note">Loading label settings…</div>';
  let saved = null;
  if (st.sku) {
    saved = await apiFetch(`/api/label-names/${encodeURIComponent(st.sku)}`)
      .then((r) => (r.ok ? r.json() : null)).catch(() => null);
  }
  if (pcardState !== st) return;
  st.label = saved || {
    label_name: null, placement: "header", sku_text: null,
    barcode_mode: "auto", barcode_text: null, bin_text: null,
  };
  const p = st.product;
  const defaults = {
    header: STORE_HEADER,
    // The centre line prints the SKU when nothing is saved (Nick,
    // 2026-09-24: the editor prefilled the name, which was never what
    // an untouched sticker actually printed).
    desc: st.sku || p.product_title || "",
    barcode: st.barcode || st.sku || "",
    bin: p.bin_location || "",
  };
  const eff = {
    header:
      st.label.label_name && st.label.placement !== "sku"
        ? st.label.label_name : defaults.header,
    desc:
      st.label.sku_text ||
      (st.label.label_name && st.label.placement !== "header"
        ? st.label.label_name : "") || defaults.desc,
    barcode:
      st.label.barcode_text ||
      ((st.label.barcode_mode || "auto") === "sku"
        ? (st.sku || "") : defaults.barcode),
    bin: st.label.bin_text || defaults.bin,
  };
  pane.innerHTML = `
    <div class="labedit">
      <div class="labedit__form">
        <div class="labedit__row">
          <div class="labedit__box">
            <input id="lab-header" maxlength="76" title="Header" value="${escapeHtml(eff.header)}" />
            <button class="labedit__reset" data-reset="header" title="Back to the default">✕</button>
          </div></div>
        <div class="labedit__row">
          <div class="labedit__box">
            <textarea id="lab-desc" maxlength="56" rows="3" title="Description - a | forces the line break">${escapeHtml(eff.desc)}</textarea>
            <button class="labedit__reset labedit__reset--area" data-reset="desc" title="Back to the default">✕</button>
            <button class="labedit__toggle labedit__toggle--area" id="lab-desc-mode" type="button"
                    title="Fill with the product name or the SKU">Name</button>
          </div></div>
        <div class="labedit__row">
          <div class="labedit__box">
            <input id="lab-barcode" maxlength="64" title="What the barcode encodes" value="${escapeHtml(eff.barcode)}" />
            <button class="labedit__reset" data-reset="barcode" title="Back to the default">✕</button>
            <button class="labedit__toggle" id="lab-mode" type="button"
                    title="Fill with the product barcode or the SKU">SKU</button>
          </div></div>
        <div class="labedit__row">
          <div class="labedit__box">
            <span class="prefix">Bin:</span>
            <input id="lab-bin" maxlength="100" title="The bin line" value="${escapeHtml(eff.bin)}" />
            <button class="labedit__reset" data-reset="bin" title="Back to the product's bin">✕</button>
          </div></div>
        <button class="labedit__save" id="lab-save" disabled>Save label</button>
        <div class="pcard__note" id="lab-msg"></div>
      </div>
      <div class="labedit__preview">
        <div class="labedit__svgwrap" id="lab-svg"></div>
        <span class="labedit__printgrp">
          <input id="lab-qty" type="number" min="1" max="200" value="1" />
          <button class="labedit__print" id="lab-print" type="button">Print 1 label</button>
        </span>
        <div class="labedit__warn" id="lab-warn" hidden></div>
      </div>
      <div class="labedit__options">
        <h4>Product options</h4>
        <div class="pcard__note">This column is where the Edit-product
        options land next (see the plan) - flags like won't-RFID-scan and
        non-taggable, scan notes, aliases, serial prefixes, and the
        open-box and bundle tools.</div>
      </div>
    </div>`;
  const els = {
    header: document.getElementById("lab-header"),
    desc: document.getElementById("lab-desc"),
    barcode: document.getElementById("lab-barcode"),
    mode: document.getElementById("lab-mode"),
    descMode: document.getElementById("lab-desc-mode"),
    bin: document.getElementById("lab-bin"),
    save: document.getElementById("lab-save"),
    qty: document.getElementById("lab-qty"),
    print: document.getElementById("lab-print"),
    msg: document.getElementById("lab-msg"),
    svg: document.getElementById("lab-svg"),
    warn: document.getElementById("lab-warn"),
  };
  const savedEff = { ...eff };

  function refresh() {
    const onSku = els.barcode.value.trim() === (st.sku || "");
    els.mode.textContent = onSku && st.barcode ? "Barcode" : "SKU";
    els.mode.disabled = !st.sku && !st.barcode;
    const descOnSku = els.desc.value.trim() === (st.sku || "");
    els.descMode.textContent =
      descOnSku && p.product_title ? "Name" : "SKU";
    els.descMode.disabled = !st.sku && !p.product_title;
    const dirtyParts = {
      header: els.header.value.trim() !== savedEff.header,
      desc: els.desc.value.trim() !== savedEff.desc,
      barcode: els.barcode.value.trim() !== savedEff.barcode,
      bin: els.bin.value.trim() !== savedEff.bin,
    };
    const { svg, warns } = labelSvg(
      els.header.value, els.desc.value, els.barcode.value.trim(),
      els.bin.value, st.label.bin_text ? "" : (p.other_bins || ""),
      dirtyParts
    );
    els.svg.innerHTML = svg;
    els.warn.hidden = warns.length === 0;
    els.warn.textContent = warns.join(" ");
    const dirty =
      els.header.value.trim() !== savedEff.header ||
      els.desc.value.trim() !== savedEff.desc ||
      els.barcode.value.trim() !== savedEff.barcode ||
      els.bin.value.trim() !== savedEff.bin;
    els.save.disabled = !dirty;
    const n = Math.max(1, parseInt(els.qty.value, 10) || 1);
    els.print.textContent = `Print ${n} label${n === 1 ? "" : "s"}`;
  }
  ["header", "desc", "barcode", "bin"].forEach((k) =>
    els[k].addEventListener("input", refresh));
  els.qty.addEventListener("input", refresh);
  // click a line ON THE LABEL to focus its input
  els.svg.addEventListener("click", (e) => {
    const part = e.target.closest("[data-part]");
    if (!part) return;
    const target = {
      header: els.header, desc: els.desc,
      barcode: els.barcode, bin: els.bin,
    }[part.dataset.part];
    if (target) { target.focus(); target.select && target.select(); }
  });
  els.mode.addEventListener("click", () => {
    const onSku = els.barcode.value.trim() === (st.sku || "");
    els.barcode.value = onSku && st.barcode ? st.barcode : (st.sku || "");
    refresh();
  });
  els.descMode.addEventListener("click", () => {
    const onSku = els.desc.value.trim() === (st.sku || "");
    els.desc.value =
      onSku && p.product_title ? p.product_title : (st.sku || "");
    refresh();
  });
  pane.querySelectorAll(".labedit__reset").forEach((btn) => {
    btn.addEventListener("click", () => {
      const k = btn.dataset.reset;
      if (k === "header") els.header.value = defaults.header;
      else if (k === "desc") els.desc.value = defaults.desc;
      else if (k === "barcode") els.barcode.value = defaults.barcode;
      else if (k === "bin") els.bin.value = defaults.bin;
      refresh();
    });
  });
  els.save.addEventListener("click", async () => {
    els.save.disabled = true;
    els.save.textContent = "Saving…";
    try {
      const top = els.header.value.trim();
      const centre = els.desc.value.trim();
      const code = els.barcode.value.trim();
      const bin = els.bin.value.trim();
      const body = {
        top_text: top,
        sku_line: centre,
        barcode_mode:
          code === (st.sku || "") && st.barcode ? "sku" : "auto",
        barcode_text:
          code === defaults.barcode || code === (st.sku || "")
            ? "" : code,
        bin_text: bin === defaults.bin ? "" : bin,
        updated_by: operatorEl.value || null,
      };
      const res = await apiFetch(
        `/api/label-names/${encodeURIComponent(st.sku)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      );
      if (!res.ok) throw new Error("save " + res.status);
      st.label = await res.json();
      savedEff.header = top || defaults.header;
      savedEff.desc = centre || defaults.desc;
      savedEff.barcode = code || defaults.barcode;
      savedEff.bin = bin || defaults.bin;
      refresh(); // the preview goes black the moment the save lands
      els.save.textContent = "Saved ✓";
      els.save.disabled = true;
      setTimeout(() => { els.save.textContent = "Save label"; refresh(); }, 1200);
    } catch (err) {
      els.save.textContent = "Save failed - try again";
      els.save.disabled = false;
    }
  });
  els.print.addEventListener("click", async () => {
    const n = Math.max(1, Math.min(200, parseInt(els.qty.value, 10) || 1));
    els.print.disabled = true;
    try {
      await postJson("/api/print-jobs", {
        quantity: n,
        sku: st.sku || null,
        barcode: st.barcode || null,
        product_title: p.product_title || st.sku || "",
        variant_title: p.variant_title || null,
        bin_location: p.bin_location || null,
        other_bins: p.other_bins || null,
        shopify_variant_id: p.shopify_variant_id || "",
        shopify_product_id: p.shopify_product_id || null,
        printer: (typeof selectedPrinter !== "undefined" && selectedPrinter) || null,
        requested_by: operatorEl.value || null,
      });
      els.msg.textContent =
        `${n} label${n === 1 ? "" : "s"} queued ✓ - they print with ` +
        "the SAVED settings (save first if the preview shows unsaved " +
        "changes).";
    } catch (err) {
      els.msg.textContent = "Could not queue the labels: " + err.message;
    } finally {
      els.print.disabled = false;
    }
  });
  renderLabTuner(pane);
  refresh();
}

// TEMPORARY layout tuner (Nick, 2026-09-24): sliders that drive the
// label editor's spacing variables live, on dev/localhost only, so the
// final numbers can be dialled in on the real page and then baked in.
const LAB_TUNER_VARS = [
  ["--lab-gap", "Column gap", 0, 80],
  ["--lab-form-top", "Form top", -30, 60],
  ["--lab-row-gap", "Row gap", 0, 30],
  ["--lab-box-pady", "Box pad", 2, 16],
  ["--lab-prev-w", "Preview w", 220, 430],
  ["--lab-save-mt", "Save top", 0, 60],
  ["--lab-print-mt", "Print top", 0, 40],
];

function renderLabTuner(pane) {
  const host = location.hostname;
  if (!(host.includes("localhost") || host.includes("-dev"))) return;
  const box = document.createElement("div");
  box.className = "labtuner";
  const out = document.createElement("div");
  out.className = "out";
  const readAll = () => LAB_TUNER_VARS.map(([v]) =>
    `${v}: ${getComputedStyle(document.documentElement).getPropertyValue(v).trim()};`
  ).join(" ");
  box.innerHTML = "<b>Layout tuner (temp)</b>";
  LAB_TUNER_VARS.forEach(([varName, label, min, max]) => {
    const cur = parseInt(
      getComputedStyle(document.documentElement).getPropertyValue(varName)
    ) || 0;
    const row = document.createElement("label");
    row.innerHTML =
      `<span>${label}</span>` +
      `<input type="range" min="${min}" max="${max}" value="${cur}" />` +
      `<span class="val">${cur}px</span>`;
    const slider = row.querySelector("input");
    const val = row.querySelector(".val");
    slider.addEventListener("input", () => {
      document.documentElement.style.setProperty(
        varName, slider.value + "px"
      );
      val.textContent = slider.value + "px";
      out.textContent = readAll();
    });
    box.append(row);
  });
  out.textContent = readAll();
  box.append(out);
  pane.style.position = "relative";
  pane.append(box);
}

// ---------- the lookup itself ----------
// Repeat lookups skip the product-resolve round trip (which can hit
// the live Shopify API when the term isn't in the bin map): the last
// few resolved products are kept for 5 minutes. Tags and history are
// always re-fetched - they're the fast, local calls.
const pcardLookupCache = new Map();
const PCARD_CACHE_MS = 5 * 60 * 1000;

// The card's bundle strip: component rows when the product IS a
// defined bundle, membership chips when it's a component of one.
function pcardRenderBundle() {
  const el = document.getElementById("pcard-bundle");
  if (!el) return;
  const st = pcardState;
  el.hidden = true;
  el.innerHTML = "";
  if (!st || !st.sku) return;
  if (st.bundle) {
    const b = st.bundle;
    el.innerHTML = `<div class="pcb">
      <div class="pcb__head">&#128230; Bundle - the components below carry
        the tags; nothing prints for this listing.${
          b.buildable != null
            ? ` Components on hand can build <b>${b.buildable}</b> unit(s).`
            : ""
        }</div>
      ${b.contents
        .map(
          (c) => `<button class="reset pcb__row" type="button"
            data-sku="${escapeHtml(c.component_sku)}"
            title="Open the component's product card">
            <span class="bndl__qty">${c.qty}&#215;</span>
            <span class="pcb__sku">${escapeHtml(c.component_sku)}</span>
            <span class="pcb__title">${escapeHtml(c.title || "")}</span>
            <span class="bndl__facts">${
              c.bin ? escapeHtml(c.bin) + " · " : ""
            }${c.tags} tag(s)${
              c.on_hand != null ? ` · ${c.on_hand} on hand` : ""
            }</span>
          </button>`
        )
        .join("")}
    </div>`;
    el.hidden = false;
  } else if ((st.partOf || []).length) {
    el.innerHTML = `<div class="pcb pcb--member">
      <span class="pcb__memlab">Part of bundle(s):</span>
      ${st.partOf
        .map((b) => {
          const me = (b.contents || []).find(
            (c) =>
              c.component_sku.toUpperCase() === st.sku.toUpperCase()
          );
          return `<button class="reset pcb__chip" type="button"
            data-sku="${escapeHtml(b.bundle_sku)}"
            title="${escapeHtml(b.title || "Open the bundle's card")}">${escapeHtml(
              b.bundle_sku
            )}${me ? ` (${me.qty}&#215; each)` : ""}</button>`;
        })
        .join("")}
    </div>`;
    el.hidden = false;
  }
}

document.getElementById("pcard-bundle").addEventListener("click", (e) => {
  const go = e.target.closest("[data-sku]");
  if (go) openProductCard(go.dataset.sku);
});

async function openProductCard(term) {
  goTab("home");
  homeEls.pcard.hidden = false;
  pcardHistFilters = new Set();
  homeEls.pcardImg.innerHTML = "";
  homeEls.pcardName.textContent = "Looking up " + term + "…";
  homeEls.pcardCodes.innerHTML = "";
  homeEls.pcardChips.innerHTML = "";
  const bndlEl = document.getElementById("pcard-bundle");
  if (bndlEl) {
    bndlEl.hidden = true;
    bndlEl.innerHTML = "";
  }
  document.getElementById("pcard-onhand").hidden = true;
  document.getElementById("pcard-tagcount").hidden = true;
  const setBtn = document.getElementById("pcard-onhand-set");
  if (setBtn) setBtn.hidden = true;
  ["history", "tags", "shopify", "rfid"].forEach((n) =>
    (pcardPane(n).innerHTML = ""));
  pcardShowTab("rfid");
  homeRememberLookup(term);
  let product = null;
  const cacheKey = term.trim().toUpperCase();
  const hit = pcardLookupCache.get(cacheKey);
  if (hit && Date.now() - hit.ts < PCARD_CACHE_MS) {
    product = hit.product;
  } else {
    try {
      const res = await apiFetch(
        `/api/products/by-barcode/${encodeURIComponent(term)}`
      );
      if (res.ok) product = await res.json();
      else if (res.status !== 404) throw new Error("lookup " + res.status);
    } catch (err) {
      homeEls.pcardName.textContent =
        "Lookup failed - is the network okay? " + (err.message || "");
      return;
    }
    if (product) {
      if (pcardLookupCache.size > 40) pcardLookupCache.clear();
      pcardLookupCache.set(cacheKey, { ts: Date.now(), product });
    }
  }
  if (!product) {
    homeEls.pcardName.textContent = `No product found for "${term}"`;
    pcardPane("history").innerHTML =
      '<div class="pcard__note">Not a known barcode, SKU, or label ' +
      "alias. If it should be, Scan station can link it.</div>";
    return;
  }
  const st = {
    term,
    product,
    sku: (product.sku || "").trim(),
    barcode: (product.barcode || "").trim(),
    tags: null, hist: null, ob: null, adminUrl: null,
    obSku: ((product.sku || "").trim() + "-O"),
  };
  pcardState = st;
  pcardRenderIdentity();
  const isOpenBox = st.sku.toUpperCase().endsWith("-O");
  // The history call passes what was just resolved, so the server
  // skips its own second product lookup; the open-box tags ride
  // light=1 (no second live on-hand fetch). Both were most of the
  // card's fill time (Nick, 2026-09-28).
  const histQs =
    `term=${encodeURIComponent(st.sku || term)}` +
    (st.sku ? `&sku=${encodeURIComponent(st.sku)}` : "") +
    (st.barcode ? `&barcode=${encodeURIComponent(st.barcode)}` : "") +
    (product.shopify_product_id
      ? `&pid=${encodeURIComponent(product.shopify_product_id)}`
      : "");
  const [tagsBody, histBody, obBody, bndlAs, bndlIn] = await Promise.all([
    apiFetch(
      `/api/products/tags?${st.sku ? "sku=" + encodeURIComponent(st.sku) : "barcode=" + encodeURIComponent(st.barcode || term)}`
    ).then((r) => (r.ok ? r.json() : null)).catch(() => null),
    apiFetch(`/api/product-history?${histQs}`)
      .then((r) => (r.ok ? r.json() : null)).catch(() => null),
    st.sku && !isOpenBox
      ? apiFetch(`/api/products/tags?light=1&sku=${encodeURIComponent(st.obSku)}`)
          .then((r) => (r.ok ? r.json() : null)).catch(() => null)
      : null,
    // Bundle context, both directions (round 12). DB-only, cheap.
    st.sku
      ? apiFetch(`/api/bundles?sku=${encodeURIComponent(st.sku)}`)
          .then((r) => (r.ok ? r.json() : null)).catch(() => null)
      : null,
    st.sku
      ? apiFetch(`/api/bundles?component=${encodeURIComponent(st.sku)}`)
          .then((r) => (r.ok ? r.json() : null)).catch(() => null)
      : null,
  ]);
  if (pcardState !== st) return;
  st.tags = tagsBody;
  st.hist = histBody;
  st.ob = obBody && (obBody.assignments || []).length ? obBody : null;
  st.bundle = (bndlAs && (bndlAs.bundles || [])[0]) || null;
  st.partOf = (bndlIn && bndlIn.bundles) || [];
  st.adminUrl = histBody && histBody.product && histBody.product.admin_url;
  pcardRenderBundle();
  pcardRenderStats();
  pcardRenderHistory();
  pcardRenderTags();
  // The default tab was shown before the product existed - run its
  // lazy loader now that the state is real.
  const activeTab =
    document.querySelector(".pcard__tabbtn--active")?.dataset.ptab;
  if (activeTab === "rfid") pcardEnsureLabelEditor();
  else if (activeTab === "shopify") pcardEnsureShopify();
}

// --- boot: Home is the front door (hash still deep-links any tab) -----------
(function homeBoot() {
  const q = new URLSearchParams(location.search).get("tab") || "";
  const want = (q || (location.hash || "").replace("#", "")).toLowerCase();
  const target = Object.prototype.hasOwnProperty.call(tabSections, want)
    ? want : "home";
  goTab(target);
})();


// hover text carries each sidebar label while the rail is collapsed
document.querySelectorAll(".tabs__tab").forEach((b) => {
  const lbl = b.querySelector(".tabs__lbl");
  if (lbl) b.title = lbl.textContent.replace(/WIP/i, "").trim();
});

// --- printer window: agent health + remote restart (Nick, 2026-09-24) -------
async function renderPrinterAgentHealth() {
  const line = document.getElementById("printer-agentline");
  const fix = document.getElementById("printer-agentfix");
  line.textContent = "Checking the print agent…";
  fix.hidden = true;
  try {
    const res = await apiFetch("/api/print-agent/status");
    if (!res.ok) throw new Error("status " + res.status);
    const st = await res.json();
    let msg;
    if (!st.online) {
      const seen = homeAgo(st.last_seen_seconds);
      msg =
        "⚠ The print agent is not polling this site" +
        (seen ? ` (last seen ${seen})` : "") +
        ". On the dev site that is normal - the warehouse agent serves " +
        "the production queue only. A queued restart waits until an " +
        "agent polls; a truly dead agent must be started at the " +
        "warehouse PC once.";
    } else {
      msg =
        `✓ Print agent v${st.agent_version || "?"} online` +
        (st.fault
          ? ` - printer fault: ${st.fault}`
          : st.wedged
            ? " - Windows queue wedged (use Clear stuck jobs)"
            : " - printer healthy");
    }
    line.textContent = msg;
    fix.hidden = false;
  } catch (err) {
    line.textContent = "Could not read the agent status.";
  }
}
document
  .getElementById("printer-restart")
  .addEventListener("click", async () => {
    if (
      !confirm(
        "Restart the print agent on the warehouse PC?\n\nIt exits " +
          "cleanly and its runner starts it fresh - this clears a " +
          "stuck state and picks up any pending agent update. Nothing " +
          "mid-label is lost (jobs re-verify on startup). If no agent " +
          "is polling, the command simply waits until one does."
      )
    )
      return;
    try {
      await postJson("/api/printer-commands", {
        printer: selectedPrinter || null,
        kind: "restart",
        requested_by: operatorEl.value || null,
      });
      document.getElementById("printer-msg").textContent =
        "Restart queued ✓ - a polling agent restarts within " +
        "seconds; this window re-checks in 15.";
      setTimeout(renderPrinterAgentHealth, 15000);
    } catch (err) {
      document.getElementById("printer-msg").textContent =
        "Could not queue the restart: " + err.message;
    }
  });

// Bin chip on the product card: one tap re-bins the product through the
// same confirmed Shopify write the Scan station uses (logged with undo).
async function pcardEditBin() {
  const st = pcardState;
  if (!st || !(st.sku || st.barcode)) return;
  const current = st.product.bin_location || "";
  const bin = prompt(
    "New bin for " + (st.sku || st.barcode) +
    " (writes the bin to Shopify and moves the local records):",
    current
  );
  if (bin == null) return;
  const clean = bin.trim();
  if (!clean || clean === current) return;
  try {
    await postJson("/api/bin-updates", {
      target: st.sku || st.barcode,
      bin: clean,
      changed_by: operatorEl.value || null,
    });
    st.product.bin_location = clean;
    pcardRenderIdentity();
    pcardRenderStats();
  } catch (err) {
    alert("Could not set the bin: " + err.message);
  }
}


// Event colours expander (Nick, 2026-09-24): the right half of the
// Settings page is reserved for large windows like this one - the
// list only appears when its row is opened.
document.getElementById("evcolor-expand").addEventListener("click", () => {
  const panel = document.querySelector(".settingspage__colors");
  const btn = document.getElementById("evcolor-expand");
  panel.hidden = !panel.hidden;
  btn.classList.toggle("open", !panel.hidden);
  if (!panel.hidden) renderEvColorList();
});
