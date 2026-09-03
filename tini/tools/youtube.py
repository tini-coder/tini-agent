"""Read-only YouTube channel and Studio analytics access."""

from __future__ import annotations

import datetime as _dt
import os
from collections.abc import Mapping

from tini.tools.registry import Tool

_SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
_INSTALL_HINT = "YouTube support is not installed; run: pip install -e '.[youtube]'"
_SETUP_HINT = "Set TINI_YOUTUBE=1 and YOUTUBE_ACCESS_TOKEN in .env, then restart Tini."


def _credentials(values: Mapping[str, str]):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise RuntimeError(_INSTALL_HINT) from exc

    token = values.get("YOUTUBE_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError(_SETUP_HINT)
    credentials = Credentials(
        token=token,
        refresh_token=values.get("YOUTUBE_REFRESH_TOKEN", "").strip() or None,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=values.get("YOUTUBE_CLIENT_ID", "").strip() or None,
        client_secret=values.get("YOUTUBE_CLIENT_SECRET", "").strip() or None,
        scopes=_SCOPES,
    )
    if credentials.expired and credentials.refresh_token:
        if not credentials.client_id or not credentials.client_secret:
            raise RuntimeError("YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET are required to refresh the token")
        credentials.refresh(Request())
    if not credentials.valid:
        raise RuntimeError("YouTube OAuth token is invalid or expired; reconnect YouTube")
    return credentials


def _service(values: Mapping[str, str]):
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(_INSTALL_HINT) from exc
    return build("youtube", "v3", credentials=_credentials(values), cache_discovery=False)


def _analytics_service(values: Mapping[str, str]):
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(_INSTALL_HINT) from exc
    return build("youtubeAnalytics", "v2", credentials=_credentials(values), cache_discovery=False)


def _channel_id(values: Mapping[str, str], service) -> str:
    configured = values.get("YOUTUBE_CHANNEL_ID", "").strip()
    if configured:
        return configured
    response = service.channels().list(part="id", mine=True).execute()
    items = response.get("items", [])
    if not items:
        raise RuntimeError("No YouTube channel is available for this Google account")
    return items[0]["id"]


def probe_youtube(values: Mapping[str, str]) -> None:
    service = _service(values)
    channel_id = _channel_id(values, service)
    service.channels().list(part="id,statistics", id=channel_id).execute()
    _analytics_service(values).reports().query(
        ids=f"channel=={channel_id}",
        startDate=(_dt.date.today() - _dt.timedelta(days=7)).isoformat(),
        endDate=_dt.date.today().isoformat(),
        metrics="views",
    ).execute()


def _report(values: Mapping[str, str], start: str, end: str, channel_id: str = "") -> str:
    service = _service(values)
    channel_id = channel_id or _channel_id(values, service)
    channel = service.channels().list(part="snippet,statistics", id=channel_id).execute()
    items = channel.get("items", [])
    if not items:
        return f"YouTube channel not found: {channel_id}"
    info = items[0]
    stats = info.get("statistics", {})
    analytics = _analytics_service(values).reports().query(
        ids=f"channel=={channel_id}",
        startDate=start,
        endDate=end,
        metrics="views,estimatedMinutesWatched,averageViewDuration,subscribersGained,subscribersLost",
        dimensions="day",
        sort="day",
    ).execute()
    headers = [item["name"] for item in analytics.get("columnHeaders", [])]
    rows = [dict(zip(headers, row, strict=False)) for row in analytics.get("rows", [])]
    lines = [
        f"YouTube channel: {info.get('snippet', {}).get('title', '(untitled)')} ({channel_id})",
        f"All-time public totals: views={stats.get('viewCount', '?')}, subscribers={stats.get('subscriberCount', '?')}, videos={stats.get('videoCount', '?')}",
        f"Analytics period: {start} to {end}",
    ]
    if rows:
        lines.append("Daily analytics:")
        lines.extend(" | ".join(f"{key}={row.get(key, '?')}" for key in headers) for row in rows)
    else:
        lines.append("No analytics rows were returned for this period.")
    return "\n".join(lines)


def make_tool() -> Tool:
    def run(start: str = "", end: str = "", channel_id: str = "") -> str:
        today = _dt.date.today()
        end_date = _dt.date.fromisoformat(end[:10]) if end else today
        start_date = _dt.date.fromisoformat(start[:10]) if start else end_date - _dt.timedelta(days=28)
        if start_date > end_date:
            return "Error: start must be on or before end"
        try:
            return _report(os.environ, start_date.isoformat(), end_date.isoformat(), channel_id)
        except Exception as exc:
            return f"YouTube unavailable ({type(exc).__name__}: {str(exc)[:160]})"

    return Tool(
        name="get_youtube_analytics",
        description=(
            "Read the connected YouTube channel's public totals and YouTube Studio daily analytics. "
            "Use for channel performance analysis. Dates are ISO; default is the last 28 days. "
            "Read-only: this tool cannot upload, edit, or delete videos."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "first day, YYYY-MM-DD"},
                "end": {"type": "string", "description": "last day, YYYY-MM-DD"},
                "channel_id": {"type": "string", "description": "optional channel ID override"},
            },
            "required": [],
        },
        fn=run,
    )