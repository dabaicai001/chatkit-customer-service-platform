import type { BootstrapConfig, SupportView } from "../types/support";
import { DEFAULT_BOOTSTRAP } from "../types/support";

export const THEME_STORAGE_KEY = "customer-service-theme";

const API_BASE = import.meta.env.VITE_SUPPORT_API_BASE ?? "/support";

/**
 * ChatKit still expects a domain key at runtime. Use any placeholder locally,
 * but register your production domain at
 * https://platform.openai.com/settings/organization/security/domain-allowlist
 * and deploy the real key.
 */
export const SUPPORT_CHATKIT_API_DOMAIN_KEY =
  import.meta.env.VITE_SUPPORT_CHATKIT_API_DOMAIN_KEY ??
  "domain_pk_localhost_dev";

export const SUPPORT_CHATKIT_API_URL =
  import.meta.env.VITE_SUPPORT_CHATKIT_API_URL ?? `${API_BASE}/chatkit`;

export const SUPPORT_CUSTOMER_URL =
  import.meta.env.VITE_SUPPORT_CUSTOMER_URL ?? `${API_BASE}/customer`;

export const SUPPORT_BOOTSTRAP_URL =
  import.meta.env.VITE_SUPPORT_BOOTSTRAP_URL ?? `${API_BASE}/bootstrap`;

/**
 * 从后端拉取引导配置(公司名/客服名/欢迎语/侧栏面板)。
 * 换一家公司的客服 = 改后端 business.yaml,前端无需改代码。
 */
export async function fetchBootstrapConfig(): Promise<BootstrapConfig> {
  try {
    const response = await fetch(SUPPORT_BOOTSTRAP_URL, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(`bootstrap ${response.status}`);
    }
    const payload = (await response.json()) as BootstrapConfig;
    if (!payload?.panels?.length) {
      return DEFAULT_BOOTSTRAP;
    }
    return payload;
  } catch {
    return DEFAULT_BOOTSTRAP;
  }
}

/** 各面板的欢迎语与推荐提问(可按覆盖配置继续扩展) */
export const SUPPORT_GREETINGS: Record<SupportView, string> = {
  overview: "您好,我是您的专属客服,请问有什么可以帮您?",
  orders: "需要查询订单、物流或售后进度吗?",
  tickets: "需要查看工单或创建新的服务记录吗?",
};

export const SUPPORT_STARTER_PROMPTS: Record<
  SupportView,
  { label: string; prompt: string; icon: string }[]
> = {
  overview: [
    { label: "查订单", prompt: "帮我查一下最新的订单到哪了。", icon: "package" },
    { label: "退款政策", prompt: "你们的退款政策是怎样的?", icon: "notebook" },
    { label: "转人工", prompt: "我要转人工客服。", icon: "profile" },
  ],
  orders: [
    { label: "物流进度", prompt: "我的订单发货了吗?快递到哪了?", icon: "truck" },
    { label: "申请退款", prompt: "我要申请退款。", icon: "refresh" },
    { label: "取消订单", prompt: "帮我取消这个订单。", icon: "close" },
  ],
  tickets: [
    { label: "商品问题", prompt: "收到的商品有质量问题,我要投诉。", icon: "alert" },
    { label: "工单进度", prompt: "我的工单处理得怎么样了?", icon: "notebook-pencil" },
    { label: "保修咨询", prompt: "这个产品保修多久?怎么保修?", icon: "shield" },
  ],
};
