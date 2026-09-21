export function formatDate(value: string): string {
  try {
    return new Date(value).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  } catch {
    return value;
  }
}

const ACTIVE_TONES = new Set(["配送中", "待发货", "进行中", "处理中", "待处理"]);
const DONE_TONES = new Set(["已完成", "已签收", "已关闭", "已解决"]);
const CANCEL_TONES = new Set(["已取消", "已退款", "已退回", "已拒绝"]);

/** 订单/工单状态的通用语气色(上游状态词不断增多时按前缀归类) */
export function statusTone(status: string): string {
  if (CANCEL_TONES.has(status)) return "text-rose-500";
  if (DONE_TONES.has(status)) return "text-emerald-500";
  if (ACTIVE_TONES.has(status)) return "text-blue-500";
  if (status.startsWith("已")) return "text-rose-500";
  return "text-blue-500";
}
