"""
main.py — Entry point for System Monitor + Telegram Bot service.

Runs the monitor loop and Telegram bot concurrently in a single asyncio event loop.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from telegram import BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler

from monitor.config import load_config
from monitor.collector import MetricsCollector
from monitor.storage import CSVLogger
from monitor.alerting import AlertManager
from bot.permissions import PermissionManager
from bot.handlers import make_handlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


async def main() -> None:
    config = load_config()

    if not config.BOT_TOKEN:
        logger.error("BOT_TOKEN is not set. Exiting.")
        return

    # ── Init components ───────────────────────────────────────────────────────
    csv_logger = CSVLogger(config.LOG_DIR)
    permission_manager = PermissionManager(config)
    collector = MetricsCollector()

    # ── Build Telegram application ────────────────────────────────────────────
    app = Application.builder().token(config.BOT_TOKEN).build()

    handlers = make_handlers(permission_manager, csv_logger, config)
    app.add_handler(CommandHandler("start", handlers["start"]))
    app.add_handler(CommandHandler("id", handlers["id"]))
    app.add_handler(CommandHandler("help", handlers["help"]))
    app.add_handler(CommandHandler("status", handlers["status"]))
    app.add_handler(CommandHandler("graph", handlers["graph"]))
    app.add_handler(CommandHandler("ping", handlers["ping"]))
    app.add_handler(CommandHandler("bash", handlers["bash"]))
    app.add_handler(CommandHandler("allow", handlers["allow"]))
    app.add_handler(CommandHandler("unallow", handlers["unallow"]))
    app.add_handler(CommandHandler("addalert", handlers["addalert"]))
    app.add_handler(CommandHandler("removealert", handlers["removealert"]))
    app.add_handler(CommandHandler("listusers", handlers["listusers"]))
    app.add_handler(CallbackQueryHandler(handlers["callback"]))

    # ── AlertManager needs the bot instance ───────────────────────────────────
    alert_manager = AlertManager(app.bot, config, csv_logger)

    # ── Manual bot lifecycle ──────────────────────────────────────────────────
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    await app.bot.set_my_commands([
        BotCommand("start", "Chào mừng"),
        BotCommand("status", "Trạng thái hệ thống"),
        BotCommand("graph", "Biểu đồ metrics"),
        BotCommand("help", "Trợ giúp"),
        BotCommand("id", "Xem Telegram ID"),
        BotCommand("ping", "Kiểm tra độ trễ"),
    ])

    await alert_manager.send_startup_notification()

    # ── Monitor loop ──────────────────────────────────────────────────────────
    logger.info("Monitor loop started. Interval: %ds", config.MONITOR_INTERVAL)
    try:
        while True:
            try:
                metrics = collector.collect()
                csv_logger.write(metrics)
                await alert_manager.check_and_alert(metrics)
                csv_logger.cleanup_old_files(config.LOG_RETENTION_DAYS)
            except Exception as exc:
                logger.error("Monitor cycle error: %s", exc, exc_info=True)
            await asyncio.sleep(config.MONITOR_INTERVAL)
    finally:
        logger.info("Shutting down...")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
