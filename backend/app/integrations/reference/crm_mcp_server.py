"""integrations/reference/crm_mcp_server.py:参考 CRM MCP Server。

作用有两个:
1. **契约**:上游系统(CRM/OMS/商品中心/工单系统)按这套工具名与入参/出参
   暴露 MCP Server,客服平台即可零改动接入;
2. **测试替身**:平台测试用它在 stdio 传输上做端到端联调。

启动方式:
    stdio(子进程,推荐用于本平台):  python app/integrations/reference/crm_mcp_server.py
    HTTP(Streamable HTTP):          python app/integrations/reference/crm_mcp_server.py --http --port 9001

数据为内存种子;生产环境由上游系统自行实现(查库/调内部服务)。
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("reference-crm")

# ---------------------------------------------------------------------------
# 内存种子数据(契约示例)
# ---------------------------------------------------------------------------
_CUSTOMERS: List[Dict[str, Any]] = [
    {
        "customer_id": "cus_10086",
        "name": "李明",
        "level": "VIP",
        "email": "liming@example.com",
        "phone": "13800008888",
        "tags": ["老客户", "数码爱好者"],
        "summary": "注册 3 年,累计消费 ¥12,600,偏好 3C 数码产品。",
    },
    {
        "customer_id": "cus_10087",
        "name": "王芳",
        "level": "普通会员",
        "email": "wangfang@example.com",
        "phone": "13900009999",
        "tags": ["新客户"],
        "summary": "首次购买,咨询过退换货政策。",
    },
]

_ORDERS: List[Dict[str, Any]] = [
    {
        "id": "20260921001",
        "customer_id": "cus_10086",
        "title": "无线降噪耳机 Pro",
        "status": "配送中",
        "amount": 1299.00,
        "created_at": "2026-09-19",
        "tracking": "SF1234567890 · 预计 09-22 送达",
    },
    {
        "id": "20260915088",
        "customer_id": "cus_10086",
        "title": "机械键盘 87 键",
        "status": "已完成",
        "amount": 459.00,
        "created_at": "2026-09-15",
        "tracking": "已于 09-17 签收",
    },
    {
        "id": "20260901033",
        "customer_id": "cus_10087",
        "title": "智能手表 S2",
        "status": "待发货",
        "amount": 899.00,
        "created_at": "2026-09-20",
        "tracking": "",
    },
]

_TICKETS: List[Dict[str, Any]] = [
    {
        "id": "TK-20260918001",
        "customer_id": "cus_10086",
        "subject": "耳机左耳无声",
        "status": "处理中",
        "priority": "高",
        "created_at": "2026-09-18",
    },
]

_PRODUCTS: List[Dict[str, Any]] = [
    {"product_id": "p_earphone_pro", "name": "无线降噪耳机 Pro", "price": 1299.00, "stock": 42, "spec": "主动降噪 / 40h 续航"},
    {"product_id": "p_keyboard_87", "name": "机械键盘 87 键", "price": 459.00, "stock": 8, "spec": "青轴 / 全键无冲"},
    {"product_id": "p_watch_s2", "name": "智能手表 S2", "price": 899.00, "stock": 0, "spec": "血氧心率 / 14 天续航"},
]


# ---------------------------------------------------------------------------
# 工具实现(契约)
# ---------------------------------------------------------------------------
@mcp.tool()
def search_customer(
    keyword: Optional[str] = None,
    customer_id: Optional[str] = None,
    phone: Optional[str] = None,
) -> Dict[str, Any]:
    """按关键字(姓名/手机号)或客户 ID 检索客户。"""
    hits = []
    for customer in _CUSTOMERS:
        if customer_id and customer["customer_id"] == customer_id:
            hits.append(customer)
        elif phone and customer["phone"] == phone:
            hits.append(customer)
        elif keyword and (
            keyword in customer["name"] or keyword in customer["phone"]
        ):
            hits.append(customer)
    return {"customers": hits}


@mcp.tool()
def get_customer(customer_id: str) -> Dict[str, Any]:
    """按客户 ID 获取客户资料。"""
    for customer in _CUSTOMERS:
        if customer["customer_id"] == customer_id:
            return {"customer": copy.deepcopy(customer)}
    return {"customer": None}


@mcp.tool()
def list_orders(customer_id: str) -> Dict[str, Any]:
    """列出客户的全部订单。"""
    return {
        "orders": [
            copy.deepcopy(order)
            for order in _ORDERS
            if order["customer_id"] == customer_id
        ]
    }


@mcp.tool()
def get_order(
    order_id: Optional[str] = None, customer_id: Optional[str] = None
) -> Dict[str, Any]:
    """按订单号查询,或返回客户的全部订单。"""
    if order_id:
        for order in _ORDERS:
            if order["id"] == order_id:
                return {"orders": [copy.deepcopy(order)]}
        return {"orders": []}
    if customer_id:
        return list_orders(customer_id)
    return {"orders": []}


@mcp.tool()
def list_tickets(customer_id: str) -> Dict[str, Any]:
    """列出客户的工单。"""
    return {
        "tickets": [
            copy.deepcopy(ticket)
            for ticket in _TICKETS
            if ticket["customer_id"] == customer_id
        ]
    }


@mcp.tool()
def get_product(
    product_id: Optional[str] = None, keyword: Optional[str] = None
) -> Dict[str, Any]:
    """按商品 ID 或关键字查询商品。"""
    hits = []
    for product in _PRODUCTS:
        if product_id and product["product_id"] == product_id:
            hits.append(product)
        elif keyword and keyword in product["name"]:
            hits.append(product)
    return {"products": copy.deepcopy(hits)}


@mcp.tool()
def create_ticket(
    subject: str,
    customer_id: Optional[str] = None,
    description: Optional[str] = None,
    priority: Optional[str] = None,
) -> Dict[str, Any]:
    """创建工单,返回工单号。"""
    ticket = {
        "id": f"TK-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "customer_id": customer_id or "",
        "subject": subject,
        "status": "待处理",
        "priority": priority or "中",
        "created_at": datetime.now().strftime("%Y-%m-%d"),
    }
    _TICKETS.append(ticket)
    return {
        "ticket": copy.deepcopy(ticket),
        "result": f"工单已创建,单号 {ticket['id']}。",
        "state_changed": True,
    }


@mcp.tool()
def refund_order(
    order_id: str, customer_id: Optional[str] = None, reason: Optional[str] = None
) -> Dict[str, Any]:
    """订单退款,返回退款结果。"""
    for order in _ORDERS:
        if order["id"] == order_id:
            if order["status"] == "已退款":
                return {"result": f"订单 {order_id} 已退款,请勿重复提交。", "state_changed": False}
            order["status"] = "已退款"
            return {
                "order": copy.deepcopy(order),
                "result": f"订单 {order_id} 退款已提交,¥{order['amount']:.2f} 原路返回。",
                "state_changed": True,
            }
    return {"result": f"未找到订单 {order_id}。", "state_changed": False}


@mcp.tool()
def cancel_order(
    order_id: str, customer_id: Optional[str] = None, reason: Optional[str] = None
) -> Dict[str, Any]:
    """取消订单,返回取消结果。"""
    for order in _ORDERS:
        if order["id"] == order_id:
            if order["status"] in ("已取消", "已退款"):
                return {"result": f"订单 {order_id} 已是终态({order['status']})。", "state_changed": False}
            order["status"] = "已取消"
            return {
                "order": copy.deepcopy(order),
                "result": f"订单 {order_id} 已取消。",
                "state_changed": True,
            }
    return {"result": f"未找到订单 {order_id}。", "state_changed": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="参考 CRM MCP Server")
    parser.add_argument("--http", action="store_true", help="以 Streamable HTTP 方式运行")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9001)
    args = parser.parse_args()
    if args.http:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
