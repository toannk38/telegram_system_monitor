import asyncio
import itertools
from types import SimpleNamespace as NS

from bot.panel import Panels, View, _fit

_ids = itertools.count(1)


class Msg:
    def __init__(self, log, photo):
        self.message_id = next(_ids)
        self.chat_id = 1
        self.photo = [1] if photo else []
        self.log = log

    async def edit_text(self, text, parse_mode=None, reply_markup=None):
        self.log.append("edit_text")

    async def edit_media(self, media, reply_markup=None):
        self.log.append("edit_media")


class Chat:
    id = 1

    def __init__(self, log):
        self.log = log

    async def send_message(self, text, parse_mode=None, reply_markup=None):
        self.log.append("send")
        return Msg(self.log, photo=False)

    async def send_photo(self, f, caption=None, parse_mode=None, reply_markup=None):
        self.log.append("send_photo")
        return Msg(self.log, photo=True)


class Bot:
    def __init__(self, log):
        self.log = log

    async def delete_message(self, chat_id, message_id):
        self.log.append(f"delete:{message_id}")

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        self.log.append(f"strip:{message_id}")


def show(panels, view, log, msg=None):
    """msg given → button press on that message; None → typed command."""
    query = NS(message=msg) if msg is not None else None
    update = NS(callback_query=query, effective_chat=Chat(log), get_bot=lambda: Bot(log))
    return asyncio.run(panels.show(update, view))


def photo_view(tmp_path):
    p = tmp_path / "g.png"
    p.write_bytes(b"x")
    return View("chart", photo=p), p


def test_text_to_text_edits():
    log = []
    show(Panels(), View("hi"), log, msg=Msg(log, photo=False))
    assert log == ["edit_text"]


def test_photo_to_photo_edits_media_and_cleans_file(tmp_path):
    log = []
    view, path = photo_view(tmp_path)
    show(Panels(), view, log, msg=Msg(log, photo=True))
    assert log == ["edit_media"]
    assert not path.exists()


def test_graph_then_menu_keeps_graph(tmp_path):
    # Status (text) → Graph (photo) → Menu (text): status and graph stay in the chat.
    log = []
    panels = Panels()
    status = show(panels, View("status"), log)
    view, _ = photo_view(tmp_path)
    graph = show(panels, view, log, msg=status)
    menu = show(panels, View("menu", disposable=True), log, msg=graph)
    assert f"strip:{status.message_id}" in log
    assert f"strip:{graph.message_id}" in log
    assert not any(x.startswith("delete") for x in log)
    assert not menu.photo


def test_menu_is_deleted_when_superseded(tmp_path):
    log = []
    panels = Panels()
    menu = show(panels, View("menu", disposable=True), log)
    view, _ = photo_view(tmp_path)
    show(panels, view, log, msg=menu)  # Menu → Graph
    assert f"delete:{menu.message_id}" in log


def test_menu_edited_into_content_is_kept(tmp_path):
    # Menu edited in place into Status: no longer a bare menu → kept when superseded.
    log = []
    panels = Panels()
    msg = show(panels, View("menu", disposable=True), log)
    show(panels, View("status"), log, msg=msg)
    show(panels, View("new panel"), log)  # typed command
    assert f"strip:{msg.message_id}" in log
    assert f"delete:{msg.message_id}" not in log


def test_non_panel_messages_are_never_touched():
    log = []
    panels = Panels()
    first = show(panels, View("panel"), log)
    bash = show(panels, View("bash output", panel=False), log)
    show(panels, View("panel 2"), log)
    assert f"strip:{first.message_id}" in log
    assert not any(str(bash.message_id) in x for x in log if ":" in x)


def test_long_html_falls_back_to_plain_text():
    text, mode = _fit("<b>" + "x" * 5000 + "</b>", 4096)
    assert mode is None and len(text) <= 4096 and "<b>" not in text
