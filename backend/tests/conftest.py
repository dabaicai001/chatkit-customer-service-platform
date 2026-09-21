"""pytest 固件:mock LLM 服务 + 指向参考 MCP Server 的测试配置。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.mock_llm import start_mock_llm

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_SERVER = BACKEND_ROOT / "app" / "integrations" / "reference" / "crm_mcp_server.py"


@pytest.fixture(scope="session")
def mock_llm_base_url() -> str:
    base_url, _thread, server = start_mock_llm()
    yield base_url
    server.should_exit = True


@pytest.fixture()
def test_config_path(mock_llm_base_url: str, tmp_path: Path) -> Path:
    """生成测试用 business.yaml:LLM 指向 mock,MCP 走 stdio 参考服务。"""

    config = f"""
company:
  name: 测试科技
  industry: ecommerce

customer_service:
  name: 测试客服
  language: zh-CN
  greeting: 您好,我是测试客服
  composer_placeholder: 请输入问题

models:
  decision:
    provider: openjev
    model: mock-jev
    base_url: {mock_llm_base_url}
    api_key: mock-key
    temperature: 0.1
    timeout_seconds: 15
  chat:
    provider: qwen
    model: mock-qwen
    base_url: {mock_llm_base_url}
    api_key: mock-key
    temperature: 0.6
    timeout_seconds: 15
  title:
    provider: qwen
    model: mock-qwen
    base_url: {mock_llm_base_url}
    api_key: mock-key

knowledge:
  enabled: true
  vector_store: memory
  top_k: 3
  score_threshold: 0.05

panels:
  - {{id: overview, label: 概览}}
  - {{id: orders, label: 订单}}
  - {{id: tickets, label: 工单}}

tools:
  - search_customer
  - get_order
  - get_product
  - query_knowledge
  - create_ticket
  - refund_order
  - cancel_order
  - transfer_to_human

intents:
  greet:
    action: none
    description: 打招呼
    keywords: [你好, 在吗]
  customer_identify:
    action: search_customer
    description: 身份识别
    keywords: [我是, 手机号, 李明, 查客户, 客户]
  order_query:
    action: get_order
    description: 查询订单
    keywords: [订单, 发货, 物流, 到哪, 快递]
  refund_request:
    action: refund_order
    description: 申请退款
    keywords: [退款, 退货]
  cancel_order:
    action: cancel_order
    description: 取消订单
    keywords: [取消]
  product_query:
    action: get_product
    description: 商品咨询
    keywords: [商品, 价格]
  knowledge_query:
    action: query_knowledge
    description: 通用问题
    keywords: [怎么, 如何, 政策, 保修, 发票]
  ticket_create:
    action: create_ticket
    description: 建工单
    keywords: [投诉, 工单]
  human_request:
    action: transfer_to_human
    description: 转人工
    keywords: [人工]
  fallback:
    action: none
    description: 无法识别

jev:
  confidence:
    high: 0.85
    low: 0.55
  fallback_action: query_knowledge
  emotion_routing:
    angry: transfer_to_human
    frustrated: transfer_to_human
    anxious: none
    happy: none
    normal: none
  retry:
    attempts: 2
    backoff_seconds: 0.1

rules:
  refund_order:
    require_confirmation: true
    confirmation_prompt: 确认要为订单 {{order_id}} 申请退款吗?
  cancel_order:
    require_confirmation: true
    confirmation_prompt: 确认要取消订单 {{order_id}} 吗?
  human_transfer:
    enabled: true
    queue_position: 2
    queue_estimate: 约 1 分钟

mcp:
  servers:
    - name: crm
      transport: stdio
      command: {sys.executable}
      args: ["{REFERENCE_SERVER.as_posix()}"]
      timeout_seconds: 30
  tool_mapping:
    search_customer: crm.search_customer
    get_order: crm.get_order
    get_product: crm.get_product
    create_ticket: crm.create_ticket
    refund_order: crm.refund_order
    cancel_order: crm.cancel_order
    get_customer: crm.get_customer
    list_orders: crm.list_orders
    list_tickets: crm.list_tickets
  profile_cache_seconds: 0
"""
    path = tmp_path / "business.yaml"
    path.write_text(config, encoding="utf-8")
    return path


@pytest.fixture()
async def customer_server(test_config_path: Path):
    """装配完整的客服服务器(含 MCP 连接)。"""

    from app.config import load_config
    from app.server import create_chatkit_server

    load_config.cache_clear()
    server = create_chatkit_server(load_config(str(test_config_path)))
    await server.startup()
    try:
        yield server
    finally:
        await server.shutdown()
        load_config.cache_clear()
