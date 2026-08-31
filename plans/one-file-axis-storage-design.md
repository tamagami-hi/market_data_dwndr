---
title: One-File Axis Storage Design — replacing physical .pN.bin segment rolling
area: data-and-storage
type: design
status: proposed
tags: [area/data-and-storage, type/design, status/proposed, bin-format, axis-identity, rc-2]
up: "[[Home]]"
related: ["[[tickvault-data-loss-root-cause-and-remediation-plan]]", "[[as-built-repository-overview]]", "[[bin-structure-spec]]"]
---

# One-File Axis Storage Design

**Goal.** Reach the invariant `one artifact + one trading date = one physical .bin file`
without recreating RC-2 (frames appended under a header that describes a different strike
axis).

**Status.** Design only. No implementation code has been changed by this document.

---

## 1. Recommendation up front

Implement **C (inline axis-definition records, schema v2)** as the storage *format*, and
adopt **B (fixed daily full-universe axis)** as the axis *policy* that runs on top of it.

They are complementary, not alternatives:

| | What it does | What it cannot do |
|---|---|---|
| **B** | Removes the *cause*. The axis stops being a function of live spot, so restarts stop producing new axes. Measured compressed cost ≈ **zero**. | Cannot *prove* anything. The file still does not record which axis a frame used, and B alone cannot express a legitimate intraday axis change. |
| **C** | Removes the *possibility*. Every frame names its axis; a foreign-axis decode becomes structurally impossible. Satisfies every requirement in the brief. | Does not by itself reduce how often the axis changes. |

B is the cheap, immediate mitigation. C is what actually earns the right to delete
`resolve_segment_path`. Ship C's reader first, then C's writer, then B's policy.

Only C satisfies the stated requirement *"support an arbitrary number of axis changes
during the session"*. B satisfies it vacuously by making the count zero, which is not the
same thing as supporting it.

---

## 2. Why A must go — measured on the live system, 2026-08-31

This is not a hypothetical. The following was measured on the production VPS during the
live session on 2026-08-31 (market open, `/srv/dev_stack/DATA_DOWNLOADER`).

### 2.1 A file can contain three different axes

`app/bin_codec/layout.py` encodes every column as `Vec<T>` = `u64 count` + elements. Vec
lengths are therefore **self-describing**, so a frame written at the wrong width still
decodes cleanly — it just maps onto the wrong strikes. A direct census of the actual
per-frame widths (the first Vec count sits at `payload_offset + 36`):

```
INDICES/NIFTY/2026-08-31.bin
  header_axis=94  frames=9477  distinct_widths={94: 5, 92: 4534, 91: 4938}  *** MIXED ***
    width=94    frames=5       ts 09:15:04 .. 09:15:08
    width=92    frames=4534    ts 09:20:07 .. 10:35:40   <== NOT the header axis
    width=91    frames=4938    ts 10:36:28 .. 11:59:55   <== NOT the header axis

INDICES/FINNIFTY/2026-08-31.bin
  header_axis=91  frames=9477  distinct_widths={91: 4539, 89: 4938}          *** MIXED ***
```

**9,472 of 9,477 NIFTY frames (99.95%) are on an axis the header does not describe.** The
header was fixed by the first five frames of the day. This is the exact RC-2 signature.

These frames were written by the *older* build. The container was restarted onto `v0.1.47`
(which carries the remediation) at 12:00:24 IST — and NIFTY's main file stops at 11:59:55
while `2026-08-31.p2.bin` starts at 12:00:47 and is clean single-width. So the deployed
segment fix **does work for a width change**. It is the other case that is unprotected.

### 2.2 The unprotected case: same width, different strikes

`resolve_segment_path` compares axis **length only**:

```python
on_disk = read_header_axis(candidate, kind)   # returns a COUNT, not the axis
if on_disk is None or on_disk == axis_len:
    return candidate
```

`option_chain_filter` produces `strike_keys[max(0, atm-50) : min(len-1, atm+50)+1]`. Away
from the universe edges that window is **always exactly 101 wide** — but it *slides* as
ATM moves. A slid 101-wide window is byte-indistinguishable from the original under a
length-only test, so it is appended to the same file under the stale header.

BANKNIFTY, MIDCPNIFTY, SENSEX and BANKEX were all at width 101 all day and never rolled.
Two independent measurements show their axes did slide:

**(a) Reconstruction from the instrument master.** Replaying the project's own
`get_spot_atm` / `nearest_index` / ±50 logic against the day's listed universe (143
strikes for BANKNIFTY exp 2026-09-29) and the recorded bootstrap spot of each restart
epoch:

```
epoch  first_ts (IST)   spot       ATM     win_start  width  shift  predicted_excess
  0/1  09:20:07         57472.20   57500      19       101     -1        -100
  2    10:36:29         57211.20   57200      16       101     +2        +200
  3/4  11:32:18         57303.40   57300      17       101     +1        +100
  5    12:00:47         57409.15   57400      18       101      0           0
  6    14:30:53         57287.05   57300      17       101     +1        +100
```

The window start index moves **19 → 16 → 17 → 18 → 17**, and the width is **101 every
single time**. The header window starts at index 18.

**(b) Put-call parity on the stored bytes.** Parity gives `K[k] + C[k] − P[k] = F`, one
forward per frame. Using quote **mids** over the 25 near-the-money strikes, the estimator
is tight — median IQR **8.6 rupees**. If frames on the true axis are decoded with the
header axis, the recovered forward is displaced by `K_header[k] − K_true[k]`:

```
frame     spot        F        iqr    basis
 4169   57272.90   57533.4     5.5    260.5     <- morning axis
 4765   57203.70   57787.7     8.4    584.0     <- +323.5 step across a 48s restart gap
 7743   57285.55   57854.9     7.2    569.4
 8338   57341.70   57819.1     7.3    477.4     <- -107 step
 9530   57394.60   57805.4    12.0    410.8
```

The implied forward jumps **+323.5 rupees while spot moved *down* 69 rupees** — 38× the
measurement precision, and in the wrong direction for any market explanation. The two
largest predicted displacements (+300, −100) match the two largest measured steps
(+323.5, −107); the residual is ordinary intraday carry drift.

**Conclusion.** A is not merely inconvenient. It is *unsound*: it protects the detectable
half of the failure and silently permits the other half, on four of six index artifacts,
today.

### 2.3 Secondary defects A leaves in place

- `read_header_axis` returns `None` for **both** "file is empty" and "header unreadable",
  and `_commit_axis` treats `_axis_on_disk is None` as "no constraint". An unreadable
  header therefore *binds the incoming axis* instead of refusing.
- Nothing validates a frame's width against the axis at decode time.
- `reconstruct_chain_metrics(frame, header)` and the Greeks/IV reconstruction take the
  axis from the **file header**, so a mis-paired frame silently corrupts IV, Greeks, PCR
  and max-pain rather than failing.

---

## 3. Option comparison

| Criterion | **A** segment rolling (today) | **B** fixed daily full axis | **C** inline `AXIS_DEF` records |
|---|---|---|---|
| One file per artifact/day | ✗ (`.p2`, `.p3`, …) | ✓ | ✓ |
| Append-only preserved | ✓ | ✓ | ✓ |
| Torn-frame safety | ✓ | ✓ | ✓ (same length-prefix rule) |
| Arbitrary axis changes/session | ✓ (new file each) | ✗ (zero by construction) | ✓ |
| Full axis identity compared | ✗ **length only** | n/a (axis never re-derived) | ✓ (full bytes + digest) |
| Same-width shift distinguished | ✗ **silently merged** | n/a | ✓ |
| Random access resolves axis | partly (per file) | ✓ (one axis) | ✓ (`ts → offset, len, axis_id`) |
| IV/Greeks/PCR get right axis | ✗ (header-derived) | ✓ | ✓ (axis travels with the frame) |
| Resume recovers axes from file | length only | ✓ (deterministic re-derivation) | ✓ (scan `AXIS_DEF` records) |
| One archive per artifact/day | ✗ | ✓ | ✓ |
| Completeness independent of axis | needs `segment_paths` glob | ✓ | ✓ |
| V1 archives readable unchanged | ✓ | ✓ | ✓ (`schema_version == 1` path) |
| Binary format change | none | **none** | new tags + `schema_version = 2` |
| Storage cost | baseline | **+876 MB/day raw, ≈0 compressed** | +4 bytes/frame (+92 KB/day/artifact) |
| Foreign-axis decode impossible | ✗ | not enforced | ✓ enforced |

---

## 4. Option B quantified — measured, not estimated

### 4.1 Frame arithmetic (verified against the wire)

```
IndexFrame payload = 4 (tag) + 8 (ts) + 8 (seq) + 8 (spot) + 8 (vix)
                     + 2 sides × 15 columns × (8 count + 8n)
                   = 36 + 240 + 240n = 276 + 240n
on-disk record     = 4 (u32 length prefix) + payload = 280 + 240n
```

At n=101 that predicts 24,520 B/frame; the live files measure 24,520–24,521. Scheduled
grid is 23,100 s/artifact/day.

### 4.2 Real universes (instrument master `_instruments/2026-08-31`)

Every index's nearest expiry is fully CE/PE paired, so the union and intersection of the
listed strikes are the same set.

| Index | Expiry | Live axis | Full universe | Raw/day live | Raw/day full | × |
|---|---|---:|---:|---:|---:|---:|
| NIFTY | 2026-09-01 | 94 | **105** | 527.6 MB | 588.6 MB | 1.12 |
| BANKNIFTY | 2026-09-29 | 101 | **143** | 566.4 MB | 799.3 MB | 1.41 |
| FINNIFTY | 2026-09-29 | 91 | **91** | 511.0 MB | 511.0 MB | 1.00 |
| MIDCPNIFTY | 2026-09-29 | 101 | **133** | 566.4 MB | 743.8 MB | 1.31 |
| SENSEX | 2026-09-03 | 101 | **130** | 566.4 MB | 727.2 MB | 1.28 |
| BANKEX | 2026-09-24 | 101 | **145** | 566.4 MB | 810.3 MB | 1.43 |
| **Total** | | | | **3,304.2 MB** | **4,180.2 MB** | **1.27** |

Raw index footprint grows **+876 MB/day (+26.5%)**. Whole-day raw goes from ~9.0 GB to
~9.9 GB (+10%); STOCKS and INDICES_FnO are token-axis artifacts and are unaffected.
The VPS has 387 GB free (11% used), so the transient cost is immaterial.

### 4.3 Compressed cost — the number that decides it

Estimating this is exactly where intuition fails, so it was measured: real consecutive
frames from today's live capture, re-emitted widened onto the full universe with real
values at their true strike positions and zeros in the added wings, both streams
compressed with the project's own settings (**zstd level 17**).

| Index | Axis | Raw B/frame | Raw Δ | zstd-17 B/frame | **zstd Δ** | Extrapolated zstd/day |
|---|---|---:|---:|---:|---:|---:|
| BANKNIFTY | 101 → 143 | 24,520 → 34,601 | **+41.1%** | 725.0 → 735.2 | **+1.4%** | 16.7 → 17.0 MB |
| SENSEX | 101 → 130 | 24,521 → 31,481 | **+28.4%** | 1,324.5 → 1,283.9 | **−3.1%** | 30.6 → **29.7 MB** |

Widening SENSEX to its full universe made the archive **smaller**. The added columns are
almost entirely zero; zstd removes them essentially for free and the more regular stream
models slightly better. Compression ratio improves from 33.8× to 47.1× (BANKNIFTY) and
18.5× to 24.5× (SENSEX).

**B's long-term storage cost is zero within measurement noise, and not even reliably
positive in sign.**

### 4.4 Feasibility checks

- **Subscription budget.** Full-universe option tokens = 2 × (105+143+91+133+130+145) =
  **1,494**, versus 2 × 589 = 1,178 today (+316). Total subscription ≈ 2,370 against a
  3,000-per-connection limit with 3 connections available. Fits comfortably.
  Alternatively the physical axis can be the full universe while the *subscription* stays
  ATM±50 — unsubscribed columns simply stay zero and the axis still never changes.
- **Determinism.** The axis must be derived from the archived per-day instrument master
  (`_instruments/<date>/<EXCH>.csv`), which is written once each morning and reused, and
  from `find_nearest_expiry(..., today)`. It must **not** depend on live spot. That is
  what makes every restart re-derive an identical axis.
- **Residual risk.** A strike listed intraday is absent from the morning master, so it is
  absent from the axis. That is a coverage gap, not a corruption, and is strictly better
  than today. But it is precisely why B still needs C underneath it.

---

## 5. Option C — exact binary specification (schema v2)

### 5.1 Constants

```python
SCHEMA_VERSION = 2

TAG_HEADER    = 0   # unchanged
TAG_DATA      = 1   # V1 data frame, no axis_id. Read forever, never written by v2.
TAG_AXIS_DEF  = 2   # new
TAG_DATA_AXIS = 3   # new: data frame carrying an explicit axis_id

AXIS_KIND_INDEX_STRIKES = 0
AXIS_KIND_STOCK_ROWS    = 1
AXIS_KIND_INDEX_FNO_ROWS = 2

BODY_ENC_STRIKES_I64 = 0   # Vec<i64> paise
BODY_ENC_STOCK_REFS  = 1   # StockRef list, exactly as the V1 StockHeader encodes it
BODY_ENC_FNO_REFS    = 2   # IndexFnoRef list, likewise
```

A new **tag** is used rather than a new field position inside tag 1. This is deliberate:
it keeps `timestamp_unix_ms` at `payload_offset + 4` for *both* data tags, so
`scan.py`'s `_TAG_TS = struct.Struct("<IQ")` and `reader._scan`'s timestamp index keep
working with a one-line change (accept tag 3 as data), and no V1 offset is disturbed.
Version dispatch stays explicit — never a heuristic.

### 5.2 Record: `AXIS_DEF` (tag 2)

```
offset  type            field
  0     u32             tag = 2
  4     u32             axis_id                 # >= 1, strictly increasing in file order
  8     u64             defined_at_unix_ms
 16     u32             axis_kind               # AXIS_KIND_*
 20     u32             body_encoding           # BODY_ENC_*
 24     u64             identity_digest         # blake2b(axis_body, digest_size=8) LE
 32     u64             axis_body_len
 40     bytes[len]      axis_body
```

The 40-byte fixed part is naturally aligned. `axis_body` is **byte-identical to the axis
portion of the corresponding V1 header** — for an index chain, exactly the bytes
`layout.put_vec(out, strikes, DT_I64)` emits. This means `AXIS_DEF` reuses the existing
encoders verbatim, and `axis_body_len` lets a reader skip an encoding it does not know.

**Identity.** Two axes are the same iff their `axis_body` bytes are equal. The digest is a
fast pre-filter and is *never* the sole comparison. Length is never compared on its own.
A same-width shifted window differs in bytes, so it necessarily receives a new `axis_id`.

### 5.3 Record: `DATA_AXIS` (tag 3), index flavour

```
offset  type            field
  0     u32             tag = 3
  4     u64             timestamp_unix_ms       # SAME offset as a V1 data frame
 12     u32             axis_id
 16     u64             sequence
 24     i64             spot_price
 32     i64             vix
 40     RawBlock        calls                   # 15 × Vec, width = len(axis[axis_id])
  …     RawBlock        puts
```

Payload = `40 + 240 + 240n = 280 + 240n`, i.e. **4 bytes larger** than V1 per frame
(+92 KB/day/artifact raw at n=101). The stock and index-F&O flavours are identical: `u32
axis_id` is inserted immediately after the timestamp, before `sequence`.

### 5.4 Invariants the reader must enforce

| # | Invariant | Violation |
|---|---|---|
| I1 | Every `axis_id` referenced by a tag-3 frame was defined by an **earlier** `AXIS_DEF` in the same file | raise `UnknownAxis` — never fall back to the header |
| I2 | `AXIS_DEF` is written and fsynced **before** the first frame referencing it | writer contract |
| I3 | `axis_id = 1` is byte-identical to the header's axis vector | raise `AxisHeaderDisagreement` |
| I4 | In a `schema_version == 2` file, a tag-1 record is illegal | raise `ForeignAxisFrame` |
| I5 | In a `schema_version == 1` file, tags 2 and 3 are illegal | raise `UnexpectedRecord` |
| I6 | Axis equality is full-`axis_body` equality | digest alone is never sufficient |
| I7 | A frame's decoded Vec width equals `len(axis[axis_id])` | raise `AxisWidthMismatch` |

I7 is the last line of defence: it converts today's silent mis-mapping into a hard error.

### 5.5 Reader and index changes

`app/bin_codec/reader.py`:

- `FrameRecord` gains `axis_id: int` (**0** for V1 frames, meaning "the header axis").
- `_BaseReader._scan()`:
  - capture `schema_version` from the header record (record 0);
  - tag 2 → decode into `self._axes: dict[int, AxisDef]`;
  - tag 1 → `axis_id = 0`; tag 3 → read `axis_id` at `payload_offset + 12`;
  - the torn-trailing-record rule (`payload_end > total → break`) is unchanged.
- For V1 files, synthesise `self._axes[0]` from the header so axis lookup is uniform
  across versions and no call site needs a version branch.
- The timestamp index becomes **`ts → (offset, len, axis_id)`**, which is what makes
  "timestamp random access resolves the correct axis automatically" true by construction.
- New API, returning frame and axis together so they cannot be separated by accident:

```python
@dataclass(frozen=True)
class FrameWithAxis:
    frame: IndexFrame
    axis_id: int
    strikes: np.ndarray      # the axis THIS frame was written against

def axis_for(self, frame_index: int) -> np.ndarray: ...
def frame_with_axis(self, i: int) -> FrameWithAxis: ...
def frame_at(self, ts: int) -> FrameWithAxis: ...
def axis_transitions(self) -> list[tuple[int, int]]:   # (frame_index, axis_id)
```

A `frames_in_range` call spanning a transition yields frames with two different axes —
correctly, and visibly.

`app/bin_codec/scan.py`:

- `scan_frames`: count tag 3 as a data frame alongside tag 1 (timestamp offset unchanged).
- Replace `read_header_axis(path, kind) -> int | None` with an axis-**identity** reader,
  e.g. `read_axis_table(path, kind) -> AxisTable`, returning the full axis bytes for every
  defined axis. Keep a thin length-only shim only for reading historical V1 segments.
- Distinguish "file empty" from "header unreadable": the latter must raise, not return
  `None`. (Today both return `None`, and `_commit_axis` reads that as "no constraint".)

### 5.6 Writer changes

`app/bin_codec/writer.py`:

- `_BaseWriter` gains `_axes: dict[int, bytes]`, `_axis_id_by_body: dict[bytes, int]`,
  `_current_axis_id`, `_next_axis_id`.
- `open()` on an existing v2 file **recovers the axis table** by scanning its `AXIS_DEF`
  records (few, small, cheap) and sets `_next_axis_id = max(id) + 1`. This is the
  resume/restart requirement.
- New method:

```python
def define_axis(self, axis_body: bytes) -> int:
    """Make ``axis_body`` current; append an AXIS_DEF only if it is genuinely new."""
```

  Full byte comparison against every known axis. An identical axis **reuses its existing
  id** and appends nothing — which is exactly what makes the A → B → A case collapse back
  onto `axis_id = 1` instead of inventing a third axis.
- `append_frame` emits tag 3 with `_current_axis_id`, after asserting the frame's width
  equals the current axis length.
- `bind_header` / `flush_header` lazy-header semantics are preserved; the `AXIS_DEF` for
  `axis_id = 1` is emitted in the same flush as the header.
- `HeaderAxisMismatch`, `resolve_segment_path` and `MAX_SEGMENTS` are **retained** for
  reading and writing historical V1 files, and as a fail-closed backstop. They are only
  removed once §7's proofs pass.

### 5.7 Crash and torn-record behaviour

- **Torn mid-`AXIS_DEF`.** Dropped by the existing length-prefix rule. Because `AXIS_DEF`
  is fsynced before any frame that references it (I2), no frame can point at the lost
  record. Invariants hold.
- **Crash after `AXIS_DEF`, before any frame.** Leaves an orphan axis definition. Legal
  and harmless: a defined-but-unreferenced axis is permitted.
- **Torn mid-frame.** Unchanged from today — the partial trailing record is ignored.

### 5.8 Downstream changes required

| Site | Change |
|---|---|
| `app/reconstruct/metrics.py` | `reconstruct_chain_metrics` must take the **frame's** axis, not `header.strikes` |
| `app/reconstruct/greeks.py` | strike vector must come from the frame's axis |
| `app/capture/broadcaster.py` | publish the current axis; emit a fresh `option_grid` keyframe when `axis_id` changes, so the UI relabels columns instead of mis-labelling them |
| `app/ops/reconcile_session.py` | `segment_paths` returns just the base file for v2; keep the `.p*` glob for historical days |
| `app/ops/retention.py` | uses timestamps only — unaffected |
| EOD compression | unchanged: one `.bin` → one `.bin.zst` per artifact/day |

### 5.9 V1 compatibility and deployment ordering

`schema_version == 1` files are never rewritten and read exactly as today: a single
implicit axis taken from the header, bound to every tag-1 frame, surfaced as `axis_id 0`.

**Ordering constraint.** A V1 reader walking a V2 file would treat tags 2 and 3 as data
frames (its `_scan` classifies anything that is not `TAG_HEADER` as data) and misparse
them. Therefore: **deploy the V2-capable reader everywhere before enabling the V2 writer.**

---

## 6. What C costs

- +4 bytes per frame (+92 KB/day/artifact raw, ~0 compressed).
- One `AXIS_DEF` record per distinct axis per day: 40 bytes + 8 + 8n. For a 143-strike
  index that is ~1.2 KB. Under policy B, one per file per day.
- New code in `layout` / `writer` / `reader` / `scan`, plus the four downstream call sites
  in §5.8.

---

## 7. Proofs required before any segment safety is removed

`resolve_segment_path`, `HeaderAxisMismatch`, `MAX_SEGMENTS` and
`tests/test_header_axis_integrity.py` stay exactly as they are until all of the following
pass:

1. **Axis A → different same-length axis B.** Write frames on a 101-strike window, restart
   onto a *shifted* 101-strike window, assert a second `AXIS_DEF` with a different
   `axis_id` and digest, one physical file, and each frame reading back on its own axis.
   (This is the case A cannot detect at all — see §2.2.)
2. **A → B → A.** Third epoch must reuse `axis_id = 1`; assert no third `AXIS_DEF` is
   appended and that frames from epochs 1 and 3 resolve to byte-identical axes.
3. **Crash/restart between axis definitions.** Truncate mid-`AXIS_DEF` and mid-frame;
   assert recovery drops only the partial record, that no frame references an undefined
   axis, and that the reopened writer recovers the axis table and continues in the same
   file.
4. **Random access across a transition.** `frame_at(ts)` on both sides of the boundary
   returns the correct axis; a `frames_in_range` spanning it yields both axes; a nearest-
   timestamp lookup never pairs a frame with a neighbour's axis.
5. **V1 compatibility.** A byte-frozen v1 fixture (`.bin` and `.bin.zst`) still decodes
   identically, with `axis_id 0` mapping to the header axis; golden metrics
   (IV/Greeks/PCR/max-pain) unchanged.
6. **No foreign-axis frame can ever be decoded.** Hand-craft (a) a tag-3 frame naming an
   undefined `axis_id`, (b) a tag-1 frame inside a v2 file, (c) a frame whose Vec width
   disagrees with its axis, (d) a v2 file whose `axis_id 1` disagrees with the header.
   Each must raise, and none may fall back to the header.

Additionally, as regression cover for the defects in §2.3: an unreadable header must raise
rather than bind the incoming axis, and axis comparison must be shown to reject a
same-width shifted axis.

---

## 8. Reproducing the measurements

All probes were read-only. Frame widths, axis identity and storage were measured with
short scripts run inside the deployed container:

```bash
ssh beonedge
# per-frame axis width census vs header width
docker exec -i market-data-dwndr-backend python3 - < width_census.py
# put-call parity forward on quote mids, plus restart-gap detection
docker exec -i market-data-dwndr-backend python3 - INDICES/BANKNIFTY/2026-08-31.bin 34 < axis_shift_probe.py
# window reconstruction from the instrument master
docker exec -i -e INDEX=BANKNIFTY market-data-dwndr-backend python3 - < shift_reconstruct.py
# real universes and raw cost
docker exec -i market-data-dwndr-backend python3 - < universe_measure.py
# widen real frames onto the full universe, zstd-17 both
docker exec market-data-dwndr-backend python3 /tmp/wb.py BANKNIFTY NFO 1800 0.55
```

Values quoted here are from the 2026-08-31 live session and the
`_instruments/2026-08-31` masters.

---

## 9. Open decisions for sign-off

1. **Axis scope for B** — full listed universe for the nearest expiry (measured above), or
   a wider superset (e.g. union across the two nearest expiries) to absorb intraday
   listings? The superset costs almost nothing compressed but widens the subscription.
2. **Subscribe to the full universe, or keep ATM±50 subscription with a full-universe
   storage axis?** The latter keeps tick volume flat and still eliminates axis changes.
3. **Retrofit for the already-corrupted days.** 2026-08-31 joins the list (NIFTY 99.95%
   mis-mapped, FINNIFTY 52%, and four artifacts with undetected same-width slides). A
   sidecar axis map can be reconstructed for width-change days from the run boundaries;
   for same-width slides the true axis is **not** recoverable from the archive alone,
   which is the strongest argument for C.
4. **Whether to write `AXIS_DEF` for every generation** even when the axis is unchanged
   (an audit trail of bootstraps), or only on genuine change as specified above.
