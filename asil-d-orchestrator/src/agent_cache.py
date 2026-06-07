import hashlib
import json
import os
from datetime import datetime


class AgentOutputCache:
    AGENT_TYPES = ["UnitTestAgent", "MCDCAgent", "CoverageAgent", "MutationAgent", "SafetyAgent"]

    def __init__(self, graph_dir):
        self.cache_dir = os.path.join(graph_dir, "cache", "agent_outputs")
        for agent in self.AGENT_TYPES:
            os.makedirs(os.path.join(self.cache_dir, agent), exist_ok=True)

    def semantic_hash(self, method):
        sem = method.get("semantic", {})
        payload = (
            str(sem.get("asilLevel", ""))
            + str(sem.get("safetyCritical", ""))
            + str(sem.get("domain", ""))
            + str(sem.get("safeState", ""))
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def graph_hash(self, method):
        parts = [
            method.get("methodHash", ""),
            json.dumps(method.get("decisions", []), sort_keys=True),
            json.dumps(method.get("dfg", {}), sort_keys=True, default=str),
            str(method.get("coverage", {}).get("coverage", {})),
            str(method.get("mutation", {}).get("mutationScore", "")),
        ]
        return hashlib.sha256("".join(parts).encode()).hexdigest()

    def cache_key(self, agent_type, method):
        payload = f"{agent_type}|{method['id']}|{self.graph_hash(method)}|{self.semantic_hash(method)}"
        return hashlib.sha256(payload.encode()).hexdigest()

    def get(self, agent_type, method):
        key = self.cache_key(agent_type, method)
        path = os.path.join(self.cache_dir, agent_type, f"{method['id']}_{key[:32]}.json")
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return json.load(f)

    def put(self, agent_type, method, output, graph_version="unknown"):
        key = self.cache_key(agent_type, method)
        agent_dir = os.path.join(self.cache_dir, agent_type)
        os.makedirs(agent_dir, exist_ok=True)
        path = os.path.join(agent_dir, f"{method['id']}_{key[:32]}.json")
        entry = {
            "cacheKey": f"sha256:{key}",
            "agentType": agent_type,
            "methodId": method["id"],
            "graphHash": f"sha256:{self.graph_hash(method)}",
            "semanticHash": f"sha256:{self.semantic_hash(method)}",
            "asilLevelAtCache": method.get("semantic", {}).get("asilLevel"),
            "generatedAt": datetime.now().isoformat(),
            "graphVersion": graph_version,
            "output": output,
            "ttlDays": 30,
        }
        with open(path, "w") as f:
            json.dump(entry, f, indent=2)
        return entry

    def hit_rate(self, hits, total):
        return round(100 * hits / total, 1) if total else 0.0

    def invalidate_method(self, method_id):
        removed = 0
        for agent in self.AGENT_TYPES:
            agent_dir = os.path.join(self.cache_dir, agent)
            if not os.path.isdir(agent_dir):
                continue
            for fname in os.listdir(agent_dir):
                if fname.startswith(f"{method_id}_"):
                    os.remove(os.path.join(agent_dir, fname))
                    removed += 1
        return removed
