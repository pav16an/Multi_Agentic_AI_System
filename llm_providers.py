from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Protocol


SUPPORTED_PROVIDERS = ("groq", "openai", "anthropic")

DEFAULT_MODELS: Dict[str, str] = {
    "groq": "llama-3.3-70b-versatile",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-sonnet-latest",
}

PROVIDER_MODEL_SUGGESTIONS: Dict[str, List[str]] = {
    "groq": [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
    ],
    "openai": [
        "gpt-4o-mini",
        "gpt-4.1-mini",
    ],
    "anthropic": [
        "claude-3-5-sonnet-latest",
        "claude-3-5-haiku-latest",
    ],
}


class ProviderError(ValueError):
    """Raised when provider configuration or calls are invalid."""


class LLMProvider(Protocol):
    def complete(
        self,
        *,
        prompt: str,
        model: str,
        api_key: str,
        temperature: float = 0.0,
        max_tokens: int = 1000,
    ) -> str:
        """Generate a completion for a single prompt."""


def normalize_provider(provider: str) -> str:
    normalized = (provider or "").strip().lower()
    if normalized not in SUPPORTED_PROVIDERS:
        raise ProviderError(
            f"Unsupported provider '{provider}'. Use one of: {', '.join(SUPPORTED_PROVIDERS)}"
        )
    return normalized


def resolve_model(provider: str, model: str | None) -> str:
    selected = (model or "").strip()
    if selected:
        return selected
    return DEFAULT_MODELS[provider]


@dataclass
class GroqAdapter:
    def complete(
        self,
        *,
        prompt: str,
        model: str,
        api_key: str,
        temperature: float = 0.0,
        max_tokens: int = 1000,
    ) -> str:
        try:
            from groq import Groq
        except ImportError as exc:
            raise ProviderError(
                "Groq SDK not installed. Add 'groq' to dependencies."
            ) from exc

        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (response.choices[0].message.content or "").strip()


@dataclass
class OpenAIAdapter:
    def complete(
        self,
        *,
        prompt: str,
        model: str,
        api_key: str,
        temperature: float = 0.0,
        max_tokens: int = 1000,
    ) -> str:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderError(
                "OpenAI SDK not installed. Add 'openai' to dependencies."
            ) from exc

        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (response.choices[0].message.content or "").strip()


@dataclass
class AnthropicAdapter:
    def complete(
        self,
        *,
        prompt: str,
        model: str,
        api_key: str,
        temperature: float = 0.0,
        max_tokens: int = 1000,
    ) -> str:
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderError(
                "Anthropic SDK not installed. Add 'anthropic' to dependencies."
            ) from exc

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )

        chunks = []
        for block in response.content:
            if getattr(block, "type", None) == "text":
                chunks.append(block.text)
        return "\n".join(chunks).strip()


def get_provider_adapter(provider: str) -> LLMProvider:
    normalized = normalize_provider(provider)
    if normalized == "groq":
        return GroqAdapter()
    if normalized == "openai":
        return OpenAIAdapter()
    if normalized == "anthropic":
        return AnthropicAdapter()
    raise ProviderError(f"Unsupported provider '{provider}'.")
