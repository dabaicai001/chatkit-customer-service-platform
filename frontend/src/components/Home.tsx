import { useCallback, useEffect, useMemo, useState } from "react";
import clsx from "clsx";

import { ChatKitPanel, type ChatKitInstance } from "./ChatKitPanel";
import { CustomerContextPanel } from "./CustomerContextPanel";
import { ThemeToggle } from "./ThemeToggle";
import type { CustomerProfile } from "../hooks/useCustomerContext";
import { useCustomerContext } from "../hooks/useCustomerContext";
import type { ColorScheme } from "../hooks/useColorScheme";
import {
  SUPPORT_STARTER_PROMPTS,
  fetchBootstrapConfig,
} from "../lib/config";
import {
  DEFAULT_BOOTSTRAP,
  type BootstrapConfig,
  type SupportView,
} from "../types/support";

type HomeProps = {
  scheme: ColorScheme;
  onThemeChange: (scheme: ColorScheme) => void;
};

export default function Home({ scheme, onThemeChange }: HomeProps) {
  const [threadId, setThreadId] = useState<string | null>(null);
  const [chatkit, setChatkit] = useState<ChatKitInstance | null>(null);
  const [bootstrap, setBootstrap] = useState<BootstrapConfig>(DEFAULT_BOOTSTRAP);
  const { profile, loading, error, refresh, setProfile } =
    useCustomerContext(threadId);

  // 引导配置来自后端 business.yaml:换公司/换行业只改 YAML
  useEffect(() => {
    let cancelled = false;
    void fetchBootstrapConfig().then((config) => {
      if (!cancelled) {
        setBootstrap(config);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const panels = bootstrap.panels;
  const [view, setView] = useState<SupportView>(panels[0]?.id ?? "overview");

  // bootstrap 面板变化时,确保当前 view 仍然有效
  useEffect(() => {
    if (panels.length && !panels.some((panel) => panel.id === view)) {
      setView(panels[0].id);
    }
  }, [panels, view]);

  const containerClass = clsx(
    "min-h-screen bg-gradient-to-br transition-colors duration-300",
    scheme === "dark"
      ? "from-slate-950 via-slate-950 to-slate-900 text-slate-100"
      : "from-slate-100 via-white to-slate-200 text-slate-900"
  );

  const handleThreadChange = useCallback((nextThreadId: string | null) => {
    setThreadId(nextThreadId);
  }, []);

  const handleResponseCompleted = useCallback(() => {
    void refresh();
  }, [refresh]);

  const handleWidgetActionComplete = useCallback(() => {
    void refresh();
  }, [refresh]);

  const handleProfileEffect = useCallback(
    (nextProfile: CustomerProfile) => {
      setProfile(nextProfile);
    },
    [setProfile]
  );

  const greeting = bootstrap.customer_service.greeting;
  const startScreen = useMemo(
    () => ({
      greeting,
      prompts:
        SUPPORT_STARTER_PROMPTS[view] ?? SUPPORT_STARTER_PROMPTS.overview,
    }),
    [greeting, view]
  );

  return (
    <div className={containerClass}>
      <div className="mx-auto flex min-h-screen w-full max-w-6xl flex-col gap-8 px-6 py-8 lg:h-screen lg:max-h-screen lg:py-10">
        <header className="flex flex-col gap-6 lg:flex-row lg:items-center lg:justify-between">
          <div className="space-y-3">
            <p className="text-sm uppercase tracking-[0.2em] text-slate-500 dark:text-slate-400">
              {bootstrap.company.name} · {bootstrap.customer_service.name}
            </p>
            <h1 className="text-3xl font-semibold sm:text-4xl">
              通用客服工作台
            </h1>
            <p className="max-w-3xl text-sm text-slate-600 dark:text-slate-300">
              左侧与{bootstrap.customer_service.name}对话,右侧实时同步客户档案、订单与工单。
              Jev 负责理解意图并调度工具,{bootstrap.customer_service.name}负责把话说好。
            </p>
          </div>
          <ThemeToggle value={scheme} onChange={onThemeChange} />
        </header>

        <div className="grid flex-1 grid-cols-1 gap-8 lg:h-[calc(100vh-260px)] lg:grid-cols-[minmax(320px,380px)_1fr] lg:items-stretch xl:grid-cols-[minmax(360px,420px)_1fr]">
          <section className="flex flex-1 flex-col overflow-hidden rounded-3xl bg-white/80 shadow-[0_45px_90px_-45px_rgba(15,23,42,0.6)] ring-1 ring-slate-200/60 backdrop-blur dark:bg-slate-900/70 dark:shadow-[0_45px_90px_-45px_rgba(15,23,42,0.85)] dark:ring-slate-800/60">
            <div className="flex flex-1">
              <ChatKitPanel
                theme={scheme}
                greeting={startScreen.greeting}
                prompts={startScreen.prompts}
                composerPlaceholder={bootstrap.customer_service.composer_placeholder}
                onThreadChange={handleThreadChange}
                onResponseCompleted={handleResponseCompleted}
                onProfileUpdate={handleProfileEffect}
                onWidgetActionComplete={handleWidgetActionComplete}
                onChatKitReady={setChatkit}
              />
            </div>
          </section>

          <CustomerContextPanel
            profile={profile}
            loading={loading}
            error={error}
            panels={panels}
            view={view}
            onViewChange={setView}
          />
        </div>
      </div>
    </div>
  );
}
