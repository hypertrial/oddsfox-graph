# CLI Reference

Entry point:

```bash
python -m oddsfox_graph.cli <command> ...
```

## `build`

Build structural graph artifacts from an input parquet file.

| Flag | Required | Description |
|---|---|---|
| `--input` | yes | Source odds parquet path |
| `--out` | yes | Output directory |
| `--taxonomy` | no | Optional taxonomy JSON; defaults to bundled WC2026 taxonomy |

## `benchmark-summary`

Print runtime and count summary from `build_manifest.json`.

| Flag | Required | Description |
|---|---|---|
| `--out` | yes | Completed build directory |

## `nodes`

List nodes from `nodes.parquet`.

| Flag | Required | Description |
|---|---|---|
| `--out` | yes | Completed build directory |
| `--top` | no | Max rows (default 50) |

## `edges`

List accepted logic edges from `logic_edges.parquet`.

| Flag | Required | Description |
|---|---|---|
| `--out` | yes | Completed build directory |
| `--edge-type` | no | One of `complement`, `equivalent`, `implies`, `mutually_exclusive` |
| `--top` | no | Max rows (default 50) |

## `condition`

Show exact conditional rows for a resolved node pair.

| Flag | Required | Description |
|---|---|---|
| `--out` | yes | Completed build directory |
| `--a` | yes | Source node id or unique text |
| `--b` | yes | Destination node id or unique text |

## `explain`

Explain one node: identity, same-market siblings, logic edges, conditionals.

| Flag | Required | Description |
|---|---|---|
| `--out` | yes | Completed build directory |
| `--node` | yes | Node id or unique text |

## `explain-edge`

Explain one typed logic edge and related conditionals.

| Flag | Required | Description |
|---|---|---|
| `--out` | yes | Completed build directory |
| `--src` | yes | Source node id or unique text |
| `--dst` | yes | Destination node id or unique text |
| `--edge-type` | yes | One of `complement`, `equivalent`, `implies`, `mutually_exclusive` |

## `search`

Search nodes by id, question, proposition, or outcome label.

| Flag | Required | Description |
|---|---|---|
| `--out` | yes | Completed build directory |
| `--query` | yes | Search text |
| `--top` | no | Max rows (default 20) |
