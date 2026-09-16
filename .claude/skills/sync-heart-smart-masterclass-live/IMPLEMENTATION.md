# Implementation Guide

Step-by-step for what Claude executes on `/sync-heart-smart-masterclass-live`.
Meta calls are Claude tool calls made from inside the session — they can't
be scripted standalone. GHL and Google Sheets are both real, standalone
scripts (`sync/fetch_ghl.py`, `sync/fetch_sheets.py`, `sync/classify_attribution.py`)
since neither needs a session-bound MCP connection.

**Step 0, always**: if this skill is ever registered globally
(`~/.claude/skills/`), the shell's cwd when the skill runs is not guaranteed
to be the project. Every `sync/fetch_*.py` / `sync/classify_attribution.py`
command below is a relative path — `cd` first:
```bash
cd /Users/jayvee/Documents/ds-work/heart-smart-masterclass-live-dashboard
```

## Config (confirmed real, safe to hardcode)

- Meta ad account: `510603536638743` (Miyagi - Heart Smart Australia, AUD)
- GHL location: `9D1lM8MPCly9IoR5YCIa`
- GHL Webinar Pipeline: `fVjHZmcsCSemRfeoRBct`
- Registrations sheet ("Heart Smart Webinar Lead Tracker"): `10UaxFImvcdS-TwarP9oBWwAw87XBmOlhiCNGNWrhKmQ`, **gid `535091378`** (not tab name — see SKILL.md)
- Revenue sheet: `1gagwwkU9N2ASJRtS0gl_Ei7VbE9Du-T2aPjlXXjP0Ds`, tab `CONSOLIDATED`

**GHL PIT** (`HS_GHL_PIT`) lives in `sync/.env`, git-ignored. Load it before
calling `fetch_ghl.py` or `classify_attribution.py`: `set -a; source
sync/.env; set +a`. Never print, log, or write the raw token value into any
file that gets committed, any code file, or any commit message.

### 14 pipeline stage IDs

| Stage | pipelineStageId |
|---|---|
| Optin | `8184bf17-80d0-4abb-a7bd-b11acaaae3e7` |
| Registered GA | `cd594f64-cb1d-4207-ac43-ed6610aff9c7` |
| VIP Upgrade | `92cee92a-0189-4724-a6b8-11c17838b754` |
| Downsell Buyer | `1c2e161e-e700-49c6-8145-433a42277e3a` |
| Replay Optin | `94acf15b-a7af-4d4d-9f2a-0fea4af0464c` |
| Purchased BG+ Diagnostic | `f0501c94-a6d2-46c3-a864-630a37c3ae9b` |
| Received Referral | `6755c59b-fa98-4cbb-aeb7-229d9a7879fc` |
| Received Results | `aa9d326d-4b75-4b9c-bd56-360ba443e211` |
| Appointment Booked | `16fc6098-720b-4640-a902-b802e446aa66` |
| Call Cancelled/Need to RS | `1fd7f98c-530d-4f19-8476-eb09212c14ea` |
| No-Show | `3b690c75-a92b-4175-9ffb-9abadc985c65` |
| Pending Sale | `450fbd93-d2d7-4299-be76-7a6971984c87` |
| Close Lost | `0ed40a14-4841-4491-9c51-e9f6a03c4792` |
| Close Won | `b1d06d29-6619-4a13-8a28-3234e4a144ca` |

Re-run `python3 sync/fetch_ghl.py list-stages` (after loading `HS_GHL_PIT`)
to refresh this table if stages are ever added/renamed/reordered in GHL.

## Phase 1: Meta Ads → marketing-performance.json

Call the Meta Ads MCP for `ad_account_id=510603536638743`, `level=ad_account`,
`fields=amount_spent,impressions,link_click,lead`,
`time_range={"since":X,"until":Y}`, `time_increment="1"` (string, not
number). Detect the range to sync from the current
`marketing-performance.json`'s last date through today (default), or honor
`--meta-days N`. A single call can hit an internal response-size limit
around ~210 days — if the requested range doesn't come back in full, split
into two or more `time_range` calls and merge.

Parse each daily row (`amount_spent` strips `"A$"`/`"AUD"`/commas → float;
`impressions`/`link_click`/`lead` → int, treating `null` as 0), then merge
into the `daily` array by date (replace if the date exists, append
otherwise, keep sorted). Recompute `meta.totalDays`/`meta.dateRange`.

## Phase 2: GHL funnel snapshot → funnel-stages.json

Run `set -a; source sync/.env; set +a && python3 sync/fetch_ghl.py
funnel-stages`. Internally this hits `GET
https://services.leadconnectorhq.com/opportunities/search` once per stage
ID above, with `Authorization: Bearer $HS_GHL_PIT` + `Version: 2021-07-28`
headers. Read `meta.total` from each response.

**Same non-obvious API gotchas as FA's version, found by testing — don't
"simplify" them back out:**
1. Query params must be **snake_case** (`location_id`, `pipeline_id`,
   `pipeline_stage_id`) on `/opportunities/search` — camelCase 422s.
2. `/opportunities/pipelines` (used only by `list-stages`, the setup
   helper) wants **camelCase** `locationId` instead — inconsistent across
   GHL endpoints.
3. Cloudflare in front of the API blocks Python's default `urllib`
   user-agent outright (403, `browser_signature_banned`). `fetch_ghl.py`
   sets `User-Agent: curl/8.7.1` to work around this.

Sort stages by count descending for `position` (0-indexed), compute
`winProbability = round(count / totalOpportunities * 100, 2)`. Overwrite the
file wholesale — it's a snapshot, not a history.

## Phase 3: Registrations, Attendance, Application (Webinar Tracker sheet)

All three are one pass over the same sheet (fetched by **gid**, not tab name
— see SKILL.md), grouped by `Webinar Date` (Column F, parsed as `"Fri Apr
24"` + current year via `parse_webinar_date()`), each counted as **unique
emails** (Column C, "Email Address") per date:

- **Registered** → `masterclass-registrations.json`: every row for that date.
- **Attended** (Column P, "Attended Webinar") → `masterclass-attendance.json`:
  rows where that column is non-empty.
- **Application** (Column K, "Call Booked Event", contains "application"
  case-insensitive — **no Booking Status condition**, see SKILL.md for why)
  → `masterclass-applications.json` (shape `{meta, applications: [{date,
  applications}]}`).

Run via `python3 sync/fetch_sheets.py registrations` / `attendance` /
`applications`.

**Merge onto the existing committed files** for all three — read the
current JSON, index by date, overlay the fresh pull's dates on top (fresh
wins), keep any date the fresh pull doesn't have. Defense-in-depth against
the Filter risk (see SKILL.md) — even a filtered pull that somehow slips
past `SuspiciousReadError` would only overwrite the dates it happened to
see, never silently erase dates outside its filtered view.

If `fetch_csv_rows` raises `SuspiciousReadError`: **stop, don't catch it and
merge anyway.** Tell the user the sheet looks like it has an active Filter
right now (name the exact numbers from the error) and ask them to clear it,
then re-run.

As of 2026-09-16, the sheet's most recent registered run is **2026-08-26**
(no runs registered since) — a real ~3-week gap as of that date, not a sync
bug (confirmed the sheet itself has no newer rows). Worth a quick sanity
check on future syncs: if this gap grows much further, flag it to the user
rather than assuming it's expected.

## Phase 4: Revenue + attribution → cash-attribution.json

**4a. Classify new emails.** Run `python3 sync/classify_attribution.py
--limit <N>` (needs `HS_GHL_PIT` loaded). This:
1. Pulls raw `CONSOLIDATED` rows via `fetch_sheets.fetch_csv_rows(...,
   tab="CONSOLIDATED")` directly (not through `build_transactions()`, to
   avoid a circular dependency on the cache it's about to update).
2. Finds emails with a valid date+amount+email that aren't already in
   `sync/attribution_cache.json`.
3. For up to `--limit` of them, calls `POST
   https://services.leadconnectorhq.com/contacts/search` with body
   `{"locationId": "9D1lM8MPCly9IoR5YCIa", "filters": [{"field": "email",
   "operator": "eq", "value": email}], "pageLimit": 1}` — **camelCase**
   `locationId` here, unlike the snake_case `opportunities/search` above.
   Classifies via `attributionSource`: PAID if `utmMedium` is
   `paid`/`paid_social`, or `fbclid`/`fbc` present, or `sessionSource` is
   `Paid Social`; else ORGANIC. No contact found → currently defaults to
   Organic (the module docstring says "don't guess" — this is a known
   inconsistency between comment and behavior, not yet resolved; flag to
   the user if it matters for a specific sync).
4. Saves the updated cache.

GHL has no batch-by-email lookup (`in` operator isn't supported on `email`
— confirmed via a 422 listing the real allowed operators) and bulk-listing
all contacts isn't practical (this location has 244k+ contacts), so this is
one network round-trip per email, roughly 1-6s each depending on API
latency. The full backlog (1,427 unique emails as of 2026-09-16) took about
15-20 minutes running as a background process — for routine syncs afterward
the backlog is just new transactions since the last sync, so `--limit 200`
(the default) should clear it in well under a minute. If you launch this in
the background, verify it actually completed (check `sync/attribution_cache.json`'s
size, or the process list) before moving on — a session compaction or
restart can leave a background classification job orphaned without it
actually finishing.

**4b. Build transactions.** Run `python3 sync/fetch_sheets.py transactions`
— pulls `CONSOLIDATED` via CSV export, parses `Date`/`Name`/`Email`/`Product`/`Amount`/`Closer`/`Mode`,
and looks up each row's `source` (Paid/Organic) from `sync/attribution_cache.json`
rather than the sheet's own Attribution column (near-empty for Heart Smart —
see SKILL.md). Rows whose email isn't in the cache yet are skipped (printed
to stderr as a count) — run 4a again with a higher `--limit` if that count
is large.

Overwrite `cash-attribution.json` wholesale from the fresh pull each sync:
group by date for `dailyBreakdown` (sum `cashFromAds`/`cashFromOrganic`), by
`YYYY-MM` for `monthlySummary`, keep `transactions` as the full parsed list.

## Phase 5: Composite → marketing-data.json

Rebuild from the files above (`masterclass-applications.json` is fetched
separately by the page and does NOT feed into this composite):

```python
{
  "meta": {...},
  "funnelSnapshot": {"generatedAt": ..., "stages": [{"stage": s["name"], "count": s["count"]} for s in funnel_stages]},
  "masterclassRuns": [...],  # date/label/registered from registrations + attended from attendance, showUpRatePct = round(attended/registered*100, 2)
  "adSpendDaily": marketing_performance["daily"],
  "cashAttributionDaily": cash_attribution["dailyBreakdown"],
  "oneOffEvents": [],
}
```

## Phase 6: Deploy — commit, push

This repo has no `docs/` GitHub Pages split yet (not deployed as of
2026-09-16) — once it is, mirror FA's Phase 6 pattern (copy built output
into whatever directory GitHub Pages serves from). Until then:

```bash
cd /Users/jayvee/Documents/ds-work/heart-smart-masterclass-live-dashboard
git add dashboard/public/data/*.json sync/attribution_cache.json
git commit -m "Sync masterclass data through <date>"
git push origin main
```

Skip the push if `--no-push`.

## History

- **2026-09-16, first build**: recreated as a sibling project to
  `fa-masterclass-live-dashboard`, same architecture, pointed at Heart
  Smart's own Meta account/GHL location/sheets per the "Heart Smart
  Marketing.docx" mapping doc. Two data-mapping ambiguities were surfaced to
  the user via AskUserQuestion rather than assumed: (1) Application count
  rule — Heart Smart's Booking Status column doesn't work like FA's (it's a
  booking channel, not a booked/cancelled outcome), so Application counts on
  Call Booked Event containing "application" alone; (2) Cash Attribution
  source — Heart Smart's CONSOLIDATED sheet has no usable Attribution column
  (3/1,861 rows filled), so it falls back to classifying via GHL
  `attributionSource` (the same mechanism FA used before its own sheet
  gained a real Attribution column), cached in `sync/attribution_cache.json`.
  Classified the full backlog of 1,427 unique CONSOLIDATED emails
  (814 Paid / 613 Organic) as part of this first build. Frontend is the FA
  dashboard-source unchanged except: retinted `index.css`/`chartPalette.ts`
  from FA's dark-green/mint theme to Heart Smart's established red
  (`#FF5757`, matching Heart Smart's other live dashboards), header text and
  page title changed from "Freedom Academy" to "Heart Smart", package name
  updated. Also confirmed via testing: the Webinar Tracker sheet's tab-name
  lookup resolves to the wrong tab (363-row garbage vs. the real 3,763-row
  sheet) — must fetch by gid `535091378`.
