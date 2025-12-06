#!/usr/bin/env python3
"""
FastAPI web backend for the CPUNK DHT Monitor.

Responsibilities:
- Serve the static dashboard UI (static/index.html)
- Expose JSON endpoints for metrics, health, config, and DB stats
- Start the background capture thread on startup
"""

import logging
import socket
import hashlib

import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from pathlib import Path
from typing import Any, Dict, Tuple

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from dht_core import (
    # Config / constants
    HTTP_HOST,
    HTTP_PORT,
    DHT_PORT,
    LISTEN_INTERFACE,
    INTERVAL_SECONDS,
    # Core API
    setup_logging,
    init_db,
    start_capture_thread,
    get_metrics_snapshot,
    get_health_info,
    get_db_stats,
)

# Base paths
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
INDEX_HTML = STATIC_DIR / "index.html"

app = FastAPI(
    title="CPUNK DHT Monitor",
    version="0.2.0",
    description="Monitoring application for CPUNK DNA-Nodus / DHT bootstrap nodes.",
)

# Serve /static/... for assets (JS, CSS, images if you add them later)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


security = HTTPBasic()

security = HTTPBasic()

def get_current_user(credentials: HTTPBasicCredentials = Depends(security)):
    """
    HTTP Basic auth for the whole DHT monitor.

    Credentials source:
      - DHT_MONITOR_USER          -> expected username (plaintext)
      - DHT_MONITOR_PASS          -> expected password (PLAINTEXT, legacy)
      - DHT_MONITOR_PASS_HASH     -> expected password SHA-256 hex (preferred)

    Priority:
      1. If DHT_MONITOR_PASS_HASH is set, we ONLY check against the hash.
      2. Else if DHT_MONITOR_PASS is set, we check plaintext.
      3. If neither is set (or no user), auth is disabled (allow all).
    """
    expected_user = os.environ.get("DHT_MONITOR_USER")
    expected_pass = os.environ.get("DHT_MONITOR_PASS")
    expected_hash = os.environ.get("DHT_MONITOR_PASS_HASH")

    # If nothing configured, do NOT enforce auth (to avoid accidental lock-out)
    if not expected_user or (not expected_pass and not expected_hash):
      return "anonymous"

    # Username check (plaintext, not really sensitive)
    if not secrets.compare_digest(credentials.username, expected_user):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    # Password check – prefer hash if configured
    provided_password = credentials.password or ""

    if expected_hash:
        # SHA3-512 hex comparison (post-quantum-friendly hash)
        candidate_hash = hashlib.sha3_512(
            provided_password.encode("utf-8")
        ).hexdigest()
        ok_pass = secrets.compare_digest(candidate_hash, expected_hash)
    else:
        # Legacy plaintext password comparison
        ok_pass = secrets.compare_digest(provided_password, expected_pass)

    if not ok_pass:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    return credentials.username





# ------------------------------------------------------------
# Startup
# ------------------------------------------------------------

@app.on_event("startup")
def on_startup() -> None:
    """
    Initialize logging, database, and start the capture thread.
    """
    setup_logging()
    logging.info("Starting CPUNK DHT Monitor FastAPI application")

    # Initialize SQLite
    init_db()
    logging.info("SQLite database initialized")

    # Start tshark capture thread
    start_capture_thread()
    logging.info("Background capture thread started")


# ------------------------------------------------------------
# UI: serve index.html
# ------------------------------------------------------------

@app.get("/", include_in_schema=False, dependencies=[Depends(get_current_user)])
async def serve_index():
    """
    Serve the main dashboard UI from static/index.html.
    """
    if not INDEX_HTML.exists():
        logging.error("index.html not found at %s", INDEX_HTML)
        return JSONResponse(
            status_code=500,
            content={"error": "index.html not found", "path": str(INDEX_HTML)},
        )

    return FileResponse(str(INDEX_HTML))


# ------------------------------------------------------------
# API: metrics
# ------------------------------------------------------------

@app.get("/metrics.json", dependencies=[Depends(get_current_user)])
async def metrics() -> Dict[str, Any]:
    """
    Return current metrics history and top talkers.

    Response schema:
    {
      "history": [ { ts, unique_peers, total_bytes, total_packets, ... }, ... ],
      "latest_top": [ { ip, bytes, packets }, ... ]
    }
    """
    history, latest_top = get_metrics_snapshot()
    return {
        "history": history,
        "latest_top": latest_top,
    }


# ------------------------------------------------------------
# API: health (includes dna-nodus process info)
# ------------------------------------------------------------

@app.get("/health", dependencies=[Depends(get_current_user)])
async def health() -> Dict[str, Any]:
    """
    Return overall monitor health plus dna-nodus process info.

    The underlying dht_core.get_health_info() already merges:
      - status / points / last_ts / last_bytes / age_seconds / interval_seconds
      - nodus_running / nodus_cpu_pct / nodus_mem_mb / nodus_uptime_seconds
    """
    info = get_health_info()
    return info


# ------------------------------------------------------------
# API: config.json (used by front-end UI)
# ------------------------------------------------------------

@app.get("/config.json", dependencies=[Depends(get_current_user)])
async def config() -> Dict[str, Any]:
    """
    Return UI config:

    {
      "hostname": "<this host>",
      "port": <DHT UDP port>,
      "iface": "<capture interface>",
      "interval_seconds": <capture interval>,
      "http_host": "<HTTP bind host>",
      "http_port": <HTTP bind port>
    }

    The index.html currently uses hostname, port, iface, interval_seconds.
    Extra fields (http_host, http_port) are just informational.
    """
    try:
      hostname = socket.gethostname()
    except Exception:
      hostname = "unknown"

    return {
        "hostname": hostname,
        "port": DHT_PORT,
        "iface": LISTEN_INTERFACE,
        "interval_seconds": INTERVAL_SECONDS,
        "http_host": HTTP_HOST,
        "http_port": HTTP_PORT,
    }


# ------------------------------------------------------------
# API: DB stats
# ------------------------------------------------------------

@app.get("/db_stats", dependencies=[Depends(get_current_user)])
async def db_stats() -> Dict[str, Any]:
    """
    Simple introspection endpoint for the SQLite store.

    {
      "rows": <int>,
      "oldest_ts": "2025-12-06T00:00:00+00:00" | null,
      "newest_ts": "2025-12-06T11:20:00+00:00" | null
    }
    """
    rows, oldest_ts, newest_ts = get_db_stats()
    return {
        "rows": rows,
        "oldest_ts": oldest_ts,
        "newest_ts": newest_ts,
    }


# ------------------------------------------------------------
# Dev entrypoint (for running directly: python dht_fastapi_app.py)
# ------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "dht_fastapi_app:app",
        host=HTTP_HOST,
        port=HTTP_PORT,
        reload=False,
    )
