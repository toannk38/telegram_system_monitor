"""
main.py — Entry point for System Monitor + Telegram Bot service.

Runs in a single asyncio event loop:
  - sampling loop  (SAMPLE_INTERVAL): sub-samples, aggregated every MONITOR_INTERVAL
                                      into one CSV row + metric alerts
  - checks loop    (MONITOR_INTERVAL): Docker / systemd / endpoint checks
  - report loop    (daily, optional):  summary report at DAILY_REPORT_TIME
  - Telegram bot   (polling)
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from datetime import datetime, timedelta

from telegram import BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler

from monitor.alerting import AlertManager, AlertTracker, Notification, Notifier
from monitor.checks import ChecksRunner
from monitor.collector import MetricsCollector
from monitor.config import Config, load_config
from monitor.processes import format_top
from monitor.report import build_report
from monitor.storage import CSVLogger
from bot.handlers import BotDeps, make_handlers
from bot.permissions import PermissionManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

CLEANUP_INTERVAL_SECONDS = 3600


async def _sleep_until(deadline: float, stop: asyncio.Event) -> None:
    """Sleep until the monotonic `deadline` or until `stop` is set."""
    try:
        await asyncio.wait_for(stop.wait(), timeout=max(0.0, deadline - time.monotonic()))
    except asyncio.TimeoutError:
        pass


def _next_slot(next_run: float, interval: float) -> float:
    """Advance a fixed-rate schedule; skip slots missed because a cycle overran."""
    next_run += interval
    now = time.monotonic()
    if next_run < now:
        next_run = now + interval - ((now - next_run) % interval)
    return next_run


async def sampling_loop(
    collector: MetricsCollector,
    csv_logger: CSVLogger,
    alert_manager: AlertManager,
    config: Config,
    stop: asyncio.Event,
) -> None:
    next_run = time.monotonic()
    window_end = next_run + config.MONITOR_INTERVAL
    last_cleanup = float("-inf")

    while not stop.is_set():
        try:
            await asyncio.to_thread(collector.sample)

            if time.monotonic() >= window_end - 0.5:
                window_end += config.MONITOR_INTERVAL
                metrics = await asyncio.to_thread(collector.flush)
                await asyncio.to_thread(csv_logger.write, metrics)
                alert_manager.check(metrics)  # sending happens in background tasks

                if time.monotonic() - last_cleanup >= CLEANUP_INTERVAL_SECONDS:
                    await asyncio.to_thread(csv_logger.cleanup_old_files, config.LOG_RETENTION_DAYS)
                    last_cleanup = time.monotonic()
        except Exception as exc:
            logger.error("Sampling cycle error: %s", exc, exc_info=True)

        next_run = _next_slot(next_run, config.SAMPLE_INTERVAL)
        if window_end < time.monotonic():
            window_end = time.monotonic() + config.MONITOR_INTERVAL
        await _sleep_until(next_run, stop)


async def checks_loop(checks: ChecksRunner, config: Config, stop: asyncio.Event) -> None:
    next_run = time.monotonic()
    while not stop.is_set():
        try:
            await checks.run_once()
        except Exception as exc:
            logger.error("Checks cycle error: %s", exc, exc_info=True)
        next_run = _next_slot(next_run, config.MONITOR_INTERVAL)
        await _sleep_until(next_run, stop)


def _seconds_until(hhmm: str) -> float:
    hour, minute = (int(x) for x in hhmm.split(":"))
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def report_loop(
    notifier: Notifier, csv_logger: CSVLogger, tracker: AlertTracker, config: Config, stop: asyncio.Event
) -> None:
    while not stop.is_set():
        await _sleep_until(time.monotonic() + _seconds_until(config.DAILY_REPORT_TIME), stop)
        if stop.is_set():
            break
        try:
            rows = await asyncio.to_thread(csv_logger.read_recent, 24)
            text = build_report(rows, 24, config.MONITOR_INTERVAL, list(tracker.active()))
            # Daily report is informational — sent even while alerts are muted.
            await notifier.send_report(text, 24)
        except Exception as exc:
            logger.error("Daily report error: %s", exc, exc_info=True)
        await _sleep_until(time.monotonic() + 61, stop)  # don't fire twice in the same minute


def _valid_hhmm(value: str) -> bool:
    try:
        datetime.strptime(value, "%H:%M")
        return True
    except ValueError:
        return False


async def main() -> None:
    config = load_config()

    if not config.BOT_TOKEN:
        logger.error("BOT_TOKEN is not set. Exiting.")
        return

    # ── Init components ───────────────────────────────────────────────────────
    csv_logger = CSVLogger(config.LOG_DIR)
    permission_manager = PermissionManager(config)
    collector = MetricsCollector(config.DISK_PATH, config.NET_DEV_PATH)

    app = Application.builder().token(config.BOT_TOKEN).build()

    tracker = AlertTracker(
        config.ALERT_CONSECUTIVE,
        config.RECOVERY_CONSECUTIVE,
        config.ALERT_REMIND_MINUTES,
        config.ALERT_REMIND_MAX_MINUTES,
    )
    notifier = Notifier(
        app.bot,
        config,
        csv_logger,
        recipients=permission_manager.get_alert_recipients,
        top_provider=lambda sort: format_top(sort, 5),
    )
    alert_manager = AlertManager(config, tracker, notifier)

    docker = None
    if config.DOCKER_MONITOR:
        from monitor.docker_monitor import DockerMonitor

        docker = DockerMonitor(
            config.DOCKER_API_URL, config.DOCKER_WATCH, config.DOCKER_IGNORE, config.DOCKER_RESTART_WINDOW_MINUTES
        )
    checks = ChecksRunner(config, tracker, notifier, docker)

    # ── Telegram handlers ─────────────────────────────────────────────────────
    handlers = make_handlers(
        BotDeps(permission_manager, csv_logger, config, collector, notifier, tracker, checks)
    )
    for name, handler in handlers.items():
        if name == "callback":
            app.add_handler(CallbackQueryHandler(handler))
        else:
            app.add_handler(CommandHandler(name, handler))

    # systemd / docker stop the service with SIGTERM — shut down cleanly.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    # ── Manual bot lifecycle ──────────────────────────────────────────────────
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    menu = [
        BotCommand("status", "Trạng thái hệ thống"),
        BotCommand("graph", "Biểu đồ metrics"),
        BotCommand("top", "Top process"),
        BotCommand("alerts", "Cảnh báo đang mở"),
        BotCommand("report", "Báo cáo 24h"),
    ]
    if checks.docker:
        menu.append(BotCommand("docker", "Trạng thái container"))
    if checks.services_enabled:
        menu.append(BotCommand("services", "Trạng thái service"))
    if checks.endpoints:
        menu.append(BotCommand("endpoints", "Kiểm tra endpoint"))
    menu += [BotCommand("help", "Trợ giúp"), BotCommand("id", "Xem Telegram ID")]
    await app.bot.set_my_commands(menu)

    features = [
        "Docker: " + (
            "off" if not checks.docker
            else f"alert {','.join(sorted(config.DOCKER_ALERT))}" if config.DOCKER_ALERT
            else "on (không cảnh báo)"
        ),
        f"Services: {len(config.SYSTEMD_SERVICES) if checks.services_enabled else 'off'}",
        f"Endpoints: {len(checks.endpoints) or 'off'}",
        f"Daily report: {config.DAILY_REPORT_TIME or 'off'}",
    ]
    notifier.dispatch(
        Notification(
            f"✅ <b>System Monitor started</b>\n"
            f"Thu thập: mẫu mỗi {config.SAMPLE_INTERVAL}s, ghi mỗi {config.MONITOR_INTERVAL}s\n"
            f"Ngưỡng — CPU {config.THRESHOLD_CPU:g}%, RAM {config.THRESHOLD_MEMORY:g}%, "
            f"Disk {config.THRESHOLD_DISK:g}%, Temp {config.THRESHOLD_TEMP:g}°C "
            f"(sau {config.ALERT_CONSECUTIVE} lần liên tiếp)\n"
            + " · ".join(features)
        ),
        force=True,
    )

    # ── Background loops ──────────────────────────────────────────────────────
    logger.info(
        "Started. Sample %ds, window %ds, log dir %s, data dir %s, disk %s; %s",
        config.SAMPLE_INTERVAL, config.MONITOR_INTERVAL, config.LOG_DIR, config.DATA_DIR,
        config.DISK_PATH, ", ".join(features),
    )
    tasks = [asyncio.create_task(sampling_loop(collector, csv_logger, alert_manager, config, stop))]
    if checks.enabled:
        tasks.append(asyncio.create_task(checks_loop(checks, config, stop)))
    if config.DAILY_REPORT_TIME:
        if _valid_hhmm(config.DAILY_REPORT_TIME):
            tasks.append(asyncio.create_task(report_loop(notifier, csv_logger, tracker, config, stop)))
        else:
            logger.warning("DAILY_REPORT_TIME=%r is not HH:MM — daily report disabled", config.DAILY_REPORT_TIME)

    try:
        await stop.wait()
    finally:
        logger.info("Shutting down...")
        await asyncio.gather(*tasks, return_exceptions=True)
        await notifier.drain()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
