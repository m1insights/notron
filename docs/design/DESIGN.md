# Notron Mac Companion — Design System

Derived from: https://claude.ai/code/artifact/3e182338-2fa2-459e-94bc-b91f276f665d, direction **mix** (Notes-native base + Operator dark skin on the Advanced-only Skills & Plugins screen), 2026-08-31.

## Principles
1. **Two audiences, one system.** Same tokens everywhere — the Skills & Plugins screen swaps to the dark palette because it is Advanced-tier only, not because it's a different app.
2. **No Terminal, ever.** Every value/state a technical user would expect to type is instead a field, a toggle, or a drag target.
3. **Opacity and weight carry meaning, never hue.** Connected/on = full opacity + accent. Disconnected/off/core-locked = dimmed, never a warning color.
4. **Complex parameters get a slider and a plain-language question, never a raw number.** "Becky" — the non-technical persona this app is designed around, per user 2026-08-31 — doesn't know what a token is. She can drag a dot between "Careful" and "No limit." Any control that touches cost, memory depth, or model behavior gets this treatment: a one-line question, a slider, plain endpoint words.

## Colour

| Token | Light (default) | Dark (Skills & Plugins only) | Use |
|---|---|---|---|
| `bg` | `#FAFAFA` | `#0B0B0D` | Window background |
| `surface` | `#FFFFFF` | `#1C1C21` | Cards, rows, fields. Dark value lifted from `#131316` on 2026-09-02 — a 2% step over `bg` is invisible on a real screen; elevation has to be seen. |
| `surface-alt` | `#EFEFF1` | `#2A2A31` | Title bar, segmented control track, selected row. Dark value lifted from `#151519` on 2026-09-02 for the same reason. |
| `text` | `#1A1A1E` | `#F2F2F4` | Primary text |
| `text-dim` | `#6B6B70` | `#9A9AA2` | Secondary, captions |
| `text-faint` | `#8A8A90` | `#6B6B72` | Section labels, timestamps |
| `accent` | `#0FA3B1` | `#0FA3B1` (links render `#3FD4E0` on dark for contrast) | The ONE accent — CTAs, active toggles, active model tier |
| `hairline` | `#E4E4E8` | `#3A3A42` | Borders, dividers. It flips with the skin — a black hairline on the dark skin is no hairline, which is most of why the first "Your notes" build read as one flat sheet. |
| `success` | text/icon at full opacity + accent-adjacent teal `#28A745` used ONLY for the literal "Connected" checkmark (system-level confirmation, not a product status color) | same | Key-validated state |
| `text-faint-dark` | n/a | `#9C9CA6` | **Section eyebrows on the dark skin only** ("Core", "Added by you", "Monthly budget"). Fixed 2026-08-31 — the original `#6B6B72` failed contrast on `#0B0B0D`; this is the corrected value, don't regress it. |

No red/yellow/green/amber anywhere else. Good/bad, on/off, core/added = opacity + weight, never a second hue.

## Type

| Token | Size / Line / Weight | Use |
|---|---|---|
| `headline` | 22–26 / 1.3 / 700 | Screen title, money-screen hero |
| `title` | 20 / 1.3 / 700 | Section header (Settings, Skills & Plugins) |
| `body` | 14–15 / 1.5 / 500–600 | Card titles, primary copy |
| `caption` | 12.5–13 / 1.4–1.5 / 400 | Descriptions, secondary lines |
| `label` | 11–12 / 1 / 600–700, uppercase, 0.04–0.05em tracking | Section eyebrows ("Core", "Model tiers", "Monthly budget") |

**No monospace font anywhere in the GUI** — fixed 2026-08-31 (user feedback: it read as intimidating/cold, not readable). Literal values (API keys, timestamps) still get letter-spacing (`.02em`) for legibility, but stay in SF Pro like everything else. Font: `-apple-system, BlinkMacSystemFont, "SF Pro Text", Helvetica, Arial, sans-serif` (system on macOS — no bundling needed). This is a hard rule, not a preference — don't reintroduce a mono/code typeface anywhere in this app.

## Spacing
`space-1: 4px · space-2: 8px · space-3: 12px · space-4: 16px · space-5: 22px · space-6: 28px · space-7: 32px`

## Radius
`sm: 8px` (dark-skin cards, buttons) `· md: 10px` (light-skin buttons/fields) `· lg: 14px` (light-skin cards) `· pill: 999px` (segmented control, status pills, toggle tracks)

## Elevation
- Light skin: one level only — `0 20px 60px rgba(0,0,0,.15)` on the window itself; cards get no shadow, a 1px `hairline` border instead.
- Dark skin: no shadows; elevation is a lighter `surface` (`#1C1C21`) against `bg` (`#0B0B0D`) plus a 1px hairline. Both halves are load-bearing: drop the hairline and the cards vanish however light the surface is. Window shadow `0 20px 60px rgba(0,0,0,.5)`.
- Menu-bar dropdown: `0 20px 50px rgba(0,0,0,.18)` light / `.5` dark, always with a 1px border — it floats over arbitrary desktop content.

## Components

**Window chrome** — every settings-window screen is 720×520, radius 12px, native traffic-light dots (`#FF5F57`/`#FEBC2E`/`#28C840`, 11px, real macOS convention — not a product status color) in a 36px title bar (`surface-alt`), centered title in `text-dim` at 13px.

**Button (primary)** — accent fill, white/`bg`-colored text on light, `bg`-colored text on dark (so it never disappears against the dark accent), radius `sm`/`md` per skin, 11–12px vertical padding, 600 weight, no exclamation marks in the label.

**Segmented control** — track = `surface-alt`, radius `pill`-adjacent (8–10px), active segment = white/`surface` card with light shadow (light skin) or solid `accent` fill with `bg`-colored text (dark skin).

**Toggle** — track 36–38×20–22px, radius `pill`. Off = `hairline`-bordered/dimmed track. On = solid `accent` fill, white/`bg` knob offset right. This is the ONLY place a size/position change (not a hue change) signals state, alongside opacity.

**Skill card** (Core) — `surface` background, `hairline` border, radius per skin, name + one-line description + a non-interactive "Core" pill (`surface-alt` bg, `text-dim`).

**Skill card** (Added) — same shape, adds a source line in `accent` (letter-spaced, still SF Pro — not mono) and a live toggle (see Toggle above).

**Skill card** (Filer, highlighted-Core) — same Core anatomy but full-width, `accent`-tinted border (not a solid fill — it's still Core, just the one worth calling out) instead of `hairline`. Reserved for the one Core skill the product leads with; don't reuse this treatment for anything else.

**Slider** — track = `hairline`-colored bar, filled portion in `accent` up to the current value, circular knob (`surface` fill, 2.5px `accent` border) centered on the fill boundary. Always paired with: a plain-language question above (not a technical label), the live value top-right, and two endpoint words below the track (never raw units alone — "Careful — $5" not just "$5"). This is the only place a numeric system parameter (budget, memory reach, etc.) is exposed to the user — never a bare number field or dropdown for these.

**Add-skill drop zone** — dashed `hairline` border, radius per skin, centered label + one line of instruction. Never a solid border — dashed = "drop something here," a convention this system reserves exclusively for this action.

**API key field** — `surface` row, `hairline` border, radius per skin, value in `mono` with the middle masked (`sk-nf-••••••••••••3fQ2`), a right-aligned checkmark + "Connected" in success-teal once validated.

**Model tier row** — stacked rows sharing a `hairline`-separated container; each row = name (`mono` on dark) + one-line role description (`caption`) + a filled/unfilled `accent` selection dot, never a checkbox.

**Money card** — single card, `accent`-bordered, radius `lg`/`sm`, price in `headline` size with a `text-faint` "/mo" suffix, one line of what's included, primary button, trust line below in `text-faint` caption.

**Menu-bar list row** — no card chrome, just `hairline`-bottom-bordered rows: one line of body copy (bold the acted-on noun — "Call the pharmacy"), one line of `text-faint`/mono relative timestamp below.

**Empty/first-run state** — icon in an `accent`-tinted 14–16px-radius tile, `headline`, one `body`-size explanatory line, single primary button. No secondary/skip action on a permission screen — CLAUDE.md's hang-not-fail behavior means skipping isn't actually safe to offer here.

## Motion
Light skin: 200ms ease on toggle/validate/segment changes. Dark skin: 100ms, no easing curve — reads as instant, matching the technical audience's expectation of responsiveness over polish. Respect `prefers-reduced-motion` — both collapse to an instant state change.

## Do / Don't
- Do keep the accent to one use per screen at a time (one active toggle glow, one CTA) · Don't let two accent elements compete for the eye on the same screen.
- Do mask API keys and show a copy/paste affordance · Don't ever render a full unmasked key outside the one moment the user pastes it in.
- Do use the dashed border exclusively for "drop a skill here" · Don't reuse dashed borders for any other empty/placeholder state.
- Do keep Core skills visually locked (pill, no toggle) · Don't let a Core skill's toggle look interactive — it isn't, and CLAUDE.md's invariants mean it can't be.
- Do render traffic-light dots at real macOS size/color · Don't reinterpret them as a status system — they're OS chrome, not product color.
- Do keep every screen in SF Pro, including literal values · Don't reach for a mono/code typeface anywhere — it read as cold and technical in review and was removed 2026-08-31.
- Do expose a numeric system parameter as a slider with a plain-language question · Don't ever show Becky a raw token count, a dropdown of numbers, or a text field for a setting like this.
