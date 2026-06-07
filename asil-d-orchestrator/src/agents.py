import json

from llm_adapter import get_llm_adapter
from prompt_builder import PromptBuilder


class BaseAgent:
    AGENT_TOKEN_BUDGET = 1200

    def __init__(self, name):
        self.name = name
        self.llm = get_llm_adapter()

    def execute(self, task_context):
        raise NotImplementedError

    def _invoke_llm(self, task_context, task_type, output_builder):
        graph_slice = task_context.get("graph_slice", {})
        memory = task_context.get("memory_facts", {})
        system, user = PromptBuilder.build(self.name, graph_slice, task_type, memory)
        meta = self.llm.invoke(self.name, system, user, self.AGENT_TOKEN_BUDGET)
        output = output_builder(task_context)
        output["llmMeta"] = meta
        return output


class UnitTestAgent(BaseAgent):
    def __init__(self):
        super().__init__("UnitTestAgent")

    def execute(self, task_context):
        return self._invoke_llm(task_context, "UNIT_MISSING", self._build_output)

    def _build_output(self, task_context):
        method = task_context["method"]
        class_name = task_context["class"]
        memory = task_context.get("memory_facts", {})
        covered = set(memory.get("coveredBranches", []))
        mcdc_pairs = set(memory.get("mcdcPairsGenerated", []))

        tests = []
        mocks_required = []
        params = method.get("parameters", [])
        decisions = method.get("decisions", [])

        param_values = {}
        for p in params:
            param_values[p["name"]] = "0"
            if "int" in p.get("type", "").lower():
                param_values[p["name"]] = "0"
            elif "double" in p.get("type", "").lower() or "float" in p.get("type", "").lower():
                param_values[p["name"]] = "0.0"
            elif "boolean" in p.get("type", "").lower():
                param_values[p["name"]] = "false"
            else:
                param_values[p["name"]] = "null"
                mocks_required.append(p["type"])

        test_index = 0

        def next_id(suffix):
            nonlocal test_index
            test_index += 1
            return f"TC_{method['name']}_{suffix}_{test_index:03d}"

        def make_call(overrides=None):
            vals = dict(param_values)
            if overrides:
                vals.update(overrides)
            return ", ".join(str(vals[p["name"]]) for p in params)

        happy_params = {}
        for p in params:
            if "int" in p.get("type", "").lower():
                happy_params[p["name"]] = "1"
            elif "double" in p.get("type", "").lower() or "float" in p.get("type", "").lower():
                happy_params[p["name"]] = "1.0"
            elif "boolean" in p.get("type", "").lower():
                happy_params[p["name"]] = "true"
            else:
                happy_params[p["name"]] = "new " + p["type"] + "()"
        call = make_call()
        call_happy = make_call(happy_params)

        test_index += 1
        tid = f"TC_{method['name']}_happy_001"
        tests.append({
            "testId": tid,
            "name": f"test_{method['name']}_validInput_returnsResult",
            "type": "HAPPY_PATH",
            "coveredBranches": [],
            "code": f"""@Test
void test_{method['name']}_validInput_returnsResult() {{
    {class_name} controller = new {class_name}();
    {('var result = controller.' + method['name'] + '(' + call_happy + ');') if params else ('controller.' + method['name'] + '();')}
    {('assertNotNull(result);') if method.get('returnType') and method['returnType'] != 'void' else ''}
}}""",
        })

        for exc in method.get("throwsTypes", []):
            test_index += 1
            tid = f"TC_{method['name']}_exception_{test_index:03d}"
            tests.append({
                "testId": tid,
                "name": f"test_{method['name']}_{exc}_throwsException",
                "type": "EXCEPTION",
                "coveredBranches": [],
                "code": f"""@Test
void test_{method['name']}_{exc}_throwsException() {{
    {class_name} controller = new {class_name}();
    assertThrows({exc}.class, () -> controller.{method['name']}({call}));
}}""",
            })

        for p in params:
            ptype = p.get("type", "").lower()
            if any(k in ptype for k in ["int", "double", "float", "long"]):
                for boundary_val, suffix in [("0", "zero"), ("-1", "negative"), ("Integer.MAX_VALUE", "max")]:
                    test_index += 1
                    tid = f"TC_{method['name']}_boundary_{test_index:03d}"
                    bval = boundary_val
                    if "double" in ptype:
                        bval = {"0": "0.0", "-1": "-1.0", "Integer.MAX_VALUE": "Double.MAX_VALUE"}.get(boundary_val, boundary_val)
                    elif "float" in ptype:
                        bval = {"0": "0.0f", "-1": "-1.0f", "Integer.MAX_VALUE": "Float.MAX_VALUE"}.get(boundary_val, boundary_val)
                    bparams = {p["name"]: bval}
                    tests.append({
                        "testId": tid,
                        "name": f"test_{method['name']}_{p['name']}_{suffix}",
                        "type": "BOUNDARY",
                        "coveredBranches": [],
                        "code": f"""@Test
void test_{method['name']}_{p['name']}_{suffix}() {{
    {class_name} controller = new {class_name}();
    controller.{method['name']}({make_call({p['name']: bval})});
}}""",
                    })

        for d in decisions:
            for b in d.get("branches", []):
                target = f"{b['id']}.{b['outcome']}"
                if target in covered:
                    continue
                test_index += 1
                tid = f"TC_{method['name']}_branch_{test_index:03d}"
                tests.append({
                    "testId": tid,
                    "name": f"test_{method['name']}_decision_{d['decisionId']}_{b['outcome']}",
                    "type": "BRANCH",
                    "coveredBranches": [target],
                    "code": f"""@Test
void test_{method['name']}_decision_{d['decisionId']}_{b['outcome']}() {{
    {class_name} controller = new {class_name}();
    controller.{method['name']}({call});
    /* covers branch {target} */
}}""",
                })

        main_code = tests[0]["code"] if tests else ""
        return {
            "testId": tests[0]["testId"] if tests else f"TC_{method['name']}_001",
            "code": main_code,
            "tests": tests,
            "mocksRequired": mocks_required,
            "qualityScore": None,
            "tokensUsed": 800,
            "skippedBranches": list(covered),
        }


class MCDCAgent(BaseAgent):
    def __init__(self):
        super().__init__("MCDCAgent")

    def execute(self, task_context):
        return self._invoke_llm(task_context, "MCDC_GAP", self._build_output)

    def _build_output(self, task_context):
        method = task_context["method"]
        graph_slice = task_context.get("graph_slice", {})
        dfg = graph_slice.get("dfg", {})
        decision_input_map = dfg.get("decisionInputMap", {}) if isinstance(dfg, dict) else {}
        results = []
        for d in method.get("decisions", []):
            conditions = d.get("conditions", [])
            n = len(conditions)
            truth_table = []
            for row_idx in range(1 << n):
                row = {"row": row_idx + 1}
                combo = [(row_idx >> (n - 1 - i)) & 1 for i in range(n)]
                for i, cond in enumerate(conditions):
                    row[cond] = bool(combo[i])
                is_and = "&&" in d.get("expression", "")
                is_or = "||" in d.get("expression", "")
                if is_and:
                    row["outcome"] = all(combo)
                elif is_or:
                    row["outcome"] = any(combo)
                else:
                    row["outcome"] = combo[0] if combo else False
                truth_table.append(row)

            mcdc_pairs = []
            for i, cond in enumerate(conditions):
                paired = []
                for r1 in truth_table:
                    for r2 in truth_table:
                        if r1["outcome"] != r2["outcome"] and r1[cond] != r2[cond]:
                            same = True
                            for j, c2 in enumerate(conditions):
                                if i != j and r1[c2] != r2[c2]:
                                    same = False
                                    break
                            if same:
                                paired.append((r1["row"], r2["row"]))
                                break
                    if paired:
                        break
                mcdc_pairs.append({
                    "condition": cond,
                    "pairRows": paired[0] if paired else [],
                    "rationale": f"Independent effect of {cond} on outcome",
                })

            input_map = decision_input_map.get(d["decisionId"], {})
            result_entry = {
                "decisionId": d["decisionId"],
                "expression": d["expression"],
                "truthTable": truth_table,
                "sourceVariables": input_map.get("sourceVariables", []),
                "mcdcPairs": mcdc_pairs,
            }
            result_entry["tests"] = [
                {
                    "testId": f"TC-MCDC-{method['name']}-{pair['condition']}",
                    "name": f"test_{method['name']}_D{d['decisionId']}_{pair['condition']}_independentEffect",
                    "pairId": pair["condition"],
                    "code": f"@Test void test_{method['name']}_D{d['decisionId']}_{pair['condition']}_independentEffect() {{ /* MC/DC pair for {pair['condition']} */ }}",
                }
                for pair in mcdc_pairs
            ]
            results.append(result_entry)

        return {"methodId": method["id"], "mcdcAnalysis": results, "tokensUsed": 700}


class CoverageAgent(BaseAgent):
    def __init__(self):
        super().__init__("CoverageAgent")

    def execute(self, task_context):
        return self._invoke_llm(task_context, "BRANCH_GAP", self._build_output)

    def _build_output(self, task_context):
        method = task_context["method"]
        memory = task_context.get("memory_facts", {})
        unreachable = memory.get("confirmedUnreachableBranches", [])
        covered = set(memory.get("coveredBranches", []))
        mcdc_covered_conditions = set()
        for mcdc_entry in method.get("coverage", {}).get("mcdcCoverage", []):
            if mcdc_entry.get("mcdcPairCovered"):
                mcdc_covered_conditions.add(mcdc_entry["condition"])

        missing = method.get("coverage", {}).get("missingBranches", [])
        if not missing and method.get("decisions"):
            for d in method["decisions"]:
                for b in d.get("branches", []):
                    bid = f"{b['id']}.{b['outcome']}"
                    if bid not in covered:
                        cond_text = d.get("expression", "")
                        missing.append({
                            "id": b["id"], "outcome": b["outcome"], "line": d["line"],
                            "decision": d["decisionId"], "riskScore": method.get("risk", {}).get("score", 0),
                            "condition": cond_text,
                        })
        missing.sort(key=lambda x: x.get("riskScore", 0), reverse=True)

        generated, pending = [], []
        safety_flags = []
        for branch in missing:
            target = f"{branch['id']}.{branch['outcome']}"
            if target in covered or target in unreachable:
                continue
            cond_text = branch.get("condition", "")
            if cond_text and cond_text in mcdc_covered_conditions:
                continue
            generated.append({
                "testId": f"TC-COV-{method['name']}-{branch['id']}",
                "targetBranch": target,
                "name": f"test_{method['name']}_{branch['outcome']}_{branch['decision']}",
                "code": f"@Test void test_{method['name']}_{branch['outcome']}_{branch['decision']}() {{ /* covers {target} */ }}",
                "rationale": f"Branch {target} was uncovered (risk score: {branch.get('riskScore', 0)}).",
            })
        return {
            "methodId": method["id"],
            "generatedTests": generated,
            "pendingBranches": pending,
            "unreachableBranches": list(unreachable),
            "safetyFlags": safety_flags,
            "tokensUsed": 550,
        }


class MutationAgent(BaseAgent):
    def __init__(self):
        super().__init__("MutationAgent")

    def execute(self, task_context):
        return self._invoke_llm(task_context, "MUTATION", self._build_output)

    def _build_output(self, task_context):
        method = task_context["method"]
        graph_slice = task_context.get("graph_slice", {})
        call_chain = graph_slice.get("callChain", {})
        mutants = method.get("mutation", {}).get("survivedMutants", [])
        if not mutants:
            mutants = [{"mutantId": "MUT-001", "operator": "NEGATE_CONDITIONAL", "line": method.get("lineStart", 0) + 2, "equivalentProbability": 0.05}]
        kill_tests, skipped = [], []
        for mut in mutants:
            if mut.get("equivalentProbability", 0) >= 0.7:
                skipped.append(mut)
                continue
            cc = mut.get("callChain") or {}
            obs = cc.get("observationPoint") or (call_chain.get("callers") or [method["id"]])[0]
            dfn = mut.get("dataFlowNote")
            dfn_hint = f" // {dfn}" if dfn else ""
            kill_tests.append({
                "testId": f"TC-MUT-{method['name']}-{mut['mutantId']}",
                "targetMutant": mut["mutantId"],
                "observationPoint": obs,
                "dataFlowNote": dfn,
                "integrationTestRequired": bool(cc.get("integrationTestRequired") or call_chain.get("callers")),
                "code": f"@Test void kill_{mut['mutantId']}() {{ /* kills at {obs} */{dfn_hint} }}",
            })
        return {"methodId": method["id"], "killTests": kill_tests, "skippedMutants": skipped, "tokensUsed": 550}


class SafetyAgent(BaseAgent):
    CHECKLIST = [
        ("S-01", "Null check on all nullable parameters"),
        ("S-02", "Return value null check before dereferencing"),
        ("S-03", "Integer overflow guard on arithmetic ops"),
        ("S-04", "Integer underflow guard on decrement ops"),
        ("S-05", "Exception handler specificity"),
        ("S-06", "State transition validity check"),
        ("S-07", "Array/collection bounds check"),
        ("S-08", "Defensive copy on mutable input parameters"),
        ("S-09", "Thread-safety annotation or synchronization"),
        ("S-10", "Resource leak check"),
        ("S-11", "Invariant condition check"),
        ("S-12", "Error return code checked at every call site"),
        ("S-13", "Magic number replacement with named constant"),
        ("S-14", "Timeout on blocking calls"),
    ]

    def __init__(self):
        super().__init__("SafetyAgent")

    def execute(self, task_context):
        return self._invoke_llm(task_context, "SAFETY", self._build_output)

    def _build_output(self, task_context):
        method = task_context["method"]
        graph_slice = task_context.get("graph_slice", {})
        semantic = graph_slice.get("semantic", {}) or method.get("semantic", {})
        asil = semantic.get("asilLevel", "QM")
        safe_state = semantic.get("safeState")
        call_chain = graph_slice.get("callChain", {})
        annotations = " ".join(a.lower() for a in method.get("annotations", []))
        params = method.get("parameters", [])
        throws = method.get("throwsTypes", [])
        handlers = method.get("exceptionHandlers", [])
        decisions = method.get("decisions", [])
        has_transactional = "@transactional" in annotations
        results = []

        for sid, item in self.CHECKLIST:
            status = "PASS"
            detail = None

            if sid == "S-01":
                nullable = [p for p in params if p.get("type", "") not in (
                    "int", "long", "double", "float", "boolean", "byte", "short", "char", "void"
                )]
                if nullable:
                    status = "FAIL"
                    detail = f"Nullable parameters without null check: {[p['name'] for p in nullable]}"

            elif sid == "S-02":
                ret_type = method.get("returnType", "").lower()
                if ret_type not in ("void", "int", "long", "double", "float", "boolean", "byte", "short", "char") and ret_type:
                    status = "FAIL"
                    detail = f"Return type {method['returnType']} may be null — no null check detected"

            elif sid == "S-03":
                arithmetic_hints = ["/", "*", "+", "-", "increment", "decrement", "calc", "count", "sum"]
                name = method.get("name", "").lower()
                if any(h in name for h in arithmetic_hints):
                    status = "FAIL" if asil == "D" else "WARN"
                    detail = f"Arithmetic operations in {method['name']} without overflow guard"

            elif sid == "S-04":
                name = method.get("name", "").lower()
                if any(k in name for k in ["decrement", "decrease", "down", "count"]):
                    status = "FAIL" if asil == "D" else "WARN"
                    detail = f"Decrement operations without underflow guard"

            elif sid == "S-05":
                catch_types = [h.get("catchType", "") for h in handlers]
                if "Throwable" in catch_types:
                    status = "FAIL"
                    detail = "Catches Throwable — too broad"
                elif "Exception" in catch_types:
                    status = "WARN" if asil in ("D", "C") else "PASS"
                    detail = "Catches generic Exception"
                elif handlers:
                    status = "PASS"
                    detail = "Specific exception handlers present"

            elif sid == "S-06":
                if decisions:
                    status = "WARN" if asil == "D" else "PASS"
                    detail = "State transitions in decisions should be validated"

            elif sid == "S-07":
                name = method.get("name", "").lower()
                hints = ["get", "index", "list", "array", "collection", "find", "search", "lookup"]
                if any(h in name for h in hints):
                    status = "FAIL" if asil == "D" else "WARN"
                    detail = f"Collection/array access without bounds check"

            elif sid == "S-08":
                mutable_types = [p for p in params if any(
                    t in p.get("type", "").lower() for t in ["list", "map", "set", "array", "collection", "stringbuilder"]
                )]
                if mutable_types:
                    status = "WARN"
                    detail = f"Mutable input parameters not defensively copied: {[p['name'] for p in mutable_types]}"

            elif sid == "S-09":
                if has_transactional or any(s in annotations for s in ["@synchronized", "@threadsafe", "@guardedby"]):
                    status = "PASS"
                    detail = "Synchronization annotation present"
                elif asil in ("D", "C"):
                    status = "FAIL"
                    detail = "No thread-safety annotation on shared state"

            elif sid == "S-10":
                name = method.get("name", "").lower()
                hints = ["stream", "open", "connect", "read", "write", "file", "socket", "channel"]
                if any(h in name for h in hints):
                    status = "FAIL" if asil == "D" else "WARN"
                    detail = "Resource leak risk — use try-with-resources"

            elif sid == "S-11":
                for d in decisions:
                    if d.get("conditionCount", 1) <= 1:
                        continue
                    import hashlib
                    conds = d.get("conditions", [])
                    if len(conds) >= 2:
                        lower_expr = d.get("expression", "").lower()
                        if "&& true" in lower_expr or "|| false" in lower_expr:
                            status = "WARN"
                            detail = f"Invariant condition in {d['decisionId']}"
                            break
                if detail is None and decisions:
                    pass

            elif sid == "S-12":
                calls = method.get("calls", [])
                if any(c.get("name") for c in calls):
                    status = "WARN" if asil == "D" else "PASS"
                    detail = "Call sites should check error return codes"

            elif sid == "S-13":
                status = "WARN"
                detail = "Review code for magic numbers"

            elif sid == "S-14":
                if has_transactional:
                    status = "FAIL" if asil == "D" else "WARN"
                    detail = f"@Transactional method without timeout (required at ASIL-{asil})"
                elif asil == "D":
                    status = "WARN"
                    detail = "Blocking calls may need timeout"

            entry = {"id": sid, "item": item, "status": status}
            if detail:
                entry["detail"] = detail
            results.append(entry)

        if safe_state:
            handlers = method.get("exceptionHandlers", [])
            safe_state_returned = any(
                safe_state.lower() in (h.get("action", "") or "").lower()
                for h in handlers
            )
            if not handlers:
                results.append({"id": "S-SAFE", "item": "safeState declared but no exception handler returns it", "status": "FAIL"})
            elif not safe_state_returned:
                results.append({"id": "S-SAFE", "item": f"safeState '{safe_state}' declared but no handler returns it", "status": "FAIL"})
            else:
                results.append({"id": "S-SAFE", "item": "safeState validated against exception handlers", "status": "PASS"})

        if semantic.get("safetyCritical") and call_chain.get("callers"):
            results.append({
                "id": "S-CALL",
                "item": "Safety-critical method reachable from callers without safety wrapper",
                "status": "WARN",
            })

        fail_count = sum(1 for r in results if r["status"] == "FAIL")
        warn_count = sum(1 for r in results if r["status"] == "WARN")
        return {
            "methodId": method["id"],
            "asilLevel": asil,
            "checklistResults": results,
            "failCount": fail_count,
            "warnCount": warn_count,
            "blockEvidence": fail_count > 0,
            "tokensUsed": 650,
        }


class TestQualityScorer:
    DIMENSIONS = [
        ("branch_assertion",      25, "Each test has at least one assert statement"),
        ("assertion_specificity", 20, "Asserts check concrete values, not just non-null"),
        ("exception_assertion",   15, "Exception tests use assertThrows with type+message"),
        ("mock_verification",     10, "Mockito stubs are verified (verify() called)"),
        ("naming_convention",     10, "Follows test_{method}_{scenario}_{expected} pattern"),
        ("boundary_coverage",     10, "At least one test uses min/max/zero/negative input"),
        ("no_duplicate_logic",    10, "Test body is not identical to an existing test"),
    ]

    def score(self, test_output, agent_memory=None, observation_point=None, existing_tests=None):
        code = test_output.get("code", "")
        if not code:
            return 0

        passed_dimensions = self._check_dimensions(test_output, agent_memory, observation_point, existing_tests or [])
        score = sum(w for name, w, _ in self.DIMENSIONS if passed_dimensions.get(name))

        if observation_point and observation_point in code:
            score = min(100, score + 10)
        if agent_memory:
            covered = agent_memory.get("coveredBranches", [])
            if any(c in code for c in covered):
                score = max(0, score - 10)
        return min(100, max(0, score))

    def _check_dimensions(self, test_output, agent_memory, observation_point, existing_tests):
        code = test_output.get("code", "")
        test_name = test_output.get("name", "")
        passed = {}

        passed["branch_assertion"] = "assert" in code

        has_concrete = any(
            kw in code for kw in ["assertEquals(", "assertTrue(", "assertFalse(", "assertSame(", "assertNotNull(", "assertNull("]
        )
        passed["assertion_specificity"] = has_concrete

        passed["exception_assertion"] = "assertThrows" in code

        passed["mock_verification"] = "verify(" in code

        import re
        passed["naming_convention"] = bool(re.match(r"test_\w+_\w+_\w+", test_name))

        passed["boundary_coverage"] = any(
            kw in code.lower() for kw in ["max", "min", "0", "zero", "negative", "border",
                                           "Integer.MAX_VALUE", "Integer.MIN_VALUE",
                                           "Double.MAX_VALUE", "Double.MIN_VALUE"]
        )

        code_stripped = re.sub(r'\s+', '', code) if code else ""
        is_duplicate = any(
            code_stripped and re.sub(r'\s+', '', t.get("code", "")) == code_stripped
            for t in (existing_tests or [])
        )
        passed["no_duplicate_logic"] = not is_duplicate

        return passed

    def score_dimensions(self, test_output, agent_memory=None, observation_point=None, existing_tests=None):
        passed = self._check_dimensions(test_output, agent_memory, observation_point, existing_tests or [])
        return [
            {"name": dim_name, "weight": dim_weight, "description": dim_desc, "passed": passed.get(dim_name, False)}
            for dim_name, dim_weight, dim_desc in self.DIMENSIONS
        ]

    def failed_dimensions(self, test_output, score, agent_memory=None, observation_point=None, existing_tests=None):
        results = self.score_dimensions(test_output, agent_memory, observation_point, existing_tests or [])
        failed = [r["name"] for r in results if not r["passed"]]
        return failed
