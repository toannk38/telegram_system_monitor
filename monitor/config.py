from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import FrozenSet, List

from dotenv import load_dotenv

load_dotenv()


def _parse_id_list(value: str) -> List[str]:
    """Parse a comma-separated string of IDs into a list, stripping whitespace."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


DOCKER_ALERT_KINDS = ("unhealthy", "crashloop", "oom", "exit")


def _parse_docker_alert(value: str) -> FrozenSet[str]:
    """
    DOCKER_ALERT: "true"/"all" → every kind, "false"/"" → none,
    or a comma-separated subset of unhealthy,crashloop,oom,exit.
    """
    v = value.strip().lower()
    if v in ("1", "true", "yes", "on", "all"):
        return frozenset(DOCKER_ALERT_KINDS)
    if v in ("", "0", "false", "no", "off", "none"):
        return frozenset()
    kinds = {k.strip() for k in v.split(",") if k.strip()}
    unknown = kinds - set(DOCKER_ALERT_KINDS)
    if unknown:
        logging.getLogger(__name__).warning(
            "DOCKER_ALERT: ignoring unknown kinds %s (valid: %s)", sorted(unknown), ",".join(DOCKER_ALERT_KINDS)
        )
    return frozenset(kinds & set(DOCKER_ALERT_KINDS))


@dataclass
class Config:
    BOT_TOKEN: str
    ALERT_CHAT_IDS: List[str]
    BOT_ALLOWED_USERS: List[str]
    BOT_ADMIN_USERS: List[str]

    # ── Sampling / storage ────────────────────────────────────────────────────
    MONITOR_INTERVAL: int = 60
    SAMPLE_INTERVAL: int = 5
    LOG_DIR: str = "logs"
    DATA_DIR: str = "data"
    LOG_RETENTION_DAYS: int = 60
    DISK_PATH: str = "/"
    NET_DEV_PATH: str = ""

    # ── Thresholds ────────────────────────────────────────────────────────────
    THRESHOLD_MEMORY: float = 80.0
    THRESHOLD_CPU: float = 90.0
    THRESHOLD_TEMP: float = 85.0
    THRESHOLD_DISK: float = 90.0

    # ── Alert behaviour ───────────────────────────────────────────────────────
    ALERT_CONSECUTIVE: int = 3
    RECOVERY_CONSECUTIVE: int = 3
    ALERT_HYSTERESIS: float = 5.0
    ALERT_REMIND_MINUTES: int = 60
    ALERT_REMIND_MAX_MINUTES: int = 480

    # ── Optional integrations ─────────────────────────────────────────────────
    DOCKER_MONITOR: bool = False
    DOCKER_API_URL: str = "unix:///var/run/docker.sock"
    DOCKER_ALERT: FrozenSet[str] = frozenset(DOCKER_ALERT_KINDS)
    DOCKER_WATCH: List[str] = field(default_factory=list)
    DOCKER_IGNORE: List[str] = field(default_factory=list)
    DOCKER_RESTART_WINDOW_MINUTES: int = 10
    SYSTEMD_SERVICES: List[str] = field(default_factory=list)
    ENDPOINTS: List[str] = field(default_factory=list)
    ENDPOINT_TIMEOUT: float = 10.0
    DAILY_REPORT_TIME: str = ""


def load_config() -> Config:
    """Load configuration from environment variables."""
    env = os.environ.get
    monitor_interval = max(5, int(env("MONITOR_INTERVAL", 60)))
    return Config(
        BOT_TOKEN=env("BOT_TOKEN", ""),
        ALERT_CHAT_IDS=_parse_id_list(env("ALERT_CHAT_IDS", "")),
        BOT_ALLOWED_USERS=_parse_id_list(env("BOT_ALLOWED_USERS", "")),
        BOT_ADMIN_USERS=_parse_id_list(env("BOT_ADMIN_USERS", "")),
        MONITOR_INTERVAL=monitor_interval,
        SAMPLE_INTERVAL=min(monitor_interval, max(1, int(env("SAMPLE_INTERVAL", 5)))),
        LOG_DIR=env("LOG_DIR", "logs"),
        DATA_DIR=env("DATA_DIR", "data"),
        LOG_RETENTION_DAYS=int(env("LOG_RETENTION_DAYS", 60)),
        DISK_PATH=env("DISK_PATH", "/"),
        NET_DEV_PATH=env("NET_DEV_PATH", ""),
        THRESHOLD_MEMORY=float(env("THRESHOLD_MEMORY", 80.0)),
        THRESHOLD_CPU=float(env("THRESHOLD_CPU", 90.0)),
        THRESHOLD_TEMP=float(env("THRESHOLD_TEMP", 85.0)),
        THRESHOLD_DISK=float(env("THRESHOLD_DISK", 90.0)),
        ALERT_CONSECUTIVE=max(1, int(env("ALERT_CONSECUTIVE", 3))),
        RECOVERY_CONSECUTIVE=max(1, int(env("RECOVERY_CONSECUTIVE", 3))),
        ALERT_HYSTERESIS=max(0.0, float(env("ALERT_HYSTERESIS", 5.0))),
        ALERT_REMIND_MINUTES=max(0, int(env("ALERT_REMIND_MINUTES", 60))),
        ALERT_REMIND_MAX_MINUTES=max(1, int(env("ALERT_REMIND_MAX_MINUTES", 480))),
        DOCKER_MONITOR=_parse_bool(env("DOCKER_MONITOR", "false")),
        DOCKER_API_URL=env("DOCKER_API_URL", "unix:///var/run/docker.sock"),
        DOCKER_ALERT=_parse_docker_alert(env("DOCKER_ALERT", "true")),
        DOCKER_WATCH=_parse_id_list(env("DOCKER_WATCH", "")),
        DOCKER_IGNORE=_parse_id_list(env("DOCKER_IGNORE", "")),
        DOCKER_RESTART_WINDOW_MINUTES=max(1, int(env("DOCKER_RESTART_WINDOW_MINUTES", 10))),
        SYSTEMD_SERVICES=_parse_id_list(env("SYSTEMD_SERVICES", "")),
        ENDPOINTS=_parse_id_list(env("ENDPOINTS", "")),
        ENDPOINT_TIMEOUT=float(env("ENDPOINT_TIMEOUT", 10.0)),
        DAILY_REPORT_TIME=env("DAILY_REPORT_TIME", "").strip(),
    )
