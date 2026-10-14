"""
Single-message "panel" navigation.

Every screen is a `View` (text or photo + inline keyboard). A button press updates
the message it belongs to instead of posting a new one:

    text  → text   edit_message_text
    photo → photo  edit_message_media
    text ↔ photo   delete + send (Telegram can't convert between the two)

Opening the menu from a content screen also posts a new message, so the screen
you were reading stays put. Typed commands / bottom-keyboard buttons post a new
panel. Whenever a panel is
superseded by a new message, it is *retired*: content screens (graph, status...)
stay in the chat with their buttons removed; bare menus are deleted.
"""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Set

from telegram import Bot, InlineKeyboardMarkup, InputMediaPhoto, Message, Update
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
    disposable: bool = False  # True → deleted (not kept) when superseded, e.g. the main menu


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
        self._disposable: Set[tuple[int, int]] = set()  # (chat_id, message_id) of menu-only panels

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

    def _mark(self, chat_id: int, message_id: int, view: View) -> None:
        key = (chat_id, message_id)
        if view.disposable:
            self._disposable.add(key)
        else:
            self._disposable.discard(key)

    async def _update(self, update: Update, msg: Message, view: View) -> Optional[Message]:
        if view.disposable and (msg.chat_id, msg.message_id) not in self._disposable:
            # Menu pressed on a content screen: keep that screen, open the menu below it.
            return await self._replace(update, msg, view)
        is_photo = bool(msg.photo)
        try:
            if view.photo is None and not is_photo:
                text, mode = _fit(view.text, TEXT_LIMIT)
                await msg.edit_text(text, parse_mode=mode, reply_markup=view.markup)
                self._mark(msg.chat_id, msg.message_id, view)
                return msg
            if view.photo is not None and is_photo:
                caption, mode = _fit(view.text, CAPTION_LIMIT)
                with open(view.photo, "rb") as f:
                    media = InputMediaPhoto(f, caption=caption, parse_mode=mode)
                    await msg.edit_media(media, reply_markup=view.markup)
                self._mark(msg.chat_id, msg.message_id, view)
                return msg
        except BadRequest as exc:
            if "not modified" in str(exc).lower():
                return msg
            raise
        # Kind changes (text ↔ photo): post a new message, retire the old one.
        return await self._replace(update, msg, view)

    async def _replace(self, update: Update, msg: Message, view: View) -> Message:
        new = await self._post(update, view)
        self._mark(msg.chat_id, new.message_id, view)
        self._last[msg.chat_id] = new.message_id
        await self._retire(update.get_bot(), msg.chat_id, msg.message_id)
        return new

    async def _send_new(self, update: Update, view: View) -> Message:
        new = await self._post(update, view)
        if view.panel:
            chat_id = update.effective_chat.id
            previous = self._last.get(chat_id)
            self._last[chat_id] = new.message_id
            self._mark(chat_id, new.message_id, view)
            if previous and previous != new.message_id:
                await self._retire(update.get_bot(), chat_id, previous)
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

    async def _retire(self, bot: Bot, chat_id: int, message_id: int) -> None:
        """Superseded panel: delete bare menus, keep content but drop its buttons."""
        key = (chat_id, message_id)
        try:
            if key in self._disposable:
                self._disposable.discard(key)
                await bot.delete_message(chat_id, message_id)
            else:
                await bot.edit_message_reply_markup(chat_id, message_id, reply_markup=None)
        except TelegramError as exc:  # too old, already gone, or no buttons — harmless
            logger.debug("Could not retire panel %s: %s", message_id, exc)
