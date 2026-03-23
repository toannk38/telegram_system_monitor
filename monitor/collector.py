from __future__ import annotations

import platform
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import psutil

logger = logging.getLogger(__name__)


@dataclass
class SystemMetrics:
    timestamp: datetime
    cpu_percent: float
    mem_percent: float
    mem_used_mb: float
    mem_total_mb: float
    cpu_temp_c: Optional[float]


class MetricsCollector:
    def collect(self) -> SystemMetrics:
        """Collect current system metrics."""
        cpu = psutil.cpu_percent(interval=1)

        mem = psutil.virtual_memory()
        mem_percent = mem.percent
        mem_used_mb = mem.used / (1024 * 1024)
        mem_total_mb = mem.total / (1024 * 1024)

        cpu_temp = self._get_cpu_temperature()

        return SystemMetrics(
            timestamp=datetime.now(),
            cpu_percent=cpu,
            mem_percent=mem_percent,
            mem_used_mb=mem_used_mb,
            mem_total_mb=mem_total_mb,
            cpu_temp_c=cpu_temp,
        )

    def _get_cpu_temperature(self) -> Optional[float]:
        """Attempt to read CPU temperature; return None if unsupported or on error."""
        try:
            if not hasattr(psutil, "sensors_temperatures"):
                return None

            temps = psutil.sensors_temperatures()
            if not temps:
                return None

            # Prefer coretemp (Intel) or cpu_thermal (ARM/Raspberry Pi)
            for key in ("coretemp", "cpu_thermal"):
                if key in temps and temps[key]:
                    return temps[key][0].current

            # Fall back to first available sensor
            for entries in temps.values():
                if entries:
                    return entries[0].current

            return None
        except Exception as exc:
            logger.warning("Could not read CPU temperature: %s", exc)
            return None
