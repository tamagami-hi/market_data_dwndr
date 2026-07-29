"use client";

import { useState } from "react";

import { Dialog } from "@/components/ui/Dialog";
import type { OperationalEvent } from "@/lib/operatorEvents";

function LogRows({ logs }: { logs: OperationalEvent[] }) {
  if (logs.length === 0) return <p className="text-xs text-muted">No session messages yet.</p>;
  return (
    <div className="space-y-1 font-mono text-xs leading-5">
      {logs.map((line) => (
        <div key={line.id} className="grid grid-cols-[auto_1fr] gap-2">
          <time className="text-accent">{new Date(line.ts).toLocaleTimeString()}</time>
          <span className={line.severity === "danger" ? "text-danger" : line.severity === "warning" ? "text-warning" : line.severity === "success" ? "text-success" : "text-secondary"}>
            {line.title}
            {line.detail && <span className="ml-1 text-muted">{line.detail}</span>}
          </span>
        </div>
      ))}
    </div>
  );
}

export function SessionLogs({ logs }: { logs: OperationalEvent[] }) {
  const [isOpen, setIsOpen] = useState(false);
  return (
    <section className="panel panel-compact">
      <div className="panel-heading">
        <div className="panel-title-line">
          <h2 className="shrink-0 text-xs font-semibold text-primary">Session logs</h2>
          <span className="truncate text-xs text-muted">{logs.length} observed today</span>
        </div>
        <button type="button" className="control min-h-11 px-3 text-xs text-accent hover:bg-surface-2" onClick={() => setIsOpen(true)}>
          Open log viewer
        </button>
      </div>
      {/* Fills the panel and scrolls, rather than a fixed 3-line peek: now that the
          panel shares a dashboard row it has real height to use. The dialog remains for
          reading long history comfortably. */}
      <div className="h-[3.75rem] overflow-auto p-3">
        <LogRows logs={logs.slice(0, 3)} />
      </div>
      <Dialog isOpen={isOpen} title="Session logs" onOpenChange={setIsOpen}>
        {isOpen ? <LogRows logs={logs} /> : null}
      </Dialog>
    </section>
  );
}
