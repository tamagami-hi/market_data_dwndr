"use client";

import { useEffect, useState } from "react";

import { getAuthStatus, type AuthStatus } from "@/lib/api";

export default function SessionBadge() {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const s = await getAuthStatus();
        if (alive) {
          setStatus(s);
          setError(false);
        }
      } catch {
        if (alive) setError(true);
      }
    };
    poll();
    const id = setInterval(poll, 15_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  let tone = "bg-zinc-600";
  let label = "backend offline";
  if (!error && status) {
    if (!status.configured) {
      tone = "bg-zinc-600";
      label = "unconfigured";
    } else if (status.authenticated) {
      tone = "bg-green-500 shadow-[0_0_6px] shadow-green-500";
      label = `session · ${status.trading_date ?? ""}`;
    } else {
      tone = "bg-amber-500";
      label = "not logged in";
    }
  }

  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-zinc-400">
      <span className={`inline-block h-2 w-2 rounded-full ${tone}`} />
      {label}
    </span>
  );
}
