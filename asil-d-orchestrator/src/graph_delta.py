import hashlib
from datetime import datetime


class GraphDeltaEngine:
    def compute(self, run_id, commit_hash, prev_manifest, current_methods, coverage_gaps=None, surviving_mutants=None):
        prev = prev_manifest or {}
        current_ids = {m["id"]: m for m in current_methods}
        prev_ids = set(prev.keys())
        curr_ids = set(current_ids.keys())

        dirty_methods = []
        new_methods = []
        deleted_methods = list(prev_ids - curr_ids)
        clean_methods = []
        auto_coverage_gaps = []
        auto_surviving = []
        stale_semantic = []
        stale_traceability = []

        for mid, m in current_ids.items():
            prev_hash = prev.get(mid, {}).get("methodHash")
            if prev_hash is None:
                new_methods.append(mid)
                m["status"] = "NEW"
                dirty_methods.append(mid)
            elif prev_hash != m["methodHash"]:
                m["status"] = "DIRTY"
                dirty_methods.append(mid)
            else:
                m["status"] = "CLEAN"
                clean_methods.append(mid)

            branch = m.get("coverage", {}).get("coverage", {}).get("branch", 0)
            if branch < 95:
                auto_coverage_gaps.append(mid)
            mut_score = m.get("mutation", {}).get("mutationScore", 100)
            if mut_score < 90:
                auto_surviving.append(mid)

            if m.get("status") in ("DIRTY", "NEW"):
                for req_id in m.get("semantic", {}).get("linkedRequirements", []):
                    if req_id not in stale_traceability:
                        stale_traceability.append(req_id)
                stale_semantic.append(mid)

        report = {
            "runId": run_id,
            "commit": commit_hash,
            "prevCommit": None,
            "dirtyMethods": dirty_methods,
            "newMethods": new_methods,
            "deletedMethods": deleted_methods,
            "cleanMethods": clean_methods,
            "coverageGaps": coverage_gaps if coverage_gaps is not None else auto_coverage_gaps,
            "survivingMutants": surviving_mutants if surviving_mutants is not None else auto_surviving,
            "staleSemanticNodes": stale_semantic,
            "staleTraceabilityLinks": stale_traceability,
        }
        return report

    def methods_needing_agents(self, delta, methods, bootstrap_pending=None):
        ids = set(delta["dirtyMethods"] + delta["newMethods"])
        ids.update(delta.get("coverageGaps", []))
        ids.update(delta.get("survivingMutants", []))
        if bootstrap_pending:
            ids.update(bootstrap_pending)
        by_id = {m["id"]: m for m in methods}
        return [by_id[i] for i in ids if i in by_id]

    def invalidate_cache_keys(self, method):
        return method.get("status") in ("DIRTY", "NEW")

    def detect_major_refactor(self, prev_manifest, current_methods, threshold=0.6):
        if not prev_manifest:
            return False
        prev_ids = set(prev_manifest.keys())
        curr_by_id = {m["id"]: m for m in current_methods}
        curr_ids = set(curr_by_id.keys())
        all_ids = prev_ids | curr_ids
        if not all_ids:
            return False
        changed = 0
        for mid in all_ids:
            if mid not in prev_ids or mid not in curr_ids:
                changed += 1
            elif prev_manifest[mid].get("methodHash") != curr_by_id[mid].get("methodHash"):
                changed += 1
        return (changed / len(all_ids)) > threshold
