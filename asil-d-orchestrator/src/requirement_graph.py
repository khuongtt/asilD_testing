import glob
import os

from semantics import parse_requirements_file


class RequirementGraph:
    def __init__(self, graph_store):
        self.store = graph_store

    def load_from_directory(self, requirements_dir):
        requirements = []
        for path in sorted(glob.glob(os.path.join(requirements_dir, "*.txt"))):
            requirements.extend(parse_requirements_file(path))
        seen = set()
        unique = []
        for req in requirements:
            if req["requirementId"] not in seen:
                seen.add(req["requirementId"])
                unique.append(req)
        return unique

    def map_methods(self, requirements, methods):
        for req in requirements:
            mapped = []
            for m in methods:
                sem = m.get("semantic", {})
                if req["requirementId"] in sem.get("linkedRequirements", []):
                    mapped.append(m["name"])
                    continue
                if m["name"] in req.get("mappedMethods", []):
                    mapped.append(m["name"])
                    continue
                desc = req.get("description", "").lower()
                if m["name"].lower() in desc or sem.get("domain", "").lower() in desc:
                    mapped.append(m["name"])
            req["mappedMethods"] = list(dict.fromkeys(mapped))
            if not mapped:
                req["verificationStatus"] = "PARTIAL"
                req["openGaps"] = ["no mapped methods"]
        return requirements

    def persist(self, requirements):
        self.store.write_json("requirements/requirements.json", {"requirements": requirements})
        for req in requirements:
            self.store.write_json(f"requirements/{req['requirementId']}.json", req)
