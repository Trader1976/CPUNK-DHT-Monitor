"""
Packet capture loop using tshark.

Responsibilities:
- Run tshark for a fixed duration
- Parse UDP packets on the DHT port
- Aggregate traffic per source IP
- Build a "window" dict with DHT + system metrics
- Push into in-memory metrics + SQLite
"""

import logging
import subprocess
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Any

from dht_config import (
    DHT_PORT,
    CAPTURE_SECONDS,
    INTERVAL_SECONDS,
    LISTEN_INTERFACE,
    TOP_TALKERS,
)
from dht_db import save_window_to_db
from dht_metrics import add_window
from dht_system import get_system_metrics


def run_capture() -> List[Tuple[str, int]]:
    """
    Run tshark for CAPTURE_SECONDS and return a list of (ip, length) tuples
    for each captured UDP packet on the DHT port.
    """
    cmd = [
        "tshark",
        "-i",
        LISTEN_INTERFACE,
        "-f",
        f"udp port {DHT_PORT}",
        "-a",
        f"duration:{CAPTURE_SECONDS}",
        "-T",
        "fields",
        "-e",
        "ip.src",
        "-e",
        "frame.len",
    ]

    logging.info("Running tshark command: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            check=False,
        )
    except Exception as e:
        logging.error("Error running tshark: %s", e)
        return []

    logging.info("tshark return code: %s", result.returncode)
    if result.stderr.strip():
        logging.debug("tshark stderr: %s", result.stderr.strip())

    stdout_lines = result.stdout.splitlines()
    logging.info("tshark produced %d stdout lines", len(stdout_lines))

    packets: List[Tuple[str, int]] = []

    for raw in stdout_lines:
        line = raw.strip()
        if not line:
            continue

        # Skip tshark runtime messages if they leak into stdout
        if line.startswith("Running as user") or line.startswith("Capturing on"):
            continue
        if "packets captured" in line:
            continue

        parts = line.split()
        if len(parts) < 2:
            logging.debug("Skipping line (not enough parts): %r", line)
            continue

        ip = parts[0].strip()
        length_str = parts[1].strip()

        try:
            length = int(length_str)
        except ValueError:
            logging.debug("Skipping line (length not int): %r", line)
            continue

        packets.append((ip, length))

    logging.info("Parsed %d packets from tshark output", len(packets))
    return packets


def capture_loop() -> None:
    """
    Background loop that continuously:

    - Captures DHT packets for CAPTURE_SECONDS
    - Aggregates per-window stats
    - Attaches system metrics
    - Stores to in-memory history + SQLite
    """
    while True:
        window_start = datetime.now(timezone.utc)
        logging.info("Starting capture window at %s", window_start.isoformat())

        packets = run_capture()

        total_packets = len(packets)
        total_bytes = sum(length for _, length in packets)

        # Aggregate per source IP
        peer_stats: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"bytes": 0, "packets": 0}
        )
        for ip, length in packets:
            peer_stats[ip]["bytes"] += length
            peer_stats[ip]["packets"] += 1

        unique_peers = len(peer_stats)

        # Sort "top talkers" by bytes desc
        top_peers = sorted(
            peer_stats.items(),
            key=lambda kv: kv[1]["bytes"],
            reverse=True,
        )[:TOP_TALKERS]

        ts = window_start.isoformat()

        # Base window payload
        window: Dict[str, Any] = {
            "ts": ts,
            "unique_peers": unique_peers,
            "total_bytes": total_bytes,
            "total_packets": total_packets,
            "top_peers": [
                {
                    "ip": ip,
                    "bytes": stats["bytes"],
                    "packets": stats["packets"],
                }
                for ip, stats in top_peers
            ],
        }

        # Attach host system metrics
        sys_metrics = get_system_metrics()
        window.update(sys_metrics)

        # Push to in-memory history + DB
        add_window(window)
        save_window_to_db(window)

        logging.info(
            "Capture done: %d unique peers, %d packets, %d bytes "
            "in last %d seconds on UDP port %d",
            unique_peers,
            total_packets,
            total_bytes,
            CAPTURE_SECONDS,
            DHT_PORT,
        )

        # Sleep so that windows align roughly to INTERVAL_SECONDS
        elapsed = (datetime.now(timezone.utc) - window_start).total_seconds()
        sleep_for = max(0, INTERVAL_SECONDS - elapsed)
        time.sleep(sleep_for)


def start_capture_thread() -> threading.Thread:
    """
    Start the capture loop in a daemon thread and return the Thread object.

    Intended to be called once from the FastAPI/Flask app startup.
    """
    t = threading.Thread(target=capture_loop, daemon=True)
    t.start()
    return t
