# Daily automation technical note

- Configuration: `AUTH_POLL_START`, `AUTH_POLL_END`, and
  `AUTH_POLL_INTERVAL_SECONDS`, with validated ordering against `MARKET_OPEN` and
  `MARKET_CLOSE`.
- Time: `TradingCalendar` remains the single IST conversion boundary.
- Persistence: daily JSON files stay mode 0600 and are atomically replaced. Access
  tokens never enter API responses or logs.
- Scheduling: async sleep/tick loop; blocking broker validation and compression use
  threadpool execution.
- Price precision: Kite rupee values are converted to integer paise once and round-trip
  exactly. UI formatting supplies two visible decimal places.
- Greeks: IV and Greeks are computed by reconstruction and WebSocket broadcasting; BIN
  files retain only the raw inputs and the header yield.
- L5: persisted spot/current/mid/far depth is serialized as five bid/ask levels for the
  frontend and rendered collapsed by default.
