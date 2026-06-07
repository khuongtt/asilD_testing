# ASIL-D Test Orchestrator Architecture v2.0

> **Note:** This file is mislabeled — it contains the v2.0 architecture reference, not v3 review findings. See `ASIL_D_TEST_ORCHESTRATOR_ARCHITECTURE_v3(2).md` for v3 post-review patches (P1–P4).

> Revision notes: This document supersedes v1.0. Major additions include Graph Delta Engine (incremental analysis), Agent Prompt Templates, Test Quality Scoring, Output Cache Layer, Batched Orchestration, and an expanded Safety Agent checklist. All changes are motivated by two goals: higher test case quality and lower token cost per workflow run.

---

## Objective

Build an AI-assisted verification framework for ISO 26262 ASIL D software testing.

Goals:

- Automated Unit Test Generation
- MC/DC Analysis
- Coverage Gap Analysis
- Mutation Testing
- Safety Review
- Traceability
- Evidence Generation

The system shall minimize LLM token consumption by:

1. Transforming source code into structured graphs before invoking AI agents (v1.0 principle — retained).
2. Computing graph deltas so agents only receive changed subgraphs (new in v2.0).
3. Caching agent outputs keyed by graph hash so unchanged methods are never re-analyzed (new in v2.0).
4. Using structured prompt templates with strict token budgets per agent (new in v2.0).
5. Batching related agent calls to amortize system-prompt overhead (new in v2.0).

---

## High-Level Architecture

```
                    Source Code Repository
                               │
                        [git diff / full]
                               │
                               ▼
                     ┌─────────────────┐
                     │ JavaParser Core │
                     └─────────────────┘
                               │
              ┌────────────────┴────────────────┐
              ▼                                 ▼
        AST Graph                        CFG Builder
              │                                 │
              ▼                                 ▼
        AST Store                      Decision Graph
              └────────────────┬────────────────┘
                               ▼
                    ┌──────────────────────┐
                    │  Graph Delta Engine  │  ← NEW
                    └──────────────────────┘
                               │
                    ┌──────────────────────┐
                    │  Knowledge Graph     │
                    │  Layer               │
                    └──────────────────────┘
                               │
       ┌────────────┬──────────┼──────────┬────────────┐
       ▼            ▼          ▼          ▼            ▼
  JaCoCo        PIT        Requirement  Evidence    Agent
  Parser        Parser      Parser      Engine     Output Cache ← NEW
       ▼            ▼          ▼
 Coverage     Mutation   Traceability
  Graph        Graph       Graph
       └────────────┬──────────┘
                    ▼
          ┌──────────────────────┐
          │  ASIL-D Test         │
          │  Orchestrator v2     │
          └──────────────────────┘
                    │
   ┌────────┬───────┬───────┬───────┬────────┐
   ▼        ▼       ▼       ▼       ▼
Unit Test  MC/DC  Coverage Mutation Safety
  Agent    Agent   Agent    Agent   Agent
     │        │       │        │       │
     └────────┴───────┴────────┴───────┘
                    ▼
          ┌──────────────────────┐
          │  Test Quality Scorer │  ← NEW
          └──────────────────────┘
                    │
          ┌──────────────────────┐
          │  Evidence Engine     │
          └──────────────────────┘
```

---

## Core Principles

1. Source code shall be parsed once per changed file (not once per run).
2. Agents shall consume graph slices, not raw source code.
3. Graph deltas shall gate all agent invocations — unchanged nodes skip analysis.
4. Agent outputs shall be cached by (node_id, graph_hash) and reused across runs.
5. Each agent call shall use a structured prompt template with a declared token budget.
6. Related agent tasks shall be batched into a single LLM call where semantics allow.
7. Coverage and mutation analysis shall use graph artifacts, not source re-reads.
8. Traceability shall be maintained through graph relationships.
9. Every artifact shall be auditable and reproducible.
10. Test quality shall be scored before evidence is accepted.

---

## Module 1 — JavaParser Core

### Purpose

Transform Java source files into AST Graph nodes.

### Technology

Recommended: JavaParser, Spoon, Eclipse JDT

### Incremental Parsing (NEW)

Parse only files changed since the last run. Use git diff or file hash comparison.

```
git diff --name-only HEAD~1 HEAD | grep ".java" → changed_files.txt
```

Feed only `changed_files.txt` to the parser. All unchanged AST nodes are loaded from the AST Store without re-parsing.

### Output Schema

```json
{
  "class": "UserService",
  "classHash": "sha256:a3f9...",
  "methods": [
    {
      "id": "M001",
      "name": "createUser",
      "returnType": "UserDto",
      "methodHash": "sha256:b1c2...",
      "parameters": [
        { "name": "request", "type": "CreateUserRequest" }
      ],
      "annotations": ["@Transactional"],
      "throwsTypes": ["UserAlreadyExistsException"],
      "lineStart": 42,
      "lineEnd": 78
    }
  ],
  "dependencies": ["UserRepository", "UserMapper", "EventPublisher"]
}
```

`methodHash` is computed from the method body only (not comments or blank lines). This hash is the cache key for agent output reuse.

### Graph Nodes

- CLASS
- METHOD
- FIELD
- PARAMETER
- ANNOTATION
- EXPRESSION
- STATEMENT
- EXCEPTION_HANDLER (new — required for Safety Agent)

---

## Module 2 — CFG Builder

### Purpose

Generate a Control Flow Graph (Decision Graph) from AST.

### Input

AST Graph (METHOD nodes only)

### Output Schema

```json
{
  "methodId": "M001",
  "methodName": "createUser",
  "decisions": [
    {
      "decisionId": "D001",
      "line": 55,
      "expression": "isValid && hasPermission",
      "conditions": ["isValid", "hasPermission"],
      "conditionCount": 2,
      "requiredMcdcPairs": 2,
      "branches": [
        { "id": "B001", "outcome": "true",  "targetNode": "N005" },
        { "id": "B002", "outcome": "false", "targetNode": "N010" }
      ]
    }
  ],
  "paths": [
    { "pathId": "P001", "nodes": ["N001", "N002", "D001-true", "N005", "N012"] },
    { "pathId": "P002", "nodes": ["N001", "N002", "D001-false", "N010", "N012"] }
  ],
  "cyclomaticComplexity": 3
}
```

### Decision Graph Format

```
D001 [isValid && hasPermission]
 ├── TRUE  → N005
 └── FALSE → N010
```

### Consumers

- MC/DC Agent
- Coverage Agent
- Safety Agent

---

## Module 3 — AST Graph Store

### Purpose

Persist normalized AST and CFG artifacts. Serve as the single source of truth for all agent inputs.

### Directory Structure

```
.graph/
├── ast/
│   ├── UserService.json
│   └── PaymentService.json
├── cfg/
│   ├── UserService_M001.json
│   └── PaymentService_M003.json
├── hashes/
│   └── manifest.json          ← methodHash → last_analyzed timestamp
└── cache/
    └── agent_outputs/         ← (methodId, agentType, graphHash) → output JSON
```

### Hash Manifest Schema

```json
{
  "M001": {
    "methodHash": "sha256:b1c2...",
    "lastAnalyzed": "2025-01-15T10:30:00Z",
    "agentsRun": ["UnitTestAgent", "MCDCAgent", "SafetyAgent"]
  }
}
```

---

## Module 4 — Graph Delta Engine (NEW)

### Purpose

Compute the delta between the current graph state and the previous run. Gate all agent invocations so that only changed or uncovered nodes are analyzed. This is the primary mechanism for token cost reduction across iterative CI runs.

### Delta Computation Algorithm

```
for each method M in current AST:
    currentHash = M.methodHash
    previousHash = manifest[M.id].methodHash

    if currentHash == previousHash:
        status = UNCHANGED → skip all agents, load cached outputs
    elif previousHash is null:
        status = NEW → run all required agents
    else:
        status = MODIFIED → invalidate cache for M, run all agents
        mark downstream traceability nodes as STALE

publish delta_report.json
```

### Delta Report Schema

```json
{
  "runId": "run-2025-01-15-001",
  "changedMethods": ["M001", "M007"],
  "newMethods":     ["M012"],
  "deletedMethods": ["M003"],
  "unchangedMethods": ["M002", "M004", "M005", "M006"],
  "coverageGaps":    ["M001", "M004"],
  "survivingMutants": ["M001", "M007"],
  "staleDependencies": ["REQ-003"]
}
```

### Token Savings Estimate

On a typical CI run where 5 of 50 methods change:

- Without delta engine: 50 methods × ~800 tokens per agent = 40,000 tokens
- With delta engine: 5 changed + 2 coverage gaps (estimated) = 7 methods × ~800 tokens = ~5,600 tokens
- Saving: ~86% reduction on unchanged code

---

## Module 5 — Coverage Graph

### Input

JaCoCo XML Report

### Parser

JaCoCo XML → Coverage Graph JSON

### Output Schema

```json
{
  "methodId": "M001",
  "method": "createUser",
  "coverage": {
    "line": 95,
    "branch": 82,
    "instruction": 91
  },
  "missingBranches": [
    {
      "id": "B003",
      "outcome": "false",
      "line": 61,
      "condition": "email.isBlank()",
      "decision": "D002"
    },
    {
      "id": "B008",
      "outcome": "true",
      "line": 70,
      "condition": "user.isAdmin()",
      "decision": "D004"
    }
  ],
  "missingLines": [72, 73],
  "meetsBranchThreshold": false
}
```

### Purpose

Allow Coverage Agent to generate targeted tests without reading source code. Each missing branch carries enough context (condition text, decision ID) that the agent can formulate an assertion without needing the full method body.

---

## Module 6 — Mutation Graph

### Input

PIT Mutation Report (XML)

### Parser

PIT XML → Mutation Graph JSON

### Output Schema

```json
{
  "methodId": "M001",
  "method": "createUser",
  "mutationScore": 73,
  "survivedMutants": [
    {
      "id": "MUT001",
      "type": "NEGATE_CONDITIONAL",
      "line": 85,
      "description": "negated conditional → removed null check on email",
      "killingTestHint": "assert exception thrown when email is null"
    },
    {
      "id": "MUT002",
      "type": "RETURN_VALUES",
      "line": 91,
      "description": "replaced return value with null",
      "killingTestHint": "assert returned DTO is not null"
    }
  ]
}
```

`killingTestHint` is derived from the mutation type using a rule-based lookup table (no LLM needed). It pre-seeds the Mutation Agent prompt to reduce reasoning tokens.

---

## Module 7 — Requirement Graph

### Purpose

Support ISO 26262 traceability from requirements to methods to tests.

### Input Format

```
REQ-001 | ASIL-D | System shall reject invalid VIN.
REQ-002 | ASIL-C | System shall log all rejected requests.
```

### Output Schema

```json
{
  "requirementId": "REQ-001",
  "asilLevel": "D",
  "description": "System shall reject invalid VIN.",
  "mappedMethods": ["validateVin"],
  "verificationStatus": "PARTIAL",
  "openGaps": ["branch B003.false not covered"]
}
```

---

## Module 8 — Traceability Graph

### Purpose

Maintain a bidirectional Requirement → Method → Test → Coverage → Evidence linkage. Detect stale links when methods change.

### Graph Structure

```
REQ-001
    │
    ▼
validateVin() [M005]
    │
    ├── TC-101 [PASS] → BranchCoverage: B001, B002
    ├── TC-102 [PASS] → BranchCoverage: B003.true
    └── GAP: B003.false [→ invoke Coverage Agent]
    │
    ▼
Evidence Package EP-001
```

### Output Schema

```json
{
  "requirement": "REQ-001",
  "asilLevel": "D",
  "method": "validateVin",
  "methodHash": "sha256:c3d4...",
  "tests": [
    { "id": "TC-101", "status": "PASS", "covers": ["B001", "B002"] },
    { "id": "TC-102", "status": "PASS", "covers": ["B003.true"] }
  ],
  "openGaps": ["B003.false"],
  "traceabilityStatus": "INCOMPLETE",
  "evidenceId": null
}
```

---

## Module 9 — Agent Output Cache (NEW)

### Purpose

Persist agent-generated test cases, analyses, and recommendations keyed by a stable hash. Avoid re-invoking the LLM for identical graph inputs.

### Cache Key

```
cache_key = sha256( agentType + "|" + methodId + "|" + graphHash )
```

`graphHash` is derived from the method's AST node + its CFG node + relevant coverage/mutation data. If any input changes, the hash changes and the cache misses.

### Cache Directory

```
.graph/cache/agent_outputs/
├── UnitTestAgent/
│   └── M001_sha256b1c2.json
├── MCDCAgent/
│   └── M001_sha256b1c2.json
└── SafetyAgent/
    └── M001_sha256b1c2.json
```

### Cache Entry Schema

```json
{
  "cacheKey": "sha256:e9f1...",
  "agentType": "UnitTestAgent",
  "methodId": "M001",
  "graphHash": "sha256:b1c2...",
  "generatedAt": "2025-01-15T10:35:00Z",
  "output": {
    "tests": [ "...test source code..." ],
    "qualityScore": 87,
    "tokensUsed": 812
  },
  "ttlDays": 30
}
```

### Cache Hit Rate Target

On a mature codebase with CI running daily: cache hit rate ≥ 70% of method-agent pairs per run.

---

## Knowledge Graph Layer

### Purpose

Create a unified queryable representation of all graph artifacts.

### Node Types

- CLASS
- METHOD
- DECISION
- BRANCH
- CONDITION
- REQUIREMENT
- TESTCASE
- COVERAGE
- MUTANT
- EVIDENCE
- SAFETYISSUE (new)

### Relationship Types

- CALLS
- DEPENDS_ON
- TESTS
- IMPLEMENTS
- COVERS
- KILLS
- TRACES_TO
- RAISED_BY (exception relationships — new)
- VALIDATES (requirement validation — new)

### Graph Query Examples

```cypher
-- Find all methods with ASIL-D requirements and branch coverage below 95%
MATCH (r:REQUIREMENT {asilLevel: "D"})-[:TRACES_TO]->(m:METHOD)
      -[:HAS_COVERAGE]->(c:COVERAGE)
WHERE c.branch < 95
RETURN m.name, c.branch, r.requirementId

-- Find survived mutants in methods linked to unmet requirements
MATCH (r:REQUIREMENT {verificationStatus: "PARTIAL"})
      -[:TRACES_TO]->(m:METHOD)-[:HAS_MUTANT]->(mut:MUTANT {status: "SURVIVED"})
RETURN m.name, mut.id, mut.type
```

---

## ASIL-D Test Orchestrator v2

### Responsibilities

- Load delta report to determine scope of current run
- Check agent output cache before invoking any LLM
- Apply priority rules to sequence agent invocations
- Batch compatible agent calls
- Invoke quality scorer before accepting test outputs
- Coordinate feedback loops for low-quality outputs
- Produce evidence package

### Priority Rules

ASIL-D requirements demand strict ordering to ensure safety-critical gaps are addressed first:

```
Priority 1 (SAFETY):   Safety Agent findings with severity HIGH or CRITICAL
Priority 2 (MCDC):     Methods with MC/DC coverage < 100%
Priority 3 (BRANCH):   Methods with branch coverage < 95%
Priority 4 (MUTATION): Methods with mutation score < 90%
Priority 5 (UNIT):     Methods with no tests at all
Priority 6 (TRACE):    Requirements with traceability gaps
```

### Batching Rules (NEW)

Group agent calls by compatibility to reduce per-call system prompt overhead:

```
Batch A (AST-only input):   UnitTestAgent + SafetyAgent for same method
                             → single LLM call, shared system prompt (~30% token saving)

Batch B (CFG input):        MCDCAgent + CoverageAgent for same method
                             → single LLM call, shared context

Batch C (mutation only):    MutationAgent handles all survived mutants in one call
                             → up to 5 mutants per call, batched
```

Do NOT batch:

- Agents from different priority levels in the same call (results become interleaved and harder to parse)
- Safety Agent with Mutation Agent (different reasoning modes)

### Orchestration Decision Table

```
┌─────────────────────────┬──────────────────────────────────────────────┐
│ Condition               │ Action                                        │
├─────────────────────────┼──────────────────────────────────────────────┤
│ Method in cache         │ Load cached output, skip LLM invocation       │
│ Method UNCHANGED        │ Load cached output, update traceability only  │
│ Safety finding CRITICAL │ Invoke Safety Agent first, block evidence gen │
│ MC/DC < 100%            │ Invoke MC/DC Agent (Batch B)                  │
│ Branch < 95%            │ Invoke Coverage Agent (Batch B)               │
│ Mutation < 90%          │ Invoke Mutation Agent (Batch C)               │
│ No tests exist          │ Invoke Unit Test Agent (Batch A)              │
│ Quality score < 70      │ Re-invoke agent with feedback prompt          │
│ Quality score ≥ 70      │ Accept output, update traceability            │
│ All criteria met        │ Generate evidence package                     │
└─────────────────────────┴──────────────────────────────────────────────┘
```

### Orchestrator State Machine

```
IDLE
  │
  ▼
LOAD_DELTA
  │
  ├── no changes → GENERATE_EVIDENCE (if all criteria met)
  │
  ▼
CHECK_CACHE
  │
  ├── cache hit → UPDATE_TRACEABILITY → loop
  │
  ▼
PRIORITIZE_AGENTS
  │
  ▼
BATCH_CALLS
  │
  ▼
INVOKE_LLM
  │
  ▼
SCORE_OUTPUT
  │
  ├── score < 70 → REFINE (max 2 retries) → INVOKE_LLM
  │
  ▼
ACCEPT_OUTPUT
  │
  ▼
UPDATE_CACHE
  │
  ▼
UPDATE_TRACEABILITY
  │
  ▼
CHECK_CRITERIA
  │
  ├── criteria not met → PRIORITIZE_AGENTS (next gap)
  │
  ▼
GENERATE_EVIDENCE
  │
  ▼
DONE
```

---

## Agent Definitions

### Prompt Template Standard (NEW)

All agent prompts follow a common structure to minimize token waste and maximize determinism.

```
[SYSTEM — shared, ~200 tokens]
You are an ISO 26262 ASIL D test engineer. You generate {AGENT_TYPE} for Java methods.
Respond ONLY with valid JSON matching the output schema below.
Do NOT include explanations, markdown, or text outside the JSON object.

Output schema:
{OUTPUT_SCHEMA}

[USER — per-call, ~400–600 tokens]
Method: {methodId}
Context: {graphSlice}
Task: {specificTask}
Constraints: {constraintList}
```

Token budget targets per agent call:

| Agent         | System prompt | User prompt | Max output | Total target |
|---------------|--------------|-------------|------------|--------------|
| UnitTestAgent | 200          | 500         | 800        | 1,500        |
| MCDCAgent     | 200          | 400         | 600        | 1,200        |
| CoverageAgent | 150          | 300         | 500        | 950          |
| MutationAgent | 150          | 300         | 500        | 950          |
| SafetyAgent   | 250          | 400         | 600        | 1,250        |
| Batch A (Unit+Safety) | 250 | 600       | 1,100      | 1,950        |
| Batch B (MCDC+Coverage) | 200 | 500     | 900        | 1,600        |

Batch calls save ~35–40% compared to separate invocations.

---

### Unit Test Agent

#### Input (Graph Slice)

```json
{
  "methodId": "M001",
  "methodName": "createUser",
  "returnType": "UserDto",
  "parameters": [{ "name": "request", "type": "CreateUserRequest" }],
  "throwsTypes": ["UserAlreadyExistsException"],
  "dependencies": ["UserRepository", "UserMapper"],
  "annotations": ["@Transactional"],
  "decisions": [
    { "decisionId": "D001", "expression": "isValid && hasPermission", "conditionCount": 2 }
  ],
  "existingTestIds": [],
  "domainHints": ["VIN validation", "reject duplicates"]
}
```

`domainHints` are extracted from the Requirement Graph and attached to the graph slice before the agent is called. This eliminates the need for the agent to infer business context from raw code.

#### Responsibilities

- Generate JUnit5 test class skeleton
- Generate happy-path test
- Generate exception tests for each declared `throwsType`
- Generate boundary tests for each numeric parameter
- Generate null-input tests for each object parameter
- Generate one test per decision branch (coordinate with Coverage Agent to avoid duplication)
- Generate Mockito stubs for each dependency
- Apply ASIL-D naming convention: `test_{methodName}_{scenario}_{expectedOutcome}`

#### Output Schema

```json
{
  "methodId": "M001",
  "tests": [
    {
      "testId": "TC-201",
      "name": "test_createUser_validRequest_returnsDto",
      "type": "HAPPY_PATH",
      "coveredBranches": ["B001.true"],
      "code": "@Test\nvoid test_createUser_validRequest_returnsDto() { ... }"
    },
    {
      "testId": "TC-202",
      "name": "test_createUser_nullEmail_throwsException",
      "type": "EXCEPTION",
      "coveredBranches": ["B003.false"],
      "code": "@Test\nvoid test_createUser_nullEmail_throwsException() { ... }"
    }
  ],
  "mocksRequired": ["UserRepository", "UserMapper"],
  "qualityScore": null
}
```

`qualityScore` is populated by the Test Quality Scorer after generation.

---

### MC/DC Agent

#### Input (Graph Slice)

```json
{
  "methodId": "M001",
  "decisionId": "D001",
  "expression": "isValid && hasPermission",
  "conditions": ["isValid", "hasPermission"],
  "conditionCount": 2,
  "requiredMcdcPairs": 2,
  "existingMcdcTests": []
}
```

#### Responsibilities

- Generate truth table for all 2^n condition combinations
- Identify the minimum set of test pairs satisfying MC/DC (each condition independently affects outcome)
- Generate one JUnit5 test per MC/DC pair
- Flag any condition that is invariant (always true or always false) — this is a Safety Agent finding
- Validate that MC/DC pairs are non-redundant

#### Truth Table Output

```json
{
  "decisionId": "D001",
  "expression": "isValid && hasPermission",
  "truthTable": [
    { "row": 1, "isValid": false, "hasPermission": false, "outcome": false },
    { "row": 2, "isValid": false, "hasPermission": true,  "outcome": false },
    { "row": 3, "isValid": true,  "hasPermission": false, "outcome": false },
    { "row": 4, "isValid": true,  "hasPermission": true,  "outcome": true  }
  ],
  "mcdcPairs": [
    { "condition": "isValid",      "pairRows": [3, 4], "rationale": "hasPermission=true, isValid varies" },
    { "condition": "hasPermission", "pairRows": [2, 4], "rationale": "isValid=false→true irrelevant; use rows where isValid=true" }
  ],
  "tests": [
    {
      "testId": "TC-MCDC-001",
      "name": "test_createUser_D001_isValid_independentEffect",
      "pairId": "isValid",
      "code": "@Test\nvoid test_createUser_D001_isValid_independentEffect() { ... }"
    }
  ]
}
```

---

### Coverage Agent

#### Input (Graph Slice)

```json
{
  "methodId": "M001",
  "missingBranches": [
    {
      "id": "B003",
      "outcome": "false",
      "condition": "email.isBlank()",
      "line": 61
    }
  ],
  "existingTestIds": ["TC-201", "TC-202"],
  "domainHints": ["reject blank email"]
}
```

#### Responsibilities

- For each missing branch, generate exactly one targeted test
- Do not duplicate tests already listed in `existingTestIds`
- Provide a brief rationale for why this branch was uncovered
- If a missing branch is unreachable (dead code), flag it as a Safety finding instead of generating a test

#### Output Schema

```json
{
  "methodId": "M001",
  "generatedTests": [
    {
      "testId": "TC-COV-001",
      "targetBranch": "B003.false",
      "name": "test_createUser_blankEmail_rejected",
      "code": "@Test\nvoid test_createUser_blankEmail_rejected() { ... }",
      "rationale": "Branch B003.false (email.isBlank() == false) was not triggered by existing tests."
    }
  ],
  "unreachableBranches": [],
  "safetyFlags": []
}
```

---

### Mutation Agent

#### Input (Graph Slice)

```json
{
  "methodId": "M001",
  "survivedMutants": [
    {
      "id": "MUT001",
      "type": "NEGATE_CONDITIONAL",
      "line": 85,
      "description": "negated conditional → removed null check on email",
      "killingTestHint": "assert exception thrown when email is null"
    }
  ]
}
```

`killingTestHint` is pre-populated from a rule lookup table (no LLM needed for hints) to reduce reasoning load.

#### Responsibilities

- For each survived mutant, analyze why existing tests did not kill it
- Generate one targeted kill test per mutant
- Prioritize mutants in ASIL-D methods (Safety Agent cross-check)

#### Output Schema

```json
{
  "methodId": "M001",
  "killTests": [
    {
      "testId": "TC-MUT-001",
      "targetMutant": "MUT001",
      "name": "test_createUser_nullEmail_nullCheckEnforced",
      "analysisNote": "No existing test asserts the exception when email==null; adding explicit assertion.",
      "code": "@Test\nvoid test_createUser_nullEmail_nullCheckEnforced() { ... }"
    }
  ]
}
```

---

### Safety Agent

#### Input (Graph Slice)

```json
{
  "methodId": "M001",
  "methodName": "createUser",
  "decisions": [...],
  "exceptionHandlers": [
    { "type": "Exception", "action": "log_and_rethrow" }
  ],
  "nullableParameters": ["request"],
  "returnType": "UserDto",
  "annotations": ["@Transactional"],
  "asilLevel": "D"
}
```

#### Checklist (Structured — NEW)

The Safety Agent evaluates the following checklist items. Each item produces a PASS, WARN, or FAIL result. FAIL items block evidence generation.

```
ID     Item                                              ASIL-D Threshold
────────────────────────────────────────────────────────────────────────
S-01   Null check on all nullable parameters            FAIL if absent
S-02   Return value null check before dereferencing     FAIL if absent
S-03   Integer overflow guard on arithmetic ops         FAIL if absent
S-04   Integer underflow guard on decrement ops         FAIL if absent
S-05   Exception handler specificity (no bare catch)    WARN if caught Exception; FAIL if caught Throwable
S-06   State transition validity check                  FAIL if invalid transition possible
S-07   Array/collection bounds check                    FAIL if absent
S-08   Defensive copy on mutable input parameters       WARN if absent for ASIL-D
S-09   Thread-safety annotation or synchronization      FAIL if shared state without guard
S-10   Resource leak check (streams, connections)       FAIL if not in try-with-resources
S-11   Invariant condition check (always true/false)    WARN — potential dead branch
S-12   Error return code checked at every call site     FAIL if unchecked
S-13   Magic number replacement with named constant     WARN
S-14   Timeout on blocking calls                        FAIL if absent in @Transactional methods
```

#### Output Schema

```json
{
  "methodId": "M001",
  "asilLevel": "D",
  "checklistResults": [
    { "id": "S-01", "status": "PASS", "detail": "Null check present at line 45." },
    { "id": "S-05", "status": "WARN", "detail": "Catch block catches generic Exception at line 62." },
    { "id": "S-10", "status": "FAIL", "detail": "InputStream not closed in finally block at line 70." }
  ],
  "failCount": 1,
  "warnCount": 1,
  "blockEvidence": true,
  "remediationTests": [
    {
      "testId": "TC-SAFE-001",
      "name": "test_createUser_resourceLeak_inputStreamClosed",
      "code": "@Test\nvoid test_createUser_resourceLeak_inputStreamClosed() { ... }"
    }
  ]
}
```

---

## Test Quality Scorer (NEW)

### Purpose

Score each agent output before it is accepted into the test suite and traceability graph. Prevents low-quality or semantically empty tests from inflating coverage metrics.

### Scoring Dimensions

```
Dimension             Weight  Criteria
──────────────────────────────────────────────────────────────────────
Branch assertion        25%   Each test has at least one assert statement
Assertion specificity   20%   Asserts check concrete values, not just non-null
Exception assertion     15%   Exception tests use assertThrows with type+message
Mock verification       10%   Mockito stubs are verified (verify() called)
Naming convention       10%   Follows test_{method}_{scenario}_{expected} pattern
Boundary coverage        10%   At least one test uses min/max/zero/negative input
No duplicate logic       10%   Test body is not identical to an existing test
──────────────────────────────────────────────────────────────────────
Maximum score          100
Acceptance threshold    70
```

### Scoring Process

Scoring is performed by a deterministic rule engine (no LLM). Checks are applied to the generated test code as AST nodes.

```
score = sum(dimension_weight × dimension_pass_flag)
if score < 70:
    flag for re-invocation with feedback prompt
    feedback_prompt includes specific failed dimensions
    max 2 re-invocations per method per run
    if score still < 70 after 2 retries: log as MANUAL_REVIEW
```

### Feedback Prompt Template

```
[SYSTEM — same as original]

[USER]
Previous output scored {score}/100.
Failed dimensions: {failedDimensionList}

Specific issues:
- {issue1}
- {issue2}

Please regenerate the tests for method {methodId} correcting the above issues.
Retain tests that passed: {passingTestIds}
```

Feedback prompts are intentionally compact — they reference the original context by `methodId` and list only the delta, not the full graph slice again.

---

## Evidence Engine

### Responsibilities

- Verify all quality gates are met before generating evidence
- Produce a Verification Summary Report
- Produce a Traceability Matrix
- Produce a Test Execution Record
- Sign all artifacts with a run hash for auditability

### Quality Gates (All must PASS)

```
Gate                        Threshold       Blocking
────────────────────────────────────────────────────
Line coverage               >= 95%          YES
Branch coverage             >= 95%          YES
MC/DC coverage              = 100%          YES
Mutation score              >= 90%          YES
Safety FAIL items           = 0             YES
Safety WARN items           <= 3 per method NO (logged)
Requirement coverage        = 100%          YES
Test quality score (avg)    >= 70           YES
```

### Evidence Artifact Set

```
evidence/
├── EP-{runId}/
│   ├── verification_summary.json
│   ├── traceability_matrix.csv
│   ├── test_execution_record.xml
│   ├── coverage_report.html     (from JaCoCo)
│   ├── mutation_report.html     (from PIT)
│   ├── safety_review.json
│   ├── mcdc_matrix.csv
│   └── run_manifest.json        (hashes, timestamps, model version)
```

### Run Manifest Schema

```json
{
  "runId": "run-2025-01-15-001",
  "timestamp": "2025-01-15T11:00:00Z",
  "modelVersion": "claude-sonnet-4",
  "orchestratorVersion": "2.0.0",
  "methodsAnalyzed": 7,
  "methodsCachedSkipped": 43,
  "totalTokensUsed": 9840,
  "estimatedTokensWithoutCache": 56000,
  "tokenSavingPercent": 82,
  "qualityGatesAllPassed": true,
  "artifactHash": "sha256:f7a3..."
}
```

---

## Storage Layout

```
.graph/
├── ast/
│   └── {ClassName}.json
├── cfg/
│   └── {ClassName}_{methodId}.json
├── hashes/
│   └── manifest.json
├── coverage/
│   └── {methodId}_coverage.json
├── mutation/
│   └── {methodId}_mutation.json
├── requirements/
│   └── requirements.json
├── traceability/
│   └── {requirementId}_trace.json
├── cache/
│   └── agent_outputs/
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

- JavaParser core
- CFG Builder
- AST Graph Store
- Unit Test Agent (with prompt template)
- Test Quality Scorer (rule engine only)
- Basic agent output cache

### Phase 2 — Coverage

- JaCoCo Parser
- Coverage Graph
- Coverage Agent
- Graph Delta Engine (file-level diff)
- MC/DC Agent

### Phase 3 — Mutation

- PIT Parser
- Mutation Graph
- Mutation Agent
- Kill hint lookup table
- Method-level delta engine

### Phase 4 — Safety and Traceability

- Safety Agent with structured checklist (S-01 through S-14)
- Requirement Parser
- Requirement Graph
- Traceability Graph
- Batched agent calls (Batch A, B, C)

### Phase 5 — ASIL D Evidence and Optimization

- Evidence Engine
- Verification Summary
- Traceability Matrix
- Feedback loop for quality < 70
- Cache TTL management
- Token usage reporting in run manifest

---

## Success Criteria

### Coverage

| Metric           | Threshold |
|------------------|-----------|
| Line coverage    | >= 95%    |
| Branch coverage  | >= 95%    |
| MC/DC coverage   | = 100%    |

### Mutation

| Metric           | Threshold |
|------------------|-----------|
| Mutation score   | >= 90%    |

### Safety

| Metric                  | Threshold |
|-------------------------|-----------|
| FAIL checklist items    | = 0       |
| WARN items per method   | <= 3      |

### Traceability

| Metric                  | Threshold |
|-------------------------|-----------|
| Requirement coverage    | = 100%    |

### Quality

| Metric                  | Threshold |
|-------------------------|-----------|
| Average test quality    | >= 70/100 |

### Evidence

| Metric                  | Requirement |
|-------------------------|-------------|
| Verification package    | Generated and signed |
| Traceability matrix     | All REQs covered |
| Run manifest            | Present in every evidence package |

### Token Efficiency (new)

| Metric                              | Target    |
|-------------------------------------|-----------|
| Token saving vs v1.0 (steady-state CI) | >= 80% |
| Cache hit rate (mature codebase)    | >= 70%    |
| Agent calls per changed method (avg)| <= 2.5   |

---

## Expected Benefits vs v1.0

| Dimension                 | v1.0 Approach                     | v2.0 Approach                                    |
|---------------------------|-----------------------------------|--------------------------------------------------|
| Source code reading       | Agents read raw source each run   | Agents receive pre-built graph slices only       |
| Re-analysis scope         | Full codebase every run           | Delta: only changed/uncovered methods            |
| Token cost per CI run     | High (all methods, every run)     | Reduced ~80–86% on steady-state runs             |
| Agent call batching       | One agent = one LLM call          | Compatible agents batched, ~35% additional saving|
| Output reuse              | None                              | Cache by (methodId, graphHash), 70%+ hit rate    |
| Test quality control      | None                              | Quality Scorer gates acceptance; feedback loop   |
| Safety review             | Ad-hoc checklist                  | Structured 14-item checklist, FAIL blocks evidence|
| MC/DC oracle              | Agent self-validates              | Truth table computed from CFG, agent validates pairs|
| Token cost predictability | Variable                          | Token budget declared per agent type             |
| Auditability              | Graph artifacts                   | Graph artifacts + run manifest with token trace  |
| ISO 26262 alignment       | Partial                           | Full (evidence package includes quality scores)  |
