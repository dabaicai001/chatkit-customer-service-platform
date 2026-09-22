"""knowledge:FAQ 语料加载(公司知识库的"书")。

语料内容全部来自 business.yaml 的 ``knowledge.documents``——换公司/换行业
只改配置,代码零样例数据。条目结构::

    knowledge:
      documents:
        - doc_id: 唯一ID
          title: 标题
          content: 正文
          tags: [检索标签, ...]

未配置时返回空列表(知识库检索无结果,由话术模型按"未查到"处理)。
生产环境可换成从 CMS/Confluence/数据库导入,同样最终变成 Document 列表。
"""

from __future__ import annotations

from typing import List

from ..config import BusinessConfig, ConfigurationError
from .vector_store import Document


def load_faq_documents(config: BusinessConfig) -> List[Document]:
    """从 business.yaml 的 knowledge.documents 加载 FAQ 语料。"""

    raw = config.get("knowledge.documents") or []
    if not isinstance(raw, list):
        raise ConfigurationError("business.yaml 的 knowledge.documents 应为列表。")
    documents: List[Document] = []
    seen: set = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ConfigurationError(f"knowledge.documents[{index}] 应为对象。")
        doc_id = str(item.get("doc_id", "")).strip()
        title = str(item.get("title", "")).strip()
        content = str(item.get("content", "")).strip()
        if not doc_id or not title or not content:
            raise ConfigurationError(
                f"knowledge.documents[{index}] 缺少 doc_id / title / content(必填)。"
            )
        if doc_id in seen:
            raise ConfigurationError(f"knowledge.documents 的 doc_id 重复:{doc_id}")
        seen.add(doc_id)
        tags = item.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        documents.append(
            Document(
                doc_id=doc_id,
                title=title,
                content=content,
                tags=[str(tag) for tag in tags],
            )
        )
    return documents
