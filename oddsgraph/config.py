"""Pipeline configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    """Runtime settings for the inference pipeline."""

    repo_root: Path = Path(__file__).resolve().parent.parent
    data_dir: Path = repo_root / "data"
    build_dir: Path = repo_root / "build"
    models_dir: Path = repo_root / "models"
    fragments_dir: Path = build_dir / "fragments"
    failed_fragments_dir: Path = build_dir / "fragments" / "_failed"

    input_glob: str = "data/*market_hourly_odds*.parquet"
    fallback_glob: str = "polymarket_wc2026_market_hourly_odds_*.parquet"
    semantic_markets_path: Path = build_dir / "semantic_markets.parquet"
    nodes_path: Path = build_dir / "nodes.parquet"
    edges_path: Path = build_dir / "edges.parquet"
    rejected_edges_path: Path = build_dir / "rejected_edges.parquet"
    ontology_path: Path = build_dir / "ontology.json"
    inference_report_path: Path = build_dir / "inference_report.json"

    model_path: Path = models_dir / "qwen3-4b-q4_k_m.gguf"
    n_ctx: int = 12288
    n_gpu_layers: int = -1
    max_tokens: int = 4096
    temperature: float = 0.1  # conservative for structured extraction
    max_retries: int = 2
    chunk_token_budget: int = 7000
    chunk_output_token_budget: int = 4096
    max_markets_per_chunk: int = 24
    chunk_context_safety_margin: int = 64
    max_text_field_chars: int = 500
    flash_attn: bool = True
    n_batch: int = 1024
    n_ubatch: int = 1024
    kv_cache_type_k: str | None = None
    kv_cache_type_v: str | None = None
    llm_backend: str = "inprocess"
    server_base_url: str = "http://127.0.0.1:8080"
    server_request_timeout: float = 120.0
    llm_concurrency: int = 4
    fuzzy_threshold: int = 92
    minimum_confidence: float = 0.0
    resume: bool = True
    limit_events: int | None = None
    event_ids: list[str] = field(default_factory=list)

    def configure_build_dir(self, build_dir: Path) -> None:
        self.build_dir = build_dir
        self.fragments_dir = build_dir / "fragments"
        self.failed_fragments_dir = build_dir / "fragments" / "_failed"
        self.semantic_markets_path = build_dir / "semantic_markets.parquet"
        self.nodes_path = build_dir / "nodes.parquet"
        self.edges_path = build_dir / "edges.parquet"
        self.rejected_edges_path = build_dir / "rejected_edges.parquet"
        self.ontology_path = build_dir / "ontology.json"
        self.inference_report_path = build_dir / "inference_report.json"

    def ensure_dirs(self) -> None:
        self.build_dir.mkdir(parents=True, exist_ok=True)
        self.fragments_dir.mkdir(parents=True, exist_ok=True)
        self.failed_fragments_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)

    def resolve_input_glob(self) -> str:
        data_glob = self.input_glob.replace("data/", "")
        if list((self.repo_root / "data").glob(data_glob)):
            return str(self.repo_root / self.input_glob)
        if list(self.repo_root.glob(self.fallback_glob)):
            return str(self.repo_root / self.fallback_glob)
        return str(self.repo_root / self.input_glob)
