"""CPU/RAM/GPU stats for the control page's resource monitor.

Only ever reports real measured values -- fields that can't be reliably
read on this machine (e.g. CPU temperature on stock Windows, or any GPU
field when there's no NVIDIA GPU / nvidia-smi) come back as None so the UI
shows "N/A" rather than a fabricated number.
"""
from __future__ import annotations

import shutil
import subprocess

import psutil

CREATIONFLAGS = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
_nvidia_smi_path: str | None = None
_nvidia_smi_checked = False


def _find_nvidia_smi() -> str | None:
    global _nvidia_smi_path, _nvidia_smi_checked
    if not _nvidia_smi_checked:
        _nvidia_smi_path = shutil.which("nvidia-smi")
        _nvidia_smi_checked = True
    return _nvidia_smi_path


def _gpu_stats() -> dict:
    empty = {
        "gpu_name": None,
        "gpu_percent": None,
        "gpu_mem_used_mb": None,
        "gpu_mem_total_mb": None,
        "gpu_temp_c": None,
    }
    exe = _find_nvidia_smi()
    if not exe:
        return empty
    try:
        out = subprocess.run(
            [exe, "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,name",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2, creationflags=CREATIONFLAGS,
        )
        line = out.stdout.strip().splitlines()[0]
        util, mem_used, mem_total, temp, name = [p.strip() for p in line.split(",")]
        return {
            "gpu_name": name,
            "gpu_percent": float(util),
            "gpu_mem_used_mb": int(mem_used),
            "gpu_mem_total_mb": int(mem_total),
            "gpu_temp_c": float(temp),
        }
    except Exception:
        return empty


def prime() -> None:
    """First psutil.cpu_percent() call is meaningless (no baseline yet).
    Call this once at startup before the periodic polling loop begins."""
    psutil.cpu_percent(interval=None)


def get_resource_stats() -> dict:
    mem = psutil.virtual_memory()
    stats = {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_temp_c": None,  # not reliably available on stock Windows
        "ram_used_mb": round((mem.total - mem.available) / (1024 * 1024)),
        "ram_total_mb": round(mem.total / (1024 * 1024)),
        "ram_percent": mem.percent,
    }
    stats.update(_gpu_stats())
    return stats
