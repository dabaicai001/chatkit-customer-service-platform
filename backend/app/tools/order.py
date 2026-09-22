"""tools/order.py:订单查询 / 退款 / 取消(MCP)。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel

from ..core.customer import _as_records, normalize_order
from . import Tool, ToolContext, ToolError


class GetOrderParams(BaseModel):
    order_id: Optional[str] = None


class RefundOrderParams(BaseModel):
    order_id: str
    reason: Optional[str] = None


class CancelOrderParams(BaseModel):
    order_id: str
    reason: Optional[str] = None


async def _get_order(ctx: ToolContext) -> Dict[str, Any]:
    arguments: Dict[str, Any] = {}
    if ctx.params.order_id:
        arguments["order_id"] = ctx.params.order_id
        # 绑定用户后同时带 customer_id:上游校验订单归属,只能查该用户的订单
        if ctx.customer_id:
            arguments["customer_id"] = ctx.customer_id
    elif ctx.customer_id:
        arguments["customer_id"] = ctx.customer_id
    else:
        raise ToolError(
            "缺少查询条件:请引导客户提供订单号,或提供手机号/客户ID 核实身份"
            "(已绑定身份时可直接查询该客户的订单)。"
        )

    mapping = ctx.mcp.require_mapping("get_order")
    payload = await ctx.mcp.call_tool(mapping, arguments)
    orders = [normalize_order(record) for record in _as_records(payload, "orders", "order", "results", "items")]
    if not orders:
        return {"result": "未查询到对应订单。", "data": {"orders": []}}

    if ctx.params.order_id:
        selected = next((o for o in orders if o.id == ctx.params.order_id), orders[0])
    else:
        selected = orders[0]

    summary = (
        f"订单 {selected.id}({selected.title})当前状态:{selected.status}"
        + (f",{selected.tracking}" if selected.tracking else "")
        + (f",金额 ¥{selected.amount:.2f}" if selected.amount else "")
    )
    return {
        "result": summary + "。",
        "data": {"order": selected.to_dict(), "orders": [o.to_dict() for o in orders]},
    }


async def _refund_order(ctx: ToolContext) -> Dict[str, Any]:
    arguments: Dict[str, Any] = {"order_id": ctx.params.order_id}
    if ctx.params.reason:
        arguments["reason"] = ctx.params.reason
    if ctx.customer_id:
        arguments["customer_id"] = ctx.customer_id
    mapping = ctx.mcp.require_mapping("refund_order")
    payload = await ctx.mcp.call_tool(mapping, arguments)
    return _mutation_result(ctx, payload, f"订单 {ctx.params.order_id} 退款申请已提交。")


async def _cancel_order(ctx: ToolContext) -> Dict[str, Any]:
    arguments: Dict[str, Any] = {"order_id": ctx.params.order_id}
    if ctx.params.reason:
        arguments["reason"] = ctx.params.reason
    if ctx.customer_id:
        arguments["customer_id"] = ctx.customer_id
    mapping = ctx.mcp.require_mapping("cancel_order")
    payload = await ctx.mcp.call_tool(mapping, arguments)
    return _mutation_result(ctx, payload, f"订单 {ctx.params.order_id} 取消申请已提交。")


def _mutation_result(ctx: ToolContext, payload: Dict[str, Any], fallback: str) -> Dict[str, Any]:
    """变更类工具:提取上游确认信息,默认视为状态已变更(触发画像刷新)。"""

    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    message = payload.get("result") or payload.get("message") or fallback
    state_changed = payload.get("state_changed", True)
    return {"result": str(message), "data": data if isinstance(data, dict) else {}, "state_changed": bool(state_changed)}


def get_order_tool() -> Tool:
    return Tool(
        name="get_order",
        description="查询订单状态/物流进度(需客户身份或订单号)",
        parameters=GetOrderParams,
        handler=_get_order,
    )


def refund_order_tool() -> Tool:
    return Tool(
        name="refund_order",
        description="为指定订单申请退款(需用户确认后执行)",
        parameters=RefundOrderParams,
        handler=_refund_order,
        mutating=True,
    )


def cancel_order_tool() -> Tool:
    return Tool(
        name="cancel_order",
        description="取消指定订单(需用户确认后执行)",
        parameters=CancelOrderParams,
        handler=_cancel_order,
        mutating=True,
    )
