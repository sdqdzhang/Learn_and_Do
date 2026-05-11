"""Unified exception hierarchy for Tiny-Devin.

The tree has two top-level branches that map directly onto the workflow
layer's decision logic:

    TinyDevinError
    ├── RetryableError   --> workflow may retry within the same session
    └── FatalError       --> workflow must abort the session

Domain-specific failure signals (a unit test failing, a hypothesis being
falsified) are NOT modeled as exceptions; they are data carried inside
``ExecutionResult`` / ``Evidence`` and surfaced to the workflow via
``EvidenceConflict``. This keeps the exception layer purpose-agnostic so
both DEVELOPMENT and PHILOSOPHY modes share the same catch sites.
"""

from __future__ import annotations

from typing import Any, Optional


class TinyDevinError(Exception):
    """Root of every custom exception raised by this project."""

    def __init__(self, message: str = "", *, details: Optional[Any] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def __str__(self) -> str:  # pragma: no cover - trivial
        if self.details is None:
            return self.message
        return f"{self.message} | details={self.details!r}"


class RetryableError(TinyDevinError):
    """Errors the workflow may retry without aborting the session."""


class FatalError(TinyDevinError):
    """Errors that must terminate the current session immediately."""


# --------------------------------------------------------------------------- #
# Retryable
# --------------------------------------------------------------------------- #

class CodeFormatError(RetryableError):
    """LLM output did not contain a parseable ``<file>`` or ``<thought>`` block."""


class MissingPathError(RetryableError):
    """A code block was found but had no ``path`` attribute."""


class LLMTimeoutError(RetryableError):
    """LLM did not respond within the configured timeout / retry budget."""


class EvidenceConflict(RetryableError):
    """Executor produced data that contradicts the current claim.

    - DEVELOPMENT mode: tests failed; ask Coder to fix.
    - PHILOSOPHY  mode: data falsified the hypothesis; ask Philosopher
      to revise the model.
    """


class ToolError(RetryableError):
    """A tool invocation failed in a way the workflow can retry.

    Distinct from SandboxViolation: the tool ran but produced an error
    result (network hiccup, file not found, bad args, etc.).
    """


class MemoryError(RetryableError):
    """Memory layer transient failure (vector store disk hiccup, etc.)."""


# --------------------------------------------------------------------------- #
# Fatal
# --------------------------------------------------------------------------- #

class SandboxViolation(FatalError):
    """Agent attempted a forbidden operation (e.g. ``rm -rf /``)."""


class ContainerImageError(FatalError):
    """Required base Docker image is missing or cannot be built."""


class ResourceExhausted(FatalError):
    """Container exceeded configured memory / GPU / disk quotas."""


class ConfigurationError(FatalError):
    """Required configuration is missing or invalid (e.g. unreachable Ollama)."""


__all__ = [
    "TinyDevinError",
    "RetryableError",
    "FatalError",
    "CodeFormatError",
    "MissingPathError",
    "LLMTimeoutError",
    "EvidenceConflict",
    "ToolError",
    "MemoryError",
    "SandboxViolation",
    "ContainerImageError",
    "ResourceExhausted",
    "ConfigurationError",
]
