# Daily automation technical note

- Configuration: `AUTH_POLL_START`, `AUTH_POLL_END`, and
  `AUTH_POLL_INTERVAL_SECONDS`, with validated ordering against `MARKET_OPEN` and
  `MARKET_CLOSE`.
- Time: `TradingCalendar` remains the single IST conversion boundary.
- Persistence: daily JSON files stay mode 0600 and are atomically replaced. Access
  tokens never enter API responses or logs.
- Authentication boundary: Calspread alone performs Kite login and holds the resulting token.
  This downloader's lookup is read-only and idempotent; the same token is reused until an
  administrator relogs in through Calspread. Feed recovery performs no session lookup.
- Auth failure: an explicit Kite rejection enters `AUTH_BLOCKED`; repeated retrieval of the
  same token cannot recover it. A different token must validate before state advances.
- Scheduling: async sleep/tick loop; blocking broker validation and compression use
  threadpool execution.
- Price precision: Kite rupee values are converted to integer paise once and round-trip
  exactly. UI formatting supplies two visible decimal places.
- Greeks: IV and Greeks are computed by reconstruction and WebSocket broadcasting; BIN
  files retain only the raw inputs and the header risk-free rate.
- L5: persisted spot/current/mid/far depth is serialized as five bid/ask levels for the
  frontend and rendered collapsed by default.

The current `AUTH_POLL_*` names and combined daily session file are transitional. The target
design uses session-sync terminology and separates credential revision from daily risk-rate
metadata. See [[broker-session-authentication]].
