import json


class PromptBuilder:
    OUTPUT_SCHEMAS = {
        "UnitTestAgent": '{"testId":"","code":"","qualityScore":null}',
        "MCDCAgent": '{"methodId":"","mcdcAnalysis":[]}',
        "CoverageAgent": '{"methodId":"","generatedTests":[],"pendingBranches":[]}',
        "MutationAgent": '{"methodId":"","killTests":[],"skippedMutants":[]}',
        "SafetyAgent": '{"methodId":"","checklistResults":[],"failCount":0,"blockEvidence":false}',
    }

    @classmethod
    def build(cls, agent_type, graph_slice, task_type, memory_facts=None):
        sem = graph_slice.get("semantic", {})
        memory_facts = memory_facts or {}
        constraints = (
            "Constraints:\n"
            "  - ASIL-D requires 100% branch and MC/DC coverage for safety-critical methods\n"
            "  - Every generated test must compile without warnings\n"
            "  - Test code must follow the naming pattern: test_{method}_{scenario}_{expected}\n"
            "  - Each test must contain at least one assertion statement\n"
            "  - Exception tests MUST use assertThrows with both type and message verification\n"
            "  - Quality score must be >= 70; regenerate if below threshold\n"
        )
        system = (
            f"You are an ISO 26262 ASIL D test engineer generating {agent_type} for Java methods.\n"
            "Respond ONLY with valid JSON matching the schema below.\n"
            f"Output schema:\n{cls.OUTPUT_SCHEMAS.get(agent_type, '{}')}\n"
            f"{constraints}"
        )
        user = (
            f"Method: {graph_slice.get('methodId')} ({graph_slice.get('methodName')})\n"
            f"ASIL Level: {sem.get('asilLevel')}\n"
            f"Domain: {sem.get('domain')}\n"
            f"Safety Critical: {sem.get('safetyCritical')}\n"
            f"Functional Description: {sem.get('functionalDescription')}\n"
            f"Safe State: {sem.get('safeState')}\n"
            f"Graph slice:\n{json.dumps(graph_slice, default=str)[:2000]}\n"
            f"Agent memory:\n{json.dumps(memory_facts, default=str)[:500]}\n"
            f"Task: {task_type}\n"
        )
        return system, user

    @classmethod
    def build_feedback(cls, agent_type, method_id, score, failed_dims):
        system = f"Regenerate {agent_type} output for method {method_id}. Respond ONLY with JSON."
        user = f"Previous score: {score}/100. Failed: {', '.join(failed_dims)}. Correct issues."
        return system, user
