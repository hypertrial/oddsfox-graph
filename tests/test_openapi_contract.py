from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from oddsfox_graph._explorer.service import create_schema_app


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_RESPONSES = {
    "/api/v1/compare": "CompareResult",
    "/api/v1/component-graph/{component_id}": "GraphView",
    "/api/v1/components": "GraphPage_ComponentSummary_",
    "/api/v1/components/{component_id}": "ComponentDetail",
    "/api/v1/coverage": "CoverageSummary",
    "/api/v1/diagnostics": "GraphPage_QuarantineSummary_",
    "/api/v1/edges/{proposal_id}": "Edge",
    "/api/v1/entity-search": "EntitySearchResult",
    "/api/v1/event-graph/{event_key}": "GraphView",
    "/api/v1/events": "GraphPage_EventSummary_",
    "/api/v1/events/{event_key}": "EventDetail",
    "/api/v1/explore": "ExploreHome",
    "/api/v1/highlights": "HumanHighlight",
    "/api/v1/markets/{market_id}": "MarketDetail",
    "/api/v1/meta": "ExplorerMetadata",
    "/api/v1/nodes/{node_id}": "NodeDetail",
    "/api/v1/overview": "GraphView",
    "/api/v1/prove": "Proof",
    "/api/v1/recording-plan": "RecordingPlan",
    "/api/v1/relationships/{proposal_id}": "RelationshipDetail",
    "/api/v1/search": "Node",
    "/api/v1/stages": "StageSummary",
    "/api/v1/stages/{stage_key}": "StageDetail",
    "/api/v1/subgraph": "GraphView",
    "/api/v1/teams": "GraphPage_TeamSummary_",
    "/api/v1/teams/{team_key}": "TeamDetail",
    "/api/v1/why-not": "Diagnostic",
}


def test_schema_app_exposes_named_non_loose_responses_for_every_route() -> None:
    document = create_schema_app().openapi()
    paths = document["paths"]
    schemas = document["components"]["schemas"]

    assert len(EXPECTED_RESPONSES) == 27
    assert set(paths) == set(EXPECTED_RESPONSES)

    operation_ids: set[str] = set()
    for path, expected_schema_name in EXPECTED_RESPONSES.items():
        assert set(paths[path]) == {"get"}
        operation = paths[path]["get"]
        operation_id = operation["operationId"]
        assert operation_id
        assert operation_id not in operation_ids
        operation_ids.add(operation_id)

        response_schema = operation["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        schema_name = _named_response_schema(response_schema)
        assert schema_name == expected_schema_name

        named_schema = schemas[schema_name]
        assert named_schema != {}
        assert named_schema.get("type") == "object"
        assert named_schema.get("properties")
        assert named_schema.get("additionalProperties") is not True


def test_openapi_export_is_byte_deterministic_across_hash_seeds(
    tmp_path: Path,
) -> None:
    outputs = [tmp_path / "first.json", tmp_path / "second.json"]
    for hash_seed, output in zip(("11", "29"), outputs, strict=True):
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "export_openapi.py"),
                "--output",
                str(output),
            ],
            cwd=ROOT,
            env={**os.environ, "PYTHONHASHSEED": hash_seed},
            check=True,
        )

    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    assert json.loads(outputs[0].read_text(encoding="utf-8")) == (
        create_schema_app().openapi()
    )


def _named_response_schema(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return _component_name(schema["$ref"])
    assert schema.get("type") == "array"
    items = schema.get("items")
    assert isinstance(items, dict)
    assert set(items) == {"$ref"}
    return _component_name(items["$ref"])


def _component_name(reference: object) -> str:
    assert isinstance(reference, str)
    prefix = "#/components/schemas/"
    assert reference.startswith(prefix)
    return reference.removeprefix(prefix)
