# Packaged benchmark

Place the independently reviewed, adjudicated, and partitioned v0.6 benchmark at
`v0.6.0.parquet` before release. Discovery automatically selects it only when
its recorded source SHA-256 matches the supplied catalog. The benchmark is
intentionally absent until genuine human labels and notes pass
`benchmark-compile`; it must never be generated from model predictions.
