import clsx from "clsx";

import type { Order } from "../../hooks/useCustomerContext";
import { formatDate } from "./utils";
import { statusTone } from "./utils";

type OrdersViewProps = {
  orders: Order[];
};

/** 订单列表:通用结构,适配电商/物流/SaaS 订阅等场景 */
export function OrdersView({ orders }: OrdersViewProps) {
  if (!orders.length) {
    return (
      <section className="rounded-3xl border border-slate-200 bg-white/80 p-5 text-sm text-slate-500 shadow-sm dark:border-slate-800 dark:bg-slate-900/70 dark:text-slate-400">
        暂无订单记录。
      </section>
    );
  }

  return (
    <section className="rounded-3xl border border-slate-200 bg-white/80 p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900/70">
      <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
        订单列表
      </h3>
      <div className="mt-4 space-y-3">
        {orders.map((order) => (
          <article
            key={order.id}
            className="rounded-2xl border border-slate-200/70 bg-white/90 p-4 shadow-sm transition hover:-translate-y-0.5 hover:border-blue-300 hover:shadow-md dark:border-slate-800/70 dark:bg-slate-900/70"
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-xs uppercase tracking-wide text-slate-400 dark:text-slate-500">
                  #{order.id}
                </p>
                <h4 className="text-base font-semibold text-slate-900 dark:text-slate-100">
                  {order.title}
                </h4>
                <p className="text-sm text-slate-500 dark:text-slate-400">
                  {formatDate(order.created_at)}
                  {order.tracking ? ` · ${order.tracking}` : ""}
                </p>
              </div>
              <div className="text-right">
                <p className="text-sm font-medium text-blue-600 dark:text-blue-300">
                  ¥{order.amount.toFixed(2)}
                </p>
                <p
                  className={clsx(
                    "text-xs font-semibold uppercase tracking-wide",
                    statusTone(order.status)
                  )}
                >
                  {order.status}
                </p>
              </div>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
