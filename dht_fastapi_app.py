#!/usr/bin/env python3
import threading
import logging
import socket
import secrets
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
import uvicorn

from dht_core import (
    DHT_PORT,
    INTERVAL_SECONDS,
    LISTEN_INTERFACE,
    HTTP_HOST,
    HTTP_PORT,
    setup_logging,
    init_db,
    capture_loop,
    get_metrics_snapshot,
    get_health_info,
    get_db_stats,
)

# =========================
# Basic Auth config
# =========================

BASIC_AUTH_USERNAME = "dhtadmin"        # change if you like
BASIC_AUTH_PASSWORD = "SuperSecret123"  # change if you like
BASIC_AUTH_REALM = "CPUNK DHT Monitor"

app = FastAPI()
security = HTTPBasic()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Mount /static for any future assets (images, separate JS, etc.)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# =========================
# Auth helper
# =========================

def get_current_user(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """FastAPI dependency for HTTP Basic Auth."""

    correct_username = secrets.compare_digest(credentials.username, BASIC_AUTH_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, BASIC_AUTH_PASSWORD)

    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": f'Basic realm="{BASIC_AUTH_REALM}"'},
        )

    return credentials.username


# =========================
# Routes
# =========================

@app.get("/", response_class=HTMLResponse)
async def index(user: str = Depends(get_current_user)):
    """Serve the static dashboard HTML (protected by Basic Auth)."""
    index_path = STATIC_DIR / "index.html"
    return FileResponse(index_path)


@app.get("/config.json")
async def config(user: str = Depends(get_current_user)):
    """Small config endpoint so the static UI knows hostname/port/interval/iface."""
    return {
        "hostname": socket.gethostname(),
        "port": DHT_PORT,
        "interval_seconds": INTERVAL_SECONDS,
        "iface": LISTEN_INTERFACE,
    }


@app.get("/metrics.json")
async def metrics_json(user: str = Depends(get_current_user)):
    """Main metrics endpoint (same JSON as before)."""
    history, top = get_metrics_snapshot()
    return {"history": history, "latest_top": top}


@app.get("/health")
async def health():
    """
    Health endpoint.

    NOTE: This one is intentionally NOT protected,
    so externals / uptime checkers can hit it.
    """
    info = get_health_info()
    return info


@app.get("/db_stats")
async def db_stats(user: str = Depends(get_current_user)):
    """SQLite stats (rows, oldest/newest record). Protected by Basic Auth."""
    rows, oldest, newest = get_db_stats()
    return {
        "rows": rows,
        "oldest_ts": oldest,
        "newest_ts": newest,
    }


# =========================
# Startup hook
# =========================

@app.on_event("startup")
def on_startup():
    """Runs once when FastAPI process starts."""
    setup_logging()
    init_db()

    t = threading.Thread(target=capture_loop, daemon=True)
    t.start()

    logging.info(
        "Starting CPUNK DHT Monitor (FastAPI static UI) on %s:%d (DHT port %d)",
        HTTP_HOST, HTTP_PORT, DHT_PORT,
    )


# =========================
# Manual run
# =========================

if __name__ == "__main__":
    uvicorn.run(
        "dht_fastapi_app:app",
        host=HTTP_HOST,
        port=HTTP_PORT,
        reload=False,
    )
