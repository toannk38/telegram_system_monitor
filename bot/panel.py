"""
Single-message "panel" navigation.

Every screen is a `View` (text or photo + inline keyboard). A button press updates
the message it belongs to instead of posting a new one:

    text  → text   edit_message_text
    photo → photo  edit_message_media
    text ↔ photo   delete + send (Telegram can't convert between the two)

Typed commands / bottom-keyboard buttons post a new panel and delete the chat's
previous one, so only one live panel stays in the chat.
"""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from telegram import InlineKeyboardMarkup, InputMediaPhoto, Message, Update
from telegram.error import BadRequest, TelegramError

logger = logging.getLogger(__name__)

TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024


@dataclass
class View:
    text: str  # HTML; used as the caption when `photo` is set
    markup: Optional[InlineKeyboardMarkup] = None
    photo: Optional[Path] = None  # temp file, deleted after sending
    panel: bool = True  # False → plain message that is never replaced (e.g. /bash output)


def _strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def _fit(text: str, limit: int) -> tuple[str, Optional[str]]:
    """(text, parse_mode): HTML if it fits, else plain text cut to the limit (never broken HTML)."""
    if len(text) <= limit:
        return text, "HTML"
    plain = _strip_html(text)
    return (plain if len(plain) <= limit else plain[: limit - 20] + "\n...[truncated]"), None


class Panels:
    def __init__(self) -> None:
        self._last: Dict[int, int] = {}  # chat_id → message_id of the live panel

    async def show(self, update: Update, view: View) -> Optional[Message]:
        try:
            query = update.callback_query
            if query is not None and query.message is not None and view.panel:
                return await self._update(update, query.message, view)
            return await self._send_new(update, view)
        finally:
            if view.photo is not None:
                try:
                    view.photo.unlink()
                except OSError:
                    pass

    async def _update(self, update: Update, msg: Message, view: View) -> Optional[Message]:
        is_photo = bool(msg.photo)
        try:
            if view.photo is None and not is_photo:
                text, mode = _fit(view.text, TEXT_LIMIT)
                await msg.edit_text(text, parse_mode=mode, reply_markup=view.markup)
                return msg
            if view.photo is not None and is_photo:
                caption, mode = _fit(view.text, CAPTION_LIMIT)
                with open(view.photo, "rb") as f:
                    media = InputMediaPhoto(f, caption=caption, parse_mode=mode)
                    await msg.edit_media(media, reply_markup=view.markup)
                return msg
        except BadRequest as exc:
            if "not modified" in str(exc).lower():
                return msg
            raise
        # Kind changes (text ↔ photo): replace the message.
        new = await self._post(update, view)
        await self._delete(msg)
        self._last[msg.chat_id] = new.message_id
        return new

    async def _send_new(self, update: Update, view: View) -> Message:
        new = await self._post(update, view)
        if view.panel:
            chat_id = update.effective_chat.id
            previous = self._last.get(chat_id)
            self._last[chat_id] = new.message_id
            if previous and previous != new.message_id:
                await self._delete_id(update, chat_id, previous)
        return new

    @staticmethod
    async def _post(update: Update, view: View) -> Message:
        chat = update.effective_chat
        if view.photo is not None:
            caption, mode = _fit(view.text, CAPTION_LIMIT)
            with open(view.photo, "rb") as f:
                return await chat.send_photo(f, caption=caption, parse_mode=mode, reply_markup=view.markup)
        text, mode = _fit(view.text, TEXT_LIMIT)
        return await chat.send_message(text, parse_mode=mode, reply_markup=view.markup)

    @staticmethod
    async def _delete(msg: Message) -> None:
        try:
            await msg.delete()
        except TelegramError as exc:  # too old (>48h) or already gone — harmless
            logger.debug("Could not delete message %s: %s", msg.message_id, exc)

    @staticmethod
    async def _delete_id(update: Update, chat_id: int, message_id: int) -> None:
        try:
            await update.get_bot().delete_message(chat_id, message_id)
        except TelegramError as exc:
            logger.debug("Could not delete old panel %s: %s", message_id, exc)
