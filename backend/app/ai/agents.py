"""ai/agents.py:专职 AGENT 注册表(配置驱动)。

AI CHAT 出口内部按职责细分多个专职 AGENT(订单客服/商品顾问/知识库客服/通用客服…),
每个 AGENT 的完整定义来自 business.yaml 的 ``agents`` 段:

    agents:
      default: chat_agent            # Jev 无法判断时的兜底
      order_agent:
        title: 订单客服
        description: 订单状态/物流进度查询      # 进 Jev 提示词,供其调度
        instructions: |               # 进生成模型系统提示词(人设/话术约束)
          你是订单客服……
        tools: [get_order]            # 该 AGENT 可用的工具(信息性,供 Jev 参考)
        needs_rag: false              # 是否检索公司知识库

Jev 只负责“调度哪个 AGENT”;新增/调整 AGENT 只改 YAML,代码不动。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..config import BusinessConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentSpec:
    """一个专职 AGENT 的完整定义。"""

    name: str
    title: str
    description: str
    instructions: str
    tools: Tuple[str, ...] = ()
    needs_rag: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "tools": list(self.tools),
            "needs_rag": self.needs_rag,
        }


class AgentRegistry:
    """从 business.yaml 的 agents 段加载 AGENT 目录。"""

    def __init__(self, config: BusinessConfig) -> None:
        section = config.section("agents")
        self._default = str(section.get("default", "") or "").strip()
        self._agents: Dict[str, AgentSpec] = {}
        for name, raw in section.items():
            if name == "default" or not isinstance(raw, dict):
                continue
            agent_name = str(name).strip()
            if not agent_name:
                continue
            tools_raw = raw.get("tools") or []
            tools = tuple(str(t) for t in tools_raw) if isinstance(tools_raw, list) else ()
            self._agents[agent_name] = AgentSpec(
                name=agent_name,
                title=str(raw.get("title", agent_name) or agent_name),
                description=str(raw.get("description", "") or ""),
                instructions=str(raw.get("instructions", "") or ""),
                tools=tools,
                needs_rag=bool(raw.get("needs_rag", False)),
            )
        if not self._agents:
            raise ValueError("business.yaml 的 agents 段为空:至少定义一个 AGENT。")
        if not self._default:
            self._default = next(iter(self._agents))
        elif self._default not in self._agents:
            raise ValueError(
                f"agents.default 指向了不存在的 AGENT:{self._default}"
                f"(已定义:{', '.join(self._agents)})"
            )

    # ------------------------------------------------------------- 查询
    @property
    def default_name(self) -> str:
        return self._default

    def names(self) -> List[str]:
        return list(self._agents)

    def get(self, name: str) -> Optional[AgentSpec]:
        return self._agents.get(str(name or "").strip())

    def resolve(self, name: str) -> AgentSpec:
        """按名字取 AGENT;不存在时回退默认 AGENT。"""

        spec = self.get(name)
        return spec if spec is not None else self._agents[self._default]

    # ------------------------------------------------------------- 提示词
    def catalog_text(self) -> str:
        """渲染给 Jev 的 AGENT 目录(含职责与可用工具)。"""

        lines = []
        for spec in self._agents.values():
            tools = "/".join(spec.tools) if spec.tools else "无(仅对话)"
            rag = "、需查知识库" if spec.needs_rag else ""
            lines.append(
                f"- {spec.name}({spec.title}):{spec.description}(可用工具:{tools}{rag})"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------- 校验
    def validate(self, enabled_tools: List[str]) -> None:
        """AGENT 声明的工具必须在启用工具列表内。"""

        problems: List[str] = []
        for spec in self._agents.values():
            unknown = [tool for tool in spec.tools if tool not in enabled_tools]
            if unknown:
                problems.append(
                    f"AGENT [{spec.name}] 声明了未启用的工具:{', '.join(unknown)}"
                )
        if problems:
            raise ValueError("AGENT 配置不合法:\n- " + "\n- ".join(problems))
