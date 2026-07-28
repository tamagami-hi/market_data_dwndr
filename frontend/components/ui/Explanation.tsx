"use client";

import { useId, useState, type ReactNode } from "react";

export function Explanation({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  const id = useId();
  const [isOpen, setIsOpen] = useState(false);
  return (
    <span className="relative inline-flex">
      <button
        type="button"
        aria-label={label}
        aria-expanded={isOpen}
        aria-controls={id}
        onClick={() => setIsOpen((current) => !current)}
        className="explanation-trigger"
      >
        ?
      </button>
      <span id={id} hidden={!isOpen} role="note" className="explanation-content">
        {children}
      </span>
    </span>
  );
}
