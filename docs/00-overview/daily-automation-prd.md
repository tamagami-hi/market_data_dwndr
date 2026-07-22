# Daily downloader automation PRD

## Outcome

Run the downloader unattended on the home VPS while preserving an explicit operator
decision for the 10-year bond yield. The shared calspread.online session is the primary
authentication source; the TOTP flow remains a manual fallback.

## Required behaviour

- Preserve all market prices as exact integer paise in BIN files. Greeks remain derived
  data and are not persisted.
- Never contact the shared token broker before 08:30 IST.
- Poll the broker from 08:30 inclusive until 09:00 exclusive on trading days, stopping
  after a validated token is saved.
- On a late Monday–Friday market-day start, validate the newest persisted token first;
  if it fails, continue broker polling during market hours until capture can start.
- Start capture at 09:00 when token and yield are capture-ready; stop and flush at
  15:30, then run verified EOD compression.
- Reuse the previous yield on the next Monday–Friday market day. On the third market day from
  its `as_of` date, require an operator update before capture can begin.
- Transport and display the already-persisted five-level stock order book.
- Ship the deployable stack under `release_manager/DATA_DOWNLOADER` without Nginx.

## Acceptance

Automation exposes a redacted status and next action, survives restarts idempotently,
never logs broker secrets/tokens, never starts capture with a stale required yield, and
never compresses before capture writers have stopped.
