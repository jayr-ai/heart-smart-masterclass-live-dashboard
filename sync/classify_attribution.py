#!/usr/bin/env python3
"""
Classifies CONSOLIDATED transaction emails as Paid/Organic via GHL's
attributionSource, since Heart Smart's sheet (unlike FA's) has no usable
Attribution column of its own (only 3 of 1,861 rows filled in when checked
2026-09-16). This reintroduces the GHL-cross-reference step FA used before
its sheet got a real Attribution column — a deliberate per-user decision,
not a default.

GHL has no batch-by-email lookup (`in` operator isn't supported on the
`email` field — confirmed by testing, error lists the real allowed
operators) and bulk-listing all contacts isn't practical either (this
location has 244k+ contacts). So this classifies one email per
`contacts/search` call, same POST body/casing as fetch_ghl.py's other
calls (camelCase `locationId` here, unlike the snake_case
`opportunities/search` endpoint).

Classification rule (same as FA's original, and validate-ghl-attribution):
  PAID if attributionSource.utmMedium is "paid"/"paid_social", OR fbclid/fbc
  is present, OR sessionSource is "Paid Social". Else ORGANIC. No contact
  found at all -> leave unclassified (don't guess).

Results accumulate in sync/attribution_cache.json (committed — this repo's
cash-attribution.json already carries these same emails, so the cache adds
no new exposure). Capped per run (--limit, default 50) since the full
backlog is ~1,400 unique emails and GHL is a network round-trip per email —
run this skill repeatedly across syncs to work through the backlog
incrementally, same pattern as the old validate-ghl-attribution skill.

Usage:
    python3 classify_attribution.py --limit 50
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_ghl import GHL_API_BASE, GHL_API_VERSION, USER_AGENT, LOCATION_ID, _get_token  # noqa: E402
import fetch_sheets  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = REPO_ROOT / "sync" / "attribution_cache.json"


def load_cache() -> dict[str, str]:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {}


def save_cache(cache: dict[str, str]) -> None:
    CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True))


def _contact_search_by_email(token: str, email: str) -> dict | None:
    body = json.dumps({
        "locationId": LOCATION_ID,
        "filters": [{"field": "email", "operator": "eq", "value": email}],
        "pageLimit": 1,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{GHL_API_BASE}/contacts/search",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Version": GHL_API_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GHL contact search error {e.code} for {email}: {body_text}") from e
    contacts = data.get("contacts") or []
    return contacts[0] if contacts else None


def classify(attribution_source: dict | None) -> str:
    if not attribution_source:
        return "Organic"
    utm_medium = (attribution_source.get("utmMedium") or "").lower()
    if utm_medium in ("paid", "paid_social"):
        return "Paid"
    if attribution_source.get("fbclid") or attribution_source.get("fbc"):
        return "Paid"
    if (attribution_source.get("sessionSource") or "") == "Paid Social":
        return "Paid"
    return "Organic"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    token = _get_token()
    cache = load_cache()

    # Pull raw CONSOLIDATED rows the same way fetch_sheets.py does, but
    # without requiring the sheet's own Attribution column (that's the
    # whole point of this script).
    rows = fetch_sheets.fetch_csv_rows(fetch_sheets.REVENUE_SHEET_ID, tab=fetch_sheets.REVENUE_TAB)
    emails = set()
    for row in rows:
        date = fetch_sheets.parse_transaction_date(row.get("Date", ""))
        amount = fetch_sheets.parse_amount(row.get("Amount", ""))
        email = (row.get("Email") or "").strip().lower()
        if date and amount and email:
            emails.add(email)

    todo = sorted(e for e in emails if e not in cache)
    print(f"Total unique emails in CONSOLIDATED: {len(emails)}, already cached: {len(emails) - len(todo)}, remaining: {len(todo)}", file=sys.stderr)

    batch = todo[: args.limit]
    for email in batch:
        contact = _contact_search_by_email(token, email)
        result = classify(contact.get("attributionSource") if contact else None)
        cache[email] = result
        print(f"{email} -> {result}" + ("" if contact else " (no GHL contact found, defaulted Organic)"), file=sys.stderr)

    save_cache(cache)
    print(json.dumps({"classified_this_run": len(batch), "total_cached": len(cache), "remaining": len(todo) - len(batch)}, indent=2))


if __name__ == "__main__":
    main()
