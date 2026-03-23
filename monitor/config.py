from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _parse_id_list(value: str) -> List[str]:
    """Parse a comma-separated string of IDs into a list, stripping whitespace."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass
class Config:
    BOT_TOKEN: str
    ALERT_CHAT_IDS: List[str]
    BOT_ALLOWED_USERS: List[str]
    BOT_ADMIN_USERS: List[str]
    MONITOR_INTERVAL: int = 60
    LOG_DIR: str = "logs"
    LOG_RETENTION_DAYS: int = 60
    THRESHOLD_MEMORY: float = 80.0
    THRESHOLD_CPU: float = 90.0
    THRESHOLD_TEMP: float = 85.0
    ALERT_COOLDOWN_SECONDS: int = 300


def load_config() -> Config:
    """Load configuration from environment variables."""
    return Config(
        BOT_TOKEN=os.environ.get("BOT_TOKEN", ""),
        ALERT_CHAT_IDS=_parse_id_list(os.environ.get("ALERT_CHAT_IDS", "")),
        BOT_ALLOWED_USERS=_parse_id_list(os.environ.get("BOT_ALLOWED_USERS", "")),
        BOT_ADMIN_USERS=_parse_id_list(os.environ.get("BOT_ADMIN_USERS", "")),
        MONITOR_INTERVAL=int(os.environ.get("MONITOR_INTERVAL", 60)),
        LOG_DIR=os.environ.get("LOG_DIR", "logs"),
        LOG_RETENTION_DAYS=int(os.environ.get("LOG_RETENTION_DAYS", 60)),
        THRESHOLD_MEMORY=float(os.environ.get("THRESHOLD_MEMORY", 80.0)),
        THRESHOLD_CPU=float(os.environ.get("THRESHOLD_CPU", 90.0)),
        THRESHOLD_TEMP=float(os.environ.get("THRESHOLD_TEMP", 85.0)),
        ALERT_COOLDOWN_SECONDS=int(os.environ.get("ALERT_COOLDOWN_SECONDS", 300)),
    )
