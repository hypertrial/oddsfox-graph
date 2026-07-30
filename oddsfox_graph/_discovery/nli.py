from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from .contracts import DEFAULT_NLI_MODEL, DEFAULT_NLI_REVISION
from .inference import canonical_json_sha256


NLI_INFERENCE_VERSION = "bidirectional-nli-v1"


def nli_inference_fingerprint(model: str, revision: str) -> str:
    return canonical_json_sha256(
        {
            "task": NLI_INFERENCE_VERSION,
            "model": model,
            "revision": revision,
        }
    )


@dataclass(frozen=True)
class NliScores:
    entailment: float
    contradiction: float
    neutral: float


@dataclass(frozen=True)
class BidirectionalNliScores:
    a_to_b: NliScores
    b_to_a: NliScores


class NliScorer(Protocol):
    def score(
        self,
        pairs: list[tuple[str, str]],
        *,
        batch_size: int,
    ) -> list[NliScores]: ...


class ModernBertNliScorer:
    """Lazy, revision-pinned local NLI scorer."""

    def __init__(
        self,
        model: str = DEFAULT_NLI_MODEL,
        revision: str = DEFAULT_NLI_REVISION,
    ) -> None:
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise ImportError(
                "NLI scoring dependencies are missing; reinstall oddsfox-graph."
            ) from exc
        self._model = CrossEncoder(
            model,
            revision=revision,
            local_files_only=True,
        )
        config = getattr(getattr(self._model, "model", None), "config", None)
        raw_labels = getattr(config, "id2label", {})
        self._labels = {
            int(index): str(label).lower()
            for index, label in dict(raw_labels).items()
        }
        required = {"entailment", "contradiction", "neutral"}
        if not required.issubset(set(self._labels.values())):
            raise ValueError(
                "The NLI model must expose entailment, contradiction, and neutral labels"
            )

    def score(
        self,
        pairs: list[tuple[str, str]],
        *,
        batch_size: int,
    ) -> list[NliScores]:
        if not pairs:
            return []
        logits = np.asarray(
            self._model.predict(
                pairs,
                batch_size=batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
            ),
            dtype=np.float64,
        )
        if logits.ndim != 2 or logits.shape[0] != len(pairs):
            raise ValueError("The NLI model returned an invalid score shape")
        if not np.isfinite(logits).all():
            raise ValueError("The NLI model returned non-finite scores")
        shifted = logits - np.max(logits, axis=1, keepdims=True)
        exponentiated = np.exp(shifted)
        probabilities = exponentiated / np.sum(
            exponentiated,
            axis=1,
            keepdims=True,
        )
        label_indices = {
            label: index for index, label in self._labels.items()
        }
        return [
            NliScores(
                entailment=float(row[label_indices["entailment"]]),
                contradiction=float(row[label_indices["contradiction"]]),
                neutral=float(row[label_indices["neutral"]]),
            )
            for row in probabilities
        ]


def score_bidirectional(
    scorer: NliScorer,
    pairs: list[tuple[str, str]],
    *,
    batch_size: int = 32,
) -> list[BidirectionalNliScores]:
    expanded: list[tuple[str, str]] = []
    for first, second in pairs:
        expanded.extend(((first, second), (second, first)))
    scores = scorer.score(expanded, batch_size=batch_size)
    if len(scores) != len(pairs) * 2:
        raise ValueError("The NLI scorer omitted one or more directional results")
    return [
        BidirectionalNliScores(
            a_to_b=scores[index],
            b_to_a=scores[index + 1],
        )
        for index in range(0, len(scores), 2)
    ]


def scores_to_columns(scores: BidirectionalNliScores) -> dict[str, Any]:
    return {
        "nli_a_to_b_entailment": scores.a_to_b.entailment,
        "nli_a_to_b_contradiction": scores.a_to_b.contradiction,
        "nli_a_to_b_neutral": scores.a_to_b.neutral,
        "nli_b_to_a_entailment": scores.b_to_a.entailment,
        "nli_b_to_a_contradiction": scores.b_to_a.contradiction,
        "nli_b_to_a_neutral": scores.b_to_a.neutral,
    }
