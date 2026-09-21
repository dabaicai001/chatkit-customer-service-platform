"""core/routing.py:通用路由器——整个系统的核心。

Jev 只负责「决定做什么、调度哪个 AGENT」,这里负责真正把请求分发出去:

    decision = jev.analyze(message)
    action = decision.action
    agent = decision.agent(或按意图映射/默认兜底)

    search_customer     -> MCP 客户检索
    get_order           -> MCP 订单查询
    query_knowledge     -> rag.search()                   公司知识库(RAG)
    transfer_to_human   -> 人工坐席队列                    转人工
    none                -> 直接对话

业务结果与 AGENT 人设最终交给生成模型产出话术(见 ai/qwen.py)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..ai.agents import AgentRegistry, AgentSpec
from ..ai.jev import Decision
from ..ai.rag import RagService
from ..core.customer import CustomerGateway
from ..core.policy import Policy
from ..core.session import PendingAction, SessionStateManager
from ..tools import ToolRegistry

HUMAN_ACTION = "transfer_to_human"
KNOWLEDGE_ACTION = "query_knowledge"
SEARCH_CUSTOMER_ACTION = "search_customer"


def resolve_agent(
    decision: Decision,
    intent_catalog: Dict[str, Any],
    registry: AgentRegistry,
) -> AgentSpec:
    """AGENT 调度决策:Jev 指定 > 意图映射 > 默认 AGENT。"""

    if decision.agent:
        spec = registry.get(decision.agent)
        if spec is not None:
            return spec
    intent_spec = intent_catalog.get(decision.intent)
    if isinstance(intent_spec, dict):
        mapped = str(intent_spec.get("agent", "") or "").strip()
        if mapped:
            spec = registry.get(mapped)
            if spec is not None:
                return spec
    return registry.resolve(registry.default_name)


@dataclass
class RouteResult:
    """一次路由的产出,server 据此决定向 ChatKit 推什么。"""

    decision: Decision
    action: str = "none"
    confidence_level: str = "medium"  # high / medium / low
    agent: Optional[AgentSpec] = None  # 本次调度的专职 AGENT
    tool_result: Optional[Dict[str, Any]] = None
    tool_error: Optional[str] = None
    passages: List[Dict[str, Any]] = field(default_factory=list)
    awaiting_confirmation: Optional[PendingAction] = None
    handoff: Optional[Dict[str, Any]] = None
    direct_answer: bool = False
    state_changed: bool = False
    direct_reply: str = ""  # 固化直回文案(垃圾/无关信息;非空则不过生成模型)
    note: str = ""


class Router:
    """意图 → AGENT / MCP 工具 / 知识库 / 人工 的通用分发器。"""

    def __init__(
        self,
        tools: ToolRegistry,
        rag: RagService,
        gateway: CustomerGateway,
        sessions: SessionStateManager,
        policy: Policy,
        agents: AgentRegistry,
        intent_catalog: Optional[Dict[str, Any]] = None,
        config: Optional[Any] = None,
    ) -> None:
        self.tools = tools
        self.rag = rag
        self.gateway = gateway
        self.sessions = sessions
        self.policy = policy
        self.agents = agents
        self.intent_catalog = intent_catalog or {}
        self._config = config

    def _resolve_direct_reply(self, decision: Decision) -> str:
        """意图配置了 direct_reply 时返回固化文案(垃圾/无关信息直通,不过生成模型)。"""

        spec = self.intent_catalog.get(decision.intent)
        if not isinstance(spec, dict):
            return ""
        template = str(spec.get("direct_reply", "") or "").strip()
        if not template:
            return ""
        if self._config is not None:
            try:
                return template.format(
                    agent_name=self._config.agent_name,
                    company_name=self._config.company_name,
                )
            except (KeyError, IndexError):
                return template
        return template

    # ------------------------------------------------------------- 主入口
    async def route(
        self,
        decision: Decision,
        thread_id: str,
        message: str,
    ) -> RouteResult:
        # 0) AGENT 调度(Jev 指定 > 意图映射 > 默认)
        agent = resolve_agent(decision, self.intent_catalog, self.agents)
        action = decision.action or "none"

        # 1) 情绪路由覆盖(如 angry → 转人工),优先于业务动作
        emotion_override = self.policy.emotion_route(decision.emotion)
        if emotion_override and action != emotion_override:
            action = emotion_override
            decision.need_human = action == HUMAN_ACTION
            decision.reason = (
                f"{decision.reason}(情绪 {decision.emotion} 触发路由覆盖 → {action})"
            ).strip()

        # 2) 置信度门控:低于 low 不执行业务工具,改走兜底动作
        bands = self.policy.confidence_bands()
        level = bands.level(decision.confidence)
        if (
            level == "low"
            and action not in ("none", KNOWLEDGE_ACTION, HUMAN_ACTION)
        ):
            action = self.policy.fallback_action()
            decision.need_rag = action == KNOWLEDGE_ACTION
            decision.need_human = action == HUMAN_ACTION
            decision.reason = (
                f"{decision.reason}(置信度 {decision.confidence:.2f} 过低,兜底 → {action})"
            ).strip()

        # 3) 只放行配置里启用的工具
        if action not in ("none", HUMAN_ACTION) and not self.tools.is_enabled(action):
            action = KNOWLEDGE_ACTION if decision.need_rag else "none"

        result = RouteResult(
            decision=decision, action=action, confidence_level=level, agent=agent
        )

        # ------------------------------------------------ 转人工
        if action == HUMAN_ACTION:
            if self.policy.human_transfer_enabled:
                try:
                    result.handoff = await self._transfer_human(thread_id, decision, message)
                except Exception as exc:  # 转人工失败不当成会话崩溃,交生成模型说明
                    result.tool_error = f"转人工失败:{exc}"
                    result.direct_answer = True
                    self.sessions.log(thread_id, f"转人工失败:{exc}", kind="error")
            else:
                result.direct_answer = True
                result.note = "人工坐席当前未开启,由 AI 继续服务。"
            return result

        # ------------------------------------------------ 垃圾/无关信息直通
        # Jev 已判定为垃圾或与业务无关:直接友好回复,不调工具、不过生成模型
        direct_reply = self._resolve_direct_reply(decision)
        if direct_reply:
            result.direct_answer = True
            result.direct_reply = direct_reply
            result.note = "垃圾/无关信息,直接友好回复(不过生成模型)。"
            self.sessions.log(thread_id, f"垃圾信息直回:{decision.reason or decision.intent}", kind="info")
            return result

        # ------------------------------------------------ 知识库(动作指定 / Jev 要求 / AGENT 要求)
        if action == KNOWLEDGE_ACTION or decision.need_rag or agent.needs_rag:
            query = str(decision.slots.get("query") or message)
            result.passages = await self.rag.search(query)
            if action == KNOWLEDGE_ACTION:
                return result

        # ------------------------------------------------ 直接回答
        if action == "none":
            result.direct_answer = True
            return result

        # ------------------------------------------------ 业务工具(带确认门槛)
        params = dict(decision.slots or {})
        if self.policy.requires_confirmation(action):
            pending = self.sessions.get_pending_action(thread_id)
            if pending is None or pending.action != action:
                prompt = self.policy.confirmation_prompt(action, params)
                result.awaiting_confirmation = self.sessions.set_pending_action(
                    thread_id, action, params, prompt
                )
                result.note = f"「{action}」需要用户确认,已挂起。"
                return result
            params = {**pending.params, **params}

        await self._execute_tool(result, thread_id, action, params, message)
        return result

    # ------------------------------------------------------------- 确认后执行
    async def execute_confirmed(
        self,
        thread_id: str,
        action_id: str,
        decision: Optional[Decision] = None,
        message: str = "",
    ) -> RouteResult:
        """用户在确认卡片上点了「确认」后,执行此前挂起的动作。"""

        pending = self.sessions.get_pending_action(thread_id, action_id)
        if pending is None:
            result = RouteResult(
                decision=decision or Decision(action="none", reason="无待确认动作"),
                action="none",
            )
            result.tool_error = "确认已过期,请重新描述你的需求。"
            return result

        self.sessions.clear_pending_action(thread_id, action_id)
        result = RouteResult(
            decision=decision or Decision(action=pending.action, reason="用户确认后执行"),
            action=pending.action,
        )
        await self._execute_tool(result, thread_id, pending.action, dict(pending.params), message)
        return result

    async def cancel_pending(self, thread_id: str, action_id: str) -> RouteResult:
        self.sessions.clear_pending_action(thread_id, action_id)
        result = RouteResult(decision=Decision(action="none", reason="用户取消"), action="none")
        result.direct_answer = True
        result.note = "已取消,上述动作不会执行。"
        return result

    # ------------------------------------------------------------- 内部
    async def _execute_tool(
        self,
        result: RouteResult,
        thread_id: str,
        action: str,
        params: Dict[str, Any],
        message: str,
    ) -> None:
        try:
            tool_result = await self.tools.execute(
                action, thread_id=thread_id, params=params, message=message
            )
        except Exception as exc:  # 工具异常转为对话内可见的错误,不中断会话
            result.tool_error = str(exc)
            result.note = f"工具 {action} 执行失败。"
            self.sessions.log(thread_id, f"工具 {action} 执行失败:{exc}", kind="error")
            return
        result.tool_result = tool_result
        result.state_changed = bool(tool_result.get("state_changed", False))

        # 改变了业务状态 → 让画像缓存失效,侧栏下次拉取最新数据
        if result.state_changed:
            customer_id = self.sessions.customer_id(thread_id)
            if customer_id:
                self.gateway.invalidate(customer_id)

    async def _transfer_human(
        self,
        thread_id: str,
        decision: Decision,
        message: str,
    ) -> Dict[str, Any]:
        tool_result = await self.tools.execute(
            HUMAN_ACTION,
            thread_id=thread_id,
            params={"reason": decision.reason or message, "intent": decision.intent},
            message=message,
        )
        data = tool_result.get("data") or {}
        return {
            "queue_position": data.get("queue_position", 1),
            "estimated_wait": data.get("estimated_wait", ""),
            "reason": decision.reason or message,
        }
