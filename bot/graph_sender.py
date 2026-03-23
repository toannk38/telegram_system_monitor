from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from monitor.grapher import GraphGenerator

if TYPE_CHECKING:
    from monitor.config import Config
    from monitor.storage import CSVLogger

logger = logging.getLogger(__name__)

_grapher = GraphGenerator()


async def send_graph(bot, chat_id: str, hours: int, csv_logger: "CSVLogger", config: "Config") -> None:
    """Read recent data, generate a graph, send it as a photo, then delete the temp file."""
    try:
        data = csv_logger.read_recent(hours)
        if not data:
            await bot.send_message(chat_id=chat_id, text=f"⚠️ Không có dữ liệu trong {hours}h gần đây.")
            return

        graph_path = _grapher.generate(data, hours, config)
        if graph_path is None:
            await bot.send_message(chat_id=chat_id, text="❌ Không thể tạo biểu đồ.")
            return

        try:
            with open(graph_path, "rb") as f:
                await bot.send_photo(chat_id=chat_id, photo=f, caption=f"📈 System metrics — last {hours}h")
        finally:
            try:
                graph_path.unlink()
            except OSError:
                pass

    except Exception as exc:
        logger.error("send_graph error: %s", exc, exc_info=True)
        try:
            await bot.send_message(chat_id=chat_id, text="❌ Đã xảy ra lỗi khi gửi biểu đồ.")
        except Exception:
            pass
