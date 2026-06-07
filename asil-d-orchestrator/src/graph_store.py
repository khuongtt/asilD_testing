import gzip
import hashlib
import json
import os
import shutil
from datetime import datetime


class GraphStore:
    SUBDIRS = [
        "ast", "cfg", "dfg", "call_graph", "semantic", "risk",
        "coverage", "mutation", "requirements", "traceability",
        "evidence_graph", "cache/agent_outputs", "hashes", "versions", "kg",
    ]

    def __init__(self, graph_dir):
        self.graph_dir = graph_dir
        for sub in self.SUBDIRS:
            os.makedirs(os.path.join(graph_dir, sub), exist_ok=True)

    def write_json(self, rel_path, data):
        path = os.path.join(self.graph_dir, rel_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return path

    def read_json(self, rel_path, default=None):
        path = os.path.join(self.graph_dir, rel_path)
        if not os.path.exists(path):
            return default
        with open(path) as f:
            return json.load(f)

    def persist_class_ast(self, class_data):
        name = class_data.get("class") or "Unknown"
        self.write_json(f"ast/{name}.json", class_data)

    def persist_method_artifacts(self, cls_name, method):
        mid = method["id"]
        if method.get("decisions") is not None:
            self.write_json(f"cfg/{cls_name}_{mid}.json", {
                "methodId": mid,
                "decisions": method.get("decisions", []),
                "paths": method.get("paths", []),
                "cyclomaticComplexity": method.get("cyclomaticComplexity", 1),
            })
        if method.get("dfg"):
            self.write_json(f"dfg/{mid}.json", method["dfg"])
        if method.get("semantic"):
            self.write_json(f"semantic/{mid}.json", {
                "methodId": mid,
                "methodName": method["name"],
                "semantic": method["semantic"],
            })
        if method.get("risk"):
            self.write_json(f"risk/{mid}.json", {
                "methodId": mid,
                "methodName": method["name"],
                **method["risk"],
            })

    def persist_call_graph(self, call_graph):
        self.write_json("call_graph/graph.json", call_graph)

    def persist_coverage(self, method_id, data):
        self.write_json(f"coverage/{method_id}_coverage.json", data)

    def persist_mutation(self, method_id, data):
        self.write_json(f"mutation/{method_id}_mutation.json", data)

    def update_hash_manifest(self, methods):
        manifest = {}
        for m in methods:
            manifest[m["id"]] = {
                "methodHash": m["methodHash"],
                "name": m["name"],
                "status": m.get("status", "CLEAN"),
                "lastAnalyzed": datetime.now().isoformat(),
            }
        self.write_json("hashes/manifest.json", manifest)
        return manifest

    def load_hash_manifest(self):
        return self.read_json("hashes/manifest.json", {})

    def write_mode(self, mode, extra=None):
        payload = {"mode": mode, "timestamp": datetime.now().isoformat()}
        if extra:
            payload.update(extra)
        self.write_json("mode.json", payload)

    def write_bootstrap_state(self, state):
        self.write_json("bootstrap_state.json", state)

    def load_bootstrap_state(self):
        return self.read_json("bootstrap_state.json")

    def write_bootstrap_manifest(self, manifest):
        self.write_json("bootstrap_manifest.json", manifest)

    def write_delta_report(self, report):
        self.write_json("delta_report.json", report)

    def write_deferred_tasks(self, tasks):
        self.write_json("deferred_tasks.json", tasks)

    def compute_graph_hash(self, methods):
        payload = json.dumps(
            [{"id": m["id"], "hash": m["methodHash"]} for m in sorted(methods, key=lambda x: x["id"])],
            sort_keys=True,
        )
        return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"

    def save_version_snapshot(self, graph_data, commit_hash, prev_version=None):
        version_id = f"v{datetime.now().strftime('%Y%m%d%H%M%S')}_{commit_hash[:8]}"
        version_path = os.path.join(self.graph_dir, "versions", version_id)
        os.makedirs(version_path, exist_ok=True)

        snapshot_gz = os.path.join(version_path, "snapshot.json.gz")
        with gzip.open(snapshot_gz, "wt", encoding="utf-8") as f:
            json.dump(graph_data, f)

        graph_hash = self.compute_graph_hash(graph_data.get("methods", []))
        manifest = {
            "graphVersion": version_id,
            "versionId": version_id,
            "commit": commit_hash,
            "timestamp": datetime.now().isoformat(),
            "methodCount": len(graph_data.get("methods", [])),
            "graphHash": graph_hash,
            "prevGraphVersion": prev_version,
        }
        with open(os.path.join(version_path, "manifest.json"), "w") as f:
            json.dump(manifest, f, indent=2)
        self.write_json("manifest.json", manifest)
        self._apply_retention()
        return version_id, manifest

    def load_latest_snapshot(self):
        manifest = self.read_json("manifest.json")
        if not manifest:
            return None, None
        version_id = manifest.get("versionId") or manifest.get("graphVersion")
        gz_path = os.path.join(self.graph_dir, "versions", version_id, "snapshot.json.gz")
        plain_path = os.path.join(self.graph_dir, "versions", version_id, "snapshot.json")
        if os.path.exists(gz_path):
            with gzip.open(gz_path, "rt", encoding="utf-8") as f:
                return json.load(f), manifest
        if os.path.exists(plain_path):
            with open(plain_path) as f:
                return json.load(f), manifest
        return None, manifest

    def _apply_retention(self, keep=10):
        versions_dir = os.path.join(self.graph_dir, "versions")
        if not os.path.isdir(versions_dir):
            return
        entries = sorted(
            [d for d in os.listdir(versions_dir) if os.path.isdir(os.path.join(versions_dir, d))],
            reverse=True,
        )
        for old in entries[keep:]:
            shutil.rmtree(os.path.join(versions_dir, old), ignore_errors=True)

    def reset(self):
        if os.path.exists(self.graph_dir):
            shutil.rmtree(self.graph_dir)
        for sub in self.SUBDIRS:
            os.makedirs(os.path.join(self.graph_dir, sub), exist_ok=True)
