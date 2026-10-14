from __future__ import annotations

import csv
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

from monitor.collector import SystemMetrics

logger = logging.getLogger(__name__)

CSV_COLUMNS = [
    "timestamp",
    "cpu_percent",
    "mem_percent",
    "mem_used_mb",
    "mem_total_mb",
    "cpu_temp_c",
    "disk_percent",
    "disk_used_gb",
    "disk_total_gb",
    "cpu_max",
    "mem_max",
    "cpu_temp_max",
    "swap_percent",
    "load1",
    "load5",
    "load15",
    "gpu_temp_c",
    "net_rx_bps",
    "net_tx_bps",
    "disk_read_bps",
    "disk_write_bps",
]


def _metrics_filename(date: datetime, log_dir: str) -> Path:
    return Path(log_dir) / f"system_stats_{date.strftime('%Y%m%d')}.csv"


def parse_timestamp(value: str) -> datetime:
    """
    Parse a CSV timestamp into a timezone-aware datetime.

    Accepts both the current format ("2026-10-14 10:00:00+07:00") and the
    legacy naive format ("2026-10-14 10:00:00"), which is assumed to be local time.
    """
    ts = datetime.fromisoformat(value)
    if ts.tzinfo is None:
        ts = ts.astimezone()
    return ts


def _format_value(value: Optional[float]) -> str:
    return "" if value is None else str(value)


def _metrics_to_row(metrics: SystemMetrics) -> Dict[str, str]:
    row = {"timestamp": metrics.timestamp.isoformat(sep=" ", timespec="seconds")}
    for column in CSV_COLUMNS[1:]:
        row[column] = _format_value(getattr(metrics, column))
    return row


def _read_header(path: Path) -> Optional[List[str]]:
    """Return the header row of an existing CSV file, or None if the file is empty."""
    with path.open("r", newline="", encoding="utf-8") as f:
        return next(csv.reader(f), None)


def _migrate_columns(path: Path) -> None:
    """Rewrite a CSV written by an older version with the current columns (missing ones blank)."""
    with path.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore", restval="")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)
    logger.info("Migrated %s to current CSV columns (%d rows)", path, len(rows))


class CSVLogger:
    def __init__(self, log_dir: str) -> None:
        self._log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)

    def write(self, metrics: SystemMetrics) -> None:
        """Append metrics to the daily CSV file, creating it with a header if new."""
        try:
            target = _metrics_filename(metrics.timestamp, self._log_dir)
            header = _read_header(target) if target.exists() else None
            if header is not None and header != CSV_COLUMNS:
                _migrate_columns(target)

            with target.open("a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
                if header is None:
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
        now = datetime.now().astimezone()
        cutoff = now - timedelta(hours=hours)
        rows: List[tuple[datetime, Dict[str, str]]] = []

        # Collect candidate files: today and any recent days within the window
        days_to_check = max(1, hours // 24 + 2)
        candidate_dates = [now - timedelta(days=d) for d in range(days_to_check)]

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
                            ts = parse_timestamp(row["timestamp"])
                        except (ValueError, KeyError, TypeError):
                            continue
                        if ts >= cutoff:
                            rows.append((ts, dict(row)))
            except Exception as exc:
                logger.error("CSVLogger read error for %s: %s", path, exc, exc_info=True)

        rows.sort(key=lambda item: item[0])
        return [row for _, row in rows]
