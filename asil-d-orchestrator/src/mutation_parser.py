import glob
import os
import xml.etree.ElementTree as ET


class MutationParser:
    def parse_pit(self, project_dir):
        paths = glob.glob(os.path.join(project_dir, "target/pit-reports", "*", "mutations.xml"))
        if not paths:
            return {}
        try:
            tree = ET.parse(paths[0])
        except ET.ParseError:
            return {}

        by_method = {}
        for mutation in tree.getroot().findall(".//mutation"):
            method_desc = mutation.findtext("mutatedMethod", "") or mutation.findtext("methodDescription", "")
            method_name = method_desc.split("(")[0].split()[-1] if method_desc else "unknown"
            killed = (mutation.get("status") or mutation.findtext("status", "")).upper() == "KILLED"
            desc = mutation.findtext("description", "")
            entry = {
                "mutantId": mutation.get("id") or f"MUT-{len(by_method)+1:03d}",
                "operator": mutation.get("mutator") or desc or "UNKNOWN",
                "method": method_name,
                "line": int(mutation.get("lineNumber") or mutation.findtext("lineNumber", "0") or 0),
                "condition": desc.split(" -> ")[0].strip() if " -> " in desc else (("negated: " + desc) if desc else None),
                "killed": killed,
                "equivalentProbability": 0.05 if not killed else 0.0,
                "killingTestHint": None if killed else "assert expected behavior at boundary",
                "callChain": None,
                "dataFlowNote": None,
            }
            by_method.setdefault(method_name, []).append(entry)

        results = {}
        for method_name, mutants in by_method.items():
            survived = [m for m in mutants if not m["killed"]]
            total = len(mutants)
            score = round(100 * (total - len(survived)) / total) if total else 0
            results[method_name] = {
                "mutationScore": score,
                "survivedMutants": survived,
            }
        return results

    def mutation_factor(self, mutation_score):
        if mutation_score < 70:
            return 2.0
        if mutation_score < 90:
            return 1.3
        return 1.0

    def attach_to_methods(self, methods, mutation_by_name, call_graph=None, methods_by_id=None):
        methods_by_id = methods_by_id or {m["id"]: m for m in methods}
        for m in methods:
            mut = mutation_by_name.get(m["name"])
            if mut:
                m["mutation"] = mut
            else:
                m["mutation"] = {"mutationScore": 0, "survivedMutants": []}
            self.enrich_mutation(m, call_graph, methods_by_id)

    def enrich_mutation(self, method, call_graph=None, methods_by_id=None):
        mut = method.setdefault("mutation", {})
        mut["mutationGraphVersion"] = "1.3.0"
        mut.setdefault("methodId", method["id"])
        callers = (call_graph or {}).get("callerMap", {}).get(method["id"], [])
        transitive = list(callers)
        if call_graph:
            for c in callers:
                transitive.extend(call_graph.get("callerMap", {}).get(c, []))
        dfg = method.get("dfg", {})
        chains = dfg.get("defUseChains", []) if isinstance(dfg, dict) else []
        for surv in mut.get("survivedMutants", []):
            if not surv.get("callChain") and callers:
                surv["callChain"] = {
                    "immediateCallers": callers,
                    "transitiveCallers": list(dict.fromkeys(transitive)),
                    "observationPoint": transitive[-1] if transitive else callers[0],
                    "integrationTestRequired": len(callers) > 0,
                }
            if not surv.get("dataFlowNote") and chains:
                var = chains[0].get("variable", "param")
                origin = chains[0].get("defSite", {}).get("source", "PARAMETER")
                surv["dataFlowNote"] = f"{var} flows from {origin} — directly settable in test"
        return mut
