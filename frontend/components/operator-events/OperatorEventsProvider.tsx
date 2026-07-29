"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  applyReadEventIds,
  clearExpiredDailyEvents,
  dailyEventReadsStorageKey,
  dailyEventsStorageKey,
  deriveCaptureEvents,
  deriveCompressionEvents,
  indiaDayKey,
  loadDailyEvents,
  loadReadEventIds,
  mergeDailyEvents,
  millisecondsUntilIndiaMidnight,
  saveDailyEvents,
  saveReadEventIds,
  type OperationalEvent,
  type OperationalEventDraft,
} from "@/lib/operatorEvents";
import {
  normalizeCaptureStatus,
  normalizeCompressionProgress,
} from "@/lib/monitor/viewModel";
import { useTopicEnvelopes } from "@/lib/useTopic";
import {
  captureStatusConnection,
  sessionConnection,
} from "@/lib/wsTopicConnection";
import {
  MSG,
  type CompressionProgressPayload,
  type GlobalStatus,
  type WsEnvelope,
} from "@/lib/wsTypes";

const TOAST_DURATION_MS = 7_000;
const MAX_LOG_MESSAGE_LENGTH = 2_000;
const MAX_SESSION_PHASE_LENGTH = 80;
const MAX_EVENT_TITLE_LENGTH = 2_000;
const MAX_EVENT_DETAIL_LENGTH = 4_000;
const MONITOR_REST_FAILURE_DETAIL =
  "Session or capture-history data could not be refreshed. Last valid values remain on screen.";
const MONITOR_REST_RECOVERY_DETAIL =
  "Session and capture-history refreshes are succeeding again.";
const COLLECTOR_LOCK_NAME = "tickvault:operational-event-collector";
const COLLECTOR_LEASE_KEY = "tickvault:operational-event-leader:v1";
const COLLECTOR_LEASE_MS = 6_000;
const COLLECTOR_HEARTBEAT_MS = 2_000;

interface OperatorEventsContextValue {
  events: OperationalEvent[];
  logs: OperationalEvent[];
  notifications: OperationalEvent[];
  activeToasts: OperationalEvent[];
  storageError: string | null;
  publish: (draft: OperationalEventDraft) => void;
  reportMonitorRestError: (error: string | null) => void;
  markAllRead: () => void;
}

const EMPTY_CONTEXT: OperatorEventsContextValue = {
  events: [],
  logs: [],
  notifications: [],
  activeToasts: [],
  storageError: null,
  publish: () => undefined,
  reportMonitorRestError: () => undefined,
  markAllRead: () => undefined,
};

const OperatorEventsContext = createContext<OperatorEventsContextValue>(EMPTY_CONTEXT);

interface CollectorLease {
  owner: string;
  expiresAt: number;
}

function readCollectorLease(): CollectorLease | null {
  try {
    const raw = window.localStorage.getItem(COLLECTOR_LEASE_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<CollectorLease>;
    if (
      typeof value.owner !== "string"
      || typeof value.expiresAt !== "number"
      || !Number.isFinite(value.expiresAt)
      || value.expiresAt > Date.now() + COLLECTOR_LEASE_MS * 2
    ) {
      return null;
    }
    return { owner: value.owner, expiresAt: value.expiresAt };
  } catch {
    return null;
  }
}

function startStorageLeadership(onChange: (isLeader: boolean) => void): () => void {
  const owner = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  let isOwner = false;

  const updateLease = () => {
    try {
      const now = Date.now();
      const lease = readCollectorLease();
      if (!lease || lease.expiresAt <= now || lease.owner === owner) {
        window.localStorage.setItem(
          COLLECTOR_LEASE_KEY,
          JSON.stringify({ owner, expiresAt: now + COLLECTOR_LEASE_MS }),
        );
      }
      isOwner = readCollectorLease()?.owner === owner;
      onChange(isOwner);
    } catch {
      isOwner = true;
      onChange(true);
    }
  };

  const handleStorage = (event: StorageEvent) => {
    if (event.key === COLLECTOR_LEASE_KEY) updateLease();
  };
  updateLease();
  const heartbeat = window.setInterval(updateLease, COLLECTOR_HEARTBEAT_MS);
  window.addEventListener("storage", handleStorage);

  return () => {
    window.clearInterval(heartbeat);
    window.removeEventListener("storage", handleStorage);
    if (isOwner && readCollectorLease()?.owner === owner) {
      try {
        window.localStorage.removeItem(COLLECTOR_LEASE_KEY);
      } catch {
        // Storage is optional; the short lease expires if removal is unavailable.
      }
    }
  };
}

function useCollectorLeadership(): boolean {
  const [isLeader, setIsLeader] = useState(false);

  useEffect(() => {
    if (!navigator.locks?.request) {
      return startStorageLeadership(setIsLeader);
    }

    const abortController = new AbortController();
    let releaseLock: () => void = () => {};
    let stopStorageLeadership: (() => void) | null = null;
    let isDisposed = false;
    const startFallback = () => {
      if (isDisposed || stopStorageLeadership) return;
      stopStorageLeadership = startStorageLeadership(setIsLeader);
    };
    void navigator.locks.request(
      COLLECTOR_LOCK_NAME,
      { mode: "exclusive", signal: abortController.signal },
      async () => {
        if (abortController.signal.aborted) return;
        setIsLeader(true);
        await new Promise<void>((resolve) => {
          releaseLock = resolve;
        });
        if (!isDisposed) setIsLeader(false);
      },
    ).catch((error: unknown) => {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        startFallback();
      }
    });

    return () => {
      isDisposed = true;
      abortController.abort();
      releaseLock();
      stopStorageLeadership?.();
    };
  }, []);

  return isLeader;
}

function OperatorEventSubscriptions({
  onCaptureStatus,
  onSession,
}: {
  onCaptureStatus: (envelope: WsEnvelope) => void;
  onSession: (envelope: WsEnvelope) => void;
}) {
  useTopicEnvelopes(captureStatusConnection, onCaptureStatus);
  useTopicEnvelopes(sessionConnection, onSession);
  return null;
}

function sessionSeverity(phase: string): OperationalEventDraft["severity"] {
  const normalized = phase.toLowerCase();
  if (normalized.includes("fail") || normalized.includes("error")) return "danger";
  if (
    normalized.includes("captur")
    || normalized.includes("complete")
    || normalized.includes("ready")
  ) {
    return "success";
  }
  return "info";
}

const INITIAL_CONDITION_TITLES: Record<string, string[]> = {
  "Live feed is stale": ["Live feed is stale", "Live feed recovered"],
  "Recovery exhausted": ["Recovery exhausted", "Feed recovery resumed"],
  "Capture ingestion degraded": [
    "Capture ingestion degraded",
    "Capture ingestion recovered",
  ],
  "Compression completed": ["Compression completed", "Compression failed"],
  "Compression failed": ["Compression completed", "Compression failed"],
};

function newestMatchingEvent(
  events: OperationalEvent[],
  source: OperationalEvent["source"],
  titles: readonly string[],
): OperationalEvent | undefined {
  return events.find((event) =>
    event.source === source && titles.includes(event.title),
  );
}

function isPersistedInitialState(
  events: OperationalEvent[],
  draft: OperationalEventDraft,
): boolean {
  const titles = INITIAL_CONDITION_TITLES[draft.title];
  if (!titles) return false;
  return newestMatchingEvent(events, draft.source, titles)?.title === draft.title;
}

function isPersistedSessionPhase(
  events: OperationalEvent[],
  title: string,
): boolean {
  return events.find((event) =>
    event.source === "session" && event.title.startsWith("Session: "),
  )?.title === title;
}

export function OperatorEventsProvider({ children }: { children: React.ReactNode }) {
  const [events, setEvents] = useState<OperationalEvent[]>([]);
  const [activeToastIds, setActiveToastIds] = useState<string[]>([]);
  const [storageError, setStorageError] = useState<string | null>(null);
  const isCollectorLeader = useCollectorLeadership();
  const dayKeyRef = useRef(indiaDayKey());
  const eventsRef = useRef<OperationalEvent[]>([]);
  const sequenceRef = useRef(0);
  const instanceIdRef = useRef<string | null>(null);
  const previousCaptureRef = useRef<GlobalStatus | null>(null);
  const previousCompressionRef = useRef<CompressionProgressPayload | null>(null);
  const previousSessionPhaseRef = useRef<string | null>(null);
  const previousMonitorRestErrorRef = useRef<string | null>(null);
  const toastTimersRef = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  const replaceForCurrentDay = useCallback((timestamp: number): string => {
    const nextDayKey = indiaDayKey(timestamp);
    if (nextDayKey === dayKeyRef.current) return nextDayKey;

    const previousDayKey = dayKeyRef.current;
    dayKeyRef.current = nextDayKey;
    previousCaptureRef.current = null;
    previousCompressionRef.current = null;
    previousSessionPhaseRef.current = null;
    previousMonitorRestErrorRef.current = null;
    toastTimersRef.current.forEach((timer) => clearTimeout(timer));
    toastTimersRef.current.clear();
    setActiveToastIds([]);

    try {
      window.localStorage.removeItem(dailyEventsStorageKey(previousDayKey));
      window.localStorage.removeItem(dailyEventReadsStorageKey(previousDayKey));
      const cleanupError = clearExpiredDailyEvents(window.localStorage, nextDayKey);
      const loaded = loadDailyEvents(window.localStorage, nextDayKey);
      const nextEvents = applyReadEventIds(
        loaded.events,
        loadReadEventIds(window.localStorage, nextDayKey),
      );
      eventsRef.current = nextEvents;
      setEvents(nextEvents);
      setStorageError(cleanupError ?? loaded.error);
    } catch {
      eventsRef.current = [];
      setEvents([]);
      setStorageError("Today's operational messages could not be reset in browser storage.");
    }
    return nextDayKey;
  }, []);

  const scheduleToast = useCallback((event: OperationalEvent) => {
    if (!event.isNotification || toastTimersRef.current.has(event.id)) return;
    const remainingMs = TOAST_DURATION_MS - Math.max(0, Date.now() - event.ts);
    if (remainingMs <= 0) return;
    setActiveToastIds((current) =>
      current.includes(event.id) ? current : [event.id, ...current],
    );
    const timer = setTimeout(() => {
      setActiveToastIds((current) => current.filter((id) => id !== event.id));
      toastTimersRef.current.delete(event.id);
    }, remainingMs);
    toastTimersRef.current.set(event.id, timer);
  }, []);

  const publish = useCallback((draft: OperationalEventDraft) => {
    const timestamp = Date.now();
    const dayKey = replaceForCurrentDay(timestamp);
    instanceIdRef.current ??= globalThis.crypto?.randomUUID?.()
      ?? `${timestamp}-${Math.random().toString(36).slice(2)}`;
    const event: OperationalEvent = {
      ...draft,
      title: draft.title.slice(0, MAX_EVENT_TITLE_LENGTH),
      detail: draft.detail.slice(0, MAX_EVENT_DETAIL_LENGTH),
      id: `${timestamp}-${instanceIdRef.current}-${++sequenceRef.current}`,
      ts: timestamp,
      dayKey,
      isRead: !draft.isNotification,
    };

    const loaded = loadDailyEvents(window.localStorage, dayKey);
    const merged = mergeDailyEvents(
      eventsRef.current,
      [...loaded.events, event],
      dayKey,
    );
    const next = applyReadEventIds(
      merged,
      loadReadEventIds(window.localStorage, dayKey),
    );
    eventsRef.current = next;
    setEvents(next);
    setStorageError(
      loaded.error ?? saveDailyEvents(window.localStorage, dayKey, next),
    );

    scheduleToast(event);
  }, [replaceForCurrentDay, scheduleToast]);

  const markAllRead = useCallback(() => {
    const dayKey = replaceForCurrentDay(Date.now());
    const loaded = loadDailyEvents(window.localStorage, dayKey);
    const merged = mergeDailyEvents(eventsRef.current, loaded.events, dayKey);
    const readIds = new Set([
      ...loadReadEventIds(window.localStorage, dayKey),
      ...merged.filter((event) => event.isNotification).map(({ id }) => id),
    ]);
    const next = applyReadEventIds(merged, readIds).map((event) =>
      event.isNotification && !event.isRead ? { ...event, isRead: true } : event,
    );
    eventsRef.current = next;
    setEvents(next);
    const readError = saveReadEventIds(window.localStorage, dayKey, readIds);
    const eventError = saveDailyEvents(window.localStorage, dayKey, next);
    setStorageError(loaded.error ?? readError ?? eventError);
  }, [replaceForCurrentDay]);

  const reportMonitorRestError = useCallback((error: string | null) => {
    const normalizedError = error?.trim().slice(0, MAX_EVENT_DETAIL_LENGTH) || null;
    const previousError = previousMonitorRestErrorRef.current;
    if (normalizedError !== null && normalizedError === previousError) return;
    previousMonitorRestErrorRef.current = normalizedError;

    if (normalizedError) {
      if (previousError) return;
      const latestRestEvent = newestMatchingEvent(
        eventsRef.current,
        "client",
        ["Monitor REST refresh failed", "Monitor REST refresh recovered"],
      );
      if (latestRestEvent?.title === "Monitor REST refresh failed") return;
      publish({
        source: "client",
        severity: "warning",
        title: "Monitor REST refresh failed",
        detail: MONITOR_REST_FAILURE_DETAIL,
        isLog: false,
        isNotification: true,
      });
      return;
    }

    const latestRestEvent = newestMatchingEvent(
      eventsRef.current,
      "client",
      ["Monitor REST refresh failed", "Monitor REST refresh recovered"],
    );
    if (previousError || latestRestEvent?.title === "Monitor REST refresh failed") {
      publish({
        source: "client",
        severity: "success",
        title: "Monitor REST refresh recovered",
        detail: MONITOR_REST_RECOVERY_DETAIL,
        isLog: false,
        isNotification: true,
      });
    }
  }, [publish]);

  const onCaptureStatus = useCallback((envelope: WsEnvelope) => {
    if (envelope.type === MSG.CAPTURE_STATUS) {
      const payload = normalizeCaptureStatus(envelope.payload);
      if (!payload) return;
      const isInitialSnapshot = previousCaptureRef.current === null;
      deriveCaptureEvents(previousCaptureRef.current, payload.global)
        .filter((draft) =>
          !isInitialSnapshot
          || !isPersistedInitialState(eventsRef.current, draft),
        )
        .forEach(publish);
      previousCaptureRef.current = payload.global;
      return;
    }
    if (envelope.type !== MSG.COMPRESSION_PROGRESS) return;
    const compression = normalizeCompressionProgress(envelope.payload);
    if (!compression) return;
    const isInitialSnapshot = previousCompressionRef.current === null;
    deriveCompressionEvents(previousCompressionRef.current, compression)
      .filter((draft) =>
        !isInitialSnapshot
        || !isPersistedInitialState(eventsRef.current, draft),
      )
      .forEach(publish);
    previousCompressionRef.current = compression;
  }, [publish]);

  const onSession = useCallback((envelope: WsEnvelope) => {
    const payload = envelope.payload;
    if (envelope.type === MSG.LOG) {
      const message = payload
        && typeof payload === "object"
        && "message" in payload
        && typeof payload.message === "string"
        ? payload.message.trim().slice(0, MAX_LOG_MESSAGE_LENGTH)
        : "";
      if (message) {
        publish({
          source: "session",
          severity: "info",
          title: message,
          detail: "",
          isLog: true,
          isNotification: false,
        });
      }
      return;
    }
    if (envelope.type !== MSG.SESSION_STATUS) return;
    const phase = payload
      && typeof payload === "object"
      && "phase" in payload
      && typeof payload.phase === "string"
      ? payload.phase.trim().slice(0, MAX_SESSION_PHASE_LENGTH)
      : "";
    if (!phase || phase === previousSessionPhaseRef.current) return;
    const previousPhase = previousSessionPhaseRef.current;
    previousSessionPhaseRef.current = phase;
    const isInitialConnection = phase.toLowerCase() === "connected" && previousPhase === null;
    const title = `Session: ${phase}`;
    if (
      previousPhase === null
      && isPersistedSessionPhase(eventsRef.current, title)
    ) {
      return;
    }
    publish({
      source: "session",
      severity: sessionSeverity(phase),
      title,
      detail: previousPhase ? `Previous phase: ${previousPhase}.` : "",
      isLog: true,
      isNotification: !isInitialConnection,
    });
  }, [publish]);

  useEffect(() => {
    const dayKey = dayKeyRef.current;
    const cleanupError = clearExpiredDailyEvents(window.localStorage, dayKey);
    const loaded = loadDailyEvents(window.localStorage, dayKey);
    const hydrated = applyReadEventIds(
      mergeDailyEvents(loaded.events, eventsRef.current, dayKey),
      loadReadEventIds(window.localStorage, dayKey),
    );
    eventsRef.current = hydrated;
    setEvents(hydrated);
    setStorageError(cleanupError ?? loaded.error);

    const handleStorage = (event: StorageEvent) => {
      const currentDayKey = dayKeyRef.current;
      const eventsKey = dailyEventsStorageKey(currentDayKey);
      const readsKey = dailyEventReadsStorageKey(currentDayKey);
      if (event.key !== eventsKey && event.key !== readsKey) return;

      if (event.key === readsKey) {
        const readEvents = applyReadEventIds(
          eventsRef.current,
          loadReadEventIds(window.localStorage, currentDayKey),
        );
        eventsRef.current = readEvents;
        setEvents(readEvents);
        setStorageError(saveDailyEvents(
          window.localStorage,
          currentDayKey,
          readEvents,
        ));
        return;
      }

      const currentIds = new Set(eventsRef.current.map(({ id }) => id));
      const next = loadDailyEvents(window.localStorage, currentDayKey);
      const merged = applyReadEventIds(
        mergeDailyEvents(eventsRef.current, next.events, currentDayKey),
        loadReadEventIds(window.localStorage, currentDayKey),
      );
      eventsRef.current = merged;
      setEvents(merged);
      setStorageError(next.error);
      next.events
        .filter(({ id, isNotification }) => isNotification && !currentIds.has(id))
        .forEach(scheduleToast);

      if (merged.length > next.events.length) {
        setStorageError(saveDailyEvents(
          window.localStorage,
          currentDayKey,
          merged,
        ));
      }
    };
    const handleVisibility = () => {
      if (!document.hidden) replaceForCurrentDay(Date.now());
    };
    window.addEventListener("storage", handleStorage);
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      window.removeEventListener("storage", handleStorage);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [replaceForCurrentDay, scheduleToast]);

  useEffect(() => {
    let rolloverTimer: ReturnType<typeof setTimeout>;
    const scheduleRollover = () => {
      rolloverTimer = setTimeout(() => {
        replaceForCurrentDay(Date.now());
        scheduleRollover();
      }, millisecondsUntilIndiaMidnight());
    };
    scheduleRollover();
    return () => clearTimeout(rolloverTimer);
  }, [replaceForCurrentDay]);

  useEffect(() => () => {
    toastTimersRef.current.forEach((timer) => clearTimeout(timer));
    toastTimersRef.current.clear();
  }, []);

  const value = useMemo<OperatorEventsContextValue>(() => {
    const logs = events.filter((event) => event.isLog);
    const notifications = events.filter((event) => event.isNotification);
    const activeIds = new Set(activeToastIds);
    return {
      events,
      logs,
      notifications,
      activeToasts: notifications.filter((event) => activeIds.has(event.id)),
      storageError,
      publish,
      reportMonitorRestError,
      markAllRead,
    };
  }, [
    activeToastIds,
    events,
    markAllRead,
    publish,
    reportMonitorRestError,
    storageError,
  ]);

  return (
    <OperatorEventsContext.Provider value={value}>
      {isCollectorLeader && (
        <OperatorEventSubscriptions
          onCaptureStatus={onCaptureStatus}
          onSession={onSession}
        />
      )}
      {children}
    </OperatorEventsContext.Provider>
  );
}

export function useOperatorEvents(): OperatorEventsContextValue {
  return useContext(OperatorEventsContext);
}
