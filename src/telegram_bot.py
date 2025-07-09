#!/usr/bin/env python3
"""
Telegram Bot Service (Legacy Entry Point)
Provides system monitoring commands via Telegram bot

This file is kept for backwards compatibility.
For new deployments, use run_bot.py instead.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add src directory to path for imports
sys.path.append(str(Path(__file__).parent))

from bot import TelegramBot

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/telegram_bot.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)



async def main():
    """Main function to run the Telegram bot (legacy entry point)"""
    try:
        logger.info("Initializing Telegram Bot (legacy entry point)...")
        bot = TelegramBot()
        
        logger.info("Starting bot...")
        await bot.run()
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (Ctrl+C)")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())