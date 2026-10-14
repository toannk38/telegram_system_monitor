"""
Keyboards.

- Main menu: inline buttons inside a chat message (one per feature the user may use).
  Pressing one turns that same message into the chosen screen.
- Bottom reply keyboard: everyday shortcuts (status, 12h graph) + the main menu.
- Every screen has its own inline buttons plus "☰ Menu" to go back.

Callback data format: "c|<view>|<arg>".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

if TYPE_CHECKING:
    from bot.handlers import BotDeps

PUBLIC, USER, ADMIN = "public", "user", "admin"
CALLBACK_PREFIX = "c"
MAX_CALLBACK_BYTES = 64  # Telegram limit for callback_data
DEFAULT_GRAPH_HOURS = 12
GRAPH_HOURS = ("1", "6", "12", "24", "48")
REPORT_HOURS = ("6", "12", "24", "48")
MUTE_DURATIONS = ("30m", "1h", "2h", "6h", "12h", "1d")


@dataclass(frozen=True)
class Action:
    view: str
    label: str
    level: str
    feature: Optional[str] = None  # "docker" / "services" / "endpoints" → only when enabled


ACTIONS: List[Action] = [
    Action("status", "📊 Trạng thái", USER),
    Action("graph", "📈 Biểu đồ", USER),
    Action("top", "🔥 Top process", USER),
    Action("alerts", "🚨 Cảnh báo", USER),
    Action("report", "📋 Báo cáo", USER),
    Action("docker", "🐳 Docker", USER, feature="docker"),
    Action("services", "⚙️ Services", USER, feature="services"),
    Action("endpoints", "🌐 Endpoints", USER, feature="endpoints"),
    Action("ping", "🏓 Ping", USER),
    Action("mute", "🔕 Tắt/bật cảnh báo", ADMIN),
    Action("listusers", "👥 Users", ADMIN),
    Action("id", "🆔 ID của tôi", PUBLIC),
    Action("help", "❓ Trợ giúp", PUBLIC),
]

# Bottom keyboard: label → view. Only the everyday shortcuts.
REPLY_SHORTCUTS: Dict[str, str] = {
    "📊 Trạng thái": "status",
    "📈 Biểu đồ": "graph",
    "☰ Menu": "menu",
}


def allowed(deps: "BotDeps", user_id: str, level: str, feature: Optional[str] = None) -> bool:
    pm, checks = deps.permission_manager, deps.checks
    if level == USER and not pm.is_authorized(user_id):
        return False
    if level == ADMIN and not pm.is_admin(user_id):
        return False
    enabled = {
        "docker": checks.docker is not None,
        "services": checks.services_enabled,
        "endpoints": bool(checks.endpoints),
    }
    return feature is None or enabled[feature]


# ── Callback data ─────────────────────────────────────────────────────────────

def cb(view: str, arg: str = "") -> str:
    return f"{CALLBACK_PREFIX}|{view}|{arg}"


def parse_cb(data: str) -> Optional[tuple[str, str]]:
    parts = data.split("|", 2)
    if len(parts) != 3 or parts[0] != CALLBACK_PREFIX:
        return None
    return parts[1], parts[2]


def button(text: str, view: str, arg: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=cb(view, arg))


def _rows(buttons: Sequence[InlineKeyboardButton], per_row: int) -> List[List[InlineKeyboardButton]]:
    return [list(buttons[i:i + per_row]) for i in range(0, len(buttons), per_row)]


def _with_nav(rows: List[List[InlineKeyboardButton]], *extra: InlineKeyboardButton) -> InlineKeyboardMarkup:
    """Append the navigation row: extra buttons (refresh/back) + ☰ Menu."""
    return InlineKeyboardMarkup(rows + [[*extra, button("☰ Menu", "menu")]])


def _choice(label: str, current: bool) -> str:
    return f"• {label}" if current else label


# ── Main menus ────────────────────────────────────────────────────────────────

def main_menu(deps: "BotDeps", user_id: str) -> InlineKeyboardMarkup:
    buttons = [
        button(a.label, a.view)
        for a in ACTIONS
        if allowed(deps, user_id, a.level, a.feature)
    ]
    return InlineKeyboardMarkup(_rows(buttons, 2))


def reply_keyboard(deps: "BotDeps", user_id: str):
    """Bottom shortcuts for authorized users; removed for everyone else."""
    if not deps.permission_manager.is_authorized(user_id):
        return ReplyKeyboardRemove()
    return ReplyKeyboardMarkup(
        [[KeyboardButton(label) for label in REPLY_SHORTCUTS]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Chọn nhanh hoặc gõ /menu",
    )


# ── Per-screen keyboards ──────────────────────────────────────────────────────

def back_only(*extra: InlineKeyboardButton) -> InlineKeyboardMarkup:
    return _with_nav([], *extra)


def refresh(view: str, arg: str = "") -> InlineKeyboardButton:
    return button("🔄 Làm mới", view, arg)


def status_keyboard() -> InlineKeyboardMarkup:
    return _with_nav(
        [
            [refresh("status"), button(f"📈 Biểu đồ {DEFAULT_GRAPH_HOURS}h", "graph", str(DEFAULT_GRAPH_HOURS))],
            [button("🔥 Top CPU", "top", "cpu"), button("💾 Top RAM", "top", "mem")],
        ]
    )


def graph_keyboard(hours: int) -> InlineKeyboardMarkup:
    return _with_nav(
        [[button(_choice(f"{h}h", int(h) == hours), "graph", h) for h in GRAPH_HOURS]],
        refresh("graph", str(hours)),
        button("📊 Trạng thái", "status"),
    )


def report_keyboard(hours: int) -> InlineKeyboardMarkup:
    return _with_nav(
        [[button(_choice(f"{h}h", int(h) == hours), "report", h) for h in REPORT_HOURS]],
        refresh("report", str(hours)),
    )


def top_keyboard(sort: str) -> InlineKeyboardMarkup:
    by_mem = sort in ("mem", "memory", "ram")
    return _with_nav(
        [[button(_choice("🖥 CPU", not by_mem), "top", "cpu"), button(_choice("💾 RAM", by_mem), "top", "mem")]],
        refresh("top", "mem" if by_mem else "cpu"),
    )


def alerts_keyboard(show_unmute: bool) -> InlineKeyboardMarkup:
    extra = [button("🔔 Bật lại", "unmute")] if show_unmute else []
    return _with_nav([], refresh("alerts"), *extra)


def mute_keyboard(muted: bool) -> InlineKeyboardMarkup:
    rows = _rows([button(d, "mute", d) for d in MUTE_DURATIONS], 3)
    if muted:
        rows.append([button("🔔 Bật lại cảnh báo", "unmute")])
    return _with_nav(rows)


def docker_keyboard(mode: str, is_admin: bool) -> InlineKeyboardMarkup:
    modes = [("🗂 Nhóm", "group"), ("⚡ CPU", "cpu"), ("💾 RAM", "mem")]
    rows = [[button(_choice(label, m == mode), "docker", m) for label, m in modes]]
    extra = [refresh("docker", mode)]
    if is_admin:
        extra.append(button("📜 Log", "dlogs_menu"))
    return _with_nav(rows, *extra)


def _short(name: str, width: int = 22) -> str:
    """Keep the tail — it carries the distinctive part (…attachment-worker)."""
    return name if len(name) <= width else "…" + name[-(width - 1):]


def container_log_keyboard(names: List[str], limit: int = 20) -> InlineKeyboardMarkup:
    """One 'logs' button per container; skips names too long for callback_data."""
    buttons = [
        button(f"📜 {_short(n)}", "dlogs", n)
        for n in names[:limit]
        if len(cb("dlogs", n).encode()) <= MAX_CALLBACK_BYTES
    ]
    return _with_nav(_rows(buttons, 2), button("⬅️ Docker", "docker"))


def dlogs_keyboard(name: str) -> InlineKeyboardMarkup:
    extra = []
    if len(cb("dlogs", name).encode()) <= MAX_CALLBACK_BYTES:
        extra.append(refresh("dlogs", name))
    extra.append(button("⬅️ Danh sách", "dlogs_menu"))
    return _with_nav([], *extra)
