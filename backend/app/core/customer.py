"""core/customer.py:客户域模型 + 数据网关。

平台自身不存储业务数据:客户、订单、工单等全部通过 MCP 从上游系统
(CRM / OMS / 工单系统)实时请求,这里只做「归一化」——把不同上游的
字段差异收敛成统一的域模型,供 Jev 上下文、Qwen 生成和侧栏面板使用。
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from ..integrations.mcp import McpClientManager, McpError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 域模型(与行业无关的通用结构)
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Order:
    id: str
    title: str
    status: str
    amount: float
    created_at: str
    tracking: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Ticket:
    id: str
    subject: str
    status: str
    priority: str
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CustomerProfile:
    """统一客户画像(由 MCP 上游数据归一化而来)。"""

    customer_id: str
    name: str
    level: str = "普通会员"
    email: str = ""
    phone: str = ""
    tags: List[str] = field(default_factory=list)
    summary: str = ""
    orders: List[Order] = field(default_factory=list)
    tickets: List[Ticket] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("raw", None)
        data["orders"] = [order.to_dict() for order in self.orders]
        data["tickets"] = [ticket.to_dict() for ticket in self.tickets]
        return data

    def summary_text(self) -> str:
        """注入给 Jev/Qwen 的客户摘要。"""

        lines = [
            f"客户:{self.name}({self.level}, ID {self.customer_id})",
            f"联系方式:{self.email or '-'} / {self.phone or '-'}",
        ]
        if self.summary:
            lines.append(f"客户概况:{self.summary}")
        if self.tags:
            lines.append(f"标签:{', '.join(self.tags)}")
        if self.orders:
            recent = self.orders[:3]
            lines.append(
                "近期订单:"
                + "; ".join(f"{o.id} {o.title}({o.status})" for o in recent)
            )
        if self.tickets:
            recent = self.tickets[:3]
            lines.append(
                "相关工单:"
                + "; ".join(f"{t.id} {t.subject}({t.status})" for t in recent)
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 字段归一化工具(容忍不同上游的字段命名差异)
# ---------------------------------------------------------------------------
def _first_str(raw: Mapping[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return str(value)
    return default


def _first_float(raw: Mapping[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return default


def _first_list(raw: Mapping[str, Any], *keys: str) -> List[str]:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, list):
            return [str(v) for v in value]
    return []


def normalize_customer(raw: Mapping[str, Any]) -> CustomerProfile:
    if not isinstance(raw, Mapping):
        raise McpError(f"上游返回的客户数据格式错误:{type(raw).__name__}")
    customer_id = _first_str(raw, "customer_id", "id", "userId", "user_id")
    if not customer_id:
        raise McpError("上游客户数据缺少 customer_id/id 字段。")
    return CustomerProfile(
        customer_id=customer_id,
        name=_first_str(raw, "name", "nickname", "customer_name", default="未知客户"),
        level=_first_str(raw, "level", "tier", "member_level", "grade", default="普通会员"),
        email=_first_str(raw, "email", "mail"),
        phone=_first_str(raw, "phone", "mobile", "telephone"),
        tags=_first_list(raw, "tags", "labels"),
        summary=_first_str(raw, "summary", "profile", "description"),
        orders=[],
        tickets=[],
        raw=dict(raw),
    )


def normalize_order(raw: Mapping[str, Any]) -> Order:
    return Order(
        id=_first_str(raw, "id", "order_id", "order_no", "orderNo"),
        title=_first_str(raw, "title", "name", "product_name", "subject"),
        status=_first_str(raw, "status", "order_status", "state", default="未知"),
        amount=_first_float(raw, "amount", "total", "total_amount", "price", "pay_amount"),
        created_at=_first_str(raw, "created_at", "create_time", "createdAt", "order_time"),
        tracking=_first_str(raw, "tracking", "tracking_no", "logistics", "express"),
    )


def normalize_ticket(raw: Mapping[str, Any]) -> Ticket:
    return Ticket(
        id=_first_str(raw, "id", "ticket_id", "ticket_no"),
        subject=_first_str(raw, "subject", "title", "content", "description"),
        status=_first_str(raw, "status", "ticket_status", "state", default="未知"),
        priority=_first_str(raw, "priority", "level", default="中"),
        created_at=_first_str(raw, "created_at", "create_time", "createdAt"),
    )


def _as_records(payload: Any, *collection_keys: str) -> List[Mapping[str, Any]]:
    """把上游返回统一解析为记录列表。

    兼容三种常见形态::
        {"orders": [...]}
        {"data": {"orders": [...]}}
        [{...}, {...}]
    """

    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in collection_keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
        data = payload.get("data")
        if isinstance(data, Mapping):
            return _as_records(data, *collection_keys)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, Mapping)]
    return []


# ---------------------------------------------------------------------------
# 数据网关
# ---------------------------------------------------------------------------
class CustomerGateway:
    """经 MCP 聚合客户数据的统一入口(带短 TTL 缓存,避免面板频繁回源)。"""

    def __init__(self, mcp: McpClientManager, cache_seconds: float = 5.0) -> None:
        self._mcp = mcp
        self._cache_seconds = max(0.0, cache_seconds)
        self._cache: Dict[str, tuple[float, CustomerProfile]] = {}
        self._loop_time = 0.0

    def _now(self) -> float:
        import time

        return time.monotonic()

    async def search_customer(
        self,
        *,
        keyword: str = "",
        customer_id: str = "",
        phone: str = "",
    ) -> Optional[CustomerProfile]:
        """按关键字/ID/手机号检索客户,命中返回画像(不含订单/工单)。"""

        arguments = {
            key: value
            for key, value in (
                ("keyword", keyword),
                ("customer_id", customer_id),
                ("phone", phone),
            )
            if value
        }
        if not arguments:
            raise McpError("检索客户至少需要 keyword / customer_id / phone 之一。")
        mapping = self._mcp.require_mapping("search_customer")
        payload = await self._mcp.call_tool(mapping, arguments)
        records = _as_records(payload, "customers", "customer", "results", "list", "items")
        if not records:
            return None
        return normalize_customer(records[0])

    async def load_profile(self, customer_id: str, *, refresh: bool = False) -> CustomerProfile:
        """加载完整画像:客户基本信息 + 订单 + 工单(三次 MCP 调用)。"""

        if not customer_id:
            raise McpError("load_profile 需要 customer_id。")
        cached = self._cache.get(customer_id)
        if not refresh and cached and self._now() - cached[0] < self._cache_seconds:
            return cached[1]

        base_mapping = self._mcp.require_mapping("get_customer")
        base_payload = await self._mcp.call_tool(base_mapping, {"customer_id": customer_id})
        base_records = _as_records(base_payload, "customer", "data", "results")
        profile = (
            normalize_customer(base_records[0])
            if base_records
            else normalize_customer({"customer_id": customer_id, **(base_payload if isinstance(base_payload, Mapping) else {})})
        )

        orders_mapping = self._mcp.require_mapping("list_orders")
        orders_payload = await self._mcp.call_tool(orders_mapping, {"customer_id": customer_id})
        profile.orders = [normalize_order(r) for r in _as_records(orders_payload, "orders", "list", "items")]

        tickets_mapping = self._mcp.require_mapping("list_tickets")
        tickets_payload = await self._mcp.call_tool(tickets_mapping, {"customer_id": customer_id})
        profile.tickets = [normalize_ticket(r) for r in _as_records(tickets_payload, "tickets", "list", "items")]

        self._cache[customer_id] = (self._now(), profile)
        return profile

    def invalidate(self, customer_id: str) -> None:
        self._cache.pop(customer_id, None)
