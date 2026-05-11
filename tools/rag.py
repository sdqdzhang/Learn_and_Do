"""RAG 检索工具。

包装 :class:`memory.vector_store.VectorStore`，让 Agent 通过和文件 IO、网页
搜索完全相同的工具协议来检索历史经验（失败的单测、引文、达成共识的公理
等）。

DEVELOPMENT 与 PHILOSOPHY 模式共用同一个工具；它们的区别只是 *存什么* ——
代码模式通常落代码 / 测试相关的片段，哲学模式落论据 / 反例。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.exceptions import ToolError
from core.schema import ToolSpec
from memory.vector_store import VectorStore
from tools.base import Tool


class RagQueryTool(Tool):
    spec = ToolSpec(
        name="rag_query",
        description="查询长期记忆库，返回与查询语义最相近的前 k 条记录。",
        args_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要检索的查询文本"},
                "k": {
                    "type": "integer",
                    "description": "返回条数，默认 5，上限 50。",
                },
            },
            "required": ["query"],
        },
    )

    def __init__(self, store: Optional[VectorStore] = None) -> None:
        self._store = store or VectorStore()

    def call(self, args: Dict[str, Any]) -> Dict[str, Any]:
        query = args["query"].strip()
        if not query:
            raise ToolError("query 不能为空")
        k = max(1, min(int(args.get("k", 5)), 50))

        hits = self._store.query(query, k=k)
        return {
            "query": query,
            "hits": [
                {
                    "id": h.id,
                    "text": h.text,
                    "metadata": h.metadata,
                    "distance": h.distance,
                }
                for h in hits
            ],
        }


class RagUpsertTool(Tool):
    """配套的写入工具。可选 —— 很多工作流只需要只读的 RAG。"""

    spec = ToolSpec(
        name="rag_upsert",
        description="向长期记忆库新增或更新若干条记录。",
        args_schema={
            "type": "object",
            "properties": {
                "texts": {"type": "array", "description": "要写入的文本列表"},
                "metadatas": {
                    "type": "array",
                    "description": "可选，与 texts 一一对应的元数据 dict 列表",
                },
                "ids": {
                    "type": "array",
                    "description": "可选，自定义 ID 列表；不填则自动生成。",
                },
            },
            "required": ["texts"],
        },
    )

    def __init__(self, store: Optional[VectorStore] = None) -> None:
        self._store = store or VectorStore()

    def call(self, args: Dict[str, Any]) -> Dict[str, Any]:
        texts = args["texts"]
        if not isinstance(texts, list) or not texts:
            raise ToolError("texts 必须是一个非空数组")
        ids = self._store.upsert(
            texts=texts,
            metadatas=args.get("metadatas"),
            ids=args.get("ids"),
        )
        return {"ids": ids, "count": len(ids)}


__all__ = ["RagQueryTool", "RagUpsertTool"]
