"""Deterministic coverage for the read-only YouTube tool."""

from __future__ import annotations

from types import SimpleNamespace

from tini.tools import youtube


def test_tool_rejects_reversed_date_range(monkeypatch):
    monkeypatch.setenv("YOUTUBE_ACCESS_TOKEN", "token")
    tool = youtube.make_tool()

    result = tool.fn(start="2026-08-20", end="2026-08-01")

    assert result == "Error: start must be on or before end"


def test_tool_passes_env_credentials_and_normalized_dates(monkeypatch):
    captured = {}
    monkeypatch.setenv("YOUTUBE_ACCESS_TOKEN", "token")
    monkeypatch.setattr(
        youtube,
        "_report",
        lambda values, start, end, channel_id: captured.update(
            values=values, start=start, end=end, channel_id=channel_id
        ) or "report",
    )
    tool = youtube.make_tool()

    result = tool.fn(start="2026-08-01T12:00", end="2026-08-07T12:00", channel_id="UC-test")

    assert result == "report"
    assert captured["values"]["YOUTUBE_ACCESS_TOKEN"] == "token"
    assert captured["start"] == "2026-08-01"
    assert captured["end"] == "2026-08-07"
    assert captured["channel_id"] == "UC-test"


def test_report_formats_channel_totals_and_daily_metrics(monkeypatch):
    class FakeChannels:
        def list(self, **kwargs):
            return SimpleNamespace(execute=lambda: {
                "items": [{
                    "id": "UC-test",
                    "snippet": {"title": "Tini Channel"},
                    "statistics": {"viewCount": "10", "subscriberCount": "2", "videoCount": "1"},
                }]
            })

    class FakeReports:
        def query(self, **kwargs):
            return SimpleNamespace(execute=lambda: {
                "columnHeaders": [{"name": "day"}, {"name": "views"}],
                "rows": [["2026-08-01", "7"]],
            })

    monkeypatch.setattr(youtube, "_service", lambda values: SimpleNamespace(channels=lambda: FakeChannels()))
    monkeypatch.setattr(youtube, "_analytics_service", lambda values: SimpleNamespace(reports=lambda: FakeReports()))

    result = youtube._report({}, "2026-08-01", "2026-08-07", "UC-test")

    assert "Tini Channel (UC-test)" in result
    assert "views=10" in result
    assert "day=2026-08-01 | views=7" in result