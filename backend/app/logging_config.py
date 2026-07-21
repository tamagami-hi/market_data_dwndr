"""Central logging configuration.

A single ``configure_logging`` call (from the app entrypoint) sets a consistent format
and level. Kept dependency-free so it works in any runtime.
"""

from __future__ import annotations

import logging

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger. Idempotent-ish for repeated calls."""
    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=numeric, format=LOG_FORMAT)
    logging.getLogger().setLevel(numeric)
