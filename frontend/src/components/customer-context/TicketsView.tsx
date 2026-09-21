import clsx from "clsx";

import type { Ticket } from "../../hooks/useCustomerContext";
import { formatDate } from "./utils";
import { statusTone } from "./utils";

type TicketsViewProps = {
  tickets: Ticket[];
};

const PRIORITY_TONE: Record<string, string> = {
  高: "text-rose-500",
  紧急: "text-rose-500",
  中: "text-amber-500",
  低: "text-slate-400",
};

/** 工单列表:投诉/报修/咨询,适配各行业服务记录 */
export function TicketsView({ tickets }: TicketsViewProps) {
  if (!tickets.length) {
    return (
      <section className="rounded-3xl border border-slate-200 bg-white/80 p-5 text-sm text-slate-500 shadow-sm dark:border-slate-800 dark:bg-slate-900/70 dark:text-slate-400">
        暂无工单记录。
      </section>
    );
  }

  return (
    <section className="rounded-3xl border border-slate-200 bg-white/80 p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900/70">
      <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
        工单列表
      </h3>
      <div className="mt-4 space-y-3">
        {tickets.map((ticket) => (
          <article
            key={ticket.id}
            className="rounded-2xl border border-slate-200/70 bg-white/90 p-4 shadow-sm dark:border-slate-800/70 dark:bg-slate-900/70"
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-xs uppercase tracking-wide text-slate-400 dark:text-slate-500">
                  {ticket.id}
                </p>
                <h4 className="text-base font-semibold text-slate-900 dark:text-slate-100">
                  {ticket.subject}
                </h4>
                <p className="text-sm text-slate-500 dark:text-slate-400">
                  {formatDate(ticket.created_at)}
                </p>
              </div>
              <div className="text-right">
                <p className={clsx("text-sm font-semibold", PRIORITY_TONE[ticket.priority] ?? "text-slate-500")}>
                  优先级 {ticket.priority}
                </p>
                <p className={clsx("text-xs font-semibold uppercase tracking-wide", statusTone(ticket.status))}>
                  {ticket.status}
                </p>
              </div>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
