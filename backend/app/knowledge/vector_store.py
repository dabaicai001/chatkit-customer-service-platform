"""knowledge:公司知识库(RAG 的检索底座)。

memory 向量检索为纯 Python 实现(中文按字符二元组切分,TF-IDF + 余弦相似度),
零外部依赖;business.yaml 里 ``knowledge.vector_store: chroma`` 时预留切换点。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List

_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[a-zA-Z0-9]+")


def tokenize(text: str) -> List[str]:
    """中文友好的轻量分词:CJK 字符二元组 + 英文/数字词。"""

    text = (text or "").lower()
    tokens: List[str] = _WORD.findall(text)
    cjk_chars = _CJK.findall(text)
    tokens.extend(cjk_chars)
    tokens.extend(
        "".join(pair) for pair in zip(cjk_chars, cjk_chars[1:])
    )
    return tokens


@dataclass
class Document:
    doc_id: str
    title: str
    content: str
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_passage(self, score: float) -> Dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "content": self.content,
            "score": round(score, 4),
            "tags": self.tags,
        }


class MemoryVectorStore:
    """进程内 TF-IDF 向量检索(演示足够,接口与 chroma 对齐)。"""

    def __init__(self) -> None:
        self._docs: List[Document] = []
        self._df: Counter[str] = Counter()
        self._tfidf: List[Dict[str, float]] = []

    def add(self, docs: Iterable[Document]) -> None:
        docs = list(docs)
        if not docs:
            return
        for doc in docs:
            self._docs.append(doc)
        self._rebuild()

    def clear(self) -> None:
        self._docs.clear()
        self._df.clear()
        self._tfidf.clear()

    def __len__(self) -> int:
        return len(self._docs)

    def _rebuild(self) -> None:
        self._df = Counter()
        for doc in self._docs:
            for token in set(self._token_set(doc)):
                self._df[token] += 1
        self._tfidf = [self._vectorize(self._token_counts(doc)) for doc in self._docs]

    @staticmethod
    def _token_set(doc: Document) -> set[str]:
        return set(tokenize(f"{doc.title} {doc.content} {' '.join(doc.tags)}"))

    @staticmethod
    def _token_counts(doc: Document) -> Counter[str]:
        return Counter(tokenize(f"{doc.title} {doc.content} {' '.join(doc.tags)}"))

    def _vectorize(self, counts: Counter[str]) -> Dict[str, float]:
        total = sum(counts.values()) or 1
        vector: Dict[str, float] = {}
        for token, count in counts.items():
            idf = math.log((1 + len(self._docs)) / (1 + self._df.get(token, 0))) + 1.0
            vector[token] = (count / total) * idf
        return vector

    def search(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        if not self._docs:
            return []
        query_vector = self._vectorize(Counter(tokenize(query)))
        query_norm = math.sqrt(sum(v * v for v in query_vector.values())) or 1.0
        scored: List[tuple[float, Document]] = []
        for doc, vector in zip(self._docs, self._tfidf):
            dot = sum(weight * vector.get(token, 0.0) for token, weight in query_vector.items())
            doc_norm = math.sqrt(sum(v * v for v in vector.values())) or 1.0
            score = dot / (query_norm * doc_norm)
            if score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [doc.to_passage(score) for score, doc in scored[:top_k]]


class ChromaVectorStore:
    """预留:chroma 向量库(未安装时由 RagService 自动回退 memory)。"""

    def __init__(self) -> None:  # pragma: no cover - 需要可选依赖
        try:
            import chromadb  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("chroma 未安装,请在 business.yaml 中使用 memory 向量库") from exc
        self._client = chromadb.Client()
        self._collection = self._client.get_or_create_collection("customer_service_kb")

    def add(self, docs: Iterable[Document]) -> None:  # pragma: no cover
        docs = list(docs)
        self._collection.add(
            ids=[doc.doc_id for doc in docs],
            documents=[f"{doc.title}\n{doc.content}" for doc in docs],
            metadatas=[{"title": doc.title, "tags": ",".join(doc.tags)} for doc in docs],
        )

    def search(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:  # pragma: no cover
        result = self._collection.query(query_texts=[query], n_results=top_k)
        passages: List[Dict[str, Any]] = []
        for idx, doc_id in enumerate(result.get("ids", [[]])[0]):
            passages.append(
                {
                    "doc_id": doc_id,
                    "title": result["metadatas"][0][idx].get("title", ""),
                    "content": result["documents"][0][idx],
                    "score": 1.0 - (result.get("distances", [[0]])[0][idx] or 0.0),
                    "tags": [],
                }
            )
        return passages
