"use client";

import { useEffect } from "react";

import { useOperatorEvents } from "@/components/operator-events/OperatorEventsProvider";

interface MonitorRestNotificationsProps {
  error: string | null;
  hasCompletedRefresh: boolean;
}

export function MonitorRestNotifications({
  error,
  hasCompletedRefresh,
}: MonitorRestNotificationsProps) {
  const { reportMonitorRestError } = useOperatorEvents();

  useEffect(() => {
    if (error === null && !hasCompletedRefresh) return;
    reportMonitorRestError(error);
  }, [error, hasCompletedRefresh, reportMonitorRestError]);

  return null;
}
