"""业务配置加载与启动校验。

读取 ``app/config/business.yaml``,把「换一家公司的客服」变成改一个 YAML。
支持 ``${ENV_VAR:-default}`` 形式的环境变量插值,密钥不进仓库。

模型槽位(decision=Jev / chat=Qwen / title)均为必配:
未配置 base_url / api_key 时 :func:`BusinessConfig.validate` 会直接抛出
:class:`ConfigurationError`,服务拒绝启动——不做任何静默降级。
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

import yaml

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "business.yaml"

#: 必须配置的模型槽位 -> 对应的环境变量说明
REQUIRED_SLOTS = ("decision", "chat")


class ConfigurationError(RuntimeError):
    """配置缺失或非法。"""


def _interpolate(value: Any) -> Any:
    """递归替换字符串中的 ``${VAR:-default}``。"""

    if isinstance(value, str):

        def _replace(match: "re.Match[str]") -> str:
            name, default = match.group(1), match.group(2)
            env = os.environ.get(name)
            if env:
                return env
            return default if default is not None else ""

        return _ENV_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {key: _interpolate(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_interpolate(item) for item in value]
    return value


class BusinessConfig:
    """business.yaml 的类型化访问门面。"""

    def __init__(self, data: Dict[str, Any]) -> None:
        self._data = _interpolate(data)

    # ------------------------------------------------------------ 基础访问
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def section(self, dotted: str) -> Dict[str, Any]:
        node = self.get(dotted, {})
        return node if isinstance(node, dict) else {}

    @property
    def raw(self) -> Dict[str, Any]:
        return self._data

    # ------------------------------------------------------------ 公司/客服
    @property
    def company_name(self) -> str:
        return str(self.get("company.name", "客服平台"))

    @property
    def industry(self) -> str:
        return str(self.get("company.industry", "generic"))

    @property
    def agent_name(self) -> str:
        return str(self.get("customer_service.name", "客服助手"))

    @property
    def language(self) -> str:
        return str(self.get("customer_service.language", "zh-CN"))

    @property
    def greeting(self) -> str:
        return str(self.get("customer_service.greeting", "您好,请问有什么可以帮您?"))

    @property
    def composer_placeholder(self) -> str:
        return str(self.get("customer_service.composer_placeholder", "输入你的问题…"))

    @property
    def binding_texts(self) -> Dict[str, str]:
        """右侧「绑定用户」卡片文案(通用平台,换行业只改 business.yaml)。"""

        section = self.section("customer_service.binding")
        defaults: Dict[str, str] = {
            "title": "绑定用户",
            "input_placeholder": "输入用户ID",
            "submit_label": "绑定",
            "unbind_label": "解绑",
            "hint": "输入用户ID绑定后,右侧展示该用户档案,对话中只能查询其订单信息。",
            "bound_hint": "当前会话仅可查询该用户的订单信息",
        }
        return {
            key: str(section.get(key) or default)
            for key, default in defaults.items()
        }

    # ------------------------------------------------------------ 模型层
    def model_config(self, slot: str) -> Dict[str, Any]:
        """decision / chat / title 三个模型槽位的配置。

        每个槽位完全独立:provider、base_url、api_key 各自配置。
        Jev(decision)与 Qwen(chat)通过各自的环境变量注入,互不影响。
        """

        base: Dict[str, Any] = {
            "provider": "",
            "model": "",
            "base_url": "",
            "api_key": "",
            "temperature": 0.3,
            "timeout_seconds": 60,
        }
        base.update(self.section(f"models.{slot}"))
        return base

    def require_model(self, slot: str) -> Dict[str, Any]:
        """获取模型槽位配置,缺失时抛出 :class:`ConfigurationError`。"""

        cfg = self.model_config(slot)
        missing = [
            name
            for name in ("provider", "model", "base_url", "api_key")
            if not str(cfg.get(name, "")).strip()
        ]
        if missing:
            raise ConfigurationError(
                f"模型槽位 [{slot}] 未完整配置,缺少:{', '.join(missing)}。"
                f"请设置环境变量(见 business.yaml models.{slot} 段)后重启。"
            )
        return cfg

    # ------------------------------------------------------------ 知识库
    @property
    def knowledge(self) -> Dict[str, Any]:
        base: Dict[str, Any] = {
            "enabled": True,
            "vector_store": "memory",
            "top_k": 3,
            "score_threshold": 0.05,
        }
        base.update(self.section("knowledge"))
        return base

    # ------------------------------------------------------------ 工具/意图/规则
    @property
    def enabled_tools(self) -> List[str]:
        tools = self.get("tools", [])
        return [str(t) for t in tools] if isinstance(tools, list) else []

    @property
    def intent_catalog(self) -> Dict[str, Any]:
        return self.section("intents")

    @property
    def panels(self) -> List[Dict[str, Any]]:
        panels = self.get("panels", [])
        return [p for p in panels if isinstance(p, dict)] if isinstance(panels, list) else []

    def rule(self, name: str) -> Dict[str, Any]:
        return self.section(f"rules.{name}")

    @property
    def rules(self) -> Dict[str, Any]:
        return self.section("rules")

    # ------------------------------------------------------------ Jev 参数
    @property
    def jev(self) -> Dict[str, Any]:
        return self.section("jev")

    # ------------------------------------------------------------ 启动校验
    def validate(self) -> None:
        """启动时校验必配项,缺失即报错(不静默降级)。"""

        problems: List[str] = []
        for slot in REQUIRED_SLOTS:
            try:
                self.require_model(slot)
            except ConfigurationError as exc:
                problems.append(str(exc))
        if not self.enabled_tools:
            problems.append("business.yaml 的 tools 列表为空,至少启用一个工具。")
        mcp_section = self.section("mcp")
        if not mcp_section.get("servers"):
            problems.append("business.yaml 的 mcp.servers 为空:数据面必须至少配置一个 MCP Server。")
        if not mcp_section.get("tool_mapping"):
            problems.append("business.yaml 的 mcp.tool_mapping 为空:至少映射一个上游工具。")
        if problems:
            raise ConfigurationError(
                "客服平台配置不完整,服务拒绝启动:\n- " + "\n- ".join(problems)
            )


@lru_cache(maxsize=1)
def load_config(path: str | None = None) -> BusinessConfig:
    """加载(并缓存)业务配置。"""

    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ConfigurationError(f"配置文件格式错误: {config_path}")
    return BusinessConfig(data)
