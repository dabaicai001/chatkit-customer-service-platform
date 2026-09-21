"""端到端测试:用户消息 → Jev → Router → MCP → Qwen → ChatKit 事件。"""

from __future__ import annotations

from datetime import datetime

import pytest
from chatkit.types import (
    AssistantMessageItem,
    InferenceOptions,
    ThreadItemDoneEvent,
    ThreadMetadata,
    UserMessageItem,
    UserMessageTextContent,
    WidgetItem,
)

from app.ai.jev import Decision


def make_thread(thread_id: str = "thread_e2e") -> ThreadMetadata:
    return ThreadMetadata(id=thread_id, created_at=datetime.now(), title=None)


def make_user_message(thread_id: str, text: str) -> UserMessageItem:
    return UserMessageItem(
        thread_id=thread_id,
        id=f"msg_{thread_id}",
        created_at=datetime.now(),
        content=[UserMessageTextContent(type="input_text", text=text)],
        inference_options=InferenceOptions(),
    )


async def collect_respond(server, thread, message):
    events = []
    async for event in server.respond(thread, message, {"request": None}):
        events.append(event)
    return events


def assistant_texts(events) -> list[str]:
    texts = []
    for event in events:
        if isinstance(event, ThreadItemDoneEvent) and isinstance(event.item, AssistantMessageItem):
            for content in event.item.content:
                texts.append(content.text)
    return texts


def widget_items(events) -> list[WidgetItem]:
    return [
        event.item
        for event in events
        if isinstance(event, ThreadItemDoneEvent) and isinstance(event.item, WidgetItem)
    ]


# ---------------------------------------------------------------------------
# Jev 决策
# ---------------------------------------------------------------------------
async def test_jev_decides_order_query(customer_server):
    decision = await customer_server.jev.analyze(
        "我的订单到哪了?订单号 20260921001", history="", customer_summary=""
    )
    assert decision.action == "get_order"
    assert decision.intent == "order_query"
    assert decision.confidence >= 0.9
    assert decision.slots.get("order_id") == "20260921001"


async def test_jev_detects_angry_emotion(customer_server):
    decision = await customer_server.jev.analyze("你们这什么垃圾服务,气死我了")
    assert decision.emotion == "angry"
    assert decision.action == "transfer_to_human"


async def test_jev_retries_then_succeeds(customer_server):
    """重试参数来自配置(attempts=2),单次成功也应正常返回。"""

    decision = await customer_server.jev.analyze("你好")
    assert decision.action == "none"


# ---------------------------------------------------------------------------
# 路由器策略
# ---------------------------------------------------------------------------
async def test_low_confidence_falls_back_to_knowledge(customer_server):
    decision = Decision(action="get_order", confidence=0.3, reason="测试低置信度")
    route = await customer_server.router.route(decision, "thread_policy_1", "订单到哪了")
    assert route.action == "query_knowledge"
    assert route.confidence_level == "low"
    assert route.passages, "低置信度应兜底检索知识库"


async def test_emotion_routing_overrides_business_action(customer_server):
    decision = Decision(action="get_order", confidence=0.95, emotion="angry")
    route = await customer_server.router.route(decision, "thread_policy_2", "订单呢")
    assert route.action == "transfer_to_human"
    assert route.handoff is not None
    assert route.handoff["queue_position"] == 2


async def test_medium_confidence_executes_but_flags(customer_server):
    decision = Decision(action="get_order", confidence=0.7, slots={"order_id": "20260921001"})
    route = await customer_server.router.route(decision, "thread_policy_3", "订单")
    assert route.action == "get_order"
    assert route.confidence_level == "medium"
    assert route.tool_result is not None
    assert "20260921001" in route.tool_result["result"]


async def test_unknown_tool_not_enabled_is_blocked(customer_server):
    decision = Decision(action="not_a_tool", confidence=0.99)
    route = await customer_server.router.route(decision, "thread_policy_4", "测试")
    assert route.action in ("none", "query_knowledge")


# ---------------------------------------------------------------------------
# 工具 / MCP 数据面
# ---------------------------------------------------------------------------
async def test_search_customer_via_mcp_binds_identity(customer_server):
    result = await customer_server.tools.execute(
        "search_customer", thread_id="thread_tool_1", params={"keyword": "李明"}
    )
    assert result["found"] is True
    assert result["data"]["customer_id"] == "cus_10086"
    assert customer_server.sessions.customer_id("thread_tool_1") == "cus_10086"


async def test_get_order_requires_identity(customer_server):
    with pytest.raises(Exception) as exc_info:
        await customer_server.tools.execute(
            "get_order", thread_id="thread_tool_2", params={}
        )
    assert "客户身份" in str(exc_info.value) or "订单号" in str(exc_info.value)


async def test_refund_confirmation_flow(customer_server):
    """退款需确认:先挂起 + 确认卡片,确认后才经 MCP 执行。"""

    thread_id = "thread_confirm_1"
    decision = Decision(
        action="refund_order",
        confidence=0.95,
        slots={"order_id": "20260921001"},
    )
    route = await customer_server.router.route(decision, thread_id, "退款")
    assert route.awaiting_confirmation is not None
    assert route.tool_result is None

    # 未确认前,上游订单状态不变
    order = await customer_server.gateway.load_profile("cus_10086")
    target = next(o for o in order.orders if o.id == "20260921001")
    assert target.status != "已退款"

    # 用户点确认
    confirmed = await customer_server.router.execute_confirmed(
        thread_id, route.awaiting_confirmation.action_id
    )
    assert confirmed.tool_result is not None
    assert confirmed.state_changed is True
    assert "退款" in confirmed.tool_result["result"]

    # 上游状态已变更(经 MCP)
    order_after = await customer_server.gateway.load_profile("cus_10086", refresh=True)
    target_after = next(o for o in order_after.orders if o.id == "20260921001")
    assert target_after.status == "已退款"


async def test_cancel_pending_action(customer_server):
    thread_id = "thread_confirm_2"
    decision = Decision(action="cancel_order", confidence=0.95, slots={"order_id": "20260915088"})
    route = await customer_server.router.route(decision, thread_id, "取消订单")
    assert route.awaiting_confirmation is not None

    cancelled = await customer_server.router.cancel_pending(
        thread_id, route.awaiting_confirmation.action_id
    )
    assert cancelled.direct_answer is True
    assert customer_server.sessions.get_pending_action(thread_id) is None


async def test_knowledge_tool_returns_passages(customer_server):
    result = await customer_server.tools.execute(
        "query_knowledge", thread_id="thread_tool_3", params={"question": "怎么退款"}
    )
    assert result["found"] is True
    assert any("退款" in p["title"] for p in result["data"]["passages"])


async def test_tool_param_validation(customer_server):
    from app.tools import ToolError

    with pytest.raises(ToolError):
        await customer_server.tools.execute(
            "refund_order", thread_id="thread_tool_4", params={"reason": "不想要了"}
        )


# ---------------------------------------------------------------------------
# 完整 respond 链路
# ---------------------------------------------------------------------------
async def test_respond_order_query_end_to_end(customer_server):
    thread = make_thread()
    message = make_user_message(thread.id, "帮我查下订单 20260921001 到哪了")
    events = await collect_respond(customer_server, thread, message)

    texts = assistant_texts(events)
    assert texts, "应产出助手消息"
    assert "mock 客服回复" in texts[-1]
    # 决策已沉淀为隐藏上下文
    assert customer_server.memory.last_decision(thread.id)["action"] == "get_order"


async def test_respond_streams_text_deltas(customer_server):
    thread = make_thread("thread_stream")
    message = make_user_message(thread.id, "你好")
    events = await collect_respond(customer_server, thread, message)
    delta_events = [
        e for e in events if type(e).__name__ == "ThreadItemUpdatedEvent"
    ]
    assert delta_events, "应产生流式增量事件"


async def test_respond_refund_shows_confirm_widget(customer_server):
    thread = make_thread("thread_widget")
    message = make_user_message(thread.id, "订单 20260921001 我要退款")
    events = await collect_respond(customer_server, thread, message)
    widgets = widget_items(events)
    assert widgets, "退款应弹出确认卡片"
    assert "确认要为订单 20260921001" in assistant_texts(events)[0]


async def test_respond_generates_title(customer_server):
    thread = make_thread("thread_title")
    message = make_user_message(thread.id, "我的订单到哪了")
    await collect_respond(customer_server, thread, message)
    assert thread.title == "订单物流咨询"


# ---------------------------------------------------------------------------
# 配置 fail-fast
# ---------------------------------------------------------------------------
def test_default_config_without_keys_refuses_to_start(monkeypatch, tmp_path):
    """默认 business.yaml 未配 key 时,validate() 必须报错(不降级)。"""

    from app.config import BusinessConfig, ConfigurationError

    for var in (
        "JEV_PROVIDER", "JEV_BASE_URL", "JEV_API_KEY", "JEV_MODEL",
        "QWEN_PROVIDER", "QWEN_BASE_URL", "QWEN_API_KEY", "QWEN_MODEL",
        "CRM_MCP_URL", "CRM_MCP_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)

    import yaml

    from app.config import DEFAULT_CONFIG_PATH

    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    config = BusinessConfig(data)
    with pytest.raises(ConfigurationError) as exc_info:
        config.validate()
    assert "decision" in str(exc_info.value)
    assert "chat" in str(exc_info.value)
