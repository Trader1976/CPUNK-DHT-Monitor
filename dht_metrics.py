"""
In-memory metrics history and health computation.

This module owns:
- metrics_history: rolling window of capture windows
- latest_top_peers: last window's "top talkers"
- get_metrics_snapshot(): used by /metrics.json
- get_health_info(): used by /health (includes dna-nodus info)
"""

from collections import deque
from datetime import datetime, timezone
import threading
from typing import Dict, List, Tuple, Any

from dht_config import MAX_POINTS, INTERVAL_SECONDS
from dht_system import get_dna_nodus_process_info

# Rolling in-memory window for the dashboard
metrics_history: "deque[Dict[str, Any]]" = deque(maxlen=MAX_POINTS)

# Last window's "top talkers"
latest_top_peers: List[Dict[str, Any]] = []

metrics_lock = threading.Lock()


def add_window(window: Dict[str, Any]) -> None:
    """
    Append one capture window to in-memory history and update top peers.
    """
    global latest_top_peers

    with metrics_lock:
        metrics_history.append(window)
        latest_top_peers = list(window.get("top_peers", []))


def get_metrics_snapshot() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Return (history_copy, latest_top_copy) for /metrics.json.

    Copies are returned so the caller cannot mutate internal state.
    """
    with metrics_lock:
        history_copy = list(metrics_history)
        top_copy = list(latest_top_peers)
    return history_copy, top_copy


def get_health_info() -> Dict[str, Any]:
    """
    Compute overall health info used by the /health endpoint.

    Status rules:
      - "cold" : no metrics yet
      - "ok"   : we have data and last window had packets
      - "idle" : we have data but last window had 0 packets

    Also augments the response with dna-nodus process metrics.
    """
    with metrics_lock:
        points = len(metrics_history)
        last = metrics_history[-1] if points > 0 else None

    status = "cold"
    last_ts = None
    last_packets = None
    last_bytes = None
    age_seconds = None

    if last:
        last_ts = last.get("ts")
        last_packets = last.get("total_packets", 0)
        last_bytes = last.get("total_bytes", 0)

        # Compute "age" from last window timestamp
        try:
            last_dt = datetime.fromisoformat(last_ts)
            if last_dt.tzinfo is None:
                # Treat naive as UTC for safety
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            now_utc = datetime.now(timezone.utc)
            age_seconds = (now_utc - last_dt).total_seconds()
        except Exception:
            age_seconds = None

        if last_packets > 0:
            status = "ok"
        else:
            status = "idle"

    health: Dict[str, Any] = {
        "status": status,
        "points": points,
        "last_ts": last_ts,
        "last_packets": last_packets,
        "last_bytes": last_bytes,
        "age_seconds": age_seconds,
        "interval_seconds": INTERVAL_SECONDS,
    }

    # Enrich with dna-nodus process info
    nodus_info = get_dna_nodus_process_info()
    health.update(nodus_info)

    return health
