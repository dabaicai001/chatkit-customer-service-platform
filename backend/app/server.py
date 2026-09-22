"""server.py:Customer Gateway——ChatKit 与客服平台的接线层。

一次用户消息的完整链路(模型直连 MCP,无中间抽取层):

    用户消息
       → Jev 决策(意图/情绪/置信度/AGENT/**推荐调用的函数**,一次 SystemOne 调用)
       → 智能体循环(chat 槽位,OpenAI 兼容 function calling):
           模型输出 tool_calls(函数名+参数)
             → 代码侧经 ToolRegistry 执行(pydantic 校验 + 剥离模型给的
               customer_id + MCP 数据面;模型碰不到 MCP 连接)
             → 工具结果以 tool 消息回灌
             → 直到模型产出最终话术(流式)
       → ChatKit 流式事件(文本增量 + 客户画像/AGENT 调度/流程耗时副作用)

业务数据一律经 MCP 请求上游;本文件只做编排,不含业务规则。
变更类/需确认的工具不开放给模型(见 _model_tools),由人工流程办理。
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional
from uuid import uuid4

from chatkit.agents import ThreadItemConverter
from chatkit.server import ChatKitServer
from chatkit.types import (
    Action,
    AssistantMessageContent,
    AssistantMessageContentPartAdded,
    AssistantMessageContentPartDone,
    AssistantMessageContentPartTextDelta,
    AssistantMessageItem,
    AudioInput,
    ClientEffectEvent,
    ErrorEvent,
    HiddenContextItem,
    ThreadItemAddedEvent,
    ThreadItemDoneEvent,
    ThreadItemUpdatedEvent,
    ThreadMetadata,
    ThreadStreamEvent,
    TranscriptionResult,
    UserMessageItem,
)
from fastapi import HTTPException
from openai import OpenAI

from .ai.jev import DecisionError, JevDecisionEngine
from .ai.mcp_agent import (
    MAX_TOOL_RESULT_CHARS,
    McpToolAgent,
    TextDelta,
    ToolCallRequest,
)
from .ai.qwen import GenerationError, generate_title
from .ai.rag import RagService
from .attachment_store import LocalAttachmentStore
from .config import BusinessConfig
from .core.conversation import ConversationMemory
from .core.customer import CustomerGateway, CustomerProfile
from .core.policy import Policy
from .core.routing import resolve_agent, resolve_direct_reply
from .core.session import SessionStateManager
from .integrations.mcp import McpClientManager, McpError
from .memory_store import MemoryStore
from .thread_item_converter import CustomerSupportThreadItemConverter
from .tools import ToolError, ToolRegistry, build_default_registry

logger = logging.getLogger(__name__)

PROFILE_EFFECT_NAME = "customer_profile/update"
AGENT_DISPATCH_EFFECT_NAME = "agent_dispatch/update"
PIPELINE_TRACE_EFFECT_NAME = "pipeline_trace/update"

#: 模型给出的参数里,身份字段一律剥离(身份只来自会话绑定,防越权)
_MODEL_IDENTITY_FIELDS = frozenset({"customer_id"})

#: 情绪 → 沟通姿态(只给方向,措辞由模型自由发挥;不做回复模板)
_POSTURE_BY_EMOTION: Dict[str, str] = {
    "angry": "客户情绪激动:先用一句话安抚,再给结论或转人工方案。",
    "frustrated": "客户不耐烦:先一句共情,再直接给结论。",
    "anxious": "客户着急:先给结论,再补必要细节。",
    "happy": "",
}

_SYSTEM_TEMPLATE = """{persona_block}

【客户资料】
{profile_block}

【本次决策(Jev)】
{decision_block}

【硬性规则】
- 需要客户资料/订单/商品/知识库等信息时,必须调用工具获取,禁止凭记忆或猜测回答;
- 工具结果是 JSON:result 是给人看的结论,data 是结构化明细;其中编号/状态/金额/时间等事实必须原样传达,不得改写或编造;
- 客户身份由系统自动管理,不要向客户索要客户 ID;客户用手机号或姓名表明身份时,调用 search_customer 核实;
- 客户明确要求转人工时,调用 transfer_to_human;
- 每次回复 2-4 句话,语气自然亲切,除非客户要求详细说明。
"""

_DEFAULT_PERSONA = (
    "你是{agent_name},{company_name}的在线客服。你的任务是基于工具查证到的结果"
    "用自然、亲切、简洁的中文回答客户。"
)


@dataclass(slots=True)
class TraceStep:
    """流程中的一步(用于侧栏「调用流程与耗时」展示)。"""

    step: str
    label: str
    ms: int  # 本步骤耗时(毫秒,非累计)
    detail: str = ""


class PipelineTrace:
    """单次用户消息的流程计时器(进程内,随响应流推送)。"""

    def __init__(self) -> None:
        self.steps: List[TraceStep] = []
        self._t0 = time.monotonic()
        self._last_ms = 0

    def add(self, step: str, label: str, detail: str = "") -> TraceStep:
        elapsed = round((time.monotonic() - self._t0) * 1000)
        entry = TraceStep(
            step=step, label=label, ms=elapsed - self._last_ms, detail=detail
        )
        self._last_ms = elapsed
        self.steps.append(entry)
        return entry

    @property
    def total_ms(self) -> int:
        return round((time.monotonic() - self._t0) * 1000)

    def to_dict(self, status: str) -> Dict[str, Any]:
        return {
            "status": status,
            "total_ms": self.total_ms,
            "steps": [
                {
                    "step": s.step,
                    "label": s.label,
                    "ms": s.ms,
                    "detail": s.detail,
                }
                for s in self.steps
            ],
        }


def _selectable_action_catalog(
    tools: ToolRegistry, policy: Policy, config: BusinessConfig
) -> Dict[str, str]:
    """Jev 可推荐的函数候选:已启用 ∩ 非变更类 ∩ 免确认。

    变更类/需确认的动作(退款/取消/建工单…)不允许被 Jev 直选,更不允许
    模型直调——它们走人工流程(当前上游未暴露,意图路由到转人工)。
    """

    if not bool(config.get("jev.action_recommendation", True)):
        return {}
    catalog: Dict[str, str] = {}
    for name in tools.enabled_names():
        tool = tools.get(name)
        if tool.mutating or policy.requires_confirmation(name):
            continue
        catalog[name] = tool.description
    return catalog


def _summarize_arguments(arguments: Dict[str, Any]) -> str:
    """工具调用参数的侧栏摘要(截断,避免刷屏)。"""

    text = json.dumps(arguments or {}, ensure_ascii=False, default=str)
    return text[:80] + ("…" if len(text) > 80 else "")


def create_chatkit_server(config: BusinessConfig) -> "CustomerServiceServer":
    """组合根:装配全部依赖,返回可用的 ChatKit 服务实例。

    数据流:用户消息 → Jev 决策(意图/情绪/AGENT/推荐函数)
          → 模型 function calling 循环(代码经 MCP 执行工具,结果回灌)
          → 流式话术 → ChatKit 事件(文本增量 + 画像/AGENT 调度/流程耗时副作用)
    """

    config.validate()

    mcp = McpClientManager(config)
    sessions = SessionStateManager(
        inherit_default_binding=bool(
            config.get("session.inherit_default_binding", True)
        )
    )
    memory = ConversationMemory()
    rag = RagService(config)
    gateway = CustomerGateway(
        mcp, cache_seconds=float(config.get("mcp.profile_cache_seconds", 5) or 0)
    )
    tools = build_default_registry(
        config=config, mcp=mcp, gateway=gateway, rag=rag, sessions=sessions
    )
    policy = Policy(config)
    jev = JevDecisionEngine(
        config, selectable_actions=_selectable_action_catalog(tools, policy, config)
    )
    agents = jev.agents
    agents.validate(tools.enabled_names())
    agent = McpToolAgent(config)

    store = MemoryStore()
    attachment_store = LocalAttachmentStore(store)
    server = CustomerServiceServer(
        store=store,
        attachment_store=attachment_store,
        config=config,
        mcp=mcp,
        jev=jev,
        agent=agent,
        tools=tools,
        sessions=sessions,
        memory=memory,
        gateway=gateway,
        policy=policy,
    )
    logger.info(
        "客服平台装配完成:公司=%s 客服=%s 已启用工具=%s 已注册 AGENT=%s "
        "Jev 可推荐函数=%s 模型工具调用上限=%d 轮",
        config.company_name,
        config.agent_name,
        ",".join(tools.enabled_names()),
        ",".join(agents.names()),
        ",".join(_selectable_action_catalog(tools, policy, config)) or "(关闭)",
        agent.max_rounds,
    )
    return server


class CustomerServiceServer(ChatKitServer[dict[str, Any]]):
    """通用客服 ChatKit 服务器。"""

    def __init__(
        self,
        *,
        store: MemoryStore,
        attachment_store: LocalAttachmentStore,
        config: BusinessConfig,
        mcp: McpClientManager,
        jev: JevDecisionEngine,
        agent: McpToolAgent,
        tools: ToolRegistry,
        sessions: SessionStateManager,
        memory: ConversationMemory,
        gateway: CustomerGateway,
        policy: Policy,
    ) -> None:
        super().__init__(store, attachment_store=attachment_store)
        self.store = store
        self._local_attachment_store = attachment_store
        self.config = config
        self.mcp = mcp
        self.jev = jev
        self.agent = agent
        self.tools = tools
        self.sessions = sessions
        self.memory = memory
        self.gateway = gateway
        self.policy = policy
        # 画像预加载开关(默认关):消息链路不主动拉画像,省掉首条消息的
        # get_customer + list_orders 两次串行 MCP 往返;客户数据改由
        # function calling 按需获取。侧栏面板仍独立拉取(不占对话关键路径)。
        self.profile_on_message = bool(
            config.get("mcp.load_profile_on_message", False)
        )
        self.thread_item_converter: ThreadItemConverter = CustomerSupportThreadItemConverter(
            attachment_store=attachment_store
        )
        self._openai_client: Optional[OpenAI] = None

    # ------------------------------------------------------------- 生命周期
    async def startup(self) -> None:
        """连接 MCP 数据面(失败即抛错,快速失败)。"""

        await self.mcp.start()

    async def shutdown(self) -> None:
        await self.mcp.stop()

    @property
    def attachment_uploader(self) -> LocalAttachmentStore:
        return self._local_attachment_store

    # ------------------------------------------------------------- 响应主链路
    async def respond(
        self,
        thread: ThreadMetadata,
        user_message: UserMessageItem | None,
        context: dict[str, Any],
    ) -> AsyncIterator[ThreadStreamEvent]:
        title_task: asyncio.Task[None] | None = None
        if user_message is not None and thread.title is None:
            title_task = asyncio.create_task(
                self._maybe_update_thread_title(thread, user_message)
            )

        try:
            async for event in self._respond_inner(thread, user_message, context):
                yield event
        except (DecisionError, GenerationError, McpError) as exc:
            logger.exception("客服响应失败")
            yield ErrorEvent(message=str(exc))
        except Exception as exc:  # 未预期错误:显式暴露,不静默吞掉
            logger.exception("客服响应出现未预期错误")
            yield ErrorEvent(message=f"服务内部错误:{exc}")
        finally:
            if title_task is not None:
                try:
                    await title_task
                except Exception as exc:
                    logger.warning("会话标题生成失败(不影响回复):%s", exc)

    async def _respond_inner(
        self,
        thread: ThreadMetadata,
        user_message: UserMessageItem | None,
        context: dict[str, Any],
    ) -> AsyncIterator[ThreadStreamEvent]:
        message_text = self._user_message_text(user_message)
        trace = PipelineTrace()

        # 0) 身份绑定(已登录场景:从请求头取身份信号,无需用户自述)
        await self._bind_identity_from_request(thread.id, context)
        trace.add("identity", "身份绑定")

        # 1) 客户画像(经 MCP;开关关闭或未识别身份时为 None)
        if self.profile_on_message:
            profile = await self._load_profile(thread.id)
            trace.add(
                "profile",
                "客户画像(MCP)",
                self._profile_trace_detail(thread.id),
            )
        else:
            # 关闭预加载:省掉 get_customer + list_orders 两次串行 MCP 往返,
            # 客户数据改由智能体循环里的 function calling 按需获取
            # (get_order 等工具凭会话绑定身份查询,见 tools/order.py)
            profile = None
            trace.add(
                "profile",
                "客户画像(关闭)",
                "预加载已关闭,客户数据由 function calling 按需获取",
            )
        customer_before = self.sessions.customer_id(thread.id)

        # 2) Jev 决策(意图/情绪/AGENT/推荐调用的函数,一次 SystemOne 调用)
        decision = await self.jev.analyze(
            message_text,
            history=self.memory.transcript(thread.id),
            customer_summary=profile.summary_text() if profile else "",
        )
        self.memory.remember_decision(thread.id, decision.to_dict())
        trace.add(
            "jev",
            "Jev 决策",
            f"{decision.intent} · 置信度 {decision.confidence:.2f} · "
            f"推荐函数 {decision.action}({decision.action_source}) · "
            f"请求 {decision.elapsed_ms}ms · 尝试 {decision.attempts} 次",
        )

        # 2.1) 垃圾/无关信息直通:Jev 已判定,固化文案回复,不调工具、不过生成模型
        direct_reply = resolve_direct_reply(
            decision, self.config.intent_catalog, self.config
        )
        if direct_reply:
            yield ThreadItemDoneEvent(
                item=self._assistant_message(thread, direct_reply, context)
            )
            self.memory.add_turn(thread.id, "user", message_text)
            self.memory.add_turn(thread.id, "assistant", direct_reply)
            await self._record_hidden_context(thread, decision, [], context)
            yield self._pipeline_trace_effect(trace)
            return

        # 3) AGENT 调度(Jev 指定 > 意图映射 > 默认)+ 侧栏事件
        agent = resolve_agent(decision, self.config.intent_catalog, self.jev.agents)
        yield self._agent_dispatch_effect(decision, agent, trace)

        # 4) 组装对话(系统提示 + 历史 + 用户消息)
        messages = self._build_messages(thread.id, message_text, decision, agent, profile)
        state_changed = False
        tool_names: List[str] = []
        tools_arg = self._model_tools()

        # 4.1) 情绪路由覆盖(angry/frustrated → 强制转人工):
        #      先由代码执行 transfer_to_human,结果回灌后本轮不再开放工具
        override = self.policy.emotion_route(decision.emotion)
        if override == "transfer_to_human":
            call = ToolCallRequest(
                id=f"call_{uuid4().hex[:10]}",
                name="transfer_to_human",
                arguments={"reason": decision.reason, "intent": decision.intent},
            )
            started = time.monotonic()
            content, changed = await self._execute_tool_call(thread.id, call, message_text)
            trace.add(
                "tool",
                "模型调用 transfer_to_human",
                f"{round((time.monotonic() - started) * 1000)}ms · 情绪触发",
            )
            state_changed = state_changed or changed
            tool_names.append(call.name)
            messages.append(
                {"role": "assistant", "content": "", "tool_calls": [call.to_openai()]}
            )
            messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
            tools_arg = None

        # 5) 智能体循环:模型流式吐字 + function calling 调 MCP
        item_id = self.store.generate_item_id("message", thread, context)
        yield ThreadItemAddedEvent(
            item=AssistantMessageItem(
                thread_id=thread.id,
                id=item_id,
                created_at=datetime.now(),
                content=[AssistantMessageContent(text="")],
            )
        )
        yield ThreadItemUpdatedEvent(
            item_id=item_id,
            update=AssistantMessageContentPartAdded(
                content_index=0, content=AssistantMessageContent(text="")
            ),
        )

        chat_started = time.monotonic()
        first_token_ms: Optional[int] = None
        parts: List[str] = []
        rounds = 0
        while rounds < self.agent.max_rounds:
            rounds += 1
            tool_calls: List[ToolCallRequest] = []
            async for event in self.agent.stream_round(messages, tools_arg):
                if isinstance(event, TextDelta):
                    if not event.text:
                        continue
                    if first_token_ms is None:
                        first_token_ms = round((time.monotonic() - chat_started) * 1000)
                    parts.append(event.text)
                    yield ThreadItemUpdatedEvent(
                        item_id=item_id,
                        update=AssistantMessageContentPartTextDelta(
                            content_index=0, delta=event.text
                        ),
                    )
                else:
                    tool_calls = event.calls
            if not tool_calls:
                break
            # 模型要求调用工具:代码侧执行(校验/身份/MCP),结果回灌后继续循环
            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [call.to_openai() for call in tool_calls],
                }
            )
            for call in tool_calls:
                started = time.monotonic()
                content, changed = await self._execute_tool_call(
                    thread.id, call, message_text
                )
                ms = round((time.monotonic() - started) * 1000)
                state_changed = state_changed or changed
                tool_names.append(call.name)
                trace.add(
                    "tool",
                    f"模型调用 {call.name}",
                    f"{ms}ms · {_summarize_arguments(call.arguments)}",
                )
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": content}
                )
        chat_ms = round((time.monotonic() - chat_started) * 1000)
        trace.add(
            "chat",
            "话术生成",
            f"首 token {first_token_ms if first_token_ms is not None else 0}ms · "
            f"共 {chat_ms}ms · 工具调用 {len(tool_names)} 次",
        )

        full_text = "".join(parts).strip()
        if not full_text:
            full_text = "抱歉,我暂时没有生成有效的回复,请稍后再试。"

        yield ThreadItemUpdatedEvent(
            item_id=item_id,
            update=AssistantMessageContentPartDone(
                content_index=0, content=AssistantMessageContent(text=full_text)
            ),
        )
        yield ThreadItemDoneEvent(
            item=self._assistant_message(
                thread, full_text, context, item_id=item_id
            )
        )

        # 6) 会话记忆 + 隐藏上下文(决策与工具调用沉淀,供后续轮次 grounding)
        self.memory.add_turn(thread.id, "user", message_text)
        self.memory.add_turn(thread.id, "assistant", full_text)
        await self._record_hidden_context(thread, decision, tool_names, context)

        # 6.1) 完整流程与耗时推送给侧栏
        yield self._pipeline_trace_effect(trace)

        # 7) 业务状态变化 / 身份新绑定 → 经 MCP 刷新画像并推送给侧栏
        customer_after = self.sessions.customer_id(thread.id)
        if state_changed or customer_after != customer_before:
            async for event in self._stream_profile_effect(
                thread.id, context, refresh=True
            ):
                yield event

    # ------------------------------------------------------------- 组件 Action
    async def action(
        self,
        thread: ThreadMetadata,
        action: Action[str, Any],
        sender: Any,
        context: dict[str, Any],
    ) -> AsyncIterator[ThreadStreamEvent]:
        """组件交互已随「模型直连 MCP」下线(流程不再产出 Widget),保留接口兼容。"""

        logger.info("收到组件 action:%s(当前流程不产出 Widget,已忽略)。", action.type)
        return
        yield  # pragma: no cover - 仅保持异步生成器形态

    # ------------------------------------------------------------- 语音听写
    async def transcribe(
        self, audio_input: AudioInput, context: dict[str, Any]
    ) -> TranscriptionResult:
        ext = {
            "audio/webm": "webm",
            "audio/mp4": "m4a",
            "audio/ogg": "ogg",
        }.get(audio_input.media_type)
        if not ext:
            raise HTTPException(status_code=400, detail="不支持的音频格式")

        if self._openai_client is None:
            self._openai_client = OpenAI()
        audio_file = io.BytesIO(audio_input.data)
        audio_file.name = f"audio.{ext}"
        transcription = self._openai_client.audio.transcriptions.create(
            model="gpt-4o-transcribe", file=audio_file
        )
        return TranscriptionResult(text=transcription.text)

    # ------------------------------------------------------------- 对外:绑定用户
    async def bind_customer(self, thread_id: str, customer_id: str) -> CustomerProfile:
        """侧栏「绑定用户」入口:先经 MCP 校验用户存在,再写入会话绑定。

        同时设置工作台级默认绑定——新开的对话线程自动继承该身份,
        客服不需要每开一个会话就重新绑定同一个客户。
        绑定后该会话的客户/订单查询以该用户为上下文(工具执行时由代码
        注入 customer_id,上游校验归属,只能查该用户的订单)。
        用户不存在时抛 ValueError——不静默降级。
        """

        customer_id = (customer_id or "").strip()
        if not customer_id:
            raise ValueError("customer_id 不能为空。")
        try:
            profile = await self.gateway.load_profile(customer_id)
        except McpError as exc:
            raise ValueError(f"用户不存在或资料加载失败:{exc}") from exc
        self.sessions.bind_default_customer(customer_id)
        self.sessions.bind_customer(thread_id, customer_id)
        self.sessions.log(thread_id, f"客服已手动绑定用户:{customer_id}", kind="system")
        logger.info("客服已绑定用户(thread=%s, customer=%s, 新会话将继承)。", thread_id, customer_id)
        return profile

    def unbind_customer(self, thread_id: str) -> None:
        """侧栏「解绑」入口:清除工作台默认绑定与当前会话的客户绑定。"""

        self.sessions.unbind_default_customer()
        self.sessions.unbind_customer(thread_id)
        self.sessions.log(thread_id, "客服已手动解绑用户", kind="system")

    async def _bind_identity_from_request(
        self, thread_id: str, context: dict[str, Any]
    ) -> None:
        """从请求头提取身份信号并绑定会话(真实环境的已登录场景)。

        身份信号来自渠道/登录态,不来自 MCP;MCP 只负责拿到 ID 之后的
        客户资料/订单/工单数据。支持的请求头:

        - ``X-Customer-Id``   :网关/前端已完成鉴权,直接传客户 ID(生产推荐,
          应由网关注入而非信任客户端直传);
        - ``X-Customer-Phone``:传手机号,经 MCP search_customer 解析出客户;
        - ``X-Customer-Token``:传令牌,经 MCP resolve_token 换取客户 ID
          (需在 business.yaml 的 mcp.tool_mapping 中配置该映射才启用)。

        匿名访客不使用请求头,仍由对话内的 search_customer 路径识别。
        """

        # 仅本线程的显式绑定才阻止请求头识别:工作台默认绑定(侧栏)不拦截,
        # 让已登录终端用户的身份头可以按线程覆盖默认绑定。
        if self.sessions.thread_customer_id(thread_id):
            return
        request = (context or {}).get("request")
        headers = getattr(request, "headers", None)
        if headers is None:
            return

        customer_id = (headers.get("x-customer-id") or "").strip()
        if customer_id:
            self.sessions.bind_customer(thread_id, customer_id)
            return

        phone = (headers.get("x-customer-phone") or "").strip()
        if phone:
            try:
                result = await self.tools.execute(
                    "search_customer", thread_id=thread_id, params={"phone": phone}
                )
            except Exception as exc:
                logger.warning("按手机号识别客户失败:%s", exc)
                return
            if result.get("found"):
                logger.info("已按手机号识别客户(thread=%s)。", thread_id)
            return

        token = (headers.get("x-customer-token") or "").strip()
        mapping = self.mcp.mapping_for("resolve_token") if token else None
        if mapping:
            try:
                payload = await self.mcp.call_tool(mapping, {"token": token})
                data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
                resolved = str(data.get("customer_id") or "").strip()
            except Exception as exc:
                logger.warning("令牌解析客户身份失败:%s", exc)
                return
            if resolved:
                self.sessions.bind_customer(thread_id, resolved)

    async def _load_profile(self, thread_id: str) -> Optional[CustomerProfile]:
        customer_id = self.sessions.customer_id(thread_id)
        if not customer_id:
            return None
        try:
            return await self.gateway.load_profile(customer_id)
        except McpError as exc:
            # 画像加载失败不阻断对话(工具仍可能按需取数),仅记录
            logger.warning("客户画像加载失败(thread=%s):%s", thread_id, exc)
            return None

    async def _stream_profile_effect(
        self, thread_id: str, context: dict[str, Any], *, refresh: bool = False
    ) -> AsyncIterator[ThreadStreamEvent]:
        customer_id = self.sessions.customer_id(thread_id)
        if not customer_id:
            return
        try:
            profile = await self.gateway.load_profile(customer_id, refresh=refresh)
        except McpError as exc:
            logger.warning("画像刷新失败:%s", exc)
            return
        yield ClientEffectEvent(
            name=PROFILE_EFFECT_NAME, data={"profile": profile.to_dict()}
        )

    # ------------------------------------------------------------- 内部:组装
    def _build_messages(
        self,
        thread_id: str,
        message: str,
        decision: Any,
        agent: Any,
        profile: Optional[CustomerProfile],
    ) -> List[Dict[str, Any]]:
        """组装 function calling 的 messages(系统提示 + 历史 + 用户消息)。"""

        messages: List[Dict[str, Any]] = [
            {
                "role": "system",
                "content": self._build_system_prompt(
                    decision,
                    agent,
                    profile,
                    identity_bound=bool(self.sessions.customer_id(thread_id)),
                ),
            }
        ]
        for turn in self.memory.recent(thread_id, limit=6):
            messages.append(
                {
                    "role": "user" if turn.role == "user" else "assistant",
                    "content": turn.text,
                }
            )
        messages.append({"role": "user", "content": message})
        return messages

    def _build_system_prompt(
        self,
        decision: Any,
        agent: Any,
        profile: Optional[CustomerProfile],
        *,
        identity_bound: bool = False,
    ) -> str:
        """系统提示:AGENT 人设 + 客户资料 + Jev 决策(含推荐函数)+ 硬性规则。"""

        persona = self._render_agent_instructions(agent)
        if not persona:
            persona = _DEFAULT_PERSONA.format(
                agent_name=self.config.agent_name,
                company_name=self.config.company_name,
            )
        if agent is not None and agent.title:
            persona = f"【当前 AGENT:{agent.title}】\n{persona}"

        level = self.policy.confidence_level(decision.confidence)
        decision_lines = [
            f"意图={decision.intent} 置信度={decision.confidence:.2f}({level}) "
            f"情绪={decision.emotion} 判断依据={decision.reason or '-'}",
            f"推荐调用函数={decision.action}(来源:{decision.action_source})",
        ]
        if level != "high":
            decision_lines.append(
                "推荐置信度不足,请以客户实际诉求为准,自行判断是否调用工具。"
            )
        posture = _POSTURE_BY_EMOTION.get((decision.emotion or "").strip().lower(), "")
        if posture:
            decision_lines.append(posture)

        return _SYSTEM_TEMPLATE.format(
            persona_block=persona,
            profile_block=(
                profile.summary_text()
                if profile
                else (
                    # 画像关闭/拉取失败但身份已绑定:别让模型再向客户索要身份,
                    # 身份参数由工具执行时自动注入(server._execute_tool_call)
                    "客户身份已由系统绑定;需要客户资料/订单时直接调用工具,"
                    "身份参数由系统自动注入,不要向客户索要身份信息。"
                    if identity_bound
                    else "尚未识别客户身份(客户自述身份时调用 search_customer 核实)。"
                )
            ),
            decision_block="\n".join(decision_lines),
        )

    def _render_agent_instructions(self, agent: Any) -> str:
        """渲染 AGENT 人设提示词(占位符:agent_name/company_name)。"""

        if agent is None or not agent.instructions:
            return ""
        try:
            return agent.instructions.format(
                agent_name=self.config.agent_name,
                company_name=self.config.company_name,
            ).strip()
        except (KeyError, IndexError):
            return agent.instructions.strip()

    def _model_tools(self) -> List[Dict[str, Any]]:
        """开放给模型的工具(OpenAI function calling 格式)。

        只放行:已启用 ∩ 非变更类 ∩ 免确认。变更动作(退款/取消/建工单)
        不允许模型直调——由人工流程办理(上游未暴露时意图路由到转人工)。
        """

        schemas: List[Dict[str, Any]] = []
        for name in self.tools.enabled_names():
            tool = self.tools.get(name)
            if tool.mutating or self.policy.requires_confirmation(name):
                continue
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters.model_json_schema(),
                    },
                }
            )
        return schemas

    async def _execute_tool_call(
        self, thread_id: str, call: ToolCallRequest, message: str
    ) -> tuple[str, bool]:
        """执行模型请求的一次工具调用。

        返回 (tool 消息内容, 是否变更业务状态):
        1. 剥离模型给出的 customer_id(身份只来自会话绑定,防越权);
        2. 经 ToolRegistry:pydantic 强校验 + MCP 数据面;
        3. 校验/上游失败不当成会话崩溃——错误作为 tool 消息回灌,
           模型可基于错误信息追问客户或换一条路。
        """

        params = {
            key: value
            for key, value in (call.arguments or {}).items()
            if key not in _MODEL_IDENTITY_FIELDS
        }
        try:
            result = await self.tools.execute(
                call.name, thread_id=thread_id, params=params, message=message
            )
        except (ToolError, McpError) as exc:
            logger.warning("模型调用的工具 %s 执行失败:%s", call.name, exc)
            return f"工具调用失败:{exc}", False
        payload = {"result": result.get("result", ""), "data": result.get("data", {})}
        content = json.dumps(payload, ensure_ascii=False, default=str)
        return content[:MAX_TOOL_RESULT_CHARS], bool(result.get("state_changed", False))

    # ------------------------------------------------------------- 内部:副作用
    def _profile_trace_detail(self, thread_id: str) -> str:
        """画像步骤的明细(客户 + 缓存命中情况)。"""

        customer_id = self.sessions.thread_customer_id(thread_id) or self.sessions.customer_id(thread_id)
        if not customer_id:
            return "未识别客户(跳过)"
        cache_hit = self.gateway.last_cache_hit
        suffix = "缓存命中" if cache_hit else ("实时查询" if cache_hit is False else "")
        return f"{customer_id}" + (f" · {suffix}" if suffix else "")

    def _agent_dispatch_effect(
        self, decision: Any, agent: Any, trace: PipelineTrace
    ) -> ClientEffectEvent:
        """AGENT 调度事件:前端侧栏展示 Jev 选择了哪个 AGENT(附当前流程耗时)。"""

        return ClientEffectEvent(
            name=AGENT_DISPATCH_EFFECT_NAME,
            data={
                "dispatch": {
                    "agent": agent.name if agent else "",
                    "agent_title": agent.title if agent else "",
                    "intent": decision.intent,
                    "action": decision.action,
                    "confidence": round(decision.confidence, 2),
                    "confidence_level": self.policy.confidence_level(decision.confidence),
                    "emotion": decision.emotion,
                    "reason": decision.reason or "",
                },
                "trace": trace.to_dict("partial"),
            },
        )

    def _pipeline_trace_effect(self, trace: PipelineTrace) -> ClientEffectEvent:
        """完整流程与耗时事件:侧栏「调用流程与耗时」面板渲染。"""

        return ClientEffectEvent(
            name=PIPELINE_TRACE_EFFECT_NAME,
            data={"trace": trace.to_dict("done")},
        )

    async def _record_hidden_context(
        self,
        thread: ThreadMetadata,
        decision: Any,
        tool_names: List[str],
        context: dict[str, Any],
    ) -> None:
        """把决策与工具调用写入隐藏上下文,后续轮次可引用。"""

        items = [
            f"<JEVDECISION intent={decision.intent} action={decision.action} "
            f"confidence={decision.confidence:.2f} emotion={decision.emotion} "
            f"reason={decision.reason or '-'} />"
        ]
        if tool_names:
            items.append("<TOOLCALLS>" + ",".join(tool_names) + "</TOOLCALLS>")
        for content in items:
            await self.store.add_thread_item(
                thread.id,
                HiddenContextItem(
                    id=self.store.generate_item_id("message", thread, context),
                    thread_id=thread.id,
                    created_at=datetime.now(),
                    content=content,
                ),
                context,
            )

    # ------------------------------------------------------------- 内部:标题
    async def _maybe_update_thread_title(
        self, thread: ThreadMetadata, user_message: UserMessageItem | None
    ) -> None:
        if user_message is None or thread.title is not None:
            return
        text = self._user_message_text(user_message)
        if not text:
            return
        try:
            title = await generate_title(self.config, text)
        except GenerationError as exc:
            logger.warning("标题生成失败,跳过:%s", exc)
            return
        thread.title = title

    # ------------------------------------------------------------- 工具方法
    @staticmethod
    def _user_message_text(user_message: UserMessageItem | None) -> str:
        parts = []
        for content in getattr(user_message, "content", None) or []:
            text = getattr(content, "text", None)
            if text:
                parts.append(str(text))
        return "\n".join(parts).strip()

    def _assistant_message(
        self,
        thread: ThreadMetadata,
        text: str,
        context: dict[str, Any],
        *,
        item_id: Optional[str] = None,
    ) -> AssistantMessageItem:
        # item_id 显式传入时必须复用:ThreadItemAdded + 增量事件 + ThreadItemDoneEvent
        # 必须是同一条目,否则客户端把 done 当成新条目,渲染出两条重复回复
        return AssistantMessageItem(
            thread_id=thread.id,
            id=item_id or self.store.generate_item_id("message", thread, context),
            created_at=datetime.now(),
            content=[AssistantMessageContent(text=text)],
        )
