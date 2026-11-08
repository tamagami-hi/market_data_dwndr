"use client";

import { useState, type FormEvent, type ReactNode } from "react";

import { minusMinutes, type CalendarHoliday, type SpecialSessionInput } from "@/lib/marketCalendar";

/** Mirrors the backend default: token fetched this many minutes before capture start. */
const DEFAULT_TOKEN_LEAD_MINUTES = 29;

const INPUT_CLASS =
  "control min-h-11 w-full border-border bg-surface-2 px-3 text-sm text-primary placeholder:text-muted";
export const BUTTON_CLASS =
  "control inline-flex min-h-11 items-center justify-center border-border bg-surface-2 px-4 text-sm font-medium text-primary hover:bg-surface-3 disabled:cursor-not-allowed disabled:opacity-50";
export const PRIMARY_BUTTON_CLASS =
  "control inline-flex min-h-11 items-center justify-center border-accent/60 bg-accent/15 px-4 text-sm font-semibold text-accent hover:bg-accent/25 disabled:cursor-not-allowed disabled:opacity-50";

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="grid gap-1">
      <span className="label text-muted">{label}</span>
      {children}
      {hint && <span className="text-[11px] leading-snug text-muted">{hint}</span>}
    </label>
  );
}

export function SpecialSessionForm({
  defaultDate,
  defaultOpen,
  defaultClose,
  isBusy,
  onSubmit,
}: {
  defaultDate: string;
  /** Pre-filled with the configured regular market hours; the operator edits them. */
  defaultOpen: string;
  defaultClose: string;
  isBusy: boolean;
  onSubmit: (session: SpecialSessionInput) => Promise<boolean>;
}) {
  const [date, setDate] = useState(defaultDate);
  const [open, setOpen] = useState(defaultOpen);
  const [close, setClose] = useState(defaultClose);
  const [lead, setLead] = useState(5);
  const [tokenAt, setTokenAt] = useState("");
  const [note, setNote] = useState("");

  const captureStart = minusMinutes(open, lead);
  const suggestedToken = minusMinutes(captureStart, DEFAULT_TOKEN_LEAD_MINUTES);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const saved = await onSubmit({
      date,
      open,
      close,
      token_fetch_at: tokenAt || undefined,
      capture_lead_minutes: lead,
      note,
    });
    if (saved) setNote("");
  }

  return (
    <form onSubmit={handleSubmit} className="grid gap-3 p-3 sm:grid-cols-2 lg:grid-cols-3">
      <Field label="Date">
        <input type="date" required value={date} min={defaultDate} onChange={(e) => setDate(e.target.value)} className={INPUT_CLASS} />
      </Field>
      <Field label="Market open (IST)">
        <input type="time" required value={open} onChange={(e) => setOpen(e.target.value)} className={INPUT_CLASS} />
      </Field>
      <Field label="Market close (IST)">
        <input type="time" required value={close} onChange={(e) => setClose(e.target.value)} className={INPUT_CLASS} />
      </Field>
      <Field label="Start capture early (minutes)" hint={captureStart ? `Capture process starts at ${captureStart}.` : undefined}>
        <input
          type="number"
          min={0}
          max={120}
          required
          value={lead}
          onChange={(e) => setLead(Math.max(0, Math.min(120, Number(e.target.value) || 0)))}
          className={INPUT_CLASS}
        />
      </Field>
      <Field label="Fetch token from (IST)" hint={`Leave blank for ${suggestedToken || `${DEFAULT_TOKEN_LEAD_MINUTES} minutes before capture`}. Retries every minute until it succeeds.`}>
        <input type="time" value={tokenAt} onChange={(e) => setTokenAt(e.target.value)} className={INPUT_CLASS} />
      </Field>
      <Field label="Note">
        <input value={note} maxLength={120} placeholder="e.g. Special trading session" onChange={(e) => setNote(e.target.value)} className={INPUT_CLASS} />
      </Field>
      <div className="sm:col-span-2 lg:col-span-3">
        <button type="submit" disabled={isBusy} className={PRIMARY_BUTTON_CLASS}>
          Save special open day
        </button>
      </div>
    </form>
  );
}

export function HolidayForm({
  defaultDate,
  isBusy,
  onSubmit,
}: {
  defaultDate: string;
  isBusy: boolean;
  onSubmit: (holiday: CalendarHoliday) => Promise<boolean>;
}) {
  const [date, setDate] = useState(defaultDate);
  const [note, setNote] = useState("");

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const saved = await onSubmit({ date, note });
    if (saved) setNote("");
  }

  return (
    <form onSubmit={handleSubmit} className="grid gap-3 p-3 sm:grid-cols-[12rem_minmax(0,1fr)_auto] sm:items-end">
      <Field label="Date">
        <input type="date" required value={date} min={defaultDate} onChange={(e) => setDate(e.target.value)} className={INPUT_CLASS} />
      </Field>
      <Field label="Note">
        <input value={note} maxLength={120} placeholder="e.g. Exchange holiday" onChange={(e) => setNote(e.target.value)} className={INPUT_CLASS} />
      </Field>
      <button type="submit" disabled={isBusy} className={PRIMARY_BUTTON_CLASS}>
        Add holiday
      </button>
    </form>
  );
}

export function OperatorKeyField({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="grid gap-2 p-3 sm:grid-cols-[minmax(0,24rem)_1fr] sm:items-end">
      <Field label="Operator key" hint="RELEASE_MAINTENANCE_TOKEN from the backend .env. Used for one change, then cleared — enter it again before the next.">
        <input
          type="password"
          autoComplete="off"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Required to save changes or fetch a token"
          className={INPUT_CLASS}
        />
      </Field>
    </div>
  );
}
