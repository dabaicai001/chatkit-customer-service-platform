import { Mail, Phone } from "lucide-react";
import clsx from "clsx";

import type { CustomerProfile } from "../hooks/useCustomerContext";
import type {
  AgentDispatch,
  AgentInfo,
  BindingTexts,
  PanelConfig,
  PipelineTrace,
  SupportView,
} from "../types/support";
import { AgentDispatchPanel } from "./customer-context/AgentDispatchPanel";
import { CustomerBindPanel } from "./customer-context/CustomerBindPanel";
import { OverviewView } from "./customer-context/OverviewView";
import { OrdersView } from "./customer-context/OrdersView";
import { PipelineTracePanel } from "./customer-context/PipelineTracePanel";
import { TicketsView } from "./customer-context/TicketsView";

type CustomerContextPanelProps = {
  profile: CustomerProfile | null;
  loading: boolean;
  error: string | null;
  /** 侧栏面板(来自后端 bootstrap 配置,换行业只改 YAML) */
  panels: PanelConfig[];
  view: SupportView;
  onViewChange: (view: SupportView) => void;
  /** 专职 AGENT 列表与默认 AGENT(来自后端 bootstrap) */
  agents: AgentInfo[];
  defaultAgent: string;
  /** Jev 本次调度的 AGENT */
  dispatch: AgentDispatch | null;
  /** 本次消息的调用流程与耗时(生成中为 partial) */
  trace: PipelineTrace | null;
  /** 当前 ChatKit 会话(绑定用户按会话隔离) */
  threadId: string | null;
  /** 绑定/解绑成功后刷新右侧画像 */
  onBindingChange: () => void;
  /** 「绑定用户」卡片文案(来自 business.yaml,换行业只改 YAML) */
  bindingTexts?: BindingTexts;
};

const PANEL_CLASS =
  "flex h-full flex-col overflow-hidden rounded-3xl border border-slate-200/60 bg-white/80 p-6 shadow-[0_45px_90px_-45px_rgba(15,23,42,0.5)] ring-1 ring-slate-200/60 backdrop-blur dark:border-slate-800/70 dark:bg-slate-900/70 dark:shadow-[0_45px_95px_-55px_rgba(15,23,42,0.85)] dark:ring-slate-800/60";

/** 通用客户侧栏:面板按配置动态渲染,内容按行业无关的画像结构展示 */
export function CustomerContextPanel({
  profile,
  loading,
  error,
  panels,
  view,
  onViewChange,
  agents,
  defaultAgent,
  dispatch,
  trace,
  threadId,
  onBindingChange,
  bindingTexts,
}: CustomerContextPanelProps) {
  const bound = profile
    ? { customer_id: profile.customer_id, name: profile.name }
    : null;
  const bindPanel = (
    <CustomerBindPanel
      threadId={threadId}
      bound={bound}
      onBindingChange={onBindingChange}
      texts={bindingTexts}
    />
  );

  if (loading && !profile) {
    return (
      <section className={PANEL_CLASS}>
        <header>
          <h2 className="text-xl font-semibold text-slate-800 dark:text-slate-100">
            客户信息
          </h2>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-300">
            正在加载客户数据…
          </p>
        </header>
        <div className="flex flex-1 items-center justify-center">
          <span className="text-sm text-slate-500 dark:text-slate-400">
            正在从业务系统拉取客户画像…
          </span>
        </div>
      </section>
    );
  }

  if (error) {
    return (
      <section className="flex h-full flex-col gap-4 rounded-3xl border border-rose-200 bg-rose-50/60 p-6 text-rose-700 shadow-sm dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-200">
        <header>
          <h2 className="text-xl font-semibold">客户信息</h2>
        </header>
        <p className="text-sm">{error}</p>
      </section>
    );
  }

  if (!profile) {
    return (
      <section className={PANEL_CLASS}>
        <header>
          <h2 className="text-xl font-semibold text-slate-800 dark:text-slate-100">
            客户信息
          </h2>
        </header>
        <div className="mt-5 flex-1 overflow-y-auto pr-1 space-y-6">
          {bindPanel}
          <p className="text-sm text-slate-500 dark:text-slate-400">
            输入用户ID绑定后,客服会自动识别客户身份并在此展示档案、订单与工单。
          </p>
          <AgentDispatchPanel
            agents={agents}
            defaultAgent={defaultAgent}
            dispatch={dispatch}
          />
          <PipelineTracePanel trace={trace} />
        </div>
      </section>
    );
  }

  const headerContent = (
    <header className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-[0.25em] text-blue-500 dark:text-blue-300">
            客户档案
          </p>
          <h2 className="text-2xl font-semibold text-slate-800 dark:text-slate-100">
            {profile.name}
          </h2>
          <div className="mt-1 flex flex-wrap items-center gap-3 text-sm text-slate-600 dark:text-slate-300">
            {profile.email && (
              <span className="inline-flex items-center gap-1.5">
                <Mail className="h-4 w-4" aria-hidden />
                {profile.email}
              </span>
            )}
            {profile.phone && (
              <span className="inline-flex items-center gap-1.5">
                <Phone className="h-4 w-4" aria-hidden />
                {profile.phone}
              </span>
            )}
          </div>
        </div>
        <div className="rounded-2xl border border-blue-200 bg-blue-50 px-4 py-2 text-sm font-medium text-blue-700 dark:border-blue-900/60 dark:bg-blue-950/30 dark:text-blue-200">
          {profile.level} · {profile.customer_id}
        </div>
      </div>
      {panels.length > 1 && (
        <nav className="flex flex-wrap gap-2 text-sm">
          {panels.map((panel) => (
            <button
              key={panel.id}
              type="button"
              onClick={() => onViewChange(panel.id)}
              className={clsx(
                "rounded-full px-4 py-2 font-medium transition",
                view === panel.id
                  ? "bg-slate-900 text-white shadow-lg dark:bg-slate-100 dark:text-slate-900"
                  : "bg-slate-100 text-slate-500 hover:text-slate-800 dark:bg-slate-900/70 dark:text-slate-400"
              )}
            >
              {panel.label}
            </button>
          ))}
        </nav>
      )}
    </header>
  );

  let bodyContent = null;
  if (view === "orders") {
    bodyContent = (
      <OrdersView orders={profile.orders} total={profile.orders_total} />
    );
  } else if (view === "tickets") {
    bodyContent = <TicketsView tickets={profile.tickets} />;
  } else {
    bodyContent = <OverviewView profile={profile} />;
  }

  return (
    <section className={PANEL_CLASS}>
      {headerContent}
      <div className="mt-5 flex-1 overflow-y-auto pr-1 space-y-6">
        {bindPanel}
        {bodyContent}
        <AgentDispatchPanel
          agents={agents}
          defaultAgent={defaultAgent}
          dispatch={dispatch}
        />
        <PipelineTracePanel trace={trace} />
      </div>
    </section>
  );
}
