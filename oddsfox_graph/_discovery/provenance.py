from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from .inference import ComputeProfile


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json_sha256(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return text_sha256(raw)


def atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
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


def peak_rss_mb() -> float:
    try:
        import resource

        value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (ImportError, ValueError):  # pragma: no cover - platform guard
        return 0.0
    denominator = 1024 * 1024 if sys.platform == "darwin" else 1024
    return round(value / denominator, 3)


def compute_accounting(
    profile: ComputeProfile,
    *,
    profile_hash: str,
    timings: Mapping[str, Any],
    usage: Mapping[str, Any],
    peak_rss_mb: object,
) -> dict[str, Any]:
    seconds = sum(
        float(timings.get(stage, 0.0) or 0.0)
        for stage in ("parse_propositions", "score_nli", "classify_pairs")
    )
    hours = seconds / 3600.0
    energy = (
        profile.load_watts * hours / 1000.0
        if profile.load_watts is not None
        else None
    )
    electricity = (
        energy * profile.electricity_usd_per_kwh
        if energy is not None and profile.electricity_usd_per_kwh is not None
        else None
    )
    input_tokens = int(usage.get("input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    total_tokens = int(
        usage.get("total_tokens", input_tokens + output_tokens)
        or input_tokens + output_tokens
    )
    hardware_cost = hours * profile.hardware_hour_usd
    return {
        "profile_hash": profile_hash,
        "currency": profile.currency,
        "model_stage_seconds": seconds,
        "model_stage_hours": hours,
        "estimated_energy_kwh": energy,
        "hardware_cost": hardware_cost,
        "electricity_cost": electricity,
        "total_compute_cost": hardware_cost + (electricity or 0.0),
        "peak_rss_mb": peak_rss_mb,
        "current_request_input_tokens": input_tokens,
        "current_request_output_tokens": output_tokens,
        "current_request_tokens": total_tokens,
        "tokens_per_second": total_tokens / seconds if seconds > 0 else None,
    }
