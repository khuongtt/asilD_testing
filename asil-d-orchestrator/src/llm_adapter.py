import json
import os


class LLMAdapter:
    def invoke(self, agent_type, system_prompt, user_prompt, max_tokens=1200):
        raise NotImplementedError


class SimulatedLLMAdapter(LLMAdapter):
    """Default adapter — returns structured stubs without external API calls."""

    def invoke(self, agent_type, system_prompt, user_prompt, max_tokens=1200):
        return {
            "agentType": agent_type,
            "simulated": True,
            "systemPromptLength": len(system_prompt),
            "userPromptLength": len(user_prompt),
            "maxTokens": max_tokens,
        }


def get_llm_adapter():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return SimulatedLLMAdapter()
    return SimulatedLLMAdapter()
