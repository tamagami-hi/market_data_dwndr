export interface AutomationStateView {
  phase?: string;
  last_action?: string | null;
  last_error?: string | null;
  last_broker_poll_at?: number | null;
  eod_completed_date?: string | null;
  eod_in_progress_date?: string | null;
}

export function automationMessage(
  automation: AutomationStateView | undefined,
  isCaptureReady: boolean,
): string {
  if (automation?.phase === "auth_window") {
    if (automation.last_error) {
      return "Shared token is not ready yet. The server will retry during the 08:30–09:00 IST window.";
    }
    return "The server is checking calspread.online for today's shared Kite token.";
  }
  if (automation?.phase === "capture_window") {
    if (isCaptureReady) {
      return "Daily authentication is ready. Capture runs automatically from 09:00 to 15:30 IST.";
    }
    return "No capture-ready session yet. The server is validating the previous token and polling the shared-token service during market hours.";
  }
  if (automation?.phase === "eod") {
    if (automation.eod_in_progress_date) {
      return `End-of-day compression is running for ${automation.eod_in_progress_date}.`;
    }
    return automation.eod_completed_date
      ? `End-of-day compression completed for ${automation.eod_completed_date}.`
      : "Waiting for end-of-day compression status.";
  }
  return "Token polling starts automatically at 08:30 IST on trading days.";
}
