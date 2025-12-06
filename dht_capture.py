"""
Packet capture loop using tshark.

Responsibilities:
- Run tshark for a fixed duration
- Parse UDP packets on the DHT port
- Classify packets as inbound vs outbound based on local IPs
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
from dht_system import get_system_metrics, get_local_ipv4_addresses

# Precompute local IPs used for direction classification
LOCAL_IPS = get_local_ipv4_addresses(LISTEN_INTERFACE)
logging.info("Local IPv4 addresses used for IN/OUT detection: %s", ", ".join(LOCAL_IPS) or "none")


def run_capture() -> List[Tuple[str, str, int]]:
    """
    Run tshark for CAPTURE_SECONDS and return a list of (src_ip, dst_ip, length)
    tuples for each captured UDP packet on the DHT port.
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
        "ip.dst",
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

    packets: List[Tuple[str, str, int]] = []

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
        if len(parts) < 3:
            logging.debug("Skipping line (not enough parts): %r", line)
            continue

        src_ip = parts[0].strip()
        dst_ip = parts[1].strip()
        length_str = parts[2].strip()

        try:
            length = int(length_str)
        except ValueError:
            logging.debug("Skipping line (length not int): %r", line)
            continue

        packets.append((src_ip, dst_ip, length))

    logging.info("Parsed %d packets from tshark output", len(packets))
    return packets


def capture_loop() -> None:
    """
    Background loop that continuously:

    - Captures DHT packets for CAPTURE_SECONDS
    - Classifies inbound vs outbound based on local IPs
    - Aggregates per-window stats
    - Attaches system metrics
    - Stores to in-memory history + SQLite
    """
    while True:
        window_start = datetime.now(timezone.utc)
        logging.info("Starting capture window at %s", window_start.isoformat())

        packets = run_capture()

        total_packets = len(packets)
        total_bytes = sum(length for _, _, length in packets)

        # Directional counters
        in_bytes = 0
        out_bytes = 0
        in_packets = 0
        out_packets = 0

        # Aggregate per source IP (same as before)
        peer_stats: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"bytes": 0, "packets": 0}
        )

        for src_ip, dst_ip, length in packets:
            # Direction classification based on local IPs
            if src_ip in LOCAL_IPS:
                # Outgoing: from this host to others
                out_bytes += length
                out_packets += 1
            elif dst_ip in LOCAL_IPS:
                # Incoming: from others to this host
                in_bytes += length
                in_packets += 1
            else:
                # Neither endpoint is a known local IP (e.g. capture on "any"
                # with bridged traffic). We still count it in totals and
                # peer_stats below but not in in/out direction.
                pass

            # Per-source aggregation (same as old behavior)
            peer_stats[src_ip]["bytes"] += length
            peer_stats[src_ip]["packets"] += 1

        unique_peers = len(peer_stats)

        # Sort "top talkers" by bytes desc (still per source IP)
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
            "in_bytes": in_bytes,
            "out_bytes": out_bytes,
            "in_packets": in_packets,
            "out_packets": out_packets,
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
            "in last %d seconds on UDP port %d "
            "(IN: %d bytes / %d pkts, OUT: %d bytes / %d pkts)",
            unique_peers,
            total_packets,
            total_bytes,
            CAPTURE_SECONDS,
            DHT_PORT,
            in_bytes,
            in_packets,
            out_bytes,
            out_packets,
        )

        # Sleep so that windows align roughly to INTERVAL_SECONDS
        elapsed = (datetime.now(timezone.utc) - window_start).total_seconds()
        sleep_for = max(0, INTERVAL_SECONDS - elapsed)
        time.sleep(sleep_for)


def start_capture_thread() -> threading.Thread:
    """
    Start the capture loop in a daemon thread and return the Thread object.

    Intended to be called once from the FastAPI app startup.
    """
    t = threading.Thread(target=capture_loop, daemon=True)
    t.start()
    return t
