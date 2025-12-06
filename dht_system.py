"""
System-level metrics and DNA-Nodus process inspection.

- get_system_metrics(): CPU, RAM, disk usage for the host
- get_dna_nodus_process_info(): status/CPU/RAM/uptime for dna-nodus process
- get_local_ipv4_addresses(): helper for capture direction classification
"""

import logging
import time
from typing import Dict, Optional, Set
import socket

import psutil


def get_system_metrics() -> Dict[str, float]:
    """
    Collect basic system metrics for the host:

    - cpu_usage: total CPU usage %
    - mem_used_mb / mem_total_mb: RAM usage in MB
    - disk_used_pct / disk_used_gb / disk_free_gb: root filesystem stats
    """
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


def get_dna_nodus_process_info(process_name: str = "dna-nodus") -> Dict[str, Optional[float]]:
    """
    Inspect dna-nodus process (or another process name if given) using psutil.

    Returns a dict with:
      - nodus_running: 1 if found, 0 if not
      - nodus_cpu_pct: float or None
      - nodus_mem_mb:  float or None
      - nodus_uptime_seconds: float or None
    """
    info: Dict[str, Optional[float]] = {
        "nodus_running": 0,
        "nodus_cpu_pct": None,
        "nodus_mem_mb": None,
        "nodus_uptime_seconds": None,
    }

    try:
        # Find the first matching process
        for proc in psutil.process_iter(attrs=["name", "create_time"]):
            if not proc.info:
                continue
            if proc.info.get("name") != process_name:
                continue

            # Mark as running
            info["nodus_running"] = 1

            # CPU percent (non-blocking snapshot)
            try:
                info["nodus_cpu_pct"] = proc.cpu_percent(interval=0.0)
            except Exception:
                logging.debug("Failed to read CPU percent for %s", process_name, exc_info=True)

            # Memory usage (RSS in MB)
            try:
                mem_rss = proc.memory_info().rss
                info["nodus_mem_mb"] = mem_rss / (1024 * 1024)
            except Exception:
                logging.debug("Failed to read memory info for %s", process_name, exc_info=True)

            # Uptime (seconds)
            try:
                create_time = proc.info.get("create_time") or proc.create_time()
                info["nodus_uptime_seconds"] = time.time() - create_time
            except Exception:
                logging.debug("Failed to read uptime for %s", process_name, exc_info=True)

            # Use the first matching process only
            break

    except Exception:
        logging.warning("Error while scanning for process %s", process_name, exc_info=True)

    return info


def get_local_ipv4_addresses(interface_filter: Optional[str] = None) -> Set[str]:
    """
    Return a set of local IPv4 addresses for this host.

    If interface_filter is:
      - None       -> all interfaces
      - "any"      -> all interfaces (tshark-style)
      - otherwise  -> only the interface with that exact name
    """
    addrs: Set[str] = set()

    try:
        for if_name, addr_list in psutil.net_if_addrs().items():
            # Respect interface filter (if any)
            if interface_filter and interface_filter != "any":
                if if_name != interface_filter:
                    continue

            for addr in addr_list:
                if addr.family == socket.AF_INET:
                    addrs.add(addr.address)
    except Exception:
        logging.warning("Failed to enumerate local IPv4 addresses", exc_info=True)

    return addrs
