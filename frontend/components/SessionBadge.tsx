"use client";

import { useEffect, useState } from "react";

import { createPollController } from "@/hooks/polling";
import { getAuthStatus, type AuthStatus } from "@/lib/api";

export default function SessionBadge() {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    const controller = createPollController({
      intervalMs: () => 15_000,
      isPaused: () => document.hidden,
      task: async (signal) => {
        try {
          const s = await getAuthStatus(signal);
          if (alive) {
            setStatus(s);
            setError(false);
          }
        } catch {
          if (alive) setError(true);
        }
      },
    });
    const handleVisibility = () => {
      if (!document.hidden) controller.resume();
    };
    controller.start();
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      alive = false;
      document.removeEventListener("visibilitychange", handleVisibility);
      controller.stop();
    };
  }, []);

  let tone = "bg-danger";
  let label = "backend offline";
  if (!error && status) {
    if (!status.configured) {
      tone = "bg-muted";
      label = "unconfigured";
    } else if (status.authenticated) {
      tone = "bg-success";
      label = `session ${status.trading_date ?? ""}`;
    } else {
      tone = "bg-warning";
      label = "not logged in";
    }
  }

  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-secondary" role="status">
      <span className={`inline-block h-2 w-2 rounded-full ${tone}`} aria-hidden="true" />
      {label}
    </span>
  );
}
