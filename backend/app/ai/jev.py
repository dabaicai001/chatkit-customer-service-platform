"""ai/jev.py:Jev 决策引擎——系统的大脑/「判断器」。

职责(且只负责这个):分析用户消息,输出结构化决策 JSON,不生成回答。

    {
      "intent": "order_query",          # 意图
      "confidence": 0.96,               # 置信度
      "emotion": "normal",              # 情绪
      "need_customer_lookup": true,     # 是否需要客户资料
      "need_rag": false,                # 是否需要知识库
      "need_tool": true,                # 是否需要调工具
      "need_human": false,              # 是否需要人工
      "action": "get_order",            # 路由动作(工具名)
      "slots": {"order_id": "..."},     # 抽取的参数
      "reason": "用户在查订单物流"
    }

Jev 通过 business.yaml 的 ``models.decision`` 槽位接入(独立的环境变量
JEV_PROVIDER / JEV_BASE_URL / JEV_API_KEY / JEV_MODEL,与 Qwen 槽位互不影响)。
未配置或调用失败时直接抛错——不做本地规则降级,避免"看起来在跑、其实在瞎猜"。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict

import httpx

from ..config import BusinessConfig
from ..core.policy import Policy
from .agents import AgentRegistry

logger = logging.getLogger(__name__)

EMOTIONS = ("normal", "happy", "anxious", "frustrated", "angry")


class DecisionError(RuntimeError):
    """Jev 决策失败(配置缺失 / 模型调用失败 / 输出不可解析)。"""


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
    agent: str = ""  # 调度哪个专职 AGENT(AI CHAT 出口内部)
    slots: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    source: str = "llm"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_JEV_SYSTEM_PROMPT = """你是客服系统的意图决策引擎 Jev,只负责"判断与调度",不负责"回答"。
分析用户最新消息,结合对话历史与客户资料,输出一个 JSON 决策对象。

可用意图(intent → action → 说明):
{intent_lines}

可调度的专职 AGENT(AI CHAT 出口内部,按职责选择最合适的一个):
{agent_lines}

输出要求(只输出 JSON,不要输出任何其他文字):
{{
  "intent": "意图名",
  "confidence": 0.0到1.0之间的数字,
  "emotion": "normal|happy|anxious|frustrated|angry 之一",
  "need_customer_lookup": true/false,
  "need_rag": true/false,
  "need_tool": true/false,
  "need_human": true/false,
  "action": "上面列出的 action 之一,不需要工具时填 none",
  "agent": "上面列出的 AGENT 名之一(必须选一个)",
  "slots": {{"order_id": "订单号,没有则填空字符串", "query": "检索用的问题改写"}},
  "reason": "一句话说明判断依据"
}}

规则:
- action 只能使用意图目录里出现的 action 或 none,禁止编造。
- agent 只能使用 AGENT 目录里出现的名字,禁止编造;无法归类时选默认 AGENT({default_agent})。
- 用户消息中出现 8 位以上数字(订单号)时,必须填入 slots.order_id。
- 咨询公司政策、售后规则、发票、物流时效等通用问题时 need_rag=true 且 action=query_knowledge。
- 用户明确要求找人工/真人客服时 action=transfer_to_human。
- 广告、骚扰、乱码、测试字符、与业务完全无关的无意义内容:intent 填 garbage,action 填 none。
- 用户表达愤怒、威胁投诉时 emotion=angry 或 frustrated,并设 need_human=true。
- 置信度不确定时填低一点(<=0.5),系统会走知识库兜底。"""


class JevDecisionEngine:
    """意图/情绪/路由决策器(LLM,配置缺失即报错)。"""

    def __init__(self, config: BusinessConfig) -> None:
        self._config = config
        self._cfg = config.require_model("decision")  # 缺失直接 ConfigurationError
        self._policy = Policy(config)
        self._intents = config.intent_catalog or {}
        self._agents = AgentRegistry(config)
        self._known_actions = self._collect_known_actions()
        logger.info(
            "Jev 决策引擎就绪(provider=%s, model=%s, 置信度分档 low=%.2f/high=%.2f, "
            "可调度 AGENT=%s)。",
            self._cfg["provider"],
            self._cfg["model"],
            self._policy.confidence_bands().low,
            self._policy.confidence_bands().high,
            "/".join(self._agents.names()),
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
        for attempt in range(1, attempts + 1):
            try:
                content = await self._call_llm(message, history, customer_summary)
                decision = self._parse_decision(content)
                logger.info(
                    "Jev 决策:intent=%s action=%s agent=%s confidence=%.2f emotion=%s(%s)",
                    decision.intent,
                    decision.action,
                    decision.agent or "(未指定,路由兜底)",
                    decision.confidence,
                    decision.emotion,
                    decision.reason,
                )
                return decision
            except DecisionError:
                raise  # 输出非法属于确定性问题,不重试
            except Exception as exc:
                last_error = exc
                logger.warning("Jev 第 %d/%d 次调用失败:%s", attempt, attempts, exc)
                if attempt < attempts:
                    await asyncio.sleep(backoff * attempt)
        raise DecisionError(f"Jev 决策失败(已重试 {attempts} 次):{last_error}")

    # ------------------------------------------------------------- LLM 调用
    async def _call_llm(self, message: str, history: str, customer_summary: str) -> str:
        intent_lines = "\n".join(
            f"- {name} → {spec.get('action', 'none')} → {spec.get('description', '')}"
            for name, spec in self._intents.items()
            if isinstance(spec, dict)
        )
        system = _JEV_SYSTEM_PROMPT.format(
            intent_lines=intent_lines or "(无)",
            agent_lines=self._agents.catalog_text(),
            default_agent=self._agents.default_name,
        )

        user_parts = []
        if customer_summary:
            user_parts.append(f"客户资料摘要:\n{customer_summary}")
        if history:
            user_parts.append(f"对话历史:\n{history}")
        user_parts.append(f"用户最新消息:{message}")

        payload = {
            "model": self._cfg["model"],
            "temperature": self._cfg["temperature"],
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": "\n\n".join(user_parts)},
            ],
        }
        async with httpx.AsyncClient(timeout=self._cfg["timeout_seconds"]) as client:
            response = await client.post(
                f"{self._cfg['base_url'].rstrip('/')}/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._cfg['api_key']}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise DecisionError(f"Jev 返回结构异常:{exc};原始返回:{data!r:.500}") from exc

    # ------------------------------------------------------------- 输出解析
    def _parse_decision(self, content: str) -> Decision:
        raw = self._loads_decision_json(content)
        if raw is None:
            raise DecisionError(f"Jev 返回无法解析为 JSON:{content[:300]!r}")

        action = str(raw.get("action", "none") or "none")
        if action not in self._known_actions:
            raise DecisionError(
                f"Jev 返回了意图目录之外的 action:{action!r}(允许:{sorted(self._known_actions)})"
            )

        slots = raw.get("slots") or {}
        if not isinstance(slots, dict):
            raise DecisionError(f"Jev 返回的 slots 不是对象:{slots!r:.200}")

        emotion = str(raw.get("emotion", "normal") or "normal").lower()
        if emotion not in EMOTIONS:
            emotion = "normal"

        try:
            confidence = float(raw.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5

        decision = Decision(
            intent=str(raw.get("intent", "fallback") or "fallback"),
            confidence=max(0.0, min(1.0, confidence)),
            emotion=emotion,
            need_customer_lookup=bool(raw.get("need_customer_lookup", False)),
            need_rag=bool(raw.get("need_rag", False)),
            need_tool=bool(raw.get("need_tool", False)),
            need_human=bool(raw.get("need_human", False)),
            action=action,
            agent=self._normalize_agent(raw.get("agent")),
            slots={str(k): v for k, v in slots.items()},
            reason=str(raw.get("reason", "") or ""),
            source="llm",
        )
        decision.need_tool = decision.action != "none"
        return decision

    def _normalize_agent(self, value: Any) -> str:
        """校验 Jev 返回的 AGENT 名;不存在时置空(由 Router 按意图/默认兜底)。"""

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

    @staticmethod
    def _loads_decision_json(content: str) -> Dict[str, Any] | None:
        content = (content or "").strip()
        if not content:
            return None
        try:
            parsed = json.loads(content)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
