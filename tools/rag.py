"""RAG query tool.

Wraps :class:`memory.vector_store.VectorStore` so the Agent can retrieve
prior experience (failed tests, citations, agreed-upon axioms) via
exactly the same tool-calling protocol used for File IO or web search.

Both DEVELOPMENT and PHILOSOPHY modes share this single tool; what
differs is *what's stored* — DEVELOPMENT typically writes code/test
artifacts, PHILOSOPHY writes argument summaries and references.
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
        description=(
            "Query the long-term memory store and return top-k similar entries."
        ),
        args_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "description": "Default 5, capped at 50."},
            },
            "required": ["query"],
        },
    )

    def __init__(self, store: Optional[VectorStore] = None) -> None:
        self._store = store or VectorStore()

    def call(self, args: Dict[str, Any]) -> Dict[str, Any]:
        query = args["query"].strip()
        if not query:
            raise ToolError("query is empty")
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
    """Companion writer tool. Optional; many workflows only need read-only RAG."""

    spec = ToolSpec(
        name="rag_upsert",
        description="Add or update entries in long-term memory.",
        args_schema={
            "type": "object",
            "properties": {
                "texts": {"type": "array"},
                "metadatas": {"type": "array"},
                "ids": {"type": "array"},
            },
            "required": ["texts"],
        },
    )

    def __init__(self, store: Optional[VectorStore] = None) -> None:
        self._store = store or VectorStore()

    def call(self, args: Dict[str, Any]) -> Dict[str, Any]:
        texts = args["texts"]
        if not isinstance(texts, list) or not texts:
            raise ToolError("texts must be a non-empty array")
        ids = self._store.upsert(
            texts=texts,
            metadatas=args.get("metadatas"),
            ids=args.get("ids"),
        )
        return {"ids": ids, "count": len(ids)}


__all__ = ["RagQueryTool", "RagUpsertTool"]
