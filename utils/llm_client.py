"""LLM 客户端：通过 OpenAI 兼容接口对接 Ollama。

本模块刻意保持与任务模式无关（不管是代码、哲学还是别的），只负责三件事：

1. 从环境变量（或显式传入的 ``LLMConfig``）读取连接配置。
2. 发起一次 chat completion 请求，自动重试瞬态错误。
3. 返回 assistant 的文本内容。

更上层的职责（prompt 拼装、响应解析）分别由 ``prompt_templates`` 与
``utils.parser`` 承担。
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
# 配置
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class LLMConfig:
    """客户端需要的全部参数。不可变，方便安全地多处共享同一份配置。"""

    base_url: str
    api_key: str
    model: str
    timeout_seconds: float
    max_retries: int
    temperature: float

    @classmethod
    def from_env(cls, *, load_dotenv_file: bool = True) -> "LLMConfig":
        """从环境变量构造一份配置。

        ``load_dotenv_file=True`` 时会尝试在当前工作目录加载 ``.env``。
        已存在的环境变量不会被覆盖（与 python-dotenv 默认行为一致）。
        """
        if load_dotenv_file:
            try:
                from dotenv import load_dotenv

                load_dotenv()
            except ImportError:
                logger.debug("未安装 python-dotenv，跳过 .env 加载")

        return cls(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            api_key=os.getenv("OLLAMA_API_KEY", "ollama"),
            model=os.getenv("OLLAMA_MODEL", "your-model-name"),
            timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "120")),
            max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.2")),
        )


# 宽松的别名 —— 让调用方不必为了塞一个 {"role": ..., "content": ...} dict 而
# 去 import openai 自己的 typed dict。
ChatMessage = Mapping[str, Any]


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #

class LLMClient:
    """对 ``openai.OpenAI`` 的薄包装，预先配置好对接 Ollama 后端。"""

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
    # 公共 API
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
        """同步 chat completion，返回 assistant 的文本内容。

        会在遇到超时、瞬态连接错误、限流响应时按 ``config.max_retries`` 重试。
        预算耗尽时抛 :class:`LLMTimeoutError`。
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
        """流式版本，逐 chunk 产出文本增量。

        流式响应一旦开始就不会中途重试；只有最开始那一次请求失败时才会按
        与 :meth:`chat` 相同的策略重试。
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
    # 内部实现
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
                logger.warning("LLM 超时（第 %s/%s 次尝试）：%s", attempt, attempts, exc)
            except (APIConnectionError, RateLimitError) as exc:
                last_error = exc
                logger.warning("LLM 瞬态错误（第 %s/%s 次尝试）：%s", attempt, attempts, exc)
            except APIError as exc:
                # 非瞬态 API 错误：立即抛出，不再重试。
                logger.error("LLM API 错误：%s", exc)
                raise

            if attempt < attempts:
                backoff = min(2 ** (attempt - 1), 10)
                time.sleep(backoff)

        raise LLMTimeoutError(
            f"LLM 请求在 {attempts} 次尝试后仍然失败",
            details=str(last_error) if last_error else None,
        ) from last_error


__all__ = ["LLMClient", "LLMConfig", "ChatMessage"]
