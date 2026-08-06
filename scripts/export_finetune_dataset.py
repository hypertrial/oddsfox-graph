#!/usr/bin/env python3
"""Export a self-distillation fine-tune dataset from teacher fragments.

Reads semantic markets + fragment JSON (deterministic/LLM/verified), emits
JSONL pairs suitable for mlx_lm.lora:

  {"text": "<prompt>\\n<completion>"}
or chat-style messages depending on --format.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

from oddsgraph.prompts import build_event_prompt
from oddsgraph.reduce import load_semantic_markets
from oddsgraph.schema import CompactGraphFragment, GraphFragment, SemanticMarket


def _load_fragments(fragments_dir: Path) -> dict[str, GraphFragment]:
    out: dict[str, GraphFragment] = {}
    for path in sorted(fragments_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        if "__part" in path.name or "__chunk_manifest" in path.name:
            continue
        if path.name.endswith("__verified.json"):
            event_id = path.name[: -len("__verified.json")]
            out[event_id] = GraphFragment.model_validate_json(
                path.read_text(encoding="utf-8")
            )
            continue
        event_id = path.stem
        if event_id not in out:
            out[event_id] = GraphFragment.model_validate_json(
                path.read_text(encoding="utf-8")
            )
    return out


def _topology_only(fragment: GraphFragment) -> GraphFragment:
    allowed = {
        "COMPETITION",
        "STAGE",
        "GROUP",
        "ROUND",
        "MATCH",
        "TEAM",
    }
    nodes = [n for n in fragment.nodes if n.type.value in allowed]
    ids = {n.local_id for n in nodes}
    edges = [
        e
        for e in fragment.edges
        if e.source in ids and e.target in ids and e.type.value
        in {"PART_OF", "PARTICIPATES_IN", "QUALIFIES_FOR", "ADVANCES_TO"}
    ]
    return GraphFragment(nodes=nodes, edges=edges)


def _write_split(
    path: Path,
    rows: list[dict],
    fmt: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            if fmt == "text":
                payload = {
                    "text": row["prompt"] + "\n" + row["completion"],
                }
            else:
                payload = {
                    "messages": [
                        {"role": "user", "content": row["prompt"]},
                        {"role": "assistant", "content": row["completion"]},
                    ]
                }
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--markets",
        type=Path,
        default=Path("build/semantic_markets.parquet"),
    )
    parser.add_argument(
        "--fragments-dir",
        type=Path,
        default=Path("build/fragments"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("build/finetune"),
    )
    parser.add_argument("--val-ratio", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--format",
        choices=["text", "chat"],
        default="chat",
        help="mlx_lm.lora data format",
    )
    parser.add_argument(
        "--from-fixtures",
        action="store_true",
        help="Use tests/fixtures fragments + golden markets when build/ is empty",
    )
    args = parser.parse_args()

    markets_path = args.markets
    fragments_dir = args.fragments_dir
    if args.from_fixtures:
        markets_path = Path("tests/fixtures/golden_semantic_markets.parquet")
        fragments_dir = Path("tests/fixtures/fragments")

    if not markets_path.exists():
        raise SystemExit(f"Markets not found: {markets_path}")
    if not fragments_dir.exists():
        raise SystemExit(f"Fragments dir not found: {fragments_dir}")

    markets = load_semantic_markets(markets_path)
    by_event: dict[str, list[SemanticMarket]] = defaultdict(list)
    for market in markets:
        by_event[market.event_id].append(market)

    fragments = _load_fragments(fragments_dir)
    rows: list[dict] = []
    for event_id, fragment in fragments.items():
        event_markets = by_event.get(event_id)
        if not event_markets:
            continue
        topology = _topology_only(fragment)
        if not topology.nodes:
            continue
        prompt = build_event_prompt(event_id, event_markets[:8], few_shot_exemplars=[])
        compact = CompactGraphFragment.from_graph_fragment(topology)
        rows.append(
            {
                "event_id": event_id,
                "prompt": prompt,
                "completion": compact.model_dump_json(),
            }
        )

    if not rows:
        raise SystemExit("No training rows produced (need fragments + matching markets)")

    random.Random(args.seed).shuffle(rows)
    val_count = max(1, int(len(rows) * args.val_ratio)) if len(rows) > 1 else 0
    val_rows = rows[:val_count]
    train_rows = rows[val_count:] or rows

    train_path = args.output_dir / "train.jsonl"
    val_path = args.output_dir / "valid.jsonl"
    _write_split(train_path, train_rows, args.format)
    if val_rows:
        _write_split(val_path, val_rows, args.format)

    meta = {
        "train_rows": len(train_rows),
        "valid_rows": len(val_rows),
        "format": args.format,
        "train_path": str(train_path),
        "valid_path": str(val_path) if val_rows else None,
    }
    (args.output_dir / "dataset_meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
