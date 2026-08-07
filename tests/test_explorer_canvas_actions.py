"""Tests for explorer canvas projection cache helpers."""

from __future__ import annotations

from oddsgraph.explorer import canvas_actions
from oddsgraph.explorer.canvas_actions import (
    _project_elements,
    projected_frame_cache_stats,
    reset_projected_frame_cache,
)


def test_projected_frame_cache_reset_clears_stats() -> None:
    reset_projected_frame_cache()
    assert projected_frame_cache_stats() == {"project_count": 0}


def test_projected_frame_cache_reuses_previous_hour(monkeypatch) -> None:
    reset_projected_frame_cache()

    class _Slice:
        def to_elements(self) -> list[dict]:
            return [{"data": {"id": "m1", "type": "MATCH"}, "classes": "MATCH"}]

    hours_seen: list[int | None] = []

    def fake_apply(elements, hour_epoch, *, stage_odds=None, previous_elements=None):
        hours_seen.append(hour_epoch)
        return [
            {
                "data": {
                    "id": "m1",
                    "type": "MATCH",
                    "home_team": "A",
                    "away_team": "B",
                    "current_home_prob": 0.4 if hour_epoch == 100 else 0.55,
                },
                "classes": "MATCH",
            }
        ]

    monkeypatch.setattr(canvas_actions, "bracket_elements", lambda _s: _Slice())
    monkeypatch.setattr(canvas_actions, "stage_odds_by_team", lambda _s: {})
    monkeypatch.setattr(canvas_actions, "apply_time_slice", fake_apply)
    monkeypatch.setattr(canvas_actions, "_artifact_token", lambda _s: ("tok",))

    class _Settings:
        pass

    settings = _Settings()
    _project_elements(settings, 100)  # seed cache at hour 100
    assert projected_frame_cache_stats()["project_count"] == 1
    assert hours_seen == [100]

    _project_elements(settings, 200, previous_hour_epoch=100)
    # Cache hit for previous hour: only the current hour is projected.
    assert projected_frame_cache_stats()["project_count"] == 2
    assert hours_seen == [100, 200]
