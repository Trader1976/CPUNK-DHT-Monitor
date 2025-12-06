"""
Central configuration for the CPUNK DHT Monitor.

This keeps all tunables (ports, intervals, DB path, etc.) in one place so
other modules only import from here.
"""

import logging
from pathlib import Path

# =========================
# DHT / capture configuration
# =========================

# UDP port used by dna-nodus for DHT traffic
DHT_PORT = 4000

# How long each tshark capture runs (seconds)
CAPTURE_SECONDS = 60

# How often we start a new capture window (seconds)
INTERVAL_SECONDS = 60

# tshark interface ("any" usually works on Linux)
LISTEN_INTERFACE = "any"

# Max in-memory points (e.g. 1440 @ 1/min ~= 24 hours)
MAX_POINTS = 1440

# Number of "top talkers" (peer IPs) to keep per window
TOP_TALKERS = 10

# =========================
# HTTP / API configuration
# =========================

HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8080

# =========================
# Logging configuration
# =========================

LOG_LEVEL = logging.INFO

# =========================
# SQLite configuration
# =========================

BASE_DIR = Path(__file__).resolve().parent

# DB lives in the same folder as the Python scripts
DB_PATH = BASE_DIR / "dht_metrics.db"

# Cap rows to avoid unbounded growth
DB_MAX_ROWS = 1_000_000
