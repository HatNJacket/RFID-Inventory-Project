# RFID Terminal - Aesthetic Guidelines (DRAFT for discussion, 2026-09-09)

The web terminal's look is deliberate: **the Shopify-admin family** -
light gray workspace, white cards with hairline borders, blue focus
states, pill badges, one dark primary button (styles.css line 1 says
exactly this). It is a warehouse tool first: read at arm's length, on
an iPad or a PC, sometimes with gloves on. Every rule below serves one
of those two facts. Nothing here is law until Nick signs off.

## 1. The north star

- Calm when healthy, loud only when wrong. A screen where everything
  is fine should read almost gray-on-white; color is reserved for
  state that needs a human.
- Glanceable. The operator decides from three feet away whether a row
  needs them. Numbers big, labels small, one signal per element.
- One design system, two themes. Everything renders in light AND dark
  from the same token set - dark mode is real tokens, never an
  afterthought.

## 2. Color comes from tokens, nowhere else

All color goes through the CSS variables in `:root` (styles.css):

| Token | Meaning |
|---|---|
| `--bg` / `--card` / `--card-2` | workspace / card surface / inset surface |
| `--line` | every hairline border |
| `--ink` / `--ink-dim` | text / secondary text |
| `--accent` / `--accent-soft` | the ONE interactive blue (focus, selection, links) |
| `--chip-bg` / `--chip-ink` | identity chips (bin names) |
| `--ok-bg` / `--ok-ink` | confirmed fact, done, matches |
| `--warn-bg` / `--warn-ink` | needs a look, unconfirmed, partial |
| `--bad-bg` / `--bad-ink` | wrong, blocked, over-count, unpaired |
| `--btn-bg` / `--btn-ink` | the one primary button per view |

**Semantics before shade**: green = the system CONFIRMED it, yellow =
a human should look, red = something is wrong or will bite, blue =
you can act here, dim = context. If a new state doesn't fit one of
those five, that's a design conversation, not a new color.

**The one sanctioned exception**: the History/Review chip palette in
app.js (`EVENT_META` and the category map) is a fixed set of hex
colors on purpose - dozens of event types need stable, distinct hues
in both themes. New event types pick from hues already in that map's
range; everything else uses tokens.

Hard rule: no bare hex anywhere else in JS or HTML. (Current debt:
two strays - the resume list's unpaired badge and one red in the
verify area - both mine to fix.)

## 3. Styling lives in styles.css, not in JS strings

New UI gets a class in styles.css with a comment saying what it's for
and which decision shaped it (the file is written as a decision log -
keep that). No `style="..."` attributes and no `element.style.cssText`
in new code; a JS-built element gets `className`, and the look lives
where the theme can reach it.

Why this is a rule and not a preference: inline styles are invisible
to dark mode, invisible to a later redesign, and they're exactly how
the drift Nick flagged happens - each one looks harmless alone.
(Current debt: 55 `style=` attributes + 17 `cssText` blocks in
app.js. Proposed: one cleanup pass that moves them into classes,
visually identical, verified against the local server in both
themes.)

## 4. Typography

- `--sans` (Inter/system) at 14px base. Titles/names 14-16px weight
  650; buttons and labels weight 550; meta lines 11-12px in
  `--ink-dim`. Nothing bigger than 17px except the batch trackers.
- `--mono` for anything an operator compares or reads aloud: SKUs,
  barcodes, EPC tails, bin names, timestamps, counts in trackers.
- Sentence case everywhere. ALL-CAPS is reserved for tiny structural
  labels (step names, "SKU:") and the C72, which has its own denser
  conventions.
- Copy rules: plain hyphens, never em dashes (standing rule; a sweep
  of old copy is still TODO). Counts as "3 box(es)". Warnings say
  what happened AND what to do next, in one sentence each.

## 5. Surfaces and spacing

- The page sits on `--bg`; content sits in cards: `--card`, 1px
  `--line` border, 8-10px radius, `--shadow`. Insets and wells use
  `--card-2`. No borderless floating text, no heavy borders.
- 8px rhythm: gaps and paddings are 4 / 6 / 8 / 10 / 12 / 16.
  Radius: 6 for chips, 8-10 for cards, 999 for pills.
- Lists are rows, not boxes-in-boxes: one card per item (`.bcell`,
  `.mlrow`, `.recent__item`), image left, name + meta stacked, the
  number or action docked right. Indent 18px to show hierarchy (the
  box-set parts pattern) instead of nesting cards.

## 6. Reuse the vocabulary before inventing

The site already has a part for almost everything. New features
compose these; a new pattern is added only when none fits, and then
it goes in styles.css with a name and a comment.

- **Status pills** (`.pill`, header): quiet hairline badge + dot.
- **Chips**: bin chips (`--chip-*`), `binlabel` meta chips, History
  event chips (EVENT_META). Chips are nouns, not buttons.
- **Cards**: `.bcell` product rows, audit cards, `.mlrow` overlay
  rows, `.recent__item` lists.
- **Overlays**: ALWAYS the native modal shell (`phist-overlay` /
  `linkbox` / `serialbox` panels, `.mlrow` rows, `linkbox__input`
  fields, `.reset` close). No bespoke floating divs - this was the
  2026-09-08 restyle and it's the standing bar.
- **Buttons**: one dark primary (`--btn-*`) per view for THE action;
  everything else is `.reset` (quiet, hairline). Destructive actions
  get red text, not red fill, plus a confirm.
- **Freshness**: data panels that cache wear a `freshtag` (yellow
  "last refreshed" / green "Up to date ✓") and refresh via
  `refreshify()` - never a bare reload link.
- **Emoji as icons**: the site uses a small stable set (📦 receiving,
  ⚠ warning, ✓ done, 🏷 labels, ⧉ multi-box set, 🎯 targeted, ⊘
  skipped, ✕ remove). One emoji at the START of a label, never
  mid-sentence, never decorative. New icons join the set only with a
  fixed meaning.

## 7. State display rules

- Color never carries meaning alone - every red/yellow/green is
  paired with a word or count ("2 silent - no sales explain it").
- Progress is two numbers ("12/77"), right-docked, mono, colored by
  verdict. Bars are thin (4-5px) and only under the numbers.
- Empty states say what to DO ("Scan a BIN barcode to start"), not
  just that nothing is here.
- Anything the system did on its own (auto-resolved, self-closed)
  wears the muted "system" tone, never the green "a human confirmed
  it" tone.

## 8. Process (how we keep to this)

- Before building UI, name which existing components the feature
  composes. If the answer is "none", design the piece in styles.css
  first.
- Verify every UI change on the local server in BOTH themes (the
  dark-mode toggle is `prefers-color-scheme`; resize_window can
  emulate it) before deploying.
- This file is the reference the same way ROADMAP.md is the status
  ledger. When a rule changes by discussion, the change lands here in
  the same commit as the code that uses it.

## Open questions for Nick

1. Density: is the current row height comfortable on the warehouse
   iPad, or should list rows get taller tap targets?
2. The chip hex palette: keep as-is (stable in both themes) or move
   into tokens too?
3. Emoji: keep the icon set, trim it, or replace with real SVG icons
   over time?
4. The inline-style cleanup pass (55 + 17 spots, visually identical):
   worth doing now, or fold into future touches per-file?
5. Anything on the site TODAY that reads wrong to you aesthetically -
   concrete offenders to fix first?
