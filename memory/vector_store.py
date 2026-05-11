"""基于 ChromaDB 的长期经验记忆。

包装器把 ChromaDB 的重量级 import 藏在 *懒加载* 调用后面：客户端只在第一次
使用时才被构造，因此即使一整个 session 从不发起向量查询，import 本模块的
代价也接近零。

存储是 **任务模式无关** 的：collection 里可以放任何 Agent 觉得值得记住的东西
—— 代码片段、失败的单元测试、哲学反例、引文。
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
# 配置与数据类
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
    """ChromaDB 的薄包装，提供幂等 upsert 与相似度检索。

    重量级 import（chromadb + 嵌入模型）被延迟到第一次调用时执行，所以创建
    :class:`VectorStore` 本身是廉价的。
    """

    def __init__(self, config: Optional[VectorStoreConfig] = None) -> None:
        self._config = config or VectorStoreConfig.from_env()
        self._collection = None
        self._client = None

    @property
    def config(self) -> VectorStoreConfig:
        return self._config

    # ------------------- 公共 API ------------------- #

    def upsert(
        self,
        texts: List[str],
        metadatas: Optional[List[Dict[str, Any]]] = None,
        ids: Optional[List[str]] = None,
    ) -> List[str]:
        if not texts:
            return []
        if metadatas is not None and len(metadatas) != len(texts):
            raise ValueError("metadatas 长度必须与 texts 一致")
        if ids is None:
            ids = [f"mem-{uuid.uuid4().hex[:12]}" for _ in texts]
        elif len(ids) != len(texts):
            raise ValueError("ids 长度必须与 texts 一致")

        try:
            collection = self._get_collection()
            collection.upsert(
                documents=list(texts),
                metadatas=list(metadatas) if metadatas else None,
                ids=ids,
            )
        except Exception as exc:  # noqa: BLE001 -- 规范化成项目自己的异常
            raise TinyDevinMemoryError(
                "向量库 upsert 失败",
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
                "向量库查询失败",
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
                "向量库 delete 失败",
                details={"error": str(exc), "count": len(ids)},
            ) from exc

    def count(self) -> int:
        try:
            collection = self._get_collection()
            return int(collection.count())
        except Exception as exc:  # noqa: BLE001
            raise TinyDevinMemoryError("向量库 count 失败") from exc

    def close(self) -> None:
        # ChromaDB 的 PersistentClient 没有显式 close；把引用释放掉让 GC
        # 自己回收文件句柄。
        self._collection = None
        self._client = None

    # ------------------- 内部实现 ------------------- #

    def _get_collection(self):
        if self._collection is not None:
            return self._collection

        try:
            import chromadb  # type: ignore
        except ImportError as exc:  # pragma: no cover - 依赖错误
            raise TinyDevinMemoryError(
                "chromadb 未安装；请通过 requirements.txt 安装",
            ) from exc

        os.makedirs(self._config.persist_dir, exist_ok=True)

        try:
            self._client = chromadb.PersistentClient(path=self._config.persist_dir)
            self._collection = self._client.get_or_create_collection(
                name=self._config.collection
            )
        except Exception as exc:  # noqa: BLE001
            raise TinyDevinMemoryError(
                "无法打开 chromadb PersistentClient",
                details={"error": str(exc), "persist_dir": self._config.persist_dir},
            ) from exc

        return self._collection


__all__ = ["VectorStore", "VectorStoreConfig", "VectorHit"]
