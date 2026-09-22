import { Clock, Loader2 } from "lucide-react";

import type { PipelineTrace } from "../../types/support";

type PipelineTracePanelProps = {
  trace: PipelineTrace | null;
};

/** 毫秒 → 人类可读(>=1s 用秒) */
function formatMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

/** 右侧「调用流程与耗时」面板:一次消息各阶段(画像/Jev/取数/生成)的耗时与流程。 */
export function PipelineTracePanel({ trace }: PipelineTracePanelProps) {
  const maxMs = trace?.steps.length
    ? Math.max(...trace.steps.map((s) => s.ms), 1)
    : 1;

  return (
    <section className="rounded-2xl border border-slate-200/70 bg-white/70 p-4 dark:border-slate-800/70 dark:bg-slate-900/50">
      <div className="flex items-center justify-between gap-3">
        <h3 className="inline-flex items-center gap-2 text-sm font-semibold text-slate-800 dark:text-slate-100">
          <Clock className="h-4 w-4 text-blue-500" aria-hidden />
          调用流程与耗时
        </h3>
        {trace && (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-medium text-blue-700 dark:border-blue-900/60 dark:bg-blue-950/30 dark:text-blue-200">
            总计 {formatMs(trace.total_ms)}
            {trace.status === "partial" && (
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
            )}
          </span>
        )}
      </div>

      {!trace ? (
        <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">
          发送消息后,这里展示本次调用的流程与各步骤耗时。
        </p>
      ) : (
        <ol className="mt-3 space-y-3">
          {trace.steps.map((step, index) => (
            <li key={`${step.step}-${index}`}>
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="font-medium text-slate-700 dark:text-slate-200">
                  {index + 1}. {step.label}
                </span>
                <span className="shrink-0 tabular-nums text-slate-500 dark:text-slate-400">
                  {formatMs(step.ms)}
                </span>
              </div>
              {step.detail && (
                <p className="mt-0.5 text-[11px] text-slate-400 dark:text-slate-500">
                  {step.detail}
                </p>
              )}
              <div className="mt-1 h-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                <div
                  className="h-full rounded-full bg-blue-400/80 dark:bg-blue-400/60"
                  style={{ width: `${Math.max(4, (step.ms / maxMs) * 100)}%` }}
                />
              </div>
            </li>
          ))}
        </ol>
      )}

      {trace?.status === "partial" && (
        <p className="mt-2 text-[11px] text-slate-400 dark:text-slate-500">
          话术生成中,完成后显示完整耗时…
        </p>
      )}
    </section>
  );
}
