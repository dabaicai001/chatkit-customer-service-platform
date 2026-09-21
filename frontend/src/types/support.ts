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

/** /support/bootstrap 返回的前端引导配置 */
export type BootstrapConfig = {
  company: { name: string; industry: string };
  customer_service: {
    name: string;
    language: string;
    greeting: string;
    composer_placeholder: string;
  };
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
  panels: [
    { id: "overview", label: "概览" },
    { id: "orders", label: "订单" },
    { id: "tickets", label: "工单" },
  ],
  agents: [],
  default_agent: "",
};
