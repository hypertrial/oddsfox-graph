# Packaged benchmark

Place the independently reviewed and adjudicated v0.4 benchmark at
`v0.4.0.parquet` before release. Discovery automatically selects it only when
its recorded source SHA-256 matches the supplied catalog. The benchmark is
intentionally absent until genuine human labels and notes pass
`benchmark-compile`; it must never be generated from model predictions.
