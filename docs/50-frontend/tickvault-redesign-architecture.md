# TickVault Frontend Redesign Architecture

## Layers

1. The App Router shell supplies route metadata, the skip link, the responsive navigation, page width policy, and global semantic tokens.
2. Shared UI primitives supply page headers, panels, metrics, status indicators, disclosures, tables, loading and error surfaces, explanations, and dialogs.
3. Feature adapters validate unknown REST and WebSocket payloads at the frontend boundary and return safe immutable values.
4. Feature view-model modules derive display state and severity without React.
5. Hooks own subscriptions, polling, cancellation, freshness, and recovery.
6. Route components compose focused feature sections.

## Monitor boundary

`useMonitorTelemetry` is the only owner of monitor WebSocket events and the `/api/stats` and `/api/capture/history` polling lifecycles. WebSocket values own live capture data. REST values own history, compression fallback, and retained snapshots while capture is idle.

The hook exposes immutable raw state plus derived freshness, retained-session context, last-success time, and errors. Live sections consume the 1 Hz values. Historical and storage sections receive stable props and are memoized so live events do not rerender them.

## Market-data boundaries

- Option Grid keyframes replace one symbol snapshot.
- Option Grid deltas immutably copy only changed columns and values.
- Stock Board payloads are normalized before projection into stable rows.
- Complete desktop matrices and mobile disclosures read from the same normalized row model.

## Shared state

WebSocket connections remain the existing ref-counted module singletons. No new global store is introduced. Local UI state owns selected symbols, filters, and disclosures.

## Compatibility

The effective live contract is `frontend/lib/wsTypes.ts` together with `backend/app/capture/broadcaster.py`. The older protocol document is descriptive history and must not be used to reduce current reconstructed option fields or the columnar stock-depth board.
