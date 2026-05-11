"""Long-term experience memory backed by ChromaDB.

The wrapper hides ChromaDB's heavy import behind a *lazy* call: the
client is constructed only on first use, so importing this module is
cheap even when no vector query is ever issued.

The store is **mode-agnostic**: collections can hold whatever the Agent
deems memorable — code snippets, failed unit tests, philosophical
counter-arguments, citations.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from core.exceptions import MemoryError as TinyDevinMemoryError

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Config & data classes
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class VectorStoreConfig:
    persist_dir: str
    collection: str

    @classmethod
    def from_env(cls) -> "VectorStoreConfig":
        return cls(
            persist_dir=os.getenv("VECTOR_PERSIST_DIR", "./runtime/vector_store"),
            collection=os.getenv("VECTOR_COLLECTION", "tiny-devin"),
        )


class VectorHit(BaseModel):
    id: str
    text: str
    metadata: Dict[str, Any] = {}
    distance: Optional[float] = None


# --------------------------------------------------------------------------- #
# VectorStore
# --------------------------------------------------------------------------- #

class VectorStore:
    """Thin ChromaDB wrapper with idempotent upserts and similarity queries.

    Heavy imports (chromadb + embedding model) are deferred to the first
    call, which lets callers instantiate :class:`VectorStore` cheaply.
    """

    def __init__(self, config: Optional[VectorStoreConfig] = None) -> None:
        self._config = config or VectorStoreConfig.from_env()
        self._collection = None
        self._client = None

    @property
    def config(self) -> VectorStoreConfig:
        return self._config

    # ------------------- public API ------------------- #

    def upsert(
        self,
        texts: List[str],
        metadatas: Optional[List[Dict[str, Any]]] = None,
        ids: Optional[List[str]] = None,
    ) -> List[str]:
        if not texts:
            return []
        if metadatas is not None and len(metadatas) != len(texts):
            raise ValueError("metadatas length must match texts length")
        if ids is None:
            ids = [f"mem-{uuid.uuid4().hex[:12]}" for _ in texts]
        elif len(ids) != len(texts):
            raise ValueError("ids length must match texts length")

        try:
            collection = self._get_collection()
            collection.upsert(
                documents=list(texts),
                metadatas=list(metadatas) if metadatas else None,
                ids=ids,
            )
        except Exception as exc:  # noqa: BLE001 -- normalise into our exception
            raise TinyDevinMemoryError(
                "vector store upsert failed",
                details={"error": str(exc), "count": len(texts)},
            ) from exc
        return list(ids)

    def query(self, text: str, k: int = 5) -> List[VectorHit]:
        if not text:
            return []
        try:
            collection = self._get_collection()
            result = collection.query(query_texts=[text], n_results=k)
        except Exception as exc:  # noqa: BLE001
            raise TinyDevinMemoryError(
                "vector store query failed",
                details={"error": str(exc), "k": k},
            ) from exc

        hits: List[VectorHit] = []
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        ids = (result.get("ids") or [[]])[0]
        dists = (result.get("distances") or [[None] * len(docs)])[0]

        for doc, meta, hid, dist in zip(docs, metas, ids, dists):
            hits.append(
                VectorHit(
                    id=str(hid),
                    text=str(doc),
                    metadata=dict(meta) if meta else {},
                    distance=float(dist) if dist is not None else None,
                )
            )
        return hits

    def delete(self, ids: List[str]) -> None:
        if not ids:
            return
        try:
            collection = self._get_collection()
            collection.delete(ids=list(ids))
        except Exception as exc:  # noqa: BLE001
            raise TinyDevinMemoryError(
                "vector store delete failed",
                details={"error": str(exc), "count": len(ids)},
            ) from exc

    def count(self) -> int:
        try:
            collection = self._get_collection()
            return int(collection.count())
        except Exception as exc:  # noqa: BLE001
            raise TinyDevinMemoryError("vector store count failed") from exc

    def close(self) -> None:
        # ChromaDB persistent client has no explicit close; drop refs so GC
        # can release file handles.
        self._collection = None
        self._client = None

    # ------------------- internals ------------------- #

    def _get_collection(self):
        if self._collection is not None:
            return self._collection

        try:
            import chromadb  # type: ignore
        except ImportError as exc:  # pragma: no cover - dependency error
            raise TinyDevinMemoryError(
                "chromadb is not installed; install it via requirements.txt",
            ) from exc

        os.makedirs(self._config.persist_dir, exist_ok=True)

        try:
            self._client = chromadb.PersistentClient(path=self._config.persist_dir)
            self._collection = self._client.get_or_create_collection(
                name=self._config.collection
            )
        except Exception as exc:  # noqa: BLE001
            raise TinyDevinMemoryError(
                "failed to open chromadb persistent client",
                details={"error": str(exc), "persist_dir": self._config.persist_dir},
            ) from exc

        return self._collection


__all__ = ["VectorStore", "VectorStoreConfig", "VectorHit"]
