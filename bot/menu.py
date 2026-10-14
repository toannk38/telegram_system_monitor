"""
Button menus.

- A persistent reply keyboard (bottom of the chat) with one button per action the
  user is allowed to use — pressing it sends its label as text, routed by `ACTIONS`.
- Inline keyboards for picking arguments (graph hours, top sort, mute duration...).
  Callback data format: "c|<command>|<arg>" runs a command handler with that arg.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

if TYPE_CHECKING:
    from bot.handlers import BotDeps

PUBLIC, USER, ADMIN = "public", "user", "admin"
CALLBACK_PREFIX = "c"
MAX_CALLBACK_BYTES = 64  # Telegram limit for callback_data


@dataclass(frozen=True)
class Action:
    key: str  # handler name, or "menu:<name>" for an inline sub-menu
    label: str
    level: str
    feature: Optional[str] = None  # "docker" / "services" / "endpoints" → shown only when enabled


ACTIONS: List[Action] = [
    Action("status", "📊 Trạng thái", USER),
    Action("menu:graph", "📈 Biểu đồ", USER),
    Action("menu:top", "🔥 Top process", USER),
    Action("alerts", "🚨 Cảnh báo", USER),
    Action("menu:report", "📋 Báo cáo", USER),
    Action("docker", "🐳 Docker", USER, feature="docker"),
    Action("services", "⚙️ Services", USER, feature="services"),
    Action("endpoints", "🌐 Endpoints", USER, feature="endpoints"),
    Action("ping", "🏓 Ping", USER),
    Action("menu:mute", "🔕 Tắt/bật cảnh báo", ADMIN),
    Action("listusers", "👥 Users", ADMIN),
    Action("id", "🆔 ID của tôi", PUBLIC),
    Action("help", "❓ Trợ giúp", PUBLIC),
]
ACTIONS_BY_LABEL: Dict[str, Action] = {a.label: a for a in ACTIONS}


def _allowed(deps: "BotDeps", user_id: str, action: Action) -> bool:
    pm, checks = deps.permission_manager, deps.checks
    if action.level == USER and not pm.is_authorized(user_id):
        return False
    if action.level == ADMIN and not pm.is_admin(user_id):
        return False
    enabled = {
        "docker": checks.docker is not None,
        "services": checks.services_enabled,
        "endpoints": bool(checks.endpoints),
    }
    return action.feature is None or enabled[action.feature]


def main_keyboard(deps: "BotDeps", user_id: str) -> ReplyKeyboardMarkup:
    """Persistent bottom keyboard with the actions this user may use, two per row."""
    labels = [a.label for a in ACTIONS if _allowed(deps, user_id, a)]
    rows = [[KeyboardButton(x) for x in labels[i:i + 2]] for i in range(0, len(labels), 2)]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True,
                               input_field_placeholder="Chọn chức năng hoặc gõ /lệnh")


# ── Inline keyboards ──────────────────────────────────────────────────────────

def cb(command: str, arg: str = "") -> str:
    return f"{CALLBACK_PREFIX}|{command}|{arg}"


def parse_cb(data: str) -> Optional[tuple[str, str]]:
    parts = data.split("|", 2)
    if len(parts) != 3 or parts[0] != CALLBACK_PREFIX:
        return None
    return parts[1], parts[2]


def _button(text: str, command: str, arg: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=cb(command, arg))


def graph_menu() -> InlineKeyboardMarkup:
    hours = ["1", "6", "12", "24", "48"]
    return InlineKeyboardMarkup([[_button(f"{h}h", "graph", h) for h in hours]])


def report_menu() -> InlineKeyboardMarkup:
    hours = ["6", "12", "24", "48"]
    return InlineKeyboardMarkup([[_button(f"{h}h", "report", h) for h in hours]])


def top_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_button("🖥 Theo CPU", "top", "cpu"), _button("💾 Theo RAM", "top", "mem")]])


def mute_menu(muted: bool) -> InlineKeyboardMarkup:
    rows = [
        [_button(d, "mute", d) for d in ("30m", "1h", "2h")],
        [_button(d, "mute", d) for d in ("6h", "12h", "1d")],
    ]
    if muted:
        rows.append([_button("🔔 Bật lại cảnh báo", "unmute")])
    return InlineKeyboardMarkup(rows)


def status_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_button("🔄 Làm mới", "status", "refresh"), _button("📈 Biểu đồ 24h", "graph", "24")],
        [_button("🔥 Top CPU", "top", "cpu"), _button("💾 Top RAM", "top", "mem")],
    ])


def docker_buttons(mode: str, is_admin: bool) -> InlineKeyboardMarkup:
    modes = [("🗂 Nhóm", "group"), ("⚡ CPU", "cpu"), ("💾 RAM", "mem")]
    rows = [
        [_button(("• " if m == mode else "") + label, "docker", m) for label, m in modes],
        [_button("🔄 Làm mới", "docker", mode)],
    ]
    if is_admin:
        rows[1].append(_button("📜 Xem log", "dlogs_menu"))
    return InlineKeyboardMarkup(rows)


def unmute_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_button("🔔 Bật lại cảnh báo", "unmute")]])


def _short(name: str, width: int = 22) -> str:
    """Keep both ends so similar names (app-worker-1 / app-worker-2) stay distinguishable."""
    if len(name) <= width:
        return name
    head = (width - 1) // 2
    return name[:head] + "…" + name[-(width - 1 - head):]


def container_log_buttons(names: List[str], limit: int = 12) -> Optional[InlineKeyboardMarkup]:
    """One 'logs' button per container (admins); skips names too long for callback_data."""
    buttons = [
        _button(f"📜 {_short(n)}", "dlogs", n)
        for n in names[:limit]
        if len(cb("dlogs", n).encode()) <= MAX_CALLBACK_BYTES
    ]
    if not buttons:
        return None
    return InlineKeyboardMarkup([buttons[i:i + 2] for i in range(0, len(buttons), 2)])


SUBMENUS = {
    "graph": ("📈 Chọn khoảng thời gian cho biểu đồ:", graph_menu),
    "report": ("📋 Chọn khoảng thời gian cho báo cáo:", report_menu),
    "top": ("🔥 Sắp xếp process theo:", top_menu),
}
