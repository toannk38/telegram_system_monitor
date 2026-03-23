from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from monitor.collector import SystemMetrics
from monitor.grapher import GraphGenerator

if TYPE_CHECKING:
    from monitor.config import Config

logger = logging.getLogger(__name__)


class AlertManager:
    def __init__(self, bot, config: "Config", csv_logger=None) -> None:
        self._bot = bot
        self._config = config
        self._csv_logger = csv_logger
        self._grapher = GraphGenerator()
        self._last_alert: dict[str, datetime] = {
            "cpu": datetime.min,
            "memory": datetime.min,
            "temperature": datetime.min,
        }

    def _cooldown_expired(self, key: str) -> bool:
        elapsed = (datetime.now() - self._last_alert[key]).total_seconds()
        return elapsed >= self._config.ALERT_COOLDOWN_SECONDS

    async def _send_alert(self, text: str) -> None:
        """Send alert text + 12h graph to all ALERT_CHAT_IDS."""
        graph_path = None
        if self._csv_logger is not None:
            try:
                data = self._csv_logger.read_recent(12)
                graph_path = self._grapher.generate(data, 12, self._config)
            except Exception as exc:
                logger.error("Failed to generate alert graph: %s", exc, exc_info=True)

        for chat_id in self._config.ALERT_CHAT_IDS:
            try:
                await self._bot.send_message(chat_id=chat_id, text=text)
                if graph_path and graph_path.exists():
                    with open(graph_path, "rb") as f:
                        await self._bot.send_photo(chat_id=chat_id, photo=f)
            except Exception as exc:
                logger.error("Failed to send alert to %s: %s", chat_id, exc, exc_info=True)

        if graph_path and graph_path.exists():
            try:
                graph_path.unlink()
            except OSError:
                pass

    async def check_and_alert(self, metrics: SystemMetrics) -> None:
        """Check metrics against thresholds and send alerts respecting per-metric cooldown."""
        now = datetime.now()

        if metrics.cpu_percent > self._config.THRESHOLD_CPU and self._cooldown_expired("cpu"):
            self._last_alert["cpu"] = now
            msg = (
                f"🔴 CPU Alert!\n"
                f"CPU usage: {metrics.cpu_percent:.1f}% (threshold: {self._config.THRESHOLD_CPU}%)\n"
                f"Time: {metrics.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            await self._send_alert(msg)

        if metrics.mem_percent > self._config.THRESHOLD_MEMORY and self._cooldown_expired("memory"):
            self._last_alert["memory"] = now
            msg = (
                f"🔴 Memory Alert!\n"
                f"RAM usage: {metrics.mem_percent:.1f}% (threshold: {self._config.THRESHOLD_MEMORY}%)\n"
                f"Time: {metrics.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            await self._send_alert(msg)

        if (
            metrics.cpu_temp_c is not None
            and metrics.cpu_temp_c > self._config.THRESHOLD_TEMP
            and self._cooldown_expired("temperature")
        ):
            self._last_alert["temperature"] = now
            msg = (
                f"🔴 Temperature Alert!\n"
                f"CPU temp: {metrics.cpu_temp_c:.1f}°C (threshold: {self._config.THRESHOLD_TEMP}°C)\n"
                f"Time: {metrics.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            await self._send_alert(msg)

    async def send_startup_notification(self) -> None:
        """Send a startup notification to all ALERT_CHAT_IDS."""
        msg = (
            f"✅ System Monitor started\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Monitoring interval: {self._config.MONITOR_INTERVAL}s\n"
            f"Thresholds — CPU: {self._config.THRESHOLD_CPU}%, "
            f"RAM: {self._config.THRESHOLD_MEMORY}%, "
            f"Temp: {self._config.THRESHOLD_TEMP}°C"
        )
        for chat_id in self._config.ALERT_CHAT_IDS:
            try:
                await self._bot.send_message(chat_id=chat_id, text=msg)
            except Exception as exc:
                logger.error("Failed to send startup notification to %s: %s", chat_id, exc, exc_info=True)
