# AGENTS.md

> 给在本仓库工作的 AI 编码代理的指引。先读本文,再读代码。
> 目标:让任何 agent 在 10 分钟内理解这个仓库的骨架、约定与扩展方式,改对地方。

## 一句话

通用客服平台:**ChatKit 管 UI,Jev 管判断,生成模型管表达,MCP 管数据,business.yaml 管配置**。
换一家公司的客服 = 改一个 YAML,不改业务代码。

```
用户消息 → Jev 决策(意图/情绪/置信度/动作/AGENT)
        → 出口判定:① AI CHAT(AI 自己办成)或 ② 人工客服(转接)
        → AI 出口内部:Jev 调度专职 AGENT(订单/商品/知识库/售后/通用,配置驱动)
          → MCP 取数 / RAG 查知识 / 直接回答,汇成「已查证信息」
          → 生成模型(chat 槽位)按 AGENT 人设流式产出话术
        → 垃圾/无关信息(garbage 意图):固化文案直回,不过生成模型
        → ChatKit 流式事件(文本增量 + Widget + 客户画像 + AGENT 调度副作用)
```

## 环境要求

- Python 3.11+、uv、Node 20+
- 必配环境变量(**缺失时服务拒绝启动,不会静默降级**):

| 变量 | 说明 |
|---|---|
| `JEV_PROVIDER` / `JEV_BASE_URL` / `JEV_API_KEY` / `JEV_MODEL` | Jev 决策模型(OpenAI 兼容端点;business.yaml 默认 https://api.typesafe.ai/v1/systemone) |
| `CHAT_PROVIDER` / `CHAT_BASE_URL` / `CHAT_API_KEY` / `CHAT_MODEL` | 话术生成模型(**不绑厂商**;默认 MiniMax https://api.minimax.cn/v1 / MiniMax-M3) |
| `UPSTREAM_MCP_URL` / `UPSTREAM_MCP_TOKEN` | MCP 数据面(上游业务系统 MCP,默认 http://127.0.0.1:9003/mcp) |

Jev 与 chat 是两个独立槽位,key 分开配置、互不影响(见 `backend/app/config/business.yaml` 的 `models` 段)。

**密钥输入位置:只有环境变量**(模板见 `backend/.env.example` / `frontend/.env.example`,
真实 `.env` 已被 git 忽略)。接入只需填 3 个值:`JEV_API_KEY`、`CHAT_API_KEY`、
`UPSTREAM_MCP_TOKEN`(其余在 business.yaml 均有默认值)。启动脚本
`backend/scripts/run-backend.sh`(bash)或 `run-backend.ps1`(Windows)会自动加载
`backend/.env`;也可用 `uv run --env-file`、Docker `--env-file`、K8s Secret 等平台
注入方式,优先级高于 .env。缺失即拒绝启动。

## 常用命令

```bash
# 一键启动(后端 :8001 + 前端 :5171)
npm run dev

# 后端单独
cd backend && uv sync --extra dev && uv run uvicorn app.main:app --port 8001

# 测试(28 个纯离线单元测试,约 1 秒,不依赖任何外部服务)
cd backend && uv run pytest tests/ -q

# Lint
cd backend && uv run ruff check app/ tests/

# 前端构建
cd frontend && npm install && npm run build
```

## 目录导览(先读这些文件)

```
backend/app/
├── server.py            ★ 编排层:respond/action 主链路,不含业务规则
├── main.py              ★ FastAPI 入口 + 全部 HTTP 端点
├── config.py            ★ business.yaml 加载 + 启动校验(fail-fast)
├── config/business.yaml ★★ 业务配置中心:换公司只改这里
├── core/
│   ├── jev 无关,见 ai/
│   ├── customer.py      客户域模型 + CustomerGateway(MCP 聚合,带缓存)
│   ├── routing.py       ★ 通用路由器:意图 → AGENT 调度 / MCP / RAG / 人工 / 固化直回
│   ├── policy.py        ★ 路由策略:置信度分档/情绪路由/确认门槛(全配置驱动)
│   ├── session.py       会话状态:身份绑定/流水/待确认动作(TTL)
│   └── conversation.py  会话记忆(供 Jev/Qwen 上下文)
├── ai/
│   ├── jev.py           ★ Jev 决策引擎(LLM,输出结构化 JSON,无本地降级)
│   ├── agents.py        ★ 专职 AGENT 注册表(配置驱动:人设/工具/是否查知识库)
│   ├── qwen.py          ★ chat 槽位流式生成(SSE,基于已查证信息 + AGENT 人设)
│   └── rag.py           RAG 编排
├── tools/               ★ 插件式工具层(pydantic 强校验 + MCP 映射)
├── knowledge/           FAQ 语料 + 向量检索(memory TF-IDF / chroma)
├── integrations/
│   └── mcp.py           ★★ 内嵌 MCP Client(http/stdio、重连、结果归一化)
└── widgets/             通用 Widget 模板(选择列表 / 确认卡片)
```

## 核心约定(改代码前必读)

1. **业务数据一律走 MCP**。不要在 `core/` 里写死任何行业逻辑(订单/航班/座位/餐食都不行)。
   平台通过 `integrations/mcp.py` 请求上游系统,只做字段归一化。
2. **模型缺失直接报错,禁止加"本地兜底/降级"**。`config.validate()` 在启动时拦截;
   Jev/chat 调用失败要显式抛错(`DecisionError`/`GenerationError`),由 server 转为
   `ErrorEvent` 让用户可见。不要为了"能跑"而加规则引擎兜底。
3. **Jev 与 chat 的配置永远分离**。新增模型槽位时沿用 `models.<slot>` 结构,
   各自独立的 base_url/api_key;chat 槽位不绑厂商(变量名是 CHAT_* 不是 QWEN_*)。
4. **新工具必须走 `ToolRegistry`**:pydantic 入参 + 明确输出契约
   (`{"result": str, "data": dict, "state_changed": bool}`),并在 business.yaml 的
   `tools` + `mcp.tool_mapping` 两处登记。
5. **路由旋钮进 YAML,不写死在代码里**:置信度分档、兜底动作、情绪路由、确认规则、
   转人工策略,全部在 `business.yaml` 的 `jev` / `rules` 段;专职 AGENT(人设/工具/
   是否查知识库)定义在 `agents` 段,意图到 AGENT 的映射在 `intents.<name>.agent`。
6. **ChatKit 事件流式协议**:文本用 `ThreadItemAddedEvent` →
   `ContentPartAdded` → `TextDelta`* → `ContentPartDone` → `ThreadItemDoneEvent`
   五件套(见 `server.py::_respond_inner`),不要只发一个 done 事件。
7. **前端零业务硬编码**:公司名/客服名/欢迎语/侧栏面板全部来自 `/support/bootstrap`,
   新增面板类型时前后端都要能 gracefully 处理未知 panel id。
8. **身份信号与身份数据分离**:客户“是谁”(customer_id/手机号/token)来自渠道或
   登录态,经请求头传入(`X-Customer-Id`/`X-Customer-Phone`/`X-Customer-Token`,
   见 `server.py::_bind_identity_from_request`),不要在代码里写死客户;
   “客户的资料/订单/工单”才走 MCP。匿名访客走对话内 `search_customer`。
9. **ChatKit 图标名是封闭集合**(`ChatKitIcon`,见 `@openai/chatkit` 类型):
   StartScreenPrompt 等的 icon 只能用集合内的值(package/truck/close 等不存在),
   且类型必须用 `StartScreenPrompt` 而非放宽的 `{icon: string}`,否则构建不报错、
   iframe 运行时白屏。
10. **mock 数据只允许出现在单元测试里**(项目约定)。单元测试可以用内联假数据
    (配置字典、FAQ 语料、解析样例);除此之外——联调、集成验证、端到端行为——
    一律打真实服务(真实 Jev/chat 端点、真实上游 MCP)。不要造 mock LLM、
    mock 上游 MCP 之类的测试替身,也不要在 `app/` 业务代码里留任何演示数据。

## 常见任务指南

### 加一个新工具(如 `query_invoice`)

1. `backend/app/tools/invoice.py`:pydantic 入参 + handler(经 `ctx.mcp` 调用上游);
2. `backend/app/tools/__init__.py::build_default_registry` 注册;
3. `business.yaml` 的 `tools` 列表 + `mcp.tool_mapping` 加映射;
4. 测试:`tests/test_units.py` 加纯单元覆盖;真实链路用真实上游验证(约定 #10)。

### 加一个新意图

只改 `business.yaml` 的 `intents` 段:`action` 指向已启用工具或 none,`agent` 指向
`agents` 段里的 AGENT(Jev 提示词自动生效);需要固化直回(如垃圾信息)时加
`direct_reply` 字段(支持 {agent_name}/{company_name} 占位符)。

### 加一个专职 AGENT(如 物流客服)

只改 `business.yaml` 的 `agents` 段:title / description(进 Jev 提示词)/
instructions(进生成模型系统提示,支持 {agent_name}/{company_name})/ tools /
needs_rag;需要时把相关意图的 `agent` 指向它。代码零改动,前端侧栏与
`/support/tools` 自动可见。

### 换一家公司/行业

改 `business.yaml`:`company`、`customer_service`、`panels`、`tools`、`intents`、
`mcp.servers`(新上游地址)、`mcp.tool_mapping`(指向新上游工具名)。代码零改动。

### 加一个侧栏面板(如 `devices` 设备列表)

1. 上游 MCP 提供数据 → `CustomerGateway` 加归一化方法 → profile 域模型加字段;
2. `business.yaml` 的 `panels` 加一项;
3. 前端 `components/customer-context/` 加 `DevicesView.tsx`,
   `CustomerContextPanel.tsx` 的 view 分支加一行。

### 切换向量库(chroma)

`business.yaml` 的 `knowledge.vector_store: chroma` + `pip install chromadb`,
其余不动(`knowledge/vector_store.py` 已预留实现)。

## 测试基建

- `tests/test_units.py`:唯一的测试文件,纯离线单元测试(约 1 秒):
  Jev 输出解析与配置校验(含 AGENT 字段)、AGENT 注册表与调度优先级、
  Router 垃圾信息直通、向量检索、Widget 模板、Policy、MCP 客户端配置。
- 按约定 #10,**不使用 mock 服务/ mock 数据做联调**;验证真实链路直接打
  真实 Jev/chat 端点 + 真实上游 MCP(配好 `.env` 三个 key 后 `npm run dev`,
  用 `/support/health`、`/support/tools` 自检)。

**新增功能必须带单元测试**;改路由策略必须覆盖对应置信度分档/情绪分支。

## 已知边界(改动前确认)

- ChatKit Store 为进程内 `MemoryStore`,多实例部署需换持久化 Store(实现 `chatkit.store.Store`)。
- 会话状态(身份绑定/待确认)为进程内,多实例需换 Redis 等共享存储。
- 语音听写走 OpenAI `gpt-4o-transcribe`,需 `OPENAI_API_KEY`;未配时听写不可用,文字对话不受影响。
- `transfer_to_human` 目前是平台内置能力(排队信息来自配置);若人工接入也走 MCP,
  把它改成 MCP 映射即可,保持全数据面统一。

## 提交规范

- 一个提交做一件事;提交信息说清"为什么",架构级改动附上影响的配置项。
- 提交前必须:`uv run ruff check app/ tests/` + `uv run pytest tests/ -q` 全绿。
