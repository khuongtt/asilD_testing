BATCH_A = {"SafetyAgent", "UnitTestAgent"}
BATCH_B = {"MCDCAgent", "CoverageAgent"}
BATCH_C = {"MutationAgent"}


class AgentBatcher:
    @staticmethod
    def group_tasks(tasks):
        by_method = {}
        for t in tasks:
            by_method.setdefault(t["methodId"], []).append(t)
        batches = []
        for mid, method_tasks in by_method.items():
            remaining = list(method_tasks)
            batch_a = [t for t in remaining if t["agentType"] in BATCH_A]
            if batch_a:
                batches.append({"batch": "A", "tasks": batch_a})
                remaining = [t for t in remaining if t not in batch_a]
            batch_b = [t for t in remaining if t["agentType"] in BATCH_B]
            if batch_b:
                batches.append({"batch": "B", "tasks": batch_b})
                remaining = [t for t in remaining if t not in batch_b]
            batch_c = [t for t in remaining if t["agentType"] in BATCH_C]
            if batch_c:
                batches.append({"batch": "C", "tasks": batch_c})
                remaining = [t for t in remaining if t not in batch_c]
            for t in remaining:
                batches.append({"batch": "SINGLE", "tasks": [t]})
        return batches
