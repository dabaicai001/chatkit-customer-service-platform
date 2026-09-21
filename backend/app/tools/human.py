"""tools/human.py:转人工(平台能力,排队信息由配置驱动)。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel

from . import Tool, ToolContext


class TransferHumanParams(BaseModel):
    reason: Optional[str] = None
    intent: Optional[str] = None


async def _transfer_to_human(ctx: ToolContext) -> Dict[str, Any]:
    rule = ctx.config.rule("human_transfer")
    if not rule.get("enabled", True):
        return {
            "result": "当前人工坐席未开放,已由 AI 客服继续为您服务。",
            "data": {"transferred": False},
        }
    queue_position = int(rule.get("queue_position", 1) or 1)
    estimated_wait = str(rule.get("queue_estimate", "约 2 分钟"))
    reason = ctx.params.reason or "用户请求"
    ctx.sessions.log(ctx.thread_id, f"用户请求转人工({reason})", kind="warning")
    return {
        "result": (
            f"已为您接入人工客服,当前排队第 {queue_position} 位,预计等待 {estimated_wait}。"
            "请稍候,人工客服马上为您服务。"
        ),
        "data": {
            "transferred": True,
            "queue_position": queue_position,
            "estimated_wait": estimated_wait,
            "reason": reason,
        },
    }


def transfer_to_human_tool() -> Tool:
    return Tool(
        name="transfer_to_human",
        description="转接人工客服(排队等待)",
        parameters=TransferHumanParams,
        handler=_transfer_to_human,
    )
