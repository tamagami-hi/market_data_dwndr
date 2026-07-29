import type {
  CompressionProgressPayload,
  GlobalStatus,
} from "@/lib/wsTypes";

export type OperationalEventSource = "capture" | "session" | "compression" | "client";
export type OperationalEventSeverity = "info" | "success" | "warning" | "danger";

export interface OperationalEvent {
  id: string;
  ts: number;
  dayKey: string;
  source: OperationalEventSource;
  severity: OperationalEventSeverity;
  title: string;
  detail: string;
  isLog: boolean;
  isNotification: boolean;
  isRead: boolean;
}

export type OperationalEventDraft = Omit<
  OperationalEvent,
  "id" | "ts" | "dayKey" | "isRead"
>;

interface StoredEvents {
  version: 1;
  dayKey: string;
  events: OperationalEvent[];
}

export interface LoadedEvents {
  events: OperationalEvent[];
  error: string | null;
}

const INDIA_OFFSET_MS = 330 * 60 * 1000;
const STORAGE_PREFIX = "tickvault:operational-events:v1:";
const READ_STORAGE_PREFIX = "tickvault:operational-event-reads:v1:";
const MAX_DATE_TIMESTAMP = 8_639_999_999_000_000;
const MAX_TITLE_LENGTH = 2_000;
const MAX_DETAIL_LENGTH = 4_000;

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isTimestampForDay(value: unknown, dayKey: string): value is number {
  if (
    !isFiniteNumber(value)
    || !Number.isSafeInteger(value)
    || Math.abs(value) > MAX_DATE_TIMESTAMP
  ) {
    return false;
  }
  try {
    return indiaDayKey(value) === dayKey;
  } catch {
    return false;
  }
}

function isOperationalEvent(value: unknown, dayKey: string): value is OperationalEvent {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const item = value as Record<string, unknown>;
  return (
    typeof item.id === "string"
    && isTimestampForDay(item.ts, dayKey)
    && item.dayKey === dayKey
    && ["capture", "session", "compression", "client"].includes(String(item.source))
    && ["info", "success", "warning", "danger"].includes(String(item.severity))
    && typeof item.title === "string"
    && item.title.length <= MAX_TITLE_LENGTH
    && typeof item.detail === "string"
    && item.detail.length <= MAX_DETAIL_LENGTH
    && typeof item.isLog === "boolean"
    && typeof item.isNotification === "boolean"
    && typeof item.isRead === "boolean"
  );
}

export function indiaDayKey(timestamp = Date.now()): string {
  return new Date(timestamp + INDIA_OFFSET_MS).toISOString().slice(0, 10);
}

export function millisecondsUntilIndiaMidnight(timestamp = Date.now()): number {
  const indiaDate = new Date(timestamp + INDIA_OFFSET_MS);
  const nextMidnightUtc = Date.UTC(
    indiaDate.getUTCFullYear(),
    indiaDate.getUTCMonth(),
    indiaDate.getUTCDate() + 1,
  ) - INDIA_OFFSET_MS;
  return Math.max(1, nextMidnightUtc - timestamp);
}

export function dailyEventsStorageKey(dayKey: string): string {
  return `${STORAGE_PREFIX}${dayKey}`;
}

export function dailyEventReadsStorageKey(dayKey: string): string {
  return `${READ_STORAGE_PREFIX}${dayKey}`;
}

export function clearExpiredDailyEvents(storage: Storage, currentDayKey: string): string | null {
  try {
    const currentKey = dailyEventsStorageKey(currentDayKey);
    const currentReadKey = dailyEventReadsStorageKey(currentDayKey);
    const expiredKeys = Array.from(
      { length: storage.length },
      (_, index) => storage.key(index),
    ).filter((key): key is string =>
      typeof key === "string"
      && (
        (key.startsWith(STORAGE_PREFIX) && key !== currentKey)
        || (key.startsWith(READ_STORAGE_PREFIX) && key !== currentReadKey)
      ),
    );
    expiredKeys.forEach((key) => storage.removeItem(key));
    return null;
  } catch {
    return "Expired operational messages could not be cleared from browser storage.";
  }
}

export function loadReadEventIds(storage: Storage, dayKey: string): Set<string> {
  try {
    const raw = storage.getItem(dailyEventReadsStorageKey(dayKey));
    if (!raw) return new Set();
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed) || !parsed.every((id) => typeof id === "string")) {
      return new Set();
    }
    return new Set(parsed);
  } catch {
    return new Set();
  }
}

export function saveReadEventIds(
  storage: Storage,
  dayKey: string,
  ids: Iterable<string>,
): string | null {
  try {
    const merged = new Set([
      ...loadReadEventIds(storage, dayKey),
      ...ids,
    ]);
    storage.setItem(
      dailyEventReadsStorageKey(dayKey),
      JSON.stringify([...merged].sort()),
    );
    return null;
  } catch {
    return "Notification read state could not be saved in browser storage.";
  }
}

export function applyReadEventIds(
  events: OperationalEvent[],
  readIds: ReadonlySet<string>,
): OperationalEvent[] {
  return events.map((event) =>
    event.isNotification && readIds.has(event.id) && !event.isRead
      ? { ...event, isRead: true }
      : event,
  );
}

export function mergeDailyEvents(
  current: OperationalEvent[],
  incoming: OperationalEvent[],
  dayKey: string,
): OperationalEvent[] {
  const byId = new Map<string, OperationalEvent>();
  [...current, ...incoming].forEach((event) => {
    if (event.dayKey !== dayKey) return;
    const existing = byId.get(event.id);
    byId.set(event.id, existing
      ? { ...event, isRead: existing.isRead || event.isRead }
      : event);
  });
  return [...byId.values()].sort((left, right) =>
    right.ts - left.ts || right.id.localeCompare(left.id),
  );
}

export function loadDailyEvents(storage: Storage, dayKey: string): LoadedEvents {
  try {
    const raw = storage.getItem(dailyEventsStorageKey(dayKey));
    if (!raw) return { events: [], error: null };
    const parsed = JSON.parse(raw) as Partial<StoredEvents>;
    if (
      parsed.version !== 1
      || parsed.dayKey !== dayKey
      || !Array.isArray(parsed.events)
      || !parsed.events.every((event) => isOperationalEvent(event, dayKey))
    ) {
      return {
        events: [],
        error: "Today's operational messages could not be restored because the saved data was invalid.",
      };
    }
    return {
      events: mergeDailyEvents([], parsed.events, dayKey),
      error: null,
    };
  } catch {
    return {
      events: [],
      error: "Today's operational messages could not be restored from browser storage.",
    };
  }
}

export function saveDailyEvents(
  storage: Storage,
  dayKey: string,
  events: OperationalEvent[],
): string | null {
  try {
    const payload: StoredEvents = {
      version: 1,
      dayKey,
      events: mergeDailyEvents([], events, dayKey),
    };
    storage.setItem(dailyEventsStorageKey(dayKey), JSON.stringify(payload));
    return null;
  } catch {
    return "Today's operational messages could not be saved in browser storage.";
  }
}

function eventDraft(
  title: string,
  detail: string,
  severity: OperationalEventSeverity,
  source: OperationalEventSource = "capture",
): OperationalEventDraft {
  return {
    source,
    severity,
    title,
    detail,
    isLog: true,
    isNotification: true,
  };
}

export function deriveCaptureEvents(
  previous: GlobalStatus | null,
  next: GlobalStatus,
): OperationalEventDraft[] {
  const events: OperationalEventDraft[] = [];
  const hadPrevious = previous !== null;

  if (next.stale && (!hadPrevious || !previous.stale)) {
    const age = next.data_age_ms === null
      ? "for an unknown interval"
      : `for ${(next.data_age_ms / 1000).toFixed(1)}s`;
    events.push(eventDraft(
      "Live feed is stale",
      `Market data has not changed ${age}.`,
      "danger",
      "capture",
    ));
  }
  if (
    hadPrevious
    && (previous.stale || previous.degraded)
    && !next.stale
    && !next.degraded
  ) {
    events.push(eventDraft(
      "Live feed recovered",
      "Fresh ticks have resumed.",
      "success",
      "capture",
    ));
  }
  if (hadPrevious && next.reconnects > previous.reconnects) {
    const tokenDetail = next.reconnect_tier === 2 ? " using a refreshed access token" : "";
    events.push(eventDraft(
      "Ticker reconnected",
      `Reconnect ${next.reconnects}${tokenDetail}.`,
      "warning",
      "capture",
    ));
  }
  if (next.exhausted && (!hadPrevious || !previous.exhausted)) {
    events.push(eventDraft(
      "Recovery exhausted",
      "Automatic feed recovery is exhausted; operator action is required.",
      "danger",
      "capture",
    ));
  }
  if (hadPrevious && previous.exhausted && !next.exhausted) {
    events.push(eventDraft(
      "Feed recovery resumed",
      "Automatic feed recovery is available again.",
      "success",
      "capture",
    ));
  }
  if (
    next.ingestion_degraded
    && (!hadPrevious || !previous.ingestion_degraded)
  ) {
    events.push(eventDraft(
      "Capture ingestion degraded",
      "The writer is not keeping up with incoming capture batches.",
      "warning",
      "capture",
    ));
  }
  if (
    hadPrevious
    && previous.ingestion_degraded
    && !next.ingestion_degraded
  ) {
    events.push(eventDraft(
      "Capture ingestion recovered",
      "Capture batch processing has returned to normal.",
      "success",
      "capture",
    ));
  }
  if (
    hadPrevious
    && (next.grid_gaps ?? 0) > (previous.grid_gaps ?? 0)
  ) {
    events.push(eventDraft(
      "Capture grid gap detected",
      `${next.grid_gaps ?? 0} unrecoverable grid gap(s) recorded this session.`,
      "danger",
      "capture",
    ));
  }
  return events;
}

export function deriveCompressionEvents(
  previous: CompressionProgressPayload | null,
  next: CompressionProgressPayload,
): OperationalEventDraft[] {
  if (previous?.phase === next.phase) return [];
  if (next.phase === "done") {
    return [eventDraft(
      "Compression completed",
      `${next.files_done} file(s) compressed.`,
      "success",
      "compression",
    )];
  }
  if (next.phase === "failed") {
    return [eventDraft(
      "Compression failed",
      next.current_file ? `Failed while processing ${next.current_file}.` : "The archive job failed.",
      "danger",
      "compression",
    )];
  }
  return [];
}
