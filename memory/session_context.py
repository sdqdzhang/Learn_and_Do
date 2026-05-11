"""Short-term working memory.

Owns the running ``List[ChatMessage]`` for the current Agent session,
counts tokens, and triggers a summarisation pass when the budget is
exceeded. The summariser itself is *injected* (typically a small wrapper
around ``LLMClient``) so this module stays dependency-light.

Design notes
------------
- ``tiktoken`` is optional: if unavailable or the configured encoding
  is unknown, we fall back to ``len(text) / 4`` which is the standard
  rough approximation for English text. The fallback keeps the module
  usable in CI / unit tests without heavy native deps.
- The compressor preserves the first SYSTEM message (the role/mode
  prompt) and the last ``preserve_recent`` messages verbatim. Anything
  in between is collapsed into a single SYSTEM "memory digest".
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable, List, Optional

from core.schema import ChatMessage, MessageRole

logger = logging.getLogger(__name__)


Summarizer = Callable[[List[ChatMessage]], str]


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ContextConfig:
    max_tokens: int = 8000
    preserve_recent: int = 4
    tiktoken_encoding: str = "cl100k_base"

    @classmethod
    def from_env(cls) -> "ContextConfig":
        return cls(
            max_tokens=int(os.getenv("CONTEXT_MAX_TOKENS", "8000")),
            preserve_recent=int(os.getenv("CONTEXT_PRESERVE_RECENT", "4")),
            tiktoken_encoding=os.getenv("CONTEXT_TIKTOKEN_ENCODING", "cl100k_base"),
        )


# --------------------------------------------------------------------------- #
# Token counter helper
# --------------------------------------------------------------------------- #

class _TokenCounter:
    """Best-effort token counter; cached so we don't reload tiktoken."""

    def __init__(self, encoding_name: str) -> None:
        self._encoding_name = encoding_name
        self._encoder = None
        self._load_attempted = False

    def count(self, text: str) -> int:
        if text is None:
            return 0
        encoder = self._encoder_lazy()
        if encoder is None:
            return max(1, len(text) // 4)
        return len(encoder.encode(text))

    def _encoder_lazy(self):
        if self._load_attempted:
            return self._encoder
        self._load_attempted = True
        try:
            import tiktoken  # type: ignore
        except ImportError:
            logger.debug("tiktoken not installed; using length/4 fallback")
            return None
        try:
            self._encoder = tiktoken.get_encoding(self._encoding_name)
        except (KeyError, ValueError):
            logger.warning(
                "tiktoken encoding %r not found; falling back to length/4",
                self._encoding_name,
            )
            self._encoder = None
        return self._encoder


# --------------------------------------------------------------------------- #
# SessionContext
# --------------------------------------------------------------------------- #

class SessionContext:
    """Append-only message log with automatic compaction."""

    def __init__(
        self,
        config: Optional[ContextConfig] = None,
        summarizer: Optional[Summarizer] = None,
    ) -> None:
        self._config = config or ContextConfig.from_env()
        self._counter = _TokenCounter(self._config.tiktoken_encoding)
        self._summarizer = summarizer
        self._messages: List[ChatMessage] = []

    # ------------------- public API ------------------- #

    def set_summarizer(self, summarizer: Summarizer) -> None:
        self._summarizer = summarizer

    def add(self, message: ChatMessage) -> None:
        self._messages.append(message)
        self.compress_if_needed()

    def extend(self, messages: List[ChatMessage]) -> None:
        for m in messages:
            self._messages.append(m)
        self.compress_if_needed()

    def messages(self) -> List[ChatMessage]:
        # Return a shallow copy so callers can't mutate internal state.
        return list(self._messages)

    def to_openai(self) -> List[dict]:
        return [m.to_openai() for m in self._messages]

    def token_count(self) -> int:
        return sum(self._counter.count(m.content) for m in self._messages)

    def compress_if_needed(self) -> bool:
        """Compress middle messages if over budget. Returns True if it ran."""
        if self.token_count() <= self._config.max_tokens:
            return False
        if self._summarizer is None:
            logger.warning(
                "context over budget (%d > %d) but no summarizer is configured",
                self.token_count(),
                self._config.max_tokens,
            )
            return False
        return self._compact_once()

    def reset(self) -> None:
        self._messages.clear()

    # ------------------- internals ------------------- #

    def _compact_once(self) -> bool:
        if len(self._messages) <= self._config.preserve_recent + 1:
            # Nothing to compress; recent buffer already covers the log.
            return False

        # Slice off the leading SYSTEM block (we keep it as the anchor) and
        # the trailing ``preserve_recent`` messages.
        head: List[ChatMessage] = []
        idx = 0
        if self._messages and self._messages[0].role is MessageRole.SYSTEM:
            head = [self._messages[0]]
            idx = 1

        tail = (
            self._messages[-self._config.preserve_recent:]
            if self._config.preserve_recent > 0
            else []
        )
        middle = self._messages[idx: len(self._messages) - len(tail)]

        if not middle:
            return False

        try:
            digest_text = self._summarizer(middle)  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001 -- log + skip; never crash a turn on summarize
            logger.exception("summarizer failed; keeping full context: %s", exc)
            return False

        digest = ChatMessage(
            role=MessageRole.SYSTEM,
            content=f"[memory digest]\n{digest_text.strip()}",
            metadata={"compressed_count": len(middle)},
        )

        self._messages = head + [digest] + tail
        logger.info(
            "compressed %d messages into a digest; new token count %d",
            len(middle),
            self.token_count(),
        )
        return True


__all__ = ["SessionContext", "ContextConfig", "Summarizer"]
