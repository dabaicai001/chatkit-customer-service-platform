"""tools/knowledge.py:知识库检索(RAG,平台内置能力)。"""

from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, Field

from . import Tool, ToolContext


class QueryKnowledgeParams(BaseModel):
    question: str = Field(min_length=1, max_length=500, description="要检索的问题")


async def _query_knowledge(ctx: ToolContext) -> Dict[str, Any]:
    passages = await ctx.rag.search(ctx.params.question)
    if not passages:
        return {
            "result": "知识库中未找到与该问题相关的内容。",
            "data": {"passages": []},
            "found": False,
        }
    lines = [f"【{p['title']}】{p['content']}" for p in passages]
    return {
        "result": "\n".join(lines),
        "data": {"passages": passages},
        "found": True,
    }


def query_knowledge_tool() -> Tool:
    return Tool(
        name="query_knowledge",
        description="检索公司知识库(退款政策/物流/发票/保修等通用问题)",
        parameters=QueryKnowledgeParams,
        handler=_query_knowledge,
    )
