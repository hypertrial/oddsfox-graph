# Automated qualification

Qualification derives all material from the canonical Polymarket catalog. It
uses no external semantic labels, external datasets, model-authored truth, or proprietary
outputs.

The generator selects 1,000 parse markets—200 each for sports, elections,
cryptocurrency, economic indicators, and date-based events—and creates 5,000
controlled pair cases: 500 for every publishable relation, 1,250 difficult
unrelated cases, and 1,250 deliberately under-specified uncertain cases. Truth
generators cover binary facts, normalized equivalence, numeric/time/stage
implication, co-possible events, polarity changes, missing scope, boundary changes,
and adversarial near misses. They are independent of production rule functions.
Selection and validation partitions are 60/40 and market-disjoint.

The automation profile chooses the lowest per-relation consensus threshold that
passes the selection precision target, then applies all gates to validation:
structured validity, ID coverage, authoritative conflicts, parse agreement,
field/numeric/date agreement, relation precision and recall, retrieval recall,
support, citations, empty assumptions, and seed stability.

The result is `AUTOMATION_VALIDATED` only if every gate passes. The profile is
bound to the generated case-set hash, both model manifests and runtimes, prompts,
request/response schemas, sampling settings, NLI, normalization, and generator.
The retrieval binding includes the embedding model/revision, retrieval version,
top-k, block size, and candidate ceiling; changing any of them requires a new
profile. Retrieval recall is measured by running the generated positive cases
through the production candidate stage.
Qualification failure writes diagnostics and blocks graph publication.
The sibling `<out>.qualification-failure` directory contains the failed profile,
report, exact cases, and both manifests for reproducible diagnosis.

These controlled metrics certify automated conformance, logical-case accuracy,
consistency, reproducibility, and performance. They do not independently measure
real-world semantic accuracy.
