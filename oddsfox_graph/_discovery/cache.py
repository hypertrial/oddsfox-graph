from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from .provenance import canonical_json_sha256, sha256_file
from .versions import CACHE_ENTRY_VERSION, CACHE_FILENAME, CACHE_FORMAT


CACHE_BATCH_SIZE = 256
CacheState = Literal["success", "stable_failure", "transient_failure"]
_CACHE_ENTRY_COLUMNS = (
    ("cache_key", "TEXT"),
    ("entry_version", "INTEGER"),
    ("task", "TEXT"),
    ("state", "TEXT"),
    ("parsed_json", "TEXT"),
    ("error_json", "TEXT"),
    ("observed_model", "TEXT"),
    ("input_tokens", "INTEGER"),
    ("output_tokens", "INTEGER"),
    ("total_tokens", "INTEGER"),
    ("usage_scope", "TEXT"),
)


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
        "usage_accounting": "request_total",
    }


def cache_error(entry: Mapping[str, Any]) -> str | None:
    error = entry.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        return str(message) if message else None
    if error is None:
        return None
    return str(error)


class InferenceCache:
    """Transactional content-addressed cache for local inference results."""

    def __init__(self, directory: Path, *, offline: bool = False) -> None:
        self.directory = directory.resolve()
        self.path = self.directory / CACHE_FILENAME
        self.offline = offline
        self.hits = 0
        self.misses = 0
        self.writes = 0
        self.success_hits = 0
        self.stable_failure_hits = 0
        self.transient_failure_hits = 0
        self.transient_retries = 0
        self.bulk_reads = 0
        self.bulk_writes = 0
        self.transactions = 0
        self._closed = False

        if not self.path.is_file():
            if self.directory.exists() and any(self.directory.iterdir()):
                raise ValueError(
                    "Incompatible discovery cache. Use an empty cache directory."
                )
            if offline:
                raise ValueError(
                    "Offline discovery cache is missing "
                    f"{CACHE_FILENAME}; rerun online with an empty cache directory"
                )
            self.directory.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(self.path, timeout=30.0)
            self._configure_writable()
            self._initialize()
        else:
            allowed = {
                CACHE_FILENAME,
                f"{CACHE_FILENAME}-wal",
                f"{CACHE_FILENAME}-shm",
            }
            if any(item.name not in allowed for item in self.directory.iterdir()):
                raise ValueError(
                    "Incompatible discovery cache. Use an empty cache directory."
                )
            if offline:
                uri = f"file:{self.path.as_posix()}?mode=ro"
                self._db = sqlite3.connect(uri, uri=True, timeout=30.0)
                self._db.execute("PRAGMA query_only = ON")
            else:
                self._db = sqlite3.connect(self.path, timeout=30.0)
                self._configure_writable()
            self._validate_schema()
        self._db.row_factory = sqlite3.Row

    @staticmethod
    def key(
        task: str,
        inference_fingerprint: str,
        prompt_version: str,
        prompt_hash: str,
        schema_hash: str,
        payload: object,
    ) -> str:
        return canonical_json_sha256(
            {
                "task": task,
                "cache_entry_version": CACHE_ENTRY_VERSION,
                "inference_fingerprint": inference_fingerprint,
                "prompt_version": prompt_version,
                "prompt_hash": prompt_hash,
                "schema_hash": schema_hash,
                "payload": payload,
            },
        )

    @staticmethod
    def usage_scope(task: str, payloads: list[dict[str, object]]) -> str:
        return canonical_json_sha256(
            {"task": task, "payloads": payloads},
        )

    def get(self, key: str, *, offline: bool | None = None) -> dict[str, Any] | None:
        return self.get_many((key,), offline=offline).get(key)

    def get_many(
        self,
        keys: Sequence[str],
        *,
        offline: bool | None = None,
    ) -> dict[str, dict[str, Any]]:
        if not keys:
            return {}
        effective_offline = self.offline if offline is None else offline
        unique = sorted(set(keys))
        found: dict[str, dict[str, Any]] = {}
        transient_misses = 0
        self.bulk_reads += 1
        for start in range(0, len(unique), CACHE_BATCH_SIZE):
            chunk = unique[start : start + CACHE_BATCH_SIZE]
            placeholders = ",".join("?" for _ in chunk)
            rows = self._db.execute(
                f"""
                SELECT cache_key, entry_version, task, state, parsed_json,
                       error_json, observed_model, input_tokens, output_tokens,
                       total_tokens, usage_scope
                FROM cache_entries
                WHERE cache_key IN ({placeholders})
                """,
                chunk,
            ).fetchall()
            for row in rows:
                entry = self._entry_from_row(row)
                state = str(entry["state"])
                if state == "transient_failure" and not effective_offline:
                    self.misses += 1
                    self.transient_retries += 1
                    transient_misses += 1
                    continue
                key = str(row["cache_key"])
                found[key] = entry
                self.hits += 1
                if state == "success":
                    self.success_hits += 1
                elif state == "stable_failure":
                    self.stable_failure_hits += 1
                else:
                    self.transient_failure_hits += 1
        self.misses += len(unique) - len(found) - transient_misses
        return found

    def contains_many(self, keys: Sequence[str]) -> set[str]:
        if not keys:
            return set()
        found: set[str] = set()
        unique = sorted(set(keys))
        self.bulk_reads += 1
        for start in range(0, len(unique), CACHE_BATCH_SIZE):
            chunk = unique[start : start + CACHE_BATCH_SIZE]
            placeholders = ",".join("?" for _ in chunk)
            found.update(
                str(row[0])
                for row in self._db.execute(
                    f"SELECT cache_key FROM cache_entries "
                    f"WHERE cache_key IN ({placeholders})",
                    chunk,
                ).fetchall()
            )
        return found

    def put(self, key: str, value: dict[str, Any]) -> None:
        self.put_many({key: value})

    def put_many(self, values: Mapping[str, dict[str, Any]]) -> None:
        if not values:
            return
        if self.offline:
            raise ValueError("Offline discovery cache is read-only")
        rows = [
            self._entry_row(key, values[key])
            for key in sorted(values)
        ]
        for start in range(0, len(rows), CACHE_BATCH_SIZE):
            chunk = rows[start : start + CACHE_BATCH_SIZE]
            with self._db:
                keys = [str(row[0]) for row in chunk]
                placeholders = ",".join("?" for _ in keys)
                existing_by_key = {
                    str(row["cache_key"]): self._entry_from_row(row)
                    for row in self._db.execute(
                        f"""
                        SELECT cache_key, entry_version, task, state,
                               parsed_json, error_json, observed_model,
                               input_tokens, output_tokens, total_tokens,
                               usage_scope
                        FROM cache_entries
                        WHERE cache_key IN ({placeholders})
                        """,
                        keys,
                    ).fetchall()
                }
                for row in chunk:
                    existing = existing_by_key.get(str(row[0]))
                    desired = self._normalize_entry(
                        values[str(row[0])]
                    )
                    if (
                        existing is not None
                        and existing["state"] != "transient_failure"
                        and existing != desired
                    ):
                        raise ValueError(
                            "Conflicting immutable discovery cache entry "
                            f"{row[0]}"
                        )
                self._db.executemany(
                    """
                    INSERT INTO cache_entries (
                        cache_key, entry_version, task, state, parsed_json,
                        error_json, observed_model, input_tokens, output_tokens,
                        total_tokens, usage_scope
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        entry_version = excluded.entry_version,
                        task = excluded.task,
                        state = excluded.state,
                        parsed_json = excluded.parsed_json,
                        error_json = excluded.error_json,
                        observed_model = excluded.observed_model,
                        input_tokens = excluded.input_tokens,
                        output_tokens = excluded.output_tokens,
                        total_tokens = excluded.total_tokens,
                        usage_scope = excluded.usage_scope
                    """,
                    chunk,
                )
            self.transactions += 1
        self.writes += len(rows)
        self.bulk_writes += 1

    def checkpoint(self) -> None:
        if self.offline:
            return
        self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def get_qualification_profile(self, key: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT profile_json FROM qualification_profiles WHERE profile_key = ?",
            [key],
        ).fetchone()
        if row is None:
            return None
        value = json.loads(str(row[0]))
        if not isinstance(value, dict):
            raise ValueError("Invalid cached automation profile")
        return {str(name): item for name, item in value.items()}

    def qualification_profile_ids(self) -> frozenset[str]:
        """Return validated profile IDs stored in this cache."""
        profile_ids: set[str] = set()
        for row in self._db.execute(
            "SELECT profile_json FROM qualification_profiles ORDER BY profile_key"
        ).fetchall():
            try:
                value = json.loads(str(row[0]))
            except json.JSONDecodeError as exc:
                raise ValueError("Invalid cached automation profile") from exc
            if not isinstance(value, dict) or not isinstance(
                value.get("profile_id"), str
            ):
                raise ValueError("Invalid cached automation profile")
            profile_ids.add(str(value["profile_id"]))
        return frozenset(profile_ids)

    def put_qualification_profile(self, key: str, profile: Mapping[str, Any]) -> None:
        if self.offline:
            raise ValueError("Offline discovery cache is read-only")
        payload = json.dumps(
            dict(profile),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        with self._db:
            existing = self._db.execute(
                "SELECT profile_json FROM qualification_profiles WHERE profile_key = ?",
                [key],
            ).fetchone()
            if existing is not None and str(existing[0]) != payload:
                raise ValueError("Conflicting immutable automation profile")
            self._db.execute(
                "INSERT OR IGNORE INTO qualification_profiles "
                "(profile_key, profile_json) VALUES (?, ?)",
                [key, payload],
            )
        self.transactions += 1

    def integrity_check(self) -> str:
        row = self._db.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row is not None else "missing"

    def stats(self) -> dict[str, int | str]:
        self.checkpoint()
        state_counts = {
            str(row[0]): int(row[1])
            for row in self._db.execute(
                "SELECT state, count(*) FROM cache_entries GROUP BY state"
            ).fetchall()
        }
        qualification_profiles = int(
            self._db.execute("SELECT count(*) FROM qualification_profiles").fetchone()[0]
        )
        storage_bytes = sum(
            candidate.stat().st_size
            for candidate in (
                self.path,
                Path(str(self.path) + "-wal"),
                Path(str(self.path) + "-shm"),
            )
            if candidate.is_file()
        )
        database_bytes = self.path.stat().st_size
        return {
            "format": CACHE_FORMAT,
            "database": self.path.name,
            "database_hash": sha256_file(self.path),
            "entry_count": sum(state_counts.values()),
            "file_bytes": database_bytes,
            "storage_bytes": storage_bytes,
            "integrity": self.integrity_check(),
            "hits": self.hits,
            "misses": self.misses,
            "writes": self.writes,
            "success_hits": self.success_hits,
            "stable_failure_hits": self.stable_failure_hits,
            "transient_failure_hits": self.transient_failure_hits,
            "transient_retries": self.transient_retries,
            "bulk_reads": self.bulk_reads,
            "bulk_writes": self.bulk_writes,
            "transactions": self.transactions,
            "success_entries": state_counts.get("success", 0),
            "stable_failure_entries": state_counts.get("stable_failure", 0),
            "transient_failure_entries": state_counts.get("transient_failure", 0),
            "qualification_profiles": qualification_profiles,
        }

    def close(self) -> None:
        if not self._closed:
            if not self.offline:
                self.checkpoint()
            self._db.close()
            self._closed = True

    def _configure_writable(self) -> None:
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute("PRAGMA synchronous = FULL")
        self._db.execute("PRAGMA busy_timeout = 30000")

    def _initialize(self) -> None:
        with self._db:
            self._db.execute(
                """
                CREATE TABLE cache_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            self._db.executemany(
                "INSERT INTO cache_metadata (key, value) VALUES (?, ?)",
                (
                    ("format", CACHE_FORMAT),
                    ("entry_version", str(CACHE_ENTRY_VERSION)),
                    ("lineage", "self-hosted-open-model"),
                ),
            )
            self._db.execute(
                """
                CREATE TABLE cache_entries (
                    cache_key TEXT PRIMARY KEY,
                    entry_version INTEGER NOT NULL,
                    task TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (
                        state IN (
                            'success',
                            'stable_failure',
                            'transient_failure'
                        )
                    ),
                    parsed_json TEXT,
                    error_json TEXT,
                    observed_model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    usage_scope TEXT
                )
                """
            )
            self._db.execute(
                "CREATE INDEX cache_entries_task_state "
                "ON cache_entries (task, state)"
            )
            self._db.execute(
                """
                CREATE TABLE qualification_profiles (
                    profile_key TEXT PRIMARY KEY,
                    profile_json TEXT NOT NULL
                )
                """
            )

    def _validate_schema(self) -> None:
        try:
            metadata = dict(
                self._db.execute(
                    "SELECT key, value FROM cache_metadata"
                ).fetchall()
            )
            columns = tuple(
                (str(row[1]), str(row[2]).upper())
                for row in self._db.execute(
                    "PRAGMA table_info('cache_entries')"
                ).fetchall()
            )
            incompatible_entries = int(
                self._db.execute(
                    "SELECT count(*) FROM cache_entries "
                    "WHERE entry_version != ?",
                    [CACHE_ENTRY_VERSION],
                ).fetchone()[0]
            )
            profile_columns = tuple(
                (str(row[1]), str(row[2]).upper())
                for row in self._db.execute(
                    "PRAGMA table_info('qualification_profiles')"
                ).fetchall()
            )
        except sqlite3.DatabaseError as exc:
            raise ValueError(
                "Incompatible discovery cache. Use an empty cache directory."
            ) from exc
        if metadata != {
            "format": CACHE_FORMAT,
            "entry_version": str(CACHE_ENTRY_VERSION),
            "lineage": "self-hosted-open-model",
        } or columns != _CACHE_ENTRY_COLUMNS or incompatible_entries or profile_columns != (
            ("profile_key", "TEXT"),
            ("profile_json", "TEXT"),
        ):
            raise ValueError(
                "Incompatible discovery cache. Use an empty cache directory."
            )
        if self.integrity_check() != "ok":
            raise ValueError("Invalid discovery cache database integrity")

    @staticmethod
    def _entry_row(key: str, raw: Mapping[str, Any]) -> tuple[object, ...]:
        entry = InferenceCache._normalize_entry(raw)
        usage = entry["usage"]
        assert isinstance(usage, dict)
        return (
            key,
            CACHE_ENTRY_VERSION,
            str(entry["task"]),
            str(entry["state"]),
            (
                json.dumps(
                    entry["parsed"],
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
                if entry["parsed"] is not None
                else None
            ),
            (
                json.dumps(
                    entry["error"],
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
                if entry["error"] is not None
                else None
            ),
            str(entry["observed_model"]),
            int(usage.get("input_tokens", 0)),
            int(usage.get("output_tokens", 0)),
            int(usage.get("total_tokens", 0)),
            entry.get("usage_scope"),
        )

    @staticmethod
    def _entry_from_row(row: sqlite3.Row) -> dict[str, Any]:
        raw = {
            "version": int(row["entry_version"]),
            "task": str(row["task"]),
            "state": str(row["state"]),
            "retryable": str(row["state"]) == "transient_failure",
            "parsed": (
                json.loads(str(row["parsed_json"]))
                if row["parsed_json"] is not None
                else None
            ),
            "error": (
                json.loads(str(row["error_json"]))
                if row["error_json"] is not None
                else None
            ),
            "observed_model": str(row["observed_model"]),
            "usage": {
                "input_tokens": int(row["input_tokens"]),
                "output_tokens": int(row["output_tokens"]),
                "total_tokens": int(row["total_tokens"]),
            },
            "usage_scope": row["usage_scope"],
            "usage_accounting": "request_total",
        }
        return InferenceCache._normalize_entry(raw)

    @staticmethod
    def _normalize_entry(raw: Mapping[str, Any]) -> dict[str, Any]:
        if raw.get("version") != CACHE_ENTRY_VERSION:
            raise ValueError(
                "Incompatible discovery cache. Use an empty cache directory."
            )
        state = raw.get("state")
        if state not in {"success", "stable_failure", "transient_failure"}:
            raise ValueError(f"Unsupported discovery cache state {state!r}")
        task = str(raw.get("task") or "")
        observed_model = str(raw.get("observed_model") or "")
        usage = raw.get("usage")
        if not task or not observed_model or not isinstance(usage, dict):
            raise ValueError("Invalid discovery cache entry")
        return dict(raw)

    def __enter__(self) -> InferenceCache:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - exception-path safety net
        try:
            self.close()
        except Exception:
            pass
