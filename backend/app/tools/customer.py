"""tools/customer.py:客户检索(MCP)。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, model_validator

from ..core.customer import _as_records, normalize_customer
from . import Tool, ToolContext


class SearchCustomerParams(BaseModel):
    """检索客户:keyword / customer_id / phone 至少提供一个。"""

    keyword: Optional[str] = None
    customer_id: Optional[str] = None
    phone: Optional[str] = None

    @model_validator(mode="after")
    def _at_least_one(self) -> "SearchCustomerParams":
        if not any([self.keyword, self.customer_id, self.phone]):
            raise ValueError("检索客户需要提供 keyword、customer_id 或 phone 之一")
        return self


async def _search_customer(ctx: ToolContext) -> Dict[str, Any]:
    mapping = ctx.mcp.require_mapping("search_customer")
    payload = await ctx.mcp.call_tool(
        mapping, ctx.params.model_dump(exclude_none=True)
    )
    records = _as_records(payload, "customers", "customer", "results", "list", "items")
    if not records:
        return {"result": "未找到匹配的客户,请核对手机号或客户 ID。", "data": {}, "found": False}
    profile = normalize_customer(records[0])
    # 识别成功 → 绑定会话身份,后续订单/工单查询才有上下文
    ctx.sessions.bind_customer(ctx.thread_id, profile.customer_id)
    return {
        "result": f"已找到客户 {profile.name}({profile.customer_id},{profile.level})。",
        "data": profile.to_dict(),
        "found": True,
    }


def search_customer_tool() -> Tool:
    return Tool(
        name="search_customer",
        description="按关键字(姓名/手机号/客户ID)检索客户,确认客户身份",
        parameters=SearchCustomerParams,
        handler=_search_customer,
    )
