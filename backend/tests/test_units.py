"""单元测试(纯离线,不依赖任何外部服务与 mock):

- Jev SystemOne answers 解析与校验(含 AGENT 字段、action 推荐函数、端点归一化)
- AGENT 注册表(配置驱动)与调度解析
- 垃圾意图固化直回(不过生成模型)
- 模型直连 MCP:function calling SSE 解析 / tool_calls 累积 / 参数残缺处理
- chat 槽位思维链过滤(<think> 状态机 / 非流式剥离)
- 向量检索(memory TF-IDF)
- Policy 路由策略
- MCP 客户端配置校验
"""

from __future__ import annotations

import pytest

from app.ai.agents import AgentRegistry
from app.ai.jev import Decision, JevDecisionEngine
from app.ai.mcp_agent import McpToolAgent, _ToolCallAccumulator
from app.ai.qwen import _ReasoningFilter, _strip_reasoning
from app.ai.rag import RagService
from app.config import BusinessConfig, ConfigurationError
from app.core.policy import Policy
from app.core.routing import resolve_agent, resolve_direct_reply
from app.core.session import SessionStateManager
from app.integrations.mcp import McpClientManager, McpError, McpServerConfig
from app.knowledge import MemoryVectorStore, load_faq_documents
from app.knowledge.vector_store import Document
from app.server import _summarize_arguments
from app.tools import ToolContext, ToolError
from app.tools.customer import SearchCustomerParams, _search_customer
from app.tools.order import GetOrderParams, _get_order


def _jev_engine(config: BusinessConfig | None = None) -> JevDecisionEngine:
    """构造一个不发起真实调用的 Jev 引擎(仅用于解析测试)。"""

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
                "knowledge_query": {
                    "action": "query_knowledge",
                    "agent": "knowledge_agent",
                    "description": "通用问题",
                },
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
                "knowledge_agent": {
                    "title": "知识库客服",
                    "description": "通用问题",
                    "needs_rag": True,
                },
            },
            "jev": {
                "confidence": {"high": 0.85, "low": 0.55},
                "fallback_action": "query_knowledge",
                "retry": {"attempts": 2, "backoff_seconds": 0.1},
            },
        }
    )
    return JevDecisionEngine(config)


def _jev_engine_with_actions() -> JevDecisionEngine:
    """注入了 action 推荐候选的 Jev 引擎(仅用于解析测试)。"""

    engine = _jev_engine()
    return JevDecisionEngine(
        engine._config,
        selectable_actions={
            "get_order": "查询订单状态/物流进度",
            "search_customer": "按手机号/客户ID 检索客户",
            "none": "不需要调用工具,直接与客户对话",
        },
    )


def _answers(
    intent: str = "order_query",
    confidence: float = 0.9,
    emotion: str = "normal",
    agent: str = "order_agent",
    action: str | None = None,
) -> dict:
    """构造一份 SystemOne 形态的 answers(choice 问题自带置信度)。"""

    answers = {
        "intent": {"type": "choice", "choice": intent, "confidence": confidence},
        "emotion": {"type": "choice", "choice": emotion, "confidence": confidence},
        "agent": {"type": "choice", "choice": agent, "confidence": confidence},
    }
    if action is not None:
        answers["action"] = {"type": "choice", "choice": action, "confidence": confidence}
    return answers


# ---------------------------------------------------------------------------
# Jev SystemOne answers 解析
# ---------------------------------------------------------------------------
def test_parse_answers_maps_action_and_confidence():
    decision = _jev_engine()._parse_decision(_answers())
    assert decision.intent == "order_query"
    assert decision.action == "get_order"  # action 永远来自 intent 的配置映射
    assert decision.confidence == 0.9
    assert decision.need_tool is True


def test_parse_answers_none_action():
    decision = _jev_engine()._parse_decision(_answers(intent="greet", agent="chat_agent"))
    assert decision.action == "none"
    assert decision.need_tool is False


def test_parse_answers_unknown_intent_falls_back():
    """Jev 幻觉出意图目录外的意图 → fallback,不报错。"""

    decision = _jev_engine()._parse_decision(_answers(intent="hack_the_planet"))
    assert decision.intent == "fallback"
    assert decision.action == "none"


def test_parse_answers_empty_is_graceful():
    """answers 为空/缺问题时按默认值处理(确定性失败由 _request_answers 抛)。"""

    decision = _jev_engine()._parse_decision({}, "你好")
    assert decision.intent == "fallback"
    assert decision.emotion == "normal"
    assert decision.action == "none"


def test_parse_answers_normalizes_emotion():
    decision = _jev_engine()._parse_decision(
        _answers(emotion="ANGRY", agent="chat_agent")
    )
    assert decision.emotion == "angry"  # 统一小写后命中 EMOTIONS
    assert decision.need_human is True


def test_parse_answers_invalid_confidence():
    answers = _answers(agent="order_agent")
    answers["intent"]["confidence"] = "abc"
    decision = _jev_engine()._parse_decision(answers)
    assert decision.confidence == 0.5  # 非法数值回退默认


def test_parse_answers_agent_field():
    decision = _jev_engine()._parse_decision(_answers())
    assert decision.agent == "order_agent"


def test_parse_answers_unknown_agent_becomes_empty():
    """Jev 幻觉出未注册 AGENT 时置空(由路由按意图/默认兜底),不报错。"""

    decision = _jev_engine()._parse_decision(_answers(agent="ghost_agent"))
    assert decision.agent == ""


def test_parse_answers_derives_flags():
    """need_* 由 action/emotion/AGENT 配置派生,非 Jev 自由发挥。"""

    engine = _jev_engine()
    # 知识库意图 + needs_rag 的 AGENT → need_rag
    decision = engine._parse_decision(
        _answers(intent="knowledge_query", agent="knowledge_agent")
    )
    assert decision.need_rag is True
    assert decision.action == "query_knowledge"
    # 业务数据动作 → need_customer_lookup
    decision = engine._parse_decision(_answers())
    assert decision.need_customer_lookup is True
    # 愤怒情绪 → need_human
    decision = engine._parse_decision(_answers(emotion="angry", agent="chat_agent"))
    assert decision.need_human is True


def test_action_recommendation_overrides_config_mapping():
    """Jev 直选 action 优先于 intents.<name>.action 配置映射。"""

    decision = _jev_engine_with_actions()._parse_decision(_answers(action="get_order"))
    assert decision.action == "get_order"
    assert decision.action_source == "jev"


def test_action_recommendation_invalid_falls_back_to_config():
    """Jev 幻觉出未注册/未启用的函数 → 回退意图配置映射,不报错。"""

    decision = _jev_engine_with_actions()._parse_decision(_answers(action="refund_order"))
    assert decision.action == "get_order"  # order_query 的配置映射
    assert decision.action_source == "config"


def test_action_recommendation_missing_answer_uses_config():
    """SystemOne 未回答 action 问题 → 走意图配置映射。"""

    decision = _jev_engine_with_actions()._parse_decision(_answers())
    assert decision.action == "get_order"
    assert decision.action_source == "config"


def test_action_recommendation_none_wins_over_config():
    """Jev 明确判断不需要工具时,即使是业务意图也以直选为准。"""

    decision = _jev_engine_with_actions()._parse_decision(_answers(action="none"))
    assert decision.action == "none"
    assert decision.action_source == "jev"


def test_action_recommendation_disabled_by_config():
    """jev.action_recommendation=false:不问 action 问题,完全走配置映射。"""

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
            "intents": {"order_query": {"action": "get_order", "agent": "order_agent"}},
            "agents": {"default": "order_agent", "order_agent": {"title": "订单客服"}},
            "jev": {"action_recommendation": False},
        }
    )
    engine = JevDecisionEngine(config, selectable_actions={"get_order": "查订单"})
    questions = engine._build_questions()
    assert "action" not in questions
    decision = engine._parse_decision(_answers(action="get_order"))
    assert decision.action == "get_order"
    assert decision.action_source == "config"


def test_build_questions_criteria_from_config():
    """未注入候选时,intent/emotion/agent 三个 choice 问题的 criteria 全部来自配置。"""

    questions = _jev_engine()._build_questions()
    assert set(questions) == {"intent", "emotion", "agent"}
    assert questions["intent"]["type"] == "choice"
    assert set(questions["intent"]["criteria"]) == {
        "greet", "order_query", "knowledge_query", "garbage",
    }
    assert set(questions["agent"]["criteria"]) == {
        "chat_agent", "order_agent", "knowledge_agent",
    }
    assert set(questions["emotion"]["criteria"]) == {
        "normal", "happy", "anxious", "frustrated", "angry",
    }


def test_build_questions_action_criteria_from_tools():
    """注入候选后新增 action 问题:criteria=启用工具 + none(自动补齐)。"""

    questions = _jev_engine_with_actions()._build_questions()
    assert set(questions) == {"intent", "emotion", "agent", "action"}
    assert questions["action"]["type"] == "choice"
    assert set(questions["action"]["criteria"]) == {
        "get_order", "search_customer", "none",
    }


def test_systemone_endpoint_never_appends_chat_completions():
    """三种 base_url 写法都归一化到 /v1/systemone,绝不拼 /chat/completions。"""

    expected = "https://api.typesafe.ai/v1/systemone"
    for base in ("https://api.typesafe.ai", "https://api.typesafe.ai/v1", expected):
        engine = _jev_engine(
            BusinessConfig(
                {
                    "models": {
                        "decision": {
                            "provider": "openjev",
                            "model": "jev-latest",
                            "base_url": base,
                            "api_key": "unit-test",
                        }
                    },
                    "intents": {"greet": {"action": "none"}},
                    "agents": {"default": "chat_agent", "chat_agent": {"title": "x"}},
                }
            )
        )
        endpoint = engine._systemone_endpoint()
        assert endpoint == expected
        assert "chat/completions" not in endpoint


def test_systemone_endpoint_missing_base_url_raises():
    """base_url 为空时引擎构造即报错(缺失即拒绝启动,不静默降级)。"""

    with pytest.raises(Exception) as exc_info:
        _jev_engine(
            BusinessConfig(
                {
                    "models": {
                        "decision": {
                            "provider": "openjev",
                            "model": "jev-latest",
                            "base_url": "",
                            "api_key": "unit-test",
                        }
                    },
                    "intents": {"greet": {"action": "none"}},
                    "agents": {"default": "chat_agent", "chat_agent": {"title": "x"}},
                }
            )
        )
    assert "base_url" in str(exc_info.value)


def test_jev_engine_requires_config():
    """模型槽位未配置时构造引擎必须报错(不静默降级)。"""

    config = BusinessConfig({"models": {"decision": {"provider": "openjev"}}})
    with pytest.raises(ConfigurationError):
        JevDecisionEngine(config)


def test_binding_texts_from_config_and_defaults():
    """「绑定用户」卡片文案来自 business.yaml(通用平台),缺省有兜底。"""

    # 1) business.yaml 默认配置(测试跑在仓库配置加载之外,手动给默认段)
    config = BusinessConfig({})
    texts = config.binding_texts
    assert texts["title"] == "绑定用户"
    assert texts["input_placeholder"] == "输入用户ID"
    assert texts["submit_label"] == "绑定"
    assert texts["unbind_label"] == "解绑"

    # 2) 换行业:YAML 改了文案就跟着变(前端零硬编码)
    config = BusinessConfig(
        {
            "customer_service": {
                "binding": {
                    "title": "绑定患者",
                    "input_placeholder": "输入患者ID",
                    "submit_label": "确认绑定",
                    "unbind_label": "解除",
                    "hint": "绑定后仅可查询该患者的就诊记录。",
                    "bound_hint": "当前会话仅可查询该患者的就诊记录",
                }
            }
        }
    )
    texts = config.binding_texts
    assert texts["title"] == "绑定患者"
    assert texts["input_placeholder"] == "输入患者ID"
    assert texts["submit_label"] == "确认绑定"
    assert texts["hint"] == "绑定后仅可查询该患者的就诊记录。"

    # 3) 部分配置时,未配的键回退默认
    config = BusinessConfig({"customer_service": {"binding": {"title": "绑定患者"}}})
    texts = config.binding_texts
    assert texts["title"] == "绑定患者"
    assert texts["submit_label"] == "绑定"


# ---------------------------------------------------------------------------
# chat 槽位思维链过滤(MiniMax-M3 会把 <think> 混进 content)
# ---------------------------------------------------------------------------
def test_reasoning_filter_passes_plain_text():
    f = _ReasoningFilter()
    assert f.feed("您好,有") == "您好,有"
    assert f.feed("什么可以帮您?") == "什么可以帮您?"
    assert f.flush() == ""


def test_reasoning_filter_drops_closed_think_block():
    f = _ReasoningFilter()
    out = f.feed("<think>这是思考过程</think>您好!非常抱歉,目前没有相关商品信息。")
    assert out == "您好!非常抱歉,目前没有相关商品信息。"
    assert f.flush() == ""


def test_reasoning_filter_handles_chunk_split_tags():
    """<think>/</think> 跨 chunk 断裂时也不能漏出思维链文本。"""

    f = _ReasoningFilter()
    assert f.feed("订单查询,<thi") == "订单查询,"
    assert f.feed("nk>这是思考过程</thi") == ""  # 思维链中,close 未闭合
    assert f.feed("nk>您好,您的订单已发货。") == "您好,您的订单已发货。"
    assert f.flush() == ""


def test_reasoning_filter_drops_unclosed_think():
    f = _ReasoningFilter()
    assert f.feed("<think>这段思考没有闭合") == ""
    assert f.flush() == ""


def test_strip_reasoning_non_streaming():
    assert _strip_reasoning("<think>思考</think>商品咨询标题") == "商品咨询标题"
    assert _strip_reasoning("<think>只有思考没有答案") == ""
    assert _strip_reasoning("普通标题") == "普通标题"


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
# 垃圾意图固化直回(不过生成模型、不调工具)
# ---------------------------------------------------------------------------
def _direct_reply_config() -> BusinessConfig:
    return BusinessConfig(
        {
            "company": {"name": "客服平台"},
            "customer_service": {"name": "客服助手"},
            "agents": {
                "default": "chat_agent",
                "chat_agent": {"title": "通用客服", "description": "接待"},
                "order_agent": {"title": "订单客服", "description": "查订单"},
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


def test_resolve_direct_reply_renders_template():
    """垃圾意图配了 direct_reply:渲染占位符返回固化文案。"""

    config = _direct_reply_config()
    catalog = config.intent_catalog
    assert (
        resolve_direct_reply(Decision(intent="garbage"), catalog, config)
        == "您好,我是客服助手,客服平台。"
    )
    # 未配置 direct_reply 的业务意图 → 空(走正常智能体流程)
    assert resolve_direct_reply(Decision(intent="order_query"), catalog, config) == ""
    # 目录外意图 → 空
    assert resolve_direct_reply(Decision(intent="ghost"), catalog, config) == ""


def test_resolve_direct_reply_without_config_returns_raw_template():
    catalog = {"garbage": {"direct_reply": "您好,我是{agent_name}。"}}
    assert resolve_direct_reply(Decision(intent="garbage"), catalog) == "您好,我是{agent_name}。"


# ---------------------------------------------------------------------------
# 向量检索
# ---------------------------------------------------------------------------
# 检索用的内联假数据(约定 #10:mock 数据只进单元测试)
_TEST_DOCS = [
    Document(
        doc_id="t-refund",
        title="退款政策",
        content="发货前可随时退款,款项原路返回,1-3 个工作日到账。",
        tags=["退款", "退货"],
    ),
    Document(
        doc_id="t-shipping",
        title="物流与配送",
        content="48 小时内发货,全国大部分地区 2-4 天送达,可在订单页查看物流进度。",
        tags=["物流", "发货", "快递"],
    ),
]


def test_vector_store_search_refund_policy():
    store = MemoryVectorStore()
    store.add(_TEST_DOCS)
    results = store.search("怎么退款,多久到账", top_k=3)
    assert results
    assert results[0]["title"] == "退款政策"


def test_vector_store_search_shipping():
    store = MemoryVectorStore()
    store.add(_TEST_DOCS)
    results = store.search("发货了吗,快递到哪了")
    assert results
    assert any(r["title"] == "物流与配送" for r in results)


def test_vector_store_empty_query():
    store = MemoryVectorStore()
    store.add(_TEST_DOCS)
    assert store.search("") == []


# ---------------------------------------------------------------------------
# 知识库语料加载(来自 business.yaml,代码零样例数据)
# ---------------------------------------------------------------------------
def test_load_faq_documents_from_config():
    """knowledge.documents 正常解析为 Document 列表。"""

    config = BusinessConfig(
        {
            "knowledge": {
                "documents": [
                    {"doc_id": "d1", "title": "标题", "content": "正文", "tags": ["标签"]}
                ]
            }
        }
    )
    docs = load_faq_documents(config)
    assert [d.doc_id for d in docs] == ["d1"]
    assert docs[0].tags == ["标签"]


def test_load_faq_documents_rejects_malformed_entry():
    """缺 doc_id/title/content 的条目启动即报错(不做静默跳过)。"""

    config = BusinessConfig({"knowledge": {"documents": [{"doc_id": "d1", "title": "标题"}]}})
    with pytest.raises(ConfigurationError):
        load_faq_documents(config)


def test_load_faq_documents_empty_when_unconfigured():
    """未配置 documents = 空知识库(检索无结果,模型按「未查到」处理)。"""

    assert load_faq_documents(BusinessConfig({})) == []


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


# ---------------------------------------------------------------------------
# 绑定用户(侧栏输入用户ID → 绑定上下文 → 仅可查该用户订单)
# ---------------------------------------------------------------------------
class _StubMcp:
    """记录 MCP 调用参数的测试替身(仅单元测试使用)。"""

    def __init__(self, payload: dict | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.payload = payload or {
            "orders": [
                {
                    "order_id": "20260901008",
                    "title": "测试商品",
                    "status": "已发货",
                    "amount": 99.5,
                }
            ]
        }

    def require_mapping(self, name: str) -> str:
        return f"upstream.{name}"

    async def call_tool(self, mapping: str, arguments: dict) -> dict:
        self.calls.append((mapping, arguments))
        return self.payload


def _tool_ctx(sessions: SessionStateManager, mcp: _StubMcp, params, thread_id: str = "thr_bind"):
    return ToolContext(
        thread_id=thread_id,
        message="",
        params=params,
        raw_params={},
        sessions=sessions,
        gateway=None,  # type: ignore[arg-type]
        rag=None,  # type: ignore[arg-type]
        mcp=mcp,  # type: ignore[arg-type]
        config=None,  # type: ignore[arg-type]
    )


def test_session_bind_and_unbind():
    sessions = SessionStateManager()
    sessions.bind_customer("thr_x", "42")
    assert sessions.customer_id("thr_x") == "42"
    sessions.unbind_customer("thr_x")
    assert sessions.customer_id("thr_x") is None
    # 未绑定时解绑不报错
    sessions.unbind_customer("thr_never_bound")
    assert sessions.customer_id("thr_never_bound") is None


def test_session_default_binding_inherited_by_new_threads():
    """右侧「绑定用户」是工作台级绑定:新开的对话线程自动继承,无需重复绑定。"""

    sessions = SessionStateManager()
    sessions.bind_default_customer("42")
    # 全新线程(未显式绑定)也能拿到身份
    assert sessions.customer_id("thr_new_1") == "42"
    assert sessions.customer_id("thr_new_2") == "42"
    # 线程显式绑定(请求头/对话识别)优先于默认绑定
    sessions.bind_customer("thr_new_1", "99")
    assert sessions.customer_id("thr_new_1") == "99"
    assert sessions.thread_customer_id("thr_new_2") is None  # 默认绑定不算显式绑定
    # 解绑默认后新线程回到未绑定
    sessions.unbind_default_customer()
    assert sessions.customer_id("thr_new_2") is None


def test_session_default_binding_can_be_disabled():
    """多租户部署关掉继承(inherit_default_binding=false):匿名会话绝不inherit客服身份。"""

    sessions = SessionStateManager(inherit_default_binding=False)
    sessions.bind_default_customer("42")
    # 关掉继承:没有显式绑定的线程一律视为未识别客户
    assert sessions.customer_id("thr_anonymous") is None
    # 但显式绑定(请求头 X-Customer-Id 等)不受影响
    sessions.bind_customer("thr_logged_in", "99")
    assert sessions.customer_id("thr_logged_in") == "99"


def test_customer_profile_to_dict_orders_total_fallback():
    """orders_total 缺失时回退为列表长度(兼容未下发 Total 的上游)。"""

    from app.core.customer import CustomerProfile, Order

    profile = CustomerProfile(
        customer_id="1",
        name="测试客户",
        orders=[Order(id="A", title="t", status="s", amount=1.0, created_at="", tracking="")],
    )
    data = profile.to_dict()
    assert data["orders_total"] == 1
    profile.orders_total = 200
    assert profile.to_dict()["orders_total"] == 200


def test_get_order_scoped_to_bound_customer():
    """绑定后订单号查询同时带 customer_id,交由上游校验归属。"""

    sessions = SessionStateManager()
    sessions.bind_customer("thr_bind", "42")
    mcp = _StubMcp()
    import asyncio

    asyncio.run(
        _get_order(_tool_ctx(sessions, mcp, GetOrderParams(order_id="20260901008")))
    )
    mapping, arguments = mcp.calls[0]
    assert mapping == "upstream.get_order"
    assert arguments == {"order_id": "20260901008", "customer_id": "42"}


def test_get_order_without_binding_sends_order_id_only():
    sessions = SessionStateManager()
    mcp = _StubMcp()
    import asyncio

    asyncio.run(
        _get_order(_tool_ctx(sessions, mcp, GetOrderParams(order_id="20260901008")))
    )
    assert mcp.calls[0][1] == {"order_id": "20260901008"}


def test_get_order_requires_identity_or_order_id():
    sessions = SessionStateManager()
    mcp = _StubMcp()
    import asyncio

    with pytest.raises(ToolError):
        asyncio.run(_get_order(_tool_ctx(sessions, mcp, GetOrderParams())))


def test_get_order_result_carries_goods_for_model():
    """订单里的商品明细必须随结果一起回灌给模型(回答「买了什么商品」不能只有订单摘要)。"""

    sessions = SessionStateManager()
    mcp = _StubMcp(
        payload={
            "orders": [
                {
                    "orderId": "S260920765242-1",
                    "title": "龙腾八方",
                    "status": "退货完成",
                    "amount": 46.3,
                    "createdAt": "2026-09-20T15:26:36+08:00",
                    "goods": [
                        {
                            "goodsId": "447311",
                            "name": "ltbf高端卤猪肘",
                            "spec": "2.5KG*2盒",
                            "qty": 2,
                            "unitPrice": 36.3,
                            "price": 72.6,
                        }
                    ],
                }
            ]
        }
    )
    import asyncio

    result = asyncio.run(
        _get_order(_tool_ctx(sessions, mcp, GetOrderParams(order_id="S260920765242-1")))
    )
    goods = result["data"]["order"]["goods"]
    assert len(goods) == 1
    assert goods[0]["name"] == "ltbf高端卤猪肘"
    assert goods[0]["spec"] == "2.5KG*2盒"
    assert goods[0]["qty"] == 2
    assert goods[0]["unit_price"] == 36.3
    assert goods[0]["price"] == 72.6


def test_normalize_order_goods_key_variants_and_missing():
    """商品明细字段容忍上游差异;未下发时为空列表,不编造。"""

    from app.core.customer import normalize_order, normalize_order_goods

    order = normalize_order(
        {
            "order_no": "S1",
            "items": [
                {"goods_name": "酱肘", "quantity": 3, "price": 90.0},
                {"product_name": "礼盒", "num": 1.0, "unit_price": 12.5},
            ],
        }
    )
    assert [g.name for g in order.goods] == ["酱肘", "礼盒"]
    assert order.goods[0].qty == 3
    assert order.goods[0].price == 90.0
    assert order.goods[1].unit_price == 12.5
    assert normalize_order({"order_id": "X1", "status": "待发货"}).goods == []
    # 无名称的明细行不进入事实(避免噪声);有价格无单价时单价留 0
    assert normalize_order_goods({"qty": 1, "price": 5}).name == ""


def test_search_customer_scoped_to_bound_customer():
    """绑定后检索客户只能用绑定用户ID,防止越过绑定身份查别人。"""

    sessions = SessionStateManager()
    sessions.bind_customer("thr_bind", "42")
    mcp = _StubMcp(payload={"customers": [{"customer_id": "42", "name": "测试客户"}]})
    import asyncio

    asyncio.run(
        _search_customer(
            _tool_ctx(sessions, mcp, SearchCustomerParams(phone="13800000000"))
        )
    )
    assert mcp.calls[0][1] == {"customer_id": "42"}


def test_search_customer_uses_params_when_unbound():
    sessions = SessionStateManager()
    mcp = _StubMcp(payload={"customers": [{"customer_id": "42", "name": "测试客户"}]})
    import asyncio

    asyncio.run(
        _search_customer(
            _tool_ctx(sessions, mcp, SearchCustomerParams(phone="13800000000"))
        )
    )
    assert mcp.calls[0][1] == {"phone": "13800000000"}


# ---------------------------------------------------------------------------
# 模型直连 MCP:function calling SSE 解析 / tool_calls 累积
# ---------------------------------------------------------------------------
def test_sse_line_parses_content_and_tool_call_delta():
    content, tool_deltas = McpToolAgent._parse_sse_line(
        'data: {"choices":[{"delta":{"content":"您好"}}]}'
    )
    assert content == "您好"
    assert tool_deltas == []

    content, tool_deltas = McpToolAgent._parse_sse_line(
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1",'
        '"type":"function","function":{"name":"get_order","arguments":"{}"}}]}}]}'
    )
    assert content == ""
    assert tool_deltas[0]["id"] == "call_1"
    assert tool_deltas[0]["function"]["name"] == "get_order"


def test_sse_line_ignores_noise():
    assert McpToolAgent._parse_sse_line("data: [DONE]") == ("", [])
    assert McpToolAgent._parse_sse_line("") == ("", [])
    assert McpToolAgent._parse_sse_line(": ping") == ("", [])
    assert McpToolAgent._parse_sse_line("data: not-json") == ("", [])
    assert McpToolAgent._parse_sse_line('data: {"choices":[]}') == ("", [])
    assert McpToolAgent._parse_sse_line('data: {"choices":[{"delta":{}}]}') == ("", [])


def test_tool_call_accumulator_builds_from_fragments():
    """name/arguments 按 SSE 碎片逐段拼接,结束后拼成完整调用。"""

    accumulator = _ToolCallAccumulator()
    accumulator.feed(
        [{"index": 0, "id": "call_1", "function": {"name": "get_", "arguments": ""}}]
    )
    accumulator.feed([{"index": 0, "function": {"name": "order", "arguments": '{"order_'}}])
    accumulator.feed([{"index": 0, "function": {"arguments": 'id": "S123"}'}}])
    assert accumulator.has_calls()
    (call,) = accumulator.build()
    assert call.id == "call_1"
    assert call.name == "get_order"
    assert call.arguments == {"order_id": "S123"}
    # 回灌 messages 的形态
    openai_call = call.to_openai()
    assert openai_call["type"] == "function"
    assert openai_call["function"]["name"] == "get_order"


def test_tool_call_accumulator_multiple_calls_in_one_round():
    accumulator = _ToolCallAccumulator()
    accumulator.feed(
        [{"index": 0, "id": "c0", "function": {"name": "get_order", "arguments": "{}"}}]
    )
    accumulator.feed(
        [
            {
                "index": 1,
                "id": "c1",
                "function": {"name": "search_customer", "arguments": '{"phone":"13800000000"}'},
            }
        ]
    )
    calls = accumulator.build()
    assert [c.name for c in calls] == ["get_order", "search_customer"]
    assert calls[1].arguments == {"phone": "13800000000"}


def test_tool_call_accumulator_malformed_arguments_do_not_crash():
    """arguments 不是合法 JSON 时不抛错,原样交给 pydantic 校验层报错。"""

    accumulator = _ToolCallAccumulator()
    accumulator.feed(
        [{"index": 0, "id": "c0", "function": {"name": "get_order", "arguments": '{"order_id":'}}]
    )
    (call,) = accumulator.build()
    assert "_raw_arguments" in call.arguments


def test_mcp_tool_agent_max_rounds_from_config():
    """单条消息的工具调用轮数上限可配(防无限循环)。"""

    agent = McpToolAgent(
        BusinessConfig(
            {
                "models": {
                    "chat": {
                        "provider": "openai-compatible",
                        "model": "m",
                        "base_url": "http://127.0.0.1:9/v1",
                        "api_key": "unit-test",
                    }
                },
                "agent": {"max_tool_rounds": 2},
            }
        )
    )
    assert agent.max_rounds == 2


def test_summarize_arguments_truncates_for_sidebar():
    assert _summarize_arguments({"order_id": "S123"}) == '{"order_id": "S123"}'
    long = _summarize_arguments({"k": "x" * 200})
    assert long.endswith("…")
    assert len(long) <= 81


# ---------------------------------------------------------------------------
# 模型工具调用的代码侧执行(身份剥离 / 越权防护)
# ---------------------------------------------------------------------------
def _exec_server(
    sessions: SessionStateManager,
    mcp: _StubMcp,
    mcp_extra: dict | None = None,
    config_extra: dict | None = None,
):
    """构造一个不打真实连接的 server(仅用于工具执行路径单测)。"""

    from app.attachment_store import LocalAttachmentStore
    from app.core.conversation import ConversationMemory
    from app.memory_store import MemoryStore
    from app.server import CustomerServiceServer
    from app.tools import build_default_registry

    mcp_section: dict = {
        "servers": [
            {"name": "upstream", "transport": "http", "url": "http://127.0.0.1:9/mcp"}
        ],
        "tool_mapping": {
            "get_order": "upstream.get_order",
            "search_customer": "upstream.search_customer",
        },
    }
    if mcp_extra:
        mcp_section.update(mcp_extra)
    config_data: dict = {
        "tools": ["get_order", "search_customer"],
        "mcp": mcp_section,
    }
    if config_extra:
        config_data.update(config_extra)
    config = BusinessConfig(config_data)
    registry = build_default_registry(
        config=config,
        mcp=mcp,  # type: ignore[arg-type]
        gateway=None,  # type: ignore[arg-type]
        rag=RagService(config),
        sessions=sessions,
    )
    store = MemoryStore()
    return CustomerServiceServer(
        store=store,
        attachment_store=LocalAttachmentStore(store),
        config=config,
        mcp=mcp,  # type: ignore[arg-type]
        jev=None,  # type: ignore[arg-type]
        agent=None,  # type: ignore[arg-type]
        tools=registry,
        sessions=sessions,
        memory=ConversationMemory(),
        gateway=None,  # type: ignore[arg-type]
        policy=Policy(config),
    )


def test_execute_tool_call_strips_model_supplied_customer_id():
    """模型伪造的 customer_id 必须被剥离:身份只来自会话绑定(防越权)。"""

    import asyncio

    from app.ai.mcp_agent import ToolCallRequest

    sessions = SessionStateManager()
    mcp = _StubMcp()
    server = _exec_server(sessions, mcp)

    # 未绑定 + 模型塞了别人的 customer_id → 剥离,上游只收到 order_id
    content, changed = asyncio.run(
        server._execute_tool_call(
            "thr_x",
            ToolCallRequest(id="c0", name="get_order", arguments={"order_id": "S1", "customer_id": "999"}),
            "查订单",
        )
    )
    assert mcp.calls[0][1] == {"order_id": "S1"}
    assert changed is False
    assert "订单" in content

    # 已绑定客户 → 由 handler 注入绑定身份(模型参数里没有也不影响)
    sessions.bind_customer("thr_x", "42")
    asyncio.run(
        server._execute_tool_call(
            "thr_x",
            ToolCallRequest(id="c1", name="get_order", arguments={"order_id": "S1"}),
            "查订单",
        )
    )
    assert mcp.calls[1][1] == {"order_id": "S1", "customer_id": "42"}


def test_execute_tool_call_unknown_tool_becomes_tool_message():
    """模型调了不存在的工具:错误作为 tool 消息回灌(会话不崩,模型可自救)。"""

    import asyncio

    from app.ai.mcp_agent import ToolCallRequest

    sessions = SessionStateManager()
    server = _exec_server(sessions, _StubMcp())
    content, changed = asyncio.run(
        server._execute_tool_call(
            "thr_x",
            ToolCallRequest(id="c0", name="hack_the_planet", arguments={}),
            "查订单",
        )
    )
    assert "工具调用失败" in content
    assert changed is False


# ---------------------------------------------------------------------------
# 画像预加载开关(business.yaml: mcp.load_profile_on_message,默认关)
# ---------------------------------------------------------------------------
def test_profile_switch_defaults_to_off():
    """画像预加载开关默认关闭;business.yaml 显式打开时才在消息链路预加载。"""

    sessions = SessionStateManager()
    assert _exec_server(sessions, _StubMcp()).profile_on_message is False

    sessions = SessionStateManager()
    server = _exec_server(
        sessions, _StubMcp(), mcp_extra={"load_profile_on_message": True}
    )
    assert server.profile_on_message is True


def test_system_prompt_without_profile_hints_bound_identity():
    """画像关闭/拉取失败时:已绑定身份不再让模型向客户索要身份。"""

    server = _exec_server(SessionStateManager(), _StubMcp())
    decision = Decision(
        intent="order_query", action="get_order", confidence=1.0, emotion="normal"
    )

    bound = server._build_system_prompt(decision, None, None, identity_bound=True)
    assert "身份已由系统绑定" in bound
    assert "尚未识别客户身份" not in bound

    anon = server._build_system_prompt(decision, None, None, identity_bound=False)
    assert "尚未识别客户身份" in anon


# ---------------------------------------------------------------------------
# Jev 客户端的 direct 开关(models.decision.direct:绕过系统代理)
# ---------------------------------------------------------------------------
def _jev_config(direct: str | None = None) -> BusinessConfig:
    decision: dict = {
        "provider": "openjev",
        "model": "jev-latest",
        "base_url": "http://127.0.0.1:9/v1",
        "api_key": "unit-test",
    }
    if direct is not None:
        decision["direct"] = direct
    return BusinessConfig(
        {
            "models": {"decision": decision},
            "intents": {
                "order_query": {"action": "get_order", "agent": "order_agent", "description": "查订单"}
            },
            "agents": {
                "default": "chat_agent",
                "chat_agent": {"title": "通用", "description": "接待"},
                "order_agent": {"title": "订单", "description": "查订单"},
            },
            "jev": {"confidence": {"high": 0.85, "low": 0.55}, "retry": {"attempts": 1}},
        }
    )


def test_jev_direct_flag_controls_trust_env():
    """models.decision.direct=true 时 Jev 客户端 trust_env=False(绕过注册表代理);
    未配置或 "false" 字符串时不动 trust_env(防 bool("false")=True 的坑)。"""

    import asyncio
    from unittest.mock import patch

    import app.ai.jev as jev_module

    class _StubResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"answers": {"intent": {"choice": "order_query", "confidence": 1.0}}}

    class _StubAsyncClient:
        last_kwargs: dict = {}

        def __init__(self, **kwargs) -> None:
            _StubAsyncClient.last_kwargs = dict(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def post(self, url, json=None, headers=None):
            return _StubResponse()

    with patch.object(jev_module.httpx, "AsyncClient", _StubAsyncClient):
        # ${JEV_DIRECT:-true} 插值出来是字符串 "true"
        asyncio.run(_jev_engine(_jev_config("true"))._request_answers("查订单", "", ""))
        assert _StubAsyncClient.last_kwargs.get("trust_env") is False

        # 未配置:保持默认(走注册表代理)
        asyncio.run(_jev_engine(_jev_config())._request_answers("查订单", "", ""))
        assert "trust_env" not in _StubAsyncClient.last_kwargs

        # 字符串 "false" 必须能关掉
        asyncio.run(_jev_engine(_jev_config("false"))._request_answers("查订单", "", ""))
        assert "trust_env" not in _StubAsyncClient.last_kwargs
def test_done_event_reuses_stream_item_id():
    """回归:ThreadItemDoneEvent 若另生成 id,客户端会把一条回复渲染成两条。"""

    from datetime import datetime

    from chatkit.types import ThreadMetadata

    server = _exec_server(SessionStateManager(), _StubMcp())
    thread = ThreadMetadata(id="thr_dup", created_at=datetime.now())

    streamed_id = server.store.generate_item_id("message", thread, {})
    done_item = server._assistant_message(thread, "订单已查到", {}, item_id=streamed_id)
    assert done_item.id == streamed_id

    # 不传 item_id(垃圾意图直回等无前置流的场景)仍自动生成且不碰撞
    direct_item = server._assistant_message(thread, "您好", {})
    assert direct_item.id != streamed_id
