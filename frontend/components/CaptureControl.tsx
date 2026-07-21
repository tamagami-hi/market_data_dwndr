"use client";

import { useCallback, useEffect, useState } from "react";

import {
  getCaptureStatus,
  startCapture,
  stopCapture,
  type CaptureStatus,
} from "@/lib/api";

export default function CaptureControl() {
  const [status, setStatus] = useState<CaptureStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setStatus(await getCaptureStatus());
    } catch {
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const s = await getCaptureStatus();
        if (alive) setStatus(s);
      } catch {
        if (alive) setStatus(null);
      }
    };
    poll();
    const id = setInterval(poll, 5000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const toggle = async () => {
    setBusy(true);
    setError(null);
    try {
      const next = status?.running ? await stopCapture() : await startCapture();
      setStatus(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      refresh();
    }
  };

  const running = !!status?.running;
  const available = status?.available !== false;

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border border-zinc-800 bg-zinc-900/60 px-4 py-3">
      <span
        className={`inline-block h-2.5 w-2.5 rounded-full ${
          running ? "bg-green-500 shadow-[0_0_6px] shadow-green-500" : "bg-zinc-600"
        }`}
      />
      <span className="text-sm font-semibold text-zinc-100">
        Capture {running ? "running" : "stopped"}
      </span>
      {status?.running && (
        <span className="text-xs text-zinc-400">
          {status.trading_date} · {status.indices?.length ?? 0} indices · {status.stocks ?? 0}{" "}
          stocks · {status.tokens ?? 0} tokens
        </span>
      )}
      {status?.skipped_indices && status.skipped_indices.length > 0 && (
        <span className="text-xs text-amber-400">skipped: {status.skipped_indices.join(", ")}</span>
      )}
      <button
        onClick={toggle}
        disabled={busy || !available}
        className={`ml-auto rounded-md px-4 py-1.5 text-sm font-semibold disabled:opacity-50 ${
          running
            ? "bg-red-500/90 text-red-50 hover:bg-red-500"
            : "bg-green-500/90 text-green-950 hover:bg-green-500"
        }`}
      >
        {busy ? "…" : running ? "Stop" : "Start capture"}
      </button>
      {!available && <span className="text-xs text-zinc-500">backend unconfigured</span>}
      {error && <span className="w-full text-xs text-red-400">{error}</span>}
      {status?.error && <span className="w-full text-xs text-red-400">engine: {status.error}</span>}
    </div>
  );
}
