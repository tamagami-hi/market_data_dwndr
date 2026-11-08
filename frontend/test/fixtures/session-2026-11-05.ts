/**
 * 2026-11-05 as GET /api/stats session_history returns it once described by the backend.
 *
 * Captured verbatim from the backend's replay of that day (backend
 * tests/test_session_incidents.py::_replay_2026_11_05, run through describe_rows): capture
 * 09:10–10:23, a feed stall, then ~100 processes that each skipped all six index chains
 * and failed to connect until 11:14:10, a 45 s restart at 14:10, close at 15:40, EOD with
 * archive reconciliation. Timestamps are epoch ms; 1793854380000 is 10:23:00 IST.
 */
export const SESSION_2026_11_05_DESCRIBED: Record<string, unknown> = {
  "archive_completeness_pct": 86.21,
  "archive_reconciliation": {
    "artifact_count": 8,
    "captured_seconds": 19914,
    "completeness_pct": 86.21,
    "data_loss_pct": 13.79,
    "gap_count": 3,
    "longest_gap_seconds": 0,
    "missing_seconds": 3186,
    "scheduled_seconds": 23100,
    "worst_artifact": "NIFTY"
  },
  "awaiting_first_tick_seconds": 0,
  "capture_failures": 102,
  "capture_starts": 103,
  "captured_seconds": 19916,
  "captures": 19916,
  "completeness_basis_seconds": 23100,
  "completeness_pct": 86.216,
  "data_loss_pct": 13.784,
  "datasets_missing": [
    {
      "dataset": "BANKEX",
      "from_at": 1793854420000,
      "quote_error": "ConnectionError: HTTPSConnectionPool(host='api.kite.trade'): Temporary failure in name resolution (<urllib3 object at 0x…>)",
      "reason": "spot must be > 0",
      "seconds": 3030,
      "starts": 100,
      "to_at": 1793857450000
    },
    {
      "dataset": "BANKNIFTY",
      "from_at": 1793854420000,
      "quote_error": "ConnectionError: HTTPSConnectionPool(host='api.kite.trade'): Temporary failure in name resolution (<urllib3 object at 0x…>)",
      "reason": "spot must be > 0",
      "seconds": 3030,
      "starts": 100,
      "to_at": 1793857450000
    },
    {
      "dataset": "FINNIFTY",
      "from_at": 1793854420000,
      "quote_error": "ConnectionError: HTTPSConnectionPool(host='api.kite.trade'): Temporary failure in name resolution (<urllib3 object at 0x…>)",
      "reason": "spot must be > 0",
      "seconds": 3030,
      "starts": 100,
      "to_at": 1793857450000
    },
    {
      "dataset": "MIDCPNIFTY",
      "from_at": 1793854420000,
      "quote_error": "ConnectionError: HTTPSConnectionPool(host='api.kite.trade'): Temporary failure in name resolution (<urllib3 object at 0x…>)",
      "reason": "spot must be > 0",
      "seconds": 3030,
      "starts": 100,
      "to_at": 1793857450000
    },
    {
      "dataset": "NIFTY",
      "from_at": 1793854420000,
      "quote_error": "ConnectionError: HTTPSConnectionPool(host='api.kite.trade'): Temporary failure in name resolution (<urllib3 object at 0x…>)",
      "reason": "spot must be > 0",
      "seconds": 3030,
      "starts": 100,
      "to_at": 1793857450000
    },
    {
      "dataset": "SENSEX",
      "from_at": 1793854420000,
      "quote_error": "ConnectionError: HTTPSConnectionPool(host='api.kite.trade'): Temporary failure in name resolution (<urllib3 object at 0x…>)",
      "reason": "spot must be > 0",
      "seconds": 3030,
      "starts": 100,
      "to_at": 1793857450000
    }
  ],
  "downtime_seconds": 3130,
  "eod": {
    "archive_data_loss_pct": 13.79,
    "artifact_count": 8,
    "at": 1793873520000,
    "files": 8,
    "status": "ok",
    "worst_artifact": "NIFTY"
  },
  "eod_runs": 1,
  "escalations": 2,
  "explanation": [
    "partial: 86.2% of 23100 scheduled seconds captured (archive figure is worse and decides)",
    "51 min lost to a restart loop (capture repeatedly failed to start or connect)",
    "54 s lost to a stale feed (frames suppressed)",
    "45 s lost to process restarts",
    "15 s lost to capture not running, no recorded reason",
    "restart loop 10:23-11:14: 100 failed start(s) (TickerConnectFailed) after CaptureStalledError; index chains skipped: BANKEX, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTY, SENSEX",
    "BANKEX missing for 50 min (spot must be > 0)",
    "BANKNIFTY missing for 50 min (spot must be > 0)",
    "FINNIFTY missing for 50 min (spot must be > 0)",
    "MIDCPNIFTY missing for 50 min (spot must be > 0)",
    "NIFTY missing for 50 min (spot must be > 0)",
    "SENSEX missing for 50 min (spot must be > 0)"
  ],
  "failed_starts": 100,
  "final": true,
  "frame_loss_pct": 13.784,
  "frames_expected": 184800,
  "frames_written": 159328,
  "generations": 103,
  "grid_gaps": 2,
  "grid_seconds_elapsed": 23100,
  "grid_seconds_lost": 0,
  "incidents_recorded": true,
  "loss_causes": {
    "awaiting_first_tick_seconds": 0,
    "awaiting_token_seconds": 0,
    "maintenance_seconds": 0,
    "process_down_seconds": 15,
    "restart_loop_seconds": 3070,
    "restart_seconds": 45,
    "stale_feed_seconds": 54,
    "unclassified_seconds": 0,
    "write_path_seconds": 0
  },
  "missing_seconds": 3184,
  "not_running_seconds": 3032,
  "outages": [
    {
      "cause": "restart_loop",
      "end_at": 1793857450000,
      "exceptions": [
        "TickerConnectFailed"
      ],
      "failed_starts": 100,
      "recovered": true,
      "seconds": 3070,
      "skipped_indices": [
        "BANKEX",
        "BANKNIFTY",
        "FINNIFTY",
        "MIDCPNIFTY",
        "NIFTY",
        "SENSEX"
      ],
      "start_at": 1793854380000,
      "trigger": "CaptureStalledError"
    },
    {
      "cause": "restart",
      "end_at": 1793868045000,
      "exceptions": [],
      "failed_starts": 0,
      "recovered": true,
      "seconds": 45,
      "skipped_indices": [],
      "start_at": 1793868000000,
      "trigger": "CaptureStalledError"
    }
  ],
  "outcome": "partial",
  "outcome_basis": "archive",
  "process_exits": 102,
  "process_starts": 104,
  "restart_downtime_seconds": 98,
  "scheduled_loss_pct": 13.784,
  "scheduled_seconds": 23100,
  "scheduled_seconds_elapsed": 23100,
  "session_frames_expected": 23046,
  "session_loss_pct": 13.582,
  "session_window": {
    "close": "15:40",
    "is_special": false,
    "note": null,
    "open": "09:15",
    "scheduled_seconds": 23100,
    "trading_day": true,
    "windows": [
      [
        1793850300000,
        1793873400000
      ]
    ]
  },
  "stale_feed_seconds": 54,
  "stale_seconds": 54,
  "stops": [
    {
      "at": 1793873400000,
      "clean": true,
      "count": 1,
      "reason": "market_close"
    }
  ],
  "trading_date": "2026-11-05",
  "unclassified_seconds": 0,
  "write_path_seconds": 0
};
