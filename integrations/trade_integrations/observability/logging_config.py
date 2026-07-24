"""Central logging configuration for Trade Python processes."""

from __future__ import annotations

import logging
import os
import sys

_LEVEL_ENV = "TRADE_LOG_LEVEL"


def configure_trade_logging(*, logger_name: str | None = None) -> None:
    """Configure root logging once per process."""
    level_name = os.getenv(_LEVEL_ENV, "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level)
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(level)
    if logger_name:
        logging.getLogger(logger_name).setLevel(level)
