import asyncio
import itertools
from types import SimpleNamespace as NS

from bot.panel import Panels, View

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

    async def delete(self):
        self.log.append(f"delete:{self.message_id}")


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


def run(panels, msg, view, log, typed=False):
    async def delete_message(chat_id, mid):
        log.append(f"delete:{mid}")
    query = None if typed else NS(message=msg)
    update = NS(callback_query=query, effective_chat=Chat(log), get_bot=lambda: NS(delete_message=delete_message))
    return asyncio.run(panels.show(update, view))


def photo_view(tmp_path):
    p = tmp_path / "g.png"
    p.write_bytes(b"x")
    return View("chart", photo=p), p


def test_text_to_text_edits(tmp_path):
    log = []
    run(Panels(), Msg(log, photo=False), View("hi"), log)
    assert log == ["edit_text"]


def test_photo_to_photo_edits_media_and_cleans_file(tmp_path):
    log = []
    view, path = photo_view(tmp_path)
    run(Panels(), Msg(log, photo=True), view, log)
    assert log == ["edit_media"]
    assert not path.exists()


def test_text_to_photo_replaces_message(tmp_path):
    log = []
    old = Msg(log, photo=False)
    view, _ = photo_view(tmp_path)
    new = run(Panels(), old, view, log)
    assert log == ["send_photo", f"delete:{old.message_id}"]
    assert new.photo


def test_typed_command_replaces_previous_panel():
    log = []
    panels = Panels()
    first = run(panels, None, View("a"), log, typed=True)
    second = run(panels, None, View("b"), log, typed=True)
    assert log == ["send", "send", f"delete:{first.message_id}"]
    assert second.message_id != first.message_id


def test_non_panel_messages_are_kept():
    log = []
    panels = Panels()
    first = run(panels, None, View("panel"), log, typed=True)
    bash = run(panels, None, View("bash output", panel=False), log, typed=True)
    run(panels, None, View("panel 2"), log, typed=True)
    # only the first panel is deleted; the bash output survives
    deleted = [x for x in log if x.startswith("delete")]
    assert deleted == [f"delete:{first.message_id}"]
    assert f"delete:{bash.message_id}" not in log


def test_long_html_falls_back_to_plain_text():
    from bot.panel import _fit
    text, mode = _fit("<b>" + "x" * 5000 + "</b>", 4096)
    assert mode is None and len(text) <= 4096 and "<b>" not in text
