import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime

from agent_batcher import AgentBatcher
from agent_cache import AgentOutputCache
from agents import (
    CoverageAgent,
    MCDCAgent,
    MutationAgent,
    SafetyAgent,
    TestQualityScorer,
    UnitTestAgent,
)
from bootstrap import BootstrapManager, PrioritizationEngine
from coverage_parser import CoverageParser
from evidence import EvidenceEngine
from graph_delta import GraphDeltaEngine
from graph_slice import build_graph_slice
from graph_store import GraphStore
from knowledge_graph import KnowledgeGraph
from memory import AgentMemoryLayer
from mode_detector import ModeDetector
from mutation_parser import MutationParser
from parser import IncrementalParser
from prompt_builder import PromptBuilder
from requirement_graph import RequirementGraph
from semantics import SemanticAnnotator
from traceability import TraceabilityGraph


class Orchestrator:
    ORCHESTRATOR_VERSION = "3.0.0"
    TOKEN_BUDGET = 50000

    def __init__(self, workspace_dir, token_budget=None):
        self.workspace_dir = workspace_dir
        self.graph_dir = os.path.join(workspace_dir, ".graph")
        self.store = GraphStore(self.graph_dir)
        self.kg = KnowledgeGraph(self.store)
        self.cache = AgentOutputCache(self.graph_dir)
        self.delta_engine = GraphDeltaEngine()
        self.prioritizer = PrioritizationEngine(token_budget or self.TOKEN_BUDGET)
        self.req_graph = RequirementGraph(self.store)
        self.trace = TraceabilityGraph(self.store)
        self.coverage_parser = CoverageParser()
        self.mutation_parser = MutationParser()
        self.run_id = datetime.now().strftime("%Y%m%d%H%M%S")
        self.memory = AgentMemoryLayer(self.run_id, self.graph_dir)
        self.quality_scorer = TestQualityScorer()
        self.evidence_engine = EvidenceEngine(workspace_dir, self.store)

        self.AGENT_MAP = {
            "UnitTestAgent": UnitTestAgent(),
            "MCDCAgent": MCDCAgent(),
            "CoverageAgent": CoverageAgent(),
            "MutationAgent": MutationAgent(),
            "SafetyAgent": SafetyAgent(),
        }

        self.stats = {
            "tasksDispatched": 0,
            "tasksDeferred": 0,
            "totalTokensUsed": 0,
            "cacheHits": 0,
            "cacheTotal": 0,
            "qualityScores": [],
            "methodsSkipped": 0,
        }

    def run(self, project_dir, force=False):
        if force:
            self.store.reset()
            self.memory.clear_snapshot()
            self.cache = AgentOutputCache(self.graph_dir)
            self.memory = AgentMemoryLayer(self.run_id, self.graph_dir)

        prev_manifest = self.store.load_hash_manifest()
        mode = ModeDetector.detect(self.graph_dir, project_dir)
        commit = ModeDetector.get_commit_hash(project_dir)

        requirements_dir = os.path.join(self.workspace_dir, "requirements")
        requirements = self.req_graph.load_from_directory(requirements_dir)

        bootstrap_state = self.store.load_bootstrap_state()
        full_graph = None
        version_manifest = None
        bootstrapper = BootstrapManager(self)

        full_graph_snap, _ = self.store.load_latest_snapshot()
        if mode == "INCREMENTAL" and full_graph_snap and self.delta_engine.detect_major_refactor(
            prev_manifest, full_graph_snap.get("methods", [])
        ):
            print("Major refactor detected (>60% methods changed) — forcing bootstrap")
            mode = "BOOTSTRAP"
            self.store.reset()
            self.cache = AgentOutputCache(self.graph_dir)
            bootstrap_state = None

        if mode in ("BOOTSTRAP", "BOOTSTRAP_WITH_DATA"):
            full_graph = bootstrapper.run(project_dir, mode, requirements, commit=commit)
            requirements = self.req_graph.map_methods(requirements, full_graph["methods"])
            full_graph["requirements"] = requirements
            self.req_graph.persist(requirements)
            if not bootstrap_state:
                waves = bootstrapper.plan_waves(full_graph["methods"], self.TOKEN_BUDGET)
                bootstrap_state = {
                    "bootstrapVersion": "v0.1.0",
                    "commit": commit,
                    "totalMethods": len(full_graph["methods"]),
                    "wavesPlan": waves,
                    "completedMethods": [],
                    "lastRunId": self.run_id,
                }
                self.store.write_bootstrap_state(bootstrap_state)
            prev_version = (self.store.read_json("manifest.json") or {}).get("graphVersion")
            _, version_manifest = self.store.save_version_snapshot(full_graph, commit, prev_version)
            scope_methods = bootstrapper.get_wave_methods(bootstrap_state, full_graph["methods"]) or full_graph["methods"]
            delta = self.delta_engine.compute(self.run_id, commit, {}, full_graph["methods"])
        elif mode == "INCREMENTAL":
            full_graph, _ = self.store.load_latest_snapshot()
            if not full_graph:
                full_graph = bootstrapper.run(project_dir, "BOOTSTRAP", requirements, commit=commit)
            inc = IncrementalParser(self.store)
            full_graph, _ = inc.run(project_dir, full_graph)
            requirements = self.req_graph.map_methods(requirements, full_graph["methods"])
            full_graph["requirements"] = requirements
            self._reannotate(full_graph, requirements)
            prev_manifest = self.store.load_hash_manifest()
            delta = self.delta_engine.compute(self.run_id, commit, prev_manifest, full_graph["methods"])
            self._invalidate_dirty_cache(delta, full_graph["methods"])
            scope_methods = self.delta_engine.methods_needing_agents(delta, full_graph["methods"])
            _, version_manifest = self.store.save_version_snapshot(full_graph, commit)
        else:
            full_graph, version_manifest = self.store.load_latest_snapshot()
            if not full_graph:
                full_graph = bootstrapper.run(project_dir, "BOOTSTRAP_WITH_DATA", requirements, commit=commit)
            requirements = self.req_graph.map_methods(requirements, full_graph["methods"])
            full_graph["requirements"] = requirements
            self._refresh_coverage_mutation(project_dir, full_graph)
            for m in full_graph["methods"]:
                m["risk"] = bootstrapper.scorer.calculate_risk(m, full_graph.get("callGraph"), bootstrap_worst_case=False)
            scope_methods = full_graph["methods"]
            delta = self.delta_engine.compute(self.run_id, commit, self.store.load_hash_manifest(), full_graph["methods"])
            _, version_manifest = self.store.save_version_snapshot(full_graph, commit)

        self.store.write_mode(mode, {"commit": commit})
        print(f"Mode: {mode} | Commit: {commit[:8]}")

        self.store.write_delta_report(delta)
        self._persist_all(full_graph)
        gv = (version_manifest or {}).get("graphVersion", "unknown")
        self.trace.rebuild_stubs(requirements, full_graph["methods"], gv)

        call_graph = full_graph.get("callGraph", self.store.read_json("call_graph/graph.json", {}))
        tasks = self.prioritizer.build_task_queue(scope_methods, delta)
        dispatched, deferred, used_tokens = self.prioritizer.apply_budget(tasks)
        print(f"Task queue: {len(tasks)} total, {len(dispatched)} dispatched, {len(deferred)} deferred")

        batches = AgentBatcher.group_tasks(dispatched)
        for batch in batches:
            for task in batch["tasks"]:
                self._dispatch_task(task, full_graph, call_graph, version_manifest)

        self.stats["tasksDispatched"] = len(dispatched)
        self.stats["tasksDeferred"] = len(deferred)
        self.stats["totalTokensUsed"] = used_tokens

        if deferred:
            payload = {"runId": self.run_id, "deferred": [t["id"] for t in deferred]}
            self.store.write_deferred_tasks(payload)

        bootstrap_complete = False
        if mode in ("BOOTSTRAP", "BOOTSTRAP_WITH_DATA") and bootstrap_state:
            completed = set(bootstrap_state.get("completedMethods", []))
            for m in scope_methods:
                if m["id"] in self.memory.facts:
                    completed.add(m["id"])
            bootstrap_state["completedMethods"] = list(completed)
            bootstrap_state = bootstrapper.advance_waves(bootstrap_state, completed)
            bootstrap_state["lastRunId"] = self.run_id
            self.store.write_bootstrap_state(bootstrap_state)
            manifest = bootstrapper.compute_bootstrap_manifest(
                bootstrap_state, self.run_id, mode, len(full_graph["methods"]),
                len(scope_methods), used_tokens, self.TOKEN_BUDGET,
            )
            self.store.write_bootstrap_manifest(manifest)
            bootstrap_complete = bootstrapper.is_bootstrap_complete(bootstrap_state, self.cache, full_graph["methods"])

        if bootstrap_complete:
            self.store.write_mode("COMPLETE", {"commit": commit, "completedAt": datetime.now().isoformat()})

        full_graph["deferredTasks"] = deferred
        full_graph["qualityScores"] = self.stats["qualityScores"]
        full_graph["delta"] = delta
        full_graph["evidenceStatus"] = self.kg.evidence_completeness(full_graph["methods"], requirements)
        full_graph["callGraph"] = call_graph

        cache_rate = self.cache.hit_rate(self.stats["cacheHits"], self.stats["cacheTotal"])
        naive = len(tasks) * 1200
        manifest_extra = {
            "graphVersion": (version_manifest or {}).get("graphVersion"),
            "commit": commit,
            "tasksDispatched": self.stats["tasksDispatched"],
            "tasksDeferred": self.stats["tasksDeferred"],
            "totalTokensUsed": self.stats["totalTokensUsed"],
            "estimatedTokensNaive": naive,
            "tokenBudgetPerRun": self.TOKEN_BUDGET,
            "cacheHitRate": cache_rate,
            "methodsSkipped": self.stats["methodsSkipped"],
            "averageTestQualityScore": (
                sum(self.stats["qualityScores"]) / len(self.stats["qualityScores"])
                if self.stats["qualityScores"] else 0
            ),
        }

        self.kg.update_test_relationships(self.memory.facts)
        self.evidence_engine.generate_evidence(self.run_id, full_graph, self.memory, manifest_extra)

        run_complete = not deferred and len(dispatched) == len([t for t in tasks if t])
        if run_complete or bootstrap_complete:
            self.memory.clear_snapshot()
        else:
            self.memory.flush()

        print(f"Run complete. Cache hit rate: {cache_rate}%. {self.memory}")

    def _refresh_coverage_mutation(self, project_dir, full_graph):
        cov = self.coverage_parser.parse_jacoco(project_dir)
        mut = self.mutation_parser.parse_pit(project_dir)
        call_graph = full_graph.get("callGraph", {})
        self.coverage_parser.attach_to_methods(full_graph["methods"], cov)
        self.mutation_parser.attach_to_methods(full_graph["methods"], mut, call_graph, {m["id"]: m for m in full_graph["methods"]})

    def _invalidate_dirty_cache(self, delta, methods):
        dirty_ids = set(delta.get("dirtyMethods", []) + delta.get("newMethods", []))
        for m in methods:
            if m["id"] in dirty_ids:
                self.cache.invalidate_method(m["id"])
                self.kg.delete_method_cascade(m["id"])
                self.trace.mark_stale_for_method(m["id"])

    def _reannotate(self, full_graph, requirements):
        semantic = SemanticAnnotator()
        scorer = BootstrapManager(self).scorer
        for cls in full_graph.get("classes", []):
            for m in cls["methods"]:
                m["semantic"] = semantic.annotate(m, cls, requirements)
                m["risk"] = scorer.calculate_risk(m, full_graph.get("callGraph"), bootstrap_worst_case=False)
                self.coverage_parser.enrich_coverage(m)
                self.mutation_parser.enrich_mutation(m, full_graph.get("callGraph"), {x["id"]: x for x in full_graph["methods"]})

    def _persist_all(self, full_graph):
        for cls in full_graph.get("classes", []):
            self.store.persist_class_ast(cls)
            for m in cls["methods"]:
                self.store.persist_method_artifacts(cls["class"], m)
                if m.get("coverage"):
                    self.store.persist_coverage(m["id"], m["coverage"])
                if m.get("mutation"):
                    self.store.persist_mutation(m["id"], m["mutation"])
        self.store.update_hash_manifest(full_graph["methods"])
        self.kg.rebuild_index(full_graph)

    def _dispatch_task(self, task, full_graph, call_graph, version_manifest):
        task_id = task["id"]
        if self.memory.is_task_complete(task_id):
            self.stats["methodsSkipped"] += 1
            return

        method = task["method"]
        agent_type = task["agentType"]
        agent = self.AGENT_MAP[agent_type]
        class_name = method.get("className") or self._find_class(method, full_graph)

        self.stats["cacheTotal"] += 1
        cached = self.cache.get(agent_type, method)
        if cached:
            self.stats["cacheHits"] += 1
            output = cached["output"]
            print(f"[CACHE HIT] {task_id}")
        else:
            graph_slice = build_graph_slice(method, class_name, call_graph, self.memory.get_all_facts(method["id"]))
            ctx = {
                "method": method,
                "class": class_name,
                "graph_slice": graph_slice,
                "memory_facts": self.memory.get_all_facts(method["id"]),
                "taskType": task.get("taskType"),
            }
            output = self._execute_with_feedback(agent, ctx, task)
            gv = (version_manifest or {}).get("graphVersion", "unknown")
            self.cache.put(agent_type, method, output, gv)

        self.memory.update_from_agent(agent_type, method["id"], output)
        if agent_type == "SafetyAgent":
            self.memory.write_fact(method["id"], "safetyReview", output)
            unreachable = [r["id"] for r in output.get("checklistResults", []) if r.get("status") == "WARN" and r["id"] == "S-11"]
            if unreachable:
                self.memory.write_fact(method["id"], "confirmedUnreachableBranches", unreachable)

        test_ids = []
        if agent_type == "UnitTestAgent":
            tid = output.get("testId")
            if tid:
                test_ids.append(tid)
        elif agent_type == "CoverageAgent":
            for t in output.get("generatedTests", []):
                if t.get("testId"):
                    test_ids.append(t["testId"])
        elif agent_type == "MCDCAgent":
            for item in output.get("mcdcAnalysis", []):
                for t in item.get("tests", []):
                    if t.get("testId"):
                        test_ids.append(t["testId"])
        elif agent_type == "MutationAgent":
            for t in output.get("killTests", []):
                if t.get("testId"):
                    test_ids.append(t["testId"])
        if test_ids:
            existing = (self.memory.read_fact(method["id"], "generatedTestIds") or [])
            self.memory.write_fact(method["id"], "generatedTestIds", existing + test_ids)
            self.trace.update_tests(method["id"], test_ids)

        self.memory.mark_task_complete(task_id)
        self.memory.flush()

        gv = (version_manifest or {}).get("graphVersion", "unknown")
        for req in full_graph.get("requirements", []):
            if method["name"] in req.get("mappedMethods", []):
                self.trace.write_link(req, method, gv, stale=method.get("status") in ("DIRTY", "NEW"))

    def _execute_with_feedback(self, agent, ctx, task):
        output = agent.execute(ctx)
        if agent.name not in ("UnitTestAgent", "CoverageAgent", "MutationAgent"):
            return output
        obs = None
        if agent.name == "MutationAgent":
            for kt in output.get("killTests", []):
                obs = kt.get("observationPoint")
        memory_facts = ctx.get("memory_facts", {})
        existing_ids = memory_facts.get("unitTestIds", []) + memory_facts.get("killTestIds", [])
        for attempt in range(3):
            score = self.quality_scorer.score(output, memory_facts, obs, existing_tests=[output])
            output["qualityScore"] = score
            if score >= 70 or attempt >= 2:
                self.stats["qualityScores"].append(score)
                break
            failed = self.quality_scorer.failed_dimensions(output, score, memory_facts, obs, existing_tests=[output])
            PromptBuilder.build_feedback(agent.name, ctx["method"]["id"], score, failed)
            output = agent.execute(ctx)
        return output

    def _find_class(self, method, full_graph):
        for cls in full_graph.get("classes", []):
            if any(m["id"] == method["id"] for m in cls.get("methods", [])):
                return cls["class"]
        return "Unknown"


def main():
    if len(sys.argv) > 1 and sys.argv[1] not in ("run", "init", "-h", "--help"):
        sys.argv.insert(1, "run")

    parser = argparse.ArgumentParser(description="ASIL-D Test Orchestrator v3")
    sub = parser.add_subparsers(dest="command")

    run_parser = sub.add_parser("run", help="Run orchestrator pipeline")
    run_parser.add_argument("project_dir", nargs="?", default="asil-d-orchestrator/tests")
    run_parser.add_argument("--workspace", default="asil-d-orchestrator")
    run_parser.add_argument("--force", action="store_true")
    run_parser.add_argument("--token-budget", type=int, default=50000)

    init_parser = sub.add_parser("init", help="Initialize graph store (bootstrap)")
    init_parser.add_argument("project_dir", nargs="?", default="asil-d-orchestrator/tests")
    init_parser.add_argument("--workspace", default="asil-d-orchestrator")
    init_parser.add_argument("--force", action="store_true", default=True)
    init_parser.add_argument("--token-budget", type=int, default=50000)

    parser.add_argument("project_dir_legacy", nargs="?", default=None)
    parser.add_argument("--workspace", default="asil-d-orchestrator", dest="workspace_legacy")
    parser.add_argument("--force", action="store_true", dest="force_legacy")
    parser.add_argument("--token-budget", type=int, default=50000, dest="token_budget_legacy")

    args = parser.parse_args()
    if args.command == "init":
        project_dir = os.path.abspath(args.project_dir)
        workspace = os.path.abspath(args.workspace)
        orch = Orchestrator(workspace, token_budget=args.token_budget)
        orch.run(project_dir, force=True)
    elif args.command == "run" or args.command is None:
        if args.command == "run":
            project_dir = os.path.abspath(args.project_dir)
            workspace = os.path.abspath(args.workspace)
            force = args.force
            budget = args.token_budget
        else:
            project_dir = os.path.abspath(args.project_dir_legacy or "asil-d-orchestrator/tests")
            workspace = os.path.abspath(args.workspace_legacy)
            force = args.force_legacy
            budget = args.token_budget_legacy
        orch = Orchestrator(workspace, token_budget=budget)
        orch.run(project_dir, force=force)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
