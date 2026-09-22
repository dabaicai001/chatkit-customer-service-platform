import { ChatKit, useChatKit } from "@openai/chatkit-react";
import type { StartScreenPrompt } from "@openai/chatkit";
import { useCallback, useRef } from "react";

import type { CustomerProfile } from "../hooks/useCustomerContext";
import type { ColorScheme } from "../hooks/useColorScheme";
import type { AgentDispatch, PipelineTrace } from "../types/support";
import {
  SUPPORT_CHATKIT_API_DOMAIN_KEY,
  SUPPORT_CHATKIT_API_URL,
} from "../lib/config";
import { IMAGE_ATTACHMENT_ACCEPT, MAX_UPLOAD_BYTES } from "../lib/uploads";

export type ChatKitInstance = ReturnType<typeof useChatKit>;

type ChatKitPanelProps = {
  theme: ColorScheme;
  greeting: string;
  prompts: StartScreenPrompt[];
  composerPlaceholder: string;
  onThreadChange: (threadId: string | null) => void;
  onResponseCompleted: () => void;
  onProfileUpdate: (profile: CustomerProfile) => void;
  onAgentDispatch?: (dispatch: AgentDispatch) => void;
  onPipelineTrace?: (trace: PipelineTrace) => void;
  onWidgetActionComplete?: () => void;
  onChatKitReady?: (chatkit: ChatKitInstance) => void;
};

export function ChatKitPanel({
  theme,
  greeting,
  prompts,
  composerPlaceholder,
  onThreadChange,
  onResponseCompleted,
  onProfileUpdate,
  onAgentDispatch,
  onPipelineTrace,
  onWidgetActionComplete,
  onChatKitReady,
}: ChatKitPanelProps) {
  const chatkitRef = useRef<ReturnType<typeof useChatKit> | null>(null);

  const handleWidgetAction = useCallback(
    async (
      action: { type: string; payload?: Record<string, unknown> },
      widgetItem: { id: string }
    ): Promise<void> => {
      const instance = chatkitRef.current;
      if (!instance) {
        return;
      }

      await instance.sendCustomAction(action, widgetItem.id);
      onWidgetActionComplete?.();
    },
    [onWidgetActionComplete]
  );

  const handleEffect = useCallback(
    ({ name, data }: { name: string; data: Record<string, unknown> }) => {
      if (name === "customer_profile/update") {
        const nextProfile = data.profile as CustomerProfile | undefined;
        if (nextProfile) {
          onProfileUpdate(nextProfile);
        }
      } else if (name === "agent_dispatch/update") {
        const dispatch = data.dispatch as AgentDispatch | undefined;
        if (dispatch) {
          onAgentDispatch?.(dispatch);
        }
        // dispatch 事件附带 partial trace(Jev/路由已完成部分的耗时)
        const partialTrace = data.trace as PipelineTrace | undefined;
        if (partialTrace?.steps?.length) {
          onPipelineTrace?.(partialTrace);
        }
      } else if (name === "pipeline_trace/update") {
        const trace = data.trace as PipelineTrace | undefined;
        if (trace?.steps?.length) {
          onPipelineTrace?.(trace);
        }
      }
    },
    [onProfileUpdate, onAgentDispatch, onPipelineTrace]
  );

  const chatkit = useChatKit({
    api: {
      url: SUPPORT_CHATKIT_API_URL,
      domainKey: SUPPORT_CHATKIT_API_DOMAIN_KEY,
      uploadStrategy: { type: "two_phase" },
    },
    theme: {
      colorScheme: theme,
      color: {
        grayscale: {
          hue: 220,
          tint: 6,
          shade: theme === "dark" ? -1 : -4,
        },
        accent: {
          primary: theme === "dark" ? "#f8fafc" : "#0f172a",
          level: 1,
        },
      },
      radius: "round",
    },
    startScreen: {
      greeting,
      prompts,
    },
    composer: {
      placeholder: composerPlaceholder,
      attachments: {
        enabled: true,
        maxSize: MAX_UPLOAD_BYTES,
        accept: IMAGE_ATTACHMENT_ACCEPT,
      },
      dictation: { enabled: true },
    },
    threadItemActions: {
      feedback: false,
    },
    widgets: {
      onAction: handleWidgetAction,
    },
    onResponseEnd: () => {
      onResponseCompleted();
    },
    onThreadChange: ({ threadId }) => {
      onThreadChange(threadId ?? null);
    },
    onError: ({ error }) => {
      console.error("ChatKit error", error);
    },
    onReady: () => {
      onChatKitReady?.(chatkit);
    },
    onEffect: handleEffect,
  });
  chatkitRef.current = chatkit;

  return (
    <div className="relative h-full w-full overflow-hidden border border-slate-200/60 bg-white shadow-card dark:border-slate-800/70 dark:bg-slate-900">
      <ChatKit control={chatkit.control} className="block h-full w-full" />
    </div>
  );
}
