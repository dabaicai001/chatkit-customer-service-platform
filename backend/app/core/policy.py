"""core/policy.py:路由策略——把 business.yaml 的 jev / rules 段变成可查询的判断接口。

所有"路由旋钮"集中在这里,运营/交付同学改 YAML 即可调整客服行为:
- 置信度分档(jev.confidence.high / low):决定何时直接执行、何时谨慎、何时兜底;
- 低置信兜底动作(jev.fallback_action);
- 情绪 → 路由覆盖(jev.emotion_routing):如愤怒直接转人工;
- 模型重试(jev.retry);
- 敏感动作确认门槛(rules.<action>.require_confirmation);
- 人工坐席开关与排队信息(rules.human_transfer)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from ..config import BusinessConfig

DEFAULT_HIGH = 0.85
DEFAULT_LOW = 0.55
DEFAULT_RETRY_ATTEMPTS = 2
DEFAULT_RETRY_BACKOFF = 0.5


@dataclass(frozen=True)
class ConfidenceBands:
    """置信度分档。"""

    low: float
    high: float

    def level(self, confidence: float) -> str:
        if confidence >= self.high:
            return "high"
        if confidence >= self.low:
            return "medium"
        return "low"


class Policy:
    """客服路由策略(配置驱动)。"""

    def __init__(self, config: BusinessConfig) -> None:
        self._config = config

    def rule(self, action: str) -> Dict[str, Any]:
        return self._config.rule(action)

    # ------------------------------------------------------------- 置信度分档
    def confidence_bands(self) -> ConfidenceBands:
        section = self._config.section("jev.confidence")
        return ConfidenceBands(
            low=_as_float(section.get("low"), DEFAULT_LOW),
            high=_as_float(section.get("high"), DEFAULT_HIGH),
        )

    def confidence_level(self, confidence: float) -> str:
        return self.confidence_bands().level(confidence)

    # ------------------------------------------------------------- 兜底动作
    def fallback_action(self) -> str:
        return str(self._config.get("jev.fallback_action", "query_knowledge") or "query_knowledge")

    # ------------------------------------------------------------- 情绪路由
    def emotion_route(self, emotion: str) -> str | None:
        """情绪对应的路由覆盖动作;none/未配置返回 None(不改道)。"""

        mapping = self._config.section("jev.emotion_routing")
        action = mapping.get(str(emotion or "").lower())
        if action is None:
            return None
        action = str(action).strip()
        return action if action and action != "none" else None

    # ------------------------------------------------------------- 重试
    def retry_attempts(self) -> int:
        return max(1, int(_as_float(self._config.get("jev.retry.attempts"), DEFAULT_RETRY_ATTEMPTS)))

    def retry_backoff_seconds(self) -> float:
        return max(0.0, _as_float(self._config.get("jev.retry.backoff_seconds"), DEFAULT_RETRY_BACKOFF))

    # ------------------------------------------------------------- 确认门槛
    def requires_confirmation(self, action: str) -> bool:
        return bool(self.rule(action).get("require_confirmation", False))

    def confirmation_prompt(self, action: str, params: Dict[str, Any]) -> str:
        template = str(self.rule(action).get("confirmation_prompt", "确认执行「{action}」吗?"))
        try:
            return template.format(action=action, **(params or {}))
        except (KeyError, IndexError):
            return template

    # ------------------------------------------------------------- 转人工
    @property
    def human_transfer_enabled(self) -> bool:
        return bool(self.rule("human_transfer").get("enabled", True))

    def human_transfer_rule(self) -> Dict[str, Any]:
        return self.rule("human_transfer")


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
