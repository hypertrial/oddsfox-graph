"""Tests for explorer canvas projection cache helpers."""

from __future__ import annotations

from oddsgraph.explorer.canvas_actions import (
    projected_frame_cache_stats,
    reset_projected_frame_cache,
)


def test_projected_frame_cache_reset_clears_stats() -> None:
    reset_projected_frame_cache()
    assert projected_frame_cache_stats() == {"project_count": 0}
