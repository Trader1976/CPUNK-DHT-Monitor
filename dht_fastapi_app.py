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

@app.get("/", include_in_schema=False)
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

@app.get("/metrics.json")
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

@app.get("/health")
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

@app.get("/config.json")
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

@app.get("/db_stats")
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
