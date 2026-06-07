# ASIL-D Test Orchestrator Architecture v3.0

> **Revision notes:** This document supersedes v2.0. Major additions: **Bootstrap Mode** (full project graph build with multi-wave dispatch for new projects), Semantic Graph, Call Graph, Data Flow Graph (DFG), Risk Graph, Evidence Graph, enriched Coverage Graph, enriched Mutation Graph, Incremental Graph Update, Graph Versioning, Agent Memory Layer (Shared Knowledge Bus), and Prioritization Engine.
>
> **v3.0 patch (post-review fixes):** (1) Agent Memory Layer: thêm persistence spec — flush ra `agent_memory_snapshot.json` sau mỗi agent call để resume an toàn khi CI bị interrupted. (2) Agent Output Cache: cache key bổ sung `semanticHash` — tránh cache hit sai khi ASIL level thay đổi mà method body không đổi. (3) Bootstrap Mode Detector: kiểm tra existing JaCoCo/PIT reports trước khi force worst-case factors, tránh lãng phí wave dispatch khi migration từ hệ thống cũ. (4) Knowledge Graph Layer: khôi phục section riêng với technology backend spec, node/relationship types đầy đủ, và Cypher query interface.

---

## Objective

Build an AI-assisted verification framework for ISO 26262 ASIL D software testing.

Goals:

- Automated Unit Test Generation
- MC/DC Analysis (with Data Flow awareness)
- Coverage Gap Analysis (with Risk-weighted priority)
- Mutation Testing (with call chain propagation)
- Safety Review
- Traceability
- Evidence Generation

The system shall minimize LLM token consumption by:

1. Transforming source code into structured graphs before invoking AI agents.
2. Enriching graphs with semantic, risk, and call-chain context so agents need no raw source.
3. Computing graph deltas for incremental updates — only changed subgraphs are reprocessed.
4. Caching agent outputs keyed by graph hash for reuse across runs.
5. Using a shared Agent Memory Layer so no two agents analyze the same fact twice.
6. Ranking all analysis tasks through a Prioritization Engine before any LLM is invoked.
7. Versioning every graph snapshot for full ASIL-D audit compliance.

---

## High-Level Architecture

```
                    Source Code Repository
                               │
                               ▼
                    ┌──────────────────────┐
                    │   Mode Detector      │
                    └──────────────────────┘
                               │
              ┌────────────────┴────────────────┐
        BOOTSTRAP                          INCREMENTAL
    (no .graph/ exists)                 (git diff scope)
              │                                  │
              ▼                                  ▼
  ┌─────────────────────┐         ┌──────────────────────┐
  │ Full Project Parse  │         │  Incremental Parser  │
  │ (all .java files)   │         │  (changed files only)│
  └─────────────────────┘         └──────────────────────┘
              └────────────────┬────────────────┘
                               ▼
                    ┌──────────────────────┐
                    │  JavaParser Core     │
                    └──────────────────────┘
                               │
         ┌─────────────────────┼──────────────────────┐
         ▼                     ▼                      ▼
    AST Builder           CFG Builder          Call Graph Builder
         │                     │                      │
         ▼                     ▼                      ▼
    AST Graph            Decision Graph         Call Graph
         │                     │                      │
         └─────────────────────┼──────────────────────┘
                               ▼
                    ┌──────────────────────┐
                    │  DFG Builder         │  ← Def-Use chains
                    └──────────────────────┘
                               │
                    ┌──────────────────────┐
                    │  Semantic Annotator  │  ← ASIL, domain, safety-critical
                    └──────────────────────┘
                               │
                    ┌──────────────────────┐
                    │  Risk Scorer         │  ← composite risk per method
                    └──────────────────────┘
                               │
                    ┌──────────────────────┐
                    │  Graph Delta Engine  │  ← incremental update
                    └──────────────────────┘
                               │
                    ┌──────────────────────┐
                    │  Graph Version Store │  ← commit-linked snapshots
                    └──────────────────────┘
                               │
       ┌───────────────────────┼───────────────────────┐
       ▼                       ▼                       ▼
  Coverage Graph          Mutation Graph        Traceability Graph
  (enriched)              (enriched)
       │                       │                       │
       └───────────────────────┼───────────────────────┘
                               ▼
                    ┌──────────────────────┐
                    │  Evidence Graph      │  ← new
                    └──────────────────────┘
                               │
                    ┌──────────────────────┐
                    │  ASIL-D Orchestrator │
                    │  v3                  │
                    └──────────────────────┘
                               │
                    ┌──────────────────────┐
                    │  Prioritization      │  ← new
                    │  Engine              │
                    └──────────────────────┘
                               │
                    ┌──────────────────────┐
                    │  Agent Memory Layer  │  ← shared knowledge bus
                    └──────────────────────┘
                               │
        ┌──────────┬───────────┼───────────┬───────────┐
        ▼          ▼           ▼           ▼           ▼
   Unit Test    MC/DC      Coverage    Mutation    Safety
     Agent      Agent        Agent       Agent      Agent
        │          │           │           │           │
        └──────────┴───────────┴───────────┴───────────┘
                               ▼
                    ┌──────────────────────┐
                    │  Test Quality Scorer │
                    └──────────────────────┘
                               ▼
                    ┌──────────────────────┐
                    │  Evidence Engine     │
                    └──────────────────────┘
```

---

## Core Principles

1. Source code is parsed only for files changed since the last graph snapshot.
2. Agents consume graph slices — never raw source code.
3. Graph slices carry semantic meaning (ASIL level, domain, safety-critical flag).
4. Call graph context is attached to mutation slices so agents understand propagation.
5. Data flow chains are attached to MC/DC slices so agents reason about variable definitions.
6. A Risk Score governs the order in which all analysis tasks are dispatched.
7. An Agent Memory Layer prevents any fact from being computed twice across agents; its state is flushed to disk after every agent call so interrupted runs resume without token loss.
8. Every graph state is versioned and commit-linked for ASIL-D audit traceability.
9. Evidence is a first-class graph entity linked to requirements, methods, tests, and reports.
10. The Prioritization Engine caps token expenditure by ranking and budgeting tasks.
11. Agent output cache keys include a `semanticHash` so ASIL re-annotation always triggers re-analysis regardless of method body changes.
12. The Knowledge Graph Layer provides a unified query interface over all graph artifacts; cross-cutting queries do not require multi-file joins.

---

## Execution Mode: Bootstrap vs Incremental

The system operates in two distinct modes depending on project state. The Orchestrator detects the mode automatically on startup and applies the appropriate pipeline.

### Mode Detection

```
Step 1 — check graph state:
  if .graph/ does not exist OR manifest.json is empty:
      graphState = UNINITIALIZED
  else:
      graphState = INITIALIZED

Step 2 — check existing coverage/mutation reports:
  existingJacoco  = file_exists("target/site/jacoco/jacoco.xml")
  existingPit     = file_exists("target/pit-reports/*/mutations.xml")
  hasExistingData = existingJacoco OR existingPit

Step 3 — determine mode:
  if graphState == UNINITIALIZED AND NOT hasExistingData:
      mode = BOOTSTRAP          ← fresh project, no data
  elif graphState == UNINITIALIZED AND hasExistingData:
      mode = BOOTSTRAP_WITH_DATA  ← migration from legacy system
  elif git diff {prev_commit} {curr_commit} returns .java changes:
      mode = INCREMENTAL
  else:
      mode = COVERAGE_ONLY      ← no code change but reports updated
```

`BOOTSTRAP_WITH_DATA` follows the same 10-step pipeline as `BOOTSTRAP` but skips Step 6's worst-case defaults — instead it parses the existing JaCoCo/PIT reports first and uses real coverage/mutation factors for Risk Scoring. This prevents dispatching HIGH-priority agent waves for methods that are already well-covered in the legacy test suite.

---

## Bootstrap Mode — Full Project Graph Build (NEW)

### When It Applies

Bootstrap Mode runs exactly once: when the project is first onboarded and no `.graph/` directory exists. It also re-triggers if:

- The graph store is deleted or corrupted.
- A major refactor changes > 60% of method hashes (configurable threshold).
- The user explicitly runs `orchestrator init --force`.

At bootstrap time, there are no existing tests, no JaCoCo reports, no PIT reports, and no prior graph state. The system must build the full graph from source alone and generate the first wave of tests purely from AST + CFG + DFG + Semantic + Call Graph data.

### Bootstrap Pipeline

```
BOOTSTRAP INIT
     │
     ▼
┌─────────────────────────────────────────────────────┐
│  Step 1: Full Project Parse                         │
│  Parse all .java files → AST Graph (all classes)    │
└─────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────┐
│  Step 2: Full CFG Build                             │
│  Build CFG for every method → Decision Graph        │
└─────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────┐
│  Step 3: Full Call Graph Build                      │
│  Resolve all inter-method call edges                │
└─────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────┐
│  Step 4: Full DFG Build                             │
│  Compute Def-Use chains for all methods             │
└─────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────┐
│  Step 5: Semantic Annotation (full)                 │
│  Annotate all methods: ASIL, domain, safetyCritical │
│  Flag low-confidence heuristic annotations          │
└─────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────┐
│  Step 6: Risk Scoring (full)                        │
│  Compute riskScore for all methods                  │
│  Coverage and mutation factors default to WORST     │
│  (no reports yet → assume 0% coverage, 0% mutation) │
└─────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────┐
│  Step 7: Initial Graph Version Snapshot             │
│  Write v0.1.0_{commit} to Graph Version Store       │
│  All methods status = NEW                           │
└─────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────┐
│  Step 8: Bootstrap Prioritization                   │
│  Rank ALL methods by riskScore                      │
│  Apply session token budget                         │
│  Split into batches: Wave 1, Wave 2, Wave N         │
└─────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────┐
│  Step 9: Multi-Wave Agent Dispatch                  │
│  Wave 1: HIGH risk methods (ASIL-D, safetyCritical) │
│  Wave 2: MEDIUM risk methods                        │
│  Wave N: LOW risk methods (may span multiple runs)  │
└─────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────┐
│  Step 10: Bootstrap Evidence Snapshot               │
│  Write initial traceability stubs                   │
│  Mark all gates as INCOMPLETE                       │
│  Write bootstrap_manifest.json                      │
└─────────────────────────────────────────────────────┘
```

### Bootstrap Risk Scoring Adjustments

At bootstrap time, coverage and mutation factors depend on whether existing reports are available.

**Mode: `BOOTSTRAP` (no existing reports)**

```
coverageGapFactor  → 3.0  (assume 0% branch coverage for all methods)
mutationFactor     → 2.0  (assume 0% mutation score for all methods)
```

Every method is treated as a gap. Once Wave 1 tests are generated and CI runs JaCoCo + PIT, the next run switches to INCREMENTAL with real data.

**Mode: `BOOTSTRAP_WITH_DATA` (migration from legacy system)**

```
Step 6a: Parse existing JaCoCo XML → build CoverageGraph for all methods
Step 6b: Parse existing PIT XML    → build MutationGraph for all methods
Step 6c: Risk Scoring uses REAL per-method factors:
           coverageGapFactor = f(method.branchCoverage)
           mutationFactor    = f(method.mutationScore)
```

This avoids dispatching Wave 1 for methods that already have 95%+ branch coverage and 90%+ mutation score in the legacy suite. Typically reduces bootstrap token cost by 40–70% on migration projects.

**Bootstrap Risk Score — default vs with-data comparison**

```
Method: validateVin (ASIL-D, safetyCritical)
  Legacy JaCoCo: branch = 97%, PIT: mutationScore = 91%

  BOOTSTRAP default:     riskScore = 4.0×2.0×3.0×2.0×1.2×1.2 = 69.1 → Wave 1
  BOOTSTRAP_WITH_DATA:   riskScore = 4.0×2.0×1.0×1.0×1.2×1.2 = 11.5 → Wave 2

Method: calculateBrakePressure (ASIL-D, safetyCritical)
  Legacy JaCoCo: branch = 55%, PIT: mutationScore = 40%

  BOOTSTRAP default:     riskScore = 4.0×2.0×3.0×2.0×1.0×1.4 = 67.2 → Wave 1
  BOOTSTRAP_WITH_DATA:   riskScore = 4.0×2.0×3.0×2.0×1.0×1.4 = 67.2 → Wave 1 (same — gap is real)
```

### Multi-Wave Batching Strategy

A project of 200 methods cannot be fully analyzed in a single run without exceeding token budgets. The Prioritization Engine splits work across waves:

```
methodCount = 200
tokenBudget per run = 50,000

Wave assignment:
  Wave 1: riskTier = HIGH   → ~20 methods × avg 3 tasks × 1,200 tokens = ~72,000 tokens
          → split into Wave 1a (top 15 methods, ~54,000 tokens)
             and Wave 1b (remaining HIGH, next run)
  Wave 2: riskTier = MEDIUM → deferred until Wave 1 complete
  Wave 3: riskTier = LOW    → deferred until Wave 2 complete
```

Wave assignments are persisted in `bootstrap_state.json` so the system resumes correctly if interrupted:

```json
{
  "bootstrapVersion": "v0.1.0",
  "commit": "abc123ef",
  "totalMethods": 200,
  "wavesPlan": [
    { "wave": 1, "methods": ["M007", "M012", "M005", "..."], "status": "IN_PROGRESS" },
    { "wave": 2, "methods": ["M001", "M003", "M008", "..."], "status": "PENDING" },
    { "wave": 3, "methods": ["M042", "M055", "..."],          "status": "PENDING" }
  ],
  "completedMethods": ["M007"],
  "deferredMethods": [],
  "lastRunId": "bootstrap-run-001"
}
```

On the next CI run, if `bootstrap_state.wavesPlan` has `PENDING` waves, the Orchestrator continues bootstrap before switching to INCREMENTAL mode.

### Bootstrap Completion Condition

```
bootstrap is COMPLETE when:
    bootstrap_state.wavesPlan has no PENDING waves
    AND all HIGH-risk methods have agent outputs in cache
    AND traceability stubs are written for all requirements

After bootstrap COMPLETE:
    set mode = INCREMENTAL permanently
    do NOT re-run full parse on subsequent commits
```

### Bootstrap Manifest Schema

```json
{
  "runId": "bootstrap-run-001",
  "bootstrapVersion": "v0.1.0",
  "commit": "abc123ef",
  "timestamp": "2025-01-15T09:00:00Z",
  "mode": "BOOTSTRAP",
  "bootstrapDataMode": "NO_EXISTING_DATA",
  "existingJacocoFound": false,
  "existingPitFound": false,
  "totalMethods": 200,
  "wavesTotal": 3,
  "waveCompleted": 1,
  "methodsProcessedThisRun": 15,
  "methodsRemainingTotal": 185,
  "estimatedRunsToComplete": 13,
  "tokensUsedThisRun": 48_300,
  "tokenBudgetPerRun": 50_000,
  "highRiskMethodsComplete": false,
  "bootstrapComplete": false,
  "nextWave": 1,
  "nextWaveMethodCount": 5
}
```

For `BOOTSTRAP_WITH_DATA` runs, `bootstrapDataMode` is `"LEGACY_REPORTS_FOUND"`, `existingJacocoFound`/`existingPitFound` are `true`, and `estimatedRunsToComplete` is typically 40–70% lower than a cold bootstrap.

### Bootstrap vs Incremental Comparison

| Dimension                  | Bootstrap Mode                              | Incremental Mode                         |
|----------------------------|---------------------------------------------|------------------------------------------|
| Trigger                    | First run / no `.graph/` directory          | Subsequent runs after code change        |
| Parse scope                | All `.java` files in project                | Changed files only (git diff)            |
| Graph build scope          | Full: AST, CFG, DFG, Call Graph, Semantic   | DIRTY nodes only                         |
| Coverage/mutation data     | Not available — worst-case assumed          | Real JaCoCo + PIT reports                |
| Risk scoring baseline      | coverageGap=3.0, mutation=2.0 for all       | Actual per-method scores                 |
| Agent dispatch scope       | All methods, batched across multiple waves  | Changed + coverage-gap methods only      |
| Cache utilization          | 0% (no prior cache)                         | 70%+ (stable codebase)                   |
| Token cost                 | High — spread across N runs                 | Low — delta-driven                       |
| Completion check           | bootstrap_state.json wave tracking          | Delta report + evidence gate check       |
| Estimated total tokens     | methodCount × avgTaskTokens (~1,000–3,000)  | dirtyMethods × avgTaskTokens             |

---

## Module 1 — JavaParser Core (Incremental)

### Purpose

Parse only changed Java files and update the corresponding graph nodes. Unchanged nodes are loaded from the Graph Version Store without re-parsing.

### Incremental Parse Algorithm

```
changed_files = git diff --name-only {prev_commit} {curr_commit} | grep ".java"

for each file F in changed_files:
    parse(F) → new_ast_nodes
    compute methodHash for each method M in F
    if methodHash(M) != manifest[M.id].methodHash:
        replace AST node for M
        invalidate CFG, DFG, Call Graph edges for M
        invalidate Coverage, Mutation, Semantic nodes for M
        set M.status = DIRTY
    else:
        set M.status = CLEAN   ← skip all downstream rebuild
```

### Output Schema

```json
{
  "class": "BrakeController",
  "classHash": "sha256:a3f9...",
  "methods": [
    {
      "id": "M007",
      "name": "calculateBrakePressure",
      "returnType": "double",
      "methodHash": "sha256:c1d2...",
      "parameters": [
        { "name": "speed",    "type": "double" },
        { "name": "distance", "type": "double" }
      ],
      "annotations":  ["@SafetyCritical", "@AsilD"],
      "throwsTypes":  ["ArithmeticException"],
      "exceptionHandlers": [
        { "catchType": "ArithmeticException", "action": "log_and_return_safe_default" }
      ],
      "lineStart": 112,
      "lineEnd":   158,
      "status": "DIRTY"
    }
  ],
  "dependencies": ["SpeedSensor", "PressureActuator"]
}
```

### Graph Nodes

- CLASS
- METHOD
- FIELD
- PARAMETER
- ANNOTATION
- EXPRESSION
- STATEMENT
- EXCEPTION_HANDLER
- DEF_SITE  ← new, for DFG
- USE_SITE  ← new, for DFG

---

## Module 2 — CFG Builder

### Purpose

Generate a Control Flow Graph with full decision and branch detail.

### Output Schema

```json
{
  "methodId": "M007",
  "decisions": [
    {
      "decisionId": "D003",
      "line": 130,
      "expression": "speed > 0 && distance > 0",
      "conditions": ["speed > 0", "distance > 0"],
      "conditionCount": 2,
      "requiredMcdcPairs": 2,
      "branches": [
        { "id": "B005", "outcome": "true",  "targetNode": "N018" },
        { "id": "B006", "outcome": "false", "targetNode": "N022" }
      ]
    }
  ],
  "paths": [
    { "pathId": "P001", "nodes": ["N010", "D003-true",  "N018", "N025"] },
    { "pathId": "P002", "nodes": ["N010", "D003-false", "N022", "N025"] }
  ],
  "cyclomaticComplexity": 4
}
```

---

## Module 3 — Call Graph Builder (NEW)

### Purpose

Build an inter-method call graph so the Orchestrator and Mutation Agent understand how a mutation in a deep callee propagates upward to callers. Without this, a mutant in `C()` cannot trigger a test in `A()` even when `A → B → C`.

### Build Algorithm

```
for each method M in AST:
    parse M body for MethodCallExpr nodes
    for each call site CS in M:
        resolve target method T (same class or dependency)
        add edge: M → T  with call site line number
        if T is external (interface/mock boundary): mark as STUB_BOUNDARY
```

### Output Schema

```json
{
  "graphVersion": "1.3.0",
  "commit": "abc123ef",
  "callEdges": [
    {
      "caller":   "M001",
      "callee":   "M005",
      "callSite": 67,
      "type":     "DIRECT"
    },
    {
      "caller":   "M005",
      "callee":   "M007",
      "callSite": 92,
      "type":     "DIRECT"
    },
    {
      "caller":   "M005",
      "callee":   "UserRepository.save",
      "callSite": 95,
      "type":     "STUB_BOUNDARY"
    }
  ],
  "callerMap": {
    "M007": ["M005"],
    "M005": ["M001"]
  },
  "calleeMap": {
    "M001": ["M005"],
    "M005": ["M007", "UserRepository.save"]
  }
}
```

### Usage by Mutation Agent

When a mutant survives in `M007`, the Mutation Agent receives the upward call chain:

```
MUT-009 survived in M007
  → callers: M005
    → callers: M001
```

The agent generates an integration-style kill test at the highest observable caller (`M001`) rather than only a unit test at `M007`. This closes a common gap where unit tests for `M007` exist but callers never assert the downstream effect.

### Usage by Prioritization Engine

Methods with high in-degree (many callers) receive a higher risk multiplier because a mutation there propagates widely.

---

## Module 4 — Data Flow Graph Builder (NEW)

### Purpose

Track definition and use of every variable through a method. Enables the MC/DC Agent to understand which input variables flow into which decision conditions, and ensures tests exercise the correct data path rather than only the branch outcome.

### Def-Use Chain Concept

```
speed = sensor.read()     ← DEF(speed, line 115)
normalized = normalize(speed)  ← USE(speed, line 118), DEF(normalized, line 118)
result = calculate(normalized) ← USE(normalized, line 122), DEF(result, line 122)
if result > threshold:        ← USE(result, line 125) → feeds D003
```

The DFG exposes that to truly exercise decision D003, a test must control `speed` at the source — not just assert `result`.

### Output Schema

```json
{
  "methodId": "M007",
  "defUseChains": [
    {
      "variable": "speed",
      "defSite":  { "line": 115, "nodeId": "N010", "source": "PARAMETER" },
      "useSites": [
        { "line": 118, "nodeId": "N014", "role": "ARGUMENT" },
        { "line": 130, "nodeId": "D003", "role": "CONDITION_INPUT" }
      ],
      "flowsIntoDecisions": ["D003"]
    },
    {
      "variable": "normalized",
      "defSite":  { "line": 118, "nodeId": "N014", "source": "COMPUTED" },
      "useSites": [
        { "line": 122, "nodeId": "N016", "role": "ARGUMENT" },
        { "line": 130, "nodeId": "D003", "role": "CONDITION_INPUT" }
      ],
      "flowsIntoDecisions": ["D003"]
    }
  ],
  "decisionInputMap": {
    "D003": {
      "conditions": ["speed > 0", "distance > 0"],
      "sourceVariables": ["speed", "distance"],
      "sourceOrigin": ["PARAMETER", "PARAMETER"]
    }
  }
}
```

### Usage by MC/DC Agent

The agent receives `decisionInputMap` and knows:

- Condition `speed > 0` is controlled by parameter `speed` (origin: PARAMETER → directly settable in test).
- No intermediate transformation obscures the value.
- MC/DC test pair for `speed > 0` must set `speed` directly, not a downstream derived variable.

If `sourceOrigin` were `COMPUTED`, the agent generates a mock of the computation node rather than setting the raw parameter.

---

## Module 5 — Semantic Annotator (NEW)

### Purpose

Enrich every method node with business meaning, ASIL level, domain classification, and safety-criticality flag. This is the key upgrade from syntactic to semantic graph. Agents receive this context without reading requirements or source comments.

### Annotation Sources (in priority order)

1. Explicit Java annotations (`@AsilD`, `@SafetyCritical`, `@Domain("VehicleIdentification")`)
2. Requirement Graph mapping (`REQ-001 → validateVin → ASIL-D`)
3. Class-level annotations propagated down to all methods
4. Keyword heuristics on method names and class names (fallback only)

### Semantic Graph Schema

```json
{
  "methodId": "M007",
  "methodName": "calculateBrakePressure",
  "semantic": {
    "safetyCritical": true,
    "asilLevel": "D",
    "domain": "BrakeSystem",
    "subDomain": "PressureControl",
    "functionalDescription": "Computes hydraulic brake pressure from vehicle speed and stopping distance.",
    "safeState": "RETURN_ZERO_PRESSURE",
    "linkedRequirements": ["REQ-007", "REQ-008"],
    "annotationSource": "EXPLICIT",
    "confidence": 1.0
  }
}
```

```json
{
  "methodId": "M042",
  "methodName": "formatDate",
  "semantic": {
    "safetyCritical": false,
    "asilLevel": "QM",
    "domain": "Utility",
    "subDomain": "Formatting",
    "functionalDescription": null,
    "safeState": null,
    "linkedRequirements": [],
    "annotationSource": "HEURISTIC",
    "confidence": 0.72
  }
}
```

`confidence < 0.8` triggers a warning in the run manifest: semantic annotation was inferred, not declared. Unconfident ASIL assignments must be reviewed before evidence is signed.

### Domain Taxonomy (configurable)

```
BrakeSystem
  ├── PressureControl
  ├── ABSControl
  └── EmergencyStop
PowerTrain
  ├── EngineControl
  └── TransmissionControl
VehicleIdentification
  ├── VINValidation
  └── ECUIdentification
Utility
  ├── Formatting
  └── Logging
```

### Effect on Agents

Every graph slice sent to any agent includes the `semantic` block. Agents no longer need to infer domain context from method names. This eliminates a major source of hallucinated test logic in raw-source-based systems.

---

## Module 6 — Risk Scorer (NEW)

### Purpose

Assign a composite Risk Score to every method. This score drives the Prioritization Engine — safety-critical methods with coverage gaps get analyzed first; utility methods with full coverage are deferred or skipped.

### Risk Score Formula

```
riskScore(M) =
    asilWeight(M.asil)
  × safetyCriticalMultiplier(M.safetyCritical)
  × coverageGapFactor(M.branchCoverage)
  × mutationFactor(M.mutationScore)
  × callChainFactor(M.inDegree)
  × requirementFactor(M.linkedRequirements)

where:

asilWeight:
  D  → 4.0
  C  → 2.0
  B  → 1.0
  A  → 0.5
  QM → 0.1

safetyCriticalMultiplier:
  true  → 2.0
  false → 1.0

coverageGapFactor:
  branchCoverage < 80%  → 3.0
  branchCoverage < 95%  → 1.5
  branchCoverage >= 95% → 1.0

mutationFactor:
  mutationScore < 70%   → 2.0
  mutationScore < 90%   → 1.3
  mutationScore >= 90%  → 1.0

callChainFactor:
  inDegree >= 5  → 1.5   (many callers → high blast radius)
  inDegree 2–4   → 1.2
  inDegree <= 1  → 1.0

requirementFactor:
  linkedRequirements count >= 3 → 1.4
  linkedRequirements count 1–2  → 1.2
  linkedRequirements count 0    → 0.8
```

### Risk Graph Schema

```json
{
  "methodId": "M007",
  "methodName": "calculateBrakePressure",
  "riskScore": 48.0,
  "riskTier": "HIGH",
  "components": {
    "asilWeight": 4.0,
    "safetyCriticalMultiplier": 2.0,
    "coverageGapFactor": 1.5,
    "mutationFactor": 2.0,
    "callChainFactor": 1.0,
    "requirementFactor": 1.0
  }
}
```

```json
{
  "methodId": "M042",
  "methodName": "formatDate",
  "riskScore": 0.1,
  "riskTier": "LOW",
  "components": {
    "asilWeight": 0.1,
    "safetyCriticalMultiplier": 1.0,
    "coverageGapFactor": 1.0,
    "mutationFactor": 1.0,
    "callChainFactor": 1.0,
    "requirementFactor": 0.8
  }
}
```

### Risk Tier Thresholds

| Tier   | Score Range | Treatment                                    |
|--------|-------------|----------------------------------------------|
| HIGH   | >= 20       | All agents run; Safety Agent mandatory       |
| MEDIUM | 5–19        | Unit, Coverage, Mutation agents              |
| LOW    | 1–4         | Unit agent only; Safety Agent skipped        |
| SKIP   | < 1         | No agent invocation; cached output reused    |

---

## Module 7 — Graph Delta Engine

### Purpose

Gate all downstream processing so only DIRTY method nodes trigger graph rebuilds and agent invocations. Produces a structured delta report consumed by the Orchestrator and Prioritization Engine.

### Delta Report Schema

```json
{
  "runId": "run-2025-01-15-003",
  "commit": "abc123ef",
  "prevCommit": "9f8e7d6c",
  "dirtyMethods":     ["M007", "M012"],
  "newMethods":       ["M015"],
  "deletedMethods":   ["M003"],
  "cleanMethods":     ["M001", "M002", "M004", "M005", "M006", "M008"],
  "coverageGaps":     ["M007", "M001"],
  "survivingMutants": ["M007", "M012"],
  "staleSemanticNodes": [],
  "staleTraceabilityLinks": ["REQ-007"]
}
```

### Rebuild Cascade Rules

```
Method M is DIRTY:
  → Rebuild: AST(M), CFG(M), DFG(M), CallGraph edges from/to M
  → Rebuild: SemanticAnnotation(M), RiskScore(M)
  → Rebuild: CoverageNode(M), MutationNode(M)
  → Invalidate: AgentOutputCache entries for M
  → Mark as STALE: TraceabilityLinks referencing M
  → Mark as STALE: EvidenceNodes referencing M

Method M is CLEAN:
  → Load all nodes from Graph Version Store
  → Skip all rebuilds
  → Agent invocation only if riskTier requires and no cache hit
```

---

## Module 8 — Graph Version Store (NEW)

### Purpose

Maintain commit-linked snapshots of the full graph. Required for ASIL-D audit — every evidence package must be traceable to an exact graph state which was traceable to an exact commit.

### Version Schema

```json
{
  "graphVersion": "1.3.0",
  "commit": "abc123ef",
  "branch": "main",
  "timestamp": "2025-01-15T11:00:00Z",
  "methodCount": 52,
  "dirtyMethodCount": 3,
  "graphHash": "sha256:9e2f...",
  "prevGraphVersion": "1.2.9",
  "prevCommit": "9f8e7d6c",
  "changeLog": [
    { "methodId": "M007", "changeType": "MODIFIED", "prevHash": "sha256:c0d1...", "newHash": "sha256:c1d2..." },
    { "methodId": "M015", "changeType": "ADDED",    "prevHash": null,            "newHash": "sha256:d3e4..." }
  ]
}
```

### Directory Structure

```
.graph/
└── versions/
    ├── v1.2.9_9f8e7d6c/
    │   ├── manifest.json
    │   └── snapshot.json.gz     ← full graph state
    └── v1.3.0_abc123ef/
        ├── manifest.json
        └── snapshot.json.gz
```

### Retention Policy

- Keep all versions linked to an evidence package indefinitely.
- Keep last 10 versions for non-evidenced runs.
- Compress snapshots older than 30 days.

---

## Module 9 — Coverage Graph (Enriched)

### Purpose

Expose full coverage dimensions required for ASIL-D and MC/DC analysis. v2.0 only carried `missingBranches`. v3.0 adds line-level, decision-level, condition-level, and MC/DC pair coverage.

### Output Schema

```json
{
  "methodId": "M007",
  "coverageGraphVersion": "1.3.0",
  "coverage": {
    "line":      88,
    "branch":    75,
    "decision":  80,
    "condition": 60,
    "mcdc":      50
  },
  "missingLines": [135, 136, 140],
  "missingBranches": [
    {
      "id": "B006",
      "outcome": "false",
      "line": 130,
      "condition": "distance > 0",
      "decision": "D003",
      "riskScore": 48.0
    }
  ],
  "decisionCoverage": [
    { "decisionId": "D003", "trueCovered": true, "falseCovered": false }
  ],
  "conditionCoverage": [
    { "decisionId": "D003", "condition": "speed > 0",    "trueCovered": true,  "falseCovered": true  },
    { "decisionId": "D003", "condition": "distance > 0", "trueCovered": true,  "falseCovered": false }
  ],
  "mcdcCoverage": [
    { "decisionId": "D003", "condition": "speed > 0",    "mcdcPairCovered": true  },
    { "decisionId": "D003", "condition": "distance > 0", "mcdcPairCovered": false }
  ],
  "meetsBranchThreshold":    false,
  "meetsMcdcRequirement":    false
}
```

`riskScore` is attached to each missing branch so the Coverage Agent can prioritize which gap to address first within the same method.

---

## Module 10 — Mutation Graph (Enriched)

### Purpose

Add equivalent mutant probability, call chain context, and killing test hints. v2.0 only stored `type` and `line`. v3.0 enables the Mutation Agent to skip likely-equivalent mutants and generate integration-level kill tests when the call chain indicates unit tests alone will not suffice.

### Output Schema

```json
{
  "methodId": "M007",
  "mutationGraphVersion": "1.3.0",
  "mutationScore": 68,
  "survivedMutants": [
    {
      "mutantId": "MUT-009",
      "operator": "NEGATE_CONDITIONAL",
      "method": "calculateBrakePressure",
      "line": 130,
      "condition": "speed > 0",
      "killed": false,
      "equivalentProbability": 0.05,
      "killingTestHint": "assert exception or safe-default when speed <= 0",
      "callChain": {
        "immediateCallers": ["M005"],
        "transitiveCallers": ["M001"],
        "observationPoint": "M001",
        "integrationTestRequired": true
      },
      "dataFlowNote": "speed flows from PARAMETER — directly settable in test"
    },
    {
      "mutantId": "MUT-010",
      "operator": "RETURN_VALUES",
      "method": "calculateBrakePressure",
      "line": 155,
      "condition": null,
      "killed": false,
      "equivalentProbability": 0.82,
      "killingTestHint": null,
      "callChain": null,
      "dataFlowNote": "return value is not asserted by any existing caller test"
    }
  ]
}
```

`equivalentProbability >= 0.7` → Mutation Agent skips this mutant and marks it `LIKELY_EQUIVALENT` in the output. This avoids spending tokens generating tests that cannot kill a structurally equivalent mutant.

`integrationTestRequired: true` → Mutation Agent generates a test at `observationPoint` (M001), not at M007.

---

## Module 11 — Requirement Graph

No structural change from v2.0. Semantic annotations from Module 5 now flow into this graph via the `linkedRequirements` field, creating a bidirectional link.

### Schema (reference)

```json
{
  "requirementId": "REQ-007",
  "asilLevel": "D",
  "description": "System shall apply maximum brake pressure when speed exceeds threshold.",
  "mappedMethods": ["calculateBrakePressure"],
  "verificationStatus": "PARTIAL",
  "openGaps": ["MC/DC pair for condition 'distance > 0' not covered"]
}
```

---

## Module 12 — Traceability Graph

### Change from v2.0

Now includes `graphVersion` on each link so every traceability assertion is tied to an exact graph snapshot. Stale links (from a modified method) are marked explicitly.

### Schema

```json
{
  "requirement": "REQ-007",
  "asilLevel": "D",
  "method": "calculateBrakePressure",
  "methodHash": "sha256:c1d2...",
  "graphVersion": "1.3.0",
  "tests": [
    { "id": "TC-301", "status": "PASS", "covers": ["B005.true",  "D003.true"] },
    { "id": "TC-302", "status": "PASS", "covers": ["D003.true",  "C001.true"] }
  ],
  "openGaps": ["B006.false", "D003.false", "C002.false (distance > 0)"],
  "traceabilityStatus": "INCOMPLETE",
  "stale": false,
  "evidenceId": null
}
```

---

## Module 13 — Evidence Graph (NEW)

### Purpose

Make evidence a first-class queryable graph entity — not just a folder of reports. Links requirements to methods to tests to coverage data to mutation data to artifacts. The Evidence Engine queries this graph to determine completeness before signing.

### Node Types

```
REQUIREMENT → METHOD → TESTCASE → COVERAGE_DATUM
                                → MUTATION_DATUM
                     → MCDC_PAIR
          → SAFETY_FINDING
          → EVIDENCE_ARTIFACT
```

### Relationship Types

```
VERIFIED_BY     REQUIREMENT → TESTCASE
COVERS          TESTCASE    → COVERAGE_DATUM
KILLS           TESTCASE    → MUTATION_DATUM
SATISFIES       TESTCASE    → MCDC_PAIR
BLOCKS          SAFETY_FINDING → EVIDENCE_ARTIFACT
INCLUDED_IN     COVERAGE_DATUM → EVIDENCE_ARTIFACT
SIGNED_BY       EVIDENCE_ARTIFACT → run_manifest
```

### Evidence Node Schema

```json
{
  "evidenceId": "EP-run-2025-01-15-003",
  "graphVersion": "1.3.0",
  "commit": "abc123ef",
  "status": "INCOMPLETE",
  "completenessCheck": {
    "allRequirementsCovered": false,
    "openRequirements": ["REQ-007"],
    "branchCoverage": 75,
    "mcdcCoverage": 50,
    "mutationScore": 68,
    "safetyFailCount": 0,
    "blockingItems": [
      "REQ-007: MC/DC pair for 'distance > 0' not covered",
      "M007: mutation score 68% < 90% threshold"
    ]
  },
  "artifacts": [],
  "signedAt": null,
  "signedBy": null
}
```

When all blocking items are resolved, `status` transitions to `COMPLETE` and the Evidence Engine signs the artifact set.

---

## Knowledge Graph Layer

### Purpose

Provide a unified, queryable representation of all graph artifacts. All modules (AST, CFG, DFG, Call Graph, Semantic, Coverage, Mutation, Traceability, Evidence) write into the Knowledge Graph. The Orchestrator, Prioritization Engine, and Evidence Engine query it rather than reading individual JSON files. This is the architectural backbone that makes cross-cutting queries — such as "find all ASIL-D methods with coverage gaps and surviving mutants" — computable in a single query rather than multi-file joins.

### Technology Backend

**Recommended: embedded graph database (Memgraph or Neo4j Embedded)** stored at `.graph/kg/`.

```
Criteria for choice:
  Must work offline (no cloud dependency)       → Memgraph embedded, Neo4j Embedded (Community)
  Must support Cypher query language            → both
  Must persist to disk (auditable)              → both
  Must handle incremental node/edge updates     → both
  Must run inside a CI environment (no daemon)  → Memgraph in-process preferred

Fallback (no graph DB available):
  JSON-based traversal using .graph/ flat files
  Slower for cross-cutting queries but fully portable
  All Cypher examples below have JSON-traversal equivalents
```

The Knowledge Graph Layer is the only component that requires a non-trivial external dependency. Projects that cannot install Memgraph/Neo4j use the JSON-traversal fallback — all other modules remain identical.

### Node Types

| Node Type      | Key Properties                                   | Source Module        |
|----------------|--------------------------------------------------|----------------------|
| CLASS          | classId, name, classHash                         | Module 1 — JavaParser|
| METHOD         | methodId, name, asilLevel, safetyCritical, riskScore, riskTier | Modules 1, 5, 6 |
| DECISION       | decisionId, expression, conditionCount           | Module 2 — CFG       |
| BRANCH         | branchId, outcome, covered                       | Module 2 — CFG       |
| CONDITION      | conditionId, expression, mcdcCovered             | Module 2 — CFG       |
| DEF_SITE       | nodeId, variable, line, source                   | Module 4 — DFG       |
| USE_SITE       | nodeId, variable, line, role                     | Module 4 — DFG       |
| REQUIREMENT    | requirementId, asilLevel, verificationStatus     | Module 11            |
| TESTCASE       | testId, type, status, qualityScore               | Agent outputs        |
| COVERAGE       | methodId, branch, mcdc, decision, condition      | Module 9             |
| MUTANT         | mutantId, operator, killed, equivalentProbability| Module 10            |
| SAFETYISSUE    | issueId, checklistId, severity, resolved         | Safety Agent         |
| EVIDENCE_ARTIFACT | evidenceId, status, graphVersion, signedAt    | Module 13            |

### Relationship Types

| Relationship   | From → To                        | Meaning                                   |
|----------------|----------------------------------|-------------------------------------------|
| CONTAINS       | CLASS → METHOD                   | Method belongs to class                   |
| HAS_DECISION   | METHOD → DECISION                | Method contains this decision point       |
| HAS_BRANCH     | DECISION → BRANCH                | Decision has this branch outcome          |
| HAS_CONDITION  | DECISION → CONDITION             | Decision has this atomic condition        |
| CALLS          | METHOD → METHOD                  | Direct call edge (from Call Graph)        |
| STUB_BOUNDARY  | METHOD → external                | Call crosses mock/interface boundary      |
| DEF_FLOWS_TO   | DEF_SITE → USE_SITE              | Variable definition reaches this use site |
| FEEDS_DECISION | USE_SITE → DECISION              | Variable value feeds this decision        |
| TRACES_TO      | REQUIREMENT → METHOD             | Requirement is verified by this method    |
| TESTED_BY      | METHOD → TESTCASE                | Test covers this method                   |
| COVERS         | TESTCASE → BRANCH                | Test exercises this branch                |
| SATISFIES      | TESTCASE → CONDITION             | Test satisfies this MC/DC condition pair  |
| KILLS          | TESTCASE → MUTANT                | Test kills this mutant                    |
| RAISES         | METHOD → SAFETYISSUE             | Method has this safety finding            |
| BLOCKS         | SAFETYISSUE → EVIDENCE_ARTIFACT  | Finding blocks evidence signing           |
| INCLUDED_IN    | COVERAGE → EVIDENCE_ARTIFACT     | Coverage data is part of this evidence    |
| VALIDATED_BY   | REQUIREMENT → EVIDENCE_ARTIFACT  | Requirement is satisfied in this evidence |

### Cypher Query Examples

```cypher
-- All ASIL-D methods with branch coverage below 95%
MATCH (m:METHOD {asilLevel: "D"})-[:HAS_COVERAGE]->(c:COVERAGE)
WHERE c.branch < 95
RETURN m.methodId, m.name, c.branch
ORDER BY c.branch ASC

-- Methods with survived mutants AND unmet requirements (highest urgency)
MATCH (r:REQUIREMENT {verificationStatus: "PARTIAL"})-[:TRACES_TO]->(m:METHOD)
      -[:HAS_MUTANT]->(mut:MUTANT {killed: false})
WHERE mut.equivalentProbability < 0.7
RETURN m.name, r.requirementId, mut.mutantId, mut.operator
ORDER BY m.name

-- Full call chain upward from a mutant's method
MATCH path = (m:METHOD {methodId: "M007"})<-[:CALLS*1..5]-(caller:METHOD)
RETURN [node in nodes(path) | node.name] AS callChain

-- MC/DC coverage gaps on safety-critical methods only
MATCH (m:METHOD {safetyCritical: true})-[:HAS_DECISION]->(d:DECISION)
      -[:HAS_CONDITION]->(c:CONDITION {mcdcCovered: false})
RETURN m.name, d.decisionId, c.expression
ORDER BY m.riskScore DESC

-- Evidence completeness check for a specific requirement
MATCH (r:REQUIREMENT {requirementId: "REQ-007"})-[:TRACES_TO]->(m:METHOD)
OPTIONAL MATCH (m)-[:TESTED_BY]->(t:TESTCASE)
OPTIONAL MATCH (m)-[:HAS_COVERAGE]->(cov:COVERAGE)
RETURN r.requirementId, m.name,
       count(t) AS testCount,
       cov.branch AS branchCoverage,
       cov.mcdc AS mcdcCoverage

-- Find all cache-invalid methods after ASIL re-annotation
MATCH (m:METHOD)
WHERE m.asilLevel <> m.cachedAsilLevel
RETURN m.methodId, m.cachedAsilLevel AS old, m.asilLevel AS new
```

### JSON-Traversal Fallback Equivalents

For projects without a graph database, the Orchestrator implements the same queries as file-based traversal over `.graph/` JSON files. Performance is acceptable for projects up to ~500 methods; beyond that, the embedded graph database is strongly recommended.

```python
# Equivalent of: MATCH (m:METHOD {asilLevel:"D"}) ... WHERE c.branch < 95
methods = load_all(".graph/semantic/*.json")
coverage = load_all(".graph/coverage/*.json")
result = [
    m for m in methods
    if m["semantic"]["asilLevel"] == "D"
    and coverage[m["methodId"]]["coverage"]["branch"] < 95
]
```

### Knowledge Graph Update Protocol

The Knowledge Graph is updated incrementally — not rebuilt from scratch on each run.

```
On each module completing its graph build for method M:
    KG.merge(nodeType, nodeId, properties)   ← upsert, not insert
    KG.merge(relationshipType, fromId, toId) ← idempotent

On method M becoming DIRTY:
    KG.delete(nodeId: M, cascade: true)      ← remove all edges from M
    rebuild all M's nodes from new graph files
    re-merge into KG
```

---

## Module 14 — Agent Output Cache

### Purpose

Persist agent-generated outputs keyed by a composite hash. Avoid re-invoking the LLM for any method whose AST, CFG, coverage, mutation, and semantic annotation have not changed since the last analyzed run.

### Cache Key (v3 — includes semantic hash)

```
semanticHash = sha256(
    method.semantic.asilLevel
  + method.semantic.safetyCritical
  + method.semantic.domain
  + method.semantic.safeState
)

cache_key = sha256(
    agentType
  + "|" + methodId
  + "|" + graphHash        ← derived from AST + CFG + DFG + Coverage + Mutation
  + "|" + semanticHash     ← NEW: invalidates cache on ASIL re-annotation
)
```

**Why `semanticHash` is required:** `graphHash` covers method body and structural graphs only. If a method is re-annotated from ASIL-C to ASIL-D (or `safeState` is added) without any code change, `methodHash` and `graphHash` remain identical — a cache hit would return a Safety Agent output evaluated under ASIL-C thresholds. Items S-09 (thread-safety) and S-14 (timeout) are FAIL at ASIL-D but only WARN at ASIL-C. This would silently pass evidence that should be blocked. Including `semanticHash` ensures a re-annotation always triggers re-analysis.

### Cache Invalidation Rules

```
Method M triggers cache invalidation when:
  M.methodHash changed        → graphHash changes  → cache miss (code changed)
  M.semantic.asilLevel changed → semanticHash changes → cache miss (annotation changed)
  M.semantic.safeState changed → semanticHash changes → cache miss
  M.semantic.domain changed   → semanticHash changes → cache miss (lower severity)
  coverage/mutation data changed → graphHash changes → cache miss for Coverage/Mutation agents

Method M does NOT invalidate cache when:
  only comments changed       → methodHash unchanged → cache hit (correct)
  formatting changed          → methodHash unchanged → cache hit (correct)
  unrelated method changed    → this method's hashes unchanged → cache hit (correct)
```

### Cache Directory

```
.graph/cache/agent_outputs/
├── UnitTestAgent/
│   └── M007_{cacheKey32chars}.json
├── MCDCAgent/
│   └── M007_{cacheKey32chars}.json
├── CoverageAgent/
│   └── M007_{cacheKey32chars}.json
├── MutationAgent/
│   └── M007_{cacheKey32chars}.json
└── SafetyAgent/
    └── M007_{cacheKey32chars}.json
```

### Cache Entry Schema

```json
{
  "cacheKey": "sha256:e9f1...",
  "agentType": "SafetyAgent",
  "methodId": "M007",
  "graphHash": "sha256:c1d2...",
  "semanticHash": "sha256:7a3b...",
  "asilLevelAtCache": "D",
  "generatedAt": "2025-01-15T10:35:00Z",
  "graphVersion": "1.3.0",
  "output": {
    "checklistResults": ["..."],
    "failCount": 0,
    "warnCount": 1,
    "blockEvidence": false,
    "qualityScore": 88,
    "tokensUsed": 1240
  },
  "ttlDays": 30
}
```

`asilLevelAtCache` is stored for audit purposes — evidence package can show which ASIL level was in effect when each agent output was generated.

### Cache Hit Rate Targets

| Codebase maturity       | Expected cache hit rate   |
|-------------------------|---------------------------|
| Bootstrap (first run)   | 0%                        |
| After 5 CI runs         | 50–65%                    |
| Steady-state (daily CI) | 70–85%                    |
| After ASIL re-annotation| Hit rate drops temporarily by ~5–15% (re-analysis of re-annotated methods) |

---

### Purpose

Before any LLM is invoked, rank every pending analysis task by priority score. This prevents token explosion when there are hundreds of coverage gaps, surviving mutants, or safety findings. Tasks below the token budget threshold are deferred to the next run.

### Task Priority Score Formula

```
taskPriority(task) =
    riskScore(task.method)
  × taskTypeWeight(task.type)
  × blockingFactor(task)
  × freshnessFactor(task)

taskTypeWeight:
  SAFETY_FAIL       → 5.0  (always top priority — blocks evidence)
  MCDC_GAP          → 4.0  (ASIL-D mandatory)
  BRANCH_GAP_HIGH   → 3.0  (risk tier HIGH)
  MUTATION_HIGH     → 2.5  (risk tier HIGH, equivalentProbability < 0.5)
  UNIT_MISSING      → 2.0  (no tests at all)
  BRANCH_GAP_MEDIUM → 1.5
  MUTATION_MEDIUM   → 1.2
  BRANCH_GAP_LOW    → 0.5
  MUTATION_LOW      → 0.4
  UNIT_ENHANCEMENT  → 0.3

blockingFactor:
  task directly blocks evidence generation → 2.0
  task indirectly blocks (stale traceability) → 1.3
  task does not block → 1.0

freshnessFactor:
  task.method was modified in this commit → 1.5
  task is from a previous run (carry-over gap) → 1.0
```

### Token Budget Management

```
sessionTokenBudget = configuredBudget (default: 50,000 tokens per run)

for each task T in priorityQueue (descending order):
    estimatedCost = agentTokenBudget[T.agentType]
    if usedTokens + estimatedCost <= sessionTokenBudget:
        dispatch(T)
        usedTokens += estimatedCost
    else:
        defer(T) → deferredTasks.json (picked up next run)
        log("Task ${T.id} deferred: budget exhausted")
```

### Priority Queue Example

Given 200 missing branches across 50 methods:

```
Rank  Task              Method                    PriorityScore  Tokens
────────────────────────────────────────────────────────────────────────
1     SAFETY_FAIL       calculateBrakePressure    480.0          1,250
2     MCDC_GAP          calculateBrakePressure    192.0          1,200
3     BRANCH_GAP        calculateBrakePressure    144.0            950
4     MUTATION          calculateBrakePressure    120.0            950
5     MCDC_GAP          validateVin                80.0          1,200
...
198   BRANCH_GAP        formatDate                  0.05           950
199   MUTATION          formatDate                  0.04           950
200   UNIT_ENHANCEMENT  formatDate                  0.03           950
────────────────────────────────────────────────────────────────────────
Budget 50,000 tokens → dispatches top ~45 tasks, defers remaining 155
```

Without the Prioritization Engine, all 200 tasks would be dispatched in one run, consuming ~160,000 tokens with most spent on low-risk utility code. With it: highest-risk ASIL-D gaps are resolved in the first run; low-risk gaps are addressed incrementally.

---

## Agent Memory Layer (NEW)

### Purpose

A shared knowledge bus that persists both within a single orchestrator run (in-memory) and across interrupted runs (on-disk snapshot). Agents write their findings to the memory layer; subsequent agents read it to avoid redundant analysis. If a CI job is killed mid-run, the next run resumes from the last saved snapshot rather than recomputing all prior facts.

### Persistence Model

```
In-memory layer:  alive for the duration of one Orchestrator process
                  written to after every agent call (flush trigger)
                  read by every subsequent agent call in the same run

On-disk snapshot: .graph/agent_memory_snapshot.json
                  written atomically (tmp file → rename) after each flush
                  loaded on run start if runId matches → resume mode
                  deleted when all tasks in a run are DONE
```

### Flush Protocol

```
after each agent call completes:
    acquire_write_lock(.graph/agent_memory_snapshot.json)
    write memory_layer → .graph/agent_memory_snapshot.tmp
    rename .tmp → agent_memory_snapshot.json   ← atomic
    release_write_lock()
```

Atomic rename ensures the snapshot is never partially written. If the process is killed between calls, the last complete flush is intact.

### Resume on Restart

```
on Orchestrator INIT:
    if .graph/agent_memory_snapshot.json exists:
        snapshot = load(agent_memory_snapshot.json)
        if snapshot.runId == currentRunId:
            AgentMemoryLayer.load(snapshot)   ← resume
            log("Resumed memory from snapshot, ${len(snapshot.facts)} methods cached")
        else:
            AgentMemoryLayer.reset()          ← stale snapshot from different run
```

### Memory Layer Schema

```json
{
  "runId": "run-2025-01-15-003",
  "lastFlushedAt": "2025-01-15T10:47:33Z",
  "completedTasks": ["SafetyAgent:M007", "MCDCAgent:M007"],
  "pendingTasks":   ["CoverageAgent:M007", "MutationAgent:M007"],
  "facts": {
    "M007": {
      "safetyChecklist": {
        "S-01": "PASS",
        "S-10": "FAIL",
        "S-05": "WARN"
      },
      "confirmedUnreachableBranches": [],
      "confirmedEquivalentMutants": ["MUT-010"],
      "mcdcPairsGenerated": ["D003-speed", "D003-distance"],
      "coveredBranches": ["B005.true", "B006.true"],
      "pendingBranches": ["B006.false"],
      "unitTestIds": ["TC-301", "TC-302"],
      "killTestIds": []
    }
  }
}
```

`completedTasks` and `pendingTasks` allow the Orchestrator to skip re-dispatching agent calls that already completed before the interruption.

### Cross-Agent Sharing Rules

```
SafetyAgent writes:   safetyChecklist, confirmedUnreachableBranches
  → CoverageAgent reads: skip unreachable branches (no test needed)
  → MutationAgent reads: FAIL items may correspond to survived mutants

MCDCAgent writes:     mcdcPairsGenerated, coveredBranches
  → CoverageAgent reads: skip branches already covered by MCDC tests
  → UnitTestAgent reads: do not regenerate MCDC-covered branches

CoverageAgent writes: coveredBranches (newly covered), pendingBranches
  → UnitTestAgent reads: skip already-covered branches

MutationAgent writes: confirmedEquivalentMutants, killTestIds
  → CoverageAgent reads: kill tests may cover additional branches (update coverage)
```

### Token Savings from Agent Memory

Without memory: Coverage Agent and Unit Test Agent both compute which branches are covered by existing tests — duplicate work, ~200 tokens each.

With memory: Coverage Agent reads `coveredBranches` written by MC/DC Agent. Zero redundant computation.

Estimated saving across a 10-method run: ~2,000–4,000 tokens.

Estimated saving from resume (interrupted run, 3 of 10 agents completed): up to 30% of run token budget recovered.

---

## ASIL-D Test Orchestrator v3

### Responsibilities

- Load delta report and graph version
- Invoke Prioritization Engine to rank all tasks
- Check Agent Output Cache before any LLM call
- Dispatch tasks in priority order within token budget
- Coordinate Agent Memory Layer across calls
- Apply quality scoring and feedback loop
- Update Evidence Graph
- Produce signed evidence package when all gates pass

### Full Orchestration Loop

```
INIT
  ├── load delta_report.json
  ├── load graph_version.json
  ├── if .graph/agent_memory_snapshot.json exists AND snapshot.runId == currentRunId:
  │       AgentMemoryLayer.load(snapshot)   ← resume interrupted run
  │       skip completed tasks from snapshot.completedTasks
  │   else:
  │       AgentMemoryLayer.reset()          ← fresh run
  └── initialize PrioritizationEngine(tokenBudget)

BUILD_TASK_QUEUE
  ├── for each dirty/new method M:
  │   ├── if riskTier == SKIP: skip
  │   ├── check cache: if hit → load, write to AgentMemoryLayer, skip LLM
  │   └── else: enqueue tasks (SAFETY, MCDC, BRANCH, MUTATION, UNIT) by priority
  └── sort queue by taskPriority descending

DISPATCH_LOOP
  ├── while queue not empty and tokenBudget > 0:
  │   ├── pop highest-priority task T
  │   ├── if T.id in completedTasks (from resume): skip
  │   ├── attach graph slice: AST + CFG + DFG + CallGraph + Semantic + Risk + AgentMemory
  │   ├── invoke agent (batched if compatible)
  │   ├── run Test Quality Scorer on output
  │   ├── if score < 70: invoke feedback loop (max 2 retries)
  │   ├── write output to AgentMemoryLayer
  │   ├── flush AgentMemoryLayer → .graph/agent_memory_snapshot.json (atomic)
  │   ├── write output to AgentOutputCache (key = graphHash + semanticHash)
  │   ├── update TraceabilityGraph
  │   ├── update EvidenceGraph (via Knowledge Graph Layer)
  │   └── deduct tokensUsed from tokenBudget
  └── write deferredTasks.json for items not dispatched

CHECK_GATES
  ├── query EvidenceGraph for completeness
  ├── if all gates pass: invoke EvidenceEngine → sign artifacts
  └── else: log open items → schedule next run

DONE
  └── write run_manifest.json
```

---

## Agent Definitions

### Prompt Template Standard

All agents follow the same envelope. The graph slice replaces raw source; the semantic block replaces inferred domain context.

```
[SYSTEM — ~220 tokens, shared per batch]
You are an ISO 26262 ASIL D test engineer generating {AGENT_TYPE} for Java methods.
Respond ONLY with valid JSON matching the schema below.
Do not include explanations, prose, or markdown outside the JSON.

Output schema:
{OUTPUT_SCHEMA}

[USER — ~400–600 tokens, per method]
Method: {methodId} ({methodName})
ASIL Level: {semantic.asilLevel}
Domain: {semantic.domain}
Safety Critical: {semantic.safetyCritical}
Functional Description: {semantic.functionalDescription}
Safe State: {semantic.safeState}

Graph slice:
{graphSlice}

Agent memory (facts from prior agents this run):
{agentMemorySlice}

Task:
{specificTask}

Constraints:
{constraintList}
```

### Token Budget Targets

| Agent / Batch           | System | User  | Max output | Total  |
|-------------------------|--------|-------|------------|--------|
| UnitTestAgent           | 220    | 500   | 800        | 1,520  |
| MCDCAgent               | 220    | 450   | 700        | 1,370  |
| CoverageAgent           | 180    | 350   | 550        | 1,080  |
| MutationAgent           | 180    | 350   | 550        | 1,080  |
| SafetyAgent             | 260    | 450   | 650        | 1,360  |
| Batch A (Unit + Safety) | 260    | 650   | 1,200      | 2,110  |
| Batch B (MCDC + Coverage)| 220   | 550   | 1,000      | 1,770  |
| Batch C (Mutation × 3)  | 180    | 500   | 900        | 1,580  |

---

### Unit Test Agent

Unchanged from v2.0 structurally. In v3.0 the input graph slice additionally carries:

- `semantic` block (domain, safe state, functional description)
- `dfg.decisionInputMap` (source variables for each decision)
- `agentMemorySlice.coveredBranches` (do not regenerate)
- `agentMemorySlice.mcdcPairsGenerated` (do not duplicate)

### MC/DC Agent

Input graph slice additionally carries `dfg.decisionInputMap`. The agent uses `sourceOrigin` to determine whether conditions are directly settable via parameters or require mocking a computed intermediate.

### Coverage Agent

Input graph slice additionally carries:

- `coverage.mcdcCoverage` (skip conditions already MC/DC covered)
- `agentMemorySlice.confirmedUnreachableBranches` (skip, flag as dead code)
- `coverage.riskScore` per missing branch (address highest-risk gap first within method)

### Mutation Agent

Input graph slice additionally carries:

- `mutation.equivalentProbability` per mutant (skip if >= 0.7)
- `mutation.callChain` (generate integration test at `observationPoint` if `integrationTestRequired`)
- `dfg.dataFlowNote` (explains how to control the variable that feeds the mutated condition)

### Safety Agent

Checklist items S-01 through S-14 (from v2.0) are unchanged. In v3.0:

- `semantic.safeState` is validated against the exception handlers: if `safeState` is declared but no handler returns it, this is a FAIL item.
- Call graph is used to check if a safety-critical method is callable from a non-safety-critical context without a safety wrapper — flagged as WARN.

---

## Test Quality Scorer

Unchanged from v2.0. In v3.0, the scorer additionally checks:

- If the test covers a branch listed in `agentMemorySlice.coveredBranches` (duplicate — deduct 10 points).
- If the test is an integration test matching `callChain.observationPoint` for a mutant (bonus + 10 points for appropriate level of abstraction).

---

## Evidence Engine

### Quality Gate Table

| Gate                          | Threshold       | Blocking |
|-------------------------------|-----------------|----------|
| Line coverage                 | >= 95%          | YES      |
| Branch coverage               | >= 95%          | YES      |
| Decision coverage             | >= 95%          | YES      |
| Condition coverage            | >= 95%          | YES      |
| MC/DC coverage                | = 100%          | YES      |
| Mutation score                | >= 90%          | YES      |
| Equivalent mutants excluded   | documented      | YES      |
| Safety FAIL items             | = 0             | YES      |
| Safety WARN items per method  | <= 3            | NO (logged) |
| Requirement coverage          | = 100%          | YES      |
| Test quality score (avg)      | >= 70           | YES      |
| Semantic confidence < 0.8     | = 0 unreviewed  | YES      |
| Graph version in evidence     | present         | YES      |

### Evidence Artifact Set

```
evidence/
└── EP-{runId}/
    ├── verification_summary.json
    ├── traceability_matrix.csv
    ├── test_execution_record.xml
    ├── coverage_report.html
    ├── mutation_report.html
    ├── safety_review.json
    ├── mcdc_matrix.csv
    ├── risk_score_report.json     ← new
    ├── call_graph_snapshot.json   ← new
    ├── semantic_annotation_report.json  ← new (flags low-confidence annotations)
    ├── deferred_tasks.json        ← new (tasks not dispatched this run)
    └── run_manifest.json
```

### Run Manifest Schema

```json
{
  "runId": "run-2025-01-15-003",
  "graphVersion": "1.3.0",
  "commit": "abc123ef",
  "timestamp": "2025-01-15T11:00:00Z",
  "modelVersion": "claude-sonnet-4",
  "orchestratorVersion": "3.0.0",
  "methodsTotal": 52,
  "methodsDirty": 3,
  "methodsCleanCached": 47,
  "methodsSkipped": 2,
  "tasksDispatched": 12,
  "tasksDeferred": 4,
  "totalTokensUsed": 14_200,
  "estimatedTokensNaive": 89_600,
  "tokenSavingPercent": 84,
  "cacheHitRate": 90,
  "averageTestQualityScore": 81,
  "qualityGatesAllPassed": true,
  "lowConfidenceSemanticAnnotations": 0,
  "artifactHash": "sha256:f7a3..."
}
```

---

## Storage Layout

```
.graph/
├── bootstrap_state.json           ← wave tracking, resume state
├── mode.json                      ← BOOTSTRAP | BOOTSTRAP_WITH_DATA | INCREMENTAL | COMPLETE
├── agent_memory_snapshot.json     ← persisted memory layer (flush after each agent call)
├── ast/
├── cfg/
├── dfg/
├── call_graph/
├── semantic/
├── risk/
├── coverage/
├── mutation/
├── requirements/
├── traceability/
├── evidence_graph/
├── kg/                            ← Knowledge Graph embedded store (Memgraph/Neo4j)
│   └── memgraph.db
├── versions/
│   └── v1.3.0_abc123ef/
├── hashes/
│   └── manifest.json
├── cache/
│   └── agent_outputs/             ← cache key = sha256(agentType+methodId+graphHash+semanticHash)
│       ├── UnitTestAgent/
│       ├── MCDCAgent/
│       ├── CoverageAgent/
│       ├── MutationAgent/
│       └── SafetyAgent/
└── evidence/
    └── EP-{runId}/
```

---

## Phase Roadmap

### Phase 1 — Foundation

- **Bootstrap Mode** (full project parse, multi-wave dispatch, `bootstrap_state.json` tracking)
- Mode Detector (`BOOTSTRAP` / `INCREMENTAL` / `COVERAGE_ONLY`)
- JavaParser (full + incremental)
- CFG Builder
- AST Graph Store + Graph Version Store (v0.1.0 snapshot)
- Unit Test Agent + Prompt Template
- Test Quality Scorer
- Bootstrap Manifest (`estimatedRunsToComplete`, wave progress)

### Phase 2 — Semantic and Risk

- Semantic Annotator (annotation-based + heuristic)
- Risk Scorer
- Prioritization Engine (task ranking, no budget cap yet)
- Enriched Coverage Graph (add decision, condition, MC/DC dimensions)
- MC/DC Agent

### Phase 3 — Call Graph and Data Flow

- Call Graph Builder
- DFG Builder
- Enriched Mutation Graph (equivalentProbability, callChain, dataFlowNote)
- Mutation Agent (integration test support)
- Coverage Agent (risk-weighted branch priority)

### Phase 4 — Safety, Traceability, Memory

- Safety Agent (full 14-item checklist, safeState validation)
- Agent Memory Layer
- Batched agent calls (Batch A, B, C)
- Traceability Graph (graph-version-linked)

### Phase 5 — Evidence, Budget, Audit

- Evidence Graph
- Prioritization Engine (token budget enforcement, deferred tasks)
- Full Graph Versioning with retention policy
- Evidence Engine with complete gate table
- Semantic annotation confidence review workflow
- Run manifest with token saving metrics

---

## Success Criteria

### Coverage

| Metric              | Threshold |
|---------------------|-----------|
| Line coverage       | >= 95%    |
| Branch coverage     | >= 95%    |
| Decision coverage   | >= 95%    |
| Condition coverage  | >= 95%    |
| MC/DC coverage      | = 100%    |

### Mutation

| Metric                       | Threshold           |
|------------------------------|---------------------|
| Mutation score               | >= 90%              |
| Equivalent mutants           | Documented and justified |

### Safety

| Metric                      | Threshold |
|-----------------------------|-----------|
| FAIL checklist items        | = 0       |
| WARN items per method       | <= 3      |
| Low-confidence semantic annotations (unreviewed) | = 0 |

### Traceability

| Metric                      | Threshold  |
|-----------------------------|------------|
| Requirement coverage        | = 100%     |
| Graph version in every link | Required   |

### Quality

| Metric                      | Threshold  |
|-----------------------------|------------|
| Average test quality score  | >= 70/100  |

### Token Efficiency

| Metric                                     | Target    |
|--------------------------------------------|-----------|
| Token saving vs naive full-run (steady CI) | >= 80%    |
| Cache hit rate (mature codebase)           | >= 70%    |
| Agent calls per changed method (avg)       | <= 2.5    |
| Tasks deferred due to budget cap           | Tracked in manifest |

### Evidence

| Metric                      | Requirement              |
|-----------------------------|--------------------------|
| Evidence Graph complete     | All gates PASS           |
| Artifacts signed            | run_manifest hash present|
| Graph version in package    | Required                 |
| Deferred tasks logged       | Required                 |

---

## Summary of Changes from v2.0 to v3.0

| # | Gap Addressed                 | Solution                          | Primary Benefit                        |
|---|-------------------------------|-----------------------------------|----------------------------------------|
| 0 | No init strategy for new projects | Bootstrap Mode (full build + multi-wave) | Safe onboarding without token explosion |
| 1 | No semantic meaning in graphs | Semantic Annotator (Module 5)     | Agents receive ASIL/domain/safeState — no hallucination |
| 2 | No call graph                 | Call Graph Builder (Module 3)     | Mutation Agent generates integration tests at right level |
| 3 | No data flow tracking         | DFG Builder (Module 4)            | MC/DC Agent controls root input variables, not derived |
| 4 | Coverage graph too shallow    | Enriched Coverage Graph (Module 9)| Decision, condition, MC/DC coverage all tracked |
| 5 | Mutation graph too sparse     | Enriched Mutation Graph (Module 10)| Equivalent mutants skipped, call chain attached |
| 6 | No risk differentiation       | Risk Scorer (Module 6)            | Safety-critical ASIL-D methods analyzed first |
| 7 | No incremental graph update   | Delta Engine + Incremental Parser | Rebuild only DIRTY nodes, not full project |
| 8 | No graph versioning           | Graph Version Store (Module 8)    | Every evidence package tied to exact graph snapshot |
| 9 | No evidence as graph entity   | Evidence Graph (Module 13)        | Queryable completeness check before signing |
| 10| Agents operate in isolation   | Agent Memory Layer                | No fact computed twice; ~2,000–4,000 tokens saved per run |
| 11| No token budget enforcement   | Prioritization Engine             | Prevents token explosion on large codebases |
| 12| Static orchestration priority | Risk-weighted Prioritization      | HIGH-risk ASIL-D gaps resolved in first run |

## Post-Review Patches (v3.0 → v3.0.1)

| # | Gap Found in Review           | Fix Applied                       | Risk Mitigated                         |
|---|-------------------------------|-----------------------------------|----------------------------------------|
| P1 | Agent Memory Layer lost state on CI interrupt | Added on-disk flush (`agent_memory_snapshot.json`) with atomic rename + resume-on-restart protocol | Up to 30% token budget recovered on interrupted runs |
| P2 | Cache key missing semanticHash — ASIL re-annotation silently reused stale Safety Agent output | Cache key expanded: `sha256(agentType + methodId + graphHash + semanticHash)` | Prevents evidence being signed with wrong-ASIL safety review |
| P3 | Bootstrap Mode ignored existing JaCoCo/PIT reports (migration scenario) | Mode Detector added `BOOTSTRAP_WITH_DATA` path; Risk Scoring uses real factors when reports exist | 40–70% token saving on legacy codebase migration |
| P4 | Knowledge Graph Layer section removed in v3 — no backend spec or query interface | Restored full section with technology choice (Memgraph embedded), node/relationship type table, Cypher query examples, JSON-traversal fallback | Cross-cutting queries (e.g. "ASIL-D + coverage gap + surviving mutant") are computable in one query rather than multi-file join |
