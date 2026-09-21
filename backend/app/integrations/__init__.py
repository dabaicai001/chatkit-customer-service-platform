"""integrations:外部系统接入层(MCP 数据面)。"""

from .mcp import McpClientManager, McpError, McpServerConfig

__all__ = ["McpClientManager", "McpError", "McpServerConfig"]
