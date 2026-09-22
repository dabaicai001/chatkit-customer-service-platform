"""core/routing.py:AGENT 调度与固化直回解析(纯函数,不发请求)。

Jev 决策之后,「执行」全部在 server.py 的智能体循环里完成
(模型经 function calling 直连 MCP,见 ai/mcp_agent.py);
这里只保留两个与请求无关的纯解析:

- ``resolve_agent``:AGENT 调度优先级(Jev 指定 > 意图映射 > 默认);
- ``resolve_direct_reply``:垃圾/无关意图的固化文案(不过生成模型)。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..ai.agents import AgentRegistry, AgentSpec
from ..ai.jev import Decision


def resolve_agent(
    decision: Decision,
    intent_catalog: Dict[str, Any],
    registry: AgentRegistry,
) -> AgentSpec:
    """AGENT 调度决策:Jev 指定 > 意图映射 > 默认 AGENT。"""

    if decision.agent:
        spec = registry.get(decision.agent)
        if spec is not None:
            return spec
    intent_spec = intent_catalog.get(decision.intent)
    if isinstance(intent_spec, dict):
        mapped = str(intent_spec.get("agent", "") or "").strip()
        if mapped:
            spec = registry.get(mapped)
            if spec is not None:
                return spec
    return registry.resolve(registry.default_name)


def resolve_direct_reply(
    decision: Decision,
    intent_catalog: Dict[str, Any],
    config: Optional[Any] = None,
) -> str:
    """意图配置了 direct_reply 时返回固化文案(垃圾/无关信息直通,不过生成模型)。"""

    spec = intent_catalog.get(decision.intent)
    if not isinstance(spec, dict):
        return ""
    template = str(spec.get("direct_reply", "") or "").strip()
    if not template:
        return ""
    if config is not None:
        try:
            return template.format(
                agent_name=config.agent_name,
                company_name=config.company_name,
            )
        except (KeyError, IndexError):
            return template
    return template
