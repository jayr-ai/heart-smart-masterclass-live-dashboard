#!/usr/bin/env python3
"""
Pulls the GHL Webinar Pipeline funnel-stage snapshot straight from the
GHL REST API using a location-scoped Private Integration Token (PIT) — no
GHL MCP connector involved. Same mechanism as fa-masterclass-live-dashboard's
fetch_ghl.py, adapted for the Heart Smart Australia GHL location/pipeline.

Auth: `Authorization: Bearer <PIT>` + `Version: 2021-07-28` header. The
opportunities/search endpoint requires `location_id`/`pipeline_id`/
`pipeline_stage_id` as snake_case query params (camelCase 422s) — but the
opportunities/pipelines endpoint (used once, to fetch the stage list below)
wants camelCase `locationId` instead. Inconsistent across GHL endpoints,
confirmed by testing, not documentation. Cloudflare in front of the API
also blocks Python's default urllib user-agent outright (403
browser_signature_banned) — fixed with a curl-like User-Agent header.

Token lives in sync/.env (HS_GHL_PIT=...), git-ignored, never committed.
Load it with:
    set -a; source sync/.env; set +a

Usage:
    python3 fetch_ghl.py funnel-stages   # -> prints funnel-stages.json shape
    python3 fetch_ghl.py list-stages     # -> prints the pipeline's stage name->ID map (setup helper)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

GHL_API_BASE = "https://services.leadconnectorhq.com"
GHL_API_VERSION = "2021-07-28"
USER_AGENT = "curl/8.7.1"  # Cloudflare blocks the default urllib UA outright

LOCATION_ID = "9D1lM8MPCly9IoR5YCIa"
PIPELINE_ID = "fVjHZmcsCSemRfeoRBct"
PIPELINE_NAME = "Webinar Pipeline"

# Fetched live via `list-stages` on 2026-09-16 — re-run that if stages are
# ever added/renamed/reordered in GHL rather than hand-editing this dict.
STAGE_IDS: dict[str, str] = {
    "Optin": "8184bf17-80d0-4abb-a7bd-b11acaaae3e7",
    "Registered GA": "cd594f64-cb1d-4207-ac43-ed6610aff9c7",
    "VIP Upgrade": "92cee92a-0189-4724-a6b8-11c17838b754",
    "Downsell Buyer": "1c2e161e-e700-49c6-8145-433a42277e3a",
    "Replay Optin": "94acf15b-a7af-4d4d-9f2a-0fea4af0464c",
    "Purchased BG+ Diagnostic": "f0501c94-a6d2-46c3-a864-630a37c3ae9b",
    "Received Referral": "6755c59b-fa98-4cbb-aeb7-229d9a7879fc",
    "Received Results": "aa9d326d-4b75-4b9c-bd56-360ba443e211",
    "Appointment Booked": "16fc6098-720b-4640-a902-b802e446aa66",
    "Call Cancelled/Need to RS": "1fd7f98c-530d-4f19-8476-eb09212c14ea",
    "No-Show": "3b690c75-a92b-4175-9ffb-9abadc985c65",
    "Pending Sale": "450fbd93-d2d7-4299-be76-7a6971984c87",
    "Close Lost": "0ed40a14-4841-4491-9c51-e9f6a03c4792",
    "Close Won": "b1d06d29-6619-4a13-8a28-3234e4a144ca",
}


def _get_token() -> str:
    token = os.environ.get("HS_GHL_PIT")
    if not token:
        print(
            "HS_GHL_PIT is not set. Load it first:\n"
            "    set -a; source sync/.env; set +a",
            file=sys.stderr,
        )
        sys.exit(1)
    return token


def _request(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Version": GHL_API_VERSION,
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GHL API error {e.code} for {url}: {body}") from e


def list_stages() -> dict:
    """Setup/refresh helper — camelCase locationId here, unlike opportunities/search."""
    token = _get_token()
    params = urllib.parse.urlencode({"locationId": LOCATION_ID})
    data = _request(f"{GHL_API_BASE}/opportunities/pipelines?{params}", token)
    for p in data.get("pipelines", []):
        if p["id"] == PIPELINE_ID:
            return {s["name"]: s["id"] for s in p["stages"]}
    raise RuntimeError(f"Pipeline {PIPELINE_ID} not found in location {LOCATION_ID}")


def _stage_count(token: str, stage_id: str) -> int:
    params = urllib.parse.urlencode({
        "location_id": LOCATION_ID,
        "pipeline_id": PIPELINE_ID,
        "pipeline_stage_id": stage_id,
        "limit": "1",
    })
    data = _request(f"{GHL_API_BASE}/opportunities/search?{params}", token)
    return data["meta"]["total"]


def build_funnel_stages() -> dict:
    token = _get_token()
    counts = {name: _stage_count(token, stage_id) for name, stage_id in STAGE_IDS.items()}
    total = sum(counts.values())
    ordered = sorted(counts.items(), key=lambda kv: -kv[1])

    stages = [
        {
            "name": name,
            "count": count,
            "position": i,
            "winProbability": round(count / total * 100, 2) if total else 0,
        }
        for i, (name, count) in enumerate(ordered)
    ]

    return {
        "meta": {
            "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source": "GHL REST API direct (Private Integration Token, no MCP, no BigQuery)",
            "dataWindow": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "totalOpportunities": total,
            "pipelineId": PIPELINE_ID,
            "pipelineName": PIPELINE_NAME,
        },
        "stages": stages,
    }


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("funnel-stages", "list-stages"):
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "list-stages":
        print(json.dumps(list_stages(), indent=2))
    else:
        print(json.dumps(build_funnel_stages(), indent=2))


if __name__ == "__main__":
    main()
