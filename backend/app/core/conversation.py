"""会话记忆:按 thread 保留最近对话轮次,供 Jev 决策与 Qwen 生成使用。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(slots=True)
class Turn:
    role: str  # "user" | "assistant"
    text: str


@dataclass
class ConversationMemory:
    """进程内会话记忆(演示用;生产环境换 Redis/DB)。"""

    max_turns: int = 12
    _turns: Dict[str, List[Turn]] = field(default_factory=lambda: defaultdict(list))
    _last_decision: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def add_turn(self, thread_id: str, role: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        turns = self._turns[thread_id]
        turns.append(Turn(role=role, text=text))
        if len(turns) > self.max_turns:
            del turns[: len(turns) - self.max_turns]

    def recent(self, thread_id: str, limit: int = 6) -> List[Turn]:
        return self._turns.get(thread_id, [])[-limit:]

    def transcript(self, thread_id: str, limit: int = 6) -> str:
        lines = [f"{turn.role}: {turn.text}" for turn in self.recent(thread_id, limit)]
        return "\n".join(lines)

    def remember_decision(self, thread_id: str, decision: Dict[str, Any]) -> None:
        self._last_decision[thread_id] = decision

    def last_decision(self, thread_id: str) -> Dict[str, Any] | None:
        return self._last_decision.get(thread_id)

    def clear(self, thread_id: str) -> None:
        self._turns.pop(thread_id, None)
        self._last_decision.pop(thread_id, None)
