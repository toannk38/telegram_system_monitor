from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from monitor.config import Config

logger = logging.getLogger(__name__)

USERS_FILE = Path("data/users.json")


class PermissionManager:
    def __init__(self, config: "Config") -> None:
        # Admin list is immutable — loaded from env only
        self._admins: set[str] = set(config.BOT_ADMIN_USERS)

        # Mutable sets — merged from env + persisted file
        self._allowed_users: set[str] = set(config.BOT_ALLOWED_USERS)
        self._alert_recipients: set[str] = set(config.ALERT_CHAT_IDS)

        self._load_persisted()

    # ── Tier checks ──────────────────────────────────────────────────────────

    def is_admin(self, user_id: str) -> bool:
        return str(user_id) in self._admins

    def is_authorized(self, user_id: str) -> bool:
        """Returns True for both admins and allowed users."""
        uid = str(user_id)
        return uid in self._admins or uid in self._allowed_users

    # ── Allowed users ─────────────────────────────────────────────────────────

    def add_allowed_user(self, user_id: str) -> None:
        self._allowed_users.add(str(user_id))
        self._save()

    def remove_allowed_user(self, user_id: str) -> None:
        self._allowed_users.discard(str(user_id))
        self._save()

    def get_allowed_users(self) -> List[str]:
        return sorted(self._allowed_users)

    # ── Alert recipients ──────────────────────────────────────────────────────

    def add_alert_recipient(self, user_id: str) -> None:
        self._alert_recipients.add(str(user_id))
        self._save()

    def remove_alert_recipient(self, user_id: str) -> None:
        self._alert_recipients.discard(str(user_id))
        self._save()

    def get_alert_recipients(self) -> List[str]:
        return sorted(self._alert_recipients)

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load_persisted(self) -> None:
        """Merge data/users.json into in-memory sets (union, no duplicates)."""
        if not USERS_FILE.exists():
            return
        try:
            with USERS_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            self._allowed_users |= set(str(u) for u in data.get("allowed_users", []))
            self._alert_recipients |= set(str(u) for u in data.get("alert_chat_ids", []))
        except Exception as exc:
            logger.error("Failed to load %s: %s", USERS_FILE, exc, exc_info=True)

    def _save(self) -> None:
        """Persist current allowed_users and alert_chat_ids to data/users.json."""
        try:
            USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "allowed_users": sorted(self._allowed_users),
                "alert_chat_ids": sorted(self._alert_recipients),
            }
            with USERS_FILE.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            logger.error("Failed to save %s: %s", USERS_FILE, exc, exc_info=True)
