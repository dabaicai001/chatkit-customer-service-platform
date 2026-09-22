"""core/session.py:会话级状态(按 thread 维度)。

与业务数据分离:业务数据(客户/订单/工单)在 MCP 上游,这里只保存
本次会话产生的过程状态——客户身份绑定、操作流水、待确认动作、备注。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

#: 待确认动作的过期时间(秒)——超时未确认自动作废
PENDING_ACTION_TTL_SECONDS = 15 * 60


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass(slots=True)
class PendingAction:
    """待用户确认的动作(如退款/取消订单,由 policy 触发)。"""

    action_id: str
    action: str
    params: Dict[str, Any]
    prompt: str
    created_at: float
    expires_at: float

    def is_expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.monotonic()) > self.expires_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action": self.action,
            "params": self.params,
            "prompt": self.prompt,
        }


@dataclass
class SessionState:
    """单个 ChatKit thread 的会话状态。"""

    thread_id: str
    customer_id: Optional[str] = None
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    pending: Dict[str, PendingAction] = field(default_factory=dict)

    def log(self, entry: str, kind: str = "info") -> None:
        self.timeline.insert(0, {"timestamp": _now_iso(), "kind": kind, "entry": entry})


class SessionStateManager:
    """进程内会话状态管理(单机部署;多实例部署时换 Redis 等共享存储)。"""

    def __init__(
        self,
        pending_ttl_seconds: int = PENDING_ACTION_TTL_SECONDS,
        *,
        inherit_default_binding: bool = True,
    ) -> None:
        self._states: Dict[str, SessionState] = {}
        self._pending_ttl = pending_ttl_seconds
        # 工作台级默认绑定(右侧「绑定用户」设置):新会话自动继承,
        # 避免客服每开一个新对话就要重新绑定同一个客户。
        # 多租户/匿名用户与客服同实例部署时关掉它(business.yaml:
        # session.inherit_default_binding=false),防止匿名会话inherit到
        # 客服当前绑定的客户身份。
        self._inherit_default_binding = inherit_default_binding
        self._default_customer_id: Optional[str] = None

    def get(self, thread_id: str) -> SessionState:
        state = self._states.get(thread_id)
        if state is None:
            state = SessionState(thread_id=thread_id)
            self._states[thread_id] = state
        return state

    def drop(self, thread_id: str) -> None:
        self._states.pop(thread_id, None)

    # ------------------------------------------------------------- 客户身份
    def bind_customer(self, thread_id: str, customer_id: str) -> None:
        state = self.get(thread_id)
        if state.customer_id != customer_id:
            state.customer_id = customer_id
            state.log(f"客户身份已绑定:{customer_id}", kind="system")

    def unbind_customer(self, thread_id: str) -> None:
        """解除客户身份绑定(侧栏「解绑」入口);未绑定时为空操作。"""

        state = self.get(thread_id)
        if state.customer_id is not None:
            state.customer_id = None
            state.log("客户身份已解除绑定", kind="system")

    def bind_default_customer(self, customer_id: str) -> None:
        """设置工作台级默认绑定(右侧「绑定用户」):新会话自动继承该身份。"""

        customer_id = (customer_id or "").strip()
        if customer_id:
            self._default_customer_id = customer_id

    def unbind_default_customer(self) -> None:
        """清除工作台级默认绑定(右侧「解绑」)。"""

        self._default_customer_id = None

    def customer_id(self, thread_id: str) -> Optional[str]:
        """会话客户ID:本线程绑定优先,否则继承工作台级默认绑定。"""

        own = self.get(thread_id).customer_id
        if own:
            return own
        return self._default_customer_id if self._inherit_default_binding else None

    def thread_customer_id(self, thread_id: str) -> Optional[str]:
        """仅本线程的显式绑定(不含默认继承)——请求头识别等场景判断用。"""

        return self.get(thread_id).customer_id

    # ------------------------------------------------------------- 流水/备注
    def log(self, thread_id: str, entry: str, kind: str = "info") -> None:
        self.get(thread_id).log(entry, kind)

    def add_note(self, thread_id: str, note: str) -> None:
        state = self.get(thread_id)
        state.notes.insert(0, note)
        state.log(f"客服备注:{note}", kind="info")

    # ------------------------------------------------------------- 待确认动作
    def set_pending_action(
        self,
        thread_id: str,
        action: str,
        params: Dict[str, Any],
        prompt: str,
    ) -> PendingAction:
        now = time.monotonic()
        self._purge_expired(thread_id, now)
        pending = PendingAction(
            action_id=f"pa_{uuid.uuid4().hex[:10]}",
            action=action,
            params=params,
            prompt=prompt,
            created_at=now,
            expires_at=now + self._pending_ttl,
        )
        self.get(thread_id).pending[pending.action_id] = pending
        return pending

    def get_pending_action(
        self, thread_id: str, action_id: Optional[str] = None
    ) -> Optional[PendingAction]:
        now = time.monotonic()
        self._purge_expired(thread_id, now)
        bucket = self.get(thread_id).pending
        if action_id:
            return bucket.get(action_id)
        if not bucket:
            return None
        return max(bucket.values(), key=lambda p: p.created_at)

    def clear_pending_action(self, thread_id: str, action_id: Optional[str] = None) -> None:
        bucket = self.get(thread_id).pending
        if action_id:
            bucket.pop(action_id, None)
        else:
            bucket.clear()

    def _purge_expired(self, thread_id: str, now: float) -> None:
        bucket = self.get(thread_id).pending
        expired = [key for key, pending in bucket.items() if pending.is_expired(now)]
        for key in expired:
            bucket.pop(key, None)
