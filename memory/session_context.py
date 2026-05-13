"""短期工作记忆。

持有当前 Agent session 正在运行的 ``List[ChatMessage]``，计数 token，并在
超出预算时触发一次摘要压缩。摘要器本身是 *注入式* 的（通常是一个对
``LLMClient`` 的薄包装），这样本模块自身保持依赖最少。

设计要点
--------
- ``tiktoken`` 是可选依赖：如果没装或者指定的 encoding 不存在，会退回到
  ``len(text) / 4`` 这个英文文本下经典的粗略估算。这个降级让模块在 CI /
  单测里不依赖重型 native 包也能跑。
- 压缩策略：保留首条 SYSTEM 消息（绑定角色/模式 prompt）和最近 ``preserve_recent``
  条消息原样不动，中间部分合并为一条 SYSTEM 角色的"记忆摘要"。
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
# 配置
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
# Token 计数辅助类
# --------------------------------------------------------------------------- #

class _TokenCounter:
    """尽力而为的 token 计数器；做了缓存避免反复 import tiktoken。"""

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
            logger.debug("tiktoken 未安装；退回到 length/4 估算")
            return None
        try:
            self._encoder = tiktoken.get_encoding(self._encoding_name)
        except (KeyError, ValueError):
            logger.warning(
                "tiktoken encoding %r 未找到；退回到 length/4 估算",
                self._encoding_name,
            )
            self._encoder = None
        return self._encoder


# --------------------------------------------------------------------------- #
# SessionContext
# --------------------------------------------------------------------------- #

class SessionContext:
    """追加式消息日志，自带自动压缩能力。"""

    def __init__(
        self,
        config: Optional[ContextConfig] = None,
        summarizer: Optional[Summarizer] = None,
    ) -> None:
        self._config = config or ContextConfig.from_env()
        self._counter = _TokenCounter(self._config.tiktoken_encoding)
        self._summarizer = summarizer
        self._messages: List[ChatMessage] = []

    # ------------------- 公共 API ------------------- #

    def set_summarizer(self, summarizer: Summarizer) -> None:
        self._summarizer = summarizer

    def add(self, message: ChatMessage) -> None:
        self._messages.append(message)
        self.compress_if_needed()

    def replace_last_assistant_content(self, new_content: str) -> bool:
        """将最近一条 ASSISTANT 消息的 ``content`` 替换为 ``new_content``。"""
        for i in range(len(self._messages) - 1, -1, -1):
            if self._messages[i].role is MessageRole.ASSISTANT:
                cur = self._messages[i]
                self._messages[i] = cur.model_copy(update={"content": new_content})
                return True
        return False

    def extend(self, messages: List[ChatMessage]) -> None:
        for m in messages:
            self._messages.append(m)
        self.compress_if_needed()

    def messages(self) -> List[ChatMessage]:
        # 返回浅拷贝，避免外部直接改我们的内部状态。
        return list(self._messages)

    def to_openai(self) -> List[dict]:
        return [m.to_openai() for m in self._messages]

    def token_count(self) -> int:
        return sum(self._counter.count(m.content) for m in self._messages)

    def compress_if_needed(self) -> bool:
        """若已超预算则压缩中段消息；返回是否实际执行了压缩。"""
        if self.token_count() <= self._config.max_tokens:
            return False
        if self._summarizer is None:
            logger.warning(
                "上下文已超预算（%d > %d）但未配置 summarizer",
                self.token_count(),
                self._config.max_tokens,
            )
            return False
        return self._compact_once()

    def reset(self) -> None:
        self._messages.clear()

    def export_snapshot_dict(self) -> Dict[str, Any]:
        """导出可 JSON 序列化快照，供 :class:`core.schema.TraceEvent` 嵌入。"""
        return {
            "version": 1,
            "messages": [m.model_dump(mode="json") for m in self._messages],
        }

    def replace_from_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """用快照覆盖当前消息列表（用于时间旅行恢复）。"""
        raw = snapshot.get("messages") if isinstance(snapshot, dict) else None
        if not isinstance(raw, list):
            self._messages.clear()
            return
        self._messages = [ChatMessage.model_validate(m) for m in raw]

    # ------------------- 内部实现 ------------------- #

    def _compact_once(self) -> bool:
        if len(self._messages) <= self._config.preserve_recent + 1:
            # 没什么可压缩的；最近窗口已经覆盖整条日志。
            return False

        # 切出首条 SYSTEM 块（保留作为锚点）与末尾 ``preserve_recent`` 条。
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
        except Exception as exc:  # noqa: BLE001 -- 出错时保留原上下文，不让一轮压缩搞挂整个会话
            logger.exception("summarizer 调用失败，保留完整上下文：%s", exc)
            return False

        digest = ChatMessage(
            role=MessageRole.SYSTEM,
            content=f"[记忆摘要]\n{digest_text.strip()}",
            metadata={"compressed_count": len(middle)},
        )

        self._messages = head + [digest] + tail
        logger.info(
            "已把 %d 条消息压缩成一条摘要；当前 token 数 %d",
            len(middle),
            self.token_count(),
        )
        return True


__all__ = ["SessionContext", "ContextConfig", "Summarizer"]
