#!/usr/bin/env python3
"""
CPUNK DHT Monitor – core facade.

Historically all logic lived in this file. We now split implementation into
smaller modules (dht_config, dht_db, dht_metrics, dht_system, dht_capture)
and keep this as a stable API surface for the FastAPI app.

Anything importing from dht_core should continue to work:
  - setup_logging()
  - init_db()
  - start_capture_thread()
  - capture_loop()  <- backward-compatible wrapper
  - get_metrics_snapshot()
  - get_health_info()
  - get_db_stats()
"""

import logging

from dht_config import (
    DHT_PORT,
    CAPTURE_SECONDS,
    INTERVAL_SECONDS,
    LISTEN_INTERFACE,
    MAX_POINTS,
    HTTP_HOST,
    HTTP_PORT,
    LOG_LEVEL,
    TOP_TALKERS,
    DB_PATH,
    DB_MAX_ROWS,
)
from dht_db import init_db as _init_db, get_db_stats as _get_db_stats
from dht_metrics import (
    get_metrics_snapshot as _get_metrics_snapshot,
    get_health_info as _get_health_info,
)
from dht_capture import (
    start_capture_thread as _start_capture_thread,
    capture_loop as _raw_capture_loop,
)


def setup_logging() -> None:
    """Configure the root logger for the monitor."""
    logging.basicConfig(
        level=LOG_LEVEL,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


def init_db() -> None:
    """Initialize SQLite database (delegates to dht_db.init_db)."""
    _init_db()


def get_db_stats():
    """Return (row_count, oldest_ts, newest_ts) from SQLite."""
    return _get_db_stats()


def get_metrics_snapshot():
    """
    Return (history, latest_top, node_candidates) used by /metrics.json.

    - history: list[window_dict]
    - latest_top: list of top talkers (still computed, but UI ignores IPs)
    - node_candidates: heuristic DHT-node candidates (hashed IDs, no raw IPs)
    """
    return _get_metrics_snapshot()


def get_health_info():
    """Return health summary + DNA-Nodus process info for /health."""
    return _get_health_info()


def start_capture_thread():
    """
    Start the background tshark capture loop in a daemon thread.

    Returns the Thread object, but most callers can ignore it.
    """
    return _start_capture_thread()


def capture_loop():
    """
    Backward-compatible wrapper for the old capture_loop function.

    dht_fastapi_app.py used to import capture_loop from dht_core and start
    its own thread. This wrapper simply delegates to the real implementation
    in dht_capture.capture_loop().
    """
    return _raw_capture_loop()


__all__ = [
    # Config constants
    "DHT_PORT",
    "CAPTURE_SECONDS",
    "INTERVAL_SECONDS",
    "LISTEN_INTERFACE",
    "MAX_POINTS",
    "HTTP_HOST",
    "HTTP_PORT",
    "LOG_LEVEL",
    "TOP_TALKERS",
    "DB_PATH",
    "DB_MAX_ROWS",
    # Core API
    "setup_logging",
    "init_db",
    "start_capture_thread",
    "capture_loop",
    "get_metrics_snapshot",
    "get_health_info",
    "get_db_stats",
]
