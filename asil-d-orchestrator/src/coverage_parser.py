import os
import xml.etree.ElementTree as ET


class CoverageParser:
    def parse_jacoco(self, project_dir):
        path = os.path.join(project_dir, "target/site/jacoco/jacoco.xml")
        if not os.path.exists(path):
            return {}
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            return {}

        results = {}
        for package in tree.getroot().findall(".//package"):
            for clazz in package.findall("class"):
                class_name = clazz.get("name", "").split("/")[-1]
                for method in clazz.findall("method"):
                    method_name = method.get("name", "")
                    line_cov = self._counter_pct(method, "LINE")
                    branch_cov = self._counter_pct(method, "BRANCH")
                    line_start = int(method.get("line", 0))
                    line_end = int(method.get("lineEnd", line_start))
                    method_missed = 0
                    method_total = 0
                    for counter in method.findall("counter"):
                        if counter.get("type") == "LINE":
                            method_missed = int(counter.get("missed", 0))
                            method_total = method_missed + int(counter.get("covered", 0))
                            break
                    missed_lines = []
                    if line_cov < 100 and method_missed > 0 and line_start > 0 and method_total > 1:
                        step = max(1, (method_total - 1) // method_missed)
                        for i in range(method_missed):
                            ln = line_start + i * step
                            missed_lines.append(ln)
                    key = f"{class_name}.{method_name}"
                    results[key] = {
                        "methodKey": key,
                        "className": class_name,
                        "methodName": method_name,
                        "coverage": {
                            "line": line_cov,
                            "branch": branch_cov,
                            "decision": branch_cov,
                            "condition": None,
                            "mcdc": None,
                        },
                        "missingLines": missed_lines,
                        "missingBranches": [] if branch_cov >= 95 else [
                            {"id": "B_GAP", "outcome": "false", "line": 0, "riskScore": 48.0}
                        ],
                        "meetsBranchThreshold": branch_cov >= 95,
                        "meetsMcdcRequirement": False,
                    }
        return results

    def _counter_pct(self, node, ctype):
        for counter in node.findall("counter"):
            if counter.get("type") == ctype:
                missed = int(counter.get("missed", 0))
                covered = int(counter.get("covered", 0))
                total = missed + covered
                return round(100 * covered / total, 1) if total else 0.0
        return 0.0

    def coverage_gap_factor(self, branch_coverage):
        if branch_coverage < 80:
            return 3.0
        if branch_coverage < 95:
            return 1.5
        return 1.0

    def enrich_coverage(self, method, risk_score=0):
        cov = method.get("coverage", {})
        if "coverageGraphVersion" not in cov:
            cov["coverageGraphVersion"] = "1.3.0"
        cov.setdefault("methodId", method["id"])
        decisions = method.get("decisions", [])
        if decisions:
            cov["decisionCoverage"] = [
                {"decisionId": d["decisionId"], "trueCovered": None, "falseCovered": None}
                for d in decisions
            ]
            cov["conditionCoverage"] = [
                {"decisionId": d["decisionId"], "condition": c, "trueCovered": None, "falseCovered": None}
                for d in decisions for c in d.get("conditions", [])
            ]
        else:
            cov["decisionCoverage"] = []
            cov["conditionCoverage"] = []
        cov["mcdcCoverage"] = []
        for mb in cov.get("missingBranches", []):
            mb["riskScore"] = risk_score or method.get("risk", {}).get("score", 0)
        method["coverage"] = cov
        return cov

    def attach_to_methods(self, methods, coverage_by_key):
        for m in methods:
            key = f"{m.get('className', '')}.{m['name']}"
            cov = coverage_by_key.get(key)
            if not cov:
                for k, v in coverage_by_key.items():
                    if v["methodName"] == m["name"]:
                        cov = v
                        break
            if cov:
                m["coverage"] = cov
            else:
                m["coverage"] = {
                    "coverage": {"line": 0, "branch": 0, "decision": 0, "condition": None, "mcdc": None},
                    "missingLines": [m.get("lineStart", 0)],
                    "missingBranches": [{"id": "B_ALL", "outcome": "false", "line": m.get("lineStart", 0)}],
                    "meetsBranchThreshold": False,
                    "meetsMcdcRequirement": False,
                }
            self.enrich_coverage(m)
