import json
import os
from datetime import datetime


class AgentMemoryLayer:
    SNAPSHOT_FILE = "agent_memory_snapshot.json"

    def __init__(self, run_id, graph_dir):
        self.run_id = run_id
        self.graph_dir = graph_dir
        self.facts = {}
        self.completed_tasks = []
        self.pending_tasks = []
        self._load_snapshot()

    def _snapshot_path(self):
        return os.path.join(self.graph_dir, self.SNAPSHOT_FILE)

    def _load_snapshot(self):
        path = self._snapshot_path()
        if not os.path.exists(path):
            return
        try:
            with open(path) as f:
                data = json.load(f)
            if data.get("runId") == self.run_id:
                self.facts = data.get("facts", {})
                self.completed_tasks = data.get("completedTasks", [])
                self.pending_tasks = data.get("pendingTasks", [])
        except (json.JSONDecodeError, OSError):
            pass

    def flush(self):
        path = self._snapshot_path()
        tmp = path + ".tmp"
        payload = {
            "runId": self.run_id,
            "lastFlushedAt": datetime.now().isoformat(),
            "completedTasks": self.completed_tasks,
            "pendingTasks": self.pending_tasks,
            "facts": self.facts,
        }
        os.makedirs(self.graph_dir, exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, path)

    def clear_snapshot(self):
        path = self._snapshot_path()
        if os.path.exists(path):
            os.remove(path)

    def write_fact(self, method_id, key, value):
        if method_id not in self.facts:
            self.facts[method_id] = {}
        self.facts[method_id][key] = value

    def read_fact(self, method_id, key):
        return self.facts.get(method_id, {}).get(key)

    def get_all_facts(self, method_id):
        return self.facts.get(method_id, {})

    def mark_task_complete(self, task_id):
        if task_id not in self.completed_tasks:
            self.completed_tasks.append(task_id)
        if task_id in self.pending_tasks:
            self.pending_tasks.remove(task_id)

    def is_task_complete(self, task_id):
        return task_id in self.completed_tasks

    def update_from_agent(self, agent_type, method_id, output):
        facts = self.facts.setdefault(method_id, {})
        if agent_type == "SafetyAgent":
            facts["safetyChecklist"] = {
                r["id"]: r["status"] for r in output.get("checklistResults", [])
            }
            facts["confirmedUnreachableBranches"] = facts.get("confirmedUnreachableBranches", [])
        elif agent_type == "MCDCAgent":
            pairs = []
            for item in output.get("mcdcAnalysis", []):
                for p in item.get("mcdcPairs", []):
                    pairs.append(f"{item['decisionId']}-{p['condition']}")
            facts["mcdcPairsGenerated"] = pairs
            facts["coveredBranches"] = facts.get("coveredBranches", [])
        elif agent_type == "CoverageAgent":
            covered = [t.get("targetBranch") for t in output.get("generatedTests", [])]
            facts["coveredBranches"] = facts.get("coveredBranches", []) + covered
            facts["pendingBranches"] = output.get("pendingBranches", [])
        elif agent_type == "MutationAgent":
            facts["confirmedEquivalentMutants"] = [
                m["mutantId"] for m in output.get("skippedMutants", [])
            ]
            facts["killTestIds"] = [t["testId"] for t in output.get("killTests", [])]
        elif agent_type == "UnitTestAgent":
            facts.setdefault("unitTestIds", []).append(output.get("testId"))

    def __repr__(self):
        return f"AgentMemoryLayer(run_id={self.run_id}, facts={len(self.facts)} methods)"
