# RFID Inventory — operating manual for Claude sessions

FastAPI + Azure SQL web terminal ("Scan Station") plus a Chainway C72
Android app (`c72-app/`) for RFID batch-tagging Telescopes Canada's
warehouse, backed by the Shopify GraphQL Admin API (2026-07, client
credentials). Prod: https://telcan-rfid.azurewebsites.net

**Before feature work, read [ROADMAP.md](ROADMAP.md)** — the living
status ledger: what's done, Steve's TODO list (do NOT start those
without asking him), and standing decisions. `git log` is the detailed
change log; commit messages here are written to be read later.

## Hard rules (each of these has burned us)

- **NEVER auto-write inventory counts** to Shopify. On-hand writes are
  operator-confirmed, logged with undo, and gated by `SHOPIFY_WRITE_MODE`
  (ONE switch since 2026-09-28: "disabled"/empty = off, anything else =
  on; old comma lists read as on). Audits are the source of truth
  (scope reset, 2026-09-28): raises and lowers are offered from the
  audited count, and the one lowering guardrail left is the
  FIRST-TAGGING ban (a product that never completed a batch tagging
  lowers only as far as recorded sales cover; Nick kept this rule).
  On-hand *sync* stays blocked - writes happen at operator
  confirmation only, never in the background.
- **Deploy ONLY via `py dev/deploy.py`** (runs mkdeploy's Python
  zipfile build, deploys PROD only - Nick 2026-09-29: stop deploying
  the dev twin, it wasn't used). The underlying pieces stay available:
  `py dev/mkdeploy.py` then
  `az webapp deploy -n telcan-rfid -g shopify-automation-rg --type zip --src-path dev/deploy.zip`.
  The dev twin (telcan-rfid-dev, own sqlite, Shopify writes off) still
  exists but is NOT deployed or mirrored; `--with-dev` does both only
  if Nick asks for dev again. Never point it at the prod database.
  PowerShell `Compress-Archive` writes backslash zip entries that break the
  Linux container — it has downed prod twice. Don't deploy while changing
  app settings (the restart collides with the zip deploy).
- **The GitHub Actions red X on pushes is intentional** (publish-profile
  workflow disabled). Never "fix" or delete it.
- **The TELCAN mirror is REMOVED (2026-08-07) — never reintroduce it.**
  Its sync died Dec 2025 and it kept poisoning records through every path
  it was left in (F9394B as `DB24010501`; batch 126's ToupTek shelf got
  renamed SKUs and handles cross-wired to the WRONG products). Lookup
  order is **live `rfid_bin_map` → live Shopify API**, nothing else; the
  `dbo.Shopify_*` tables still exist in the database but no app code may
  read them. The one-off record repair lives at
  `dev/repair_mirror_records.py`. Quantities come from the live API (bin
  map snapshot as offline fallback). Compare SKUs case-insensitively
  everywhere (`.upper()`), always.
- **Never batch-edit UTF-8 templates with PowerShell** (`-replace` +
  `Set-Content` produced mojibake). Use the Edit tool. Git commits: write
  the message to a file and `git commit -F <file>` — inline here-strings
  break in this harness.
- Steve runs Windows, no venv, `py` launcher. Give exact commands.

## Layout

- `app/main.py` — the entire API (scan station, batches, bins, audit,
  review, history, on-hand). `app/models.py` — schema (Azure SQL prod,
  sqlite for tests; new columns need a one-off ALTER script for prod).
- `app/static/app.js` + `app/templates/index.html` — the whole web
  terminal (vanilla JS, tabs: Home / Scan / Batch / Inventory / Queue /
  Audits / History / Settings; the Review inbox was removed in the
  2026-09-28 scope reset). Event chips: `EVENT_META` in app.js.
- `c72-app/` — Android source (one MainActivity). Build:
  `py c72-app/build.py` → signed APK lands in `app/static/tc-rfid-sweep.apk`
  (served to the gun). Bump `versionCode`/`versionName` in
  `c72-app/AndroidManifest.xml` every release.
- `print_agent.py` — runs on the warehouse PC as the SYSTEM scheduled
  task "RFID Print Agent" (`schtasks`, at boot, no login needed since
  2026-09-28; wraps `C:\rfid\run_agent.cmd`). v6+ self-updates from
  the server; the web restart button only works while the process is
  alive - if it is fully dead, `schtasks /run /tn "RFID Print Agent"`
  on that laptop. Logs at `C:\rfid\print_agent.log`.
- `dev/` — session tooling: `mkdeploy.py`, `run_local.py` (seeded browser-
  verify server, port 8123), `tests/` (self-contained suites + `run_all.py`).

## Testing & verification

- Suites in `dev/tests/` are standalone scripts: temp sqlite DB, mocked
  `app.shopify.*`, FastAPI `TestClient`, PASS/FAIL per check, exit code.
  Run all: `py dev/tests/run_all.py`. Add a new suite for each feature.
- Browser verification: launch config `rfid-uiverify` (`.claude/launch.json`,
  untracked) runs `dev/run_local.py` — sqlite seed + fake Shopify on
  port 8123. Verify web changes there before deploying.
- Prod smoke test after deploy: `curl` the site, grep the served
  `/static/app.js` for a new symbol. Older scratchpad suites failing on
  codepage/network are pre-existing noise.

## Conventions

- C72 keeps UX-only state ON THE GUN (prefs JSON keyed to batch id: scan
  order, prior-asked); the server stores only essentials.
- Side trips = batches with `parent_batch_id`. They never count a bin as
  done, never pose as batch verification in History, and are excluded from
  audit's completed-bin logic.
- Every mutating endpoint logs to History (`BarcodeChange` rows or derived
  events) with an undo where sane. New event types need an `EVENT_META`
  entry (label + colour) in app.js.
- Batch flow: collect → check → pair → verify. Labels queue once
  (`collecting` → `printing`); `tagged_before` boxes count as units but
  never labels.

## Session workflow (Steve's multi-window structure)

One session per task, scoped to a feature area. When significant work
lands: commit with a detailed message, deploy if appropriate, update
ROADMAP.md (status/TODOs/decisions), and update auto-memory if a durable
decision changed. Keep THIS file small and stable — never append change
history here.
