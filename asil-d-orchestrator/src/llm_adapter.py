"""LLM provider adapters for the ASIL-D Test Orchestrator.

Supports:
- GitHub Copilot (OpenAI-compatible API at api.githubcopilot.com)
- Anthropic Claude
- OpenAI
- Any OpenAI-compatible endpoint (vLLM, Ollama, Together, etc.)
- Simulated (no network — for local testing)

Configuration is read from environment variables (or a .env file).
"""
import json
import os
import time
from pathlib import Path


def _load_dotenv():
    """Load .env file from the current working directory or project root if present.

    Uses python-dotenv if available, else falls back to a built-in minimal parser.
    Never overrides existing environment variables.
    """
    for candidate in [Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"]:
        if not candidate.exists():
            continue
        try:
            from dotenv import load_dotenv
            load_dotenv(candidate, override=False)
            return
        except ImportError:
            pass
        # Built-in fallback parser
        try:
            for raw_line in candidate.read_text().splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if not key:
                    continue
                # Strip optional surrounding quotes
                if (value.startswith('"') and value.endswith('"')) or \
                   (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
                # Don't override existing env vars
                if key not in os.environ:
                    os.environ[key] = value
        except OSError:
            pass
        return


_load_dotenv()


# --- Provider presets ---------------------------------------------------------

PROVIDER_PRESETS = {
    "copilot": {
        "base_url": "https://api.githubcopilot.com",
        "default_model": "gpt-4o",
        "requires_key": True,
    },
    "anthropic": {
        "base_url": None,  # uses official SDK
        "default_model": "claude-sonnet-4-5",
        "requires_key": True,
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
        "requires_key": True,
    },
    "openai-compatible": {
        "base_url": None,  # user must provide via LLM_BASE_URL
        "default_model": "gpt-3.5-turbo",
        "requires_key": False,
    },
}


def _resolve_config():
    """Resolve active provider config from env vars.

    Returns: (provider_name, api_key, base_url, model, source_dict)
    """
    provider = (os.environ.get("LLM_PROVIDER") or "").strip().lower() or None
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("LLM_MODEL") or os.environ.get("ANTHROPIC_MODEL")
    base_url = os.environ.get("LLM_BASE_URL")

    # Backward compat: legacy ANTHROPIC_API_KEY-only setup
    if not provider:
        if os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("LLM_PROVIDER"):
            provider = "anthropic"
        elif os.environ.get("OPENAI_API_KEY") and not os.environ.get("LLM_PROVIDER"):
            provider = "openai"
        else:
            provider = "simulated"

    source = {"provider": provider, "key_source": "none"}

    if provider == "simulated":
        return "simulated", None, None, None, source

    if provider not in PROVIDER_PRESETS:
        raise ValueError(
            f"Unknown LLM_PROVIDER={provider!r}. "
            f"Supported: {', '.join(list(PROVIDER_PRESETS) + ['simulated'])}"
        )

    preset = PROVIDER_PRESETS[provider]
    if preset["requires_key"] and not api_key:
        # Fall back to simulated if no key is available
        source["provider"] = "simulated"
        source["key_source"] = "missing"
        return "simulated", None, None, None, source

    if provider == "anthropic" and not model:
        model = preset["default_model"]
    if provider in ("copilot", "openai", "openai-compatible") and not model:
        model = preset["default_model"]

    if not base_url and preset["base_url"]:
        base_url = preset["base_url"]

    if api_key:
        if api_key.startswith("ghp_") or api_key.startswith("github_pat_") or api_key.startswith("gho_"):
            source["key_source"] = "github_pat"
        elif api_key.startswith("sk-ant-") or api_key.startswith("sk_ant-"):
            source["key_source"] = "anthropic"
        elif api_key.startswith("sk-"):
            source["key_source"] = "openai"
        else:
            source["key_source"] = "custom"

    return provider, api_key, base_url, model, source


# --- Base adapter -------------------------------------------------------------

class LLMAdapter:
    """Abstract LLM adapter interface."""
    provider_name = "base"

    def invoke(self, agent_type, system_prompt, user_prompt, max_tokens=1200):
        raise NotImplementedError

    def describe(self):
        return {"provider": self.provider_name}


class SimulatedLLMAdapter(LLMAdapter):
    """No-network adapter — returns a static metadata stub."""
    provider_name = "simulated"

    def invoke(self, agent_type, system_prompt, user_prompt, max_tokens=1200):
        return {
            "agentType": agent_type,
            "simulated": True,
            "systemPromptLength": len(system_prompt),
            "userPromptLength": len(user_prompt),
            "maxTokens": max_tokens,
            "model": "simulated-adapter",
        }


# --- OpenAI-compatible adapter (Copilot, OpenAI, vLLM, Ollama, etc.) --------

class OpenAICompatibleAdapter(LLMAdapter):
    """Adapter for any OpenAI-compatible chat completions endpoint.

    Used for:
    - GitHub Copilot (api.githubcopilot.com)
    - Direct OpenAI (api.openai.com/v1)
    - vLLM, Ollama, Together, Anyscale, LM Studio, etc.
    """
    provider_name = "openai-compatible"

    def __init__(self, api_key, base_url, model, provider_label="openai-compatible"):
        try:
            from openai import OpenAI
        except ImportError as e:
            raise ImportError(
                "openai package is required for the OpenAI-compatible adapter. "
                "Install with: pip install openai"
            ) from e
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.provider_label = provider_label
        kwargs = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)

    def invoke(self, agent_type, system_prompt, user_prompt, max_tokens=1200):
        started = time.time()
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
                temperature=0.2,
            )
        except Exception as e:
            return {
                "agentType": agent_type,
                "provider": self.provider_label,
                "model": self.model,
                "error": str(e),
                "elapsedMs": int((time.time() - started) * 1000),
            }
        elapsed = int((time.time() - started) * 1000)
        choice = response.choices[0] if response.choices else None
        content = choice.message.content if choice and choice.message else ""
        usage = getattr(response, "usage", None)
        return {
            "agentType": agent_type,
            "provider": self.provider_label,
            "model": self.model,
            "baseUrl": self.base_url,
            "content": content,
            "promptTokens": getattr(usage, "prompt_tokens", None) if usage else None,
            "completionTokens": getattr(usage, "completion_tokens", None) if usage else None,
            "totalTokens": getattr(usage, "total_tokens", None) if usage else None,
            "finishReason": getattr(choice, "finish_reason", None) if choice else None,
            "elapsedMs": elapsed,
        }

    def describe(self):
        return {
            "provider": self.provider_label,
            "model": self.model,
            "baseUrl": self.base_url,
        }


# --- Anthropic adapter --------------------------------------------------------

class AnthropicAdapter(LLMAdapter):
    """Adapter for Anthropic Claude API.

    Uses the official `anthropic` SDK if available, else falls back to the
    OpenAI SDK with Anthropic's OpenAI-compatible endpoint.
    """
    provider_name = "anthropic"

    def __init__(self, api_key, model):
        self.api_key = api_key
        self.model = model
        self._client = None
        self._mode = None
        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=api_key)
            self._mode = "native"
        except ImportError:
            # Fallback to OpenAI-compatible endpoint
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    api_key=api_key,
                    base_url="https://api.anthropic.com/v1",
                )
                self._mode = "openai-compat"
            except ImportError as e:
                raise ImportError(
                    "Either `anthropic` or `openai` is required for the Anthropic adapter. "
                    "Install with: pip install anthropic   (or: pip install openai)"
                ) from e

    def invoke(self, agent_type, system_prompt, user_prompt, max_tokens=1200):
        started = time.time()
        try:
            if self._mode == "native":
                response = self._client.messages.create(
                    model=self.model,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                    max_tokens=max_tokens,
                    temperature=0.2,
                )
                content = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        content += block.text
                usage = response.usage
                return {
                    "agentType": agent_type,
                    "provider": "anthropic",
                    "model": self.model,
                    "mode": "native",
                    "content": content,
                    "promptTokens": getattr(usage, "input_tokens", None),
                    "completionTokens": getattr(usage, "output_tokens", None),
                    "totalTokens": (
                        (getattr(usage, "input_tokens", 0) or 0)
                        + (getattr(usage, "output_tokens", 0) or 0)
                    ) if usage else None,
                    "elapsedMs": int((time.time() - started) * 1000),
                }
            else:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=max_tokens,
                    temperature=0.2,
                )
                choice = response.choices[0] if response.choices else None
                content = choice.message.content if choice and choice.message else ""
                usage = getattr(response, "usage", None)
                return {
                    "agentType": agent_type,
                    "provider": "anthropic",
                    "model": self.model,
                    "mode": "openai-compat",
                    "content": content,
                    "promptTokens": getattr(usage, "prompt_tokens", None) if usage else None,
                    "completionTokens": getattr(usage, "completion_tokens", None) if usage else None,
                    "totalTokens": getattr(usage, "total_tokens", None) if usage else None,
                    "elapsedMs": int((time.time() - started) * 1000),
                }
        except Exception as e:
            return {
                "agentType": agent_type,
                "provider": "anthropic",
                "model": self.model,
                "error": str(e),
                "elapsedMs": int((time.time() - started) * 1000),
            }

    def describe(self):
        return {"provider": "anthropic", "model": self.model, "mode": self._mode}


# --- Factory ------------------------------------------------------------------

_cached_adapter = None
_cached_config = None


def get_llm_adapter(force_reload=False):
    """Return a configured LLMAdapter based on env vars.

    Resolution order:
    1. LLM_PROVIDER env var
    2. Legacy: ANTHROPIC_API_KEY → AnthropicAdapter
    3. Legacy: OPENAI_API_KEY → OpenAIAdapter
    4. Default: SimulatedLLMAdapter
    """
    global _cached_adapter, _cached_config
    if _cached_adapter is not None and not force_reload:
        return _cached_adapter

    provider, api_key, base_url, model, source = _resolve_config()
    _cached_config = source

    if provider == "simulated":
        _cached_adapter = SimulatedLLMAdapter()
    elif provider == "anthropic":
        _cached_adapter = AnthropicAdapter(api_key, model)
    else:  # copilot | openai | openai-compatible
        label = "github-copilot" if provider == "copilot" else provider
        _cached_adapter = OpenAICompatibleAdapter(api_key, base_url, model, provider_label=label)

    return _cached_adapter


def get_active_config():
    """Return the resolved configuration (for diagnostics / evidence)."""
    global _cached_config
    if _cached_config is None:
        _, _, _, _, _cached_config = _resolve_config()
    return dict(_cached_config)


def reset_adapter_cache():
    """Clear cached adapter — useful for tests."""
    global _cached_adapter, _cached_config
    _cached_adapter = None
    _cached_config = None
