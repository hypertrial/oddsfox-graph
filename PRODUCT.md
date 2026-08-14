# Product

## 1. Purpose

OddsFox Graph compiles prediction-market records into a validated logical
knowledge graph for analysis and downstream deterministic reasoning.

- Problem: Prediction-market listings describe related competitions, teams,
  matches, markets, and outcomes in inconsistent source-native text and
  structures.
- Target user: Prediction-market analysts, researchers, and data integrators.
- Core value: Converts market data into explicit entities, propositions, and
  relationships that can be queried and checked independently.
- Success looks like: A local run produces validated, reproducible node and edge
  artifacts with clear deterministic and inferred provenance.

---

## 2. Product Principles

Rules that guide product and engineering decisions.

1. Prefer deterministic extraction and curated rules before model inference.
2. Preserve provenance and confidence so consumers can distinguish facts,
   compiled logic, and inferred structure.
3. Operate locally and publish files, not a hosted graph service or bundled
   production data.

---

## 3. Users

### Primary Users

Analysts and researchers who need structured prediction-market entities and
logical relationships for WC2026 analysis.

### Secondary Users

Operators running the compiler, contributors improving ontology and entity
resolution, and downstream tools consuming Parquet artifacts.

---

## 4. User Outcomes

- Users can reduce source market data into normalized semantic market records.
- Users can compile competition, team, stage, match, market, outcome, and
  proposition relationships.
- Users can validate and query graph artifacts with deterministic and inference
  provenance intact.

---

## 5. Scope

### In Scope

- Semantic parsing, entity resolution, proposition compilation, and ontology.
- Deterministic WC2026 topology plus optional structured local inference for
  residual cases.
- Graph validation and Parquet/JSON artifact export.

### Out of Scope

- Source-data collection or warehouse ownership.
- Strategy selection, profitability claims, or order execution.
- A hosted database, public API, or managed inference service.

---

## 6. Core Capabilities

### Semantic Reduction

**Purpose:**
Normalize source market records into stable entities and claims.

**Responsibilities:**
- Parse market and outcome identity from documented source fields.
- Resolve aliases and retain unresolved or rejected evidence.

**Non-responsibilities:**
- Correcting or refreshing upstream market history.
- Assigning trading value to a market.

### Logical Compilation

**Purpose:**
Build explicit topology and implication relationships.

**Responsibilities:**
- Apply deterministic match, group, stage, bracket, and logical rules.
- Use constrained local inference only for supported residual structures.

**Non-responsibilities:**
- Treating inferred relationships as deterministic facts.
- Computing execution fills or portfolio actions.

### Validation and Export

**Purpose:**
Make compiled graph results portable and independently inspectable.

**Responsibilities:**
- Enforce node, edge, endpoint, ontology, and provenance invariants.
- Export documented nodes, edges, histories, rejections, and reports.

**Non-responsibilities:**
- Hosting artifacts for users.
- Guaranteeing downstream suitability beyond the documented contracts.

---

## 7. Product Model

Core domain concepts and their relationships.

```text
Competition
 ├── Team
 ├── Stage
 └── Match
      └── Market
           └── Outcome
                └── Proposition
                     ├── Structural Relationship
                     └── Logical Implication
```
