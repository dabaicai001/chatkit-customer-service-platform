"""tools:客服工具层(插件式)。

每个工具 = 强校验的入参(pydantic) + 明确的输出契约。
业务数据类工具(search_customer / get_order / get_product / create_ticket /
refund_order / cancel_order)全部经 MCP 请求上游系统;
平台能力类工具(query_knowledge / transfer_to_human)由平台自身提供。

换行业(电商/游戏/SaaS/物流/金融)= 换 business.yaml 的 mcp.tool_mapping
指向新上游系统的同名工具,工具层代码不动。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Type

from pydantic import BaseModel, ValidationError

from ..ai.rag import RagService
from ..config import BusinessConfig
from ..core.customer import CustomerGateway
from ..core.session import SessionStateManager
from ..integrations.mcp import McpClientManager

logger = logging.getLogger(__name__)


class ToolError(RuntimeError):
    """工具执行失败(参数非法 / 上游不可用 / 业务拒绝)。"""


@dataclass
class ToolContext:
    """工具执行上下文。"""

    thread_id: str
    message: str
    params: BaseModel
    raw_params: Dict[str, Any]
    sessions: SessionStateManager
    gateway: CustomerGateway
    rag: RagService
    mcp: McpClientManager
    config: BusinessConfig

    @property
    def customer_id(self) -> Optional[str]:
        return self.sessions.customer_id(self.thread_id)


@dataclass
class Tool:
    """一个客服工具的定义。"""

    name: str
    description: str
    parameters: Type[BaseModel]
    handler: Callable[[ToolContext], Awaitable[Dict[str, Any]]]
    mutating: bool = False  # 是否改变业务状态(用于触发画像刷新)


class ToolRegistry:
    """工具注册表:注册、启用过滤、参数校验、统一执行与流水记录。"""

    def __init__(
        self,
        *,
        config: BusinessConfig,
        mcp: McpClientManager,
        gateway: CustomerGateway,
        rag: RagService,
        sessions: SessionStateManager,
    ) -> None:
        self._config = config
        self._mcp = mcp
        self._gateway = gateway
        self._rag = rag
        self._sessions = sessions
        self._tools: Dict[str, Tool] = {}

    # ------------------------------------------------------------- 注册
    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ToolError(f"工具重复注册:{tool.name}")
        self._tools[tool.name] = tool

    def is_enabled(self, name: str) -> bool:
        return name in self._tools and name in self._config.enabled_tools

    def enabled_names(self) -> List[str]:
        return [name for name in self._config.enabled_tools if name in self._tools]

    def get(self, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"工具未注册:{name}")
        return tool

    def schemas(self) -> List[Dict[str, Any]]:
        """启用工具的 JSON Schema(供调试/文档/提示词使用)。"""

        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters.model_json_schema(),
            }
            for tool in (self._tools[name] for name in self.enabled_names())
        ]

    # ------------------------------------------------------------- 执行
    async def execute(
        self,
        name: str,
        *,
        thread_id: str,
        params: Optional[Mapping[str, Any]] = None,
        message: str = "",
    ) -> Dict[str, Any]:
        tool = self.get(name)
        raw_params = dict(params or {})
        try:
            validated = tool.parameters.model_validate(raw_params)
        except ValidationError as exc:
            raise ToolError(f"工具 {name} 参数校验失败:{_format_validation_error(exc)}") from exc

        context = ToolContext(
            thread_id=thread_id,
            message=message,
            params=validated,
            raw_params=raw_params,
            sessions=self._sessions,
            gateway=self._gateway,
            rag=self._rag,
            mcp=self._mcp,
            config=self._config,
        )
        try:
            result = await tool.handler(context)
        except ToolError:
            raise
        except Exception as exc:
            logger.exception("工具 %s 执行异常", name)
            raise ToolError(f"工具 {name} 执行失败:{exc}") from exc

        if not isinstance(result, dict):
            raise ToolError(f"工具 {name} 返回值必须是 dict,实际为 {type(result).__name__}")
        result.setdefault("result", "")
        result.setdefault("data", {})
        result.setdefault("state_changed", tool.mutating)
        self._sessions.log(thread_id, f"调用工具 {name}:{result.get('result', '')[:80]}", kind="info")
        return result


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        location = ".".join(str(loc) for loc in error.get("loc", ()))
        parts.append(f"{location or '参数'}:{error.get('msg', '非法')}")
    return ";".join(parts)


# ---------------------------------------------------------------------------
# 默认注册
# ---------------------------------------------------------------------------
def build_default_registry(
    *,
    config: BusinessConfig,
    mcp: McpClientManager,
    gateway: CustomerGateway,
    rag: RagService,
    sessions: SessionStateManager,
) -> ToolRegistry:
    """注册全部内置工具(按 business.yaml 的 tools 列表启用)。"""

    from . import customer as customer_tools
    from . import human as human_tools
    from . import knowledge as knowledge_tools
    from . import order as order_tools
    from . import product as product_tools
    from . import ticket as ticket_tools

    registry = ToolRegistry(
        config=config, mcp=mcp, gateway=gateway, rag=rag, sessions=sessions
    )
    for tool in (
        customer_tools.search_customer_tool(),
        order_tools.get_order_tool(),
        order_tools.refund_order_tool(),
        order_tools.cancel_order_tool(),
        product_tools.get_product_tool(),
        ticket_tools.create_ticket_tool(),
        knowledge_tools.query_knowledge_tool(),
        human_tools.transfer_to_human_tool(),
    ):
        registry.register(tool)

    missing = [name for name in config.enabled_tools if name not in registry.enabled_names()]
    if missing:
        raise ToolError(f"business.yaml 启用了未注册的工具:{', '.join(missing)}")
    return registry
