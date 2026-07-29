"use client";

import { useEffect, useRef, useState } from "react";

import { useOperatorEvents } from "@/components/operator-events/OperatorEventsProvider";
import type { OperationalEvent } from "@/lib/operatorEvents";

function EventTime({ timestamp }: { timestamp: number }) {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return <span>--:--</span>;
  return (
    <time dateTime={date.toISOString()}>
      {date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
    </time>
  );
}

function NotificationItem({ event }: { event: OperationalEvent }) {
  return (
    <li className="notification-item">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <span className="min-w-0 font-medium text-primary">{event.title}</span>
        <span className="shrink-0 font-mono text-[11px] text-muted">
          <EventTime timestamp={event.ts} />
        </span>
      </div>
      {event.detail && <p className="mt-1 text-xs leading-5 text-secondary">{event.detail}</p>}
      <span className={`notification-source notification-source-${event.severity}`}>
        {event.source}
      </span>
    </li>
  );
}

export function NotificationCenter() {
  const {
    notifications,
    activeToasts,
    storageError,
    markAllRead,
  } = useOperatorEvents();
  const [isOpen, setIsOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const unreadCount = notifications.filter((event) => !event.isRead).length;

  const close = () => {
    setIsOpen(false);
    triggerRef.current?.focus();
  };

  const toggle = () => {
    if (isOpen) {
      close();
      return;
    }
    markAllRead();
    setIsOpen(true);
  };

  useEffect(() => {
    if (!isOpen) return;
    panelRef.current?.focus();
    const handlePointer = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) close();
    };
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        close();
        return;
      }
      if (event.key !== "Tab" || !panelRef.current) return;
      const focusable = [...panelRef.current.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      )].filter((element) => !element.hasAttribute("disabled"));
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable.at(-1)!;
      if (event.shiftKey && (document.activeElement === first || document.activeElement === panelRef.current)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("mousedown", handlePointer);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handlePointer);
      document.removeEventListener("keydown", handleKey);
    };
  }, [isOpen]);

  return (
    <div ref={rootRef} className="notification-center">
      <button
        ref={triggerRef}
        type="button"
        className="notification-trigger control"
        aria-label={unreadCount > 0 ? `Notifications, ${unreadCount} unread` : "Notifications"}
        aria-expanded={isOpen}
        aria-controls="notification-history"
        onClick={toggle}
      >
        <svg aria-hidden="true" viewBox="0 0 24 24" className="h-[18px] w-[18px]">
          <path
            d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"
            fill="none"
            stroke="currentColor"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="1.7"
          />
        </svg>
        {unreadCount > 0 && (
          <span className="notification-count" aria-hidden="true">
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        )}
      </button>

      {isOpen && (
        <div
          ref={panelRef}
          id="notification-history"
          role="dialog"
          aria-label="Notifications"
          tabIndex={-1}
          className="notification-panel"
        >
          <div className="flex items-center justify-between gap-3 border-b border-border px-3 py-2">
            <div>
              <h2 className="text-sm font-semibold text-primary">Notifications</h2>
              <p className="text-xs text-muted">Today</p>
            </div>
            <button type="button" className="control min-h-9 px-2 text-xs text-secondary" onClick={close}>
              Close
            </button>
          </div>
          {storageError && (
            <p role="alert" className="border-b border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
              {storageError}
            </p>
          )}
          {notifications.length === 0 ? (
            <p className="px-3 py-5 text-center text-xs text-muted">No notifications today.</p>
          ) : (
            <ol className="notification-list">
              {notifications.map((event) => <NotificationItem key={event.id} event={event} />)}
            </ol>
          )}
        </div>
      )}

      <div className="notification-toasts" role="region" aria-label="Recent notifications">
        {activeToasts.map((event) => (
          <div
            key={event.id}
            role={event.severity === "danger" ? "alert" : "status"}
            className={`notification-toast notification-toast-${event.severity}`}
          >
            <div className="font-semibold text-primary">{event.title}</div>
            {event.detail && <div className="mt-1 text-xs text-secondary">{event.detail}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}
