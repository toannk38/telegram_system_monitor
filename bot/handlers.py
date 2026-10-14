"""
Telegram handlers.

Every feature is a *view builder* `async (update, args) -> View`. The same builder
serves a typed command (/status), a bottom-keyboard shortcut, and an inline button;
`Panels` decides whether to edit the current message or post a new one.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Awaitable, Callable, Dict, List, Optional

import psutil
from telegram import Update
from telegram.ext import ContextTypes

from bot import menu
from bot.menu import ADMIN, PUBLIC, USER
from bot.panel import Panels, View
from monitor.alerting import format_duration
from monitor.report import build_report, human_bytes

if TYPE_CHECKING:
    from bot.permissions import PermissionManager
    from monitor.alerting import AlertTracker, Notifier
    from monitor.checks import ChecksRunner
    from monitor.collector import MetricsCollector
    from monitor.config import Config
    from monitor.storage import CSVLogger

logger = logging.getLogger(__name__)

NOT_AUTHORIZED = "❌ Bạn chưa được cấp quyền. Dùng /id và liên hệ admin."
ADMIN_ONLY = "❌ Chức năng này chỉ dành cho Admin."
MAX_MUTE = timedelta(days=7)


@dataclass
class BotDeps:
    permission_manager: "PermissionManager"
    csv_logger: "CSVLogger"
    config: "Config"
    collector: "MetricsCollector"
    notifier: "Notifier"
    tracker: "AlertTracker"
    checks: "ChecksRunner"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _truncate(text: str, limit: int = 4096) -> str:
    if len(text) <= limit:
        return text
    return text[:limit - 20] + "\n...[output truncated]"


def _pre_block(text: str, limit: int = 3800, keep: str = "head") -> str:
    """
    Escape `text` into a <pre> block of at most `limit` chars. Truncation happens on
    the raw text (never inside an HTML entity); `keep` chooses head or tail.
    """
    def render(n: int) -> str:
        if n >= len(text):
            part = text
        elif keep == "head":
            part = text[:n] + "\n...[truncated]"
        else:
            part = "[truncated]...\n" + (text[len(text) - n:] if n else "")
        return f"<pre>{html.escape(part, quote=False)}</pre>"

    if len(render(len(text))) <= limit:
        return render(len(text))
    lo, hi = 0, len(text)  # largest n whose rendering fits
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(render(mid)) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return render(lo)


def _uptime() -> str:
    boot = psutil.boot_time()
    elapsed = time.time() - boot
    days = int(elapsed // 86400)
    hours = int((elapsed % 86400) // 3600)
    minutes = int((elapsed % 3600) // 60)
    return f"{days}d {hours}h {minutes}m"


def _fmt(value: Optional[float], unit: str = "", digits: int = 1) -> str:
    return "N/A" if value is None else f"{value:.{digits}f}{unit}"


def _updated() -> str:
    # Shown on refreshable screens: also guarantees an edit is never "not modified".
    return f"\n\n<i>🕒 Cập nhật {datetime.now():%H:%M:%S}</i>"


def parse_duration(text: str) -> Optional[timedelta]:
    """'30m', '2h', '1d', '90' (minutes) → timedelta; None if invalid."""
    m = re.fullmatch(r"(\d+)\s*([mhd]?)", text.strip().lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2) or "m"
    delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]
    return delta if delta.total_seconds() > 0 else None


def _int_arg(args: List[str], default: int, lo: int, hi: int) -> Optional[int]:
    """First arg as int within [lo, hi]; default when absent; None when invalid."""
    if not args:
        return default
    try:
        value = int(args[0])
    except ValueError:
        return None
    return value if lo <= value <= hi else None


async def _status_text(deps: BotDeps) -> str:
    m = await asyncio.to_thread(deps.collector.snapshot)
    last = deps.collector.last_metrics
    cpu_line = f"🖥 CPU: <code>{m.cpu_percent:.1f}%</code>"
    if last is not None:
        cpu_line += f" (TB {deps.config.MONITOR_INTERVAL}s: {last.cpu_percent:.1f}%)"
    lines = [
        "📊 <b>System Status</b>\n",
        cpu_line,
        f"⚖️ Load: <code>{m.load1:.2f} {m.load5:.2f} {m.load15:.2f}</code> ({psutil.cpu_count()} cores)",
        f"💾 RAM: <code>{m.mem_percent:.1f}%</code> ({m.mem_used_mb:.0f}/{m.mem_total_mb:.0f} MB)"
        f" · Swap: {_fmt(m.swap_percent, '%')}",
        f"💿 Disk: <code>{m.disk_percent:.1f}%</code> ({m.disk_used_gb:.0f}/{m.disk_total_gb:.0f} GB)",
    ]
    if m.net_rx_bps is not None:
        lines.append(f"📶 Net: ↓ {human_bytes(m.net_rx_bps, '/s')} · ↑ {human_bytes(m.net_tx_bps, '/s')}")
    if m.disk_read_bps is not None:
        lines.append(f"🔁 Disk IO: R {human_bytes(m.disk_read_bps, '/s')} · W {human_bytes(m.disk_write_bps, '/s')}")
    temp = f"🌡 CPU: <code>{_fmt(m.cpu_temp_c, '°C')}</code>"
    if m.gpu_temp_c is not None:
        temp += f" · GPU: <code>{m.gpu_temp_c:.0f}°C</code>"
    lines.append(temp)
    lines.append(f"⏱ Uptime: <code>{_uptime()}</code>")

    active = deps.tracker.active()
    if active:
        lines.append(f"\n🔴 {len(active)} cảnh báo đang mở")
    if deps.notifier.is_muted():
        lines.append(f"🔕 Cảnh báo tạm tắt đến {deps.notifier.muted_until:%H:%M %d/%m}")
    return "\n".join(lines)


def _help_lines(deps: BotDeps, user_id: str) -> list[str]:
    pm, checks = deps.permission_manager, deps.checks
    lines = [
        "📋 <b>Danh sách lệnh</b>",
        "Bấm nút trong menu, hoặc gõ lệnh:\n",
        "/menu — Mở menu",
        "/id — Xem Telegram ID",
        "/help — Trợ giúp",
    ]
    if pm.is_authorized(user_id):
        lines += [
            "",
            "/status — Trạng thái hệ thống",
            "/graph [hours] — Biểu đồ (1-48h, mặc định 12h)",
            "/top [cpu|mem] — Top process",
            "/alerts — Cảnh báo đang mở",
            "/report [hours] — Báo cáo min/avg/max (mặc định 24h)",
            "/ping — Kiểm tra độ trễ",
        ]
        if checks.docker:
            lines.append("/docker — Trạng thái container")
        if checks.services_enabled:
            lines.append("/services — Trạng thái systemd service")
        if checks.endpoints:
            lines.append("/endpoints — Kiểm tra endpoint")
    if pm.is_admin(user_id):
        lines += [
            "",
            "/bash &lt;cmd&gt; — Chạy lệnh bash",
            "/mute [30m|2h|1d] — Tạm tắt cảnh báo",
            "/unmute — Bật lại cảnh báo",
        ]
        if checks.docker:
            lines.append("/dlogs &lt;container&gt; [n] — Log container")
        lines += [
            "/allow &lt;id&gt; — Thêm user",
            "/unallow &lt;id&gt; — Xóa user",
            "/addalert &lt;id&gt; — Thêm alert recipient",
            "/removealert &lt;id&gt; — Xóa alert recipient",
            "/listusers — Danh sách users",
        ]
    return lines


# ── Factory ───────────────────────────────────────────────────────────────────

ViewFn = Callable[[Update, List[str]], Awaitable[View]]


def make_handlers(deps: BotDeps):
    """
    Returns {"commands": {name: handler}, "callback": handler, "menu_text": handler}.
    """
    pm, config = deps.permission_manager, deps.config
    panels = Panels()
    views: Dict[str, tuple[str, ViewFn]] = {}  # name → (permission level, builder)

    def view(name: str, level: str):
        def register(fn: ViewFn) -> ViewFn:
            views[name] = (level, fn)
            return fn
        return register

    def uid(update: Update) -> str:
        return str(update.effective_user.id)

    # ── Dispatch ──────────────────────────────────────────────────────────────

    async def run_view(name: str, update: Update, args: List[str]) -> None:
        level, fn = views[name]
        query = update.callback_query
        denied = None
        if level == USER and not pm.is_authorized(uid(update)):
            denied = NOT_AUTHORIZED
        elif level == ADMIN and not pm.is_admin(uid(update)):
            denied = ADMIN_ONLY
        if denied:
            if query:
                await query.answer(denied, show_alert=True)
            else:
                await update.effective_chat.send_message(denied)
            return
        if query:
            await query.answer()
        try:
            await panels.show(update, await fn(update, args))
        except Exception as exc:
            logger.error("view %s error: %s", name, exc, exc_info=True)
            try:
                await update.effective_chat.send_message("❌ Đã xảy ra lỗi.")
            except Exception:
                pass

    def command(name: str):
        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await run_view(name, update, list(context.args or []))
        handler.__name__ = f"{name}_command"
        return handler

    async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Inline button: callback_data "c|<view>|<arg>" → that view, editing the same message."""
        query = update.callback_query
        parsed = menu.parse_cb(query.data or "")
        if parsed is None or parsed[0] not in views:
            await query.answer("Nút này đã hết hạn — gõ /menu để mở lại.")
            return
        name, arg = parsed
        await run_view(name, update, [arg] if arg else [])

    async def menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Bottom-keyboard shortcut (arrives as plain text)."""
        name = menu.REPLY_SHORTCUTS.get((update.message.text or "").strip())
        if name is not None:
            await run_view(name, update, [])
        elif update.effective_chat.type == "private":
            await run_view("menu", update, [])

    # ── Navigation ────────────────────────────────────────────────────────────

    @view("menu", PUBLIC)
    async def menu_view(update: Update, args: List[str]) -> View:
        user_id = uid(update)
        if pm.is_authorized(user_id):
            text = "🖥 <b>System Monitor</b>\nChọn chức năng:"
        else:
            text = (
                "👋 Xin chào! Tôi là System Monitor Bot.\n\n"
                f"Bạn chưa được cấp quyền. Gửi ID <code>{user_id}</code> cho admin để được thêm vào."
            )
        return View(text, menu.main_menu(deps, user_id))

    async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Install (or remove) the bottom shortcuts, then show the inline menu.
        user_id = uid(update)
        hint = "⌨️ Lối tắt: 📊 Trạng thái · 📈 Biểu đồ ở bàn phím bên dưới." if pm.is_authorized(user_id) else "👋"
        await update.effective_chat.send_message(hint, reply_markup=menu.reply_keyboard(deps, user_id))
        await run_view("menu", update, [])

    @view("id", PUBLIC)
    async def id_view(update: Update, args: List[str]) -> View:
        user_id = update.effective_user.id
        chat = update.effective_chat
        text = f"🆔 Telegram ID của bạn: <code>{user_id}</code>"
        if chat.type != "private":
            text += f"\n👥 Group ID: <code>{chat.id}</code>"
        return View(text, menu.back_only())

    @view("help", PUBLIC)
    async def help_view(update: Update, args: List[str]) -> View:
        return View("\n".join(_help_lines(deps, uid(update))), menu.back_only())

    # ── Monitoring screens ────────────────────────────────────────────────────

    @view("status", USER)
    async def status_view(update: Update, args: List[str]) -> View:
        return View(await _status_text(deps) + _updated(), menu.status_keyboard())

    @view("graph", USER)
    async def graph_view(update: Update, args: List[str]) -> View:
        hours = _int_arg(args, menu.DEFAULT_GRAPH_HOURS, 1, 48)
        if hours is None:
            return View("❌ Số giờ phải trong khoảng 1–48. Ví dụ: /graph 24", menu.graph_keyboard(0))
        data = await asyncio.to_thread(deps.csv_logger.read_recent, hours)
        if not data:
            return View(f"⚠️ Chưa có dữ liệu trong {hours}h gần đây.", menu.graph_keyboard(hours))
        from monitor.grapher import GraphGenerator
        path = await asyncio.to_thread(GraphGenerator().generate, data, hours, config)
        if path is None:
            return View("❌ Không thể tạo biểu đồ.", menu.graph_keyboard(hours))
        return View(
            f"📈 <b>Biểu đồ {hours}h</b> · {datetime.now():%H:%M %d/%m}",
            menu.graph_keyboard(hours),
            photo=path,
        )

    @view("top", USER)
    async def top_view(update: Update, args: List[str]) -> View:
        from monitor.processes import format_top
        sort = args[0].lower() if args else "cpu"
        if sort not in ("cpu", "mem", "memory", "ram"):
            sort = "cpu"
        text = await asyncio.to_thread(format_top, sort, 10)
        return View("🔥 " + text + _updated(), menu.top_keyboard(sort))

    @view("alerts", USER)
    async def alerts_view(update: Update, args: List[str]) -> View:
        active = deps.tracker.active()
        now = datetime.now()
        lines = []
        muted = deps.notifier.is_muted()
        if muted:
            lines.append(f"🔕 Cảnh báo tạm tắt đến {deps.notifier.muted_until:%H:%M %d/%m}\n")
        if not active:
            lines.append("✅ Không có cảnh báo nào đang mở.")
        else:
            lines.append(f"🚨 <b>{len(active)} cảnh báo đang mở:</b>")
            for key, st in sorted(active.items(), key=lambda kv: kv[1].since):
                info = html.escape(st.detail) if st.detail else (
                    f"hiện tại {_fmt(st.value)} · đỉnh {_fmt(st.peak)}"
                )
                lines.append(f"• <b>{html.escape(key)}</b> — {format_duration(now - st.since)}\n  {info}")
        show_unmute = muted and pm.is_admin(uid(update))
        return View("\n".join(lines) + _updated(), menu.alerts_keyboard(show_unmute))

    @view("report", USER)
    async def report_view(update: Update, args: List[str]) -> View:
        hours = _int_arg(args, 24, 1, 48)
        if hours is None:
            return View("❌ Cú pháp: /report [1-48]", menu.report_keyboard(24))
        rows = await asyncio.to_thread(deps.csv_logger.read_recent, hours)
        text = build_report(rows, hours, config.MONITOR_INTERVAL, list(deps.tracker.active()))
        keyboard = menu.report_keyboard(hours)
        if not rows:
            return View(text, keyboard)
        # Report + graph in one message when the caption fits (Telegram caps captions at 1024).
        from monitor.grapher import GraphGenerator
        path = await asyncio.to_thread(GraphGenerator().generate, rows, hours, config)
        if path is not None and len(text) <= 1024:
            return View(text, keyboard, photo=path)
        if path is not None:
            path.unlink(missing_ok=True)
        return View(text, keyboard)

    @view("docker", USER)
    async def docker_view(update: Update, args: List[str]) -> View:
        from bot.docker_view import MODES, render
        docker = deps.checks.docker
        if docker is None:
            return View("ℹ️ Giám sát Docker đang tắt (DOCKER_MONITOR=false).", menu.back_only())
        mode = args[0] if args and args[0] in MODES else "group"
        try:
            containers = await asyncio.to_thread(docker.list_containers)
        except Exception as exc:
            return View(
                f"❌ Không kết nối được Docker API:\n<code>{html.escape(str(exc))[:500]}</code>",
                menu.back_only(menu.refresh("docker", mode)),
            )
        running = [c.name for c in containers if c.status == "running"]
        stats = await asyncio.to_thread(docker.stats, running)
        text = render(containers, stats, docker.watching_since, mode)
        return View(text + _updated(), menu.docker_keyboard(mode, pm.is_admin(uid(update))))

    @view("dlogs_menu", ADMIN)
    async def dlogs_menu_view(update: Update, args: List[str]) -> View:
        docker = deps.checks.docker
        if docker is None:
            return View("ℹ️ Giám sát Docker đang tắt.", menu.back_only())
        containers = await asyncio.to_thread(docker.list_containers)
        # Problem containers first, then running ones — most likely to need logs.
        containers.sort(key=lambda c: (c.problem(docker.watching_since) is None, c.status != "running", c.name))
        return View("📜 Chọn container để xem log:", menu.container_log_keyboard([c.name for c in containers]))

    @view("dlogs", ADMIN)
    async def dlogs_view(update: Update, args: List[str]) -> View:
        docker = deps.checks.docker
        if docker is None:
            return View("ℹ️ Giám sát Docker đang tắt (DOCKER_MONITOR=false).", menu.back_only())
        if not args:
            return View("❌ Cú pháp: /dlogs &lt;container&gt; [số dòng, tối đa 200]", menu.back_only())
        name = args[0]
        lines = _int_arg(args[1:], 50, 1, 200) or 50
        try:
            text = await asyncio.to_thread(docker.logs, name, lines)
        except Exception as exc:
            return View(f"❌ <code>{html.escape(str(exc))[:500]}</code>", menu.dlogs_keyboard(name))
        # Keep the tail when truncating — the latest lines matter most.
        header = f"📜 <b>{html.escape(name)}</b> (tail {lines})\n"
        return View(header + _pre_block(text or "(trống)", limit=3700, keep="tail"), menu.dlogs_keyboard(name))

    @view("services", USER)
    async def services_view(update: Update, args: List[str]) -> View:
        from monitor.services import check_services
        if not deps.checks.services_enabled:
            return View(
                "ℹ️ Chưa cấu hình SYSTEMD_SERVICES hoặc không có systemctl (chế độ Docker).", menu.back_only()
            )
        statuses = await check_services(config.SYSTEMD_SERVICES)
        lines = ["⚙️ <b>Services</b>"]
        for s in statuses:
            icon = "✅" if s.ok else ("❌" if s.failed else "⚠️")
            lines.append(f"{icon} <b>{html.escape(s.name)}</b> — {html.escape(s.summary)}")
        return View("\n".join(lines) + _updated(), menu.back_only(menu.refresh("services")))

    @view("endpoints", USER)
    async def endpoints_view(update: Update, args: List[str]) -> View:
        from monitor.endpoints import check_endpoints
        if not deps.checks.endpoints:
            return View("ℹ️ Chưa cấu hình ENDPOINTS.", menu.back_only())
        results = await check_endpoints(deps.checks.endpoints, config.ENDPOINT_TIMEOUT)
        lines = ["🌐 <b>Endpoints</b>"]
        for r in results:
            latency = f" · {r.latency_ms:.0f} ms" if r.latency_ms is not None else ""
            lines.append(
                f"{'✅' if r.ok else '❌'} <b>{html.escape(r.endpoint.name)}</b> — {html.escape(r.detail)}{latency}\n"
                f"   <code>{html.escape(r.endpoint.target)}</code>"
            )
        return View("\n".join(lines) + _updated(), menu.back_only(menu.refresh("endpoints")))

    @view("ping", USER)
    async def ping_view(update: Update, args: List[str]) -> View:
        start = time.monotonic()
        await update.get_bot().get_me()
        elapsed_ms = (time.monotonic() - start) * 1000
        return View(f"🏓 Pong! Telegram API: <code>{elapsed_ms:.0f} ms</code>" + _updated(),
                    menu.back_only(menu.refresh("ping")))

    # ── Admin ─────────────────────────────────────────────────────────────────

    def _mute_text() -> str:
        if deps.notifier.is_muted():
            return f"🔕 Cảnh báo đang tắt đến <b>{deps.notifier.muted_until:%H:%M %d/%m}</b>.\nGia hạn hoặc bật lại:"
        return "🔔 Cảnh báo đang bật.\nTạm tắt trong:"

    @view("mute", ADMIN)
    async def mute_view(update: Update, args: List[str]) -> View:
        if args:
            duration = parse_duration(args[0])
            if duration is None:
                return View("❌ Cú pháp: /mute [30m|2h|1d]", menu.mute_keyboard(deps.notifier.is_muted()))
            duration = min(duration, MAX_MUTE)
            deps.notifier.mute(duration)
            text = (
                f"✅ Đã tạm tắt cảnh báo {format_duration(duration)}.\n"
                "Trạng thái vẫn được theo dõi — xem 🚨 Cảnh báo.\n\n" + _mute_text()
            )
            return View(text, menu.mute_keyboard(True))
        return View(_mute_text(), menu.mute_keyboard(deps.notifier.is_muted()))

    @view("unmute", ADMIN)
    async def unmute_view(update: Update, args: List[str]) -> View:
        deps.notifier.unmute()
        active = deps.tracker.active()
        suffix = f"\n🚨 Đang có {len(active)} cảnh báo mở." if active else ""
        return View("✅ Đã bật lại cảnh báo." + suffix + "\n\n" + _mute_text(), menu.mute_keyboard(False))

    @view("listusers", ADMIN)
    async def listusers_view(update: Update, args: List[str]) -> View:
        allowed = pm.get_allowed_users()
        alerts = pm.get_alert_recipients()
        text = (
            f"👥 <b>Authorized users:</b>\n{', '.join(allowed) or 'Trống'}\n\n"
            f"🔔 <b>Alert recipients:</b>\n{', '.join(alerts) or 'Trống'}\n\n"
            "<i>Thêm/xoá bằng /allow, /unallow, /addalert, /removealert &lt;id&gt;</i>"
        )
        return View(text, menu.back_only())

    @view("bash", ADMIN)
    async def bash_view(update: Update, args: List[str]) -> View:
        if not args:
            return View("❌ Cú pháp: /bash &lt;command&gt;", panel=False)
        cmd = " ".join(args)
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            return View("⏱ Lệnh bị timeout sau 30 giây.", panel=False)

        out = stdout.decode(errors="replace")
        err = stderr.decode(errors="replace")
        combined = f"$ {cmd}\n\n"
        if out:
            combined += f"stdout:\n{out}\n"
        if err:
            combined += f"stderr:\n{err}\n"
        combined += f"exit code: {proc.returncode}"
        # Command output is kept in the chat history (not replaced by later panels).
        return View(_pre_block(combined), panel=False)

    def _id_admin(name: str, syntax: str, action, done: str) -> None:
        @view(name, ADMIN)
        async def id_admin_view(update: Update, args: List[str]) -> View:
            if not args:
                return View(f"❌ Cú pháp: {html.escape(syntax)}", panel=False)
            action(args[0])
            return View(done.format(f"<code>{html.escape(args[0])}</code>"), panel=False)

    _id_admin("allow", "/allow <user_id>", pm.add_allowed_user, "✅ Đã thêm user {} vào danh sách.")
    _id_admin("unallow", "/unallow <user_id>", pm.remove_allowed_user, "✅ Đã xóa user {} khỏi danh sách.")
    _id_admin("addalert", "/addalert <user_id>", pm.add_alert_recipient, "✅ Đã thêm {} vào danh sách alert.")
    _id_admin("removealert", "/removealert <user_id>", pm.remove_alert_recipient, "✅ Đã xóa {} khỏi danh sách alert.")

    # ── Registration ──────────────────────────────────────────────────────────

    # Internal screens reachable only by buttons.
    button_only = {"dlogs_menu"}
    commands = {name: command(name) for name in views if name not in button_only}
    commands["start"] = start_command
    return {"commands": commands, "callback": button_callback, "menu_text": menu_text}
