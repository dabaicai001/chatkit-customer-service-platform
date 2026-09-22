"""ai/jev.py:Jev 决策引擎——系统的大脑/「判断器」。

职责(且只负责这个):分析用户消息,输出结构化决策,不生成回答。

    Decision {
      intent: "order_query",        # 意图
      confidence: 0.96,             # 置信度
      emotion: "normal",            # 情绪
      action: "get_order",          # 推荐调用的函数(工具名)
      action_source: "jev",         # jev=模型直选 / config=意图配置兜底
      agent: "order_agent",         # 调度哪个专职 AGENT
    }

Jev 的真实端点是 TypeSafe SystemOne(注意:它**不是** OpenAI Chat Completions,
没有 /chat/completions 路径,不能把 base_url 塞给会自动拼该路径的客户端):

    POST {JEV_BASE_URL 规范化后}/v1/systemone      # 如 https://api.typesafe.ai/v1/systemone
    Authorization: Bearer {JEV_API_KEY}
    {"state": <待判断内容>, "model": "jev-latest",
     "questions": {"intent": {"type": "choice", "criteria": {...}}, ...}}

choice 问题的回答自带 choice + confidence,比"让模型吐 JSON 再正则解析"更稳。
JEV_BASE_URL 三种写法均可(代码自动归一化):
    https://api.typesafe.ai / https://api.typesafe.ai/v1 / https://api.typesafe.ai/v1/systemone

未配置或调用失败时直接抛错——不做本地规则降级,避免"看起来在跑、其实在瞎猜"。

action(推荐函数)是第四个 choice 问题:候选 = 已启用的非变更类工具(工具描述由
调用方注入,见 server.py 的 _selectable_action_catalog)。Jev 直选非法或缺失时,
回退到 intents.<name>.action 的配置映射;business.yaml 关掉
jev.action_recommendation 则完全走配置映射。
工具调用的**参数**不在本层产生:由话术模型经 function calling 自然给出
(见 ai/mcp_agent.py),本层不做任何规则/正则参数抽取。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict

import httpx

from ..config import BusinessConfig
from ..core.policy import Policy
from .agents import AgentRegistry

logger = logging.getLogger(__name__)

EMOTIONS = ("normal", "happy", "anxious", "frustrated", "angry")

# 情绪 choice 问题的 criteria(键必须在 EMOTIONS 内)
_EMOTION_CRITERIA: Dict[str, str] = {
    "normal": "平静、中性地咨询或交流",
    "happy": "愉快、满意",
    "anxious": "焦虑、着急",
    "frustrated": "沮丧、不耐烦",
    "angry": "愤怒、威胁投诉",
}

# 依赖客户身份/画像的动作(need_customer_lookup 的来源)
_CUSTOMER_ACTIONS = frozenset(
    {"search_customer", "get_customer", "get_order", "list_orders", "get_product"}
)


class DecisionError(RuntimeError):
    """Jev 决策失败(配置缺失 / 模型调用失败 / 输出不可用)。"""


def _as_bool(value: Any) -> bool:
    """配置布尔:兼容原生 bool 与 ${VAR:-true} 插值出的字符串。"""

    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


@dataclass
class Decision:
    """Jev 的结构化决策。"""

    intent: str = "fallback"
    confidence: float = 0.0
    emotion: str = "normal"
    need_customer_lookup: bool = False
    need_rag: bool = False
    need_tool: bool = False
    need_human: bool = False
    action: str = "none"
    action_source: str = "config"  # jev=模型直选 / config=意图配置兜底
    agent: str = ""  # 调度哪个专职 AGENT(AI CHAT 出口内部)
    reason: str = ""
    source: str = "llm"
    #  Observability:一次 analyze 的端到端耗时与第几次尝试才成功(排查慢/重试)
    elapsed_ms: int = 0
    attempts: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class JevDecisionEngine:
    """意图/情绪/路由决策器(SystemOne 协议,配置缺失即报错)。"""

    def __init__(self, config: BusinessConfig, *, selectable_actions: Dict[str, str] | None = None) -> None:
        self._config = config
        self._cfg = config.require_model("decision")  # 缺失直接 ConfigurationError
        self._policy = Policy(config)
        self._intents = config.intent_catalog or {}
        self._agents = AgentRegistry(config)
        self._known_actions = self._collect_known_actions()
        # action 推荐的候选(启用工具名 -> 描述);关闭配置或未注入时走意图映射兜底
        self._action_candidates: Dict[str, str] = (
            dict(selectable_actions or {})
            if bool(config.get("jev.action_recommendation", True))
            else {}
        )
        logger.info(
            "Jev 决策引擎就绪(provider=%s, model=%s, endpoint=%s, "
            "置信度分档 low=%.2f/high=%.2f, 可调度 AGENT=%s, 可推荐函数=%s)。",
            self._cfg["provider"],
            self._cfg["model"],
            self._systemone_endpoint(),
            self._policy.confidence_bands().low,
            self._policy.confidence_bands().high,
            "/".join(self._agents.names()),
            "/".join(self._action_candidates) or "(关闭,走意图配置映射)",
        )

    @property
    def agents(self) -> AgentRegistry:
        return self._agents

    def _collect_known_actions(self) -> set[str]:
        actions = {
            str(spec.get("action"))
            for spec in self._intents.values()
            if isinstance(spec, dict) and spec.get("action")
        }
        actions.update({"none", "transfer_to_human", "query_knowledge"})
        return actions

    # ------------------------------------------------------------- 对外
    async def analyze(
        self,
        message: str,
        history: str = "",
        customer_summary: str = "",
    ) -> Decision:
        message = (message or "").strip()
        if not message:
            raise DecisionError("用户消息为空,无法决策。")

        last_error: Exception | None = None
        attempts = self._policy.retry_attempts()
        backoff = self._policy.retry_backoff_seconds()
        started = time.monotonic()
        for attempt in range(1, attempts + 1):
            try:
                answers = await self._request_answers(message, history, customer_summary)
                decision = self._parse_decision(answers, message)
                decision.elapsed_ms = round((time.monotonic() - started) * 1000)
                decision.attempts = attempt
                logger.info(
                    "Jev 决策:intent=%s action=%s agent=%s confidence=%.2f emotion=%s(%s) "
                    "[耗时 %dms,第 %d/%d 次尝试]",
                    decision.intent,
                    decision.action,
                    decision.agent or "(未指定,路由兜底)",
                    decision.confidence,
                    decision.emotion,
                    decision.reason,
                    decision.elapsed_ms,
                    attempt,
                    attempts,
                )
                return decision
            except DecisionError:
                raise  # 输出不可用属于确定性问题,不重试
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Jev 第 %d/%d 次调用失败:%r", attempt, attempts, exc
                )
                if attempt < attempts:
                    await asyncio.sleep(backoff * attempt)
        raise DecisionError(f"Jev 决策失败(已重试 {attempts} 次):{last_error}")

    # ------------------------------------------------------------- SystemOne 调用
    def _systemone_endpoint(self) -> str:
        """把 JEV_BASE_URL 归一化为 /v1/systemone(绝不拼 /chat/completions)。"""

        base = str(self._cfg.get("base_url") or "").strip().rstrip("/")
        if not base:
            raise DecisionError(
                "Jev 的 base_url 未配置(JEV_BASE_URL):应为 TypeSafe 地址,"
                "例如 https://api.typesafe.ai(代码自动拼接 /v1/systemone)。"
            )
        if base.endswith("/systemone"):  # .../v1/systemone 或 .../systemone,直接用
            return base
        if base.endswith("/v1"):
            return f"{base}/systemone"
        return f"{base}/v1/systemone"

    def _build_questions(self) -> Dict[str, Dict[str, Any]]:
        """意图 / 情绪 / AGENT 三个 choice 问题(criteria 全部来自配置)。"""

        intent_criteria: Dict[str, str] = {}
        for name, spec in self._intents.items():
            if not isinstance(spec, dict):
                continue
            action = str(spec.get("action", "none") or "none")
            desc = str(spec.get("description", "") or "").strip()
            intent_criteria[name] = f"{desc}(动作 {action})" if desc else f"(动作 {action})"

        agent_criteria: Dict[str, str] = {}
        for name in self._agents.names():
            spec = self._agents.get(name)
            if spec is None:
                continue
            agent_criteria[name] = (
                f"{spec.title}:{spec.description}" if spec.description else spec.title
            )

        questions: Dict[str, Dict[str, Any]] = {
            "intent": {
                "type": "choice",
                "instructions": "这条客服场景的用户消息最符合哪个意图?",
                "criteria": intent_criteria,
            },
            "emotion": {
                "type": "choice",
                "instructions": "这条消息里用户的情绪是什么?",
                "criteria": dict(_EMOTION_CRITERIA),
            },
            "agent": {
                "type": "choice",
                "instructions": "应该由哪个专职客服 AGENT 来处理?",
                "criteria": agent_criteria,
            },
        }
        if self._action_candidates:
            # 第四个问题:推荐调用哪个函数(候选=已启用工具)。
            # 只是"推荐",真正发起调用与参数由话术模型的 function calling 完成。
            action_criteria = dict(self._action_candidates)
            action_criteria.setdefault("none", "不需要调用工具,直接与客户对话")
            questions["action"] = {
                "type": "choice",
                "instructions": "处理这条客户消息,建议调用哪个工具(函数)?",
                "criteria": action_criteria,
            }
        return questions

    async def _request_answers(
        self, message: str, history: str, customer_summary: str
    ) -> Dict[str, Any]:
        """POST /v1/systemone,返回原始 answers 字典。"""

        parts = []
        if customer_summary:
            parts.append(f"客户资料摘要:\n{customer_summary}")
        if history:
            parts.append(f"对话历史:\n{history}")
        parts.append(f"用户最新消息:{message}")

        payload = {
            "state": "\n\n".join(parts),
            "model": self._cfg["model"],
            "questions": self._build_questions(),
        }
        client_kwargs: Dict[str, Any] = {"timeout": self._cfg["timeout_seconds"]}
        # models.decision.direct:绕过系统代理直连(Windows 上注册表代理对
        # typesafe.ai 约 25% 传输失败,直连 0 失败且更快;字符串 "false" 也算
        # 关闭,避免 ${JEV_DIRECT:-true} 插值出字符串被 bool() 全判真)
        if _as_bool(self._cfg.get("direct", False)):
            client_kwargs["trust_env"] = False
        async with httpx.AsyncClient(**client_kwargs) as client:
            response = await client.post(
                self._systemone_endpoint(),
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._cfg['api_key']}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()

        answers = data.get("answers") if isinstance(data, dict) else None
        if not isinstance(answers, dict) or not answers:
            raise DecisionError(f"Jev 返回缺少 answers 字段:{data!r:.500}")
        return answers

    # ------------------------------------------------------------- 答案解析 → Decision
    def _parse_decision(self, answers: Dict[str, Any], message: str = "") -> Decision:
        """把 SystemOne 的 answers 映射为 Decision。

        校验语义:未知意图 → fallback;非法情绪 → normal;非法置信度 → 0.5;
        未注册 AGENT → 置空(由路由按意图/默认兜底);action 优先取 Jev 直选,
        非法/缺失时回退到意图配置(action 永远来自配置或本节点的候选集)。
        """

        intent_ans = self._answer(answers, "intent")
        emotion_ans = self._answer(answers, "emotion")
        agent_ans = self._answer(answers, "agent")
        action_ans = self._answer(answers, "action") if self._action_candidates else {}

        intent = str(intent_ans.get("choice", "") or "").strip()
        if intent not in self._intents:
            intent = "fallback"

        emotion = str(emotion_ans.get("choice", "") or "").strip().lower()
        if emotion not in EMOTIONS:
            emotion = "normal"

        agent = self._normalize_agent(agent_ans.get("choice"))

        spec = self._intents.get(intent)
        config_action = (
            str(spec.get("action", "none") or "none") if isinstance(spec, dict) else "none"
        )
        if config_action not in self._known_actions:
            config_action = "none"
        action, action_source = self._resolve_action(action_ans, config_action)

        confidence = self._confidence(intent_ans)
        agent_spec = self._agents.get(agent) if agent else None

        return Decision(
            intent=intent,
            confidence=confidence,
            emotion=emotion,
            need_customer_lookup=action in _CUSTOMER_ACTIONS,
            need_rag=action == "query_knowledge" or bool(agent_spec and agent_spec.needs_rag),
            need_tool=action != "none",
            need_human=action == "transfer_to_human" or emotion in ("angry", "frustrated"),
            action=action,
            action_source=action_source,
            agent=agent,
            reason=(
                f"intent={intent}(置信度 {confidence:.2f}); emotion={emotion}; "
                f"agent={agent or '默认'}"
            ),
            source="llm",
        )

    def _resolve_action(self, action_ans: Dict[str, Any], config_action: str) -> tuple[str, str]:
        """action 解析:Jev 直选(在候选集内)优先,否则回退意图配置映射。"""

        choice = str(action_ans.get("choice", "") or "").strip()
        if not choice:
            return config_action, "config"
        if choice not in self._known_actions:
            logger.warning(
                "Jev 推荐了未知动作 %r(已注册:%s),回退到意图配置映射 %r。",
                choice,
                "/".join(sorted(self._known_actions)),
                config_action,
            )
            return config_action, "config"
        if choice not in self._action_candidates:
            # 合法但未作为候选注入(如配置关闭了推荐):仍以直选为准
            return choice, "jev"
        return choice, "jev"

    @staticmethod
    def _answer(answers: Dict[str, Any], name: str) -> Dict[str, Any]:
        value = answers.get(name)
        if not isinstance(value, dict):
            logger.warning("Jev 返回缺少 %r 问题的回答,按默认处理。", name)
            return {}
        return value

    @staticmethod
    def _confidence(answer: Dict[str, Any]) -> float:
        try:
            value = float(answer.get("confidence", 0.5))
        except (TypeError, ValueError):
            value = 0.5
        return max(0.0, min(1.0, value))

    def _normalize_agent(self, value: Any) -> str:
        """校验 Jev 返回的 AGENT 名;不存在时置空(由 resolve_agent 兜底)。"""

        name = str(value or "").strip()
        if not name:
            return ""
        if self._agents.get(name) is None:
            logger.warning(
                "Jev 返回了未注册的 AGENT:%r(已注册:%s),置空由路由兜底。",
                name,
                "/".join(self._agents.names()),
            )
            return ""
        return name
