---
name: sync-heart-smart-masterclass-live
description: Sync Heart Smart Masterclass dashboard data directly from Meta MCP, the GHL REST API (Private Integration Token), and Google Sheets to JSON — no BigQuery, no Apps Script, no agency-scoped MCP
---

# Sync Heart Smart Masterclass Live Dashboard

**Project directory**: `/Users/jayvee/Documents/ds-work/heart-smart-masterclass-live-dashboard`
— `cd` there first; this skill can be registered globally so it can be
invoked from any session, but every command below assumes that cwd (see
IMPLEMENTATION.md Step 0).

Pulls Meta Ads, the GHL funnel snapshot, and the two Google Sheets straight
into `dashboard/public/data/*.json`. Same architecture and mechanism as
`fa-masterclass-live-dashboard`'s `/sync-fa-masterclass-live` — direct Meta
MCP, direct GHL REST+PIT, direct Sheets CSV export, no BigQuery, no Apps
Script — pointed at Heart Smart's own Meta account, GHL location/pipeline,
and Google Sheets. Several data-mapping details differ from FA's version;
those differences are called out explicitly below rather than silently
inherited from the template.

**GHL access is a location-scoped Private Integration Token (PIT)**, same as
FA — the token lives in `sync/.env` (`HS_GHL_PIT=...`), git-ignored, never
hardcoded into any script and never committed (this repo is **public**).
Load it before calling `fetch_ghl.py` or `classify_attribution.py`:
`set -a; source sync/.env; set +a`.

**Heart Smart's CONSOLIDATED sheet has no usable `Attribution` column of its
own** (only 3 of 1,861 rows filled in when checked 2026-09-16) — unlike FA's
sheet, which gained a real `Attribution` column and no longer needs this.
Per user decision 2026-09-16, Cash Attribution falls back to classifying
each transaction email via GHL's `attributionSource` (same mechanism FA used
*before* its sheet had the column), cached in `sync/attribution_cache.json`
(committed — the same emails already appear in `cash-attribution.json`, so
this adds no new exposure). GHL has no batch-by-email lookup, so this is one
`contacts/search` call per email — capped `--limit` per run
(`classify_attribution.py`), meant to be run repeatedly across syncs to work
through any backlog of newly-appearing emails.

**Heart Smart's Webinar Tracker sheet must be fetched by `gid`, not tab
name** — its name-based ("X – Auto") gviz lookup silently resolves to an
unrelated 363-row tab in the same spreadsheet (confirmed by testing: no
`Webinar Date`/`Attended Webinar` columns at all). `fetch_sheets.py` always
uses `REGISTRATIONS_GID = "535091378"` for this sheet — don't "simplify" it
back to a tab-name lookup.

**Heart Smart's "Application" count has a different rule than FA's**:
Column K ("Call Booked Event") containing "application" (case-insensitive)
is the *only* condition — there's no Booking Status gate. FA's Booking
Status column means booked/not-booked; Heart Smart's means booking
*channel* (Outbound/Funnel/Charlie AI/Online/etc.) — "BOOKED" appears only 2
times total in that column, so reusing FA's exact rule would have massively
undercounted. Confirmed with the user via AskUserQuestion 2026-09-16.

**All period math (Weekly/Monthly/Custom on the Marketing page) is anchored
to Sydney's calendar date**, not the viewer's browser timezone —
`src/utils/dateRanges.ts`'s `sydneyTodayISO()` — inherited unchanged from
the FA template; keep any future date-range code in that same
string-comparison style.

**A truncated sheet read is a real, recurring risk** — `fetch_csv_rows`
retries spaced ~8s apart and raises `SuspiciousReadError` against a
persisted baseline (`sync/row_count_baseline.json`, gitignored) rather than
silently returning an undercounted pull. Do not catch this and proceed
anyway — tell the user a Filter looks active on the sheet.

## Usage

```bash
/sync-heart-smart-masterclass-live
```

Options:

```bash
/sync-heart-smart-masterclass-live --meta-days 7      # Only refresh the last 7 days of Meta data
/sync-heart-smart-masterclass-live --ghl-only         # Skip Meta, refresh only GHL (funnel + attendance)
/sync-heart-smart-masterclass-live --classify-limit N # Classify up to N more attribution_cache.json backlog emails (default 200)
/sync-heart-smart-masterclass-live --no-push          # Update local JSON, skip git commit/push
```

## What it does

1. **Meta Ads** (`act_510603536638743`) — pull daily spend/impressions/link
   clicks/leads via Meta MCP (`time_increment=1`), merge into
   `marketing-performance.json`'s `daily` array by date (upsert, no dupes).
2. **GHL Webinar Pipeline** (`fVjHZmcsCSemRfeoRBct`, location
   `9D1lM8MPCly9IoR5YCIa`) — `python3 sync/fetch_ghl.py funnel-stages` (needs
   `HS_GHL_PIT` loaded from `sync/.env`). 14 direct REST calls to
   `/opportunities/search`, one per stage, `meta.total` is the count.
   Overwrites `funnel-stages.json` — it's a point-in-time snapshot, not a
   history, so there's nothing to merge.
3. **Registrations, Attendance, Application** — all three come from the same
   one pass over the Webinar Tracker sheet (`10UaxFImvcdS-TwarP9oBWwAw87XBmOlhiCNGNWrhKmQ`,
   gid `535091378`), grouped by Webinar Date (Column F), each counted as
   **unique emails** (Column C) per date:
   - **Registered**: every row for that date.
   - **Attended** (Column P, "Attended Webinar"): rows with a non-empty value.
   - **Application** (`masterclass-applications.json`): Column K ("Call
     Booked Event") contains "application" (case-insensitive) — no Booking
     Status condition (see note above).
   Run via `sync/fetch_sheets.py registrations` / `attendance` / `applications`.
   **Merge onto the existing committed files, don't overwrite** — defense
   against a truncated read slipping past the SuspiciousReadError check.
4. **Revenue / attribution** (Google Sheet `1gagwwkU9N2ASJRtS0gl_Ei7VbE9Du-T2aPjlXXjP0Ds`,
   tab `CONSOLIDATED`) — pull via `sync/fetch_sheets.py transactions`, which
   joins each row's email against `sync/attribution_cache.json` (see note
   above) rather than the sheet's own near-empty Attribution column. Before
   this step, run `python3 sync/classify_attribution.py --limit <N>` to
   classify any new emails the cache doesn't have yet (GHL
   `attributionSource`: PAID if `utmMedium` is `paid`/`paid_social`, or
   `fbclid`/`fbc` present, or `sessionSource` is `Paid Social`; else
   ORGANIC). Rows whose email still isn't classified after that are skipped,
   not guessed — re-run with a higher `--classify-limit` if the skip count
   looks large. Overwrite `cash-attribution.json`'s
   `transactions`/`dailyBreakdown`/`monthlySummary` wholesale from the fresh
   pull each time.
5. **Composite** — regenerate `marketing-data.json` from the files above
   (funnelSnapshot, masterclassRuns, adSpendDaily, cashAttributionDaily,
   oneOffEvents: []) — the frontend fetches this one first and errors if
   it's missing. `masterclass-applications.json` is fetched separately by
   the page, not folded into this composite.
6. **Commit & push** `dashboard/public/data/*.json` and `sync/attribution_cache.json`
   to the repo.

See `IMPLEMENTATION.md` for the exact tool calls, tag formats, and file
shapes.
