from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .provenance import peak_rss_mb

_ZERO_USAGE = {
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
}


def _add_usage(target: dict[str, int], usage: dict[str, int]) -> None:
    for key in _ZERO_USAGE:
        target[key] += int(usage.get(key, 0))


@dataclass
class RunState:
    """Mutable request provenance collected before the manifest is frozen."""

    observed_parse_models: set[str] = field(default_factory=set)
    observed_classify_models: set[str] = field(default_factory=set)
    current_usage: dict[str, int] = field(default_factory=lambda: dict(_ZERO_USAGE))
    cached_origin_usage: dict[str, int] = field(
        default_factory=lambda: dict(_ZERO_USAGE)
    )
    current_usage_by_task: dict[str, dict[str, int]] = field(default_factory=dict)
    cached_usage_by_task: dict[str, dict[str, int]] = field(default_factory=dict)
    _cached_usage_scopes: set[str] = field(default_factory=set)

    def add_usage(self, usage: dict[str, int], task: str | None = None) -> None:
        _add_usage(self.current_usage, usage)
        if task:
            target = self.current_usage_by_task.setdefault(
                task,
                dict(_ZERO_USAGE),
            )
            _add_usage(target, usage)

    def add_cached_usage(
        self,
        usage: dict[str, int],
        scope: str | None,
        task: str | None = None,
    ) -> None:
        # Usage-free deterministic/profile entries do not contribute tokens.
        if scope is None:
            return
        if not any(int(usage.get(key, 0)) for key in _ZERO_USAGE):
            return
        if scope in self._cached_usage_scopes:
            return
        self._cached_usage_scopes.add(scope)
        _add_usage(self.cached_origin_usage, usage)
        if task:
            target = self.cached_usage_by_task.setdefault(
                task,
                dict(_ZERO_USAGE),
            )
            _add_usage(target, usage)

    def usage_manifest(self) -> dict[str, object]:
        accounted = {
            key: self.current_usage[key] + self.cached_origin_usage[key]
            for key in _ZERO_USAGE
        }
        tasks = {}
        for task in sorted(
            set(self.current_usage_by_task) | set(self.cached_usage_by_task)
        ):
            current = self.current_usage_by_task.get(task, _ZERO_USAGE)
            cached = self.cached_usage_by_task.get(task, _ZERO_USAGE)
            tasks[task] = {
                "current_request": dict(current),
                "cached_origin": dict(cached),
                "accounted_total": {
                    key: current[key] + cached[key] for key in _ZERO_USAGE
                },
            }
        return {
            **self.current_usage,
            "cached_origin": dict(self.cached_origin_usage),
            "accounted_total": accounted,
            "tasks": tasks,
        }


class StageRecorder:
    """Monotonic stage and total-runtime recorder."""

    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.timings: dict[str, float] = {}
        self.stage_metrics: dict[str, dict[str, float]] = {}
        self._last_peak_rss_mb = peak_rss_mb()

    def run(self, name: str, fn: Callable[[], Any]) -> Any:
        stage_started = time.perf_counter()
        value = fn()
        wall_seconds = round(time.perf_counter() - stage_started, 3)
        current_peak_rss = peak_rss_mb()
        self.timings[name] = wall_seconds
        self.stage_metrics[name] = {
            "wall_seconds": wall_seconds,
            "peak_rss_mb": current_peak_rss,
            "rss_high_water_delta_mb": round(
                max(0.0, current_peak_rss - self._last_peak_rss_mb),
                3,
            ),
        }
        self._last_peak_rss_mb = current_peak_rss
        return value

    def runtime_seconds(self) -> float:
        return round(time.perf_counter() - self.started, 3)
