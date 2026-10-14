from __future__ import annotations

import html
import time
from dataclasses import dataclass
from typing import List

import psutil


@dataclass
class ProcInfo:
    pid: int
    name: str
    user: str
    cpu_percent: float
    mem_percent: float
    rss_mb: float


def top_processes(sort: str = "cpu", limit: int = 10, window: float = 1.0) -> List[ProcInfo]:
    """
    Top processes by CPU or memory. Blocks for `window` seconds (per-process CPU
    needs two readings) — call via asyncio.to_thread.
    """
    procs = list(psutil.process_iter(["pid", "name", "username"]))
    for p in procs:
        try:
            p.cpu_percent(None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    time.sleep(window)

    result: List[ProcInfo] = []
    for p in procs:
        try:
            with p.oneshot():
                mem = p.memory_info()
                result.append(
                    ProcInfo(
                        pid=p.pid,
                        name=p.info["name"] or "?",
                        user=p.info["username"] or "?",
                        cpu_percent=p.cpu_percent(None),
                        mem_percent=p.memory_percent(),
                        rss_mb=mem.rss / 1024**2,
                    )
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    key = (lambda x: x.mem_percent) if sort in ("mem", "memory", "ram") else (lambda x: x.cpu_percent)
    result.sort(key=key, reverse=True)
    return result[:limit]


def format_top(sort: str = "cpu", limit: int = 5) -> str:
    """HTML block listing the top processes."""
    procs = top_processes(sort, limit)
    by_mem = sort in ("mem", "memory", "ram")
    title = "RAM" if by_mem else "CPU"
    lines = [f"<b>Top {len(procs)} process theo {title}:</b>", "<pre>"]
    lines.append(f"{'PID':>7} {'CPU%':>6} {'MEM%':>5} {'RSS':>7}  NAME")
    for p in procs:
        lines.append(
            f"{p.pid:>7} {p.cpu_percent:>6.1f} {p.mem_percent:>5.1f} {p.rss_mb:>6.0f}M  "
            f"{html.escape(p.name[:24])}"
        )
    lines.append("</pre>")
    return "\n".join(lines)
