"""Shared Typer option aliases for infer/run commands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Optional

import typer

LLM_BACKENDS = ("inprocess", "server", "mlx")
LlmBackend = Literal["inprocess", "server", "mlx"]

ModelPathOpt = Annotated[
    Optional[Path],
    typer.Option(help="Path to GGUF model file"),
]
MlxModelPathOpt = Annotated[
    Optional[Path],
    typer.Option(help="Path to MLX model directory"),
]
LimitEventsOpt = Annotated[
    Optional[int],
    typer.Option(help="Limit number of events to infer (>= 0)"),
]
EventIdOpt = Annotated[
    list[str],
    typer.Option(help="Specific event IDs to infer"),
]
ResumeOpt = Annotated[
    bool,
    typer.Option(help="Skip events with existing fragments/verified artifacts"),
]
LlmBackendOpt = Annotated[
    Optional[LlmBackend],
    typer.Option(
        help="LLM backend: inprocess, server, or mlx",
        metavar="BACKEND",
    ),
]
ServerUrlOpt = Annotated[
    Optional[str],
    typer.Option(help="Base URL for llama-server when llm-backend=server"),
]
ConcurrencyOpt = Annotated[
    Optional[int],
    typer.Option(help="Concurrent LLM requests (server backend only)"),
]
DeterministicTopologyOpt = Annotated[
    Optional[bool],
    typer.Option(
        "--deterministic-topology/--no-deterministic-topology",
        help="Extract TEAM/MATCH/GROUP/STAGE topology without LLM when possible",
    ),
]
VerifyDeterministicOpt = Annotated[
    Optional[bool],
    typer.Option(
        "--verify-deterministic/--no-verify-deterministic",
        help="LLM confirm/patch pass over deterministic topology (default: off)",
    ),
]
FewShotOpt = Annotated[
    Optional[bool],
    typer.Option(
        "--few-shot/--no-few-shot",
        help="Include rapidfuzz-ranked few-shot exemplars in residual prompts",
    ),
]
ChunkTokenBudgetOpt = Annotated[
    Optional[int],
    typer.Option(help="Approx input-token budget per residual chunk"),
]
ChunkOutputTokenBudgetOpt = Annotated[
    Optional[int],
    typer.Option(help="Approx output-token budget per residual chunk"),
]
MaxMarketsPerChunkOpt = Annotated[
    Optional[int],
    typer.Option(help="Hard cap on markets included in one residual chunk"),
]
