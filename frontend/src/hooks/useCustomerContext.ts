import { useCallback, useEffect, useState } from "react";

import { SUPPORT_CUSTOMER_URL } from "../lib/config";

export type Order = {
  id: string;
  title: string;
  status: string;
  amount: number;
  created_at: string;
  tracking: string;
};

export type Ticket = {
  id: string;
  subject: string;
  status: string;
  priority: string;
  created_at: string;
};

export type TimelineEntry = {
  timestamp: string;
  kind: string;
  entry: string;
};

/** 通用客户画像(由后端经 MCP 聚合上游系统数据后归一化) */
export type CustomerProfile = {
  customer_id: string;
  name: string;
  level: string;
  email: string;
  phone: string;
  tags: string[];
  summary: string;
  orders: Order[];
  tickets: Ticket[];
  timeline: TimelineEntry[];
};

type CustomerResponse = {
  customer: CustomerProfile | null;
};

export function useCustomerContext(threadId: string | null) {
  const [profile, setProfile] = useState<CustomerProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchProfile = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const url = threadId
        ? `${SUPPORT_CUSTOMER_URL}?thread_id=${encodeURIComponent(threadId)}`
        : SUPPORT_CUSTOMER_URL;
      const response = await fetch(url, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(`Failed to load customer context (${response.status})`);
      }
      const payload = (await response.json()) as CustomerResponse;
      setProfile(payload.customer);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      setProfile(null);
    } finally {
      setLoading(false);
    }
  }, [threadId]);

  useEffect(() => {
    void fetchProfile();
  }, [fetchProfile]);

  const applyProfileUpdate = useCallback((nextProfile: CustomerProfile) => {
    setProfile(nextProfile);
    setLoading(false);
    setError(null);
  }, []);

  return {
    profile,
    loading,
    error,
    refresh: fetchProfile,
    setProfile: applyProfileUpdate,
  };
}
