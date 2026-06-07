import json
import os


class KnowledgeGraph:
    """JSON-traversal fallback for cross-cutting graph queries."""

    NODE_TYPES = {
        "CLASS", "METHOD", "DECISION", "BRANCH", "CONDITION",
        "DEF_SITE", "USE_SITE", "REQUIREMENT", "TESTCASE",
        "COVERAGE_DATUM", "MUTANT", "SAFETYISSUE", "EVIDENCE_ARTIFACT",
    }

    RELATIONSHIP_TYPES = {
        "CONTAINS", "HAS_DECISION", "HAS_BRANCH", "HAS_CONDITION",
        "CALLS", "STUB_BOUNDARY",
        "DEF_FLOWS_TO", "FEEDS_DECISION",
        "TRACES_TO", "TESTED_BY", "COVERS", "SATISFIES", "KILLS",
        "HAS_COVERAGE", "HAS_MUTANT", "RAISES", "BLOCKS",
        "INCLUDED_IN", "VALIDATED_BY",
    }

    def __init__(self, graph_store):
        self.store = graph_store
        self.edges = []

    def _node_path(self, node_type, node_id):
        safe = node_id.replace("/", "_").replace(":", "_")
        return f"kg/nodes/{node_type}/{safe}.json"

    def _write_node(self, node_type, node_id, properties):
        path = self._node_path(node_type, node_id)
        data = {"type": node_type, "id": node_id, **properties}
        os.makedirs(os.path.dirname(os.path.join(self.store.graph_dir, path)), exist_ok=True)
        self.store.write_json(path, data)

    def _delete_node(self, node_type, node_id):
        path = os.path.join(self.store.graph_dir, self._node_path(node_type, node_id))
        if os.path.exists(path):
            os.remove(path)

    def merge_relationship(self, from_id, rel_type, to_id, properties=None):
        if rel_type not in self.RELATIONSHIP_TYPES:
            raise ValueError(f"Unknown relationship type: {rel_type}")
        edge = {"from": from_id, "type": rel_type, "to": to_id, "properties": properties or {}}
        self.edges = [e for e in self.edges if not (
            e["from"] == from_id and e["type"] == rel_type and e["to"] == to_id
        )]
        self.edges.append(edge)
        return edge

    def merge_method(self, method, class_name):
        self._write_node("METHOD", method["id"], {
            "name": method["name"],
            "className": class_name,
            "asilLevel": method.get("semantic", {}).get("asilLevel"),
            "safetyCritical": method.get("semantic", {}).get("safetyCritical"),
            "riskScore": method.get("risk", {}).get("score"),
            "riskTier": method.get("risk", {}).get("tier"),
            "returnType": method.get("returnType"),
        })
        self.merge_relationship(class_name, "CONTAINS", method["id"])

    def merge_decision(self, decision, method_id):
        did = decision["decisionId"]
        self._write_node("DECISION", did, {
            "methodId": method_id,
            "expression": decision.get("expression"),
            "conditionCount": decision.get("conditionCount", len(decision.get("conditions", []))),
        })
        self.merge_relationship(method_id, "HAS_DECISION", did)

    def merge_branch(self, branch, decision_id):
        bid = f"{decision_id}_{branch['id']}"
        self._write_node("BRANCH", bid, {
            "decisionId": decision_id,
            "outcome": branch.get("outcome"),
            "targetNode": branch.get("targetNode"),
        })
        self.merge_relationship(decision_id, "HAS_BRANCH", bid)

    def merge_condition(self, condition_text, decision_id, idx):
        cid = f"{decision_id}_C{idx}"
        self._write_node("CONDITION", cid, {
            "decisionId": decision_id,
            "expression": condition_text,
        })
        self.merge_relationship(decision_id, "HAS_CONDITION", cid)

    def merge_coverage(self, method_id, coverage):
        cov_id = f"COV_{method_id}"
        self._write_node("COVERAGE_DATUM", cov_id, {
            "methodId": method_id,
            "line": coverage.get("coverage", {}).get("line"),
            "branch": coverage.get("coverage", {}).get("branch"),
            "decision": coverage.get("coverage", {}).get("decision"),
        })
        self.merge_relationship(method_id, "HAS_COVERAGE", cov_id)

    def merge_mutation(self, method_id, mutation):
        for surv in mutation.get("survivedMutants", []):
            mid = surv.get("mutantId", f"MUT_{method_id}")
            self._write_node("MUTANT", mid, {
                "methodId": method_id,
                "operator": surv.get("operator"),
                "killed": surv.get("killed", False),
                "equivalentProbability": surv.get("equivalentProbability"),
            })
            self.merge_relationship(method_id, "HAS_MUTANT", mid)

    def merge_dfg_edges(self, method):
        dfg = method.get("dfg", {})
        if not isinstance(dfg, dict):
            return
        for chain in dfg.get("defUseChains", []):
            var = chain.get("variable")
            def_site = chain.get("defSite", {})
            def_id = f"{method['id']}_DEF_{var}"
            self._write_node("DEF_SITE", def_id, {
                "methodId": method["id"],
                "variable": var,
                "line": def_site.get("line"),
                "source": def_site.get("source"),
            })
            for use in chain.get("useSites", []):
                use_id = f"{method['id']}_USE_{var}_{use.get('line', 0)}"
                self._write_node("USE_SITE", use_id, {
                    "methodId": method["id"],
                    "variable": var,
                    "line": use.get("line"),
                    "role": use.get("role"),
                })
                self.merge_relationship(def_id, "DEF_FLOWS_TO", use_id)
                if use.get("role") == "CONDITION_INPUT":
                    for dec_id in method.get("decisions", []):
                        actual_did = dec_id["decisionId"]
                        self.merge_relationship(use_id, "FEEDS_DECISION", actual_did)

    def merge_call_edges(self, call_graph):
        for edge in call_graph.get("callEdges", []):
            rel_type = "STUB_BOUNDARY" if edge.get("type") == "STUB_BOUNDARY" else "CALLS"
            self.merge_relationship(edge["caller"], rel_type, edge["callee"], {
                "callSite": edge.get("callSite"),
            })

    def merge_requirement_link(self, req_id, method_id):
        self.merge_relationship(req_id, "TRACES_TO", method_id)

    def persist_edges(self):
        self.store.write_json("kg/edges.json", {"edges": self.edges})

    def query_asil_d_coverage_gaps(self, methods, branch_threshold=95):
        results = []
        for m in methods:
            if m.get("semantic", {}).get("asilLevel") != "D":
                continue
            branch = m.get("coverage", {}).get("coverage", {}).get("branch", 0)
            if branch < branch_threshold:
                results.append({"methodId": m["id"], "name": m["name"], "branch": branch})
        return sorted(results, key=lambda x: x["branch"])

    def query_survived_mutants_with_gaps(self, methods):
        results = []
        for m in methods:
            for mut in m.get("mutation", {}).get("survivedMutants", []):
                if mut.get("equivalentProbability", 0) < 0.7:
                    results.append({
                        "methodId": m["id"], "name": m["name"],
                        "mutantId": mut.get("mutantId"), "operator": mut.get("operator"),
                    })
        return results

    def evidence_completeness(self, methods, requirements):
        open_reqs, blocking = [], []
        for req in requirements:
            if not req.get("mappedMethods"):
                open_reqs.append(req["requirementId"])
                blocking.append(f"{req['requirementId']}: no mapped methods")
        for m in methods:
            branch = m.get("coverage", {}).get("coverage", {}).get("branch", 0)
            if branch < 95 and m.get("risk", {}).get("tier") in ("HIGH", "MEDIUM"):
                blocking.append(f"{m['id']}: branch coverage {branch}% < 95%")
            score = m.get("mutation", {}).get("mutationScore", 0)
            if score < 90 and m.get("semantic", {}).get("asilLevel") == "D":
                blocking.append(f"{m['id']}: mutation score {score}% < 90%")
            if m.get("semantic", {}).get("confidence", 1) < 0.8:
                blocking.append(f"{m['id']}: low confidence semantic annotation ({m['semantic']['confidence']})")
        return {
            "allRequirementsCovered": len(open_reqs) == 0,
            "openRequirements": open_reqs,
            "blockingItems": blocking,
            "status": "COMPLETE" if not blocking else "INCOMPLETE",
        }

    def rebuild_index(self, full_graph):
        self.edges = []
        for cls in full_graph.get("classes", []):
            cls_name = cls.get("class", "")
            self._write_node("CLASS", cls_name, {
                "classHash": cls.get("classHash"),
                "filePath": cls.get("filePath"),
            })
            for m in cls.get("methods", []):
                self.merge_method(m, cls_name)

                for d in m.get("decisions", []):
                    self.merge_decision(d, m["id"])
                    for bi, b in enumerate(d.get("branches", [])):
                        self.merge_branch(b, d["decisionId"])
                    for ci, c in enumerate(d.get("conditions", [])):
                        self.merge_condition(c, d["decisionId"], ci)

                if m.get("coverage"):
                    self.merge_coverage(m["id"], m["coverage"])
                if m.get("mutation"):
                    self.merge_mutation(m["id"], m["mutation"])

                self.merge_dfg_edges(m)

                for req_id in m.get("semantic", {}).get("linkedRequirements", []):
                    self.merge_requirement_link(req_id, m["id"])

        call_graph = full_graph.get("callGraph", {})
        self.merge_call_edges(call_graph)

        memory = full_graph.get("agentMemory", {})
        for mid, facts in (memory or {}).items():
            for tid in facts.get("unitTestIds", []):
                self._write_node("TESTCASE", tid, {"methodId": mid, "type": "UNIT"})
                self.merge_relationship(mid, "TESTED_BY", tid)
                for branch_label in facts.get("coveredBranches", []):
                    self.merge_relationship(tid, "COVERS", branch_label)
            for tid in facts.get("killTestIds", []):
                self._write_node("TESTCASE", tid, {"methodId": mid, "type": "KILL"})
                self.merge_relationship(mid, "TESTED_BY", tid)

        self.persist_edges()

    def update_test_relationships(self, memory_facts):
        for mid, facts in memory_facts.items():
            test_ids = []
            for tid in facts.get("unitTestIds", []):
                self._write_node("TESTCASE", tid, {"methodId": mid, "type": "UNIT"})
                self.merge_relationship(mid, "TESTED_BY", tid)
                test_ids.append(tid)
            for tid in facts.get("killTestIds", []):
                self._write_node("TESTCASE", tid, {"methodId": mid, "type": "KILL"})
                self.merge_relationship(mid, "TESTED_BY", tid)
                test_ids.append(tid)
            for branch in facts.get("coveredBranches", []):
                for tid in test_ids:
                    self.merge_relationship(tid, "COVERS", branch)
        self.persist_edges()

    def delete_method_cascade(self, method_id):
        self.edges = [e for e in self.edges if e["from"] != method_id and e["to"] != method_id]
        for ntype in ["METHOD", "COVERAGE_DATUM"]:
            self._delete_node(ntype, method_id)
            self._delete_node(ntype, f"COV_{method_id}")
        kg_dir = os.path.join(self.store.graph_dir, "kg")
        for root, dirs, files in os.walk(kg_dir):
            for f in files:
                if method_id in f or f"COV_{method_id}" in f:
                    try:
                        os.remove(os.path.join(root, f))
                    except OSError:
                        pass