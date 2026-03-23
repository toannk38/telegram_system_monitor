from __future__ import annotations

import csv
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict

from monitor.collector import SystemMetrics

logger = logging.getLogger(__name__)

CSV_COLUMNS = ["timestamp", "cpu_percent", "mem_percent", "mem_used_mb", "mem_total_mb", "cpu_temp_c"]


def _metrics_filename(date: datetime, log_dir: str) -> Path:
    return Path(log_dir) / f"system_stats_{date.strftime('%Y%m%d')}.csv"


def _metrics_to_row(metrics: SystemMetrics) -> Dict[str, str]:
    return {
        "timestamp": metrics.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "cpu_percent": str(metrics.cpu_percent),
        "mem_percent": str(metrics.mem_percent),
        "mem_used_mb": str(metrics.mem_used_mb),
        "mem_total_mb": str(metrics.mem_total_mb),
        "cpu_temp_c": "" if metrics.cpu_temp_c is None else str(metrics.cpu_temp_c),
    }


class CSVLogger:
    def __init__(self, log_dir: str) -> None:
        self._log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)

    def write(self, metrics: SystemMetrics) -> None:
        """Append metrics to the daily CSV file, creating it with a header if new."""
        try:
            target = _metrics_filename(metrics.timestamp, self._log_dir)
            is_new = not target.exists()

            with target.open("a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
                if is_new:
                    writer.writeheader()
                writer.writerow(_metrics_to_row(metrics))
        except Exception as exc:
            logger.error("CSVLogger write error: %s", exc, exc_info=True)

    def cleanup_old_files(self, retention_days: int) -> None:
        """Delete CSV files older than retention_days days."""
        cutoff = datetime.now() - timedelta(days=retention_days)
        log_path = Path(self._log_dir)
        for f in log_path.glob("system_stats_*.csv"):
            try:
                date_str = f.stem.replace("system_stats_", "")
                file_date = datetime.strptime(date_str, "%Y%m%d")
                if file_date < cutoff:
                    f.unlink()
                    logger.info("Deleted old log file: %s", f)
            except (ValueError, OSError) as exc:
                logger.warning("Could not process file %s: %s", f, exc)

    def read_recent(self, hours: int) -> List[Dict[str, str]]:
        """Read all metric rows from CSV files within the last `hours` hours."""
        cutoff = datetime.now() - timedelta(hours=hours)
        rows: List[Dict[str, str]] = []

        # Collect candidate files: today and any recent days within the window
        days_to_check = max(1, hours // 24 + 2)
        candidate_dates = [datetime.now() - timedelta(days=d) for d in range(days_to_check)]

        seen_files: set = set()
        for date in candidate_dates:
            path = _metrics_filename(date, self._log_dir)
            if path in seen_files or not path.exists():
                continue
            seen_files.add(path)
            try:
                with path.open("r", newline="", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        try:
                            ts = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
                            if ts >= cutoff:
                                rows.append(dict(row))
                        except (ValueError, KeyError):
                            continue
            except Exception as exc:
                logger.error("CSVLogger read error for %s: %s", path, exc, exc_info=True)

        rows.sort(key=lambda r: r.get("timestamp", ""))
        return rows
