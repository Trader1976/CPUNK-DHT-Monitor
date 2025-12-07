"""
Packet capture loop using tshark.

Responsibilities:
  - Run tshark for a fixed duration
  - Parse UDP packets on the DHT port
  - Classify packets as inbound vs outbound based on local IPs
  - Aggregate traffic per source IP
  - Compute peer churn (new / expired peers vs previous window)
  - Build a "window" dict with DHT + system metrics
  - Push into in-memory metrics + SQLite
"""

import logging
import subprocess
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
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

import hashlib

# ---------------------------------------------------------------------------
# Local IPs & churn state
# ---------------------------------------------------------------------------

# Precompute local IPs used for direction classification
LOCAL_IPS = get_local_ipv4_addresses(LISTEN_INTERFACE)
logging.info(
    "Local IPv4 addresses used for IN/OUT detection: %s",
    ", ".join(LOCAL_IPS) or "none",
)

# Keep track of peers between windows for churn calculation
PREVIOUS_PEERS = set()

# ---------------------------------------------------------------------------
# Heuristic NodeTracker for "likely DHT nodes"
# ---------------------------------------------------------------------------


@dataclass
class PeerStats:
    ip: str
    first_seen: float
    last_seen: float
    windows_seen: int = 0
    in_bytes_total: int = 0
    out_bytes_total: int = 0
    in_packets_total: int = 0
    out_packets_total: int = 0


class NodeTracker:
    """
    Tracks per-remote-IP behavior across windows and computes a heuristic
    "node score" in [0, 1]. This is purely packet-based – no help from
    dna-nodus – and is meant for hints, not strict truth.

    IMPORTANT: we never expose raw IPs in the public API/UI. We only export
    hashed IDs (node-xxxxxxxx + full SHA-256) via dht_metrics / /metrics.json.
    """

    def __init__(
        self,
        min_windows: int = 20,
        min_lifetime_sec: int = 10 * 60,
        min_bytes: int = 200_000,
        min_packets: int = 500,
    ) -> None:
        self._peers: Dict[str, PeerStats] = {}
        self.min_windows = min_windows
        self.min_lifetime_sec = min_lifetime_sec
        self.min_bytes = min_bytes
        self.min_packets = min_packets

    def update_for_window(
        self,
        now: float,
        window_in_bytes_by_ip: Dict[str, int],
        window_out_bytes_by_ip: Dict[str, int],
        window_in_packets_by_ip: Dict[str, int],
        window_out_packets_by_ip: Dict[str, int],
    ) -> None:
        """
        Called once per capture window.

        The window_*_by_ip maps are derived from tshark output for this window,
        keyed by *remote* IP (not local).
        """
        all_ips = (
            set(window_in_bytes_by_ip)
            | set(window_out_bytes_by_ip)
            | set(window_in_packets_by_ip)
            | set(window_out_packets_by_ip)
        )

        for ip in all_ips:
            stats = self._peers.get(ip)
            if stats is None:
                stats = PeerStats(ip=ip, first_seen=now, last_seen=now)
                self._peers[ip] = stats
            else:
                stats.last_seen = now

            stats.windows_seen += 1
            stats.in_bytes_total += window_in_bytes_by_ip.get(ip, 0)
            stats.out_bytes_total += window_out_bytes_by_ip.get(ip, 0)
            stats.in_packets_total += window_in_packets_by_ip.get(ip, 0)
            stats.out_packets_total += window_out_packets_by_ip.get(ip, 0)

    def _score_peer(self, peer: PeerStats, now: float) -> float:
        """Compute a 0..1 heuristic score: higher = more likely DHT node."""
        lifetime = peer.last_seen - peer.first_seen
        if lifetime < 0:
            lifetime = 0

        total_bytes = peer.in_bytes_total + peer.out_bytes_total
        total_packets = peer.in_packets_total + peer.out_packets_total

        if peer.windows_seen < 2:
            return 0.0  # too ephemeral

        # Normalize components to [0, 1]
        win_score = min(peer.windows_seen / float(self.min_windows), 1.0)
        life_score = min(lifetime / float(self.min_lifetime_sec), 1.0)
        bytes_score = min(total_bytes / float(self.min_bytes), 1.0)
        pkts_score = min(total_packets / float(self.min_packets), 1.0)

        # Bidirectional factor: 0 if only one direction
        has_in = peer.in_bytes_total > 0 and peer.in_packets_total > 0
        has_out = peer.out_bytes_total > 0 and peer.out_packets_total > 0
        bidi = 1.0 if (has_in and has_out) else 0.0

        # Weighted combination
        score = (
            0.30 * win_score
            + 0.25 * life_score
            + 0.25 * bytes_score
            + 0.20 * pkts_score
        )

        # Require some minimum traffic to be considered at all
        if total_bytes < self.min_bytes or total_packets < self.min_packets:
            score *= 0.3

        # Penalize one-direction-only peers heavily
        score *= 0.2 + 0.8 * bidi

        # Clamp
        if score < 0:
            score = 0.0
        if score > 1:
            score = 1.0
        return score

    @staticmethod
    def _hash_ip(ip: str) -> Tuple[str, str]:
        """
        Return a stable, non-reversible ID for the IP.

        - Short ID: "node-1a2b3c4d"
        - Full hash: 64-char SHA-256 hex
        """
        h = hashlib.sha256(ip.encode("utf-8")).hexdigest()
        return "node-" + h[:8], h

    def get_candidates(
        self,
        now: float,
        min_score: float = 0.2,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Return a list of candidate DHT nodes:

        [
          {
            "id": "node-1a2b3c4d",
            "ip_hash": "fullsha256...",
            "score": 0.87,
            "lifetime_sec": 1234,
            "windows_seen": 89,
            "in_bytes": 123,
            "out_bytes": 456,
            "in_packets": 789,
            "out_packets": 42
          },
          ...
        ]
        """
        candidates: List[Tuple[float, PeerStats]] = []
        for peer in self._peers.values():
            score = self._score_peer(peer, now)
            if score >= min_score:
                candidates.append((score, peer))

        # Sort by score descending and cut off
        candidates.sort(key=lambda x: x[0], reverse=True)
        candidates = candidates[:limit]

        result: List[Dict[str, Any]] = []
        for score, peer in candidates:
            node_id, ip_hash = self._hash_ip(peer.ip)
            result.append(
                {
                    "id": node_id,
                    "ip_hash": ip_hash,
                    "score": round(score, 3),
                    "lifetime_sec": round(peer.last_seen - peer.first_seen),
                    "windows_seen": peer.windows_seen,
                    "in_bytes": peer.in_bytes_total,
                    "out_bytes": peer.out_bytes_total,
                    "in_packets": peer.in_packets_total,
                    "out_packets": peer.out_packets_total,
                }
            )
        return result


NODE_TRACKER = NodeTracker()

# ---------------------------------------------------------------------------
# tshark capture
# ---------------------------------------------------------------------------


def run_capture() -> List[Tuple[str, str, int]]:
    """
    Run tshark for CAPTURE_SECONDS and return a list of
    (src_ip, dst_ip, length) tuples for each captured UDP packet on the DHT
    port.
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
      - Computes peer churn (new / expired peers vs previous window)
      - Attaches system metrics
      - Stores to in-memory history + SQLite
    """
    global PREVIOUS_PEERS

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

        # Aggregate per source IP (old behavior for top_peers)
        peer_stats: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"bytes": 0, "packets": 0}
        )

        # Directional per-remote-IP maps for NodeTracker
        window_in_bytes_by_ip: Dict[str, int] = defaultdict(int)
        window_out_bytes_by_ip: Dict[str, int] = defaultdict(int)
        window_in_packets_by_ip: Dict[str, int] = defaultdict(int)
        window_out_packets_by_ip: Dict[str, int] = defaultdict(int)

        for src_ip, dst_ip, length in packets:
            remote_ip = None

            # Direction classification based on local IPs
            if src_ip in LOCAL_IPS:
                # Outgoing: from this host to others
                out_bytes += length
                out_packets += 1
                remote_ip = dst_ip
                window_out_bytes_by_ip[remote_ip] += length
                window_out_packets_by_ip[remote_ip] += 1
            elif dst_ip in LOCAL_IPS:
                # Incoming: from others to this host
                in_bytes += length
                in_packets += 1
                remote_ip = src_ip
                window_in_bytes_by_ip[remote_ip] += length
                window_in_packets_by_ip[remote_ip] += 1

            # Even if neither endpoint is a known local IP (capture on "any"
            # with bridged traffic), we still keep per-source stats for top_peers.
            peer_stats[src_ip]["bytes"] += length
            peer_stats[src_ip]["packets"] += 1

        # Peer count (based on per-source aggregation, same as before)
        current_peers = set(peer_stats.keys())
        unique_peers = len(current_peers)

        # Peer churn vs previous window
        new_peers = len(current_peers - PREVIOUS_PEERS)
        expired_peers = len(PREVIOUS_PEERS - current_peers)

        # Update for next window
        PREVIOUS_PEERS = current_peers

        # Sort "top talkers" by bytes desc (per source IP)
        top_peers = sorted(
            peer_stats.items(),
            key=lambda kv: kv[1]["bytes"],
            reverse=True,
        )[:TOP_TALKERS]

        ts = window_start.isoformat()

        # Update NodeTracker with this window's directional per-remote-IP stats
        now = time.time()
        NODE_TRACKER.update_for_window(
            now,
            window_in_bytes_by_ip,
            window_out_bytes_by_ip,
            window_in_packets_by_ip,
            window_out_packets_by_ip,
        )
        node_candidates = NODE_TRACKER.get_candidates(now)

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
            "new_peers": new_peers,
            "expired_peers": expired_peers,
            "top_peers": [
                {
                    "ip": ip,
                    "bytes": stats["bytes"],
                    "packets": stats["packets"],
                }
                for ip, stats in top_peers
            ],
            "node_candidates": node_candidates,
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
            "(IN: %d bytes / %d pkts, OUT: %d bytes / %d pkts, "
            "churn: +%d / -%d)",
            unique_peers,
            total_packets,
            total_bytes,
            CAPTURE_SECONDS,
            DHT_PORT,
            in_bytes,
            in_packets,
            out_bytes,
            out_packets,
            new_peers,
            expired_peers,
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
