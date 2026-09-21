"""server.py:Customer Gateway——ChatKit 与客服平台的接线层。

一次用户消息的完整链路:

    用户消息
      → Jev 决策(意图/情绪/置信度)
      → Router 分发(MCP 工具 / RAG 知识库 / 人工 / 直接回答)
      → Qwen 流式生成客服话术
      → ChatKit 流式事件(文本增量 + 组件 + 客户画像副作用)

所有业务数据经 MCP 请求上游;本文件只做编排,不含业务规则。
"""

from __future__ import annotations

import asyncio
import io
import logging
from datetime import datetime
from typing import Any, AsyncIterator, Callable, Optional

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
    ThreadItemUpdated,
    ThreadItemUpdatedEvent,
    ThreadMetadata,
    ThreadStreamEvent,
    TranscriptionResult,
    UserMessageItem,
    WidgetItem,
    WidgetRootUpdated,
)
from fastapi import HTTPException
from openai import OpenAI

from .ai.jev import DecisionError, JevDecisionEngine
from .ai.qwen import ComposeContext, GenerationError, QwenComposer
from .ai.rag import RagService
from .attachment_store import LocalAttachmentStore
from .config import BusinessConfig
from .core.conversation import ConversationMemory
from .core.customer import CustomerGateway, CustomerProfile
from .core.routing import Router, RouteResult
from .core.session import SessionStateManager
from .integrations.mcp import McpClientManager, McpError
from .memory_store import MemoryStore
from .thread_item_converter import CustomerSupportThreadItemConverter
from .tools import ToolRegistry, build_default_registry
from .widgets import (
    CANCEL_ACTION_TYPE,
    CONFIRM_ACTION_TYPE,
    SELECT_ACTION_TYPE,
    build_confirm_widget,
)

logger = logging.getLogger(__name__)

PROFILE_EFFECT_NAME = "customer_profile/update"
MAX_HISTORY_ITEMS = 20


def create_chatkit_server(config: BusinessConfig) -> "CustomerServiceServer":
    """组合根:装配全部依赖,返回可用的 ChatKit 服务实例。"""

    config.validate()

    mcp = McpClientManager(config)
    sessions = SessionStateManager()
    memory = ConversationMemory()
    rag = RagService(config)
    gateway = CustomerGateway(
        mcp, cache_seconds=float(config.get("mcp.profile_cache_seconds", 5) or 0)
    )
    jev = JevDecisionEngine(config)
    qwen = QwenComposer(config)
    tools = build_default_registry(
        config=config, mcp=mcp, gateway=gateway, rag=rag, sessions=sessions
    )

    from .core.policy import Policy

    policy = Policy(config)
    router = Router(tools=tools, rag=rag, gateway=gateway, sessions=sessions, policy=policy)

    store = MemoryStore()
    attachment_store = LocalAttachmentStore(store)
    server = CustomerServiceServer(
        store=store,
        attachment_store=attachment_store,
        config=config,
        mcp=mcp,
        jev=jev,
        qwen=qwen,
        router=router,
        tools=tools,
        sessions=sessions,
        memory=memory,
        gateway=gateway,
    )
    logger.info(
        "客服平台装配完成:公司=%s 客服=%s 已启用工具=%s",
        config.company_name,
        config.agent_name,
        ",".join(tools.enabled_names()),
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
        qwen: QwenComposer,
        router: Router,
        tools: ToolRegistry,
        sessions: SessionStateManager,
        memory: ConversationMemory,
        gateway: CustomerGateway,
    ) -> None:
        super().__init__(store, attachment_store=attachment_store)
        self.store = store
        self._local_attachment_store = attachment_store
        self.config = config
        self.mcp = mcp
        self.jev = jev
        self.qwen = qwen
        self.router = router
        self.tools = tools
        self.sessions = sessions
        self.memory = memory
        self.gateway = gateway
        self.thread_item_converter: ThreadItemConverter = CustomerSupportThreadItemConverter(
            attachment_store=attachment_store
        )
        self._openai_client: Optional[OpenAI] = None
        self._action_handlers: dict[str, Callable] = {
            CONFIRM_ACTION_TYPE: self._handle_confirm_action,
            CANCEL_ACTION_TYPE: self._handle_cancel_action,
            SELECT_ACTION_TYPE: self._handle_select_option_action,
        }

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

        # 1) 客户画像(经 MCP;未识别身份时为 None)
        profile = await self._load_profile(thread.id)

        # 2) Jev 决策
        decision = await self.jev.analyze(
            message_text,
            history=self.memory.transcript(thread.id),
            customer_summary=profile.summary_text() if profile else "",
        )
        self.memory.remember_decision(thread.id, decision.to_dict())

        # 3) 路由分发
        route = await self.router.route(decision, thread.id, message_text)

        # 4) 需要确认 → 先弹确认卡片
        if route.awaiting_confirmation is not None:
            async for event in self._stream_confirmation(thread, route, context):
                yield event
            return

        # 5) Qwen 流式生成
        compose_context = await self._build_compose_context(thread.id, message_text, route, profile)
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

        chunks: list[str] = []
        async for delta in self.qwen.compose_stream(compose_context):
            if not delta:
                continue
            chunks.append(delta)
            yield ThreadItemUpdatedEvent(
                item_id=item_id,
                update=AssistantMessageContentPartTextDelta(content_index=0, delta=delta),
            )
        full_text = "".join(chunks).strip()
        if not full_text:
            full_text = "抱歉,我暂时没有生成有效的回复,请稍后再试。"

        yield ThreadItemUpdatedEvent(
            item_id=item_id,
            update=AssistantMessageContentPartDone(
                content_index=0, content=AssistantMessageContent(text=full_text)
            ),
        )
        yield ThreadItemDoneEvent(
            item=AssistantMessageItem(
                thread_id=thread.id,
                id=item_id,
                created_at=datetime.now(),
                content=[AssistantMessageContent(text=full_text)],
            )
        )

        # 6) 会话记忆 + 隐藏上下文(决策与工具结果沉淀,供后续轮次 grounding)
        self.memory.add_turn(thread.id, "user", message_text)
        self.memory.add_turn(thread.id, "assistant", full_text)
        await self._record_hidden_context(thread, decision, route, context)

        # 7) 业务状态变化 → 经 MCP 刷新画像并推送给侧栏
        if route.state_changed:
            async for event in self._stream_profile_effect(thread.id, context, refresh=True):
                yield event

    # ------------------------------------------------------------- 组件 Action
    async def action(
        self,
        thread: ThreadMetadata,
        action: Action[str, Any],
        sender: WidgetItem | None,
        context: dict[str, Any],
    ) -> AsyncIterator[ThreadStreamEvent]:
        handler = self._action_handlers.get(action.type)
        if handler is None:
            logger.warning("未注册的 action 类型:%s", action.type)
            return
        try:
            async for event in handler(thread, action, sender, context):
                yield event
        except (DecisionError, GenerationError, McpError) as exc:
            logger.exception("action %s 执行失败", action.type)
            yield ErrorEvent(message=str(exc))

    async def _handle_confirm_action(
        self,
        thread: ThreadMetadata,
        action: Action[str, Any],
        sender: WidgetItem | None,
        context: dict[str, Any],
    ) -> AsyncIterator[ThreadStreamEvent]:
        action_id = str((action.payload or {}).get("action_id", ""))
        route = await self.router.execute_confirmed(thread.id, action_id)

        # 锁定已消费的确认卡片
        if sender is not None:
            yield ThreadItemUpdated(
                item_id=sender.id,
                update=WidgetRootUpdated(widget=sender.widget),
            )

        async for event in self._stream_route_reply(thread, route, context, prefix="已确认:"):
            yield event

    async def _handle_cancel_action(
        self,
        thread: ThreadMetadata,
        action: Action[str, Any],
        sender: WidgetItem | None,
        context: dict[str, Any],
    ) -> AsyncIterator[ThreadStreamEvent]:
        action_id = str((action.payload or {}).get("action_id", ""))
        route = await self.router.cancel_pending(thread.id, action_id)
        if sender is not None:
            yield ThreadItemUpdated(
                item_id=sender.id,
                update=WidgetRootUpdated(widget=sender.widget),
            )
        async for event in self._stream_route_reply(thread, route, context):
            yield event

    async def _handle_select_option_action(
        self,
        thread: ThreadMetadata,
        action: Action[str, Any],
        sender: WidgetItem | None,
        context: dict[str, Any],
    ) -> AsyncIterator[ThreadStreamEvent]:
        """通用选择列表:把用户选择沉淀为隐藏上下文,并基于选择继续对话。"""

        payload = action.payload or {}
        selected = payload.get("option") or {}
        label = str(selected.get("title") or selected.get("id") or "")
        await self.store.add_thread_item(
            thread.id,
            HiddenContextItem(
                id=self.store.generate_item_id("message", thread, context),
                thread_id=thread.id,
                created_at=datetime.now(),
                content=f"<USER_SELECTION>{label}</USER_SELECTION>",
            ),
            context,
        )
        yield ThreadItemDoneEvent(
            item=self._assistant_message(
                thread,
                f"已记录你的选择:{label}。还需要我做什么吗?",
                context,
            )
        )

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

    # ------------------------------------------------------------- 内部:画像
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

    # ------------------------------------------------------------- 内部:确认卡片
    async def _stream_confirmation(
        self,
        thread: ThreadMetadata,
        route: RouteResult,
        context: dict[str, Any],
    ) -> AsyncIterator[ThreadStreamEvent]:
        pending = route.awaiting_confirmation
        assert pending is not None
        widget = build_confirm_widget(action_id=pending.action_id, prompt=pending.prompt)
        widget_id = self.store.generate_item_id("message", thread, context)
        yield ThreadItemDoneEvent(
            item=WidgetItem(
                thread_id=thread.id,
                id=widget_id,
                created_at=datetime.now(),
                widget=widget,
            )
        )
        yield ThreadItemDoneEvent(
            item=self._assistant_message(thread, pending.prompt, context)
        )
        self.sessions.log(thread.id, f"等待用户确认:{pending.action}", kind="warning")

    # ------------------------------------------------------------- 内部:Action 后回复
    async def _stream_route_reply(
        self,
        thread: ThreadMetadata,
        route: RouteResult,
        context: dict[str, Any],
        *,
        prefix: str = "",
    ) -> AsyncIterator[ThreadStreamEvent]:
        """确认/取消后,用同一套 Qwen 生成链路产出回复。"""

        profile = await self._load_profile(thread.id)
        compose_context = await self._build_compose_context(
            thread.id, "", route, profile, prefix=prefix
        )
        text = await self.qwen.compose(compose_context)
        yield ThreadItemDoneEvent(item=self._assistant_message(thread, text, context))
        await self._record_hidden_context(thread, route.decision, route, context)
        if route.state_changed:
            async for event in self._stream_profile_effect(thread.id, context, refresh=True):
                yield event

    # ------------------------------------------------------------- 内部:组装
    async def _build_compose_context(
        self,
        thread_id: str,
        message: str,
        route: RouteResult,
        profile: Optional[CustomerProfile],
        *,
        prefix: str = "",
    ) -> ComposeContext:
        decision = route.decision
        bands_level = route.confidence_level
        decision_summary = (
            f"意图={decision.intent} 动作={route.action} "
            f"置信度={decision.confidence:.2f}({bands_level}) 情绪={decision.emotion} "
            f"判断依据={decision.reason or '-'}"
        )
        if bands_level == "medium":
            decision_summary += "\n注意:置信度中等,回复需谨慎,不确定处建议客户补充信息或转人工。"
        if prefix:
            decision_summary = f"{prefix}\n{decision_summary}"
        return ComposeContext(
            message=message,
            decision_summary=decision_summary,
            profile=profile,
            tool_result=route.tool_result,
            tool_error=route.tool_error,
            passages=route.passages,
            awaiting_confirmation=(
                route.awaiting_confirmation.prompt if route.awaiting_confirmation else None
            ),
            handoff=route.handoff,
            history=self.memory.transcript(thread_id),
        )

    async def _record_hidden_context(
        self,
        thread: ThreadMetadata,
        decision: Any,
        route: RouteResult,
        context: dict[str, Any],
    ) -> None:
        """把决策与工具结果写入隐藏上下文,后续轮次可引用。"""

        items = [
            f"<JEVDECISION intent={decision.intent} action={route.action} "
            f"confidence={decision.confidence:.2f} emotion={decision.emotion} "
            f"reason={decision.reason or '-'} />"
        ]
        if route.tool_result:
            items.append(
                "<TOOLRESULT>" + str(route.tool_result.get("result", ""))[:500] + "</TOOLRESULT>"
            )
        if route.tool_error:
            items.append(f"<TOOLERROR>{route.tool_error}</TOOLERROR>")
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
            title = await self.qwen.generate_title(text)
        except GenerationError as exc:
            logger.warning("标题生成失败,跳过:%s", exc)
            return
        thread.title = title

    # ------------------------------------------------------------- 工具方法
    @staticmethod
    def _user_message_text(user_message: UserMessageItem | None) -> str:
        if user_message is None:
            return ""
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
    ) -> AssistantMessageItem:
        return AssistantMessageItem(
            thread_id=thread.id,
            id=self.store.generate_item_id("message", thread, context),
            created_at=datetime.now(),
            content=[AssistantMessageContent(text=text)],
        )
