"""tools/product.py:商品/产品咨询(MCP)。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, model_validator

from ..core.customer import _as_records
from . import Tool, ToolContext


class GetProductParams(BaseModel):
    product_id: Optional[str] = None
    keyword: Optional[str] = None

    @model_validator(mode="after")
    def _at_least_one(self) -> "GetProductParams":
        if not any([self.product_id, self.keyword]):
            raise ValueError("查询商品需要提供 product_id 或 keyword")
        return self


async def _get_product(ctx: ToolContext) -> Dict[str, Any]:
    arguments = ctx.params.model_dump(exclude_none=True)
    mapping = ctx.mcp.require_mapping("get_product")
    payload = await ctx.mcp.call_tool(mapping, arguments)

    records = _as_records(payload, "products", "product", "results", "items")
    if not records:
        return {"result": "未找到相关商品信息。", "data": {"products": []}}

    lines = []
    for record in records[:5]:
        name = record.get("name") or record.get("title") or record.get("product_id", "")
        price = record.get("price") or record.get("amount")
        stock = record.get("stock") or record.get("inventory")
        parts = [str(name)]
        if price not in (None, ""):
            parts.append(f"价格 ¥{price}")
        if stock not in (None, ""):
            parts.append(f"库存 {stock}")
        lines.append(":".join([parts[0], ", ".join(parts[1:])]) if len(parts) > 1 else parts[0])

    return {
        "result": "；".join(lines),
        "data": {"products": [dict(r) for r in records[:5]]},
    }


def get_product_tool() -> Tool:
    return Tool(
        name="get_product",
        description="查询商品/产品信息(价格、规格、库存)",
        parameters=GetProductParams,
        handler=_get_product,
    )
