"""Summary report (min / avg / max) over recent CSV rows."""
from __future__ import annotations

from statistics import fmean
from typing import Dict, List, Optional

from monitor.storage import parse_timestamp

_SERIES = [
    ("cpu_percent", "CPU", "%"),
    ("mem_percent", "RAM", "%"),
    ("swap_percent", "Swap", "%"),
    ("disk_percent", "Disk", "%"),
    ("load1", "Load 1m", ""),
    ("cpu_temp_c", "CPU temp", "°C"),
    ("gpu_temp_c", "GPU temp", "°C"),
]


def _values(rows: List[Dict[str, str]], column: str) -> List[float]:
    out = []
    for r in rows:
        try:
            v = r.get(column)
            if v:
                out.append(float(v))
        except ValueError:
            continue
    return out


def human_bytes(n: Optional[float], suffix: str = "") -> str:
    if n is None:
        return "N/A"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f} {unit}{suffix}" if unit != "B" else f"{n:.0f} {unit}{suffix}"
        n /= 1024
    return f"{n:.1f}"


def build_report(rows: List[Dict[str, str]], hours: int, monitor_interval: int, active_alerts: List[str]) -> str:
    """HTML report text."""
    if not rows:
        return f"📋 <b>Báo cáo {hours}h</b>\n\nKhông có dữ liệu."

    first = parse_timestamp(rows[0]["timestamp"])
    last = parse_timestamp(rows[-1]["timestamp"])
    lines = [
        f"📋 <b>Báo cáo {hours}h</b> ({first:%H:%M %d/%m} → {last:%H:%M %d/%m})",
        "<pre>",
        f"{'':<9}{'min':>8}{'avg':>8}{'max':>8}",
    ]
    for column, label, unit in _SERIES:
        vals = _values(rows, column)
        if not vals:
            continue
        # The CPU column is a per-window average; its peak is in cpu_max.
        peak_vals = _values(rows, "cpu_max") if column == "cpu_percent" else vals
        lines.append(
            f"{label:<9}{min(vals):>7.1f}{unit[:1]:<1}{fmean(vals):>7.1f}{unit[:1]:<1}{max(peak_vals or vals):>7.1f}{unit[:1]:<1}"
        )
    lines.append("</pre>")

    # Approximate totals from per-window average rates.
    totals = []
    for column, label in (("net_rx_bps", "Net ↓"), ("net_tx_bps", "Net ↑"), ("disk_read_bps", "Disk read"), ("disk_write_bps", "Disk write")):
        vals = _values(rows, column)
        if vals:
            totals.append(f"{label}: {human_bytes(sum(vals) * monitor_interval)}")
    if totals:
        lines.append("📶 " + " · ".join(totals))

    if active_alerts:
        lines.append("\n🔴 <b>Đang cảnh báo:</b> " + ", ".join(active_alerts))
    else:
        lines.append("\n✅ Không có cảnh báo đang mở")
    return "\n".join(lines)
