export type SupportView = string;

/** 侧栏面板配置(来自后端 /support/bootstrap,换行业只改 YAML) */
export type PanelConfig = {
  id: string;
  label: string;
};

/** 专职 AGENT 配置(来自 business.yaml 的 agents 段) */
export type AgentInfo = {
  name: string;
  title: string;
  description: string;
  tools: string[];
  needs_rag: boolean;
};

/** Jev 本次调度的 AGENT(来自 agent_dispatch/update 副作用) */
export type AgentDispatch = {
  agent: string;
  agent_title: string;
  intent: string;
  action: string;
  confidence: number;
  confidence_level: string;
  emotion: string;
  reason: string;
};

/** 流程中的一步(来自 pipeline_trace/update 副作用,ms 为本步耗时) */
export type TraceStep = {
  step: string;
  label: string;
  ms: number;
  detail?: string;
};

/** 一次用户消息的调用流程与耗时 */
export type PipelineTrace = {
  /** partial = 生成中(仅前几步);done = 完整 */
  status: "partial" | "done";
  total_ms: number;
  steps: TraceStep[];
};

/** 右侧「绑定用户」卡片文案(来自 business.yaml 的 customer_service.binding 段) */
export type BindingTexts = {
  title: string;
  input_placeholder: string;
  submit_label: string;
  unbind_label: string;
  hint: string;
  bound_hint: string;
};

export const DEFAULT_BINDING_TEXTS: BindingTexts = {
  title: "绑定用户",
  input_placeholder: "输入用户ID",
  submit_label: "绑定",
  unbind_label: "解绑",
  hint: "输入用户ID绑定后,右侧展示该用户档案,对话中只能查询其订单信息。",
  bound_hint: "当前会话仅可查询该用户的订单信息",
};

/** /support/bootstrap 返回的前端引导配置 */
export type BootstrapConfig = {
  company: { name: string; industry: string };
  customer_service: {
    name: string;
    language: string;
    greeting: string;
    composer_placeholder: string;
  };
  binding?: BindingTexts;
  panels: PanelConfig[];
  agents: AgentInfo[];
  default_agent: string;
};

export const DEFAULT_BOOTSTRAP: BootstrapConfig = {
  company: { name: "客服平台", industry: "generic" },
  customer_service: {
    name: "客服助手",
    language: "zh-CN",
    greeting: "您好,请问有什么可以帮您?",
    composer_placeholder: "输入你的问题…",
  },
  binding: DEFAULT_BINDING_TEXTS,
  panels: [
    { id: "overview", label: "概览" },
    { id: "orders", label: "订单" },
    { id: "tickets", label: "工单" },
  ],
  agents: [],
  default_agent: "",
};
