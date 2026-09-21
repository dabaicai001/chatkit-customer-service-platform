"""单元测试(纯离线,不依赖任何外部服务与 mock):

- Jev 输出解析与校验(含 AGENT 字段)
- AGENT 注册表(配置驱动)与调度解析
- Router 垃圾信息直通(不过生成模型)
- 向量检索(memory TF-IDF)
- Widget 模板构建
- Policy 路由策略
- MCP 客户端配置校验
"""

from __future__ import annotations

import pytest

from app.ai.agents import AgentRegistry
from app.ai.jev import JevDecisionEngine
from app.ai.rag import RagService
from app.config import BusinessConfig, ConfigurationError
from app.core.policy import Policy
from app.core.routing import Router, resolve_agent
from app.core.session import SessionStateManager
from app.integrations.mcp import McpClientManager, McpError, McpServerConfig
from app.knowledge import FAQ_DOCUMENTS, MemoryVectorStore
from app.widgets import build_confirm_widget, build_option_list_widget


def _jev_engine(config: BusinessConfig | None = None) -> JevDecisionEngine:
    """构造一个不发起真实调用的 Jev 引擎(仅用于输出解析测试)。"""

    config = config or BusinessConfig(
        {
            "models": {
                "decision": {
                    "provider": "openjev",
                    "model": "jev-latest",
                    "base_url": "http://127.0.0.1:9/v1",
                    "api_key": "unit-test",
                }
            },
            "intents": {
                "greet": {"action": "none", "agent": "chat_agent", "description": "打招呼"},
                "order_query": {"action": "get_order", "agent": "order_agent", "description": "查订单"},
                "garbage": {
                    "action": "none",
                    "agent": "chat_agent",
                    "description": "垃圾信息",
                    "direct_reply": "您好,我是{agent_name}。",
                },
            },
            "agents": {
                "default": "chat_agent",
                "chat_agent": {"title": "通用客服", "description": "接待"},
                "order_agent": {"title": "订单客服", "description": "查订单"},
            },
            "jev": {
                "confidence": {"high": 0.85, "low": 0.55},
                "fallback_action": "query_knowledge",
                "retry": {"attempts": 2, "backoff_seconds": 0.1},
            },
        }
    )
    return JevDecisionEngine(config)


# ---------------------------------------------------------------------------
# Jev 输出解析
# ---------------------------------------------------------------------------
def test_parse_plain_json():
    decision = _jev_engine()._parse_decision(
        '{"intent":"order_query","action":"get_order","confidence":0.9}'
    )
    assert decision.action == "get_order"
    assert decision.confidence == 0.9
    assert decision.need_tool is True


def test_parse_markdown_fenced_json():
    content = '好的,结果如下:\n```json\n{"intent":"greet","action":"none","confidence":0.6}\n```'
    decision = _jev_engine()._parse_decision(content)
    assert decision.action == "none"


def test_parse_rejects_unknown_action():
    with pytest.raises(Exception) as exc_info:
        _jev_engine()._parse_decision('{"action":"hack_the_planet"}')
    assert "action" in str(exc_info.value)


def test_parse_rejects_garbage():
    with pytest.raises(Exception):
        _jev_engine()._parse_decision("完全不是 JSON")


def test_parse_normalizes_emotion():
    decision = _jev_engine()._parse_decision(
        '{"action":"none","emotion":"ANGRY","confidence":"abc"}'
    )
    assert decision.emotion == "angry"
    assert decision.confidence == 0.5  # 非法数值回退默认


def test_parse_agent_field():
    decision = _jev_engine()._parse_decision(
        '{"intent":"order_query","action":"get_order","agent":"order_agent","confidence":0.9}'
    )
    assert decision.agent == "order_agent"


def test_parse_unknown_agent_becomes_empty():
    """Jev 幻觉出未注册 AGENT 时置空(由路由按意图/默认兜底),不报错。"""

    decision = _jev_engine()._parse_decision(
        '{"action":"none","agent":"ghost_agent"}'
    )
    assert decision.agent == ""


def test_jev_engine_requires_config():
    """模型槽位未配置时构造引擎必须报错(不静默降级)。"""

    config = BusinessConfig({"models": {"decision": {"provider": "openjev"}}})
    with pytest.raises(ConfigurationError):
        JevDecisionEngine(config)


# ---------------------------------------------------------------------------
# AGENT 注册表与调度
# ---------------------------------------------------------------------------
def _agent_config() -> BusinessConfig:
    return BusinessConfig(
        {
            "agents": {
                "default": "chat_agent",
                "chat_agent": {
                    "title": "通用客服",
                    "description": "寒暄接待",
                    "instructions": "你是{agent_name}。",
                    "tools": ["search_customer"],
                },
                "order_agent": {
                    "title": "订单客服",
                    "description": "订单查询",
                    "tools": ["get_order"],
                    "needs_rag": False,
                },
            },
            "intents": {
                "order_query": {"action": "get_order", "agent": "order_agent"},
                "garbage": {
                    "action": "none",
                    "agent": "chat_agent",
                    "direct_reply": "您好,我是{agent_name},{company_name}。",
                },
            },
        }
    )


def test_agent_registry_loads_from_config():
    registry = AgentRegistry(_agent_config())
    assert registry.names() == ["chat_agent", "order_agent"]
    assert registry.default_name == "chat_agent"
    order = registry.get("order_agent")
    assert order is not None
    assert order.title == "订单客服"
    assert order.tools == ("get_order",)


def test_agent_registry_catalog_and_resolve():
    registry = AgentRegistry(_agent_config())
    catalog = registry.catalog_text()
    assert "order_agent(订单客服)" in catalog
    assert registry.resolve("不存在").name == "chat_agent"  # 未知回退默认


def test_agent_registry_rejects_unknown_tools():
    config = BusinessConfig(
        {"agents": {"default": "a", "a": {"title": "A", "tools": ["ghost_tool"]}}}
    )
    registry = AgentRegistry(config)
    with pytest.raises(ValueError):
        registry.validate(["get_order"])


def test_agent_registry_rejects_empty():
    with pytest.raises(ValueError):
        AgentRegistry(BusinessConfig({"agents": {}}))


def test_resolve_agent_priority():
    """调度优先级:Jev 指定 > 意图映射 > 默认。"""

    from app.ai.jev import Decision

    config = _agent_config()
    registry = AgentRegistry(config)
    catalog = config.intent_catalog

    assert resolve_agent(Decision(agent="order_agent"), catalog, registry).name == "order_agent"
    assert resolve_agent(Decision(intent="order_query"), catalog, registry).name == "order_agent"
    assert resolve_agent(Decision(intent="未知"), catalog, registry).name == "chat_agent"


# ---------------------------------------------------------------------------
# Router:垃圾信息直通(不过生成模型)
# ---------------------------------------------------------------------------
def _minimal_router(config: BusinessConfig) -> Router:
    """构造不打真实连接的 Router(仅用于路由逻辑单测)。"""

    from app.tools import ToolRegistry

    mcp = McpClientManager(
        BusinessConfig(
            {
                "mcp": {
                    "servers": [
                        {"name": "upstream", "transport": "http", "url": "http://127.0.0.1:9/mcp"}
                    ],
                    "tool_mapping": {},
                }
            }
        )
    )
    sessions = SessionStateManager()
    return Router(
        tools=ToolRegistry(
            config=config, mcp=mcp, gateway=None, rag=RagService(config), sessions=sessions
        ),
        rag=RagService(config),
        gateway=None,
        sessions=sessions,
        policy=Policy(config),
        agents=AgentRegistry(config),
        intent_catalog=config.intent_catalog,
        config=config,
    )


def test_router_garbage_direct_reply():
    """垃圾意图配了 direct_reply:直接固化回复,不调工具、不生成。"""

    import asyncio

    from app.ai.jev import Decision

    config = _agent_config()
    route = asyncio.run(
        _minimal_router(config).route(
            Decision(intent="garbage", action="none", confidence=0.9),
            "thr_garbage",
            "广告广告广告",
        )
    )
    assert route.direct_reply == "您好,我是客服助手,客服平台。"
    assert route.direct_answer is True
    assert route.tool_result is None


def test_router_order_query_agent_dispatch():
    """订单意图未指定 agent 时按意图映射到订单客服。"""

    import asyncio

    from app.ai.jev import Decision

    config = _agent_config()
    route = asyncio.run(
        _minimal_router(config).route(
            Decision(intent="order_query", action="get_order", confidence=0.9),
            "thr_order",
            "查订单",
        )
    )
    assert route.agent is not None
    assert route.agent.name == "order_agent"


# ---------------------------------------------------------------------------
# 向量检索
# ---------------------------------------------------------------------------
def test_vector_store_search_refund_policy():
    store = MemoryVectorStore()
    store.add(FAQ_DOCUMENTS)
    results = store.search("怎么退款,多久到账", top_k=3)
    assert results
    assert results[0]["title"] == "退款政策"


def test_vector_store_search_shipping():
    store = MemoryVectorStore()
    store.add(FAQ_DOCUMENTS)
    results = store.search("发货了吗,快递到哪了")
    assert results
    assert any(r["title"] == "物流与配送" for r in results)


def test_vector_store_empty_query():
    store = MemoryVectorStore()
    store.add(FAQ_DOCUMENTS)
    assert store.search("") == []


# ---------------------------------------------------------------------------
# Widget 模板
# ---------------------------------------------------------------------------
def test_confirm_widget_builds():
    widget = build_confirm_widget(action_id="pa_123", prompt="确认退款吗?")
    assert widget is not None
    payload = widget.model_dump(mode="json")
    assert payload["type"] == "Card"
    rendered = str(payload)
    assert "pa_123" in rendered
    assert "确认退款吗?" in rendered


def test_option_list_widget_builds():
    widget = build_option_list_widget(
        [
            {"id": "o1", "title": "订单 A", "subtitle": "配送中", "badge": "进行中", "badge_color": "info"},
            {"id": "o2", "title": "订单 B", "subtitle": "已完成", "badge": "完成", "badge_color": "success"},
        ],
        title="请选择订单",
    )
    payload = widget.model_dump(mode="json")
    assert payload["type"] == "ListView"
    children = payload.get("children", [])
    assert len(children) == 3  # 标题 + 两个选项


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------
def _policy() -> Policy:
    config = BusinessConfig(
        {
            "jev": {
                "confidence": {"high": 0.85, "low": 0.55},
                "fallback_action": "query_knowledge",
                "emotion_routing": {"angry": "transfer_to_human", "normal": "none"},
                "retry": {"attempts": 3, "backoff_seconds": 0.2},
            },
            "rules": {
                "refund_order": {
                    "require_confirmation": True,
                    "confirmation_prompt": "确认退款 {order_id}?",
                },
                "human_transfer": {"enabled": True, "queue_position": 5},
            },
        }
    )
    return Policy(config)


def test_confidence_bands_and_levels():
    policy = _policy()
    bands = policy.confidence_bands()
    assert (bands.low, bands.high) == (0.55, 0.85)
    assert policy.confidence_level(0.9) == "high"
    assert policy.confidence_level(0.7) == "medium"
    assert policy.confidence_level(0.3) == "low"


def test_emotion_route_and_fallback():
    policy = _policy()
    assert policy.emotion_route("angry") == "transfer_to_human"
    assert policy.emotion_route("normal") is None
    assert policy.emotion_route("unknown") is None
    assert policy.fallback_action() == "query_knowledge"


def test_retry_settings():
    policy = _policy()
    assert policy.retry_attempts() == 3
    assert policy.retry_backoff_seconds() == 0.2


def test_confirmation_prompt_rendering():
    policy = _policy()
    assert policy.requires_confirmation("refund_order") is True
    assert policy.confirmation_prompt("refund_order", {"order_id": "123"}) == "确认退款 123?"


# ---------------------------------------------------------------------------
# MCP 配置校验
# ---------------------------------------------------------------------------
def test_mcp_server_config_requires_url():
    with pytest.raises(ConfigurationError):
        McpServerConfig.from_dict({"name": "upstream", "transport": "http"})


def test_mcp_server_config_rejects_bad_transport():
    with pytest.raises(ConfigurationError):
        McpServerConfig.from_dict({"name": "upstream", "transport": "grpc"})


def test_mcp_manager_requires_servers():
    config = BusinessConfig({"mcp": {"servers": [], "tool_mapping": {}}})
    with pytest.raises(ConfigurationError):
        McpClientManager(config)


def test_mcp_manager_requires_mapping():
    config = BusinessConfig(
        {
            "mcp": {
                "servers": [{"name": "upstream", "transport": "http", "url": "http://x/mcp"}],
                "tool_mapping": {},
            }
        }
    )
    manager = McpClientManager(config)
    with pytest.raises(McpError):
        manager.require_mapping("get_order")
    assert manager.mapping_for("get_order") is None
