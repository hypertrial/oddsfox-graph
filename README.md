# oddsgraph

Build logical graph artifacts from token-minute Polymarket odds.

```bash
python -m oddsgraph.cli build \
  --input wc2026_token_minutely_odds_20260702T070755Z.parquet \
  --out output/wc2026
```

Useful readers:

```bash
python -m oddsgraph.cli search --out output/wc2026 --query "Brazil win World Cup"
python -m oddsgraph.cli edges --out output/wc2026 --edge-type implies --top 50
python -m oddsgraph.cli violations --out output/wc2026 --top 50
python -m oddsgraph.cli condition --out output/wc2026 --a "<token or search text>" --b "<token or search text>"
```

Requires DuckDB: either the Python `duckdb` package or the `duckdb` CLI on `PATH`.
