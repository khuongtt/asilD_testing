from datetime import datetime


class TraceabilityGraph:
    def __init__(self, graph_store):
        self.store = graph_store

    def write_link(self, requirement, method, graph_version, stale=False):
        link = {
            "requirement": requirement.get("requirementId"),
            "asilLevel": requirement.get("asilLevel"),
            "method": method["name"],
            "methodId": method["id"],
            "methodHash": method.get("methodHash"),
            "graphVersion": graph_version,
            "tests": [],
            "openGaps": requirement.get("openGaps", []),
            "traceabilityStatus": "INCOMPLETE",
            "stale": stale,
            "evidenceId": None,
            "updatedAt": datetime.now().isoformat(),
        }
        rid = requirement.get("requirementId", "UNKNOWN")
        self.store.write_json(f"traceability/{rid}_{method['id']}.json", link)
        return link

    def rebuild_stubs(self, requirements, methods, graph_version):
        links = []
        for req in requirements:
            for m in methods:
                if m["name"] in req.get("mappedMethods", []) or req["requirementId"] in m.get("semantic", {}).get("linkedRequirements", []):
                    links.append(self.write_link(req, m, graph_version, stale=m.get("status") in ("DIRTY", "NEW")))
        return links

    def update_tests(self, method_id, test_ids):
        import glob, os
        trace_dir = os.path.join(self.store.graph_dir, "traceability")
        for path in glob.glob(os.path.join(trace_dir, f"*_{method_id}.json")):
            data = self.store.read_json(f"traceability/{os.path.basename(path)}")
            if data:
                existing = set(data.get("tests", []))
                updated = list(existing | set(test_ids))
                if updated != existing:
                    data["tests"] = updated
                    self.store.write_json(f"traceability/{os.path.basename(path)}", data)

    def mark_stale_for_method(self, method_id):
        import glob
        import os
        trace_dir = os.path.join(self.store.graph_dir, "traceability")
        for path in glob.glob(os.path.join(trace_dir, f"*_{method_id}.json")):
            data = self.store.read_json(f"traceability/{os.path.basename(path)}")
            if data:
                data["stale"] = True
                self.store.write_json(f"traceability/{os.path.basename(path)}", data)
