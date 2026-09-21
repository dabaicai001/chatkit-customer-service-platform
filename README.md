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
                               ▲
                               │ 流式文本 / Widget / 画像副作用
                    ┌──────────┴──────────┐
                    │  Customer Gateway   │  ← server.py(编排,不含业务规则)
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │        Jev          │  ← ai/jev.py(大脑:意图/情绪/置信度)
                    │      Decision       │
                    └──────────┬──────────┘
                               │ 出口判定(意图/情绪/置信度/规则)
              ┌────────────────┴────────────────┐
              ▼                                 ▼
        ┌──────────────┐                  ┌──────────────┐
        │  ① AI CHAT   │                  │ ② 人工客服   │
        │  AI 自己办成  │                  │ 转接排队接管  │
        └──────┬───────┘                  └──────────────┘
               │ 内部三种办案手段
    ┌──────────┼──────────┐
    ▼          ▼          ▼
┌────────┐ ┌────────┐ ┌──────────┐
│  MCP   │ │  RAG   │ │ 直接回答  │
│ 上游数据│ │公司知识库│ │(无需查证)│
└───┬────┘ └───┬────┘ └──────────┘
    │          │
    └────┬─────┘
         ▼
┌─────────────────────────┐
│  已查证信息(统一上下文)  │
│  工具结果 + 知识库段落    │  ← ComposeContext(ai/qwen.py)
└──────────┬──────────────┘
           ▼
    ┌──────────┐
    │ 生成模型 │  ← ai/qwen.py(把话说好:流式生成客服话术)
    └──────────┘
```

**最终出口只有两个**:

1. **AI CHAT** — AI 把事办成:内部按需走 MCP 取数 / RAG 查知识 / 直接回答,
   结果统一汇成「已查证信息」交给生成模型产出话术;
2. **人工客服** — 转接:用户明确要求、情绪愤怒、低置信度、或上游未暴露的
   变更类操作(退款/取消/建工单)时,由人工接管(转接提示语也由 AI 生成)。

生成模型是 AI 出口内部各手段的汇合点——它不自己调 MCP/RAG,只负责
“把已查证的事说好”。

- **ChatKit = UI**:官方 SDK 串起聊天、流式、Widget、附件、听写,不重造轮子。
- **Jev = 判断器**:小模型只输出结构化决策(意图/情绪/置信度/路由),不生成回答。
- **生成模型 = 说话的人**:chat 槽位大模型基于已查证结果生成自然语言话术,流式返回。
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
    ├── tests/                             # 19 个纯离线单元测试(无 mock 服务)
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
        │   ├── qwen.py                    # chat 槽位流式生成
        │   └── rag.py                     # RAG 编排
        ├── tools/                         # 插件式工具(强校验 + 统一契约)
        │   ├── customer.py  order.py  product.py  ticket.py
        │   ├── knowledge.py  human.py
        │   └── __init__.py                # ToolRegistry
        ├── knowledge/
        │   ├── faq.py                     # FAQ 语料
        │   └── vector_store.py            # memory(TF-IDF)/ chroma
        ├── integrations/
        │   └── mcp.py                     # 内嵌 MCP Client
        ├── widgets/                       # 通用 Widget(选择列表/确认卡片)
        ├── memory_store.py                # ChatKit Store(官方示例复用)
        ├── attachment_store.py            # 附件存储(官方示例复用)
        └── thread_item_converter.py       # thread→模型输入(官方示例复用)
```

## 快速开始

### 1. 配置(只差 3 个 key)

`business.yaml` 已内置全部默认值(Jev 指向 typesafe 网关、chat 指向 MiniMax 的
OpenAI 兼容端点),**真正要填的只有 3 个密钥**:

```bash
cp backend/.env.example backend/.env
# 编辑 backend/.env,填这三个值:
#   JEV_API_KEY=...        # Jev 决策模型(business.yaml 默认 https://api.typesafe.ai/v1/systemone)
#   CHAT_API_KEY=...       # 话术生成模型(默认 MiniMax: https://api.minimax.cn/v1,模型 MiniMax-M3)
#   UPSTREAM_MCP_TOKEN=... # 上游业务系统 MCP 的 auth token(URL 按实际改)
cp frontend/.env.example frontend/.env   # 填真实 ChatKit 域名密钥(见下方注意项)
```

chat 槽位不绑死厂商(Qwen/GPT/DeepSeek/MiniMax 均可,只要是 OpenAI 兼容端点),
换厂商只改 `CHAT_BASE_URL` + `CHAT_MODEL` 两个值。

### 2. 启动

```bash
# 仓库根目录
npm run install:all
npm run dev          # backend :8001 + frontend :5171(Windows 用 npm run dev:win)
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
cd backend && uv sync --extra dev && uv run pytest tests/ -q     # 19 passed
```

测试为**纯离线单元测试**(约 1 秒):不依赖任何外部服务,也不需要 mock 服务。
按项目约定,mock 数据只允许出现在单元测试里;集成/端到端行为请直接打真实服务
(真实 Jev/chat 端点 + 真实上游 MCP)验证。

## 准生产/生产接入:模型 key 在哪里输入

**唯一入口:环境变量**(代码与 `business.yaml` 里不含任何密钥)。
完整清单见 `backend/.env.example` 与 `frontend/.env.example`,复制后填真实值:

| 变量 | 必填 | 说明 |
|---|---|---|
| `JEV_PROVIDER` / `JEV_BASE_URL` / `JEV_API_KEY` / `JEV_MODEL` | ✅ key | Jev 决策模型;默认 `https://api.typesafe.ai/v1/systemone` |
| `CHAT_PROVIDER` / `CHAT_BASE_URL` / `CHAT_API_KEY` / `CHAT_MODEL` | ✅ key | 话术生成模型(不限厂商);默认 MiniMax `https://api.minimax.cn/v1` / `MiniMax-M3` |
| `UPSTREAM_MCP_URL` / `UPSTREAM_MCP_TOKEN` | ✅ token | MCP 数据面(上游业务系统);默认 `http://127.0.0.1:9003/mcp` |
| `OPENAI_API_KEY` | ⬜ | 语音听写(不配则听写不可用,文字对话不受影响) |
| `VITE_CHATKIT_API_DOMAIN_KEY`(前端) | ✅ | ChatKit 域名密钥,见下方“域名 allowlist” |

按部署方式四选一:

```bash
# ① .env 文件(单机/准生产最常用)
cp backend/.env.example backend/.env      # 填入真实值(.env 已被 .gitignore)
cp frontend/.env.example frontend/.env    # 填真实 ChatKit 域名密钥
npm run dev:win                           # Windows;Linux/macOS 用 npm run dev
# 启动脚本会自动加载 backend/.env;也可指定其他文件:ENV_FILE=/path/to/env

# ② 平台注入环境变量(Docker -e / K8s Secret / systemd EnvironmentFile)
#    优先级高于 .env,与之混用不会冲突
JEV_API_KEY=sk-xxx CHAT_API_KEY=sk-yyy \
  uv run uvicorn app.main:app --port 8001

# ③ uv 原生 env-file(不改代码)
uv run --env-file backend/.env uvicorn app.main:app --app-dir backend --port 8001

# ④ Docker
docker run --env-file backend/.env -p 8001:8001 your-image
```

**接入后自检(30 秒)**:

```bash
curl http://<host>:8001/support/health    # {"status":"healthy","mcp":{"upstream":{"connected":true,...}}}
curl http://<host>:8001/support/tools     # 确认 jev/chat 槽位显示真实 provider/model、mcp 映射齐全
```

若 key 缺失,服务**拒绝启动**并列出缺少的变量(不静默降级);若端点不通,
`/support/health` 的 `mcp` 段会显示 `connected: false` 与原因。

**两个准生产注意项**:

1. **ChatKit 域名 allowlist**:前端 `VITE_CHATKIT_API_DOMAIN_KEY` 必须是在
   [platform.openai.com/settings/organization/security/domain-allowlist](https://platform.openai.com/settings/organization/security/domain-allowlist)
   注册过准生产域名后生成的 `domain_pk_...` 真实密钥;本地占位符只够开发用。
   同时在 `frontend/vite.config.ts` 的 `server.allowedHosts` 中加入该域名。
2. **身份接入**:已登录场景由网关/前端带 `X-Customer-Id`(或 `X-Customer-Phone` /
   `X-Customer-Token`)请求头,详见下方「身份来源」;客户端直传的 ID 可伪造,
   生产上应由网关注入或改用 token 换取。

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
    anxious: none   # none = 不改道,由生成模型话术安抚
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
    search_customer: upstream.search_customer
    get_order: upstream.get_order
    get_customer: upstream.get_customer        # 侧栏画像聚合
    list_orders: upstream.list_orders
    get_product: upstream.get_product
```

### MCP 数据面(integrations/mcp.py)

- 传输:`http`(Streamable HTTP,推荐)/ `stdio`(子进程拉起上游 MCP Server);
- 懒连接、断线重连重试一次、调用超时、结构化结果归一化(自动解包 FastMCP 的
  `{"result": ...}` 约定);
- 启动时连接全部 Server,失败即退出(fail-fast);
- 兼容 stateful MCP Server(自动 initialize + 会话 ID 管理);
- 上游字段命名容忍差异(snake_case / camelCase 都认,归一化在 `core/customer.py`);
- 工单为可选数据源:上游未配置 `list_tickets` 映射时明确跳过(记录日志),不报错。

### 接入上游业务系统

平台经 MCP 从上游取数,上游需要按约定暴露以下工具(工具名可在
`mcp.tool_mapping` 中自行映射):

| 平台工具 | 用途 | 上游入参约定 |
|---|---|---|
| `search_customer` | 客户检索(识别身份) | `phone` / `customer_id` / `keyword` 至少一个 |
| `get_customer` | 客户资料(画像聚合) | `customer_id` |
| `get_order` | 订单查询 | `order_id` 或 `customer_id`(可选 `limit`) |
| `list_orders` | 客户订单列表(侧栏) | `customer_id`(可选 `limit`) |
| `get_product` | 商品查询 | `product_id` 或 `keyword`(可选 `limit`) |

返回结构约定:`search_customer → {"customers": [...]}`、
`get_customer → {"customer": {...}}`、`get_order → {"orders": [...]}`、
`list_orders → {"customer_id": ..., "total": n, "orders": [...]}`、
`get_product → {"products": [...]}`;字段名 snake_case / camelCase 均可。

- **v1 为查询能力**:退款/取消/建工单上游尚未暴露 MCP 工具,相应意图按配置
  路由到转人工(见 business.yaml intents 注释);上游后续暴露后,取消注释即可恢复。
- **联调方式**:直接打真实上游(配置 `UPSTREAM_MCP_URL` / `UPSTREAM_MCP_TOKEN`),
  按项目约定不使用 mock 数据;`/support/tools` 端点可查看当前工具与映射。

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
     (需在 `mcp.tool_mapping` 中配置 `resolve_token: upstream.resolve_token` 才启用)。
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
| 生成 | 与决策同一个 Agent | 独立生成层(chat 槽位),基于已查证结果 |
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
