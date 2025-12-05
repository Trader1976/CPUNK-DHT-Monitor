#!/usr/bin/env python3
import subprocess
import threading
import time
import logging
import sqlite3
import psutil
import shutil
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

# =========================
# Configuration
# =========================

DHT_PORT = 4000                 # UDP port used by dna-nodus
CAPTURE_SECONDS = 10            # how long each tshark capture runs
INTERVAL_SECONDS = 10           # how often we start a new capture window
LISTEN_INTERFACE = "any"        # tshark interface ("any" is usually fine)
MAX_POINTS = 1440               # max in-memory points (e.g. 1440 @ 1/min ~= 24h)

HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8080

LOG_LEVEL = logging.INFO
TOP_TALKERS = 10

# SQLite config
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "dht_metrics.db"     # DB in same folder as scripts
DB_MAX_ROWS = 1_000_000                   # cap rows to avoid unbounded growth

# =========================
# Global state
# =========================

metrics_history = deque(maxlen=MAX_POINTS)  # rolling window for UI
latest_top_peers = []                       # last window's top peers
metrics_lock = threading.Lock()

db_conn = None
db_lock = threading.Lock()


# =========================
# Logging
# =========================

def setup_logging():
    logging.basicConfig(
        level=LOG_LEVEL,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


# =========================
# SQLite setup
# =========================

def init_db():
    """Initialize SQLite DB and create table if needed."""
    global db_conn
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db_conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    db_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dht_metrics (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc          TEXT    NOT NULL,
            unique_peers    INTEGER NOT NULL,
            total_bytes     INTEGER NOT NULL,
            total_packets   INTEGER NOT NULL,
            -- Future fields for system metrics (can be NULL for now)
            cpu_usage       REAL,
            mem_used_mb     REAL,
            mem_total_mb    REAL,
            disk_used_pct   REAL,
            disk_used_gb    REAL,
            disk_free_gb    REAL
        );
        """
    )
    db_conn.commit()
    logging.info("SQLite DB initialized at %s", DB_PATH)


def save_window_to_db(window: dict) -> None:
    """
    Persist one capture window into SQLite.
    For now we only store DHT metrics; CPU/RAM/disk columns stay NULL.
    """
    if db_conn is None:
        return

    with db_lock:
        cur = db_conn.cursor()
        cur.execute(
            """
            INSERT INTO dht_metrics (
                ts_utc,
                unique_peers,
                total_bytes,
                total_packets,
                cpu_usage,
                mem_used_mb,
                mem_total_mb,
                disk_used_pct,
                disk_used_gb,
                disk_free_gb
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                window["ts"],
                window["unique_peers"],
                window["total_bytes"],
                window["total_packets"],
                window.get("cpu_usage"),
                window.get("mem_used_mb"),
                window.get("mem_total_mb"),
                window.get("disk_used_pct"),
                window.get("disk_used_gb"),
                window.get("disk_free_gb"),
            ),
        )

        # Optional retention: keep at most DB_MAX_ROWS rows
        cur.execute("SELECT COUNT(*) FROM dht_metrics")
        (count,) = cur.fetchone()
        if count > DB_MAX_ROWS:
            to_delete = count - DB_MAX_ROWS
            logging.info("Trimming DB, deleting %d oldest rows", to_delete)
            cur.execute(
                """
                DELETE FROM dht_metrics
                WHERE id IN (
                    SELECT id FROM dht_metrics
                    ORDER BY id ASC
                    LIMIT ?
                )
                """,
                (to_delete,),
            )

        db_conn.commit()


def get_db_stats():
    """Return (row_count, oldest_ts, newest_ts) from SQLite."""
    if db_conn is None:
        return 0, None, None

    with db_lock:
        cur = db_conn.cursor()
        cur.execute("SELECT COUNT(*), MIN(ts_utc), MAX(ts_utc) FROM dht_metrics")
        row = cur.fetchone()
    if not row:
        return 0, None, None
    count, oldest, newest = row
    return count or 0, oldest, newest


# =========================
# tshark capture logic
# =========================

def run_capture():
    """
    Run tshark for CAPTURE_SECONDS, return list of (ip, length) for each packet.
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

    packets = []
    for raw in stdout_lines:
        line = raw.strip()
        if not line:
            continue

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


def capture_loop():
    """Background thread that continuously captures windows and updates metrics."""
    global latest_top_peers

    while True:
        window_start = datetime.now(timezone.utc)
        logging.info("Starting capture window at %s", window_start.isoformat())

        packets = run_capture()

        total_packets = len(packets)
        total_bytes = sum(length for _, length in packets)

        peer_stats = defaultdict(lambda: {"bytes": 0, "packets": 0})
        for ip, length in packets:
            peer_stats[ip]["bytes"] += length
            peer_stats[ip]["packets"] += 1

        unique_peers = len(peer_stats)

        top_peers = sorted(
            peer_stats.items(),
            key=lambda kv: kv[1]["bytes"],
            reverse=True,
        )[:TOP_TALKERS]

        ts = window_start.isoformat()

        window = {
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

        # Attach system metrics
        sysm = get_system_metrics()
        window.update(sysm)

        with metrics_lock:
            metrics_history.append(window)
            latest_top_peers = window["top_peers"]

        save_window_to_db(window)

        logging.info(
            "Capture done: %d unique peers, %d packets, %d bytes in last %d seconds on UDP port %d",
            unique_peers,
            total_packets,
            total_bytes,
            CAPTURE_SECONDS,
            DHT_PORT,
        )

        elapsed = (datetime.now(timezone.utc) - window_start).total_seconds()
        sleep_for = max(0, INTERVAL_SECONDS - elapsed)
        time.sleep(sleep_for)


# =========================
# Helpers for Flask layer
# =========================

def get_system_metrics():
    """Collect basic system metrics: CPU, RAM, disk."""
    # CPU %
    cpu_usage = psutil.cpu_percent(interval=None)

    # Memory (MB)
    mem = psutil.virtual_memory()
    mem_used_mb = mem.used / (1024 * 1024)
    mem_total_mb = mem.total / (1024 * 1024)

    # Disk usage for root filesystem
    disk = psutil.disk_usage("/")
    disk_used_gb = disk.used / (1024 ** 3)
    disk_free_gb = disk.free / (1024 ** 3)
    disk_used_pct = disk.percent

    return {
        "cpu_usage": cpu_usage,
        "mem_used_mb": mem_used_mb,
        "mem_total_mb": mem_total_mb,
        "disk_used_pct": disk_used_pct,
        "disk_used_gb": disk_used_gb,
        "disk_free_gb": disk_free_gb,
    }


def get_metrics_snapshot():
    """Return (history_copy, latest_top_copy) for /metrics.json."""
    with metrics_lock:
        history_copy = list(metrics_history)
        top_copy = list(latest_top_peers)
    return history_copy, top_copy


def get_health_info():
    """
    Compute health info used by /health.
    Status rules:
      - "cold" : no metrics yet
      - "ok"   : we have data and last window had packets
      - "idle" : we have data but last window had 0 packets
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

        try:
            last_dt = datetime.fromisoformat(last_ts)
            age_seconds = (datetime.now(timezone.utc) - last_dt).total_seconds()
        except Exception:
            age_seconds = None

        if last_packets > 0:
            status = "ok"
        else:
            status = "idle"

    return {
        "status": status,
        "points": points,
        "last_ts": last_ts,
        "last_packets": last_packets,
        "last_bytes": last_bytes,
        "age_seconds": age_seconds,
        "interval_seconds": INTERVAL_SECONDS,
    }
