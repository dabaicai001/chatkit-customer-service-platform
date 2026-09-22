# AGENTS.md

> 给在本仓库工作的 AI 编码代理的指引。先读本文,再读代码。
> 目标:让任何 agent 在 10 分钟内理解这个仓库的骨架、约定与扩展方式,改对地方。

## 一句话

通用客服平台:**ChatKit 管 UI,Jev 管判断(含推荐函数),模型经 function calling 直连 MCP 办事,MCP 管数据,business.yaml 管配置**。
换一家公司的客服 = 改一个 YAML,不改业务代码。

```
用户消息 → Jev 决策(意图/情绪/置信度/AGENT/推荐调用的函数,一次 SystemOne 调用)
        → 垃圾/无关信息(garbage 意图):固化文案直回,不调工具、不过生成模型
        → AGENT 调度(Jev 指定 > 意图映射 > 默认),人设进系统提示
        → 智能体循环(chat 槽位,OpenAI 兼容 function calling):
            模型输出 tool_calls(函数名 + 参数)
              → 代码侧经 ToolRegistry 执行(pydantic 校验 + 剥离模型给的
                customer_id + MCP 数据面;模型永远碰不到 MCP 连接)
              → 工具结果以 tool 消息回灌(≤ business.yaml agent.max_tool_rounds 轮)
            直到模型不再调用工具,基于事实流式产出最终话术
        → 变更类/需确认工具(退款/取消/建工单)不开放给模型,走人工流程
        → ChatKit 流式事件(文本增量 + 客户画像/AGENT 调度/流程耗时副作用)
```

## 环境要求

- Python 3.11+、uv、Node 20+
- 必配环境变量(**缺失时服务拒绝启动,不会静默降级**):

| 变量 | 说明 |
|---|---|
| `JEV_PROVIDER` / `JEV_BASE_URL` / `JEV_API_KEY` / `JEV_MODEL` | Jev 决策模型(TypeSafe SystemOne,`POST /v1/systemone`,非 OpenAI Chat Completions;business.yaml 默认 https://api.typesafe.ai) |
| `CHAT_PROVIDER` / `CHAT_BASE_URL` / `CHAT_API_KEY` / `CHAT_MODEL` | 话术生成模型(**不绑厂商**;默认 MiniMax https://api.minimax.cn/v1 / MiniMax-M3)。**端点必须支持 OpenAI 兼容 function calling(`tools` 参数)**——模型经它直连 MCP |
| `UPSTREAM_MCP_URL` / `UPSTREAM_MCP_TOKEN` | MCP 数据面(上游业务系统 MCP,默认 http://127.0.0.1:9003/mcp) |

Jev 与 chat 是两个独立槽位,key 分开配置、互不影响(见 `backend/app/config/business.yaml` 的 `models` 段)。
Jev 是 TypeSafe SystemOne 协议(choice 问答,不涉及 tools);function calling 只发生在 chat 槽位。

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

# 测试(70 个纯离线单元测试,约 4 秒,不依赖任何外部服务)
cd backend && uv run pytest tests/ -q

# Lint
cd backend && uv run ruff check app/ tests/

# 前端构建
cd frontend && npm install && npm run build
```

## 目录导览(先读这些文件)

```
backend/app/
├── server.py            ★ 编排层:respond 主链路(Jev → function calling 循环),不含业务规则
├── main.py              ★ FastAPI 入口 + 全部 HTTP 端点
├── config.py            ★ business.yaml 加载 + 启动校验(fail-fast)
├── config/business.yaml ★★ 业务配置中心:换公司只改这里
├── core/
│   ├── jev 无关,见 ai/
│   ├── customer.py      客户域模型 + CustomerGateway(MCP 聚合,带缓存)
│   ├── routing.py       ★ AGENT 调度 + 垃圾意图固化直回解析(纯函数,不发请求)
│   ├── policy.py        ★ 路由策略:置信度分档/情绪路由/确认门槛(全配置驱动)
│   ├── session.py       会话状态:身份绑定/流水/待确认动作(TTL)
│   └── conversation.py  会话记忆(供 Jev 决策与模型上下文)
├── ai/
│   ├── jev.py           ★ Jev 决策引擎(SystemOne choice 问答:意图/情绪/AGENT/推荐函数,无本地降级)
│   ├── mcp_agent.py     ★ 模型直连 MCP:function calling 循环(SSE 解析 + tool_calls 累积)
│   ├── agents.py        ★ 专职 AGENT 注册表(配置驱动:人设/工具/是否查知识库)
│   ├── qwen.py          chat 槽位公共件:思维链过滤 + 会话标题生成
│   └── rag.py           RAG 编排(经 query_knowledge 工具被模型调用)
├── tools/               ★ 插件式工具层(pydantic 强校验 + MCP 映射;变更类不开放给模型)
├── knowledge/           FAQ 语料 + 向量检索(memory TF-IDF / chroma)
├── integrations/
│   └── mcp.py           ★★ 内嵌 MCP Client(http/stdio、重连、结果归一化)
```

## 核心约定(改代码前必读)

1. **业务数据一律走 MCP;模型只"发起调用",执行永远在代码侧**。不要在 `core/` 里写死任何行业逻辑(订单/航班/座位/餐食都不行)。
   平台通过 `integrations/mcp.py` 请求上游系统,只做字段归一化。function calling 的
   tool_calls 由 `server._execute_tool_call` 经 `ToolRegistry` 执行(pydantic 校验 +
   剥离模型给的 `customer_id`),模型永远碰不到 MCP 连接。
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
9. **变更类工具不开放给模型**。`mutating=True` 或被 `rules.<action>.require_confirmation`
   标记的工具(退款/取消/建工单):不进 Jev 的 action 推荐候选(见
   `server._selectable_action_catalog`),不进模型的 tools 列表(见 `server._model_tools`),
   由人工流程办理。将来恢复确认卡片时,再通过 ChatKit action 链路接入,不要直接
   开放给 function calling。
10. **mock 数据只允许出现在单元测试里**(项目约定)。单元测试可以用内联假数据
    (配置字典、FAQ 语料、解析样例);除此之外——联调、集成验证、端到端行为——
    一律打真实服务(真实 Jev/chat 端点、真实上游 MCP)。不要造 mock LLM、
    mock 上游 MCP 之类的测试替身,也不要在 `app/` 业务代码里留任何演示数据。

## 常见任务指南

### 加一个新工具(如 `query_invoice`)

1. `backend/app/tools/invoice.py`:pydantic 入参 + handler(经 `ctx.mcp` 调用上游);
2. `backend/app/tools/__init__.py::build_default_registry` 注册;
3. `business.yaml` 的 `tools` 列表 + `mcp.tool_mapping` 加映射;
4. 注册后自动成为:Jev 的 action 推荐候选 + 模型的 function calling 工具
   (非变更、免确认时——见约定 #9),无需改 server;
5. 测试:`tests/test_units.py` 加纯单元覆盖;真实链路用真实上游验证(约定 #10)。

### 加一个新意图

只改 `business.yaml` 的 `intents` 段:`action` 是该意图的默认函数(Jev 直选
缺失/非法时的兜底,不影响模型发起调用),`agent` 指向 `agents` 段里的 AGENT
(Jev 提示词自动生效);需要固化直回(如垃圾信息)时加 `direct_reply` 字段
(支持 {agent_name}/{company_name} 占位符)。

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

- `tests/test_units.py`:唯一的测试文件,纯离线单元测试(约 4 秒):
  Jev 输出解析与配置校验(含 AGENT 字段、action 推荐函数)、AGENT 注册表与调度
  优先级、垃圾意图固化直回、function calling SSE 解析与 tool_calls 累积
  (含碎片拼接/残缺 JSON)、模型工具调用的身份剥离、向量检索、Policy、
  MCP 客户端配置、chat 槽位思维链过滤。
- 按约定 #10,**不使用 mock 服务/ mock 数据做联调**;验证真实链路直接打
  真实 Jev/chat 端点 + 真实上游 MCP(配好 `.env` 三个 key 后 `npm run dev`,
  用 `/support/health`、`/support/tools` 自检)。

**新增功能必须带单元测试**;改路由策略必须覆盖对应置信度分档/情绪分支。

## 已知边界(改动前确认)

- ChatKit Store 为进程内 `MemoryStore`,多实例部署需换持久化 Store(实现 `chatkit.store.Store`)。
- 会话状态(身份绑定/待确认)为进程内,多实例需换 Redis 等共享存储。
- chat 槽位端点必须支持 OpenAI 兼容 function calling(`tools` 参数);不支持时
  请求显式报错(按约定 #2 不静默降级),换支持的模型/网关即可。
- 语音听写走 OpenAI `gpt-4o-transcribe`,需 `OPENAI_API_KEY`;未配时听写不可用,文字对话不受影响。
- `transfer_to_human` 目前是平台内置能力(排队信息来自配置);若人工接入也走 MCP,
  把它改成 MCP 映射即可,保持全数据面统一。

## 提交规范

- 一个提交做一件事;提交信息说清"为什么",架构级改动附上影响的配置项。
- 提交前必须:`uv run ruff check app/ tests/` + `uv run pytest tests/ -q` 全绿。
