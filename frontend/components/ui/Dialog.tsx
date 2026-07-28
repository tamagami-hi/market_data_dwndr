"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";

export function Dialog({
  isOpen,
  title,
  onOpenChange,
  children,
}: {
  isOpen: boolean;
  title: string;
  onOpenChange: (isOpen: boolean) => void;
  children: ReactNode;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    if (isOpen) {
      returnFocusRef.current = document.activeElement as HTMLElement | null;
      const previousOverflow = document.body.style.overflow;
      if (!dialog.open) dialog.showModal();
      document.body.style.overflow = "hidden";
      const closeButton = dialog.querySelector<HTMLButtonElement>("[data-dialog-close]");
      closeButton?.focus();
      return () => {
        document.body.style.overflow = previousOverflow;
      };
    }

    if (dialog.open) dialog.close();
    returnFocusRef.current?.focus();
  }, [isOpen]);

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      className="operator-dialog"
      onCancel={(event) => {
        event.preventDefault();
        onOpenChange(false);
      }}
      onClose={() => {
        if (isOpen) onOpenChange(false);
      }}
    >
      <div className="flex items-center justify-between gap-4 border-b border-border px-4 py-3">
        <h2 id={titleId} className="text-sm font-semibold text-primary">
          {title}
        </h2>
        <button
          type="button"
          data-dialog-close
          onClick={() => onOpenChange(false)}
          aria-label={`Close ${title}`}
          className="control min-h-11 px-3 text-secondary hover:text-primary"
        >
          Close
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-4">{children}</div>
    </dialog>
  );
}
