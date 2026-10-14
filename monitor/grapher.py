from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _num(row: Dict[str, str], column: str) -> Optional[float]:
    value = row.get(column)
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _nan(values: List[Optional[float]]) -> List[float]:
    return [float("nan") if v is None else v for v in values]


def _has(values: List[Optional[float]]) -> bool:
    return any(v is not None for v in values)


def _rate_scale(values: List[Optional[float]]) -> tuple[float, str]:
    peak = max((v for v in values if v is not None), default=0)
    if peak >= 1024**2:
        return 1024**2, "MB/s"
    return 1024, "KB/s"


class GraphGenerator:
    def generate(self, data: List[Dict[str, str]], hours: int, config) -> Optional[Path]:
        """
        Generate a system metrics graph and save to a temp file.
        Returns the Path to the temp file, or None on error.
        Caller is responsible for deleting the file after use.
        """
        try:
            # Figure API (no pyplot): no global state, safe to call from worker threads.
            from matplotlib.figure import Figure
            import matplotlib.dates as mdates

            from monitor.storage import parse_timestamp

            if not data:
                logger.warning("GraphGenerator: no data to plot")
                return None

            ts, series = [], {}
            columns = (
                "cpu_percent", "cpu_max", "mem_percent", "swap_percent", "cpu_temp_c", "gpu_temp_c",
                "net_rx_bps", "net_tx_bps", "disk_read_bps", "disk_write_bps",
            )
            for c in columns:
                series[c] = []
            for row in data:
                try:
                    # Plot in local wall-clock time (matplotlib defaults to UTC for aware datetimes).
                    t = parse_timestamp(row["timestamp"]).astimezone().replace(tzinfo=None)
                    cpu = float(row["cpu_percent"])
                    mem = float(row["mem_percent"])
                except (ValueError, KeyError, TypeError):
                    continue
                ts.append(t)
                for c in columns:
                    series[c].append(_num(row, c))
                series["cpu_percent"][-1] = cpu
                series["mem_percent"][-1] = mem

            if not ts:
                logger.warning("GraphGenerator: no valid rows after parsing")
                return None

            panels = ["cpu", "mem"]
            if _has(series["cpu_temp_c"]) or _has(series["gpu_temp_c"]):
                panels.append("temp")
            if _has(series["net_rx_bps"]) or _has(series["net_tx_bps"]):
                panels.append("net")
            if _has(series["disk_read_bps"]) or _has(series["disk_write_bps"]):
                panels.append("diskio")

            fig = Figure(figsize=(10, 2.6 * len(panels)))
            axes = fig.subplots(len(panels), 1, sharex=True, squeeze=False)[:, 0]

            for ax, panel in zip(axes, panels):
                ax.grid(True, alpha=0.3)
                if panel == "cpu":
                    if _has(series["cpu_max"]):
                        ax.plot(ts, _nan(series["cpu_max"]), color="blue", alpha=0.25, linewidth=0.8, label="max")
                    ax.plot(ts, series["cpu_percent"], color="blue", linewidth=1.2, label="avg")
                    ax.axhline(y=config.THRESHOLD_CPU, color="orange", linestyle="--", linewidth=1)
                    ax.set_ylabel("CPU (%)")
                    ax.set_ylim(0, 100)
                    if _has(series["cpu_max"]):
                        ax.legend(loc="upper left", fontsize=8)
                elif panel == "mem":
                    ax.plot(ts, series["mem_percent"], color="red", linewidth=1.2, label="RAM")
                    if _has(series["swap_percent"]):
                        ax.plot(ts, _nan(series["swap_percent"]), color="brown", linestyle=":", linewidth=1, label="Swap")
                        ax.legend(loc="upper left", fontsize=8)
                    ax.axhline(y=config.THRESHOLD_MEMORY, color="orange", linestyle="--", linewidth=1)
                    ax.set_ylabel("RAM (%)")
                    ax.set_ylim(0, 100)
                elif panel == "temp":
                    if _has(series["cpu_temp_c"]):
                        ax.plot(ts, _nan(series["cpu_temp_c"]), color="purple", linewidth=1, label="CPU")
                    if _has(series["gpu_temp_c"]):
                        ax.plot(ts, _nan(series["gpu_temp_c"]), color="green", linewidth=1, label="GPU")
                    ax.axhline(y=config.THRESHOLD_TEMP, color="orange", linestyle="--", linewidth=1)
                    ax.set_ylabel("Temp (°C)")
                    ax.legend(loc="upper left", fontsize=8)
                elif panel in ("net", "diskio"):
                    a, b = ("net_rx_bps", "net_tx_bps") if panel == "net" else ("disk_read_bps", "disk_write_bps")
                    la, lb = ("↓ rx", "↑ tx") if panel == "net" else ("read", "write")
                    scale, unit = _rate_scale(series[a] + series[b])
                    ax.plot(ts, [v / scale for v in _nan(series[a])], color="teal", linewidth=1, label=la)
                    ax.plot(ts, [v / scale for v in _nan(series[b])], color="darkorange", linewidth=1, label=lb)
                    ax.set_ylabel(f"{'Net' if panel == 'net' else 'Disk IO'} ({unit})")
                    ax.set_ylim(bottom=0)
                    ax.legend(loc="upper left", fontsize=8)

            # X-axis formatting on the last subplot
            axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
            for label in axes[-1].xaxis.get_majorticklabels():
                label.set_rotation(45)
                label.set_horizontalalignment("right")

            fig.suptitle(f"System Metrics — Last {hours}h", fontsize=12)
            fig.tight_layout()

            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp.close()
            fig.savefig(tmp.name, dpi=100, bbox_inches="tight")

            return Path(tmp.name)

        except Exception as exc:
            logger.error("GraphGenerator error: %s", exc, exc_info=True)
            return None
