"""LLM client wrapping Ollama via the OpenAI-compatible API.

This module is intentionally agnostic to task type (code / philosophy /
anything else). It only knows how to:

1. Read connection settings from environment variables (or an explicit
   ``LLMConfig``).
2. Send a chat-completion request, with retry on transient failures.
3. Return the assistant's text content.

Higher layers are responsible for prompt construction (``prompt_templates``)
and response parsing (``utils.parser``).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Iterator, List, Mapping, Optional

from openai import APIConnectionError, APIError, APITimeoutError, OpenAI, RateLimitError

from core.exceptions import LLMTimeoutError

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class LLMConfig:
    """All knobs the client cares about. Immutable so it is safe to share."""

    base_url: str
    api_key: str
    model: str
    timeout_seconds: float
    max_retries: int
    temperature: float

    @classmethod
    def from_env(cls, *, load_dotenv_file: bool = True) -> "LLMConfig":
        """Build a config from environment variables.

        ``load_dotenv_file=True`` will try to load a ``.env`` file from the
        current working directory if one exists. Existing env vars are NOT
        overridden, matching python-dotenv's default behaviour.
        """
        if load_dotenv_file:
            try:
                from dotenv import load_dotenv

                load_dotenv()
            except ImportError:
                logger.debug("python-dotenv not installed; skipping .env load")

        return cls(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            api_key=os.getenv("OLLAMA_API_KEY", "ollama"),
            model=os.getenv("OLLAMA_MODEL", "your-model-name"),
            timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "120")),
            max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.2")),
        )


# A loose alias so callers don't need to import OpenAI's types just to
# pass a list of {"role": ..., "content": ...} dicts.
ChatMessage = Mapping[str, Any]


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #

class LLMClient:
    """Thin wrapper around ``openai.OpenAI`` configured for an Ollama backend."""

    def __init__(self, config: Optional[LLMConfig] = None) -> None:
        self._config = config or LLMConfig.from_env()
        self._client = OpenAI(
            base_url=self._config.base_url,
            api_key=self._config.api_key,
            timeout=self._config.timeout_seconds,
        )

    @property
    def config(self) -> LLMConfig:
        return self._config

    @property
    def model(self) -> str:
        return self._config.model

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #

    def chat(
        self,
        messages: List[ChatMessage],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        extra: Optional[dict] = None,
    ) -> str:
        """Synchronous chat completion. Returns the assistant text content.

        Retries on timeout, transient connection errors, and rate-limit
        responses, up to ``config.max_retries`` extra attempts. After the
        budget is exhausted, raises :class:`LLMTimeoutError`.
        """
        return self._with_retries(
            lambda: self._chat_once(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                extra=extra,
            )
        )

    def stream_chat(
        self,
        messages: List[ChatMessage],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        extra: Optional[dict] = None,
    ) -> Iterator[str]:
        """Streaming variant. Yields content deltas as they arrive.

        Streaming responses are NOT retried mid-stream; if the very first
        request errors out, the same retry policy as ``chat`` applies.
        """
        stream = self._with_retries(
            lambda: self._client.chat.completions.create(
                model=model or self._config.model,
                messages=list(messages),
                temperature=self._coerce_temperature(temperature),
                max_tokens=max_tokens,
                stream=True,
                **(extra or {}),
            )
        )

        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            content = getattr(delta, "content", None)
            if content:
                yield content

    # --------------------------------------------------------------------- #
    # Internals
    # --------------------------------------------------------------------- #

    def _chat_once(
        self,
        *,
        messages: List[ChatMessage],
        model: Optional[str],
        temperature: Optional[float],
        max_tokens: Optional[int],
        extra: Optional[dict],
    ) -> str:
        response = self._client.chat.completions.create(
            model=model or self._config.model,
            messages=list(messages),
            temperature=self._coerce_temperature(temperature),
            max_tokens=max_tokens,
            **(extra or {}),
        )
        if not response.choices:
            return ""
        return response.choices[0].message.content or ""

    def _coerce_temperature(self, value: Optional[float]) -> float:
        return self._config.temperature if value is None else value

    def _with_retries(self, call):
        attempts = self._config.max_retries + 1
        last_error: Optional[BaseException] = None

        for attempt in range(1, attempts + 1):
            try:
                return call()
            except APITimeoutError as exc:
                last_error = exc
                logger.warning("LLM timeout (attempt %s/%s): %s", attempt, attempts, exc)
            except (APIConnectionError, RateLimitError) as exc:
                last_error = exc
                logger.warning("LLM transient error (attempt %s/%s): %s", attempt, attempts, exc)
            except APIError as exc:
                # Non-transient API error: surface immediately, don't retry.
                logger.error("LLM API error: %s", exc)
                raise

            if attempt < attempts:
                backoff = min(2 ** (attempt - 1), 10)
                time.sleep(backoff)

        raise LLMTimeoutError(
            f"LLM request failed after {attempts} attempt(s)",
            details=str(last_error) if last_error else None,
        ) from last_error


__all__ = ["LLMClient", "LLMConfig", "ChatMessage"]
