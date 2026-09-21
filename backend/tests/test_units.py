"""单元测试:Jev 输出解析、向量检索、Widget 模板、Policy。"""

from __future__ import annotations

import pytest

from app.ai.jev import DecisionError
from app.config import BusinessConfig, ConfigurationError
from app.core.policy import Policy
from app.integrations.mcp import McpClientManager, McpError, McpServerConfig
from app.knowledge import FAQ_DOCUMENTS, MemoryVectorStore
from app.widgets import build_confirm_widget, build_option_list_widget


# ---------------------------------------------------------------------------
# Jev 输出解析
# ---------------------------------------------------------------------------
def test_parse_plain_json(customer_server):
    decision = customer_server.jev._parse_decision(
        '{"intent":"order_query","action":"get_order","confidence":0.9}'
    )
    assert decision.action == "get_order"
    assert decision.confidence == 0.9
    assert decision.need_tool is True


def test_parse_markdown_fenced_json(customer_server):
    content = '好的,结果如下:\n```json\n{"intent":"greet","action":"none","confidence":0.6}\n```'
    decision = customer_server.jev._parse_decision(content)
    assert decision.action == "none"


def test_parse_rejects_unknown_action(customer_server):
    with pytest.raises(DecisionError):
        customer_server.jev._parse_decision('{"action":"hack_the_planet"}')


def test_parse_rejects_garbage(customer_server):
    with pytest.raises(DecisionError):
        customer_server.jev._parse_decision("完全不是 JSON")


def test_parse_normalizes_emotion(customer_server):
    decision = customer_server.jev._parse_decision(
        '{"action":"none","emotion":"ANGRY","confidence":"abc"}'
    )
    assert decision.emotion == "angry"
    assert decision.confidence == 0.5  # 非法数值回退默认


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
        McpServerConfig.from_dict({"name": "crm", "transport": "http"})


def test_mcp_server_config_rejects_bad_transport():
    with pytest.raises(ConfigurationError):
        McpServerConfig.from_dict({"name": "crm", "transport": "grpc"})


def test_mcp_manager_requires_servers():
    config = BusinessConfig({"mcp": {"servers": [], "tool_mapping": {}}})
    with pytest.raises(ConfigurationError):
        McpClientManager(config)


def test_mcp_manager_requires_mapping():
    config = BusinessConfig(
        {
            "mcp": {
                "servers": [{"name": "crm", "transport": "http", "url": "http://x/mcp"}],
                "tool_mapping": {},
            }
        }
    )
    manager = McpClientManager(config)
    with pytest.raises(McpError):
        manager.require_mapping("get_order")
    assert manager.mapping_for("get_order") is None
