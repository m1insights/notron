# Directions — Notron Mac companion app

House palette rules apply to both: whites/blacks/greys/blues, one cyan accent, never red/yellow/green/amber, good/bad = opacity+weight not hue.

## Direction A — "Notes-native"
The friend, not the tool. Matches the README's own voice ("she," warm, plain) and the Simple/$12-mo tier's non-technical buyer.

- **Palette:** off-white ground (`#FAFAFA`), ink text (`#1A1A1E`), warm grey borders (`#E4E4E8`), cyan accent (`#0FA3B1`) used sparingly — one CTA, one active state at a time.
- **Type:** SF Pro (system), generous size — 17pt body, 28pt headline. No condensed weights.
- **Shape:** 14px radius on cards, 10px on buttons/fields. Soft 1px borders, near-zero shadow (2px blur, 4% opacity) — Notion/Craft-level restraint, not skeuomorphic.
- **Density:** low. One decision per screen. Lots of whitespace, like Greenlight/Brick's permission screens.
- **Motion:** a single 200ms ease on state changes (toggle, validate checkmark). No bouncy spring.
- **Imagery:** none needed — this is a settings app, not a marketing site. If anything, a single line-weight icon per permission card (SF Symbols).

## Direction B — "Operator"
Control-panel confidence, for the Advanced/free tier and specifically the Skills & Plugins screen — the audience that will judge Notron by whether it feels like a real dev tool, not a toy.

- **Palette:** near-black ground (`#0B0B0D`), off-white text (`#F2F2F4`), the same single cyan accent (`#0FA3B1`) now doing more work — active toggles, the usage meter, links.
- **Type:** SF Pro + SF Mono for anything that's a value (API key, model ID, token count) — matches Vapi/Cursor/Cloudflare AI Gateway references.
- **Shape:** 8px radius, sharper than Direction A. 1px hairline borders at 12% white opacity.
- **Density:** high. Settings rows stack tightly (Retool/Cloudflare pattern) — this is a screen a technical user scans, not admires.
- **Motion:** near-instant (100ms), no easing flourish — feels responsive, not decorative.
- **Imagery:** none; monochrome app icons for each connected skill/tool (Gmail, Slack-style icon treatment seen across the Mobbin integrations boards), desaturated to fit the palette.

## Recommendation going into Gate B

Not an either/or — **ship Direction A as the base system, with Direction B's palette/density swapped in specifically when the app is in Advanced mode** (Settings-Advanced and Skills & Plugins screens only). Same token set, same component shapes, just a dark/dense skin on the two screens the technical audience actually lives in. This mirrors how the product already segments its two audiences, so the visual language should segment the same way rather than forcing one look on both.

Reference boards pulled directly from Phase 2 Mobbin sweep — see `01-research.md` for the full link list.
