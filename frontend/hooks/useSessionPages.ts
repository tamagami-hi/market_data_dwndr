"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getSessionPage, type SessionSummary } from "@/lib/api";
import { normalizeSessionPage } from "@/lib/monitor/sessionSummary";

/** Rows fetched per "Load older sessions" click (the endpoint allows up to 500). */
export const SESSION_PAGE_SIZE = 100;

export interface SessionPages {
  /** Recent rows followed by every loaded older row, newest first, one row per date. */
  sessions: SessionSummary[];
  /** Every recorded session the backend holds; null until the first page is fetched. */
  total: number | null;
  hasMore: boolean;
  isLoading: boolean;
  error: string | null;
  loadOlder: () => void;
}

/** One row per trading date, first occurrence wins (recent rows are the fresher copy). */
function dedupeByDate(rows: readonly SessionSummary[]): SessionSummary[] {
  const seen = new Set<string>();
  return rows.filter((row) => {
    if (seen.has(row.trading_date)) return false;
    seen.add(row.trading_date);
    return true;
  });
}

function oldestDate(rows: readonly SessionSummary[]): string | null {
  return rows.reduce<string | null>(
    (oldest, row) => (oldest === null || row.trading_date < oldest ? row.trading_date : oldest),
    null,
  );
}

/**
 * The full session history on demand. ``/api/stats`` carries the most recent sessions;
 * older ones are paged from ``GET /api/stats/sessions`` with ``before`` = the oldest date
 * already shown, then the returned ``next_before`` cursor until it is null.
 */
export function useSessionPages(recent: readonly SessionSummary[]): SessionPages {
  const [older, setOlder] = useState<SessionSummary[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  // undefined: nothing fetched yet; null: the last page has been reached.
  const [cursor, setCursor] = useState<string | null | undefined>(undefined);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const sessions = useMemo(() => dedupeByDate([...recent, ...older]), [recent, older]);

  const loadOlder = useCallback(() => {
    if (isLoading || cursor === null) return;
    const before = cursor ?? oldestDate(sessions);
    const controller = new AbortController();
    abortRef.current?.abort();
    abortRef.current = controller;
    setIsLoading(true);
    setError(null);
    getSessionPage(SESSION_PAGE_SIZE, before, controller.signal)
      .then((raw) => {
        const page = normalizeSessionPage(raw);
        if (!page) throw new Error("Session history page was malformed.");
        setOlder((current) => dedupeByDate([...current, ...page.sessions]));
        setTotal(page.total);
        setCursor(page.next_before);
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : "Could not load older sessions.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoading(false);
      });
  }, [cursor, isLoading, sessions]);

  return {
    sessions,
    total,
    hasMore: cursor !== null,
    isLoading,
    error,
    loadOlder,
  };
}
