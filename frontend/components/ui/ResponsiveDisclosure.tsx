"use client";

import { useId, useState, type ReactNode } from "react";

export function ResponsiveDisclosure({
  id,
  label,
  summary,
  children,
  defaultOpen = false,
  className = "",
}: {
  id?: string;
  label: string;
  summary: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
}) {
  const generatedId = useId();
  const panelId = id ?? generatedId;
  const [isOpen, setIsOpen] = useState(defaultOpen);

  return (
    <section className={`disclosure ${className}`}>
      <button
        type="button"
        aria-label={label}
        aria-expanded={isOpen}
        aria-controls={panelId}
        onClick={() => setIsOpen((current) => !current)}
        className="flex min-h-11 w-full items-center gap-3 px-3 py-2 text-left"
      >
        <span className="min-w-0 flex-1">{summary}</span>
        <span aria-hidden="true" className="text-muted">
          {isOpen ? "−" : "+"}
        </span>
      </button>
      <div id={panelId} hidden={!isOpen} className="border-t border-border px-3 py-3">
        {children}
      </div>
    </section>
  );
}
