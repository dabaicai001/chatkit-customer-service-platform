import { Bot, Cpu } from "lucide-react";
import clsx from "clsx";

import type { AgentDispatch, AgentInfo } from "../../types/support";

type AgentDispatchPanelProps = {
  /** 全部专职 AGENT(来自 /support/bootstrap) */
  agents: AgentInfo[];
  /** 默认 AGENT 名 */
  defaultAgent: string;
  /** Jev 本次调度的 AGENT(来自 agent_dispatch/update 副作用) */
  dispatch: AgentDispatch | null;
};

const LEVEL_TONE: Record<string, string> = {
  high: "text-emerald-500",
  medium: "text-amber-500",
  low: "text-rose-500",
};

/** AGENT 调度面板:展示全部 AGENT 与 Jev 当前的选择 */
export function AgentDispatchPanel({
  agents,
  defaultAgent,
  dispatch,
}: AgentDispatchPanelProps) {
  if (!agents.length) {
    return null;
  }

  return (
    <section className="rounded-3xl border border-slate-200 bg-white/80 p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900/70">
      <div className="flex items-center gap-2">
        <Cpu className="h-4 w-4 text-blue-500" aria-hidden />
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          AGENT 调度
        </h3>
      </div>

      {dispatch ? (
        <div className="mt-4 rounded-2xl border border-blue-200/70 bg-blue-50/60 p-4 dark:border-blue-900/60 dark:bg-blue-950/30">
          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-600 px-3 py-1 text-xs font-semibold text-white">
              <Bot className="h-3.5 w-3.5" aria-hidden />
              {dispatch.agent_title || dispatch.agent || "未知 AGENT"}
            </span>
            {dispatch.agent ? (
              <code className="text-xs text-slate-400">{dispatch.agent}</code>
            ) : null}
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-600 dark:text-slate-300">
            <div>
              <dt className="text-slate-400">意图</dt>
              <dd className="font-medium">{dispatch.intent}</dd>
            </div>
            <div>
              <dt className="text-slate-400">动作</dt>
              <dd className="font-medium">{dispatch.action}</dd>
            </div>
            <div>
              <dt className="text-slate-400">置信度</dt>
              <dd className={clsx("font-medium", LEVEL_TONE[dispatch.confidence_level] ?? "text-slate-500")}>
                {dispatch.confidence.toFixed(2)} · {dispatch.confidence_level}
              </dd>
            </div>
            <div>
              <dt className="text-slate-400">情绪</dt>
              <dd className="font-medium">{dispatch.emotion}</dd>
            </div>
          </dl>
          {dispatch.reason ? (
            <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
              Jev 判断依据:{dispatch.reason}
            </p>
          ) : null}
        </div>
      ) : (
        <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">
          发送消息后,这里展示 Jev 本次调度的 AGENT。
        </p>
      )}

      <ul className="mt-4 space-y-2">
        {agents.map((agent) => {
          const active = dispatch?.agent === agent.name;
          return (
            <li
              key={agent.name}
              className={clsx(
                "flex items-start gap-2 rounded-2xl border px-3 py-2 text-xs transition",
                active
                  ? "border-blue-300 bg-blue-50/70 dark:border-blue-800 dark:bg-blue-950/40"
                  : "border-slate-200/70 bg-white/90 dark:border-slate-800/70 dark:bg-slate-900/60"
              )}
            >
              <Bot
                className={clsx("mt-0.5 h-3.5 w-3.5", active ? "text-blue-500" : "text-slate-400")}
                aria-hidden
              />
              <div className="flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold text-slate-800 dark:text-slate-100">
                    {agent.title}
                  </span>
                  <code className="text-[10px] text-slate-400">{agent.name}</code>
                  {agent.name === defaultAgent ? (
                    <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                      默认
                    </span>
                  ) : null}
                  {active ? (
                    <span className="rounded-full bg-blue-600 px-2 py-0.5 text-[10px] font-semibold text-white">
                      当前
                    </span>
                  ) : null}
                </div>
                <p className="mt-0.5 text-slate-500 dark:text-slate-400">{agent.description}</p>
                {agent.tools.length ? (
                  <p className="mt-0.5 text-[10px] text-slate-400">
                    工具:{agent.tools.join(" / ")}
                    {agent.needs_rag ? " · 查知识库" : ""}
                  </p>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
