# 通用客服平台(Customer Service Platform)

> GitHub:[github.com/dabaicai001/jeves-desk](https://github.com/dabaicai001/jeves-desk)

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
                                │ 流式文本 / 画像 / AGENT 调度 / 流程耗时副作用
                     ┌──────────┴──────────┐
                     │  Customer Gateway   │  ← server.py(编排,不含业务规则)
                     └──────────┬──────────┘
                                │
                     ┌──────────▼──────────┐
                     │        Jev          │  ← ai/jev.py(大脑:意图/情绪/置信度/
                     │      Decision       │     AGENT/推荐调用的函数,一次调用)
                     └──────────┬──────────┘
                                │ 决策 + 推荐函数
                     ┌──────────▼──────────┐
                     │   模型直连 MCP      │  ← ai/mcp_agent.py(function calling)
                     │  tool_calls 循环    │    模型出函数+参数;代码侧执行:
                     └──────────┬──────────┘     pydantic 校验 / 剥离伪造的
                                │                customer_id / MCP 数据面
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                 ▼
        ┌───────────┐     ┌───────────┐     ┌───────────┐
        │ 订单客服   │     │ 商品顾问   │     │ 知识库客服 │ …(AGENT 人设进系统提示)
        └─────┬─────┘     └─────┬─────┘     └─────┬─────┘
              │                 │                 │
              └─────────────────┼─────────────────┘
                                ▼
                     ┌─────────────────────┐
                     │  工具结果回灌(tool)  │  模型基于事实继续,直到产出最终话术
                     └──────────┬──────────┘
                                ▼
                        ┌──────────┐
                        │ 流式话术  │  ← chat 槽位(MiniMax-M3 等,function calling)
                        └──────────┘

    垃圾/无关信息(Jev 判定 intent=garbage):直接固化友好回复,不调工具、不过生成模型
    变更类/需确认的工具(退款/取消/建工单):不开放给模型,由人工流程办理
```

**出口只有两个**:

1. **AI CHAT** — AI 把事办成:Jev 决策并推荐函数;话术模型带 MCP 工具定义直接
   发起调用(function calling),代码侧经 MCP 执行并把结果回灌,模型基于事实
   按 AGENT 人设流式产出话术;
2. **人工客服** — 转接:用户明确要求、情绪愤怒、或上游未暴露的变更类操作
   (退款/取消/建工单)时,由人工接管(转接由代码执行 `transfer_to_human`,
   话术由模型基于执行结果组织)。

**专职 AGENT(配置驱动)**:`business.yaml` 的 `agents` 段定义每个 AGENT 的人设
提示词与可用工具;Jev 输出决策时指定 `agent`,前端右侧面板实时展示 Jev 的选择
(`agent_dispatch/update` 副作用)。新增/调整 AGENT 只改 YAML。

- **ChatKit = UI**:官方 SDK 串起聊天、流式、附件、听写,不重造轮子。
- **Jev = 判断器 + 推荐员**:小模型输出结构化决策(意图/情绪/置信度/**推荐函数**/AGENT),不生成回答。
- **模型直连 MCP = 办事的人**:chat 槽位模型经 function calling 发起工具调用(函数+参数都由模型给出),代码侧校验执行、结果回灌。
- **专职 AGENT = 办案小组**:订单/商品/知识库/售后/通用,配置驱动,Jev 调度。
- **MCP = 数据面**:客户/订单/商品/工单全部经内嵌 MCP Client 请求上游系统,平台不直连业务库。
- **RAG = 公司知识库**:内置纯 Python 向量检索(可切换 chroma),以 `query_knowledge` 工具形态被模型调用。

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
│   └── public/
│       ├── chatkit/                       # ChatKit UI 本地化:loader + iframe HTML
│       └── assets/ck1/                    # iframe 主包/CSS/字体/1639 个图标 chunk
│                                         # (以上为 cdn.platform.openai.com 快照,共 ~7.4MB,
│                                         #  国内网络免翻墙;升级 = 重新覆盖这些文件)
└── backend/
    ├── pyproject.toml
    ├── scripts/run-backend.sh
    ├── tests/                             # 70 个纯离线单元测试(无 mock 服务)
    └── app/
        ├── main.py                        # FastAPI 入口
        ├── server.py                      # Customer Gateway(编排层)
        ├── config.py                      # business.yaml 加载 + 启动校验
        ├── config/business.yaml           # ★ 业务配置中心(换公司只改它)
        ├── core/
        │   ├── customer.py                # 客户域模型 + MCP 数据网关
        │   ├── conversation.py            # 会话记忆
        │   ├── policy.py                  # 路由策略(置信度/情绪/确认门槛)
        │   ├── routing.py                 # AGENT 调度 + 固化直回解析(纯函数)
        │   └── session.py                 # 会话状态(身份绑定/流水/待确认)
        ├── ai/
        │   ├── jev.py                     # Jev 决策引擎(含 action 推荐函数)
        │   ├── mcp_agent.py               # 模型直连 MCP(function calling 循环)
        │   ├── qwen.py                    # chat 槽位公共件(思维链过滤/标题)
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
#   JEV_API_KEY=...        # Jev 决策模型(默认 https://api.typesafe.ai,端点 POST /v1/systemone)
#   CHAT_API_KEY=...       # 话术生成模型(默认 MiniMax: https://api.minimax.cn/v1,模型 MiniMax-M3)
#   UPSTREAM_MCP_TOKEN=... # 上游业务系统 MCP 的 auth token(URL 按实际改)
cp frontend/.env.example frontend/.env   # 填真实 ChatKit 域名密钥(见下方注意项)
```

chat 槽位不绑死厂商(Qwen/GPT/DeepSeek/MiniMax 均可,只要是 OpenAI 兼容端点且
支持 function calling),换厂商只改 `CHAT_BASE_URL` + `CHAT_MODEL` 两个值。

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
cd backend && uv sync --extra dev && uv run pytest tests/ -q     # 61 passed
```

测试为**纯离线单元测试**(约 4 秒):不依赖任何外部服务,也不需要 mock 服务。
按项目约定,mock 数据只允许出现在单元测试里;集成/端到端行为请直接打真实服务
(真实 Jev/chat 端点 + 真实上游 MCP)验证。

## 准生产/生产接入:模型 key 在哪里输入

**唯一入口:环境变量**(代码与 `business.yaml` 里不含任何密钥)。
完整清单见 `backend/.env.example` 与 `frontend/.env.example`,复制后填真实值:

| 变量 | 必填 | 说明 |
|---|---|---|
| `JEV_PROVIDER` / `JEV_BASE_URL` / `JEV_API_KEY` / `JEV_MODEL` | ✅ key | Jev 决策模型(TypeSafe SystemOne,`POST /v1/systemone`);默认 `https://api.typesafe.ai` |
| `CHAT_PROVIDER` / `CHAT_BASE_URL` / `CHAT_API_KEY` / `CHAT_MODEL` | ✅ key | 话术生成模型(不限厂商);默认 MiniMax `https://api.minimax.cn/v1` / `MiniMax-M3`。**需支持 OpenAI 兼容 function calling(`tools` 参数)** |
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
curl http://<host>:8001/support/tools     # 确认 jev/chat 槽位显示真实 provider/model、agent.max_tool_rounds、mcp 映射齐全
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

每条用户消息先由 Jev 输出结构化决策(Jev 是 TypeSafe SystemOne 的 choice 问答,
一次调用问四个问题:意图 / 情绪 / AGENT / **推荐调用的函数**):

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
  "action_source": "jev",
  "agent": "order_agent",
  "reason": "用户在查订单物流"
}
```

- `action` = **推荐调用的函数**(工具名)。候选 = 已启用 ∩ 非变更类 ∩ 免确认的
  工具(见 `server._selectable_action_catalog`);Jev 直选非法/缺失时回退到
  `intents.<name>.action` 配置映射,`action_source` 记录来源(`jev`/`config`);
- **参数不在 Jev 产生**:函数入参由话术模型在 function calling 里自然给出
  (订单号/手机号/商品名…),本层不做任何规则抽取。

路由策略全部可在 `business.yaml` 的 `jev` 段调整:

```yaml
jev:
  confidence:
    high: 0.85      # ≥ high:推荐作为强引导
    low: 0.55       # < low:系统提示中注明推荐仅供参考
  action_recommendation: true   # Jev 直选推荐函数;false = 完全走 intents 配置映射
  fallback_action: query_knowledge
  emotion_routing:  # 情绪 → 路由覆盖( anger/frustrated 强制转人工,先于模型执行)
    angry: transfer_to_human
    anxious: none   # none = 不改道,由生成模型话术安抚
  retry:
    attempts: 2
    backoff_seconds: 0.5
```

### 模型直连 MCP:function calling(ai/mcp_agent.py)

Jev 给出推荐函数后,由 chat 槽位模型带 `tools` 定义(OpenAI 兼容 function
calling)发起真正的调用,循环直到产出最终话术:

```
messages(system:AGENT 人设 + 客户画像 + Jev 决策 + 硬性规则;+ 历史 + 用户消息)
  └─ 模型流式输出 ── content:实时吐字(经思维链过滤)
                   └─ tool_calls:代码侧执行后回灌,进入下一轮(≤ agent.max_tool_rounds)
                        ├─ pydantic 强校验入参
                        ├─ 剥离模型给出的 customer_id(身份只来自会话绑定,防越权)
                        └─ McpClientManager 请求上游 MCP
```

关键不变量:

- **模型永远碰不到 MCP 连接**:工具调用全部由代码经 `ToolRegistry` 执行
  (校验 + 身份注入 + MCP),与「Jev 只推荐、代码才执行」一致;
- **工具结果回灌为 `role: "tool"` 消息**,模型基于事实继续;工具失败(参数非法/
  上游报错)也作为 tool 消息回灌,模型可基于错误信息追问客户,会话不崩;
- **只开放安全工具给模型**:已启用 ∩ 非变更类 ∩ 免确认(`server._model_tools`);
  退款/取消/建工单等变更动作不允许模型直调,走人工流程;
- **多轮有上限**:`business.yaml` 的 `agent.max_tool_rounds`(默认 4)防无限循环;
- **硬性要求**:chat 槽位端点必须支持 OpenAI 兼容 `tools` 参数
  (MiniMax-M3 / Qwen / DeepSeek / GPT 系均可);不支持时按约定显式报错,
  不静默降级。

### 专职 AGENT(ai/agents.py,配置驱动)

AI CHAT 出口内部按职责细分多个专职 AGENT,Jev 决策时指定调度哪个。
每个 AGENT 的完整定义在 `business.yaml` 的 `agents` 段——人设提示词、可用工具
全部可配,新增/调整只改 YAML:

```yaml
agents:
  default: chat_agent                # Jev 无法判断时的兜底
  order_agent:
    title: 订单客服
    description: 订单状态、物流进度查询      # 进 Jev 提示词,供其调度
    instructions: |                   # 进模型系统提示词(人设/话术约束)
      你是{agent_name},{company_name}的订单客服…
    tools: [get_order, search_customer]
    needs_rag: false
  knowledge_agent:
    title: 知识库客服
    description: 政策、发票、保修等通用问题
    instructions: 你是{agent_name},依据公司知识库回答…
    tools: [query_knowledge]
    needs_rag: true                   # 该 AGENT 处理时建议查知识库
```

- **调度优先级**:Jev 指定的 `agent` > 意图目录里该意图的 `agent` 映射 > `agents.default`;
- **生效方式**:AGENT 的 `instructions`(占位符 `{agent_name}`/`{company_name}`)替换通用
  人设进入模型系统提示(`server._render_agent_instructions`);
- **前端可见**:`agent_dispatch/update` 副作用把「Jev 选择了哪个 AGENT + 意图/置信度/
  情绪/判断依据」实时推到右侧面板,AGENT 列表来自 `/support/bootstrap`;
- **运维可见**:`/support/tools` 返回全部 AGENT 定义与默认 AGENT。

### 垃圾/无关信息直通

意图目录里配了 `direct_reply` 的意图(默认的 `garbage` 意图即用于广告、骚扰、
乱码、与业务无关的内容)走**直通通道**:Jev 判定后直接回复固化文案,
**不调工具、不经过生成模型**(省钱且不让模型陪垃圾话)。文案支持
`{agent_name}`/`{company_name}` 占位符:

```yaml
intents:
  garbage:
    action: none
    agent: chat_agent
    description: 垃圾信息或与业务无关的内容,直接友好回复
    direct_reply: |
      您好,我是{agent_name},只能为您解答订单、商品、售后相关的问题…
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

### 侧栏:调用流程与耗时

每次消息的完整链路(身份绑定 → 客户画像 → Jev 决策 → 模型调用 MCP × N → 话术生成)
以 `pipeline_trace/update` 客户端副作用推送到右侧面板,分步展示耗时(Jev/每次
工具调用/首 token 时间)与相对占比,`partial`(生成中)→ `done`(完整)两段刷新。

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
  路由到转人工(见 business.yaml intents 注释);上游后续暴露后,取消注释即可恢复
  (变更类工具恢复后也不会开放给模型直调,走人工/确认流程);
- **联调方式**:直接打真实上游(配置 `UPSTREAM_MCP_URL` / `UPSTREAM_MCP_TOKEN`),
  按项目约定不使用 mock 数据;`/support/tools` 端点可查看当前工具与映射。

### 换一家公司的客服 = 改 YAML

`backend/app/config/business.yaml` 一站式配置:公司名、客服人设、语言、欢迎语、
侧栏面板、启用工具、意图目录、Jev 路由参数、模型工具调用轮数上限、转人工策略、
MCP 连接与映射。前端启动时从 `/support/bootstrap` 拉取这套配置渲染,无需改前端代码。

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
   即默认已实现的行为(模型调用 `search_customer` 时由 handler 完成绑定)。

请求头由 `server.py::_bind_identity_from_request` 处理,绑定后客户画像、模型
系统提示中的客户资料、后续工具调用全部自动就绪。

### 绑定用户(侧栏手动绑定)

生产渠道身份未知或需要客服手动指定服务对象时(如线下扫码进线、后台代客查询),
右侧「客户信息」面板顶部提供**绑定用户**卡片:

1. 输入用户ID → 点「绑定」;
2. 平台先经 MCP `get_customer` 按该 ID 查出用户(不存在则明确报错,不静默降级),
   然后写入当前会话的客户上下文;
3. 右侧立即**回显该用户档案**(姓名/手机号/等级/订单/工单),模型系统提示中的
   客户资料、后续工具调用全部以该用户为准;
4. **绑定后只能查询该用户的订单信息**:订单查询会带上绑定用户的 ID 交由上游
   校验归属,查别人的订单号会被上游拒绝;客户检索也被限定为该用户,防止越过
   绑定身份查其他客户;
5. 绑定是**工作台级**的:之后新开的对话线程自动继承该身份,客服无需重复绑定
   (请求头 `X-Customer-Id` 等按线程传入的登录态身份优先于继承);
6. 点「解绑」清除绑定(含继承),恢复匿名访客模式。

接口(前端面板调用,也可供渠道系统集成):

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/support/bind` | body `{"thread_id": "...", "customer_id": "123"}`,返回该用户档案;用户不存在返回 400 |
| DELETE | `/support/bind?thread_id=...` | 解除该会话绑定 |

绑定按 ChatKit thread 隔离(未开始对话时用默认会话);会话状态为进程内,多实例
部署需换 Redis 等共享存储。

卡片文案(标题/输入框占位符/按钮/提示语)全部来自 `business.yaml` 的
`customer_service.binding` 段,经 `/support/bootstrap` 下发——换行业(如医疗把
"输入用户ID"改成"输入患者ID")只改 YAML,前端零硬编码。

### 多用户隔离(会话与数据两层)

| 层 | 隔离方式 |
|---|---|
| 会话状态 | 按 ChatKit thread 隔离:客户绑定、画像缓存、待确认动作、操作流水都是 per-thread(`SessionStateManager._states`) |
| 数据面 | 每次 MCP 调用**显式带当前线程绑定的 customer_id**,由上游校验归属;与连接/会话无关,换连接不换身份 |
| 模型侧 | 模型参数里的 `customer_id` 一律被代码剥离,身份只来自会话绑定(防模型伪造越权) |
| 请求头身份 | `X-Customer-Id`/`X-Customer-Phone`/`X-Customer-Token` 按线程绑定,优先于工作台默认绑定 |
| 上游会话绑定 | `bind_customer`/`unbind_customer` 按 MCP sessionID 隔离;**平台刻意不用它**(单连接会被多用户踩),只作为直连 MCP 客户端的能力 |

一个注意点:右侧「绑定用户」是**工作台级**的(新对话自动继承,见
`session.inherit_default_binding`)。单客服工作台默认开启(客服不用每个新对话
重绑同一客户);**多租户/匿名用户与客服同实例部署时改成 `false`**——匿名会话
绝不会继承客服当前绑定的客户身份,请求头身份按线程显式绑定,彻底隔离。

**上游 MCP 侧契约(数据面按此实现即自动获得完整语义)**:`get_order` 在同时收到
`order_id` + `customer_id` 时校验订单归属(不属于该客户直接报错);`list_orders` /
`get_customer` / `search_customer` 的客户维度查询以传入的 `customer_id` 为准。
若上游另提供 `bind_customer` / `unbind_customer` 会话级绑定工具(绑定后
`customer_id` 可省略、传别的用户 ID 被拒绝),则 MCP 客户端侧也具备同样的
绑定语义;平台侧不依赖该工具,显式传 `customer_id` 已可完成归属校验。
订单记录里带商品明细(如 `goods: [{name, spec, qty, unitPrice, price}]`)时,
平台归一化进 `Order.goods`(`core/customer.py`),随工具结果回灌给模型——
客户问「这个订单买了什么商品」才能答出商品名/规格/数量/单价,而不是只会
重复订单摘要(订单摘要字段可能是商家名,不等于商品)。

## 与原官方示例的差异

| 维度 | 官方 customer-support | 本平台 |
|---|---|---|
| 业务 | 航空(航班/座位/行李/餐食写死) | 通用(客户/订单/商品/工单) |
| 状态 | AirlineStateManager(进程内种子数据) | MCP 实时请求上游 + 会话状态分离 |
| 决策 | gpt-4.1-mini Agent 自带工具调用 | Jev 结构化决策(含推荐函数)+ 代码侧执行(可控/可观测) |
| 生成 | 与决策同一个 Agent | chat 槽位 function calling:模型出调用,代码执行并回灌,模型基于事实产出 |
| 工具 | 6 个航空工具写死在 Agent | 插件式 ToolRegistry,pydantic 校验,MCP 映射;变更类不开放给模型 |
| 组件 | 航班选择/餐食选择 | 无 Widget(纯对话 + 侧栏面板) |
| 侧栏 | 航班/行程/会员 | 客户档案/订单/工单(面板按配置渲染) |
| 配置 | 散落在代码 | business.yaml 配置中心 |
| 身份 | thread 内写死种子客户 | 请求头/渠道/对话内识别三条路径,MCP 取数 |

## 已知边界

- ChatKit Store 为进程内 MemoryStore(官方示例复用),多实例部署需换持久化 Store;
- 会话状态(身份绑定/待确认)为进程内,多实例部署需换 Redis 等共享存储;
- chat 槽位端点必须支持 OpenAI 兼容 function calling(`tools` 参数);不支持时
  请求会显式报错(按约定不静默降级),换支持的模型/网关即可;
- 语音听写(transcribe)仍走 OpenAI `gpt-4o-transcribe`,未配置 `OPENAI_API_KEY` 时
  听写不可用,不影响文字对话;
- 知识库默认为内存 TF-IDF 检索,`knowledge.vector_store: chroma` 可切换(需装 chromadb)。
