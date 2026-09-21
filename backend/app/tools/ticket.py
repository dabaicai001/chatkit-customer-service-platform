"""tools/ticket.py:工单创建(MCP)。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from . import Tool, ToolContext


class CreateTicketParams(BaseModel):
    subject: str = Field(min_length=1, max_length=200, description="工单主题")
    description: Optional[str] = Field(default=None, max_length=2000)
    priority: Optional[str] = Field(default=None, pattern="^(低|中|高|紧急)$")


async def _create_ticket(ctx: ToolContext) -> Dict[str, Any]:
    arguments: Dict[str, Any] = {"subject": ctx.params.subject}
    if ctx.params.description:
        arguments["description"] = ctx.params.description
    if ctx.params.priority:
        arguments["priority"] = ctx.params.priority
    if ctx.customer_id:
        arguments["customer_id"] = ctx.customer_id

    mapping = ctx.mcp.require_mapping("create_ticket")
    payload = await ctx.mcp.call_tool(mapping, arguments)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    ticket_id = ""
    if isinstance(data, dict):
        ticket_id = str(data.get("ticket_id") or data.get("id") or "")
    message = payload.get("result") or payload.get("message") or (
        f"工单已创建(单号 {ticket_id}),客服会尽快跟进。" if ticket_id else "工单已创建。"
    )
    return {
        "result": str(message),
        "data": data if isinstance(data, dict) else {},
        "state_changed": bool(payload.get("state_changed", True)),
    }


def create_ticket_tool() -> Tool:
    return Tool(
        name="create_ticket",
        description="创建投诉/报修/咨询工单",
        parameters=CreateTicketParams,
        handler=_create_ticket,
        mutating=True,
    )
