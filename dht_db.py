"""
SQLite persistence for DHT metrics.

This module is responsible for:
- Initializing the DB
- Inserting each capture window
- Enforcing a simple retention policy
- Exposing basic DB stats
"""

import logging
import sqlite3
import threading
from typing import Dict, Tuple, Optional

from dht_config import DB_PATH, DB_MAX_ROWS

db_conn: Optional[sqlite3.Connection] = None
db_lock = threading.Lock()


def init_db() -> None:
    """
    Initialize SQLite DB and create table if needed.

    Safe to call multiple times; it will reuse the existing connection.
    """
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

            -- System metrics (may be NULL for older rows)
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


def save_window_to_db(window: Dict) -> None:
    """
    Persist one capture window into SQLite.

    Expects a dict with at least:
      ts, unique_peers, total_bytes, total_packets

    System metric fields are optional.
    """
    if db_conn is None:
        # DB not initialized; nothing to do.
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

        # Simple retention: keep at most DB_MAX_ROWS rows.
        cur.execute("SELECT COUNT(*) FROM dht_metrics")
        (count,) = cur.fetchone()
        if count and count > DB_MAX_ROWS:
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


def get_db_stats() -> Tuple[int, Optional[str], Optional[str]]:
    """
    Return (row_count, oldest_ts, newest_ts) from SQLite.

    If the table is empty, timestamps will be None.
    """
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
