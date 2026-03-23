from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class GraphGenerator:
    def generate(self, data: List[Dict[str, str]], hours: int, config) -> Optional[Path]:
        """
        Generate a system metrics graph and save to a temp file.
        Returns the Path to the temp file, or None on error.
        Caller is responsible for deleting the file after use.
        """
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates
            from datetime import datetime

            if not data:
                logger.warning("GraphGenerator: no data to plot")
                return None

            timestamps = []
            cpu_vals = []
            mem_vals = []
            temp_vals = []

            for row in data:
                try:
                    ts = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
                    timestamps.append(ts)
                    cpu_vals.append(float(row["cpu_percent"]))
                    mem_vals.append(float(row["mem_percent"]))
                    temp_str = row.get("cpu_temp_c", "")
                    temp_vals.append(float(temp_str) if temp_str else None)
                except (ValueError, KeyError):
                    continue

            if not timestamps:
                logger.warning("GraphGenerator: no valid rows after parsing")
                return None

            has_temp = any(t is not None for t in temp_vals)
            n_plots = 3 if has_temp else 2

            fig, axes = plt.subplots(n_plots, 1, figsize=(10, 3 * n_plots), sharex=True)
            if n_plots == 1:
                axes = [axes]

            date_fmt = mdates.DateFormatter("%m-%d %H:%M")

            # CPU subplot
            ax_cpu = axes[0]
            ax_cpu.plot(timestamps, cpu_vals, color="blue", linewidth=1)
            ax_cpu.axhline(y=config.THRESHOLD_CPU, color="orange", linestyle="--", linewidth=1)
            ax_cpu.set_ylabel("CPU (%)")
            ax_cpu.set_ylim(0, 100)
            ax_cpu.grid(True, alpha=0.3)

            # RAM subplot
            ax_mem = axes[1]
            ax_mem.plot(timestamps, mem_vals, color="red", linewidth=1)
            ax_mem.axhline(y=config.THRESHOLD_MEMORY, color="orange", linestyle="--", linewidth=1)
            ax_mem.set_ylabel("RAM (%)")
            ax_mem.set_ylim(0, 100)
            ax_mem.grid(True, alpha=0.3)

            # Temp subplot (only if data available)
            if has_temp:
                ax_temp = axes[2]
                clean_temps = [t if t is not None else float("nan") for t in temp_vals]
                ax_temp.plot(timestamps, clean_temps, color="purple", linewidth=1)
                ax_temp.axhline(y=config.THRESHOLD_TEMP, color="orange", linestyle="--", linewidth=1)
                ax_temp.set_ylabel("Temp (°C)")
                ax_temp.grid(True, alpha=0.3)

            # X-axis formatting on the last subplot
            axes[-1].xaxis.set_major_formatter(date_fmt)
            plt.setp(axes[-1].xaxis.get_majorticklabels(), rotation=45, ha="right")

            fig.suptitle(f"System Metrics — Last {hours}h", fontsize=12)
            plt.tight_layout()

            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp.close()
            fig.savefig(tmp.name, dpi=100, bbox_inches="tight")
            plt.close(fig)

            return Path(tmp.name)

        except Exception as exc:
            logger.error("GraphGenerator error: %s", exc, exc_info=True)
            return None
