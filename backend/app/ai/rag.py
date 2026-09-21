"""ai:RAG 检索服务(公司知识库的统一入口)。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..config import BusinessConfig
from ..knowledge import FAQ_DOCUMENTS, ChromaVectorStore, Document, MemoryVectorStore

logger = logging.getLogger(__name__)


class RagService:
    """按配置选择向量库(memory / chroma),索引 FAQ 语料并提供检索。"""

    def __init__(self, config: BusinessConfig, documents: List[Document] | None = None) -> None:
        settings = config.knowledge
        self.enabled: bool = bool(settings.get("enabled", True))
        self.top_k: int = int(settings.get("top_k", 3))
        self.score_threshold: float = float(settings.get("score_threshold", 0.05))
        store_kind = str(settings.get("vector_store", "memory"))
        self._store: MemoryVectorStore | ChromaVectorStore
        if store_kind == "chroma":
            try:
                self._store = ChromaVectorStore()
            except Exception as exc:
                logger.warning("chroma 不可用(%s),回退 memory 向量库。", exc)
                self._store = MemoryVectorStore()
        else:
            self._store = MemoryVectorStore()
        self.rebuild(documents or FAQ_DOCUMENTS)

    def rebuild(self, documents: List[Document]) -> None:
        self._store.clear()
        self._store.add(documents)
        logger.info("知识库索引完成:%d 篇文档(%s)。", len(self._store), type(self._store).__name__)

    async def search(self, query: str, top_k: int | None = None) -> List[Dict[str, Any]]:
        if not self.enabled or not query:
            return []
        passages = self._store.search(query, top_k or self.top_k)
        return [p for p in passages if p.get("score", 0) >= self.score_threshold]

    async def add_documents(self, documents: List[Document]) -> None:
        self._store.add(documents)
