from __future__ import annotations

from typing import Any


def calibration(
    rows: list[dict[str, Any]],
) -> tuple[float | None, float | None]:
    if not rows:
        return None, None
    bins: list[list[dict[str, Any]]] = [[] for _ in range(10)]
    for row in rows:
        index = min(9, int(float(row["confidence"]) * 10))
        bins[index].append(row)
    ece = 0.0
    for bucket in bins:
        if not bucket:
            continue
        accuracy = sum(bool(row["correct"]) for row in bucket) / len(bucket)
        confidence = sum(float(row["confidence"]) for row in bucket) / len(bucket)
        ece += len(bucket) / len(rows) * abs(accuracy - confidence)
    brier = sum(
        (float(row["confidence"]) - float(bool(row["correct"]))) ** 2
        for row in rows
    ) / len(rows)
    return ece, brier


def precision(rows: list[dict[str, Any]]) -> float | None:
    return (
        sum(bool(row["correct"]) for row in rows) / len(rows)
        if rows
        else None
    )

def f1(precision_value: float, recall: float) -> float:
    return (
        2 * precision_value * recall / (precision_value + recall)
        if precision_value + recall
        else 0.0
    )
