import { useCallback, useState } from "react";
import { Link2, Link2Off, Loader2, UserRound } from "lucide-react";

import { SUPPORT_BIND_URL } from "../../lib/config";
import {
  DEFAULT_BINDING_TEXTS,
  type BindingTexts,
} from "../../types/support";

export type BoundCustomer = {
  customer_id: string;
  name: string;
};

type CustomerBindPanelProps = {
  /** 当前 ChatKit 会话(绑定按会话隔离;未开始对话时为 null,后端用默认会话) */
  threadId: string | null;
  /** 当前会话绑定的用户(由客户画像推导:有画像即已绑定) */
  bound: BoundCustomer | null;
  /** 绑定/解绑成功后回调(触发右侧画像刷新) */
  onBindingChange: () => void;
  /** 卡片文案(来自 business.yaml 的 customer_service.binding 段,换行业只改 YAML) */
  texts?: BindingTexts;
};

/** 右侧「绑定用户」卡片:输入用户ID → MCP 校验并绑定 → 回显该用户信息。 */
export function CustomerBindPanel({
  threadId,
  bound,
  onBindingChange,
  texts,
}: CustomerBindPanelProps) {
  // 后端未下发文案时用内置默认(防御旧版本 bootstrap)
  const t: BindingTexts = { ...DEFAULT_BINDING_TEXTS, ...texts };
  const [customerId, setCustomerId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleBind = useCallback(async () => {
    const id = customerId.trim();
    if (!id || submitting) {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch(SUPPORT_BIND_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ thread_id: threadId ?? "", customer_id: id }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload?.detail || `绑定失败(${response.status})`);
      }
      setCustomerId("");
      onBindingChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }, [customerId, submitting, threadId, onBindingChange]);

  const handleUnbind = useCallback(async () => {
    if (submitting) {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const query = threadId ? `?thread_id=${encodeURIComponent(threadId)}` : "";
      const response = await fetch(`${SUPPORT_BIND_URL}${query}`, {
        method: "DELETE",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(`解绑失败(${response.status})`);
      }
      onBindingChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }, [submitting, threadId, onBindingChange]);

  return (
    <section className="rounded-2xl border border-slate-200/70 bg-white/70 p-4 dark:border-slate-800/70 dark:bg-slate-900/50">
      <div className="flex items-center justify-between gap-3">
        <h3 className="inline-flex items-center gap-2 text-sm font-semibold text-slate-800 dark:text-slate-100">
          <Link2 className="h-4 w-4 text-blue-500" aria-hidden />
          {t.title}
        </h3>
        {bound && (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700 dark:border-emerald-900/60 dark:bg-emerald-950/30 dark:text-emerald-200">
            <UserRound className="h-3.5 w-3.5" aria-hidden />
            已绑定:{bound.name}({bound.customer_id})
          </span>
        )}
      </div>

      {bound ? (
        <div className="mt-3 flex items-center justify-between gap-3">
          <p className="text-xs text-slate-500 dark:text-slate-400">{t.bound_hint}</p>
          <button
            type="button"
            onClick={() => void handleUnbind()}
            disabled={submitting}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:border-slate-300 hover:text-slate-800 disabled:opacity-50 dark:border-slate-700 dark:text-slate-300 dark:hover:text-slate-100"
          >
            {submitting ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <Link2Off className="h-3.5 w-3.5" aria-hidden />
            )}
            {t.unbind_label}
          </button>
        </div>
      ) : (
        <div className="mt-3 flex items-center gap-2">
          <input
            value={customerId}
            onChange={(event) => setCustomerId(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                void handleBind();
              }
            }}
            placeholder={t.input_placeholder}
            aria-label={t.input_placeholder}
            className="min-w-0 flex-1 rounded-full border border-slate-200 bg-white px-4 py-2 text-sm text-slate-700 outline-none transition placeholder:text-slate-400 focus:border-blue-400 dark:border-slate-700 dark:bg-slate-950/40 dark:text-slate-200"
          />
          <button
            type="button"
            onClick={() => void handleBind()}
            disabled={submitting || !customerId.trim()}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-slate-900 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-slate-700 disabled:opacity-40 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white"
          >
            {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
            {t.submit_label}
          </button>
        </div>
      )}

      <p className="mt-2 text-xs text-slate-400 dark:text-slate-500">{t.hint}</p>
      {error && (
        <p className="mt-2 text-xs text-rose-600 dark:text-rose-300" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
