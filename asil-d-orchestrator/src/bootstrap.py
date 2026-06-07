import glob
import os
from datetime import datetime

from parser import JavaParser
from semantics import SemanticAnnotator, parse_requirements_file
from flow_analysis import CallGraphBuilder
from coverage_parser import CoverageParser
from mutation_parser import MutationParser


class RiskScorer:
    def __init__(self, coverage_parser=None, mutation_parser=None):
        self.asil_weights = {"D": 4.0, "C": 2.0, "B": 1.0, "A": 0.5, "QM": 0.1}
        self.coverage_parser = coverage_parser or CoverageParser()
        self.mutation_parser = mutation_parser or MutationParser()

    def calculate_risk(self, method, call_graph=None, bootstrap_worst_case=True):
        semantic = method.get("semantic", {})
        asil_weight = self.asil_weights.get(semantic.get("asilLevel", "QM"), 0.1)
        safety_multiplier = 2.0 if semantic.get("safetyCritical") else 1.0

        if bootstrap_worst_case:
            coverage_gap_factor = 3.0
            mutation_factor = 2.0
        else:
            branch = method.get("coverage", {}).get("coverage", {}).get("branch", 0)
            mut_score = method.get("mutation", {}).get("mutationScore", 0)
            coverage_gap_factor = self.coverage_parser.coverage_gap_factor(branch)
            mutation_factor = self.mutation_parser.mutation_factor(mut_score)

        in_degree = 0
        if call_graph:
            in_degree = len(call_graph.get("callerMap", {}).get(method["id"], []))
        if in_degree >= 5:
            call_chain_factor = 1.5
        elif in_degree >= 2:
            call_chain_factor = 1.2
        else:
            call_chain_factor = 1.0

        req_count = len(semantic.get("linkedRequirements", []))
        if req_count >= 3:
            req_factor = 1.4
        elif req_count >= 1:
            req_factor = 1.2
        else:
            req_factor = 0.8

        score = asil_weight * safety_multiplier * coverage_gap_factor * mutation_factor * call_chain_factor * req_factor
        tier = self._tier(score)
        return {
            "score": round(score, 2),
            "riskScore": round(score, 2),
            "tier": tier,
            "riskTier": tier,
            "components": {
                "asilWeight": asil_weight,
                "safetyCriticalMultiplier": safety_multiplier,
                "coverageGapFactor": coverage_gap_factor,
                "mutationFactor": mutation_factor,
                "callChainFactor": call_chain_factor,
                "requirementFactor": req_factor,
            },
        }

    def _tier(self, score):
        if score >= 20:
            return "HIGH"
        if score >= 5:
            return "MEDIUM"
        if score >= 1:
            return "LOW"
        return "SKIP"


class PrioritizationEngine:
    TASK_WEIGHTS = {
        "SAFETY_FAIL": 5.0,
        "MCDC_GAP": 4.0,
        "BRANCH_GAP_HIGH": 3.0,
        "MUTATION_HIGH": 2.5,
        "UNIT_MISSING": 2.0,
        "BRANCH_GAP_MEDIUM": 1.5,
        "MUTATION_MEDIUM": 1.2,
        "BRANCH_GAP_LOW": 0.5,
        "MUTATION_LOW": 0.4,
        "UNIT_ENHANCEMENT": 0.3,
    }

    AGENT_COST = {
        "SafetyAgent": 1250,
        "MCDCAgent": 1200,
        "CoverageAgent": 950,
        "MutationAgent": 950,
        "UnitTestAgent": 1200,
    }

    def __init__(self, token_budget=50000):
        self.scorer = RiskScorer()
        self.token_budget = token_budget

    def rank_methods(self, methods):
        for m in methods:
            if "risk" not in m:
                m["risk"] = self.scorer.calculate_risk(m)
        return sorted(methods, key=lambda m: m["risk"]["score"], reverse=True)

    def _blocking_factor(self, task_type, method, delta):
        delta = delta or {}
        if task_type == "SAFETY_FAIL":
            return 2.0
        if task_type in ("MCDC_GAP", "BRANCH_GAP_HIGH", "MUTATION_HIGH"):
            if method.get("semantic", {}).get("asilLevel") == "D":
                return 2.0
        if method.get("semantic", {}).get("asilLevel") == "D" and task_type == "UNIT_MISSING":
            return 2.0
        if method["id"] in delta.get("staleTraceabilityLinks", []):
            return 1.3
        return 1.0

    def build_task_queue(self, methods, delta=None):
        delta = delta or {}
        dirty_set = set(delta.get("dirtyMethods", []) + delta.get("newMethods", []))
        tasks = []
        for m in methods:
            risk = m.get("risk", {})
            tier = risk.get("tier", "LOW")
            if tier == "SKIP":
                continue
            rs = risk.get("score", 1)
            sem = m.get("semantic", {})
            freshness = 1.5 if m["id"] in dirty_set else 1.0

            if tier == "HIGH":
                bf = self._blocking_factor("SAFETY_FAIL", m, delta)
                tasks.append(self._task("SAFETY_FAIL", "SafetyAgent", m, rs * 5.0 * bf * freshness, delta, bf))
            if m.get("decisions"):
                bf = self._blocking_factor("MCDC_GAP", m, delta)
                tasks.append(self._task("MCDC_GAP", "MCDCAgent", m, rs * 4.0 * bf * freshness, delta, bf))
            branch = m.get("coverage", {}).get("coverage", {}).get("branch", 0)
            if branch < 95:
                tw = "BRANCH_GAP_HIGH" if tier == "HIGH" else "BRANCH_GAP_MEDIUM"
                bf = self._blocking_factor(tw, m, delta)
                tasks.append(self._task(tw, "CoverageAgent", m, rs * self.TASK_WEIGHTS[tw] * bf * freshness, delta, bf))
            mut_score = m.get("mutation", {}).get("mutationScore", 0)
            if mut_score < 90:
                tw = "MUTATION_HIGH" if tier == "HIGH" else "MUTATION_MEDIUM"
                bf = self._blocking_factor(tw, m, delta)
                tasks.append(self._task(tw, "MutationAgent", m, rs * self.TASK_WEIGHTS[tw] * bf * freshness, delta, bf))
            bf = self._blocking_factor("UNIT_MISSING", m, delta)
            tasks.append(self._task("UNIT_MISSING", "UnitTestAgent", m, rs * 2.0 * bf * freshness, delta, bf))

            if sem.get("confidence", 1.0) < 0.8:
                m["_lowConfidence"] = True

        tasks.sort(key=lambda t: t["priorityScore"], reverse=True)
        return tasks

    def _task(self, task_type, agent_type, method, priority, delta=None, blocking_factor=1.0):
        return {
            "id": f"{agent_type}:{method['id']}",
            "taskType": task_type,
            "agentType": agent_type,
            "methodId": method["id"],
            "method": method,
            "priorityScore": priority,
            "estimatedTokens": self.AGENT_COST.get(agent_type, 1000),
            "blockingFactor": blocking_factor,
        }

    def apply_budget(self, tasks):
        dispatched = []
        deferred = []
        used = 0
        for t in tasks:
            cost = t["estimatedTokens"]
            if used + cost <= self.token_budget:
                dispatched.append(t)
                used += cost
            else:
                deferred.append(t)
        return dispatched, deferred, used


class BootstrapManager:
    def __init__(self, orchestrator):
        self.orchestrator = orchestrator
        self.parser = JavaParser()
        self.semantic = SemanticAnnotator()
        self.call_builder = CallGraphBuilder()
        self.coverage_parser = CoverageParser()
        self.mutation_parser = MutationParser()
        self.scorer = RiskScorer(self.coverage_parser, self.mutation_parser)

    def run(self, project_dir, mode, requirements=None, commit=None):
        print(f"Starting {mode} for {project_dir}")
        java_files = glob.glob(os.path.join(project_dir, "**/*.java"), recursive=True)
        full_graph = {"methods": [], "classes": [], "requirements": requirements or []}

        for f in java_files:
            metadata = self.parser.parse_file(f)
            metadata["className"] = metadata.get("class")
            for method in metadata["methods"]:
                method["className"] = metadata["class"]
                method["semantic"] = self.semantic.annotate(method, metadata, requirements)
                method["linkedRequirements"] = method["semantic"].get("linkedRequirements", [])
            full_graph["classes"].append(metadata)
            full_graph["methods"].extend(metadata["methods"])

        call_graph = self.call_builder.build_call_graph(full_graph["classes"], commit=commit)
        full_graph["callGraph"] = call_graph

        worst_case = mode == "BOOTSTRAP"
        if mode == "BOOTSTRAP_WITH_DATA":
            cov = self.coverage_parser.parse_jacoco(project_dir)
            mut = self.mutation_parser.parse_pit(project_dir)
            self.coverage_parser.attach_to_methods(full_graph["methods"], cov)
            self.mutation_parser.attach_to_methods(full_graph["methods"], mut, call_graph, {m["id"]: m for m in full_graph["methods"]})
        else:
            for m in full_graph["methods"]:
                m["coverage"] = {"coverage": {"line": 0, "branch": 0, "decision": 0, "condition": None, "mcdc": None}, "missingBranches": []}
                m["mutation"] = {"mutationScore": 0, "survivedMutants": []}
                self.coverage_parser.enrich_coverage(m)
                self.mutation_parser.enrich_mutation(m, call_graph, {m["id"]: m for m in full_graph["methods"]})

        for m in full_graph["methods"]:
            m["risk"] = self.scorer.calculate_risk(m, call_graph, bootstrap_worst_case=worst_case)
            m["status"] = "NEW"

        self._persist_graph(full_graph, call_graph)
        return full_graph

    def plan_waves(self, methods, token_budget=50000):
        ranked = sorted(methods, key=lambda m: m["risk"]["score"], reverse=True)
        waves = {"HIGH": [], "MEDIUM": [], "LOW": []}
        for m in ranked:
            waves[m["risk"]["tier"]].append(m["id"])

        wave_plan = []
        for i, tier in enumerate(["HIGH", "MEDIUM", "LOW"], 1):
            if waves[tier]:
                wave_plan.append({"wave": i, "tier": tier, "methods": waves[tier], "status": "PENDING"})
        if wave_plan:
            wave_plan[0]["status"] = "IN_PROGRESS"
        return wave_plan

    def get_wave_methods(self, bootstrap_state, all_methods):
        if not bootstrap_state:
            return all_methods
        for wave in bootstrap_state.get("wavesPlan", []):
            if wave["status"] in ("IN_PROGRESS", "PENDING"):
                ids = set(wave["methods"]) - set(bootstrap_state.get("completedMethods", []))
                by_id = {m["id"]: m for m in all_methods}
                return [by_id[i] for i in wave["methods"] if i in by_id and i in ids]
        return []

    def advance_waves(self, bootstrap_state, completed_methods):
        completed = set(completed_methods)
        waves = bootstrap_state.get("wavesPlan", [])
        for wave in waves:
            if wave["status"] == "IN_PROGRESS":
                if all(mid in completed for mid in wave.get("methods", [])):
                    wave["status"] = "COMPLETE"
        for wave in waves:
            if wave["status"] == "PENDING":
                wave["status"] = "IN_PROGRESS"
                break
        return bootstrap_state

    def compute_bootstrap_manifest(self, bootstrap_state, run_id, mode, total_methods, processed, tokens_used, token_budget):
        waves = bootstrap_state.get("wavesPlan", [])
        completed = set(bootstrap_state.get("completedMethods", []))
        remaining = total_methods - len(completed)
        pending_waves = [w for w in waves if w["status"] != "COMPLETE"]
        next_wave = pending_waves[0]["wave"] if pending_waves else None
        next_count = len(pending_waves[0]["methods"]) if pending_waves else 0
        avg_tokens = 5000
        est_runs = max(1, (remaining * avg_tokens) // max(token_budget, 1))
        return {
            "runId": run_id,
            "bootstrapVersion": bootstrap_state.get("bootstrapVersion", "v0.1.0"),
            "commit": bootstrap_state.get("commit"),
            "timestamp": __import__("datetime").datetime.now().isoformat(),
            "mode": mode,
            "bootstrapDataMode": "LEGACY_REPORTS_FOUND" if mode == "BOOTSTRAP_WITH_DATA" else "NO_EXISTING_DATA",
            "totalMethods": total_methods,
            "wavesTotal": len(waves),
            "waveCompleted": sum(1 for w in waves if w["status"] == "COMPLETE"),
            "methodsProcessedThisRun": processed,
            "methodsRemainingTotal": remaining,
            "estimatedRunsToComplete": est_runs,
            "tokensUsedThisRun": tokens_used,
            "tokenBudgetPerRun": token_budget,
            "highRiskMethodsComplete": all(
                mid in completed for w in waves if w.get("tier") == "HIGH" for mid in w.get("methods", [])
            ) if waves else True,
            "bootstrapComplete": not pending_waves,
            "nextWave": next_wave,
            "nextWaveMethodCount": next_count,
        }

    def is_bootstrap_complete(self, bootstrap_state, cache, methods):
        waves = bootstrap_state.get("wavesPlan", [])
        if any(w["status"] in ("PENDING", "IN_PROGRESS") for w in waves):
            return False
        high_methods = [m for m in methods if m.get("risk", {}).get("tier") == "HIGH"]
        for m in high_methods:
            if not cache.get("SafetyAgent", m):
                return False
        return True

    def _persist_graph(self, full_graph, call_graph):
        self.orchestrator.store.persist_call_graph(call_graph)
        self.orchestrator._persist_all(full_graph)
