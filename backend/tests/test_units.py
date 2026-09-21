"""单元测试(纯离线,不依赖任何外部服务与 mock):

- Jev 输出解析与校验
- 向量检索(memory TF-IDF)
- Widget 模板构建
- Policy 路由策略
- MCP 客户端配置校验
"""

from __future__ import annotations

import pytest

from app.ai.jev import JevDecisionEngine
from app.config import BusinessConfig, ConfigurationError
from app.core.policy import Policy
from app.integrations.mcp import McpClientManager, McpError, McpServerConfig
from app.knowledge import FAQ_DOCUMENTS, MemoryVectorStore
from app.widgets import build_confirm_widget, build_option_list_widget


def _jev_engine() -> JevDecisionEngine:
    """构造一个不发起真实调用的 Jev 引擎(仅用于输出解析测试)。"""

    config = BusinessConfig(
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
                "greet": {"action": "none", "description": "打招呼"},
                "order_query": {"action": "get_order", "description": "查订单"},
                "human_request": {"action": "transfer_to_human", "description": "转人工"},
                "knowledge_query": {"action": "query_knowledge", "description": "通用问题"},
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


def test_jev_engine_requires_config():
    """模型槽位未配置时构造引擎必须报错(不静默降级)。"""

    config = BusinessConfig({"models": {"decision": {"provider": "openjev"}}})
    with pytest.raises(ConfigurationError):
        JevDecisionEngine(config)


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
