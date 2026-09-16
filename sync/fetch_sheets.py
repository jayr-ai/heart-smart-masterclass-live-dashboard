#!/usr/bin/env python3
"""
Pulls the two Google Sheets that feed this dashboard, straight to JSON — no
BigQuery, no Apps Script, no auth (both sheets are link-viewable). Same
mechanism as fa-masterclass-live-dashboard's fetch_sheets.py, adapted for
Heart Smart Australia's sheets.

This script only touches the two Google Sheets. Meta Ads and GHL data
(funnel stages) are pulled separately — Meta via Claude's Meta MCP tool
inside the sync skill, GHL via fetch_ghl.py's direct REST+PIT calls.

IMPORTANT: the Webinar Tracker sheet must be fetched by **gid**, not tab
name — its name-based ("X – Auto") gviz lookup silently resolves to a
different, unrelated tab in the same spreadsheet (confirmed by testing:
363 rows of garbage vs. 3,763 real rows via the correct gid). The
CONSOLIDATED sheet's name-based lookup is fine (verified identical to its
own gid-based fetch).

Usage:
    python3 fetch_sheets.py registrations   # -> prints masterclass-registrations.json shape
    python3 fetch_sheets.py attendance      # -> prints masterclass-attendance.json shape (Column P)
    python3 fetch_sheets.py applications    # -> prints masterclass-applications.json shape (Column K only, see note)
    python3 fetch_sheets.py transactions    # -> prints CONSOLIDATED rows with source attribution
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REGISTRATIONS_SHEET_ID = "10UaxFImvcdS-TwarP9oBWwAw87XBmOlhiCNGNWrhKmQ"  # Heart Smart Webinar Lead Tracker
REGISTRATIONS_GID = "535091378"  # tab name lookup is unreliable here — see module docstring

REVENUE_SHEET_ID = "1gagwwkU9N2ASJRtS0gl_Ei7VbE9Du-T2aPjlXXjP0Ds"  # Heart Smart revenue tracker
REVENUE_TAB = "CONSOLIDATED"

REPO_ROOT = Path(__file__).resolve().parent.parent
ROW_COUNT_BASELINE_PATH = REPO_ROOT / "sync" / "row_count_baseline.json"


def _fetch_csv_once(sheet_id: str, tab: str | None = None, gid: str | None = None) -> list[dict[str, str]]:
    if gid:
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    else:
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={urllib.parse.quote(tab)}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(raw))
    # Sheet headers/values often carry stray whitespace (e.g. "Name ", " Amount ")
    return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in reader]


def _load_row_count_baseline() -> dict[str, int]:
    if ROW_COUNT_BASELINE_PATH.exists():
        return json.loads(ROW_COUNT_BASELINE_PATH.read_text())
    return {}


def _save_row_count_baseline(baseline: dict[str, int]) -> None:
    ROW_COUNT_BASELINE_PATH.write_text(json.dumps(baseline, indent=2))


class SuspiciousReadError(RuntimeError):
    """Raised when a sheet pull comes back much smaller than its last known
    good size — almost always someone's Filter (not Filter View) is active
    on the sheet, hiding rows from every reader including this script, not
    an actual data loss. Filter Views are per-viewer and don't affect this;
    the regular Filter does, for everyone, until it's cleared."""


def fetch_csv_rows(sheet_id: str, tab: str | None = None, gid: str | None = None, attempts: int = 4, retry_delay_seconds: float = 8.0) -> list[dict[str, str]]:
    """Retries spaced several seconds apart, not back-to-back — a person
    filtering the sheet to check something typically clears it within
    seconds to a couple minutes, so back-to-back retries (all landing inside
    the same filtered window) don't help, but a few seconds' gap gives a
    real chance of catching it unfiltered. Keeps the largest result seen.

    Then checks the result against a persisted per-(sheet,tab-or-gid)
    row-count baseline (sync/row_count_baseline.json). If the best result is
    still under 70% of the last known-good count, raises SuspiciousReadError
    instead of returning a partial dataset.
    """
    key = f"{sheet_id}:{tab or gid}"
    baseline = _load_row_count_baseline()
    last_good = baseline.get(key)

    best: list[dict[str, str]] = []
    for i in range(attempts):
        rows = _fetch_csv_once(sheet_id, tab=tab, gid=gid)
        if len(rows) > len(best):
            best = rows
        if last_good is None or len(best) >= last_good * 0.7:
            break
        if i < attempts - 1:
            time.sleep(retry_delay_seconds)
    if last_good is not None and len(best) < last_good * 0.7:
        raise SuspiciousReadError(
            f"'{key}' returned {len(best)} rows across {attempts} spaced attempts, "
            f"but the last known-good pull had {last_good}. This almost always means "
            f"someone has a Filter (not a Filter View) active on the sheet right now, "
            f"hiding rows from every reader. Ask them to clear it (Data > Remove filter, "
            f"or check the filter icon in the toolbar) and re-run the sync — do not "
            f"proceed with this data, it would overwrite good history with undercounts."
        )

    baseline[key] = max(len(best), int(last_good * 0.9)) if last_good else len(best)
    _save_row_count_baseline(baseline)
    return best


def parse_webinar_date(raw: str, today: datetime | None = None) -> str | None:
    """Sheet values like "Fri Apr 24" carry no year — same ambiguity as FA's
    sheet, same fix: assume the current year (checking the two adjacent
    years too, in case of a year-boundary run)."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw
    today = today or datetime.now()
    for year in (today.year, today.year + 1, today.year - 1):
        try:
            dt = datetime.strptime(f"{raw} {year}", "%a %b %d %Y")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _webinar_metrics_by_date() -> dict[str, dict]:
    """One pass over the Webinar Tracker sheet, keyed by Webinar Date
    (Column F), each value a dict of per-date sets of unique emails for
    registered/attended/application.

    Column layout confirmed 2026-09-16 (differs from FA's letter positions,
    same column *names*): C=Email Address, F=Webinar Date,
    K=Call Booked Event, O=Booking Status, P=Attended Webinar.

    APPLICATION rule differs from FA's: Booking Status here is a booking
    *channel* (Outbound/Funnel/Charlie AI/Online/etc.), not a booked/not
    outcome — "BOOKED" barely appears (2 rows total). Per user decision
    2026-09-16: count on Call Booked Event containing "application" alone,
    no Booking Status condition.
    """
    rows = fetch_csv_rows(REGISTRATIONS_SHEET_ID, gid=REGISTRATIONS_GID)
    by_date: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"registered": set(), "attended": set(), "application": set()})
    for row in rows:
        email = (row.get("Email Address") or "").strip().lower()
        webinar_date = parse_webinar_date(row.get("Webinar Date", ""))
        if not email or not webinar_date:
            continue
        by_date[webinar_date]["registered"].add(email)

        if (row.get("Attended Webinar") or "").strip():
            by_date[webinar_date]["attended"].add(email)

        cbe = (row.get("Call Booked Event") or "").strip().lower()
        if "application" in cbe:
            by_date[webinar_date]["application"].add(email)

    return by_date


def build_registrations() -> dict:
    by_date = _webinar_metrics_by_date()
    runs = []
    for date, metrics in sorted(by_date.items()):
        dt = datetime.strptime(date, "%Y-%m-%d")
        runs.append({
            "date": date,
            "label": dt.strftime("%d %b %Y"),
            "registered": len(metrics["registered"]),
        })
    runs.sort(key=lambda r: r["date"], reverse=True)

    return {
        "meta": {
            "generatedAt": datetime.utcnow().isoformat() + "Z",
            "source": "Google Sheet 'Heart Smart Webinar Lead Tracker' (gid-fetched tab, direct pull) — unique emails (Column C) per Webinar Date (Column F)",
            "dataWindow": f"{runs[-1]['date']} to {runs[0]['date']}" if runs else "no data",
            "totalRegistrations": sum(r["registered"] for r in runs),
        },
        "masterclassRegistrations": runs,
    }


def build_attendance() -> dict:
    by_date = _webinar_metrics_by_date()
    rows_out = []
    for date, metrics in sorted(by_date.items()):
        rows_out.append({"date": date, "attended": len(metrics["attended"])})
    rows_out.sort(key=lambda r: r["date"], reverse=True)

    return {
        "meta": {
            "generatedAt": datetime.utcnow().isoformat() + "Z",
            "source": "Google Sheet 'Heart Smart Webinar Lead Tracker' (Column P 'Attended Webinar') — unique emails per Webinar Date",
            "dataWindow": f"{rows_out[-1]['date']} to {rows_out[0]['date']}" if rows_out else "no data",
            "totalAttendees": sum(r["attended"] for r in rows_out),
        },
        "masterclassAttendance": rows_out,
    }


def build_applications() -> dict:
    by_date = _webinar_metrics_by_date()
    rows_out = []
    for date, metrics in sorted(by_date.items()):
        rows_out.append({"date": date, "applications": len(metrics["application"])})
    rows_out.sort(key=lambda r: r["date"], reverse=True)

    return {
        "meta": {
            "generatedAt": datetime.utcnow().isoformat() + "Z",
            "source": "Google Sheet 'Heart Smart Webinar Lead Tracker' (Column K 'Call Booked Event' contains 'application', no Booking Status condition — see fetch_sheets.py docstring) — unique emails per Webinar Date",
            "dataWindow": f"{rows_out[-1]['date']} to {rows_out[0]['date']}" if rows_out else "no data",
            "totalApplications": sum(r["applications"] for r in rows_out),
        },
        "applications": rows_out,
    }


MODE_VALUES = {"Stripe", "Finance", "EFT"}


def parse_amount(raw: str) -> float | None:
    cleaned = re.sub(r"[^0-9.\-]", "", raw or "")
    if not cleaned:
        return None
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return None


def parse_transaction_date(raw: str) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%b-%d-%Y", "%Y-%m-%d", "%d-%b-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


ATTRIBUTION_CACHE_PATH = REPO_ROOT / "sync" / "attribution_cache.json"


def _load_attribution_cache() -> dict[str, str]:
    if ATTRIBUTION_CACHE_PATH.exists():
        return json.loads(ATTRIBUTION_CACHE_PATH.read_text())
    return {}


def build_transactions() -> list[dict]:
    """Raw CONSOLIDATED rows, source-attributed via sync/attribution_cache.json
    (GHL attributionSource lookup, see classify_attribution.py) rather than
    the sheet's own 'Attribution' column — that column is essentially unused
    here (3 of 1,861 rows filled in when checked 2026-09-16), unlike FA's
    sheet. Per user decision 2026-09-16. Every row with a valid amount and a
    classified email is included; no closer-assigned filter. Rows whose email
    hasn't been classified yet (classify_attribution.py backlog) are skipped
    rather than guessed — re-run this after the backlog completes."""
    rows = fetch_csv_rows(REVENUE_SHEET_ID, tab=REVENUE_TAB)
    cache = _load_attribution_cache()
    out = []
    skipped_unclassified = 0
    for row in rows:
        date = parse_transaction_date(row.get("Date", ""))
        amount = parse_amount(row.get("Amount", ""))
        email = (row.get("Email") or "").strip().lower()
        if not date or amount is None or amount == 0 or not email:
            continue
        attribution = cache.get(email)
        if not attribution:
            skipped_unclassified += 1
            continue
        out.append({
            "date": date,
            "name": (row.get("Name") or "").strip(),
            "email": email,
            "product": (row.get("Product") or "").strip(),
            "amount": amount,
            "closer": (row.get("Closer") or "").strip(),
            "mode": (row.get("Mode") or "").strip() if row.get("Mode") in MODE_VALUES else "",
            "source": attribution,
        })
    if skipped_unclassified:
        print(f"Skipped {skipped_unclassified} rows with unclassified emails (not yet in attribution_cache.json)", file=sys.stderr)
    return out


def main():
    valid = ("registrations", "attendance", "applications", "transactions")
    if len(sys.argv) < 2 or sys.argv[1] not in valid:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "registrations":
        print(json.dumps(build_registrations(), indent=2))
    elif cmd == "attendance":
        print(json.dumps(build_attendance(), indent=2))
    elif cmd == "applications":
        print(json.dumps(build_applications(), indent=2))
    elif cmd == "transactions":
        print(json.dumps(build_transactions(), indent=2))


if __name__ == "__main__":
    main()
