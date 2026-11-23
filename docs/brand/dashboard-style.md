# Tishan's dashboard and UI style (Ledger to Logic)

Use this for every dashboard, web app, admin page, chart, report or README visual you build for Tishan, unless he says otherwise for that project. In this repo, fonts are self-hosted in `app/web/static/fonts/` (with their licenses), and the README hero is `docs/ap-repo-hero.svg`.

Positioning note: Tishan's brand is AI first (AI Automation Engineer). Do not frame him as a finance analyst in any UI copy, about page or footer.

## Design tokens (drop these in as CSS variables)

```css
:root {
  /* Palette A: Ledger to Logic. Only these four hues plus tints of them. */
  --navy:   #0B1F33;  /* text on light, dark surfaces, borders with authority */
  --teal:   #14B8A6;  /* automation accent: lines, chart series, links on navy, active states */
  --amber:  #F59E0B;  /* ONLY "needs a human / awaiting approval" */
  --paper:  #F8FAFC;  /* light background */

  --surface:   #FFFFFF;                 /* cards on paper */
  --border:    rgba(11, 31, 51, 0.12);
  --text:      var(--navy);
  --text-2:    rgba(11, 31, 51, 0.70);  /* secondary text: navy tint, not a new gray */
  --text-3:    rgba(11, 31, 51, 0.50);
  --teal-soft: rgba(20, 184, 166, 0.12);/* selected rows, chips, area fills */
  --amber-soft:rgba(245, 158, 11, 0.14);

  --font-sans: "Inter", system-ui, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, monospace;
  --radius: 12px;
  --space: 8px; /* 8px spacing grid */
}
```

Self-host from `app/web/static/fonts/` (Inter 400/500/600, JetBrains Mono 400/500), loaded with `@font-face` from `/static/fonts/`.

## Layout

- Light by default: paper page, white cards, 1px `--border`, `--radius` corners, no heavy shadows, no gradients.
- Optional dark mode: navy background, paper text, teal accents, the same amber rule.
- Thin navy top bar (optionally with a teal line and a short amber end-cap) as the header accent.
- Calm and uncluttered: generous whitespace, clear hierarchy, one primary action per view.
- Responsive: rows and chip groups wrap; it should work at about 1280 px wide and on a phone.

## Type

- Inter for headings and UI copy. Page title is SemiBold 600, section titles are Medium 500, body is Regular 400.
- JetBrains Mono for numbers, amounts, IDs, timestamps, codes and badges. Use `font-variant-numeric: tabular-nums` so columns line up.
- Right-align numeric table columns. Never italicize. No all-caps paragraphs.

## Color rules (important)

- Text on light backgrounds is always navy. **Never put teal text on paper or white**, because it fails contrast. Teal goes on lines, borders, icons, chart marks and fills.
- **Amber means a person needs to look at or approve this**: needs review, pending approval, an exception queue. Do not use it for decoration, warnings in general, or as a chart series.
- Marks inside teal or amber fills are navy, not white.
- Status: done or matched gets teal, needs review gets amber, and neutral or draft gets a navy tint. Avoid adding red. If a destructive or error state truly needs its own color, keep it to a small label or icon and mention it to Tishan.

## Charts

- A single series is teal. A second series is navy. Further series are navy and teal tints. Comparison or baseline lines are dashed in a navy tint.
- Hairline gridlines in `--border`. Axis labels use `--text-3` in the mono font.
- Label lines directly where possible, and keep legends minimal.

## Signature motif

When a page explains a process, use the four-step flow: an open teal ring, then a teal double ring, then an amber node with a navy check (the human approval step), then a solid teal node, joined by a thin teal line. Always four steps, and amber only on the approval step. No robots, brains, neon or "AI sparkle" icons.

## Content

- Plain, specific, evidence-first copy. Never invent metrics, employers, credentials or results.
- Tagline (use sparingly, once per surface at most): "AI does the busywork. People make the call."
- Public repos use synthetic data only.
