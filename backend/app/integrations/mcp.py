"""integrations/mcp.py:内嵌 MCP 客户端——业务数据的统一入口。

平台不直连业务数据库:客户、订单、商品、工单等数据全部通过 MCP 协议
请求上游系统(CRM / OMS / 商品中心 / 工单系统…)。

支持两种传输(在 business.yaml 的 ``mcp.servers`` 中配置):
- ``http`` :MCP Streamable HTTP(推荐,上游以 HTTP 服务暴露 MCP);
- ``stdio``:以子进程方式拉起上游 MCP Server。

特性:懒连接 / 断线重连 / 调用超时 / 结构化结果归一化 / 启动时连通性校验。
任何配置缺失或连接失败都会显式抛错,不做静默降级。
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client

from ..config import BusinessConfig, ConfigurationError

logger = logging.getLogger(__name__)


class McpError(RuntimeError):
    """MCP 调用失败(配置错误 / 连接失败 / 上游工具报错)。"""


@dataclass(frozen=True)
class McpServerConfig:
    """单个 MCP Server 的连接配置。"""

    name: str
    transport: str  # "http" | "stdio"
    url: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    command: str = ""
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 30.0

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "McpServerConfig":
        name = str(raw.get("name", "")).strip()
        if not name:
            raise ConfigurationError("mcp.servers 中存在未命名的 server 配置。")
        transport = str(raw.get("transport", "http")).strip().lower()
        if transport not in ("http", "stdio"):
            raise ConfigurationError(
                f"MCP server [{name}] 的 transport 必须是 http 或 stdio,当前为 {transport!r}。"
            )
        if transport == "http" and not str(raw.get("url", "")).strip():
            raise ConfigurationError(f"MCP server [{name}](http)缺少 url 配置。")
        if transport == "stdio" and not str(raw.get("command", "")).strip():
            raise ConfigurationError(f"MCP server [{name}](stdio)缺少 command 配置。")
        headers = raw.get("headers") or {}
        env = raw.get("env") or {}
        return cls(
            name=name,
            transport=transport,
            url=str(raw.get("url", "")).strip(),
            headers={str(k): str(v) for k, v in headers.items()} if isinstance(headers, dict) else {},
            command=str(raw.get("command", "")).strip(),
            args=[str(a) for a in (raw.get("args") or [])],
            env={str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {},
            timeout_seconds=float(raw.get("timeout_seconds", 30) or 30),
        )


class McpClientManager:
    """按配置管理多个 MCP Server 的连接与调用。"""

    def __init__(self, config: BusinessConfig) -> None:
        self._config = config
        section = config.section("mcp")
        servers_raw = section.get("servers") or []
        if not isinstance(servers_raw, list) or not servers_raw:
            raise ConfigurationError("business.yaml 的 mcp.servers 为空:数据面必须至少配置一个 MCP Server。")
        self._servers: Dict[str, McpServerConfig] = {}
        for raw in servers_raw:
            if not isinstance(raw, dict):
                raise ConfigurationError("mcp.servers 配置项格式错误(应为对象)。")
            server = McpServerConfig.from_dict(raw)
            self._servers[server.name] = server

        mapping = section.get("tool_mapping") or {}
        if not isinstance(mapping, dict):
            raise ConfigurationError("mcp.tool_mapping 配置格式错误(应为对象)。")
        self._tool_mapping: Dict[str, str] = {str(k): str(v) for k, v in mapping.items()}

        self._sessions: Dict[str, ClientSession] = {}
        self._stacks: Dict[str, AsyncExitStack] = {}
        self._locks: Dict[str, asyncio.Lock] = {name: asyncio.Lock() for name in self._servers}
        self._started = False

    # ------------------------------------------------------------- 配置查询
    @property
    def server_names(self) -> List[str]:
        return list(self._servers)

    def mapping_for(self, tool_name: str) -> str | None:
        """平台工具名 -> ``server.tool`` 映射,未映射返回 None。"""

        return self._tool_mapping.get(tool_name)

    def require_mapping(self, tool_name: str) -> str:
        mapping = self.mapping_for(tool_name)
        if not mapping:
            raise McpError(
                f"工具 [{tool_name}] 未在 business.yaml 的 mcp.tool_mapping 中配置映射,无法获取数据。"
            )
        return mapping

    # ------------------------------------------------------------- 连接管理
    async def start(self) -> None:
        """启动时连接所有 MCP Server(失败即报错,快速失败)。"""

        if self._started:
            return
        for name in self._servers:
            await self._connect(name)
        self._started = True
        logger.info("MCP 数据面已连接:%s", ", ".join(self._servers))

    async def stop(self) -> None:
        for name in list(self._stacks):
            await self._disconnect(name)
        self._started = False
        logger.info("MCP 数据面连接已全部关闭。")

    async def _connect(self, name: str) -> ClientSession:
        server = self._servers[name]
        stack = AsyncExitStack()
        try:
            if server.transport == "http":
                read_stream, write_stream, _ = await stack.enter_async_context(
                    streamablehttp_client(
                        server.url,
                        headers=server.headers or None,
                        timeout=server.timeout_seconds,
                    )
                )
            else:
                import os

                params = StdioServerParameters(
                    command=server.command,
                    args=server.args,
                    env={**os.environ, **server.env},
                )
                read_stream, write_stream = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await asyncio.wait_for(session.initialize(), timeout=server.timeout_seconds)
        except Exception as exc:
            await stack.aclose()
            raise McpError(f"MCP server [{name}] 连接失败:{exc}") from exc
        self._stacks[name] = stack
        self._sessions[name] = session
        logger.info("MCP server [%s] 已连接(%s)。", name, server.transport)
        return session

    async def _disconnect(self, name: str) -> None:
        stack = self._stacks.pop(name, None)
        self._sessions.pop(name, None)
        if stack is not None:
            try:
                await stack.aclose()
            except Exception as exc:  # 关闭阶段的异常只记录不抛出
                logger.warning("关闭 MCP server [%s] 连接时出错:%s", name, exc)

    async def _ensure_session(self, name: str) -> ClientSession:
        session = self._sessions.get(name)
        if session is not None:
            return session
        async with self._locks[name]:
            session = self._sessions.get(name)
            if session is not None:
                return session
            return await self._connect(name)

    async def _reconnect(self, name: str) -> ClientSession:
        await self._disconnect(name)
        async with self._locks[name]:
            session = self._sessions.get(name)
            if session is not None:
                return session
            return await self._connect(name)

    # ------------------------------------------------------------- 调用
    async def call_tool(
        self,
        mapping: str,
        arguments: Dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> Dict[str, Any]:
        """调用映射为 ``server.tool`` 的上游工具,返回归一化后的 dict。

        传输层异常(断线等)会自动重连并重试一次;上游工具自身报错立即抛出。
        """

        if "." not in mapping:
            raise McpError(f"MCP 工具映射格式错误(应为 server.tool):{mapping!r}")
        server_name, tool_name = mapping.split(".", 1)
        server = self._servers.get(server_name)
        if server is None:
            raise McpError(f"未配置的 MCP 服务器:{server_name}(映射 {mapping!r})")
        timeout = timeout_seconds or server.timeout_seconds

        last_error: Exception | None = None
        for attempt in (1, 2):
            try:
                session = await self._ensure_session(server_name)
                result = await asyncio.wait_for(
                    session.call_tool(tool_name, arguments or {}),
                    timeout=timeout,
                )
                return self._normalize_result(result, mapping)
            except McpError:
                raise  # 上游工具报错,不重试
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "MCP 调用 %s 第 %d 次失败:%s,尝试重连重试。", mapping, attempt, exc
                )
                try:
                    await self._reconnect(server_name)
                except McpError as reconnect_error:
                    raise reconnect_error from exc
        raise McpError(f"MCP 调用 {mapping} 失败:{last_error}")

    async def list_server_tools(self, server_name: str) -> List[Dict[str, Any]]:
        """列出上游 MCP Server 暴露的工具(健康检查/能力发现)。"""

        server = self._servers.get(server_name)
        if server is None:
            raise McpError(f"未配置的 MCP 服务器:{server_name}")
        session = await self._ensure_session(server_name)
        result = await asyncio.wait_for(session.list_tools(), timeout=server.timeout_seconds)
        return [tool.model_dump(mode="json") for tool in result.tools]

    async def health(self) -> Dict[str, Any]:
        """数据面健康状态(供 /health 端点使用)。"""

        status: Dict[str, Any] = {}
        for name in self._servers:
            try:
                tools = await self.list_server_tools(name)
                status[name] = {"connected": True, "tools": len(tools)}
            except Exception as exc:
                status[name] = {"connected": False, "error": str(exc)}
        return status

    # ------------------------------------------------------------- 结果归一化
    @staticmethod
    def _normalize_result(result: Any, mapping: str) -> Dict[str, Any]:
        """把 CallToolResult 归一化为 dict。

        优先级:structuredContent > 文本内容(尝试 JSON 解析)> 纯文本。
        FastMCP 等框架会把工具返回值包在 ``{"result": ...}`` 里,这里统一解包,
        避免各上游框架差异渗透到业务层。
        """

        if getattr(result, "isError", False):
            raise McpError(f"上游工具 {mapping} 返回错误:{McpClientManager._content_text(result)}")
        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, dict) and structured:
            unwrapped = McpClientManager._unwrap_fastmcp(structured)
            return unwrapped
        text = McpClientManager._content_text(result)
        if not text:
            return {"result": f"上游工具 {mapping} 未返回内容。", "data": {}}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"result": text, "data": {}}
        if isinstance(parsed, dict):
            return parsed
        return {"result": text, "data": parsed}

    @staticmethod
    def _unwrap_fastmcp(structured: Dict[str, Any]) -> Dict[str, Any]:
        """解包 FastMCP 的 ``{"result": ...}`` 约定。"""

        if set(structured.keys()) == {"result"}:
            inner = structured["result"]
            if isinstance(inner, dict):
                return inner
            if isinstance(inner, list):
                return {"items": inner, "result": ""}
        return structured

    @staticmethod
    def _content_text(result: Any) -> str:
        parts: List[str] = []
        for block in getattr(result, "content", None) or []:
            text = getattr(block, "text", None)
            if text:
                parts.append(str(text))
        return "\n".join(parts).strip()
