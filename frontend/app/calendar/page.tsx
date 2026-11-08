"use client";

import { useCallback, useEffect, useState } from "react";

import {
  BUTTON_CLASS,
  HolidayForm,
  OperatorKeyField,
  PRIMARY_BUTTON_CLASS,
  SpecialSessionForm,
} from "@/components/calendar/CalendarForms";
import { Metric } from "@/components/ui/Metric";
import { PageFrame } from "@/components/ui/PageFrame";
import { PageHeader } from "@/components/ui/PageHeader";
import { Panel } from "@/components/ui/Panel";
import { StateMessage } from "@/components/ui/StateMessage";
import { StatusIndicator } from "@/components/ui/StatusIndicator";
import { createPollController } from "@/hooks/polling";
import {
  addHoliday,
  fetchTokenNow,
  formatDay,
  getMarketCalendar,
  readOperatorToken,
  removeHoliday,
  removeSpecialSession,
  saveSpecialSession,
  storeOperatorToken,
  type CalendarHoliday,
  type MarketCalendar,
  type SpecialSessionInput,
} from "@/lib/marketCalendar";

type Notice = { tone: "success" | "danger"; text: string } | null;

const POLL_INTERVAL_MS = 10_000;

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Request failed.";
}

export default function MarketCalendarPage() {
  const [calendar, setCalendar] = useState<MarketCalendar | null | undefined>(undefined);
  const [operatorToken, setOperatorToken] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  useEffect(() => {
    let isMounted = true;
    const controller = createPollController({
      intervalMs: () => POLL_INTERVAL_MS,
      isPaused: () => document.hidden,
      task: async (signal) => {
        try {
          const next = await getMarketCalendar(signal);
          if (!isMounted) return;
          setCalendar(next);
          // Restore a key typed earlier in this tab (sessionStorage is client-only).
          setOperatorToken((current) => current || readOperatorToken());
        } catch (error) {
          if (isMounted && !(error instanceof DOMException && error.name === "AbortError")) {
            setCalendar(null);
          }
        }
      },
    });
    const handleVisibility = () => {
      if (!document.hidden) controller.resume();
    };
    controller.start();
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      isMounted = false;
      document.removeEventListener("visibilitychange", handleVisibility);
      controller.stop();
    };
  }, []);

  const handleTokenChange = (value: string) => {
    setOperatorToken(value);
    storeOperatorToken(value);
  };

  const run = useCallback(
    async (action: () => Promise<MarketCalendar>, success: string): Promise<boolean> => {
      if (!operatorToken) {
        setNotice({ tone: "danger", text: "Enter the operator key first." });
        return false;
      }
      setIsBusy(true);
      setNotice(null);
      try {
        setCalendar(await action());
        setNotice({ tone: "success", text: success });
        return true;
      } catch (error) {
        setNotice({ tone: "danger", text: errorMessage(error) });
        return false;
      } finally {
        setIsBusy(false);
      }
    },
    [operatorToken],
  );

  const today = calendar?.today;
  const todayDate = today?.date ?? "";

  return (
    <PageFrame>
      <PageHeader
        title="Market Calendar"
        eyebrow="Holidays and special open days"
        description="Close a weekday for a holiday, or open any day — weekends included — for a special session with its own market hours and token fetch time."
        actions={
          today ? (
            <StatusIndicator
              label={today.special_session ? "special session today" : today.is_trading_day ? "trading day" : "market closed today"}
              severity={today.special_session ? "warning" : today.is_trading_day ? "success" : "neutral"}
            />
          ) : undefined
        }
      />

      <div className="mx-auto w-full max-w-5xl space-y-3">
        {calendar === null && (
          <StateMessage title="Backend unreachable" severity="danger" role="alert">
            The calendar could not be loaded. This page retries every few seconds.
          </StateMessage>
        )}
        {notice && (
          <StateMessage title={notice.tone === "success" ? "Saved" : "Not saved"} severity={notice.tone} role={notice.tone === "danger" ? "alert" : "status"}>
            {notice.text}
          </StateMessage>
        )}

        {calendar && calendar.load_errors && calendar.load_errors.length > 0 && (
          <StateMessage title="Some calendar entries were skipped" severity="warning" role="alert">
            {calendar.load_errors.join(" ")} Re-save the affected days from this page.
          </StateMessage>
        )}

        <Panel title="Operator key" subtitle="needed for every change">
          <OperatorKeyField value={operatorToken} onChange={handleTokenChange} />
        </Panel>

        {calendar && today && (
          <TodayPanel
            calendar={calendar}
            isBusy={isBusy}
            onFetchToken={() => run(() => fetchTokenNow(operatorToken), "Today's broker token was fetched and validated.")}
          />
        )}

        {calendar && (
          <>
            <Panel title="Special open days" subtitle="one window for every segment; no pre-open or closing auction">
              <SpecialSessionForm
                key={todayDate}
                defaultDate={todayDate}
                defaultOpen={calendar.regular_hours.market_open}
                defaultClose={calendar.regular_hours.market_close}
                isBusy={isBusy}
                onSubmit={(session: SpecialSessionInput) =>
                  run(() => saveSpecialSession(operatorToken, session), `Special session saved for ${session.date}.`)
                }
              />
              <EntryList
                emptyText="No special open days scheduled."
                rows={calendar.special_sessions.map((session) => ({
                  key: session.date,
                  title: `${formatDay(session.date)} · ${session.open}–${session.close}`,
                  detail: `Token from ${session.token_fetch_at} · capture from ${session.capture_lead_minutes} min before open${session.note ? ` · ${session.note}` : ""}`,
                  isPast: session.date < todayDate,
                }))}
                isBusy={isBusy}
                onRemove={(day) => run(() => removeSpecialSession(operatorToken, day), `Special session on ${day} removed.`)}
              />
            </Panel>

            <Panel title="Holidays" subtitle="no token fetch, no capture, no end-of-day run">
              <HolidayForm
                key={todayDate}
                defaultDate={todayDate}
                isBusy={isBusy}
                onSubmit={(holiday: CalendarHoliday) =>
                  run(() => addHoliday(operatorToken, holiday), `Holiday added for ${holiday.date}.`)
                }
              />
              <EntryList
                emptyText="No holidays added here."
                rows={calendar.holidays.map((holiday) => ({
                  key: holiday.date,
                  title: formatDay(holiday.date),
                  detail: holiday.note,
                  isPast: holiday.date < todayDate,
                }))}
                isBusy={isBusy}
                onRemove={(day) => run(() => removeHoliday(operatorToken, day), `Holiday on ${day} removed.`)}
              />
              {calendar.env_holidays.length > 0 && (
                <div className="border-t border-border p-3">
                  <p className="label text-muted">From MARKET_HOLIDAYS in .env (read-only)</p>
                  <p className="mt-1 font-mono text-xs leading-relaxed text-secondary">
                    {calendar.env_holidays.join(", ")}
                  </p>
                </div>
              )}
            </Panel>
          </>
        )}
      </div>
    </PageFrame>
  );
}

function TodayPanel({
  calendar,
  isBusy,
  onFetchToken,
}: {
  calendar: MarketCalendar;
  isBusy: boolean;
  onFetchToken: () => void;
}) {
  const today = calendar.today;
  const special = today.special_session;
  const automationError = today.automation?.last_error;
  return (
    <Panel
      title="Today"
      subtitle={`${formatDay(today.date)} · ${today.local_time} IST`}
      action={
        <button type="button" disabled={isBusy || !today.is_trading_day} onClick={onFetchToken} className={today.token_ready ? BUTTON_CLASS : PRIMARY_BUTTON_CLASS}>
          {today.token_ready ? "Re-check token" : "Fetch token now"}
        </button>
      }
    >
      <div className="grid gap-3 p-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric
          label="Market hours"
          value={
            today.is_trading_day
              ? special
                ? `${special.open}–${special.close}`
                : `${calendar.regular_hours.market_open}–${calendar.regular_hours.market_close}`
              : "closed"
          }
          detail={special ? special.note || "special session" : today.market_phase}
          severity={special ? "warning" : "neutral"}
        />
        <Metric label="Token window" value={today.is_trading_day ? today.token_window.join("–") : "--"} />
        <Metric
          label="Capture window"
          value={today.is_trading_day ? today.capture_window.join("–") : "--"}
          detail={today.capture_running ? "capture running" : undefined}
          severity={today.capture_running ? "success" : "neutral"}
        />
        <Metric
          label="Kite token"
          value={today.token_ready ? "ready" : "not fetched"}
          detail={today.token_fetched_at ? new Date(today.token_fetched_at).toLocaleTimeString("en-IN") : undefined}
          severity={today.token_ready ? "success" : today.is_trading_day ? "warning" : "neutral"}
        />
      </div>
      {automationError && (
        <div className="border-t border-border p-3">
          <StateMessage title="Automation needs attention" severity="warning">
            {automationError}
          </StateMessage>
        </div>
      )}
    </Panel>
  );
}

function EntryList({
  rows,
  emptyText,
  isBusy,
  onRemove,
}: {
  rows: { key: string; title: string; detail: string; isPast: boolean }[];
  emptyText: string;
  isBusy: boolean;
  onRemove: (day: string) => void;
}) {
  if (rows.length === 0) {
    return <p className="border-t border-border p-3 text-xs text-muted">{emptyText}</p>;
  }
  return (
    <ul className="divide-y divide-border border-t border-border">
      {rows.map((row) => (
        <li key={row.key} className="flex items-center justify-between gap-3 px-3 py-2">
          <div className="min-w-0">
            <p className={`text-sm font-medium ${row.isPast ? "text-muted" : "text-primary"}`}>
              {row.title}
              {row.isPast && <span className="ml-2 text-xs font-normal">(past)</span>}
            </p>
            {row.detail && <p className="truncate text-xs text-secondary">{row.detail}</p>}
          </div>
          <button type="button" disabled={isBusy} onClick={() => onRemove(row.key)} className={BUTTON_CLASS} aria-label={`Remove ${row.title}`}>
            Remove
          </button>
        </li>
      ))}
    </ul>
  );
}
