"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";

export function Explanation({
  label,
  children,
  contentClassName = "",
}: {
  label: string;
  children: ReactNode;
  contentClassName?: string;
}) {
  const id = useId();
  const [isOpen, setIsOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!isOpen) return;

    function dismissOnEscape(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setIsOpen(false);
      triggerRef.current?.focus();
    }

    function dismissOutside(event: PointerEvent) {
      if (wrapperRef.current?.contains(event.target as Node)) return;
      setIsOpen(false);
    }

    document.addEventListener("keydown", dismissOnEscape);
    document.addEventListener("pointerdown", dismissOutside);
    return () => {
      document.removeEventListener("keydown", dismissOnEscape);
      document.removeEventListener("pointerdown", dismissOutside);
    };
  }, [isOpen]);

  return (
    <div ref={wrapperRef} className="relative inline-flex">
      <button
        ref={triggerRef}
        type="button"
        aria-label={label}
        aria-expanded={isOpen}
        aria-controls={id}
        onClick={() => setIsOpen((current) => !current)}
        className="explanation-trigger"
      >
        ?
      </button>
      <div
        id={id}
        hidden={!isOpen}
        role="note"
        className={`explanation-content ${contentClassName}`.trim()}
      >
        {children}
      </div>
    </div>
  );
}
