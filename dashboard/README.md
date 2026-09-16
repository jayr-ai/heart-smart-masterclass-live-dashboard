# Heart Smart Masterclass Dashboard

A standalone React/Vite dashboard (Masterclass + Marketing tabs) fed
directly from Meta Ads, GHL, and Google Sheets — no BigQuery, no Apps
Script. Sibling project to `fa-masterclass-live-dashboard`, same
architecture, pointed at Heart Smart Australia's own Meta account, GHL
location/pipeline, and Google Sheets.

```
Meta MCP ──┐
GHL REST API (PIT) ──┼──►  sync scripts / Claude session  ──►  dashboard/public/data/*.json  ──►  GitHub Pages
Google Sheets (CSV export) ──┘
```

## Running it locally

```bash
cd dashboard
npm install
npm run dev
```

Two routes:

- `/masterclass` — Masterclass Dashboard (per-run funnel + revenue, date selector)
- `/marketing` — Marketing Dashboard (Weekly/Monthly/Custom KPIs, Sales Breakdown by Channel)

`npm run build` type-checks and produces a production build; `npm run preview`
serves that build locally.

## Data pipeline

See [`.claude/skills/sync-heart-smart-masterclass-live/SKILL.md`](../.claude/skills/sync-heart-smart-masterclass-live/SKILL.md)
and its `IMPLEMENTATION.md` for the full sync mechanism, data-source IDs,
and the mapping decisions specific to Heart Smart (different from FA's
version in a few places — Application-count rule, and Cash Attribution
falling back to a GHL-classification cache since the sheet has no usable
Attribution column of its own).

`sync/fetch_ghl.py` and `sync/fetch_sheets.py` are standalone scripts (GHL
via a location-scoped Private Integration Token, Sheets via no-auth CSV
export). Meta Ads pulls happen via Claude's Meta MCP tool inside a sync
session, since that tool isn't callable outside one.

## Branding

Retinted from the `fa-masterclass-live-dashboard` template (dark
green/mint) to Heart Smart's established red (`#FF5757`), matching Heart
Smart's other live dashboards (EOD report, Revenue Dashboard, Meta report).
See `src/index.css` (`--color-fa-*` tokens — names kept as-is from the
template to minimize the diff) and `src/utils/chartPalette.ts`.
