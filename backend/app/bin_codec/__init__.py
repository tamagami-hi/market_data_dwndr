"""Integer-native BIN codec.

Reads and writes the ``.bin`` format defined in
docs/20-data-and-storage/bin-structure-spec.md -- a standalone, little-endian,
fixed-width format we own: ``[u32 LE payload_len][payload]`` framing, one header
frame then 1 Hz data frames, whole-file zstd L17 at end of day.

Modules:
    layout    -- primitives, enum tags, dtypes, column order, frame data models
    writer    -- append-only frame writers (header-once)
    reader    -- scan -> timestamp index, nearest-ts search, random access
    compress  -- whole-file zstd L17 + transparent .zst read

Submodules are imported explicitly (``from app.bin_codec import layout``) rather
than eagerly here, so each layer can be used independently.
"""

__all__ = ["layout", "writer", "reader", "compress"]
