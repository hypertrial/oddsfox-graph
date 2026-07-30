from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Literal


CACHE_ENTRY_VERSION = 2
CacheState = Literal["success", "stable_failure", "transient_failure"]


def cache_entry(
    *,
    task: str,
    parsed: object,
    error: str | None,
    observed_model: str,
    usage: dict[str, int],
    usage_scope: str | None,
    state: CacheState,
    error_type: str | None = None,
    status_code: int | None = None,
) -> dict[str, Any]:
    return {
        "version": CACHE_ENTRY_VERSION,
        "task": task,
        "state": state,
        "retryable": state == "transient_failure",
        "parsed": parsed,
        "error": (
            {
                "message": error,
                "type": error_type,
                "status_code": status_code,
            }
            if error
            else None
        ),
        "observed_model": observed_model,
        "usage": {
            "input_tokens": int(usage.get("input_tokens", 0)),
            "output_tokens": int(usage.get("output_tokens", 0)),
            "total_tokens": int(usage.get("total_tokens", 0)),
        },
        "usage_scope": usage_scope,
        "usage_accounting": "batch_total",
    }


def cache_error(entry: dict[str, Any]) -> str | None:
    error = entry.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        return str(message) if message else None
    if error is None:
        return None
    return str(error)


class JsonCache:
    """Content-addressed cache with explicit online/offline failure semantics."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0
        self.writes = 0
        self.success_hits = 0
        self.stable_failure_hits = 0
        self.transient_failure_hits = 0
        self.transient_retries = 0
        self.legacy_hits = 0

    @staticmethod
    def key(
        task: str,
        model: str,
        prompt_version: str,
        prompt_hash: str,
        schema_hash: str,
        payload: object,
    ) -> str:
        raw = json.dumps(
            {
                "task": task,
                "model": model,
                "reasoning_effort": "medium",
                "prompt_version": prompt_version,
                "prompt_hash": prompt_hash,
                "schema_hash": schema_hash,
                "payload": payload,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def usage_scope(task: str, payloads: list[dict[str, object]]) -> str:
        raw = json.dumps(
            {"task": task, "payloads": payloads},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str, *, offline: bool = False) -> dict[str, Any] | None:
        path = self.directory / f"{key}.json"
        if not path.exists():
            self.misses += 1
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid discovery cache entry {path}: {exc}") from exc
        entry = self._normalize_entry(raw)
        state = str(entry["state"])
        if state == "transient_failure" and not offline:
            self.misses += 1
            self.transient_retries += 1
            return None
        if entry.get("usage_accounting") == "legacy_unscoped":
            self.legacy_hits += 1
        self.hits += 1
        if state == "success":
            self.success_hits += 1
        elif state == "stable_failure":
            self.stable_failure_hits += 1
        else:
            self.transient_failure_hits += 1
        return entry

    def put(self, key: str, value: dict[str, Any]) -> None:
        path = self.directory / f"{key}.json"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{key}.",
            suffix=".tmp",
            dir=self.directory,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(value, stream, indent=2, sort_keys=True, default=str)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, path)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
        self.writes += 1

    def stats(self) -> dict[str, int]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "writes": self.writes,
            "success_hits": self.success_hits,
            "stable_failure_hits": self.stable_failure_hits,
            "transient_failure_hits": self.transient_failure_hits,
            "transient_retries": self.transient_retries,
            "legacy_hits": self.legacy_hits,
        }

    @staticmethod
    def _normalize_entry(raw: object) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("Discovery cache entry must be a JSON object")
        if raw.get("version") == CACHE_ENTRY_VERSION:
            state = raw.get("state")
            if state not in {"success", "stable_failure", "transient_failure"}:
                raise ValueError(f"Unsupported discovery cache state {state!r}")
            return dict(raw)

        # v1 stored both permanent and retryable failures as a plain error string.
        # Replay them offline, but always retry them online.
        error = raw.get("error")
        entry = cache_entry(
            task=str(raw.get("task") or "legacy"),
            parsed=raw.get("parsed"),
            error=str(error) if error else None,
            observed_model=str(raw.get("observed_model") or ""),
            usage=(
                dict(raw.get("usage"))
                if isinstance(raw.get("usage"), dict)
                else {}
            ),
            usage_scope=None,
            state="transient_failure" if error else "success",
            error_type="LegacyCacheError" if error else None,
        )
        entry["usage_accounting"] = "legacy_unscoped"
        return entry
