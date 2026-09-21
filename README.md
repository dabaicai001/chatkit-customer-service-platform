# 通用客服平台(Customer Service Platform)

由 OpenAI ChatKit 官方 customer-support 示例重构而来:拔掉全部航空业务,抽象为
**换行业只改配置、不改代码**的通用客服平台。

> 衍生自 [openai/openai-chatkit-advanced-samples](https://github.com/openai/openai-chatkit-advanced-samples)(MIT, Copyright 2025 OpenAI),
> 保留原许可证与归属声明,详见 [LICENSE](./LICENSE)。

```
                    ┌─────────────────────┐
                    │      ChatKit UI     │
                    │   通用客服聊天页面    │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │  Customer Gateway   │  ← server.py(编排,不含业务规则)
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │        Jev          │  ← ai/jev.py(大脑:意图/情绪/置信度)
                    │      Decision       │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
        ┌──────────┐     ┌──────────┐     ┌──────────┐
        │   MCP    │     │   RAG    │     │  Human   │
        │ 上游系统  │     │ 公司知识库│     │ 人工客服  │
        └────┬─────┘     └──────────┘     └──────────┘
             │
    ┌────────┼────────┐
    ▼        ▼        ▼
  CRM/OMS  商品中心  工单系统
             │
             ▼
        ┌──────────┐
        │  Qwen    │  ← ai/qwen.py(把话说好:流式生成客服话术)
        └──────────┘
```

- **ChatKit = UI**:官方 SDK 串起聊天、流式、Widget、附件、听写,不重造轮子。
- **Jev = 判断器**:小模型只输出结构化决策(意图/情绪/置信度/路由),不生成回答。
- **Qwen = 说话的人**:大模型基于已查证结果生成自然语言话术,流式返回。
- **MCP = 数据面**:客户/订单/商品/工单全部经内嵌 MCP Client 请求上游系统,平台不直连业务库。
- **RAG = 公司知识库**:内置纯 Python 向量检索(可切换 chroma)。

## 目录结构

```
customer-service/
├── package.json                  # 一键启动(backend + frontend)
├── frontend/                     # Vite + React + ChatKit
│   └── src/
│       ├── components/
│       │   ├── ChatKitPanel.tsx           # ChatKit 接线(基本不改)
│       │   ├── CustomerContextPanel.tsx   # 通用客户侧栏(面板按配置渲染)
│       │   └── customer-context/          # OverviewView / OrdersView / TicketsView
│       ├── hooks/useCustomerContext.ts    # 通用客户画像
│       ├── lib/config.ts                  # API 地址 + bootstrap 拉取
│       └── types/support.ts               # 通用类型
└── backend/
    ├── pyproject.toml
    ├── scripts/run-backend.sh
    ├── tests/                             # 36 个测试:单元 + 端到端(mock LLM + 参考 MCP)
    └── app/
        ├── main.py                        # FastAPI 入口
        ├── server.py                      # Customer Gateway(编排层)
        ├── config.py                      # business.yaml 加载 + 启动校验
        ├── config/business.yaml           # ★ 业务配置中心(换公司只改它)
        ├── core/
        │   ├── customer.py                # 客户域模型 + MCP 数据网关
        │   ├── conversation.py            # 会话记忆
        │   ├── policy.py                  # 路由策略(置信度/情绪/确认门槛)
        │   ├── routing.py                 # 通用路由器
        │   └── session.py                 # 会话状态(身份绑定/流水/待确认)
        ├── ai/
        │   ├── jev.py                     # Jev 决策引擎
        │   ├── qwen.py                    # Qwen 流式生成
        │   └── rag.py                     # RAG 编排
        ├── tools/                         # 插件式工具(强校验 + 统一契约)
        │   ├── customer.py  order.py  product.py  ticket.py
        │   ├── knowledge.py  human.py
        │   └── __init__.py                # ToolRegistry
        ├── knowledge/
        │   ├── faq.py                     # FAQ 语料
        │   └── vector_store.py            # memory(TF-IDF)/ chroma
        ├── integrations/
        │   ├── mcp.py                     # 内嵌 MCP Client
        │   └── reference/crm_mcp_server.py# 参考上游实现(契约 + 测试替身)
        ├── widgets/                       # 通用 Widget(选择列表/确认卡片)
        ├── memory_store.py                # ChatKit Store(官方示例复用)
        ├── attachment_store.py            # 附件存储(官方示例复用)
        └── thread_item_converter.py       # thread→模型输入(官方示例复用)
```

## 快速开始

### 1. 配置(必配,缺失则服务拒绝启动)

模型层为三个独立槽位,**Jev 与 Qwen 的 key 完全分开配置**:

```bash
# Jev(决策模型,小模型即可)
export JEV_PROVIDER=openjev
export JEV_BASE_URL=https://your-jev-gateway/v1     # OpenAI 兼容端点
export JEV_API_KEY=sk-xxx
export JEV_MODEL=openjev-qwen4b

# Qwen(生成模型,大模型)
export QWEN_PROVIDER=qwen
export QWEN_BASE_URL=https://your-qwen-endpoint/v1  # 如 DashScope 兼容模式
export QWEN_API_KEY=sk-xxx
export QWEN_MODEL=qwen3.8-14b

# MCP 数据面(上游系统以 MCP 协议暴露数据)
export CRM_MCP_URL=http://127.0.0.1:9001/mcp
export CRM_MCP_TOKEN=xxx
```

也可直接在 `backend/app/config/business.yaml` 中修改 `models` / `mcp` 段。

### 2. 启动

```bash
# 仓库根目录
npm run install:all
npm run dev          # backend :8001 + frontend :5171
```

或分步:

```bash
# 后端
cd backend && uv sync --extra dev && uv run uvicorn app.main:app --port 8001

# 前端
cd frontend && npm install && npm run dev
```

前端访问 http://localhost:5171(代理 `/support` 到后端 8001)。

### 3. 跑测试

```bash
cd backend && uv run pytest tests/ -q     # 36 passed
```

测试不依赖任何外部服务:mock LLM(OpenAI 兼容)+ 参考 MCP Server(stdio)。

## 核心机制

### Jev 决策(ai/jev.py)

每条用户消息先由 Jev 输出结构化决策,再由代码路由:

```json
{
  "intent": "order_query",
  "confidence": 0.96,
  "emotion": "normal",
  "need_customer_lookup": true,
  "need_rag": false,
  "need_tool": true,
  "need_human": false,
  "action": "get_order",
  "slots": {"order_id": "20260921001"},
  "reason": "用户在查订单物流"
}
```

路由策略全部可在 `business.yaml` 的 `jev` 段调整:

```yaml
jev:
  confidence:
    high: 0.85      # ≥ high:直接执行业务工具
    low: 0.55       # < low:不执行业务工具,走 fallback_action
  fallback_action: query_knowledge
  emotion_routing:  # 情绪 → 路由覆盖(优先于业务动作)
    angry: transfer_to_human
    anxious: none   # none = 不改道,由 Qwen 话术安抚
  retry:
    attempts: 2
    backoff_seconds: 0.5
```

### 通用路由器(core/routing.py)

```
decision.action
  ├─ search_customer / get_order / get_product / create_ticket / refund_order / cancel_order
  │     → 经 MCP 请求上游系统;敏感动作先弹确认卡片(rules.<action>.require_confirmation)
  ├─ query_knowledge  → RAG 检索公司知识库
  ├─ transfer_to_human→ 人工排队(rules.human_transfer)
  └─ none             → 直接对话
```

### 工具层(tools/)

每个工具 = pydantic 强校验入参 + 明确输出契约 + MCP 映射。
换行业 = 在 `business.yaml` 的 `mcp.tool_mapping` 里把平台工具指向新上游系统的
同名工具,平台代码零改动:

```yaml
mcp:
  tool_mapping:
    search_customer: crm.search_customer
    get_order: crm.get_order
    refund_order: crm.refund_order
    get_customer: crm.get_customer        # 侧栏画像聚合
    list_orders: crm.list_orders
    list_tickets: crm.list_tickets
```

### MCP 数据面(integrations/mcp.py)

- 传输:`http`(Streamable HTTP,推荐)/ `stdio`(子进程拉起上游 MCP Server);
- 懒连接、断线重连重试一次、调用超时、结构化结果归一化(自动解包 FastMCP 的
  `{"result": ...}` 约定);
- 启动时连接全部 Server,失败即退出(fail-fast);
- `app/integrations/reference/crm_mcp_server.py` 是参考实现:既是给上游团队的
  契约示例,也是测试替身。启动参考服务:

  ```bash
  python backend/app/integrations/reference/crm_mcp_server.py --http --port 9001
  ```

### 换一家公司的客服 = 改 YAML

`backend/app/config/business.yaml` 一站式配置:公司名、客服人设、语言、欢迎语、
侧栏面板、启用工具、意图目录、Jev 路由参数、确认规则、转人工策略、MCP 连接与映射。
前端启动时从 `/support/bootstrap` 拉取这套配置渲染,无需改前端代码。

### 身份来源(真实环境如何拿到“用户是谁”)

用户信息分两层,**来源不同**:

| 层 | 内容 | 来源 | 方式 |
|---|---|---|---|
| 身份信号 | customer_id / 手机号 / token | 渠道或登录态(不来自 MCP) | 请求头传入,或对话内自述 |
| 身份数据 | 档案、订单、工单 | 上游系统 | 全部走 MCP |

因为 MCP 查询本身需要先知道 customer_id,所以“你是谁”必须从平台外部传入。
三种真实接入方式:

1. **已登录 Web/App(最常见)**:打开客服时带请求头,平台直接绑定 session,
   用户无需自述身份,侧栏立即可见:
   - `X-Customer-Id` — 直接传客户 ID(**生产推荐由网关注入**,客户端直传可伪造);
   - `X-Customer-Phone` — 传手机号,平台经 MCP `search_customer` 解析;
   - `X-Customer-Token` — 传令牌,平台经 MCP `resolve_token` 换客户 ID
     (需在 `mcp.tool_mapping` 中配置 `resolve_token: crm.resolve_token` 才启用)。
2. **第三方渠道(微信/WhatsApp/飞书)**:渠道回调带 openid/unionid,由渠道适配层
   映射成 customer_id 后以同样方式传入。
3. **匿名访客**:对话内自述(手机号/订单号),走 `search_customer` 路径——
   即默认已实现的行为。

请求头由 `server.py::_bind_identity_from_request` 处理,绑定后 Jev 的客户上下文、
侧栏画像、后续工具调用全部自动就绪。

## 与原官方示例的差异

| 维度 | 官方 customer-support | 本平台 |
|---|---|---|
| 业务 | 航空(航班/座位/行李/餐食写死) | 通用(客户/订单/商品/工单) |
| 状态 | AirlineStateManager(进程内种子数据) | MCP 实时请求上游 + 会话状态分离 |
| 决策 | gpt-4.1-mini Agent 自带工具调用 | Jev 结构化决策 + 代码路由(可控/可观测) |
| 生成 | 与决策同一个 Agent | 独立 Qwen 生成层,基于已查证结果 |
| 工具 | 6 个航空工具写死在 Agent | 插件式 ToolRegistry,pydantic 校验,MCP 映射 |
| 组件 | 航班选择/餐食选择 | 通用选择列表/确认卡片(.widget 模板) |
| 侧栏 | 航班/行程/会员 | 客户档案/订单/工单(面板按配置渲染) |
| 配置 | 散落在代码 | business.yaml 配置中心 |
| 身份 | thread 内写死种子客户 | 请求头/渠道/对话内识别三条路径,MCP 取数 |

## 已知边界

- ChatKit Store 为进程内 MemoryStore(官方示例复用),多实例部署需换持久化 Store;
- 会话状态(身份绑定/待确认)为进程内,多实例部署需换 Redis 等共享存储;
- 语音听写(transcribe)仍走 OpenAI `gpt-4o-transcribe`,未配置 `OPENAI_API_KEY` 时
  听写不可用,不影响文字对话;
- 知识库默认为内存 TF-IDF 检索,`knowledge.vector_store: chroma` 可切换(需装 chromadb)。
