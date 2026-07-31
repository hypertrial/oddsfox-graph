from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .provenance import canonical_json_sha256
from .versions import EXECUTION_PLAN_VERSION


EXECUTION_PLAN_COLUMNS = {
    "stage": "VARCHAR",
    "unit_type": "VARCHAR",
    "unit_id": "VARCHAR",
    "status": "VARCHAR",
    "dependency_ids": "VARCHAR[]",
    "invalidation_reasons": "VARCHAR[]",
    "input_fingerprint": "VARCHAR",
    "output_fingerprint": "VARCHAR",
    "plan_version": "VARCHAR",
}


@dataclass
class ExecutionPlan:
    incremental: bool
    _rows: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_rows(
        cls,
        *,
        incremental: bool,
        rows: list[dict[str, Any]],
    ) -> ExecutionPlan:
        plan = cls(incremental=incremental)
        plan._rows.extend(
            {key: value for key, value in row.items()}
            for row in rows
        )
        return plan

    def add(
        self,
        *,
        stage: str,
        unit_type: str,
        unit_id: str,
        status: str,
        dependency_ids: list[str] | None = None,
        invalidation_reasons: list[str] | None = None,
        input_fingerprint: str | None = None,
        output_fingerprint: str | None = None,
    ) -> None:
        if status not in {"reused", "recomputed", "removed", "required"}:
            raise ValueError(f"Unknown execution status: {status}")
        self._rows.append(
            {
                "stage": stage,
                "unit_type": unit_type,
                "unit_id": unit_id,
                "status": status,
                "dependency_ids": sorted(set(dependency_ids or [])),
                "invalidation_reasons": sorted(set(invalidation_reasons or [])),
                "input_fingerprint": input_fingerprint,
                "output_fingerprint": output_fingerprint,
                "plan_version": EXECUTION_PLAN_VERSION,
            }
        )

    def rows(self) -> list[dict[str, Any]]:
        return sorted(
            self._rows,
            key=lambda row: (
                str(row["stage"]),
                str(row["unit_type"]),
                str(row["unit_id"]),
                str(row["status"]),
            ),
        )

    def verify(self) -> tuple[bool, list[str]]:
        errors: list[str] = []
        seen: set[tuple[str, str, str]] = set()
        for row in self.rows():
            key = (
                str(row["stage"]),
                str(row["unit_type"]),
                str(row["unit_id"]),
            )
            if row.get("plan_version") != EXECUTION_PLAN_VERSION:
                errors.append(
                    "execution unit has an incompatible plan version: "
                    + "/".join(key)
                )
            if key in seen:
                errors.append("duplicate execution unit: " + "/".join(key))
            seen.add(key)
            if row["status"] == "reused":
                if not row["input_fingerprint"]:
                    errors.append("reused unit has no baseline fingerprint: " + "/".join(key))
                elif row["input_fingerprint"] != row["output_fingerprint"]:
                    errors.append("reused unit fingerprint changed: " + "/".join(key))
            if row["status"] == "removed" and row["output_fingerprint"] is not None:
                errors.append("removed unit has an output fingerprint: " + "/".join(key))
            if (
                row["stage"] == "candidate_blocks"
                and row["status"] == "recomputed"
                and row["input_fingerprint"] == row["output_fingerprint"]
            ):
                errors.append("unchanged candidate block was recomputed: " + "/".join(key))
        required_global = (
            "candidate_cap",
            "global_stage",
            "canonical_candidate_aggregation",
        )
        if required_global not in seen:
            errors.append(
                "execution plan is missing required global stage: "
                + "/".join(required_global)
            )
        return not errors, errors

    def manifest(self) -> dict[str, Any]:
        rows = self.rows()
        verified, errors = self.verify()
        counts = Counter(str(row["status"]) for row in rows)
        stage_counts: dict[str, dict[str, int]] = {}
        for row in rows:
            per_stage = stage_counts.setdefault(str(row["stage"]), {})
            status = str(row["status"])
            per_stage[status] = per_stage.get(status, 0) + 1
        return {
            "version": EXECUTION_PLAN_VERSION,
            "row_count": len(rows),
            "hash": canonical_json_sha256(rows),
            "status_counts": dict(sorted(counts.items())),
            "stage_counts": {
                stage: dict(sorted(values.items()))
                for stage, values in sorted(stage_counts.items())
            },
            "affected_only_verified": bool(self.incremental and verified),
            "verification_errors": errors,
        }
