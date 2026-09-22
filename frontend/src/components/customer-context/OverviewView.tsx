import { Package, Sparkles, Ticket as TicketIcon } from "lucide-react";

import type { CustomerProfile } from "../../hooks/useCustomerContext";
import { TimelineList } from "./TimelineList";
import { InfoPill } from "./InfoPill";

type OverviewViewProps = {
  profile: CustomerProfile;
};

/** 概览:客户快照 + 标签 + 订单/工单统计 + 本次服务流水 */
export function OverviewView({ profile }: OverviewViewProps) {
  // 画像字段按可选处理:上游/后端缺字段时降级为空,不让整个页面白屏
  const tags = profile.tags ?? [];
  const orders = profile.orders ?? [];
  const tickets = profile.tickets ?? [];
  return (
    <div className="space-y-6">
      <div className="rounded-3xl border border-slate-200 bg-white/80 p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900/70">
        <h3 className="text-lg font-semibold text-slate-800 dark:text-slate-100">
          客户快照
        </h3>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">
          {profile.summary || "暂无客户概况。"}
        </p>
        {tags.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-2">
            {tags.map((tag) => (
              <span
                key={tag}
                className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-200"
              >
                <Sparkles className="h-3 w-3 text-amber-500" aria-hidden />
                {tag}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <InfoPill icon={Package} label="订单数">
          {profile.orders_total ?? orders.length}
        </InfoPill>
        <InfoPill icon={TicketIcon} label="工单数">
          {tickets.length}
        </InfoPill>
      </div>

      <TimelineList timeline={profile.timeline} limit={5} />
    </div>
  );
}
