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
  "batch-reprinted": ["Batch Reprint", "#5c5f62"],
  "printing-stopped": ["Stopped Printing", "#d72c0d"],
  "printing-resumed": ["Resumed Printing", "#116329"],
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
  "inventory-check": ["Inventory Check", "#8a6116"],
  "pairing-incomplete": ["Pairing Incomplete", "#d72c0d"],
  "unresolved-barcode": ["Unresolved Barcode", "#d72c0d"],
  "could-not-scan": ["Could Not Scan", "#8a6116"],
  "bin-mismatch": ["Mismatched Bins", "#0e7a8a"],
  "tags-rebinned": ["Tags Re-binned", "#0e7a8a"],
  "bundle-contents-set": ["Bundle Contents", "#6f42c1"],
  "locate-list": ["Locate List", "#5561c9"],
  oneleft: ["1-left Check", "#b07d00"],
  "audit-session": ["Audit Session", "#0e7a8a"],
  "bin-audited": ["Audit Done", "#0b6e99"],
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

// Settings → Event colours: a picker + hex box + live preview per event.
function renderEvColorList() {
  const wrap = document.getElementById("evcolor-list");
  const overrides = eventColorOverrides();
  wrap.innerHTML = "";
  Object.keys(EVENT_META)
    .sort((a, b) => evLabel(a).localeCompare(evLabel(b)))
    .forEach((type) => {
      const current = overrides[type] || EVENT_META[type][1];
      const row = document.createElement("div");
      row.className = "evcolor-row";
      row.innerHTML = `
        <span class="evcolor-preview">${evChip(type)}</span>
        <input type="color" value="${current}" aria-label="colour for ${escapeHtml(evLabel(type))}" />
        <input type="text" class="linkbox__input evcolor-hex" value="${current}" maxlength="7" spellcheck="false" />`;
      const picker = row.querySelector('input[type="color"]');
      const hex = row.querySelector(".evcolor-hex");
      const save = (value) => {
        if (!/^#[0-9a-fA-F]{6}$/.test(value)) return;
        const o = eventColorOverrides();
        if (value.toLowerCase() === EVENT_META[type][1].toLowerCase()) {
          delete o[type];
        } else {
          o[type] = value;
        }
        localStorage.setItem("eventColors", JSON.stringify(o));
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
      wrap.append(row);
    });
}

document.getElementById("evcolor-open").addEventListener("click", () => {
  document.getElementById("settings-menu").open = false;
  renderEvColorList();
  document.getElementById("evcolor-overlay").hidden = false;
});
document.getElementById("evcolor-close").addEventListener("click", () => {
  document.getElementById("evcolor-overlay").hidden = true;
});
document.getElementById("evcolor-reset").addEventListener("click", () => {
  localStorage.removeItem("eventColors");
  applyEventColors();
  renderEvColorList();
});
document
  .getElementById("evcolor-overlay")
  .addEventListener("click", (e) => {
    if (e.target.id === "evcolor-overlay")
      document.getElementById("evcolor-overlay").hidden = true;
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
  scan: [document.getElementById("tab-scan"), document.getElementById("scan-footer")],
  batch: [document.getElementById("tab-batch")],
  inventory: [document.getElementById("tab-inventory")],
  queue: [document.getElementById("tab-queue")],
  review: [document.getElementById("tab-review")],
  audits: [document.getElementById("tab-audits")],
  history: [document.getElementById("tab-history")],
};
const tabLoaders = {
  batch: () => enterBatchTab(),
  inventory: () => loadInventory(),
  queue: () => loadQueue(),
  review: () => loadReview(),
  audits: () => loadAudits(),
  history: () => loadHistory(),
};
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
    if (tabLoaders[name]) tabLoaders[name]();
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
    if (product.alias_warning) {
      openConfirmBox(product);
      return;
    }
    acceptProduct(
      product,
      product.serial_brand
        ? `${product.serial_brand} serial number recognized — the first ` +
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
        `the label name is ${label.length} characters — it gets cut off ` +
          `at 76 and prints crowded`
      );
    else if (label.length > zplLineChars(size, 2))
      problems.push(
        "the label name is too long for two printed lines — they would " +
          "overlap"
      );
  }
  const sku = String(p.sku || "").trim();
  if (sku.length > zplLineChars(30, 1))
    problems.push(
      `the SKU (${sku}) is longer than one printed line — it overlaps itself`
    );
  const bin =
    p.bin_location && p.bin_location !== "No bin assigned"
      ? p.bin_location
      : "";
  if (bin && `BIN: ${bin}`.length > zplLineChars(30, 1))
    problems.push(
      `the bin line (BIN: ${bin}) is longer than one printed line — it ` +
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
    warnEl.textContent = `⚠ Label will print badly: ${problems[0]} — update the text before printing.`;
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
      "Auto-print skipped: the label name isn't confirmed yet — check the " +
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
      "Auto-print held: no bin assigned — click the bin chip to set one " +
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
    `${data.barcode} isn't a product of its own — it's a box holding ` +
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
    `${code} — record what's inside so every scan of it says so.`;
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
    ? "Serial recognized — store SKU outdated"
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
  el.linkboxTitle.textContent = "Linked barcode — confirm the item";
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
    "Serial not recognized — set flow opened. Scan the remaining filters, " +
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
      "That filter's prefix is already in a slot — scan a different one.",
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
      li.innerHTML = `${escapeHtml(s.title)} — ${escapeHtml(s.variant || "")}
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
    setResult("Filter set registered — rescanning…", "ok");
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
    ["edit-barcode", "edit-barcode-save", editDefaults.barcode, false],
    // An empty note is a VALID save (it clears the note) — only equality
    // with the saved value greys the button.
    ["edit-note", "edit-note-save", editDefaults.note, true],
  ];
  rows.forEach(([inputId, saveId, def, emptyOk]) => {
    const value = document.getElementById(inputId).value.trim();
    document.getElementById(saveId).disabled =
      value === def || (!emptyOk && !value);
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

// SKU + barcode go through the SAME audited Shopify-write endpoints as
// the unknown-barcode flows (History-logged there); the checkbox ritual
// is replaced by a confirm() since the greyed-at-saved-value buttons
// already stop accidental no-op writes.
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
    const res = await postJson("/api/sku-overwrites", {
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
    if (
      !confirm(
        `Replace this product's barcode in Shopify?\n\n${editDefaults.barcode || "(none)"} → ${newBarcode}\n\nPermanent (History keeps the record).`
      )
    )
      return;
    const btn = document.getElementById("edit-barcode-save");
    btn.disabled = true;
    try {
      const res = await postJson("/api/barcode-overwrites", {
        new_barcode: newBarcode,
        target: editDefaults.sku || editDefaults.barcode,
        changed_by: operator,
        confirmed: true,
      });
      editDefaults.barcode = newBarcode;
      pendingProduct.barcode = newBarcode;
      el.pBarcode.textContent = newBarcode; // the card behind follows
      if (res.product)
        renderAliasPreview({ ...pendingProduct, ...res.product });
      editMsg(
        `Barcode updated to ${newBarcode} ✓ (History-logged)` +
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
    editMsg(note ? "Scan note saved ✓ — it shows on every scan." : "Scan note cleared ✓");
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
      setResult(`Prefix ${prefix} saved — rescanning…`, "ok");
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
      `📦 On order: ${data.total_remaining} more expected — ${pos}`;
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
  head.textContent = top;
  head.className =
    "label-preview__header " +
    (top === STORE_HEADER || top.length <= 26
      ? "label-preview__header--lg"
      : top.length <= 56
        ? "label-preview__header--md"
        : "label-preview__header--sm");
  document.getElementById("p-prev-sku").textContent = skuLine || "—";
  document.getElementById("p-prev-bc").textContent =
    p.barcode || p.sku || "";
  document.getElementById("p-prev-bin").textContent =
    "BIN: " +
    (p.bin_location && p.bin_location !== "No bin assigned"
      ? p.bin_location
      : "—");
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
              ? '<span class="suspect" title="Probably a bad read — ' +
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
  // A little icon in the header (left of ⚙): the choice applies to every
  // label this device queues, so it lives outside any one tab. Only on
  // stations that can print at all.
  btn.hidden = !printingEnabled;
  btn.textContent = "🖨";
  btn.title = selectedPrinter
    ? `Printer: ${selectedPrinter} — click to change`
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
      `<p class="linkbox__text" style="grid-column:1/-1">No printers detected yet. ` +
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
        : "Use it anyway (offline — labels wait)";
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
    `Yes — print all ${qty}`;
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
              : `Label printed ✓ — scan the RFID tag.`,
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
  el.printStatus.textContent += " (still queued — agent will print when up)";
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
    setResult("No product loaded — scan a barcode first.", "err", "rfid");
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
          `characters (tags are normally 24) — likely a bad read. ` +
          `Re-scan this tag into inventory to be safe.`,
        "err",
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
    if (saved.suspect || !el.autoReset.checked) {
      // Keep the product loaded (stay-on-product mode, or so a flagged
      // tag can be re-scanned immediately).
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
    const note = ` · ⚠ ${n} terminals listening — labels can print twice.`;
    if (!linkStatus.textContent.includes("terminals listening")) {
      linkStatus.textContent =
        (linkStatus.textContent + note).slice(0, 200);
    }
  }
}

function linkGunStatusText(guns) {
  if (!guns.length) {
    return "Listening — no C72 checking in right now. Scans will act " +
      "here once a gun is on its LINK tab.";
  }
  const onLink = guns.filter((g) => g.tab === "link");
  if (onLink.length) {
    return `Listening — "${onLink[0].device}" is on its LINK tab. ` +
      "Gun scans act here now.";
  }
  if (guns.length === 1) {
    const tab = guns[0].tab ? ` (on the ${guns[0].tab} tab)` : "";
    return `Listening — "${guns[0].device}" is online${tab}. ` +
      "Open LINK on the gun.";
  }
  const names = guns.map((g) => `"${g.device}"`).join(", ");
  return `Listening — ${guns.length} guns online (${names}). ` +
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
    recvRememberPairs([s.value], item.id, itemDisplayName(item));
    await pullBatch(false);
    const fresh =
      (batchItems || []).find((i) => i.id === item.id) || item;
    const t =
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
    out = { ok: false, text: "No product loaded — scan a barcode first." };
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
          `Resumed — ${skipped} scan(s) sent while this screen was ` +
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
        "Gun scans act on EVERY listening terminal — a label scan " +
        "would print twice.\n\nTurn LINK ON here anyway?"
      );
      if (!go) {
        linkRelease();
        linkStatus.textContent = interactive
          ? `Left OFF — ${listeners[0].operator || "another terminal"} ` +
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
    linkStatus.textContent = "Could not reach the server — try again.";
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
            ? ` — ${bulkPrinted - bulkTagged} to go.`
            : ".")
        : `${bulkTagged} tag(s) assigned · no labels printed this visit, ` +
          `so no auto-reset target — Reset (Esc) when done.`;
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
      `All ${bulkPrinted} label(s) printed this visit are tagged ✓ — ` +
        `resetting.`,
      "ok",
      "rfid"
    );
    bulkWarnEl.hidden = true;
    setTimeout(resetStation, 900);
  } else if (bulkTagged > bulkPrinted && bulkTagged > bulkWarnedAt) {
    document.getElementById("bulk-warn-text").textContent =
      `${bulkTagged} tag(s) assigned against ${bulkPrinted} label(s) ` +
      `printed this visit — a spare or blank label in range may have ` +
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
        "No C72 sweeps received yet — SWEEP then SEND on the gun first.",
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
        (dup ? ` · ${dup} already assigned — skipped` : "") +
        ".",
      res.count > 0 ? "ok" : "err",
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
        `Sweep undone — ${res.count} tag(s) unlinked (History has the ` +
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
  setResult("Kept — the over-count stands.", "ok", "rfid");
});

// --- Recent list -----------------------------------------------------------
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
          '24-character EPC — probably a bad read. Re-scan this tag into ' +
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

async function loadInventory() {
  const body = document.getElementById("inv-body");
  try {
    const res = await apiFetch("/api/inventory/summary");
    if (!res.ok) {
      body.innerHTML =
        '<tr><td colspan="7" class="inventory__empty">Could not load inventory.</td></tr>';
      return;
    }
    const data = await res.json();
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
  } catch (err) {
    body.innerHTML =
      '<tr><td colspan="7" class="inventory__empty">Network error.</td></tr>';
  }
}

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

  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="7" class="inventory__empty">${
      filtered
        ? "Nothing matches those filters."
        : "No products yet — assign or print a first tag."
    }</td></tr>`;
    return;
  }
  body.innerHTML = rows
    .map((p) => {
      const title =
        productLink(p.product_title, p.shopify_product_id, p.sku) +
        (p.variant_title
          ? ` <span class="inventory__variant">(${escapeHtml(p.variant_title)})</span>`
          : "") +
        (p.rfid_incompatible
          ? ' <span class="noscan-chip" title="tag won\'t scan when on ' +
            'box — sweeps don\'t expect it to answer">⊘ no RFID</span>'
          : "");
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
            ? `<span class="skulink" data-sku="${escapeHtml(p.sku)}" title="Open this product — label editor, RFID flag, full history">${escapeHtml(p.sku)}</span>`
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
          p.unit_breakdown
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
          `bin write — Shopify, the bin map and this product's tags all ` +
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

async function loadResumeList() {
  try {
    const { batches } = await apiJson("/api/batches?status=open&limit=10");
    bEl.resumeList.innerHTML = "";
    bEl.resumeWrap.hidden = !batches.length;
    batches.forEach((b) => {
      const li = document.createElement("li");
      const label =
        b.kind === "receiving"
          ? "📦 Receiving"
          : `Bin ${escapeHtml(b.bin_name)}`;
      li.innerHTML =
        `<b>${label}</b> — ${b.products} product(s), ` +
        `${b.boxes} box(es), ${b.paired} paired · ${escapeHtml(b.status)} ` +
        `<span class="mono">${escapeHtml(fmtWhen(b.created_at))}` +
        `${b.created_by ? " · " + escapeHtml(b.created_by) : ""}</span>`;
      li.addEventListener("click", () => resumeBatch(b.id));
      bEl.resumeList.append(li);
    });
  } catch (err) {
    bEl.resumeWrap.hidden = true;
  }
}

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
            "carried over were tagged — the rest of this shelf was " +
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
            ? `Nothing left to do — ${binBoard.hidden_count} bin(s) are hidden.`
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
          ? `<span class="binlist__odd" title="Bin name doesn't match the A1-2 format (one letter, then 1-99, dash, 1-99). Usually means one product's stock is split across shelves — worth fixing in Shopify before tagging this bin.">⚠ odd name</span>`
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
      "No sweep on file yet — on the C72, open SWEEP, hold the trigger " +
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
        `Every tag read marks its product as already tagged — those boxes ` +
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
        ? `${batchItems.length} product(s) expected in bin ${batch.bin_name} — start scanning boxes.`
        : `Nothing on file for bin ${batch.bin_name} — scan boxes and they'll be added.`) +
        (covered
          ? ` 📦 ${batch.covered_bundles.length} bundle listing(s) covered by their components — no separate count needed: ${covered}.`
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
    bEl.verifyCount.textContent = `${verifyEpcs.size} unique tags collected.`;
    setBatchResult(
      `Sweep #${cap.id} arrived from ${cap.device || "the C72"} ` +
        `(${cap.epc_count} tags) — checking the bin…`,
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

function openBatchView(stage) {
  bEl.start.hidden = true;
  bEl.active.hidden = false;
  document.querySelector(".binboard").hidden = true;
  bEl.binChip.textContent = isReceivingBatch()
    ? "📦 Receiving"
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
  setBatchResult("Batch left open — pick it up any time.", "ok");
});

bEl.abandon.addEventListener("click", async () => {
  if (!batch) return;
  const ties = batchItems.reduce((n, i) => n + (i.paired_count || 0), 0);
  const msg = ties
    ? `Abandon the batch for bin ${batch.bin_name}?\n\n${ties} tag(s) were ` +
      `paired in this batch — those ties will be REMOVED so the products ` +
      `aren't left tied to unverified labels. Counts stay in History.`
    : `Abandon the batch for bin ${batch.bin_name}? Collected counts are ` +
      `kept in History but the batch closes.`;
  if (!confirm(msg)) return;
  try {
    const res = await postJson(`/api/batches/${batch.id}/abandon`, {
      remove_ties: true,
    });
    if (res.ties_removed)
      setBatchResult(`Batch abandoned — ${res.ties_removed} tie(s) released.`, "ok");
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
              item.skip_reason ? " — " + escapeHtml(item.skip_reason) : ""
            } · no label, nothing counted</div>`
          : ""
      }
      ${
        item.tagged_before
          ? `<div class="bcell__meta bcell__done">✓ ${item.tagged_before} already tagged — no labels will print for those</div>`
          : ""
      }
      ${
        unitBreakdown(item)
          ? `<div class="bcell__meta bcell__cases" title="${escapeHtml(
              `${item.qty_scanned} loose box(es) plus ${item.case_count} sealed case(s) of ${item.case_units} — ${item.labels_total} label(s) in total`
            )}">${escapeHtml(unitBreakdown(item))} — ${item.labels_total} label(s)</div>`
          : ""
      }
      ${
        item.other_bins
          ? `<div class="bcell__meta bcell__split">${
              item.kind === "bundle"
                ? `Components on ${escapeHtml(item.other_bins)} — a bundle, not a box of its own`
                : `Also on ${escapeHtml(item.other_bins)} — ${
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
          `OK  — opened: counts ${c.units} units and prints ${c.units} labels.\n` +
          `Cancel — left sealed: counts ${c.units} units but prints ONE ` +
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
        `"${code}" isn't in the system — kept in the count as unresolved. ` +
          `Link it later at the Scan Station.`,
        "err"
      );
    } else if (data.serial_note) {
      setBatchResult(
        `⚠ ${data.serial_note} — ${itemDisplayName(item)}: ${item.qty_scanned} scanned.`,
        "err"
      );
    } else if (data.case) {
      // Say both numbers: a case makes units and labels diverge.
      setBatchResult(
        (data.case.scan_note ? `⚠ ${data.case.scan_note} — ` : "") +
          `${itemDisplayName(item)} — ${item.units_total} unit(s)` +
          (unitBreakdown(item) ? ` (${unitBreakdown(item)})` : "") +
          `, ${item.labels_total} label(s)` +
          (data.case_action === "sealed" ? " — box left sealed." : "."),
        data.case.scan_note ? "err" : "ok"
      );
    } else {
      setBatchResult(
        `${itemDisplayName(item)} — ${item.qty_scanned} scanned` +
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
    const boxes = batchItems.reduce((n, i) => n + i.qty_scanned, 0);
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
    bEl.items.append(li);
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

// Fully-paired products leave the list (Nick, 2026-08-31) - unless
// they're OVER-paired, which stays as a flag until dismissed.
function recvItemDone(item, dismissed) {
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
  const dismissed = recvOverDismissedSet();
  const done = items.filter((i) => recvItemDone(i, dismissed));
  const shown = items.filter((i) => !recvItemDone(i, dismissed));
  const printed = items.reduce((n, i) => n + (i.printed_count || 0), 0);
  const tagged = items.reduce((n, i) => n + (i.paired_count || 0), 0);
  const flagged = shown.filter((i) => recvProblemText(i)).length;
  summary.textContent = items.length
    ? `${items.length} product(s) · ${printed} label(s) printed · ` +
      `${tagged} tagged` +
      (flagged ? ` · ⚠ ${flagged} flagged` : "") +
      (done.length ? ` · ${done.length} fully tagged (hidden)` : "")
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
    shown
      .filter((i) => i.id !== focusedItem.id)
      .forEach((item) => list.append(recvCard(item)));
  } else {
    shown.forEach((item) => list.append(recvCard(item)));
  }
  syncRecvSweepPoll(
    !!focusedItem && !recvProblemText(focusedItem) && batch && !batch.completed_at
  );
  renderRecvUndo();
}

document
  .getElementById("recv-undo")
  .addEventListener("click", recvUndoLastPair);

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
        )}</div>
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
        ? "Bundle — made of separate products, so nothing here gets a tag"
        : `Multi-box product — ${n} boxes, one label each`
    }</span>
    <button class="reset" type="button" data-act="toggle">${
      bundle ? "No — it's one product in " + n + " boxes" : "No — it's a bundle"
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
            `labelled. Its component products are unaffected — they keep ` +
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
    setBatchResult("Nothing scanned yet — scan at least one known product.", "err");
    return;
  }
  showBatchStage("labels");
});

// --- Stage 2: check ---------------------------------------------------------
// Only items needing a human decision appear here (server decides why);
// everything else sails straight through to label queueing.
function labelItems() {
  return batchItems.filter((i) => i.resolved && i.qty_scanned > 0);
}

const FLAG_TEXT = {
  skipped: "skipped — couldn't be scanned, nothing counted",
  "tagged-not-detected":
    "tags on file for this shelf, but the sweep read none — find the " +
    "tagged box(es) before printing more",
  bundle: "a bundle — no box of its own to tag",
  "not-on-shelf":
    "Shopify expects this here, but none was scanned — it's in another " +
    "bin or the count is wrong",
  ambiguous: "barcode matches several listings",
  "count-mismatch": "count differs from Shopify",
  "unconfirmed-name": "serial name not confirmed",
  unresolved: "unknown barcode",
  "bad-chars":
    "the SKU or barcode has a broken special character — records can't " +
    "match until it's fixed",
  "tags-unheard":
    "the shelf sweep heard fewer tags than expected — tap to resolve " +
    "(scan one-by-one on the gun, or count by eye)",
  "tags-silent":
    "tags were expected on this shelf but the sweep heard NONE — find " +
    "the stickered boxes before printing more",
  "wrong-bin": "saved bin is a different shelf",
  "double-count":
    "boxes scanned AND marked already-tagged — if the stickered boxes " +
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
        <b>${escapeHtml(b.bin)}</b> — ${escapeHtml(b.skus.filter(Boolean).join(", "))}</span>
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
    `Side trip — tagging strays into ${batch.bin_name}` +
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
          ? ` — sweep heard ${sh.heard} of ${sh.on_file} on file, expected ${sh.expected}` +
            (sh.presumed_sold ? ` (${sh.presumed_sold} presumed sold)` : "") +
            (sh.over_heard
              ? ` · heard ${sh.over_heard} more tag(s) than boxes collected, check for a neighboring shelf or uncollected stock`
              : "")
          : "");
      li.querySelector(".bcell__info").append(flags);
    }
    li.style.cursor = "pointer";
    li.addEventListener("click", () => openBitem(entry));
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
  document.getElementById("bitem-meta").textContent =
    `SKU: ${showing.sku || "—"} · Barcode: ${showing.barcode || it.scanned_code || "—"}` +
    ` · Bin: ${showing.bin_location || "—"}`;
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
      (current ? " — currently selected" : "");
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
        "store (it shows as <b>?</b>) — records won't match until it's " +
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
  el.textContent = header;
  el.className =
    "label-preview__header " +
    (!asHeader || header.length <= 26
      ? "label-preview__header--lg"
      : header.length <= 56
        ? "label-preview__header--md"
        : "label-preview__header--sm");
  document.getElementById("bitem-prev-sku").textContent = asSku
    ? typed
    : it.sku || "";
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
        note = " (batch row catches up in a few seconds — hit ↻ if needed)";
    } catch (e) {
      note = ` (batch row refresh failed: ${e.message})`;
    }
    bitemEntry.flags = bitemEntry.flags.filter((f) => f !== "bad-chars");
    renderBitem();
    renderCheckList();
    msg.textContent =
      `SKU saved ✓ — now ${newSku}${note}.` +
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
    });
    // No re-lookup here: the OLD barcode is what this row scanned as, so
    // a live search by it would now miss. The row's display just follows.
    it.barcode = newBc;
    bitemEntry.flags = bitemEntry.flags.filter((f) => f !== "bad-chars");
    renderBitem();
    renderCheckList();
    msg.textContent =
      `Barcode saved ✓ — now ${newBc}.` +
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
      ? `Saved ✓ — labels print this as the ${
          bitemLabelMode === "both"
            ? "name and SKU"
            : bitemLabelMode === "sku"
              ? "SKU line"
              : "name"
        }.`
      : "Cleared ✓ — standard label.";
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
      "Ignored for this batch — it'll come up again next time.",
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
          `print for it — take them to bin ${it.bin_location}.`
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
        `Its component products are unaffected — they keep their own tags.`
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
    setBatchResult(`Re-checked ✓ — ${fixed} now resolved.`, "ok");
  } else if (fixed) {
    setBatchResult(
      `${fixed} now resolved ✓ — still unknown: ${stuck.join(", ")}.`,
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
      `Barcode updated in Shopify ✓ — now RE-SCAN those ` +
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
        `box(es)) from the list? Nothing permanent changes — scanning it ` +
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
    row.className = "linkbox__form";
    row.style.marginBottom = "6px";
    // The row it's currently sitting on starts with the full count; the
    // operator moves boxes off it.
    const startQty =
      c.shopify_variant_id === it.shopify_variant_id ? it.qty_scanned : 0;
    row.innerHTML = `
      <input type="number" class="linkbox__input bitem-split-qty" min="0"
             max="${it.qty_scanned}" value="${startQty}"
             data-variant="${escapeHtml(c.shopify_variant_id)}"
             style="max-width:70px" />
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
      : `${total} of ${it.qty_scanned} assigned — every box needs a home`;
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
    setBatchResult("Straight to pairing — no labels queued.", "ok");
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
        `They join the print queue with the rest — the other products in ` +
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
    msg.textContent = `${res.count} label(s) queued — collect them at the printer.`;
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
  const total = labelItems().reduce((n, i) => n + i.qty_scanned, 0);
  // Re-tagged bins often have NOTHING to print — every box already
  // wears a sticker. Ask instead of dead-ending on the server's 422;
  // verify still matters (the final sweep checks every tag).
  if (total === 0) {
    const tagged = batchItems.reduce((n, i) => n + (i.tagged_before || 0), 0);
    if (
      !confirm(
        `No untagged boxes were counted` +
          (tagged ? ` — all ${tagged} box(es) here already wear a tag` : "") +
          `, so there are no labels to queue and nothing to pair.\n\n` +
          `Sure there's nothing to print? OK skips straight ahead — ` +
          `run the verify sweep to finish the bin.`
      )
    )
      return;
    bEl.queue.disabled = true;
    try {
      await postJson(`/api/batches/${batch.id}/skip-print`, {});
      batch.status = "pairing";
      setBatchResult(
        "No labels — go to Verify and sweep the shelf to finish.",
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
    setBatchResult(`${data.count} label(s) queued.`, "ok");
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
    bEl.printAgent.textContent = agent.online
      ? "Printer agent: online ✓ (warehouse PC)" +
        (agent.realign_capable
          ? ""
          : " · running OLD code: the rip re-align fixes are inactive " +
            "until print_agent.py is updated and its task restarted")
      : "Printer agent: OFFLINE — is the warehouse PC on? Jobs stay queued.";
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
      (counts.error ? ` — ${counts.error} FAILED` : "") +
      (counts.pending + counts.printing
        ? ` — ${counts.pending + counts.printing} in the queue…`
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
      ? "⊘ RFID flag ON — remove"
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
  batchItems
    .filter((i) => i.resolved && i.qty_scanned > 0)
    .forEach((item) => {
      const li = itemCard(item, "pair");
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
      "Scan a product barcode from this batch first — then its tags.",
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
        ? `Saved, but ${code} doesn't look like a normal 24-char EPC — ` +
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

// Mirrors the print agent's layout rules: the top zone holds at most two
// lines (font steps down 28/20/16 with length) and ends where the SKU
// line starts; the SKU line is ONE line at font 30 — ZPL overprints
// rather than clipping, which is exactly the mess being fixed.
function labelFitIssues(top, sku) {
  const issues = [];
  if (top && top !== STORE_HEADER) {
    if (top.length > 76)
      issues.push("Top line: cut off after 76 characters.");
    const size = top.length <= 26 ? 28 : top.length <= 56 ? 20 : 16;
    const lines = Math.max(1, Math.ceil(zplTextDots(top, size) / LABEL_PW));
    if (lines > 2)
      issues.push(
        "Top line: needs more than the two lines available — the text " +
          "will overprint itself."
      );
    else if (lines === 2 && size === 28)
      issues.push(
        "Top line: wraps onto a second line that lands ON the SKU line."
      );
  }
  if (sku) {
    if (sku.length > 56)
      issues.push("SKU line: cut off after 56 characters.");
    else if (zplTextDots(sku, 30) > LABEL_PW)
      issues.push(
        "SKU line: too wide for its single line — the text will overlap " +
          "itself on the sticker."
      );
  }
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
  el.textContent = top;
  el.className =
    "label-preview__header " +
    (top === STORE_HEADER || top.length <= 26
      ? "label-preview__header--lg"
      : top.length <= 56
        ? "label-preview__header--md"
        : "label-preview__header--sm");
  document.getElementById("breprint-prev-sku").textContent = skuLine || "—";
  if (breprintItem) {
    document.getElementById("breprint-prev-bc").textContent =
      breprintItem.barcode || breprintItem.sku || "";
    document.getElementById("breprint-prev-bin").textContent =
      "BIN: " + (batch ? batch.bin_name : "—");
  }
  const warnEl = document.getElementById("breprint-fitwarn");
  const issues = labelFitIssues(top, skuLine);
  warnEl.hidden = !issues.length;
  warnEl.textContent = issues.length
    ? "⚠ " + issues.join("\n⚠ ") + "\nYou can still print — this is a warning, not a block."
    : "";
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
      `PEEL THOSE STICKERS OFF the boxes before printing — a leftover ` +
      `sticker answers sweeps alongside the new one. You'll be asked to ` +
      `confirm they're off.`
    : `The old printed labels become invalid — bin them so they never ` +
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
        `Labels still print and pairing still counts — but sweeps and ` +
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
        ? `⊘ ${itemDisplayName(item)} flagged — sweeps won't expect it to answer.`
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
        `labels stay valid — you just re-scan them onto their products. ` +
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
      `${res.removed} tie(s) released — pair the shelf again.`,
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
      await runVerifyCheck();
      setBatchResult(
        "Counts corrected ✓ — batch records only. If Shopify's on-hand " +
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
          `bin write — Shopify, the bin map and this product's tags all ` +
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
      await runVerifyCheck();
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
      await runVerifyCheck();
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
          `the retired list (kept forever — returns recoverable), ` +
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
      await runVerifyCheck();
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
          `\n\nEach writes separately — every product gets its own ` +
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
          ? ` · ${failed.length} FAILED — ${failed.join(" · ")}`
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
    });
    setBatchResult(res.message, "ok");
    await runVerifyCheck();
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
  if (
    !confirm(
      `Set Shopify ON-HAND for ${sku} DOWN to ${qty}?\n\n` +
        `Recorded sales account for the missing unit(s). This lowers ` +
        `the count, retires ${epcs.length} silent tag(s) as ` +
        `presumed-sold, and consumes the matching sales.\n\n` +
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
    });
    setBatchResult(res.message, "ok");
    await runVerifyCheck();
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
    bEl.verifyCount.textContent = `${verifyEpcs.size} unique tags collected.`;
    setBatchResult(
      `Pulled sweep #${cap.id} from ${cap.device || "the C72"} ` +
        `(${cap.epc_count} tags, ${fmtWhen(cap.created_at)}) — ` +
        `${verifyEpcs.size - before} new. Checking…`,
      "ok"
    );
    await runVerifyCheck();
  } catch (err) {
    setBatchResult(err.message, "err");
  }
});

async function runVerifyCheck() {
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
          expCell += ` <button class="reset onhand-lower onhand-fix--icon" type="button"
            data-sku="${escapeHtml(r.sku)}" data-qty="${found}"
            data-item="${r.item_id}"
            data-epcs="${escapeHtml(epcs.slice(0, Math.max(0, -diff)).join(","))}"
            title="Set product count to ${found} (recorded sales account for the ${-diff} missing; lowers Shopify on-hand, retires the silent tags presumed-sold, consumes the sales; one undo reverses all of it)">⇩</button>`;
        }
      }
      const flaggedRow = (red || yel || !paired) && !na;
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
                    ? `${r.paired_count} tag(s) paired vs ${r.qty_scanned} box(es) scanned — finish pairing at the gun, or fix the scan count below. `
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
      return `<tr${
        flaggedRow
          ? ` class="bvx-flag${yel && !red ? " bvx-flag--yel" : ""}" data-item="${r.item_id}" title="Click to review — what the sweep heard vs this batch's counts"`
          : ""
      }>
        <td>${productLink(r.product_title, r.shopify_product_id, r.sku)}${
          na
            ? ' <span class="noscan-chip" title="tag won\'t scan when on box — sweeps don\'t expect it to answer">⊘</span>'
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
                } — retire them (local records only, undoable from History)">Retire ${r.shelf.presumed_sold} presumed sold</button>`
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
                ? '<span class="bexp--warn" title="earlier tags silent — likely sold or moved before this batch">⚠ ▸</span>'
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
    ? `<p class="result result--err">⚠ This batch's own chain (printed → paired → heard) does NOT hold — ${mismatches.join(
        " · "
      )}. Check the ⚠ rows.</p>`
    : `<p class="result result--ok">✓ Every label printed here was paired, and every tag paired here answered the sweep.</p>`;
  const yellowNote = yellowCount
    ? `<p class="result result--warn-soft">⚠ ${yellowCount} product(s) have EARLIER tags that stayed silent — likely sold or moved before this batch. Yellow rows; the retire buttons clean their records.</p>`
    : "";
  // Expected silence is stated out loud, not hidden inside a green tick:
  // flagged products were paired but no sweep will ever hear them.
  const naSilent = rep.items.filter(
    (r) => r.rfid_incompatible && r.paired_count > 0 && r.detected === 0
  ).length;
  const naNote = naSilent
    ? `<p class="result">⊘ ${naSilent} product(s) flagged "won't RFID scan" answered nothing, as expected — their tags are paired and counted; the sweep can't hear them on the box.</p>`
    : "";
  // The already-tagged exception is said out loud too: 0 scanned and 0
  // paired on those rows is CORRECT, not a miss — the boxes arrived with
  // stickers from an earlier session and only need to answer the sweep.
  const tbRows = rep.items.filter((r) => (r.tagged_before || 0) > 0);
  const tbNote = tbRows.length
    ? `<p class="result">✓ ${tbRows.length} product(s) had boxes already RFID tagged before this batch (side trip or earlier session) — 0 scans and 0 pairs there is expected; their tags are counted in Detected instead.</p>`
    : "";
  // Unresolved codes are a heads-up, never a blocker: completing simply
  // drops them (same as removing them by hand) — no Review task is filed.
  const unresolvedNote = (rep.unresolved_codes || []).length
    ? `<p class="result result--warn-soft">⚠ ${rep.unresolved_codes.length} unresolved barcode(s) still in this batch (${rep.unresolved_codes
        .map(escapeHtml)
        .join(", ")}) — they never matched a product. Completing drops them; nothing goes to Review. Link them at the Scan Station first if they matter.</p>`
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
      ? `<div class="linkbox__actions" style="margin-top:8px">
           <button class="reset" id="bverify-fixall" type="button"
             title="Runs each row's Set-to button in turn — every product gets its own confirmation summary line, History entry and Undo">
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
            )}</span> — ${escapeHtml(t.message)}`
        )
        .join(" · ")}</p>`
    : "";

  bEl.verifyReport.innerHTML = `
    ${verdict}${yellowNote}${retiredNote}${naNote}${tbNote}${unresolvedNote}
    <div class="inventory__scroll"><table class="inventory__table">
      <thead><tr><th>Product</th><th>SKU</th><th class="num" title="Boxes physically collected this batch (new + already tagged)">Counted</th><th class="num" title="Shopify on-hand for this shelf; brackets show counted-vs-expected">Expected</th><th class="num">Detected</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>${fixAll}
    ${
      otherCount
        ? `<div class="linkbox__actions" style="margin-top:8px">
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
  return rep;
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
      <p class="result">Bin <b>${escapeHtml(rep.bin)}</b> checked against ${rep.swept} swept tag(s) — ${rep.count} product(s) on file there.</p>
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
      `⚠ ${unpaired.length} product(s) — ${missingBoxes} box(es) — ` +
      `have NOT been entered into inventory with RFID tags yet:\n\n` +
      `${names}${unpaired.length > 6 ? "\n…" : ""}\n\n` +
      `Are you sure you want to finish? The missing ones will be filed ` +
      `in Review as incomplete pairing.`;
  }
  // Closing a bin without ever sweeping it means the tags were never
  // checked against the shelf — worth one more question.
  if (!batch.verified_at) {
    msg =
      `This bin has never been verified — no RFID sweep has been checked ` +
      `against it.\n\n${msg}`;
  }
  if (!confirm(msg)) return;
  bEl.complete.disabled = true;
  try {
    const data = await postJson(`/api/batches/${batch.id}/complete`, {
      created_by: operatorEl.value || null,
      finalize: true,
    });
    const n = data.review_tasks.length;
    batch = null;
    batchItems = [];
    pairHistory = [];
    pairActiveItemId = null;
    stopBatchLive();
    enterBatchTab();
    setBatchResult(
      n
        ? `Batch done. ${n} item(s) sent to Review (count/pairing follow-ups).`
        : "Batch done — no follow-ups. Clean bin ✓",
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
  tr.innerHTML = `
        <td class="mono">#${j.id}</td>
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
        <td>${canCancel ? '<button class="recent__unassign" data-act="cancel">cancel</button>' : ""}${
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
      const reprintBtn = tr.querySelector('[data-act="reprint"]');
      if (reprintBtn)
        reprintBtn.addEventListener("click", async () => {
          if (!confirm(`Reprint one label for ${j.sku || j.product_title}? (New EPC — the damaged label's tag stays unassigned.)`)) return;
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

function queueStatusSummary(jobs) {
  const c = {};
  jobs.forEach((j) => (c[j.status] = (c[j.status] || 0) + 1));
  const parts = [];
  if (c.done) parts.push(`${c.done} printed`);
  if ((c.pending || 0) + (c.printing || 0))
    parts.push(`${(c.pending || 0) + (c.printing || 0)} queued`);
  if (c.error) parts.push(`${c.error} FAILED`);
  if ((c.voided || 0) + (c.canceled || 0))
    parts.push(`${(c.voided || 0) + (c.canceled || 0)} voided`);
  return parts.join(" · ");
}

function queueGroupRow(key, label, summary, level) {
  const open = queueOpen.has(key);
  const tr = document.createElement("tr");
  tr.className = "queue-group" + (level > 0 ? " queue-group--sub" : "");
  tr.innerHTML = `<td colspan="9">
    <span class="queue-group__arrow">${open ? "▾" : "▸"}</span>
    ${label}
    <span class="recent__meta"> — ${escapeHtml(summary)}</span></td>`;
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
      body.append(
        queueGroupRow(
          key,
          `${escapeHtml(j0.product_title || g.key)} <span class="mono recent__meta">${escapeHtml(j0.sku || "")}</span> × ${g.jobs.length} · <span class="mono">${queueIdRange(g.jobs)}</span>`,
          queueStatusSummary(g.jobs),
          0
        )
      );
      if (!queueOpen.has(key)) continue;
      if (g.sessions.length === 1) {
        // One print run — no sub level, straight to the labels.
        g.jobs.forEach((j) => body.append(queueJobRow(j, true)));
        continue;
      }
      for (const s of g.sessions) {
        const subKey = `${key}|${s.key}`;
        body.append(
          queueGroupRow(
            subKey,
            `<span class="mono">${queueIdRange(s.jobs)}</span> · ${s.jobs.length} label(s) <span class="recent__meta">${escapeHtml(fmtWhen(s.jobs[s.jobs.length - 1].created_at))}</span>`,
            queueStatusSummary(s.jobs),
            1
          )
        );
        if (queueOpen.has(subKey))
          s.jobs.forEach((j) => body.append(queueJobRow(j, true)));
      }
      continue;
    }
    const g = entry.batch;
    const recv = g.info.kind === "receiving";
    const key = `b${g.id}`;
    // Receiving batches carry their planner reference in created_by
    // ("TC-Planner · SO 123"); batch runs show their bin.
    const label = recv
      ? `📦 Receiving #${g.id}${
          g.info.created_by
            ? ` <span class="recent__meta">${escapeHtml(g.info.created_by)}</span>`
            : ""
        }`
      : `Batch #${g.id}${
          g.info.bin_name
            ? ` · bin <span class="mono">${escapeHtml(g.info.bin_name)}</span>`
            : ""
        }`;
    body.append(
      queueGroupRow(
        key,
        label + ` · <span class="mono">${queueIdRange(g.jobs)}</span>`,
        `${g.jobs.length} label(s): ${queueStatusSummary(g.jobs)}`,
        0
      )
    );
    if (!queueOpen.has(key)) continue;
    if (!recv) {
      // Batch-tagging runs expand to the flat rows, exactly as before.
      g.jobs.forEach((j) => body.append(queueJobRow(j, true)));
      continue;
    }
    // Receiving: one sub-group per product, then the individual labels.
    const bySku = new Map();
    for (const j of g.jobs) {
      const sk = (j.sku || j.product_title || "?").trim();
      if (!bySku.has(sk)) bySku.set(sk, []);
      bySku.get(sk).push(j);
    }
    for (const [sk, jobs] of bySku) {
      const subKey = `${key}|${sk}`;
      body.append(
        queueGroupRow(
          subKey,
          `${escapeHtml(jobs[0].product_title || sk)} <span class="mono recent__meta">${escapeHtml(sk)}</span> × ${jobs.length} · <span class="mono">${queueIdRange(jobs)}</span>`,
          queueStatusSummary(jobs),
          1
        )
      );
      if (queueOpen.has(subKey))
        jobs.forEach((j) => body.append(queueJobRow(j, true)));
    }
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
    // Wedged beats "online": the agent is fine but the PHYSICAL printer
    // stopped taking data - labels pile up in the Windows queue while
    // everything server-side reads done (Nick, 2026-09-01).
    if (agent.wedged) {
      const mins = Math.round((agent.win_oldest_seconds || 0) / 60);
      pill.textContent =
        `⚠ Printer WEDGED - ${agent.win_jobs} label(s) stuck in the ` +
        `Windows queue for ${mins}m. Power-cycle the printer, or ` +
        `Clear stuck jobs and reprint.`;
      pill.className = "pill pill--bad";
    } else {
      pill.textContent = agent.online
        ? "Printer agent: online ✓" +
          (agent.realign_capable
            ? agent.agent_version
              ? ` · v${agent.agent_version}`
              : ""
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

// === Review tab (WIP: task inbox) ==========================================
let reviewTasks = [];
let reviewFilter = "";
let reviewNotesOnly = false;
let reviewOpenIds = new Set();
// Tasks whose dismiss is waiting on the notes are-you-sure strip.
let dismissConfirmIds = new Set();

async function loadReview() {
  const list = document.getElementById("review-list");
  renderOrderSyncNote();
  try {
    const { tasks } = await apiJson("/api/review-tasks?status=open&limit=100");
    reviewTasks = tasks;
    // The type filter offers exactly the categories present right now.
    const sel = document.getElementById("review-filter");
    const cats = [...new Set(tasks.map((t) => t.category))].sort();
    if (reviewFilter && !cats.includes(reviewFilter)) reviewFilter = "";
    sel.innerHTML =
      '<option value="">All types</option>' +
      cats
        .map(
          (c) =>
            `<option value="${escapeHtml(c)}"${c === reviewFilter ? " selected" : ""}>${escapeHtml(evLabel(c))}</option>`
        )
        .join("");
    renderReview();
  } catch (err) {
    list.innerHTML =
      '<li class="recent__empty">Could not load review tasks.</li>';
  }
}

// One plain-language paragraph per task type: what the tag means and why
// products land under it — shown under the filter while it's active.
const REVIEW_NOTES = {
  "inventory-check":
    "The number of units counted on the shelf during batch tagging " +
    "didn't match Shopify's on-hand. Nothing was changed anywhere — " +
    "each of these is a recommendation to go count that product " +
    "properly (the bin audit's Set-to-N button is the sanctioned fix).",
  "pairing-incomplete":
    "Labels were printed for these products but not every label got " +
    "its RFID tag scanned in. An unpaired label is an orphan sticker — " +
    "pair it at the Scan Station or reprint before it ends up on a box.",
  "unresolved-barcode":
    "LEGACY entries — new batches no longer file these (2026-08-08): " +
    "unresolved codes now show as a heads-up at the verify step and are " +
    "simply dropped at completion. For these old ones: link the code to " +
    "its product at the Scan Station, or resolve/dismiss.",
  "could-not-scan":
    "Someone physically couldn't scan these during tagging (damaged " +
    "box, unreachable shelf, dead label). They were NOT counted — " +
    "each one still needs identifying and tagging by hand.",
  "bin-check":
    "These bins received stock (receiving) or were manually marked for " +
    "a check. Each one wants a quick RFID walk-scan of the shelf — the " +
    "run audit button opens the Audits tab with the bin loaded.",
  "bin-mismatch":
    "The RFID tags for these products were physically placed on a " +
    "different shelf than the bin Shopify has on file. These entries " +
    "are LIVE — they clear themselves when either side is fixed: write " +
    "the tags' shelf to Shopify (the boxes are where the tags say), or " +
    "move the boxes and update the tags. Nothing to dismiss.",
  "tag-onhand-mismatch":
    "System arithmetic, not a human count: the units this product's " +
    "tags stand for don't equal Shopify on-hand + boxes sold since the " +
    "last audit. Different from Inventory Check (someone counted a " +
    "shelf) — this one files AND clears itself as the daily order sync " +
    "re-checks. The fix is a bin audit: a sweep that hears the " +
    "remaining tags can mark the sold ones.",
  "duplicate-product":
    "Two tagged SKUs share the SAME saved barcode, or are the same SKU " +
    "written differently (exact evidence only — open-box products are " +
    "ignored; checked once per sync run, never per scan). Resolve to " +
    "MERGE the tags into one product — you pick the surviving SKU and " +
    "which name it keeps — which also files an inventory check for the " +
    "merged product. Dismiss if they really are two products; a " +
    "dismissed pair is never re-flagged.",
};

function renderReview() {
  const list = document.getElementById("review-list");
  const note = document.getElementById("review-filter-note");
  note.hidden = !(reviewFilter && REVIEW_NOTES[reviewFilter]);
  note.textContent = REVIEW_NOTES[reviewFilter] || "";
  const q = document
    .getElementById("review-search")
    .value.trim()
    .toLowerCase();
  // The with-notes filter only exists while some open task carries notes.
  const anyNotes = reviewTasks.some((t) => (t.notes || []).length);
  const notesBtn = document.getElementById("review-notesonly");
  notesBtn.hidden = !anyNotes;
  if (!anyNotes) reviewNotesOnly = false;
  notesBtn.textContent = reviewNotesOnly ? "📝 All tasks" : "📝 With notes";
  notesBtn.classList.toggle("chip-toggle--on", reviewNotesOnly);
  const tasks = reviewTasks.filter(
    (t) =>
      (!reviewFilter || t.category === reviewFilter) &&
      (!reviewNotesOnly || (t.notes || []).length) &&
      (!q ||
        (t.sku || "").toLowerCase().includes(q) ||
        (t.barcode || "").toLowerCase().includes(q) ||
        (t.product_title || "").toLowerCase().includes(q) ||
        (t.detail || "").toLowerCase().includes(q))
  );
  list.innerHTML = "";
  if (!tasks.length) {
    list.innerHTML = `<li class="recent__empty">${
      q
        ? "Nothing matches that search."
        : reviewFilter
          ? "No open tasks of that type."
          : "Inbox zero — nothing needs review."
    }</li>`;
    return;
  }
  tasks.forEach((t) => {
    const li = document.createElement("li");
    li.style.display = "block";
    const open = reviewOpenIds.has(t.id);
    // The boilerplate recommendation sentence lives behind the expansion,
    // on its own line — the collapsed row keeps just the facts.
    const rec = /Recommend[^.]*\.\s*$/.exec(t.detail || "");
    const short = rec ? t.detail.slice(0, rec.index).trim() : t.detail;
    // Bin checks carry their bin in the detail ("Bin K3-1: …") — that's
    // enough to jump straight into the Audits tab with the bin loaded.
    const checkBin =
      t.category === "bin-check" && /^Bin\s+(.+?):/.exec(t.detail || "");
    li.innerHTML =
      `<div class="rv-row">
        ${evChip(t.category)}
        <span class="recent__prod"><b>${escapeHtml(t.product_title || t.sku || "")}</b> ${escapeHtml(short)}</span>
        <span class="recent__meta recent__when" title="${escapeHtml(fmtWhen(t.created_at))}">${escapeHtml(fmtAgo(t.created_at))}</span>
        ${
          checkBin
            ? `<button class="rv-btn rv-btn--audit" data-act="audit" type="button"
                 title="Open the Audits tab with ${escapeHtml(checkBin[1])} loaded — runs right away if a fresh C72 sweep is waiting">run audit</button>`
            : ""
        }
        ${
          (t.notes || []).length
            ? `<span class="rv-noteflag" data-act="notes" title="${(t.notes || []).length} note(s) — click to read">📝 ${(t.notes || []).length}</span>`
            : ""
        }
        <button class="rv-btn rv-btn--resolve" data-act="resolve" type="button">resolve</button>
        <button class="rv-btn rv-btn--dismiss" data-act="dismiss" type="button">dismiss</button>
        <span class="auditrow__chev">${open ? "▾" : "▸"}</span>
      </div>` +
      (dismissConfirmIds.has(t.id)
        ? `<div class="rv-confirm">
             <span>This task has ${(t.notes || []).length} note(s) — dismiss anyway?</span>
             <button class="rv-btn rv-btn--dismiss" data-act="dismiss-yes" type="button">YES, DISMISS</button>
             <button class="rv-btn" data-act="dismiss-no" type="button">Cancel</button>
           </div>`
        : "") +
      (open
        ? `<div class="rv-detail">
            ${
              t.image_url
                ? `<img class="rv-img" src="${escapeHtml(t.image_url)}" alt="" />`
                : ""
            }
            <div>
              <div>${
                t.sku
                  ? `<b class="prodopen rv-prod" title="Open this product — label editor, RFID flag, full history">${escapeHtml(t.product_title || "")}</b>`
                  : `<b>${escapeHtml(t.product_title || "")}</b>`
              }${
                t.sku
                  ? ` <span class="mono recent__meta">· ${escapeHtml(t.sku)}</span>`
                  : ""
              }</div>
              <div class="recent__meta" style="margin-top:2px">${escapeHtml(short)}</div>
              ${
                rec
                  ? `<div class="recent__meta" style="margin-top:4px"><i>${escapeHtml(rec[0].trim())}</i></div>`
                  : ""
              }
              <div class="rv-timeline"><div class="rv-note__empty">Loading timeline…</div></div>
              <div class="rv-notes">
                <div class="rv-notes__title">Notes</div>
                ${
                  (t.notes || []).length
                    ? (t.notes || [])
                        .map(
                          (n) => `<div class="rv-note">
                            <div class="rv-note__meta">${escapeHtml(n.created_by || "?")} · ${escapeHtml(fmtAgo(n.created_at))}</div>
                            <div>${escapeHtml(n.note)}</div>
                          </div>`
                        )
                        .join("")
                    : `<div class="rv-note__empty">No notes yet.</div>`
                }
                <div class="rv-notes__add">
                  <input class="rv-notein" type="text" maxlength="500" placeholder="Add a note…" />
                  <button class="reset rv-notesave" type="button">Save note</button>
                </div>
              </div>
            </div>
          </div>`
        : "");
    li.querySelector(".rv-row").addEventListener("click", (ev) => {
      if (ev.target.closest("button")) return;
      if (reviewOpenIds.has(t.id)) reviewOpenIds.delete(t.id);
      else reviewOpenIds.add(t.id);
      renderReview();
    });
    // The product opens ONLY from the expanded detail — never from the
    // collapsed row, where the click belongs to expand/resolve/dismiss.
    const rvProd = li.querySelector(".rv-detail .rv-prod");
    if (rvProd)
      rvProd.addEventListener("click", (ev) => {
        ev.stopPropagation();
        openProductHistory(t.sku);
      });
    // Quick dismiss: instant when the task carries no notes; a note means
    // someone left context, so closing it takes a second, deliberate press.
    const quickDismiss = async () => {
      const operator = operatorEl.value;
      if (!operator) {
        alert("Pick who's scanning (top right) first.");
        return;
      }
      try {
        if (t.synthetic) {
          await postJson("/api/review/mismatch-dismissals", {
            sku: t.sku,
            tag_bin: t.tag_bin,
            shopify_bin: t.shopify_bin,
            dismissed_by: operator,
          });
        } else {
          await postJson(`/api/review-tasks/${t.id}/resolve`, {
            resolved_by: operator,
            dismissed: true,
          });
        }
        dismissConfirmIds.delete(t.id);
        reviewTasks = reviewTasks.filter((x) => x.id !== t.id);
        renderReview();
      } catch (err) {
        alert(err.message);
      }
    };
    const resolveBtn = li.querySelector('[data-act="resolve"]');
    if (resolveBtn)
      resolveBtn.addEventListener("click", () => openResolveWindow(t));
    const dismissBtn = li.querySelector('[data-act="dismiss"]');
    if (dismissBtn)
      dismissBtn.addEventListener("click", () => {
        if ((t.notes || []).length && !dismissConfirmIds.has(t.id)) {
          dismissConfirmIds.add(t.id);
          renderReview();
        } else {
          quickDismiss();
        }
      });
    const dyes = li.querySelector('[data-act="dismiss-yes"]');
    if (dyes) dyes.addEventListener("click", quickDismiss);
    const dno = li.querySelector('[data-act="dismiss-no"]');
    if (dno)
      dno.addEventListener("click", () => {
        dismissConfirmIds.delete(t.id);
        renderReview();
      });
    const noteFlag = li.querySelector('[data-act="notes"]');
    if (noteFlag)
      noteFlag.addEventListener("click", (ev) => {
        ev.stopPropagation();
        reviewOpenIds.add(t.id);
        renderReview();
      });
    const noteIn = li.querySelector(".rv-notein");
    const noteSave = li.querySelector(".rv-notesave");
    if (noteIn)
      noteIn.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") noteSave.click();
      });
    if (noteSave)
      noteSave.addEventListener("click", async () => {
        const input = li.querySelector(".rv-notein");
        const text = (input.value || "").trim();
        if (!text) return;
        const operator = operatorEl.value;
        if (!operator) {
          alert("Pick who's scanning (top right) first.");
          return;
        }
        noteSave.disabled = true;
        try {
          const saved = await postJson("/api/review-notes", {
            task_key: String(t.id),
            note: text,
            created_by: operator,
          });
          t.notes = [...(t.notes || []), saved];
          renderReview();
        } catch (err) {
          noteSave.disabled = false;
          alert(err.message);
        }
      });
    const auditBtn = li.querySelector('[data-act="audit"]');
    if (auditBtn)
      auditBtn.addEventListener("click", () => jumpToBinAudit(checkBin[1]));
    // (The old inline bin-write chip is gone — the resolve window offers
    // the same fix with full context.)
    const tlHost = li.querySelector(".rv-timeline");
    if (tlHost) loadReviewTimeline(t, tlHost);
    list.append(li);
  });
}

// === Review timelines =======================================================
// A scoped view of the product's history inside the expanded task — from
// the noticed discrepancy up to now, only the event types that matter for
// this category. It's a FILTER over the same events as the product's full
// History panel: resolving the task simply removes this view; the product
// history keeps everything.
const RV_TIMELINE_TYPES = {
  // Only events that SET or REPORT a bin — the mismatch timeline shows
  // bin records, never tag-by-tag noise (Nick's note, 2026-08-18).
  "bin-mismatch": new Set([
    "shopify-bin-read", "bin-updated", "tags-rebinned", "tag-assigned",
  ]),
  "inventory-check": new Set([
    "tag-assigned", "tag-unlinked", "on-hand-updated", "on-hand-undone",
    "on-hand-lowered", "on-hand-lower-undone",
    "batch-counted", "already-tagged-set", "receiving-completed",
    "inventory-check", "oneleft", "order-sold",
  ]),
};
// The sold-arithmetic category reads the same stock story (with the
// running tags column) — synthetic-style scoping comes from created_at.
RV_TIMELINE_TYPES["tag-onhand-mismatch"] = new Set([
  ...RV_TIMELINE_TYPES["inventory-check"],
  "tag-sold",
]);
const rvHistCache = {}; // sku -> {at, events}

async function rvProductEvents(sku) {
  const hit = rvHistCache[sku];
  if (hit && Date.now() - hit.at < 60000) return hit.events;
  const data = await apiJson(
    `/api/product-history?term=${encodeURIComponent(sku)}`
  );
  const events = (data.events || []).slice();
  // Oldest first — timelines read downward.
  events.sort((a, b) => String(a.at || "").localeCompare(String(b.at || "")));
  rvHistCache[sku] = { at: Date.now(), events };
  return events;
}

// Distill events into BIN RECORD rows for the mismatch timeline: which
// side claimed which bin, when, from where — [event] [when] [Bin X]
// [source]. Runs of tag assignments to the same bin collapse into one
// row carrying the tag count; nobody needs the per-tag stream here.
function rvBinRecordRows(events) {
  const rows = [];
  const afterArrow = (e) => {
    const m = /→\s*(.+)$/.exec(e.detail || "");
    return m ? m[1].trim() : null;
  };
  events.forEach((e) => {
    let bin = null;
    let src = null;
    let count = 0;
    if (e.type === "shopify-bin-read") {
      bin = (e.detail || "").split(" · ")[0].trim();
      src = "Shopify";
    } else if (e.type === "bin-updated") {
      bin = afterArrow(e);
      src = e.worker || "Shopify";
    } else if (e.type === "tags-rebinned") {
      bin = afterArrow(e);
      src = e.worker || "RFID system";
    } else if (e.type === "tag-assigned") {
      bin = (/(?:^|· )bin (.+)$/.exec(e.detail || "") || [])[1];
      src = e.worker || "C72";
      count = (e.epcs || [null]).length;
    } else {
      return;
    }
    if (!bin) return;
    const prev = rows[rows.length - 1];
    if (prev && prev.type === e.type && prev.bin === bin && prev.src === src) {
      prev.at = e.at;
      prev.count += count;
    } else {
      rows.push({ type: e.type, at: e.at, bin, src, count });
    }
  });
  return rows;
}

function rvTimelineRow(e, extra = "") {
  return (
    `<div class="rv-tl__row">
       ${evChip(e.type)}
       <span class="rv-tl__when" title="${escapeHtml(fmtWhen(e.at))}">${escapeHtml(fmtAgo(e.at))}</span>
       <span class="rv-tl__detail">${epcsDetailCell(e)}${
         e.worker ? ` · ${escapeHtml(e.worker)}` : ""
       }</span>${extra}
     </div>`
  );
}

async function loadReviewTimeline(t, host) {
  const cat = t.category;
  // Categories without a meaningful movement history: when it was filed,
  // during what, and by whom — straight from the task itself.
  if (!RV_TIMELINE_TYPES[cat] || !t.sku) {
    host.innerHTML =
      `<div class="rv-tl__row">
         ${evChip(cat)}
         <span class="rv-tl__when">${escapeHtml(fmtAgo(t.created_at))}</span>
         <span class="rv-tl__detail">filed${
           t.created_by ? ` by ${escapeHtml(t.created_by)}` : " by the system"
         }${t.batch_id ? ` · during batch #${t.batch_id}` : ""}</span>
       </div>`;
    return;
  }
  try {
    const all = await rvProductEvents(t.sku);
    const wanted = all.filter((e) => RV_TIMELINE_TYPES[cat].has(e.type));
    const since = t.synthetic ? null : t.created_at;
    let rows = wanted;
    let baseline = null;
    if (since) {
      rows = wanted.filter((e) => (e.at || "") >= since);
      // The last good state right before the discrepancy — the baseline.
      const before = wanted.filter((e) => (e.at || "") < since);
      baseline = before.length ? before[before.length - 1] : null;
    } else {
      // Live (synthetic) entries have no filed date: recent movement only.
      rows = wanted.slice(-12);
    }
    if (cat === "bin-mismatch") {
      const recs = rvBinRecordRows(since ? wanted : rows);
      host.innerHTML =
        `<div class="rv-tl__title">Bin record</div>` +
        (recs.length
          ? recs
              .map(
                (r) =>
                  `<div class="rv-tl__row">
                     ${evChip(r.type)}
                     <span class="rv-tl__when" title="${escapeHtml(fmtWhen(r.at))}">${escapeHtml(fmtAgo(r.at))}</span>
                     <span class="rv-tl__binv">Bin ${escapeHtml(r.bin)}</span>
                     <span class="rv-tl__src">${escapeHtml(r.src)}</span>
                     ${r.count > 1 ? `<span class="rv-tl__count">${r.count} tags</span>` : ""}
                   </div>`
              )
              .join("")
          : `<div class="rv-note__empty">No bin records on file.</div>`);
      return;
    }
    if (cat === "inventory-check" || cat === "tag-onhand-mismatch") {
      // Running tag count per event, derived backwards from the live
      // count so each row can say "tags: N".
      let tags = null;
      try {
        const tg = await apiJson(
          `/api/products/tags?sku=${encodeURIComponent(t.sku)}`
        );
        tags = (tg.tags || tg.assignments || []).length;
      } catch { /* count column just stays blank */ }
      if (tags != null) {
        const deltas = rows.map((e) =>
          e.type === "tag-assigned"
            ? (e.epcs || [null]).length
            : e.type === "tag-unlinked" || e.type === "tag-sold"
              ? -1
              : 0
        );
        let running = tags;
        const counts = new Array(rows.length);
        for (let i = rows.length - 1; i >= 0; i--) {
          counts[i] = running;
          running -= deltas[i];
        }
        host.innerHTML =
          `<div class="rv-tl__title">Timeline since the discrepancy</div>` +
          (baseline
            ? `<div class="rv-tl__baseline">baseline · ${rvTimelineRow(baseline)}</div>`
            : "") +
          (rows.length
            ? rows
                .map((e, i) =>
                  rvTimelineRow(
                    e,
                    `<span class="rv-tl__count">tags: ${counts[i]}</span>`
                  )
                )
                .join("")
            : `<div class="rv-note__empty">No stock-relevant events since this was filed.</div>`);
        return;
      }
    }
    host.innerHTML =
      `<div class="rv-tl__title">${
        since ? "Timeline since the discrepancy" : "Recent movement"
      }</div>` +
      (baseline
        ? `<div class="rv-tl__baseline">baseline · ${rvTimelineRow(baseline)}</div>`
        : "") +
      (rows.length
        ? rows.map((e) => rvTimelineRow(e)).join("")
        : `<div class="rv-note__empty">No movement recorded${
            since ? " since this was filed" : ""
          }.</div>`);
  } catch (err) {
    host.innerHTML = `<div class="rv-note__empty">Timeline unavailable.</div>`;
  }
}

// === Review resolve window ==================================================
// Resolve never closes a task blind: the window shows live context and
// the category's actual fixes; every write goes through the existing
// audited endpoints. Dismiss stays the quick action on the card.
function closeResolveWindow() {
  document.getElementById("resolve-overlay").hidden = true;
}
document.getElementById("resolve-overlay").addEventListener("click", (e) => {
  if (e.target === e.currentTarget) closeResolveWindow();
});

async function commitResolve(t, note) {
  const operator = operatorEl.value;
  if (!operator) {
    alert("Pick who's scanning (top right) first.");
    return;
  }
  try {
    await postJson(`/api/review-tasks/${t.id}/resolve`, {
      resolved_by: operator,
      dismissed: false,
      note: (note || "").slice(0, 255) || null,
    });
    reviewTasks = reviewTasks.filter((x) => x.id !== t.id);
    closeResolveWindow();
    renderReview();
  } catch (err) {
    alert(err.message);
  }
}

function openResolveWindow(t) {
  const overlay = document.getElementById("resolve-overlay");
  const body = document.getElementById("resolve-body");
  // The duplicate resolver freezes this SHARED modal's geometry with
  // inline min-height/width; the element is reused across opens, so
  // clear it here or every other category inherits the dupe window's
  // tall frame (Nick, 2026-08-19).
  const modalBox = body.closest(".phist-modal");
  if (modalBox) {
    modalBox.style.minHeight = "";
    modalBox.style.width = "";
  }
  const binFromDetail = (/^Bin\s+(.+?):/.exec(t.detail || "") || [])[1];
  const counts = /(\d+)\s+unit\(s\).*?on-hand is (\d+)/.exec(t.detail || "");
  const counted = counts ? Number(counts[1]) : null;
  const expectedThen = counts ? Number(counts[2]) : null;

  const notesHtml = (t.notes || []).length
    ? `<div class="rv-notes" style="margin:10px 0">
         <div class="rv-notes__title">Notes on this task</div>
         ${(t.notes || [])
           .map(
             (n) => `<div class="rv-note">
               <div class="rv-note__meta">${escapeHtml(n.created_by || "?")} · ${escapeHtml(fmtAgo(n.created_at))}</div>
               <div>${escapeHtml(n.note)}</div>
             </div>`
           )
           .join("")}
       </div>`
    : "";

  // Category-specific middles; footers differ for synthetic entries
  // (nothing stored to "mark resolved" — an action or a dismissal IS the
  // resolution).
  let middle = "";
  if (t.category === "inventory-check") {
    middle = `
      <div class="rvw-stats">
        <div class="rvw-stat"><div class="rvw-stat__l">Counted</div><div class="rvw-stat__n"><span id="rvw-counted">${counted ?? "?"}</span> <span id="rvw-delta" class="rvw-delta" hidden></span></div></div>
        <div class="rvw-stat"><div class="rvw-stat__l">Shopify then</div><div class="rvw-stat__n">${expectedThen ?? "?"}</div></div>
        <div class="rvw-stat rvw-stat--live"><div class="rvw-stat__l">Shopify NOW</div><div class="rvw-stat__n" id="rvw-live">…</div></div>
      </div>
      <div class="recent__meta" id="rvw-liveline" style="margin-bottom:8px">Checking the live count…</div>
      <div id="rvw-actions"></div>
      <button class="reset rvw-wide rvw-choice rvw-choice--amber" id="rvw-userfid" type="button">
        Shopify is wrong → use the RFID count${counted != null ? ` (${counted})` : ""}
        <span class="rvw-choice__sub">Writes the counted number to Shopify on-hand. Raises are the normal audited write; a lower must be fully covered by recorded sales. Undoable from History.</span>
      </button>
      ${binFromDetail ? `<button class="reset rvw-wide" id="rvw-audit" type="button">Jump to ${escapeHtml(binFromDetail)}'s bin audit</button>` : ""}
      <button class="reset rvw-wide rvw-recount" id="rvw-recount" type="button"
        title="You KNOW what's on the shelf: build the correction with - and +, then press the middle to apply. The RFID side updates and logs always; Shopify on-hand is only raised when its number differs (audited, undoable). Lowering Shopify stays a bin-audit job.">
        <span class="rvw-recount__pm" id="rvw-minus">−</span>
        <span class="rvw-recount__label" id="rvw-recount-label">Manually set the counted number</span>
        <span class="rvw-recount__pm" id="rvw-plus">+</span>
      </button>`;
  } else if (t.category === "labels-not-printed") {
    // The Update-stock safety net (Nick, 2026-08-26): stock reached
    // Shopify without labels. One click queues everything the linked
    // receiving batch is still owed - mechanically the planner's Print
    // labels pass - and the receiving list takes over from there.
    middle = `
      <button class="reset rvw-wide rvw-choice rvw-choice--amber" id="rvw-queuelabels" type="button">
        Queue the missing labels
        <span class="rvw-choice__sub">Prints one label per unlabelled box on the receiving batch, each with its home bin - identical to the planner's Print labels. No-bin products are held out and named.</span>
      </button>`;
  } else if (t.category === "tag-onhand-mismatch") {
    // The sold-out shortcut (Nick, 2026-08-26): when the LIVE on-hand
    // is 0, every remaining box has sold - the context loader offers
    // one click that retires all tags presumed-sold and resolves this.
    middle = `
      <div class="recent__meta" id="rvw-liveline" style="margin-bottom:8px">Checking the live numbers…</div>
      <div id="rvw-actions"></div>
      <button class="reset rvw-wide" id="rvw-station" type="button">Open at the Scan Station</button>`;
  } else if (t.category === "pairing-incomplete") {
    middle = `
      <div class="recent__meta" id="rvw-liveline" style="margin-bottom:8px">Checking the live pairing state…</div>
      <div id="rvw-actions"></div>
      <button class="reset rvw-wide" id="rvw-station" type="button">Open at the Scan Station (pair the stragglers)</button>`;
  } else if (t.category === "bin-check") {
    middle = `
      <div class="recent__meta" id="rvw-liveline" style="margin-bottom:8px"></div>
      ${binFromDetail ? `<button class="reset rvw-wide" id="rvw-audit" type="button">Run ${escapeHtml(binFromDetail)}'s bin audit</button>` : ""}`;
  } else if (t.category === "bin-mismatch") {
    middle = `
      <div class="rvw-bins">
        <div class="rvw-bins__cell"><div class="rvw-stat__l">Shopify says</div><div class="rvw-bins__b">${escapeHtml(t.shopify_bin || "?")}</div></div>
        <div class="rvw-bins__arrow">⇢</div>
        <div class="rvw-bins__cell"><div class="rvw-stat__l">Tags sit at</div><div class="rvw-bins__b">${escapeHtml(t.tag_bin || "?")}</div></div>
      </div>
      <button class="reset rvw-wide rvw-choice rvw-choice--amber" id="rvw-shopwrong" type="button">
        Shopify is wrong → write ${escapeHtml(t.tag_bin || "?")} to Shopify
        <span class="rvw-choice__sub">The audited bin update — Shopify, map and tags follow. Undoable.</span>
      </button>
      <button class="reset rvw-wide rvw-choice rvw-choice--blue" id="rvw-shopright" type="button">
        Shopify is right → boxes moved to ${escapeHtml(t.shopify_bin || "?")}
        <span class="rvw-choice__sub">Updates the tag records only — Shopify already says ${escapeHtml(t.shopify_bin || "?")}.</span>
      </button>`;
  } else if (t.category === "duplicate-product") {
    middle = `<div id="rvw-dupe" class="recent__meta">Loading both sides…</div>`;
  } else {
    // could-not-scan, legacy unresolved-barcode, anything else: the Scan
    // Station is where identifying/tagging/linking happens. Bundles get
    // their component list (or the one-time contents setup) injected
    // into the actions slot once the context answers.
    middle = `
      <div class="recent__meta" id="rvw-liveline" style="margin-bottom:8px"></div>
      <div id="rvw-actions"></div>
      <button class="reset rvw-wide" id="rvw-station" type="button">Open at the Scan Station</button>`;
  }

  body.innerHTML = `
    <div class="rvw-head">
      ${evChip(t.category)}
      <span class="rvw-head__title">${escapeHtml(t.product_title || t.sku || t.detail.slice(0, 40))}</span>
      <span class="rvw-close" id="rvw-close" title="Close">✕</span>
    </div>
    <div class="recent__meta" style="margin-bottom:10px">${
      t.sku ? `SKU ${escapeHtml(t.sku)} · ` : ""
    }${escapeHtml(t.synthetic ? "live entry — clears itself once the bins agree" : `filed ${fmtAgo(t.created_at)}${t.created_by ? ` by ${t.created_by}` : ""}`)}</div>
    ${
      // The duplicate resolver writes its own concise reason line — the
      // raw task detail would repeat the SKUs already on the cards.
      t.category === "duplicate-product"
        ? ""
        : `<div class="recent__meta" style="margin-bottom:10px">${escapeHtml(t.detail || "")}</div>`
    }
    ${notesHtml}
    ${middle}
    <input class="rv-notein rvw-resnote" id="rvw-note" type="text" maxlength="255" placeholder="Resolution note…" />
    <div class="rvw-foot">
      ${
        t.synthetic
          ? `<button class="rv-btn rv-btn--dismiss rvw-grow" id="rvw-dismiss" type="button" title="Suppressed for this exact disagreement — reappears only if either bin changes">Dismiss this mismatch</button>`
          : t.category === "duplicate-product"
            ? // Merge or split IS the resolution — the only other honest
              // exit is a dismissal (which this pair-flag never re-raises).
              `<button class="rv-btn rv-btn--dismiss rvw-grow" id="rvw-dupedismiss" type="button" title="They're fine as they are — closes the flag without changing anything, and this pair is never flagged again">Dismiss</button>`
            : `<button class="rv-btn rv-btn--resolve rvw-grow" id="rvw-resolve" type="button">Mark resolved</button>`
      }
      <button class="rv-btn" id="rvw-cancel" type="button">Cancel</button>
    </div>`;
  overlay.hidden = false;

  document.getElementById("rvw-close").addEventListener("click", closeResolveWindow);
  document.getElementById("rvw-cancel").addEventListener("click", closeResolveWindow);
  const noteVal = () => document.getElementById("rvw-note").value.trim();

  const resolveBtn = document.getElementById("rvw-resolve");
  if (resolveBtn)
    resolveBtn.addEventListener("click", () => {
      if (
        t.category === "inventory-check" &&
        !noteVal() &&
        !resolveBtn.dataset.armed
      ) {
        // "Recounted — still off" without a word of context helps nobody.
        resolveBtn.dataset.armed = "1";
        document.getElementById("rvw-note").focus();
        document.getElementById("rvw-note").placeholder =
          "What did the recount find? (required — press Mark resolved again)";
        return;
      }
      commitResolve(t, noteVal());
    });

  const auditBtn = document.getElementById("rvw-audit");
  if (auditBtn)
    auditBtn.addEventListener("click", () => {
      closeResolveWindow();
      jumpToBinAudit(binFromDetail);
    });

  // Manual recount (Nick, 2026-08-26): − / + build a correction shown
  // next to Counted as "(+1)" - yellow while it disagrees with Shopify,
  // green the moment counted+delta equals the live number. The middle
  // press applies it: the RFID side always (logged, source batch row
  // corrected), Shopify only when its number differs - raises ride the
  // audited on-hand endpoint, lowering stays a bin-audit job.
  const recountBtn = document.getElementById("rvw-recount");
  if (recountBtn) {
    let rvwDelta = 0;
    let rvwCounted = counted;
    const deltaChip = document.getElementById("rvw-delta");
    const readLive = () => {
      const txt = document.getElementById("rvw-live").textContent;
      return /^\d+$/.test(txt) ? Number(txt) : null;
    };
    const refreshRecount = () => {
      const label = document.getElementById("rvw-recount-label");
      if (rvwDelta === 0) {
        deltaChip.hidden = true;
        label.textContent = "Manually set the counted number";
        return;
      }
      const target = (rvwCounted ?? 0) + rvwDelta;
      deltaChip.hidden = false;
      deltaChip.textContent = `(${rvwDelta > 0 ? "+" : ""}${rvwDelta})`;
      const live = readLive();
      deltaChip.classList.toggle(
        "rvw-delta--green",
        live != null && target === live
      );
      label.textContent = `Set counted to ${target}`;
    };
    document.getElementById("rvw-minus").addEventListener("click", (ev) => {
      ev.stopPropagation();
      if ((rvwCounted ?? 0) + rvwDelta > 0) {
        rvwDelta--;
        refreshRecount();
      }
    });
    document.getElementById("rvw-plus").addEventListener("click", (ev) => {
      ev.stopPropagation();
      rvwDelta++;
      refreshRecount();
    });
    recountBtn.addEventListener("click", async () => {
      if (rvwDelta === 0) return;
      const operator = operatorEl.value;
      if (!operator) {
        alert("Pick who's scanning (top right) first.");
        return;
      }
      const target = (rvwCounted ?? 0) + rvwDelta;
      const live = readLive();
      const shopifyPart =
        live == null
          ? "Shopify's live number couldn't be read - it will NOT be touched."
          : live === target
            ? `Shopify already says ${live} - it will NOT be touched.`
            : target > live
              ? `Shopify says ${live} - on-hand will be RAISED to ${target} (confirmed, undoable in History).`
              : `Shopify says ${live}, which is higher - lowering runs through the bin audit path, so Shopify will NOT be touched here.`;
      if (
        !confirm(
          `Set the counted number to ${target} (was ${rvwCounted ?? "?"})?\n\n` +
            `The RFID side updates and logs always. ${shopifyPart}`
        )
      )
        return;
      recountBtn.disabled = true;
      try {
        const res = await postJson(`/api/review-tasks/${t.id}/recount`, {
          count: target,
          changed_by: operator,
        });
        let msg = res.message;
        if (live != null && target > live) {
          try {
            const r2 = await postJson("/api/onhand-updates", {
              sku: t.sku,
              new_qty: target,
              confirmed: true,
              changed_by: operator,
            });
            msg += ` ${r2.message || `On-hand raised to ${target}.`}`;
            document.getElementById("rvw-live").textContent = String(target);
          } catch (err2) {
            msg += ` Shopify write FAILED: ${err2.message}`;
          }
        }
        rvwCounted = target;
        rvwDelta = 0;
        if (res.task && res.task.detail) t.detail = res.task.detail;
        document.getElementById("rvw-counted").textContent = String(target);
        refreshRecount();
        document.getElementById("rvw-liveline").textContent = msg;
      } catch (err) {
        alert(err.message);
      } finally {
        recountBtn.disabled = false;
      }
    });
  }

  // Queue-labels: the Update-stock safety net's one-click resolution.
  const queueLabelsBtn = document.getElementById("rvw-queuelabels");
  if (queueLabelsBtn)
    queueLabelsBtn.addEventListener("click", async () => {
      const operator = operatorEl.value;
      if (!operator) {
        alert("Pick who's scanning (top right) first.");
        return;
      }
      if (
        !confirm(
          `Queue the missing labels for this receiving batch?\n\n` +
            `One label per unlabelled box, printed with its home bin - ` +
            `same as the planner's Print labels. The receiving list ` +
            `then runs the normal print and pair flow.`
        )
      )
        return;
      queueLabelsBtn.disabled = true;
      try {
        const res = await postJson(
          `/api/review-tasks/${t.id}/queue-labels`,
          { changed_by: operator }
        );
        alert(res.message);
        reviewTasks = reviewTasks.filter((x) => x.id !== t.id);
        closeResolveWindow();
        renderReview();
      } catch (err) {
        queueLabelsBtn.disabled = false;
        alert(err.message);
      }
    });

  // "Shopify is wrong → use the RFID count" (Nick, 2026-08-26): the
  // bin-mismatch-style one-click for count disagreements. Reads the
  // CURRENT counted number (a recount in this window counts) and the
  // live Shopify figure, then writes counted to on-hand: raises ride
  // the normal audited write; lowers ride the bin-audit lowering path,
  // whose guard refuses any drop recorded sales don't fully cover.
  const useRfid = document.getElementById("rvw-userfid");
  if (useRfid)
    useRfid.addEventListener("click", async () => {
      const operator = operatorEl.value;
      if (!operator) {
        alert("Pick who's scanning (top right) first.");
        return;
      }
      const countedNow = Number(
        document.getElementById("rvw-counted")?.textContent
      );
      if (!Number.isFinite(countedNow)) {
        alert("This task carries no counted number to write.");
        return;
      }
      const liveTxt = document.getElementById("rvw-live").textContent;
      const live = /^\d+$/.test(liveTxt) ? Number(liveTxt) : null;
      if (live == null) {
        alert("Shopify's live number couldn't be read - try again in a moment.");
        return;
      }
      if (live === countedNow) {
        await commitResolve(t, `Shopify already says ${live} - counts agree.`);
        return;
      }
      useRfid.disabled = true;
      try {
        if (countedNow > live) {
          if (
            !confirm(
              `Write on-hand ${live} → ${countedNow} to Shopify for ${t.sku}?\n\nConfirmed, logged, undoable from History.`
            )
          ) {
            useRfid.disabled = false;
            return;
          }
          const r = await postJson("/api/onhand-updates", {
            sku: t.sku,
            new_qty: countedNow,
            confirmed: true,
            changed_by: operator,
          });
          await commitResolve(t, r.message || `On-hand set to ${countedNow}.`);
        } else {
          if (!binFromDetail) {
            alert(
              "This task names no bin, and lowering a count runs through " +
                "the bin-audit path - run the bin audit instead."
            );
            useRfid.disabled = false;
            return;
          }
          // Two-phase like the other lowering flows: the unconfirmed
          // call answers with exactly what will happen (including the
          // sales-coverage arithmetic), and that text IS the prompt.
          let ask = null;
          try {
            await postJson("/api/onhand-updates/lower", {
              sku: t.sku,
              bin_name: binFromDetail,
              new_qty: countedNow,
              epcs: [],
              changed_by: operator,
            });
          } catch (err) {
            if (!/Confirm to proceed/.test(err.message)) throw err;
            ask = err.message;
          }
          if (ask && !confirm(ask)) {
            useRfid.disabled = false;
            return;
          }
          const r = await postJson("/api/onhand-updates/lower", {
            sku: t.sku,
            bin_name: binFromDetail,
            new_qty: countedNow,
            epcs: [],
            changed_by: operator,
            confirmed: true,
          });
          await commitResolve(
            t,
            r.message || `On-hand lowered to ${countedNow}.`
          );
        }
      } catch (err) {
        useRfid.disabled = false;
        alert(err.message);
      }
    });

  const stationBtn = document.getElementById("rvw-station");
  if (stationBtn)
    stationBtn.addEventListener("click", () => {
      closeResolveWindow();
      document.querySelector('.tabs__tab[data-tab="scan"]').click();
      const code = t.barcode || t.sku;
      if (code) {
        el.barcode.value = code;
        stationBarcodeScan(code);
      }
    });

  const dupeDismiss = document.getElementById("rvw-dupedismiss");
  if (dupeDismiss)
    dupeDismiss.addEventListener("click", async () => {
      const operator = operatorEl.value;
      if (!operator) {
        alert("Pick who's scanning (top right) first.");
        return;
      }
      dupeDismiss.disabled = true;
      try {
        await postJson(`/api/review-tasks/${t.id}/resolve`, {
          resolved_by: operator,
          dismissed: true,
          note: noteVal().slice(0, 255) || null,
        });
        reviewTasks = reviewTasks.filter((x) => x.id !== t.id);
        closeResolveWindow();
        renderReview();
      } catch (err) {
        dupeDismiss.disabled = false;
        alert(err.message);
      }
    });

  const dismissBtn2 = document.getElementById("rvw-dismiss");
  if (dismissBtn2)
    dismissBtn2.addEventListener("click", async () => {
      const operator = operatorEl.value;
      if (!operator) {
        alert("Pick who's scanning (top right) first.");
        return;
      }
      try {
        await postJson("/api/review/mismatch-dismissals", {
          sku: t.sku,
          tag_bin: t.tag_bin,
          shopify_bin: t.shopify_bin,
          dismissed_by: operator,
        });
        reviewTasks = reviewTasks.filter((x) => x.id !== t.id);
        closeResolveWindow();
        renderReview();
      } catch (err) {
        alert(err.message);
      }
    });

  const shopWrong = document.getElementById("rvw-shopwrong");
  if (shopWrong)
    shopWrong.addEventListener("click", async () => {
      const operator = operatorEl.value;
      if (!operator) {
        alert("Pick who's scanning (top right) first.");
        return;
      }
      if (
        !confirm(
          `Set the Shopify bin for ${t.sku} to ${t.tag_bin}?\n\n` +
            `Shopify currently says: ${t.shopify_bin}. This is the normal ` +
            `audited bin write — Shopify, the bin map and this product's ` +
            `tags all follow, with a History entry.`
        )
      )
        return;
      shopWrong.disabled = true;
      try {
        await postJson("/api/bin-updates", {
          target: t.sku,
          bin: t.tag_bin,
          changed_by: operator,
        });
        reviewTasks = reviewTasks.filter((x) => x.id !== t.id);
        closeResolveWindow();
        renderReview();
      } catch (err) {
        shopWrong.disabled = false;
        alert(err.message);
      }
    });

  const shopRight = document.getElementById("rvw-shopright");
  if (shopRight)
    shopRight.addEventListener("click", async () => {
      const operator = operatorEl.value;
      if (!operator) {
        alert("Pick who's scanning (top right) first.");
        return;
      }
      if (
        !confirm(
          `Move the TAG RECORDS for ${t.sku} to ${t.shopify_bin}?\n\n` +
            `For when the boxes physically moved (or are moving) to ` +
            `Shopify's shelf. Local only — nothing in Shopify changes.`
        )
      )
        return;
      shopRight.disabled = true;
      try {
        await postJson("/api/assignments/rebin", {
          sku: t.sku,
          bin: t.shopify_bin,
          changed_by: operator,
        });
        reviewTasks = reviewTasks.filter((x) => x.id !== t.id);
        closeResolveWindow();
        renderReview();
      } catch (err) {
        shopRight.disabled = false;
        alert(err.message);
      }
    });

  // Live context: quick answers that let stale tasks close in one click.
  if (!t.synthetic) loadResolveContext(t, counted);
}

async function loadResolveContext(t, counted) {
  const line = document.getElementById("rvw-liveline");
  try {
    const ctx = await apiJson(`/api/review-tasks/${t.id}/context`);
    if (t.category === "inventory-check") {
      const live = ctx.live_on_hand;
      document.getElementById("rvw-live").textContent = live ?? "—";
      const actions = document.getElementById("rvw-actions");
      if (live != null && counted != null && live === counted) {
        line.textContent =
          "Live on-hand now MATCHES the count — the world caught up.";
        actions.innerHTML = `<button class="reset rvw-wide rvw-ok" id="rvw-agree" type="button">Counts agree now — resolve</button>`;
        document.getElementById("rvw-agree").addEventListener("click", () =>
          commitResolve(t, `Live on-hand now matches the count (${counted}).`)
        );
      } else if (live != null && counted != null && counted > live) {
        // The write itself is the "Shopify is wrong" choice button.
        line.textContent = `Shelf count (${counted}) is HIGHER than live on-hand (${live}) — physical proof the boxes exist.`;
      } else if (live != null) {
        line.textContent = `Live on-hand is ${live} — above the count. "Use the RFID count" can lower it when recorded sales fully cover the drop; otherwise recount or resolve with a note.`;
      } else {
        line.textContent = "Live on-hand unavailable right now.";
      }
    } else if (t.category === "tag-onhand-mismatch") {
      const live = ctx.live_on_hand;
      const units = ctx.units_on_file;
      if (live == null) {
        line.textContent = "Live on-hand unavailable right now.";
      } else {
        line.textContent = `Shopify on-hand is now ${live} · RFID tags stand for ${units ?? "?"} unit(s).`;
        if (live === 0 && (units ?? 0) > 0) {
          const actions = document.getElementById("rvw-actions");
          actions.innerHTML = `
            <button class="reset rvw-wide rvw-choice rvw-choice--amber" id="rvw-allsold" type="button">
              Mark all ${ctx.tag_count} tag(s) presumed sold
              <span class="rvw-choice__sub">Shopify says 0 on hand - every remaining box has sold. Retires the tags (restorable from History) and resolves this task.</span>
            </button>`;
          document.getElementById("rvw-allsold").addEventListener("click", async () => {
            const operator = operatorEl.value;
            if (!operator) {
              alert("Pick who's scanning (top right) first.");
              return;
            }
            if (
              !confirm(
                `Mark all ${ctx.tag_count} tag(s) (${units} unit(s)) of ${t.sku} presumed sold?\n\n` +
                  `Shopify on-hand is 0, so no boxes are expected on the shelf. ` +
                  `The tags retire (each restorable from History) and this task resolves. ` +
                  `Shopify is not touched.`
              )
            )
              return;
            const allSold = document.getElementById("rvw-allsold");
            allSold.disabled = true;
            try {
              await postJson(`/api/review-tasks/${t.id}/retire-all-sold`, {
                changed_by: operator,
                confirmed: true,
              });
              reviewTasks = reviewTasks.filter((x) => x.id !== t.id);
              closeResolveWindow();
              renderReview();
            } catch (err) {
              allSold.disabled = false;
              alert(err.message);
            }
          });
        }
      }
    } else if (t.category === "pairing-incomplete") {
      if (ctx.paired_count != null && ctx.labels_total != null) {
        if (ctx.paired_count >= ctx.labels_total) {
          line.textContent = `Pairing has CAUGHT UP since (${ctx.paired_count} of ${ctx.labels_total}).`;
          document.getElementById("rvw-actions").innerHTML =
            `<button class="reset rvw-wide rvw-ok" id="rvw-caught" type="button">Pairing complete now — resolve</button>`;
          document.getElementById("rvw-caught").addEventListener("click", () =>
            commitResolve(t, `Pairing complete (${ctx.paired_count}/${ctx.labels_total}).`)
          );
        } else {
          line.textContent = `Still ${ctx.labels_total - ctx.paired_count} unpaired (${ctx.paired_count} of ${ctx.labels_total}).`;
        }
      } else {
        line.textContent = "";
      }
    } else if (t.category === "could-not-scan") {
      if (ctx.units_on_file != null)
        line.textContent = `The RFID system now holds ${ctx.units_on_file} unit(s) for this SKU${ctx.units_on_file > 0 ? " — if that covers this box, resolve below." : "."}`;
      if (ctx.kind === "bundle") renderBundleActions(t, ctx);
    } else if (t.category === "bin-check") {
      line.textContent = ctx.latest_sweep_at
        ? `Newest C72 sweep: ${fmtAgo(ctx.latest_sweep_at)} from ${ctx.latest_sweep_device || "the gun"}.`
        : "No C72 sweeps on file yet.";
    } else if (t.category === "duplicate-product") {
      renderDupeMerge(t, ctx);
    }
  } catch (err) {
    if (line) line.textContent = "";
  }
}

// The duplicate resolver (Nick's spec, 2026-08-19): a concise reason
// line up top ("Duplicate barcodes detected."), the two products as
// fixed preview panels, and the traits that still TELL THEM APART
// offered as labelled selector pairs — bundle-style broken-outline
// groups — pick one from each pair, then merge. Split swaps those
// selector pairs for SKU + Barcode inputs in the same slots. The two
// action buttons sit on the window's horizontal thirds in BOTH modes,
// and the window never moves or resizes once open.
function renderDupeMerge(t, ctx) {
  const host = document.getElementById("rvw-dupe");
  if (!host || !ctx.sides || ctx.sides.length !== 2) {
    if (host) host.textContent = "Couldn't load the two sides.";
    return;
  }
  const S = ctx.sides;
  const normId = (v) => String(v || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  // Why the pair was flagged decides which traits are offered: shared
  // barcode -> pick Name + SKU; shared SKU -> pick Name + Barcode;
  // both shared -> only the name still tells them apart.
  const sameBc = !!(
    S[0].barcode && S[1].barcode &&
    normId(S[0].barcode) === normId(S[1].barcode)
  );
  const sameSku = normId(S[0].sku) === normId(S[1].sku);
  // "Duplicate barcodes detected: DUMMY-000111" — name the value too
  // (both saved forms when they differ only in formatting).
  const joinVals = (a, b) =>
    String(a || "").toUpperCase() === String(b || "").toUpperCase()
      ? a
      : `${a} / ${b}`;
  const reason =
    sameBc && sameSku
      ? `Duplicate SKUs and barcodes detected: ${joinVals(S[0].sku, S[1].sku)} · barcode ${joinVals(S[0].barcode, S[1].barcode)}`
      : sameBc
        ? `Duplicate barcodes detected: ${joinVals(S[0].barcode, S[1].barcode)}`
        : `Duplicate SKUs detected: ${joinVals(S[0].sku, S[1].sku)}`;
  const identLabel = sameBc && sameSku ? null : sameBc ? "SKU" : "Barcode";
  const identValue = (s) =>
    identLabel === "SKU" ? s.sku : s.barcode || "(no barcode)";

  let mode = "merge";
  let nameSel = null; // {side, idx}
  let identSel = null; // 0 | 1 — the surviving identity's side
  if (identLabel && S[0].in_catalog !== S[1].in_catalog)
    identSel = S[0].in_catalog ? 0 : 1;
  const survivorSide = () =>
    identLabel ? identSel : nameSel ? nameSel.side : null;

  const card = (s, i) => `
      <div class="dupe2__card${
        mode === "merge" && survivorSide() === i ? " dupe2__card--sel" : ""
      }">
        ${
          s.image_url
            ? `<img class="dupe2__img" src="${escapeHtml(s.image_url)}" alt="">`
            : `<div class="dupe2__img dupe2__img--none">📦</div>`
        }
        <div class="dupe2__title">${escapeHtml(
          s.title || (s.titles && s.titles[0]) || s.sku
        )}</div>
        <div class="recent__meta mono">${
          identLabel
            ? `${identLabel} ${escapeHtml(identValue(s))}`
            : `SKU ${escapeHtml(s.sku)}`
        }</div>
        <div class="recent__meta">${s.units} tag unit(s)${
          s.bin ? ` · bin ${escapeHtml(s.bin)}` : ""
        }</div>
        <span class="dupe2__chip ${
          s.in_catalog ? "dupe2__chip--ok" : "dupe2__chip--warn"
        }">${s.in_catalog ? "in the live catalog ✓" : "not in the catalog"}</span>
      </div>`;

  const nameGroup = () => `
      <fieldset class="optgroup dupe2__group">
        <legend class="optgroup__legend">Product Name</legend>
        <div class="dupe2__pair">
          ${[0, 1]
            .map(
              (i) => `<div class="dupe2__cell">${(S[i].titles.length
                ? S[i].titles
                : ["(no recorded name)"]
              )
                .map(
                  (n, j) => `<button type="button" class="dupe2__name${
                    nameSel && nameSel.side === i && nameSel.idx === j
                      ? " dupe2__name--sel"
                      : ""
                  }" data-side="${i}" data-idx="${j}">${escapeHtml(n)}</button>`
                )
                .join("")}</div>`
            )
            .join("")}
        </div>
      </fieldset>`;
  const identGroup = () =>
    identLabel
      ? `<fieldset class="optgroup dupe2__group">
          <legend class="optgroup__legend">${identLabel}</legend>
          <div class="dupe2__pair">
            ${[0, 1]
              .map(
                (i) => `<div class="dupe2__cell">
                  <button type="button" class="dupe2__name dupe2__ident${
                    identSel === i ? " dupe2__name--sel" : ""
                  }" data-side="${i}">${escapeHtml(identValue(S[i]))}</button>
                </div>`
              )
              .join("")}
          </div>
        </fieldset>`
      : // Both traits shared: reserve the second group's space so the
        // split view (always two groups) doesn't grow the window.
        `<fieldset class="optgroup dupe2__group dupe2__group--ghost" aria-hidden="true">
          <legend class="optgroup__legend">&nbsp;</legend>
          <div class="dupe2__pair">
            <div class="dupe2__cell"><button type="button" class="dupe2__name" disabled>&nbsp;</button></div>
            <div class="dupe2__cell"><button type="button" class="dupe2__name" disabled>&nbsp;</button></div>
          </div>
        </fieldset>`;

  const skuInputs = () => `
      <fieldset class="optgroup dupe2__group">
        <legend class="optgroup__legend">SKU</legend>
        <div class="dupe2__pair">
          ${[0, 1]
            .map(
              (i) => `<div class="dupe2__cell dupe2__splitrow">
                <input class="linkbox__input dupe2__in dupe2__insku" data-side="${i}"
                       placeholder="Enter SKU" value="${escapeHtml(S[i].sku)}"
                       autocomplete="off" spellcheck="false" />
                <button type="button" class="reset dupe2__usesku" data-side="${i}"
                        title="Use this SKU as the barcode.">↴</button>
              </div>`
            )
            .join("")}
        </div>
      </fieldset>`;
  const bcInputs = () => `
      <fieldset class="optgroup dupe2__group">
        <legend class="optgroup__legend">Barcode</legend>
        <div class="dupe2__pair">
          ${[0, 1]
            .map(
              (i) => `<div class="dupe2__cell">
                <input class="linkbox__input dupe2__in dupe2__inbc" data-side="${i}"
                       placeholder="Enter Barcode" value="${escapeHtml(S[i].barcode || "")}"
                       autocomplete="off" spellcheck="false" />
              </div>`
            )
            .join("")}
        </div>
      </fieldset>`;

  const validate = () => {
    const hint = document.getElementById("dupe2-hint");
    if (mode === "merge") {
      const merge = document.getElementById("dupe2-merge");
      const ready = !!nameSel && (identLabel ? identSel != null : true);
      merge.disabled = !ready;
      const surv = survivorSide();
      hint.textContent = !ready
        ? identLabel
          ? `Pick the name and the ${identLabel.toLowerCase()} the merged product keeps.`
          : "Pick the name the merged product keeps."
        : `Merging ${S[1 - surv].sku} into ${S[surv].sku}, named "${
            S[nameSel.side].titles[nameSel.idx] || S[surv].sku
          }".`;
      return;
    }
    const inEl = (cls, i) => host.querySelector(`.${cls}[data-side="${i}"]`);
    const skuEls = [inEl("dupe2__insku", 0), inEl("dupe2__insku", 1)];
    const bcEls = [inEl("dupe2__inbc", 0), inEl("dupe2__inbc", 1)];
    const [sa, sb] = skuEls.map((e) => e.value.trim());
    const [ba, bb] = bcEls.map((e) => e.value.trim());
    // Which fields are in a live clash right now (an empty SKU counts —
    // it can't stand on its own).
    const clash = { sku: [!sa, !sb], bc: [false, false] };
    if (sa && sb && normId(sa) === normId(sb)) clash.sku = [true, true];
    if (ba && bb && normId(ba) === normId(bb)) clash.bc = [true, true];
    if (ba && sb && normId(ba) === normId(sb)) {
      clash.bc[0] = true;
      clash.sku[1] = true;
    }
    if (bb && sa && normId(bb) === normId(sa)) {
      clash.bc[1] = true;
      clash.sku[0] = true;
    }
    // The fields that CAUSED the flag show red while clashing and green
    // once fixed; innocent fields only ever go red (never green).
    const offending = { sku: sameSku, bc: sameBc };
    [["sku", skuEls], ["bc", bcEls]].forEach(([k, els]) =>
      els.forEach((e, i) => {
        e.classList.toggle("dupe2__in--bad", clash[k][i]);
        e.classList.toggle("dupe2__in--ok", offending[k] && !clash[k][i]);
      })
    );
    let problem = null;
    if (!sa || !sb) problem = "Both products need a SKU.";
    else if (normId(sa) === normId(sb)) problem = "The two SKUs are still the same.";
    else if (ba && bb && normId(ba) === normId(bb))
      problem = "The two barcodes are still the same.";
    else if (ba && normId(ba) === normId(sb))
      problem = `${sa}'s barcode equals the other SKU — they'd still collide.`;
    else if (bb && normId(bb) === normId(sa))
      problem = `${sb}'s barcode equals the other SKU — they'd still collide.`;
    document.getElementById("dupe2-splitgo").disabled = !!problem;
    document.getElementById("dupe2-hint").textContent = problem || "";
  };

  const draw = () => {
    host.innerHTML =
      `<p class="dupe2__reason">${escapeHtml(reason)}</p>` +
      `<div class="dupe2">${card(S[0], 0)}${card(S[1], 1)}</div>` +
      `<div class="dupe2__groups">${
        mode === "merge"
          ? nameGroup() + identGroup()
          : skuInputs() + bcInputs()
      }</div>` +
      `<div class="dupe2__actions">${
        mode === "merge"
          ? `<button class="reset rvw-ok dupe2__act" id="dupe2-merge" type="button" disabled>Merge products into one</button>
             <button class="reset dupe2__act" id="dupe2-split" type="button"
               title="They really are two products — give each its own SKU and barcode">Split products into two</button>`
          : `<button class="reset rvw-ok dupe2__act" id="dupe2-splitgo" type="button" disabled>Confirm split</button>
             <button class="reset dupe2__act" id="dupe2-splitback" type="button">Back</button>`
      }</div>` +
      `<p class="recent__meta" id="dupe2-hint"></p>`;

    host.querySelectorAll(".dupe2__name[data-idx]").forEach((n) =>
      n.addEventListener("click", () => {
        nameSel = { side: Number(n.dataset.side), idx: Number(n.dataset.idx) };
        draw();
      })
    );
    host.querySelectorAll(".dupe2__ident").forEach((n) =>
      n.addEventListener("click", () => {
        identSel = Number(n.dataset.side);
        draw();
      })
    );
    host.querySelectorAll(".dupe2__insku, .dupe2__inbc").forEach((inp) =>
      inp.addEventListener("input", validate)
    );
    const splitBtn = document.getElementById("dupe2-split");
    if (splitBtn)
      splitBtn.addEventListener("click", () => {
        mode = "split";
        draw();
      });
    const backBtn = document.getElementById("dupe2-splitback");
    if (backBtn)
      backBtn.addEventListener("click", () => {
        mode = "merge";
        draw();
      });
    host.querySelectorAll(".dupe2__usesku").forEach((b) =>
      b.addEventListener("click", () => {
        const i = b.dataset.side;
        host.querySelector(`.dupe2__inbc[data-side="${i}"]`).value =
          host.querySelector(`.dupe2__insku[data-side="${i}"]`).value.trim();
        validate();
      })
    );
    const mergeBtn = document.getElementById("dupe2-merge");
    if (mergeBtn)
      mergeBtn.addEventListener("click", async () => {
        const operator = operatorEl.value;
        if (!operator) {
          alert("Pick who's scanning (top right) first.");
          return;
        }
        const surv = survivorSide();
        const into = S[surv];
        const from = S[1 - surv];
        const title = S[nameSel.side].titles[nameSel.idx] || null;
        if (
          !confirm(
            `Merge ${from.sku} into ${into.sku}?\n\n${from.units} tag ` +
              `unit(s) move over, the merged product is named ` +
              `"${title || into.sku}", and an inventory check is filed. ` +
              `RFID records only — Shopify is not touched.`
          )
        )
          return;
        mergeBtn.disabled = true;
        try {
          const r = await postJson("/api/products/merge", {
            from_sku: from.sku,
            into_sku: into.sku,
            title,
            changed_by: operator,
          });
          reviewTasks = reviewTasks.filter((x) => x.id !== t.id);
          closeResolveWindow();
          renderReview();
          loadReview();
          alert(
            `Merged ✓ — ${r.moved_tags} tag(s) now under ${r.into_sku}. ` +
              `An inventory check was filed for it.`
          );
        } catch (err) {
          mergeBtn.disabled = false;
          alert(err.message);
        }
      });
    const splitGo = document.getElementById("dupe2-splitgo");
    if (splitGo)
      splitGo.addEventListener("click", async () => {
        const operator = operatorEl.value;
        if (!operator) {
          alert("Pick who's scanning (top right) first.");
          return;
        }
        const sideIn = (i) => ({
          sku: S[i].sku,
          new_sku: host
            .querySelector(`.dupe2__insku[data-side="${i}"]`)
            .value.trim(),
          new_barcode:
            host.querySelector(`.dupe2__inbc[data-side="${i}"]`).value.trim() ||
            null,
        });
        const sides = [sideIn(0), sideIn(1)];
        if (
          !confirm(
            `Split them into two distinct products?\n\n` +
              `${S[0].sku} → SKU ${sides[0].new_sku}` +
              `${sides[0].new_barcode ? `, barcode ${sides[0].new_barcode}` : ""}\n` +
              `${S[1].sku} → SKU ${sides[1].new_sku}` +
              `${sides[1].new_barcode ? `, barcode ${sides[1].new_barcode}` : ""}\n\n` +
              `Catalog products write to Shopify (History-logged); ` +
              `RFID-only records update locally. This pair is never ` +
              `flagged again.`
          )
        )
          return;
        splitGo.disabled = true;
        try {
          const r = await postJson("/api/products/split", {
            sides,
            changed_by: operator,
          });
          reviewTasks = reviewTasks.filter((x) => x.id !== t.id);
          closeResolveWindow();
          renderReview();
          loadReview();
          alert(
            `Split ✓ — ${r.sides.map((s) => s.sku).join(" and ")} are two ` +
              `distinct products now.`
          );
        } catch (err) {
          splitGo.disabled = false;
          alert(err.message);
        }
      });
    validate();
  };
  draw();
  // Freeze the window's geometry after the first draw: mode swaps and
  // hint changes must never move or flex it (Nick, 2026-08-19).
  // Synchronous, not rAF — getBoundingClientRect forces layout, and rAF
  // never fires in a hidden tab. Card images are fixed-size, so nothing
  // late-loading can change the height.
  const modal = host.closest(".phist-modal");
  if (modal && !modal.style.minHeight) {
    const r = modal.getBoundingClientRect();
    modal.style.minHeight = `${Math.ceil(r.height)}px`;
    modal.style.width = `${Math.ceil(r.width)}px`;
  }
}

// Bundles in the could-not-scan window: bundles are never tagged — their
// COMPONENTS are. With contents defined (once, reused forever) the window
// offers each component at the Scan Station; without, it offers the setup.
function renderBundleActions(t, ctx) {
  const slot = document.getElementById("rvw-actions");
  if (!slot) return;
  const contents = ctx.bundle_contents || [];
  if (contents.length) {
    slot.innerHTML =
      `<div class="recent__meta" style="margin-bottom:6px">This is a
       bundle — bundles aren't tagged, their components are. One bundle
       contains:</div>` +
      contents
        .map(
          (c, i) =>
            `<button class="reset rvw-wide" data-comp="${i}" type="button">Tag ${c.qty}× ${escapeHtml(c.component_sku)} at the Scan Station</button>`
        )
        .join("") +
      `<div class="recent__meta" style="margin:2px 0 8px"><a href="#" id="rvw-bundle-edit">Edit bundle contents…</a></div>`;
    slot.querySelectorAll("[data-comp]").forEach((btn) =>
      btn.addEventListener("click", () => {
        const c = contents[Number(btn.dataset.comp)];
        closeResolveWindow();
        document.querySelector('.tabs__tab[data-tab="scan"]').click();
        el.barcode.value = c.component_sku;
        stationBarcodeScan(c.component_sku);
      })
    );
    slot
      .querySelector("#rvw-bundle-edit")
      .addEventListener("click", (ev) => {
        ev.preventDefault();
        renderBundleSetup(t, slot, contents);
      });
  } else {
    renderBundleSetup(t, slot, []);
  }
}

function renderBundleSetup(t, slot, existing) {
  const prefill = existing
    .map((c) => `${c.component_sku} x ${c.qty}`)
    .join(", ");
  slot.innerHTML = `
    <div class="recent__meta" style="margin-bottom:6px">${
      existing.length
        ? "Edit what one bundle contains"
        : "This looks like a bundle. Define what ONE bundle contains — set once, used everywhere (batch collect stops counting it separately)."
    }</div>
    <div class="rv-notes__add" style="margin:0 0 8px">
      <input class="rv-notein" id="rvw-bundle-in" type="text"
             placeholder="e.g. W9184B x 10, 51701-1 x 3"
             value="${escapeHtml(prefill)}" />
      <button class="reset" id="rvw-bundle-save" type="button">Save contents</button>
    </div>
    <button class="reset rvw-wide" id="rvw-bundle-import" type="button"
            title="Reads the component list straight from Shopify (the Bundles app relationship) — no typing">⇣ Import contents from Shopify</button>`;
  document
    .getElementById("rvw-bundle-import")
    .addEventListener("click", async () => {
      const btn = document.getElementById("rvw-bundle-import");
      btn.disabled = true;
      btn.textContent = "Asking Shopify…";
      try {
        const r = await postJson("/api/bundle-contents/import", {
          sku: t.sku,
          updated_by: operatorEl.value || null,
        });
        renderBundleActions(t, { bundle_contents: r.contents });
      } catch (err) {
        btn.disabled = false;
        btn.textContent = "⇣ Import contents from Shopify";
        alert(err.message);
      }
    });
  document
    .getElementById("rvw-bundle-save")
    .addEventListener("click", async () => {
      const raw = document.getElementById("rvw-bundle-in").value.trim();
      const contents = [];
      for (const part of raw.split(",")) {
        if (!part.trim()) continue;
        const m = /^(.+?)\s*[x×]\s*(\d+)$/i.exec(part.trim());
        if (!m) {
          alert(
            `Couldn't read "${part.trim()}" — write each piece as ` +
              `SKU x QTY, separated by commas.`
          );
          return;
        }
        contents.push({ component_sku: m[1].trim(), qty: Number(m[2]) });
      }
      if (!contents.length) {
        alert("Nothing to save — write at least one SKU x QTY.");
        return;
      }
      try {
        const r = await postJson("/api/bundle-contents", {
          bundle_sku: t.sku,
          contents,
          updated_by: operatorEl.value || null,
        });
        renderBundleActions(t, { bundle_contents: r.contents });
      } catch (err) {
        alert(err.message);
      }
    });
}

document.getElementById("review-notesonly").addEventListener("click", () => {
  reviewNotesOnly = !reviewNotesOnly;
  renderReview();
});

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
  loadReview();
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
        "Order sync hasn't run yet — it runs daily at 8 AM, or press " +
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
        `fulfilled order(s) seen · ${last.recorded ?? 0} new sale(s) ` +
        `recorded · mismatch tasks +${last.tasks_opened ?? 0} / ` +
        `−${last.tasks_closed ?? 0}`
      : `⚠ Order sync failed ${fmtAgo(last.at)}: ${last.error || "unknown"}`;
    note.hidden = false;
  } catch {
    note.hidden = true;
  }
}
document.getElementById("review-filter").addEventListener("change", (e) => {
  reviewFilter = e.target.value;
  renderReview();
});

document.getElementById("review-search").addEventListener("input", renderReview);

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
      ? "— age unknown"
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

  const drift = auditData.bins.filter((b) => b.batch_done && b.score > 0);
  if (drift.length) {
    const worst = drift[0];
    audSetCard(
      "ahc-drift", String(drift.length),
      `worst: ${worst.bin} (score ${worst.score})`, "warn"
    );
  } else {
    audSetCard("ahc-drift", "0", "all tagged bins agree ✓", "ok");
  }

  const rows = auditData.bins.filter((b) => {
    if (!auditShowUntagged && !b.batch_done) return false;
    if (!q) return true;
    return (
      b.bin.toLowerCase().includes(q) ||
      b.products.some((p) => (p.sku || "").toLowerCase().includes(q))
    );
  });
  list.innerHTML = "";
  if (!rows.length) {
    list.innerHTML = `<li class="recent__empty">${
      q
        ? "No bins match that."
        : "No batch-tagged bins yet — complete a batch first."
    }</li>`;
    return;
  }
  rows.forEach((b) => {
    const li = document.createElement("li");
    li.style.display = "block";
    const clean = b.score === 0;
    const open = auditOpenBins.has(b.bin);
    li.innerHTML =
      `<div class="auditrow${clean ? " auditrow--clean" : ""}">
         <span class="inventory__bin">${escapeHtml(b.bin)}</span>
         <span class="auditrow__num ${clean ? "auditrow__num--ok" : ""}" title="sum of |Shopify − RFID| across this bin's products">${
           clean ? "✓" : b.score
         }</span>
         <span class="binlist__count" style="margin-left:auto">${b.product_count} product(s)${
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
        ? `<div class="inventory__scroll" style="margin:6px 0 10px 30px"><table class="inventory__table">
             <thead><tr><th>Product</th><th>SKU</th><th class="num">Shopify</th><th class="num">RFID</th><th class="num">Diff</th></tr></thead>
             <tbody>${b.products
               .map(
                 (p) => `<tr>
                   <td>${escapeHtml(p.product_title || "")}${
                     p.rfid_incompatible
                       ? ' <span class="noscan-chip" title="tag won\'t scan when on box">⊘</span>'
                       : ""
                   }</td>
                   <td class="mono"><span class="skulink" data-sku="${escapeHtml(p.sku || "")}">${escapeHtml(p.sku || "—")}</span></td>
                   <td class="num">${p.on_hand == null ? "—" : p.on_hand}${
                     p.sold_unretired
                       ? ` <span class="bexp--note" title="Boxes sold on fulfilled orders whose tag is still on file — they raise the expected tag count until an audit marks them sold">(+${p.sold_unretired} sold)</span>`
                       : ""
                   }</td>
                   <td class="num">${p.rfid_units}</td>
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
  });
}

// === Bin audit: newest C72 sweep vs any bin =================================
// The Check-step story without a batch: per product, what Shopify expects,
// what's tagged here, what the sweep actually heard — plus strays and
// unknown tags. The check is read-only; the panel's two WRITES are both
// operator-confirmed — "Set to N" (Shopify on-hand, increase-only, undoable
// from History) and "Record as batch tagged" (local batch record only).
let binAudit = null; // { rep, cap } — kept so toggles re-render for free
let binAuditShowUntagged = false;

// One-tap jump from a Review bin-check card: land on the Audits tab with
// the bin loaded. If the newest C72 sweep is fresh the operator has
// clearly just walked the shelf, so the audit runs itself; a stale sweep
// would only produce a scary everything-is-missing report, so instead the
// panel says what to go do.
async function jumpToBinAudit(bin) {
  document.querySelector('.tabs__tab[data-tab="audits"]').click();
  audShowPane("binaudit");
  const binEl = document.getElementById("binaudit-bin");
  const out = document.getElementById("binaudit-report");
  binEl.value = bin;
  binEl.scrollIntoView({ behavior: "smooth", block: "center" });
  try {
    const cap = await apiJson("/api/epc-captures/latest");
    const ageMs = Date.now() - tsDate(cap.created_at).getTime();
    if (Number.isFinite(ageMs) && ageMs <= 5 * 60000) {
      document.getElementById("binaudit-run").click();
      return;
    }
    out.innerHTML = `<p class="result">Walk-scan ${escapeHtml(bin)} with the C72
      (SWEEP → SEND), then hit RUN — the newest sweep on file is
      ${escapeHtml(fmtAgo(cap.created_at))}.</p>`;
  } catch (err) {
    out.innerHTML = `<p class="result">Walk-scan ${escapeHtml(bin)} with the C72
      (SWEEP → SEND), then hit RUN.</p>`;
  }
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
  out.innerHTML = `<p class="result">Checking…</p>`;
  try {
    const rep = await postJson(
      `/api/bins/${encodeURIComponent(bin)}/check`,
      { epcs: cap.epcs }
    );
    binAudit = { rep, cap };
    binAuditShowUntagged = false;
    renderBinAudit();
  } catch (err) {
    out.innerHTML = `<p class="result result--err">${escapeHtml(err.message)}</p>`;
  }
}

document.getElementById("binaudit-run").addEventListener("click", async () => {
  const out = document.getElementById("binaudit-report");
  out.innerHTML = `<p class="result">Pulling the latest sweep…</p>`;
  try {
    const cap = await apiJson("/api/epc-captures/latest");
    await runBinAudit(cap);
  } catch (err) {
    out.innerHTML = `<p class="result result--err">${escapeHtml(err.message)}</p>`;
  }
});

// --- Recent-sweep picker (Nick, 2026-09-01): a good sweep shouldn't be
// lost because a newer one landed - list the last few, tick one or
// several (combined into one union check).
document.getElementById("binaudit-pick").addEventListener("click", async () => {
  const box = document.getElementById("binaudit-sweeps");
  if (!box.hidden) {
    box.hidden = true;
    return;
  }
  box.innerHTML = `<p class="result">Loading recent sweeps…</p>`;
  box.hidden = false;
  try {
    const body = await apiJson("/api/epc-captures?limit=10");
    if (!body.captures.length) {
      box.innerHTML = `<p class="result">No sweeps received yet.</p>`;
      return;
    }
    box.innerHTML =
      body.captures
        .map(
          (c) => `<label class="binaudit-sweeprow">
            <input type="checkbox" value="${c.id}">
            #${c.id} · ${escapeHtml(c.device || "C72")} ·
            ${c.epc_count} tag(s) · ${escapeHtml(fmtWhen(c.created_at))}
          </label>`
        )
        .join("") +
      `<div class="linkbox__actions" style="margin-top:6px">
         <button class="reset" id="binaudit-runpicked" type="button">Check with ticked sweep(s)</button>
       </div>`;
  } catch (err) {
    box.innerHTML = `<p class="result result--err">${escapeHtml(err.message)}</p>`;
  }
});

document
  .getElementById("binaudit-sweeps")
  .addEventListener("click", async (e) => {
    if (!e.target.closest("#binaudit-runpicked")) return;
    const box = document.getElementById("binaudit-sweeps");
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
      for (const id of ids) {
        const cap = await apiJson(`/api/epc-captures/${id}`);
        (cap.epcs || []).forEach((x) => epcs.add(String(x).toUpperCase()));
        if (!newest || cap.id > newest.id) newest = cap;
      }
      box.hidden = true;
      await runBinAudit({
        id: ids.length === 1 ? String(ids[0]) : ids.join("+"),
        device: newest.device,
        created_at: newest.created_at,
        epc_count: epcs.size,
        epcs: [...epcs],
      });
    } catch (err) {
      out.innerHTML = `<p class="result result--err">${escapeHtml(err.message)}</p>`;
    }
  });

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

function renderBinAudit() {
  const out = document.getElementById("binaudit-report");
  if (!binAudit) return;
  const { rep, cap } = binAudit;

  const scored = rep.items
    // Nothing expected, nothing tagged, nothing heard: not part of this
    // bin's story at all.
    .filter((r) => (r.expected_qty || 0) > 0 || r.tags_here > 0 || r.detected > 0)
    .map((r) => {
      const flags = [];
      const silent = r.tags_here - r.detected;
      if (r.rfid_incompatible) {
        flags.push(["⊘ won't scan on box", "chip--na"]);
      } else if (silent > 0 && (r.sold_unretired || 0) > 0) {
        // Sales explain some or all of the silence: offer MARK SOLD for
        // the covered tags; anything beyond the sold count is still a
        // real silence (and the daily sync files the mismatch task).
        if (silent <= r.sold_unretired) {
          flags.push([
            `${silent} silent — ${r.sold_unretired} sold since last audit`,
            "chip--ok",
          ]);
        } else {
          flags.push([
            `${silent} silent vs ${r.sold_unretired} sold — count off`,
            "chip--warn",
          ]);
        }
      } else if (silent > 0) {
        flags.push([`${silent} tagged box(es) silent`, "chip--warn"]);
      }
      // Ghosts: presumed-sold (or replaced/dead) tags that ANSWERED -
      // the box never left. Treated as one more scan in the end; the
      // chip says why the numbers moved (Nick, 2026-09-01).
      if ((r.ghosts || []).length) {
        flags.push([
          `${r.ghosts.length} retired tag(s) answered - box still here`,
          "chip--warn",
        ]);
      }
      if (r.finds_open > 0) {
        flags.push([
          `${r.finds_open} tagless box(es) scanned - labels not printed`,
          "chip--warn",
        ]);
      }
      if (r.finds_printed > 0) {
        flags.push([
          `${r.finds_printed} label(s) printed, not yet paired`,
          "chip--warn",
        ]);
      }
      // Untagged: Shopify expects stock here but the RFID system holds
      // nothing for it. On a part-tagged shelf that's most of the list,
      // so it sits behind a toggle, below everything that IS tagged.
      const untagged = r.tags_here === 0 && r.detected === 0;
      return { r, flags, untagged };
    })
    .sort(
      (a, b) =>
        a.untagged - b.untagged ||
        b.flags.length - a.flags.length ||
        String(a.r.product_title).localeCompare(String(b.r.product_title))
    );
  const untaggedCount = scored.filter((s) => s.untagged).length;
  const shown = binAuditShowUntagged
    ? scored
    : scored.filter((s) => !s.untagged);

  const cells = shown
    .map(({ r, flags, untagged }) => {
      const tagsNote = (units, tags) =>
        units !== tags
          ? ` <span class="bexp--note">(${tags} tag${tags === 1 ? "" : "s"})</span>`
          : "";
      // Expected reads exactly like the batch-tagging verify table:
      // Shopify's number with the difference in brackets, and the
      // increase-only on-hand write offered when the shelf holds more
      // than Shopify knows about.
      let expCell = "—";
      if (r.expected_qty != null) {
        // Uncleared backorder debt RAISES what the shelf should hold:
        // those boxes arrived but Shopify's on-hand ran behind.
        const debt = r.backorder_debt || 0;
        const expTotal = r.expected_qty + debt;
        const diff = r.units_here - expTotal;
        expCell =
          `${expTotal}` +
          (debt
            ? ` <span class="bexp--note">(incl. ${debt} backorder)</span>`
            : "") +
          (diff
            ? ` <span class="bexp--off">(${diff > 0 ? "+" : "−"}${Math.abs(diff)})</span>`
            : "");
        if (diff > 0 && r.sku) {
          expCell += `<div><button class="reset binaudit-fix" type="button"
            data-sku="${escapeHtml(r.sku)}" data-qty="${r.units_here}"
            data-exp="${expTotal}"
            title="Write the tagged count to Shopify on-hand — confirmed, logged, undoable from History">Set to ${r.units_here}</button></div>`;
        }
      }
      return `<tr${untagged ? ' class="binaudit-untagged"' : ""}>
        <td>${
          r.image_url
            ? `<img class="bvx__img" style="width:40px;height:40px" src="${escapeHtml(r.image_url)}" alt="">`
            : ""
        }</td>
        <td>${
          r.sku
            ? `<span class="prodopen" data-sku="${escapeHtml(r.sku)}" title="Open this product — label editor, RFID flag, full history">${escapeHtml(r.product_title || "(unknown)")}</span>`
            : escapeHtml(r.product_title || "(unknown)")
        }${r.variant_title ? ` (${escapeHtml(r.variant_title)})` : ""}</td>
        <td class="mono"><span class="skulink" data-sku="${escapeHtml(r.sku || "")}" title="Open this product — label editor, RFID flag, full history">${escapeHtml(r.sku || "—")}</span></td>
        <td class="num">${expCell}</td>
        <td class="num">${r.units_here}${tagsNote(r.units_here, r.tags_here)}</td>
        <td class="num">${r.detected_units}${tagsNote(r.detected_units, r.detected)}</td>
        <td>${
          flags.length
            ? flags
                .map(
                  ([t, c]) =>
                    `<span class="binaudit-chip ${c}">${escapeHtml(t)}</span>`
                )
                .join(" ")
            : "✓"
        }${(() => {
          // Silence fully covered by fulfilled orders: one click retires
          // the shipped boxes' tags against the sold ledger.
          const silent = r.tags_here - r.detected;
          return silent > 0 &&
            (r.sold_unretired || 0) >= silent &&
            (r.silent_epcs || []).length &&
            r.sku
            ? `<div><button class="reset binaudit-marksold" type="button"
                 data-sku="${escapeHtml(r.sku)}"
                 data-epcs="${escapeHtml((r.silent_epcs || []).join(","))}"
                 title="These boxes shipped on fulfilled orders — remove their tag record(s) and retire the sale(s) in the ledger. History-logged; Shopify untouched.">MARK ${silent} SOLD</button></div>`
            : "";
        })()}</td>
      </tr>`;
    })
    .join("");

  const strays = rep.foreign
    .map(
      (f) =>
        `<li>${
          f.sku
            ? `<span class="prodopen" data-sku="${escapeHtml(f.sku)}">${escapeHtml(f.product_title || "?")}</span>`
            : escapeHtml(f.product_title || "?")
        } <span class="mono">${escapeHtml(f.sku || "")}</span>${
          f.bin_location ? " · recorded at " + escapeHtml(f.bin_location) : ""
        } <span class="mono">${escapeHtml(f.epc)}</span></li>`
    )
    .join("");
  const unknowns = rep.unknown_epcs
    .map((e) => `<li>Unknown tag <span class="mono">${escapeHtml(e)}</span></li>`)
    .join("");
  // Printed labels that answered but were never paired - the strongest
  // owed-pairing signal (a label applied and forgotten answers sweeps
  // as a productless tag).
  const owedLabels = (rep.printed_labels_heard || [])
    .map(
      (l) =>
        `<li>⚠ Printed label for ${
          l.sku
            ? `<span class="prodopen" data-sku="${escapeHtml(l.sku)}">${escapeHtml(l.product_title || l.sku)}</span>`
            : escapeHtml(l.product_title || "?")
        } answered but was never PAIRED - pair it, then sweep again.
        <span class="mono">${escapeHtml(l.epc)}</span> (job #${l.job_id})</li>`
    )
    .join("");
  const strayGhosts = (rep.stray_ghosts || [])
    .map(
      (g) =>
        `<li>Retired tag (${escapeHtml(g.kind)}) of ${
          g.sku
            ? `<span class="prodopen" data-sku="${escapeHtml(g.sku)}">${escapeHtml(g.product_title || g.sku)}</span>`
            : escapeHtml(g.product_title || "?")
        } answered here <span class="mono">${escapeHtml(g.epc)}</span></li>`
    )
    .join("");

  out.innerHTML = `
    <p class="result result--ok">Sweep #${cap.id} from ${escapeHtml(
      cap.device || "the C72"
    )} — ${cap.epc_count} tag(s), ${escapeHtml(fmtWhen(cap.created_at))} —
    checked against ${escapeHtml(rep.bin)}${
      rep.rack
        ? ` <b>(whole rack: ${(rep.bins_covered || [])
            .map((b) => escapeHtml(b.toUpperCase()))
            .join(", ")})</b>`
        : ""
    }.</p>
    ${
      rep.rack
        ? `<p class="result">${
            (rep.bins_batch_done || []).length ===
            (rep.bins_covered || []).length
              ? "✓ Every bin on this rack is recorded as batch tagged."
              : `Batch tagged so far: ${
                  (rep.bins_batch_done || [])
                    .map((b) => escapeHtml(b.toUpperCase()))
                    .join(", ") || "none"
                } - the rest of the rack doesn't count as tagged yet.`
          }</p>`
        : ""
    }
    ${
      rep.rack
        ? ""
        : rep.batch_done
        ? `<p class="result result--ok">✓ Already recorded as batch tagged —
           batch #${rep.batch_done_id}${
             rep.batch_done_at
               ? `, finished ${escapeHtml(fmtWhen(rep.batch_done_at))}`
               : ""
           }. Nothing to record here.${
             (rep.abandoned_batches || []).length
               ? ` (Bin also has ${
                   rep.abandoned_batches.length
                 } abandoned attempt(s): ${rep.abandoned_batches
                   .map((n) => "#" + n)
                   .join(", ")} — superseded by #${rep.batch_done_id}.)`
               : ""
           }</p>`
        : `<p class="result result--warn-soft">This bin has no completed batch —
           it doesn't count as tagged. If the shelf really is fully tagged (a
           batch abandoned after every tag was paired), you can record it:
           <button class="reset" id="binaudit-marktagged" type="button"
             title="Records the bin as batch tagged from the tags already on file — tags nothing, prints nothing, writes nothing to Shopify">Record ${escapeHtml(rep.bin)} as batch tagged…</button></p>`
    }
    <div class="inventory__scroll"><table class="inventory__table">
      <thead><tr><th></th><th>Product</th><th>SKU</th>
        <th class="num" title="Shopify on-hand for this bin; brackets show tagged-vs-expected">Expected</th>
        <th class="num" title="Units whose tag records say this bin">Tagged here</th>
        <th class="num" title="Units whose tags answered this sweep">Seen</th>
        <th></th></tr></thead>
      <tbody>${
        cells ||
        `<tr><td colspan="7">${
          untaggedCount
            ? "Nothing on this shelf is tagged yet."
            : `Nothing expected or tagged in ${escapeHtml(rep.bin)}.`
        }</td></tr>`
      }</tbody>
    </table></div>
    ${
      untaggedCount
        ? `<div class="linkbox__actions" style="margin-top:8px">
             <button class="reset" id="binaudit-toggle" type="button">${
               binAuditShowUntagged ? "Hide" : "Show"
             } ${untaggedCount} product(s) with no tags here</button>
           </div>`
        : ""
    }
    ${
      owedLabels
        ? `<div class="recent__head" style="margin-top:14px"><h2>Printed labels never paired (${rep.printed_labels_heard.length})</h2></div>
           <ul class="recent__list">${owedLabels}</ul>`
        : ""
    }
    ${
      strays || unknowns || strayGhosts
        ? `<div class="recent__head" style="margin-top:14px"><h2>Also heard on this shelf (${rep.foreign.length + rep.unknown_epcs.length + (rep.stray_ghosts || []).length})</h2></div>
           <ul class="recent__list">${strays}${strayGhosts}${unknowns}</ul>`
        : `<p class="result">No stray or unknown tags in the sweep.</p>`
    }`;
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
        });
        alert(res.message);
        document.getElementById("binaudit-run").click();
      } catch (err) {
        alert(err.message);
        fix.disabled = false;
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
          `${res.removed_tags} tag(s) marked sold — ` +
            `${res.retired_against_orders} unit(s) retired against orders.`
        );
        document.getElementById("binaudit-run").click();
      } catch (err) {
        alert(err.message);
        soldBtn.disabled = false;
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

async function loadAudits() {
  loadOneleft();
  loadAuditBins();
  loadAuditSessions();
  const list = document.getElementById("audit-list");
  try {
    const { tasks } = await apiJson("/api/review-tasks?status=open&limit=100");
    // Biggest single-product mismatch first — the size is parsed out of
    // the task's own wording ("N unit(s) counted but Shopify on-hand is
    // M"); anything unparsable sorts last rather than lying.
    const checks = tasks
      .filter((t) => t.category === "inventory-check")
      .map((t) => {
        const m = /(\d+)\s+unit\(s\).*?on-hand is (\d+)/.exec(t.detail || "");
        return { ...t, mismatch: m ? Math.abs(+m[1] - +m[2]) : 0 };
      })
      .sort(
        (a, b) =>
          b.mismatch - a.mismatch ||
          String(b.created_at || "").localeCompare(String(a.created_at || ""))
      );
    audSetCard(
      "ahc-more", String(checks.length),
      checks.length ? "from finished batches" : "nothing recommended ✓",
      checks.length ? "warn" : "ok"
    );
    list.innerHTML = checks.length
      ? ""
      : '<li class="recent__empty">No product checks recommended right now.</li>';
    checks.forEach((t) => {
      // [Bin chip] [Product name] ......... [mismatch chip] [N days ago]
      const binMatch = /^Bin\s+([^:]+):/.exec(t.detail || "");
      const li = document.createElement("li");
      li.innerHTML = `
        <span class="inventory__bin">${escapeHtml(binMatch ? binMatch[1] : "—")}</span>
        <span class="recent__prod" title="${escapeHtml(t.detail)}"><b>${escapeHtml(t.product_title || t.sku || "")}</b></span>
        <span class="audit-mm" title="${escapeHtml(t.detail)}">${t.mismatch || "?"}</span>
        <span class="recent__meta recent__when" title="${escapeHtml(fmtWhen(t.created_at))}">${escapeHtml(fmtAgo(t.created_at))}</span>`;
      list.append(li);
    });
  } catch (err) {
    list.innerHTML = '<li class="recent__empty">Could not load.</li>';
  }
  const sweeps = document.getElementById("sweep-list");
  try {
    const { captures } = await apiJson("/api/epc-captures?limit=10");
    sweeps.innerHTML = "";
    if (!captures.length) {
      audSetCard("ahc-sweep", "—", "no sweeps from the C72 yet", null);
      sweeps.innerHTML =
        '<li class="recent__empty">No sweeps from the C72 app yet.</li>';
      return;
    }
    const newest = captures[0];
    const ageMin = Math.max(
      0, Math.round((Date.now() - tsDate(newest.created_at).getTime()) / 60000)
    );
    audSetCard(
      "ahc-sweep",
      ageMin < 60 ? `${ageMin}m` : `${Math.round(ageMin / 60)}h`,
      `newest sweep: ${newest.epc_count} tags from ${newest.device || "C72"}`,
      ageMin < 5 ? "ok" : null
    );
    captures.forEach((c) => {
      const li = document.createElement("li");
      li.innerHTML = `
        ${evChip("sweep")}
        <span class="recent__prod"><b>#${c.id} · ${c.epc_count} tags</b> from ${escapeHtml(c.device || "C72")}${c.note ? " — " + escapeHtml(c.note) : ""}</span>
        <span class="recent__meta recent__when">${escapeHtml(fmtAgo(c.created_at))}</span>`;
      sweeps.append(li);
    });
  } catch (err) {
    sweeps.innerHTML = '<li class="recent__empty">Could not load sweeps.</li>';
  }
}

// === 1-left stock checks (Audits tab) =======================================
// The dashboard's verification queue joined against RFID evidence. The
// server does all the judging; this block only renders and relays clicks.
let olData = null;
let olAnsweredOnly = false;

const OL_VERDICTS = {
  confirmable: [
    "chip--ok",
    "RFID answers this",
    "A bin walk-scan or batch count since the check was raised covers the claimed stock — auto-clear will take it, or confirm it yourself",
  ],
  discrepancy: [
    "chip--bad",
    "Shopify 0, RFID sees stock",
    "Shopify now says none on hand but RFID evidence found stock after the check was raised — walk this one, something disagrees",
  ],
  "zero-claim": [
    "chip--warn",
    "now 0 — walk it",
    "Shopify has dropped to 0 since the check was raised; RFID can't prove an absence, so a human walk settles it",
  ],
  requeued: [
    "chip--warn",
    "re-queued — walk it",
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
    status.textContent = `The dashboard didn't answer: ${olData.error || "unknown error"}. Nothing is broken here — reload to retry.`;
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
        title="Open the confirm window — live stock breakdown + the actual shelf count. Undoable with re-queue.">Confirm ✓</button>` : ""}`;
    list.append(li);
  });
  if (total > rows.length) {
    const li = document.createElement("li");
    li.className = "recent__empty";
    li.textContent = `…and ${total - rows.length} more — narrow with the filter.`;
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
          : `auto-cleared (as ${r.employee || "?"}) — evidence ${r.evidence_units} vs claimed ${r.claimed == null ? "?" : r.claimed}`;
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
    openOlConfirm(confirmBtn.dataset.sku, confirmBtn.dataset.title || "");
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
// Committed, Available, On-hand) plus the ACTUAL shelf count. Equal
// count = plain confirm; higher = the audited increase-only on-hand
// write is offered first; lower = confirmed, and the discrepancy is
// filed for Review (nothing here writes stock DOWN).
let olcSku = null;
let olcOnHand = null;

async function openOlConfirm(sku, title) {
  olcSku = sku;
  olcOnHand = null;
  document.getElementById("olc-title").textContent =
    `Confirm stock check — ${sku}`;
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
      `Live stock unavailable right now (${err.message}) — you can still ` +
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
    // A higher count is physical proof: offer the audited increase-only
    // on-hand write BEFORE confirming the check.
    if (counted != null && olcOnHand != null && counted > olcOnHand) {
      if (
        confirm(
          `You counted ${counted} but Shopify on-hand is ${olcOnHand}.\n\n` +
            `Write on-hand ${olcOnHand} → ${counted} to Shopify? ` +
            `Confirmed, logged, undoable from History. (Cancel keeps ` +
            `Shopify as is — the check still confirms.)`
        )
      ) {
        await postJson("/api/onhand-updates", {
          sku: olcSku,
          new_qty: counted,
          confirmed: true,
          changed_by: operator,
        });
      }
    }
    await postJson("/api/oneleft/confirm", {
      sku: olcSku,
      worker: operator,
      counted,
    });
    if (counted != null && olcOnHand != null && counted < olcOnHand) {
      alert(
        `Check confirmed. You counted ${counted} vs on-hand ${olcOnHand} ` +
          `— nothing here writes stock DOWN, so the discrepancy was ` +
          `filed in Review.`
      );
    }
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
}

document.querySelectorAll("#tab-audits .audcard").forEach((card) => {
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
}

// === Audit sessions =========================================================
let audSessions = [];
let audSessShowDone = false;
let audSessOpenId = null;

async function loadAuditSessions() {
  const list = document.getElementById("audsess-list");
  try {
    const data = await apiJson(
      `/api/audit-sessions?status=${audSessShowDone ? "done" : "open"}`
    );
    audSessions = data.sessions;
  } catch (err) {
    list.innerHTML = `<li class="recent__empty">Could not load sessions: ${escapeHtml(err.message)}</li>`;
    return;
  }
  document.getElementById("audsess-meta").textContent = audSessShowDone
    ? `(${audSessions.length} finished)`
    : audSessions.length
      ? `(${audSessions.length} open)`
      : "";
  document.getElementById("audsess-toggle").textContent = audSessShowDone
    ? "Show open"
    : "Show finished";
  list.innerHTML = audSessions.length
    ? ""
    : `<li class="recent__empty">${audSessShowDone ? "No finished audits yet." : "No open audits — start one to bundle a rack walk or a 1-left blitz."}</li>`;
  audSessions.forEach((s) => {
    const pct = s.total ? Math.round((s.done / s.total) * 100) : 0;
    const li = document.createElement("li");
    li.className = "audsess";
    li.innerHTML = `
      <div class="audsess__row">
        <span class="audsess__name" data-sid="${s.id}">${escapeHtml(s.name)}</span>
        <span class="audsess__meta">${s.kind === "bins" ? `${s.total} bin(s)` : `${s.total} check(s)`} · started ${escapeHtml(fmtAgo(s.created_at))}${s.created_by ? " by " + escapeHtml(s.created_by) : ""}${s.status !== "open" ? " · " + s.status : ""}</span>
        <button class="binlist__go audsess-open" type="button" data-sid="${s.id}">${s.status === "open" ? "Resume" : "View"}</button>
      </div>
      <div class="audsess__bar"><div class="audsess__fill" style="width:${pct}%"></div></div>
      <div class="audsess__nums"><span>${s.done} of ${s.total} done</span><span>${pct}%</span></div>`;
    list.append(li);
  });
  // The open session detail refreshes from the same fetch.
  if (audSessOpenId !== null) {
    const open = audSessions.find((s) => s.id === audSessOpenId);
    if (open) renderAuditSessionDetail(open);
  }
}

function renderAuditSessionDetail(s) {
  audSessOpenId = s.id;
  const el = document.getElementById("audsess-detail");
  const pct = s.total ? Math.round((s.done / s.total) * 100) : 0;
  const openCount = s.total - s.done;
  const rows = s.items
    .map((i) => {
      const doneBtn = s.status === "open"
        ? `<button class="binlist__go audsess-done" type="button"
             data-item="${i.id}" data-done="${i.done ? 1 : 0}"
             title="${i.done ? "Un-tick this item" : "Mark this item accounted for"}">${i.done ? "✓ done" : "mark done"}</button>`
        : i.done ? `<span class="binlist__check">✓</span>` : "";
      const jump = s.kind === "bins"
        ? `<button class="binlist__go audsess-jump" type="button" data-bin="${escapeHtml(i.key)}"
             title="Open the bin audit with ${escapeHtml(i.key)} loaded">audit</button>`
        : "";
      const sub = [
        i.done && i.done_by ? `by ${i.done_by}` : "",
        i.done && i.done_at ? fmtAgo(i.done_at) : "",
        i.note || "",
      ].filter(Boolean).join(" · ");
      return `
        <li class="olrow${i.done ? " olrow--done" : ""}">
          <div class="olrow__main">
            <span class="binlist__name ${s.kind === "oneleft" ? "ol-sku" : ""}"
                  ${s.kind === "oneleft" ? `data-sku="${escapeHtml(i.key)}"` : ""}>${escapeHtml(i.key)}</span>
            <span class="olrow__title">${escapeHtml(i.label || "")}</span>
            ${sub ? `<div class="olrow__sub">${escapeHtml(sub)}</div>` : ""}
          </div>
          ${jump}${doneBtn}
        </li>`;
    })
    .join("");
  el.innerHTML = `
    <div class="recent__head">
      <h2>${escapeHtml(s.name)} <span class="recent__note">${s.kind === "bins" ? "bin walk" : "1-left checks"} · ${s.status}</span></h2>
    </div>
    <div class="audsess__bar" style="max-width:420px"><div class="audsess__fill" style="width:${pct}%"></div></div>
    <div class="audsess__nums" style="max-width:420px"><span>${s.done} of ${s.total} done</span><span>${pct}%</span></div>
    ${s.kind === "oneleft" ? `<p class="linkbox__text" style="max-width:70ch">Items tick themselves when their 1-left check clears (auto or manual confirm); anything left needs a walk.</p>` : `<p class="linkbox__text" style="max-width:70ch">Sweep each bin on the C72, check it with the bin audit, then mark it done here.</p>`}
    ${s.status === "open" ? `
      <div class="linkbox__actions" style="margin:8px 0 12px">
        <button class="reset" id="audsess-finish" type="button">Finish audit</button>
        <button class="reset" id="audsess-abandon" type="button">Abandon</button>
      </div>` : ""}
    <ul class="recent__list binlist" style="max-height:480px">${rows}</ul>`;

  const finish = document.getElementById("audsess-finish");
  if (finish)
    finish.addEventListener("click", async () => {
      if (
        openCount > 0 &&
        !window.confirm(
          `${openCount} item(s) are still open — finish anyway?`
        )
      )
        return;
      try {
        await postJson(`/api/audit-sessions/${s.id}/finish`, {
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
        await postJson(`/api/audit-sessions/${s.id}/abandon`, {
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

document.getElementById("audsess-list").addEventListener("click", (e) => {
  const open = e.target.closest(".audsess-open, .audsess__name");
  if (!open) return;
  const s = audSessions.find((x) => x.id === Number(open.dataset.sid));
  if (!s) return;
  renderAuditSessionDetail(s);
  audShowPane("session");
});

document.getElementById("audsess-detail").addEventListener("click", async (e) => {
  const sku = e.target.closest(".ol-sku");
  if (sku) {
    openProductHistory(sku.dataset.sku);
    return;
  }
  const jump = e.target.closest(".audsess-jump");
  if (jump) {
    jumpToBinAudit(jump.dataset.bin);
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
  loadAuditSessions();
});

const audsessKindEl = document.getElementById("audsess-kind");
const audsessScopeEl = document.getElementById("audsess-scope");
audsessKindEl.addEventListener("change", () => {
  audsessScopeEl.placeholder = audsessKindEl.value === "bins"
    ? "Bins or rack prefix, e.g. I1"
    : "Vendor (blank = the whole queue)";
});
document.getElementById("audsess-newbtn").addEventListener("click", () => {
  const form = document.getElementById("audsess-new");
  form.hidden = !form.hidden;
  if (!form.hidden) document.getElementById("audsess-name").focus();
});
document.getElementById("audsess-cancel").addEventListener("click", () => {
  document.getElementById("audsess-new").hidden = true;
});
document.getElementById("audsess-create").addEventListener("click", async (ev) => {
  const name = document.getElementById("audsess-name").value.trim();
  if (!name) {
    alert("Give the audit a name first.");
    return;
  }
  const kind = audsessKindEl.value;
  const scope = audsessScopeEl.value.trim();
  const payload = { name, kind, worker: operatorEl.value || null };
  if (kind === "bins") {
    // Tokens with a dash are bins ("I1-3"); a bare token is a rack prefix.
    const tokens = scope.split(",").map((t) => t.trim()).filter(Boolean);
    payload.bins = tokens.filter((t) => t.includes("-"));
    const rack = tokens.find((t) => !t.includes("-"));
    if (rack) payload.rack = rack;
    if (!payload.bins.length && !payload.rack) {
      alert("Name at least one bin, or a rack prefix like I1.");
      return;
    }
  } else if (scope) {
    payload.vendor = scope;
  }
  ev.currentTarget.disabled = true;
  try {
    const s = await postJson("/api/audit-sessions", payload);
    document.getElementById("audsess-new").hidden = true;
    document.getElementById("audsess-name").value = "";
    audsessScopeEl.value = "";
    audSessShowDone = false;
    await loadAuditSessions();
    renderAuditSessionDetail(s);
    audShowPane("session");
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
      <td class="recent__meta" style="white-space:nowrap">${escapeHtml(fmtWhen(e.at))}</td>
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
        ? "Serialized product — the top line is its item name, printed " +
          "on every label including Scan Station auto-prints. The SKU " +
          "line stays standard:"
        : "Edit the two label lines — saved store-wide, every future " +
          "print uses them. ✕ resets a line to its default:";
      updateLabelPreview();
    }
    renderNoScan(!!data.rfid_incompatible);
    renderNonTaggable(!!data.non_taggable);
    renderVendorRow();
    renderBundleRow();
    renderLocateRow();
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
        ? `Dropped from the RFID system${who}${when} — it isn't seeded into ` +
          `new batches and never gets a label.`
        : pk.kind === "bundle"
          ? `Marked as a bundle${who}${when} — no labels print for it; its ` +
            `component products carry the tags.`
          : `Marked as a multi-box product${who}${when} — one label per box.`;
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
    document.getElementById("phist-meta").textContent =
      `SKU: ${data.sku || "—"} · Barcode: ${data.barcode || "—"}` +
      (p ? ` · Bin: ${p.bin_location || "—"}` : "") +
      ` · ${data.tag_count} tag(s) on file` +
      (data.on_hand != null ? ` · on-hand ${data.on_hand}` : "");
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
        <td class="recent__meta" style="white-space:nowrap">${escapeHtml(fmtWhen(e.at))}</td>
        <td>${evChip(e.type)}</td>
        <td>${escapeHtml(e.worker || "—")}</td>
        <td class="recent__meta">${exp}${escapeHtml(detailText)}</td>
        <td>${
          e.shopify
            ? '<span class="chip-status chip-status--done" title="This event wrote to (or read from) the live Shopify store">Shopify ✓</span>'
            : '<span class="chip-status chip-status--pending" title="This event only touched the RFID system\'s own records — nothing in Shopify changed">RFID only</span>'
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
  el.textContent = top;
  el.className =
    "label-preview__header " +
    (top === STORE_HEADER || top.length <= 26
      ? "label-preview__header--lg"
      : top.length <= 56
        ? "label-preview__header--md"
        : "label-preview__header--sm");
  document.getElementById("phist-prev-sku").textContent = skuLine || "—";
  document.getElementById("phist-prev-bc").textContent =
    p.barcode || p.sku || phistData.barcode || "";
  document.getElementById("phist-prev-bin").textContent =
    "BIN: " + (p.bin_location || "—");
  const issues = labelFitIssues(top, skuLine);
  const warn = document.getElementById("phist-fitwarn");
  warn.hidden = !issues.length;
  warn.textContent = issues.length
    ? "⚠ " + issues.join("\n⚠ ") +
      "\nYou can still print — this is a warning, not a block."
    : "";
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
  document.getElementById("phist-flags").hidden =
    document.getElementById("phist-norfid").hidden &&
    document.getElementById("phist-notag").hidden;
  renderFlagChips();
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
        "contains and batch collect stops counting it separately — the " +
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
        `Write each piece as SKU x QTY, separated by commas — e.g.\n` +
        `W9184B x 10   or   51701-1 x 3, 51701-2 x 1\n\n` +
        `(Leave empty and press OK to clear — the bundle becomes ` +
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
          `Couldn't read "${part.trim()}" — write each piece as SKU x QTY.`
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
        ? "Flagged ⊘ — logged; sweeps stop expecting this product to answer."
        : "Flag removed ✓ — logged; sweeps expect it again.";
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
  document.getElementById("phist-flags").hidden =
    document.getElementById("phist-norfid").hidden &&
    document.getElementById("phist-notag").hidden;
  renderFlagChips();
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
    ? `🏷 Vendor: ${phistData.vendor} — change…`
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
      msg.textContent = want
        ? "Marked non-taggable 🚫 - logged; batches and audits skip it now."
        : "Back in the RFID system ✓ - logged; batches and audits count it again.";
    } catch (err) {
      msg.textContent = err.message;
    }
  });

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
        ` — pick it on the gun's LOCATE tab to hunt its ` +
        `${mine.tag_count} tag(s). Click to take it off the list.`;
    } else {
      btn.textContent = "📡 Send to C72 locate list";
      btn.title =
        "Need to physically find this product's tags on the shelf? This " +
        "queues it on the gun's LOCATE tab — no EPC typing on the C72.";
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
        await postJson("/api/locate-queue", {
          sku: phistData.sku,
          label: title,
          worker: operatorEl.value || null,
        });
        msg.textContent =
          "On the locate list ✓ — open LOCATE on the C72 and tap LIST.";
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
        '<li class="inventory__empty">Nothing queued — use "Send to C72 ' +
        "locate list\" on any product's panel.</li>";
      return;
    }
    list.innerHTML = entries
      .map(
        (e) => `<li class="recent__item" style="display:flex;align-items:center;gap:10px">
        <div style="flex:1;min-width:0">
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
          "Serialized products need a name — shorten it instead of clearing.";
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
    msg.textContent = "Label saved ✓ — new prints use it.";
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
      msg.textContent = `${qty} label(s) queued ✓ — collect at the printer (Print queue tab tracks them).`;
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
async function undoHistoryEvent(e, btn) {
  if (!e || !e.undo) return;
  // Resolved/dismissed review tasks: undo = reopen — the task returns to
  // the Review inbox and this resolution entry leaves History (the task
  // is simply open again, as if never closed).
  if (e.undo.kind === "review-reopen") {
    if (
      !confirm(
        `Reopen this review task?\n\n${e.title || e.sku || ""} — it goes ` +
          `back to the Review inbox, as if it was never closed.`
      )
    )
      return;
    btn.disabled = true;
    try {
      await postJson(`/api/review-tasks/${e.undo.task_id}/reopen`, {});
      await loadHistory();
    } catch (err) {
      btn.disabled = false;
      alert(err.message);
    }
    return;
  }
  // Dismissed live bin-mismatches: undo deletes the suppression — the
  // entry is back on the next Review fetch if the bins still disagree.
  if (e.undo.kind === "mismatch-undismiss") {
    if (
      !confirm(
        `Un-dismiss this bin mismatch?\n\n${e.sku || ""} — if the bins ` +
          `still disagree, the entry returns to the Review inbox.`
      )
    )
      return;
    btn.disabled = true;
    try {
      await apiJson(
        `/api/review/mismatch-dismissals/${e.undo.dismissal_id}`,
        { method: "DELETE" }
      );
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
          `audited bin update — Shopify, the bin map and the product's ` +
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
  if (e.undo.kind === "backorder-debt") {
    const operator = operatorEl.value;
    if (!operator) {
      alert("Pick who's scanning (top right) first.");
      return;
    }
    if (
      !confirm(
        `Clear this backorder note?\n\n${e.sku || ""} - the expected tag ` +
          `count drops by ${e.undo.units} unit(s), and the Tags vs ` +
          `On-hand check may flag this product again.`
      )
    )
      return;
    btn.disabled = true;
    try {
      await postJson(`/api/backorder-debts/${e.undo.debt_id}/clear`, {
        changed_by: operator,
      });
      await loadHistory();
    } catch (err) {
      btn.disabled = false;
      alert(err.message);
    }
    return;
  }
  // On-hand corrections: two-phase undo. The unconfirmed call answers
  // with exactly what will happen — including the CURRENT live value, in
  // case something else moved the number since — and that text IS the
  // confirmation prompt.
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
    alert("Could not undo that link — try again.");
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
