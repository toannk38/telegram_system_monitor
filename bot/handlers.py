from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

import psutil
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from bot import menu
from bot.menu import ADMIN, PUBLIC, USER

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
ADMIN_ONLY = "❌ Lệnh này chỉ dành cho Admin."
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

async def _send(update: Update, text: str, **kw):
    """Send to the current chat — works for commands, menu text and button callbacks."""
    return await update.effective_chat.send_message(text, **kw)


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


def _strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def _uptime() -> str:
    boot = psutil.boot_time()
    elapsed = time.time() - boot
    days = int(elapsed // 86400)
    hours = int((elapsed % 86400) // 3600)
    minutes = int((elapsed % 3600) // 60)
    return f"{days}d {hours}h {minutes}m"


def _fmt(value: Optional[float], unit: str = "", digits: int = 1) -> str:
    return "N/A" if value is None else f"{value:.{digits}f}{unit}"


def parse_duration(text: str) -> Optional[timedelta]:
    """'30m', '2h', '1d', '90' (minutes) → timedelta; None if invalid."""
    m = re.fullmatch(r"(\d+)\s*([mhd]?)", text.strip().lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2) or "m"
    delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]
    return delta if delta.total_seconds() > 0 else None


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
        lines.append(f"\n🔴 {len(active)} cảnh báo đang mở — xem /alerts")
    if deps.notifier.is_muted():
        lines.append(f"🔕 Cảnh báo tạm tắt đến {deps.notifier.muted_until:%H:%M %d/%m}")
    return "\n".join(lines)


def _help_lines(deps: BotDeps, user_id: str) -> list[str]:
    pm, checks = deps.permission_manager, deps.checks
    lines = [
        "📋 <b>Danh sách lệnh:</b>\n",
        "/start — Chào mừng",
        "/id — Xem Telegram ID",
        "/help — Trợ giúp",
        "/menu — Hiện bàn phím chức năng",
    ]
    if pm.is_authorized(user_id):
        lines += [
            "",
            "/status — Trạng thái hệ thống",
            "/graph [hours] — Biểu đồ (1-48h)",
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

def make_handlers(deps: BotDeps):
    """Return a dict of handler coroutines bound to the given dependencies."""
    pm, config = deps.permission_manager, deps.config

    def command(level: str):
        """Decorator: permission check + uniform error handling."""
        def wrap(fn):
            async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
                try:
                    user_id = str(update.effective_user.id)
                    if level == USER and not pm.is_authorized(user_id):
                        await _send(update, NOT_AUTHORIZED)
                        return
                    if level == ADMIN and not pm.is_admin(user_id):
                        await _send(update, ADMIN_ONLY)
                        return
                    await fn(update, context)
                except Exception as exc:
                    logger.error("%s error: %s", fn.__name__, exc, exc_info=True)
                    try:
                        await _send(update, "❌ Đã xảy ra lỗi.")
                    except Exception:
                        pass
            handler.__name__ = fn.__name__
            return handler
        return wrap

    async def reply_html(update: Update, text: str, **kw) -> None:
        if len(text) > 4096:
            # Cutting HTML could leave an unclosed tag — fall back to plain text.
            await _send(update, _truncate(_strip_html(text)), **kw)
            return
        await _send(update, text, parse_mode="HTML", **kw)

    # ── Public commands ───────────────────────────────────────────────────────

    @command(PUBLIC)
    async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = str(update.effective_user.id)
        text = "👋 Xin chào! Tôi là System Monitor Bot.\n\n"
        if pm.is_authorized(user_id):
            text += "Chọn chức năng ở bàn phím bên dưới, hoặc gõ /help để xem toàn bộ lệnh."
        else:
            text += (
                f"Bạn chưa được cấp quyền. Gửi ID <code>{user_id}</code> cho admin để được thêm vào."
            )
        await _send(update, text, parse_mode="HTML", reply_markup=menu.main_keyboard(deps, user_id))

    @command(PUBLIC)
    async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = str(update.effective_user.id)
        await _send(update, "📱 Chọn chức năng:", reply_markup=menu.main_keyboard(deps, user_id))

    @command(PUBLIC)
    async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        chat = update.effective_chat
        if chat.type == "private":
            await reply_html(update, f"🆔 Your Telegram ID: <code>{user_id}</code>")
        else:
            await reply_html(update, f"🆔 Group ID: <code>{chat.id}</code>\n👤 Your ID: <code>{user_id}</code>")

    @command(PUBLIC)
    async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        lines = _help_lines(deps, str(update.effective_user.id))
        await reply_html(update, "\n".join(lines), reply_markup=menu.main_keyboard(deps, str(update.effective_user.id)))

    # ── Authorized commands ───────────────────────────────────────────────────

    @command(USER)
    async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = await _status_text(deps)
        query = update.callback_query
        if query and context.args == ["refresh"]:
            try:
                await query.edit_message_text(text, parse_mode="HTML", reply_markup=menu.status_buttons())
            except BadRequest as exc:
                if "not modified" not in str(exc).lower():
                    raise
            return
        await reply_html(update, text, reply_markup=menu.status_buttons())

    @command(USER)
    async def graph_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        hours = 12
        if context.args:
            try:
                hours = int(context.args[0])
            except ValueError:
                await _send(update, "❌ Tham số hours phải là số nguyên.")
                return
        if hours < 1 or hours > 48:
            await _send(update, "❌ Số giờ phải trong khoảng 1–48.")
            return
        from bot.graph_sender import send_graph
        await send_graph(context.bot, str(update.effective_chat.id), hours, deps.csv_logger, config)

    @command(USER)
    async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        start = time.monotonic()
        msg = await _send(update, "🏓 Pong!")
        elapsed_ms = (time.monotonic() - start) * 1000
        await msg.edit_text(f"🏓 Pong! <code>{elapsed_ms:.0f}ms</code>", parse_mode="HTML")

    @command(USER)
    async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        from monitor.processes import format_top
        sort = context.args[0].lower() if context.args else "cpu"
        if sort not in ("cpu", "mem", "memory", "ram"):
            await _send(update, "❌ Cú pháp: /top [cpu|mem]")
            return
        await reply_html(update, await asyncio.to_thread(format_top, sort, 10))

    @command(USER)
    async def alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        active = deps.tracker.active()
        now = datetime.now()
        lines = []
        if deps.notifier.is_muted():
            lines.append(f"🔕 Cảnh báo tạm tắt đến {deps.notifier.muted_until:%H:%M %d/%m}\n")
        if not active:
            lines.append("✅ Không có cảnh báo nào đang mở.")
        else:
            lines.append(f"🔴 <b>{len(active)} cảnh báo đang mở:</b>")
            for key, st in sorted(active.items(), key=lambda kv: kv[1].since):
                info = html.escape(st.detail) if st.detail else (
                    f"hiện tại {_fmt(st.value)} · đỉnh {_fmt(st.peak)}"
                )
                lines.append(f"• <b>{html.escape(key)}</b> — {format_duration(now - st.since)}\n  {info}")
        markup = None
        if deps.notifier.is_muted() and pm.is_admin(str(update.effective_user.id)):
            markup = menu.unmute_button()
        await reply_html(update, "\n".join(lines), reply_markup=markup)

    @command(USER)
    async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        hours = 24
        if context.args:
            try:
                hours = int(context.args[0])
            except ValueError:
                hours = 0
        if not 1 <= hours <= 48:
            await _send(update, "❌ Cú pháp: /report [1-48]")
            return
        rows = await asyncio.to_thread(deps.csv_logger.read_recent, hours)
        text = build_report(rows, hours, config.MONITOR_INTERVAL, list(deps.tracker.active()))
        await reply_html(update, text)
        if rows:
            from bot.graph_sender import send_graph
            await send_graph(context.bot, str(update.effective_chat.id), hours, deps.csv_logger, config)

    @command(USER)
    async def docker_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        from bot.docker_view import MODES, render
        docker = deps.checks.docker
        if docker is None:
            await _send(update, "ℹ️ Giám sát Docker đang tắt (DOCKER_MONITOR=false).")
            return
        mode = context.args[0] if context.args and context.args[0] in MODES else "group"
        try:
            containers = await asyncio.to_thread(docker.list_containers)
        except Exception as exc:
            await reply_html(update, f"❌ Không kết nối được Docker API:\n<code>{html.escape(str(exc))[:500]}</code>")
            return
        running = [c.name for c in containers if c.status == "running"]
        stats = await asyncio.to_thread(docker.stats, running)
        text = render(containers, stats, docker.watching_since, mode)
        markup = menu.docker_buttons(mode, pm.is_admin(str(update.effective_user.id)))

        # Buttons on an existing overview switch view / refresh in place.
        query = update.callback_query
        if query and query.message is not None:
            try:
                await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
            except BadRequest as exc:
                if "not modified" not in str(exc).lower():
                    raise
            return
        await reply_html(update, text, reply_markup=markup)

    @command(ADMIN)
    async def dlogs_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        docker = deps.checks.docker
        if docker is None:
            return
        containers = await asyncio.to_thread(docker.list_containers)
        # Problem containers first, then running ones — most likely to need logs.
        containers.sort(key=lambda c: (c.problem(docker.watching_since) is None, c.status != "running", c.name))
        markup = menu.container_log_buttons([c.name for c in containers], limit=20)
        if markup is None:
            await _send(update, "Không có container nào.")
            return
        await _send(update, "📜 Chọn container để xem 50 dòng log cuối:", reply_markup=markup)

    @command(USER)
    async def services_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        from monitor.services import check_services
        if not deps.checks.services_enabled:
            await _send(update, 
                "ℹ️ Chưa cấu hình SYSTEMD_SERVICES hoặc không có systemctl (chế độ Docker)."
            )
            return
        statuses = await check_services(config.SYSTEMD_SERVICES)
        lines = ["⚙️ <b>Services</b>"]
        for s in statuses:
            icon = "✅" if s.ok else ("❌" if s.failed else "⚠️")
            lines.append(f"{icon} <b>{html.escape(s.name)}</b> — {html.escape(s.summary)}")
        await reply_html(update, "\n".join(lines))

    @command(USER)
    async def endpoints_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        from monitor.endpoints import check_endpoints
        if not deps.checks.endpoints:
            await _send(update, "ℹ️ Chưa cấu hình ENDPOINTS.")
            return
        results = await check_endpoints(deps.checks.endpoints, config.ENDPOINT_TIMEOUT)
        lines = ["🌐 <b>Endpoints</b>"]
        for r in results:
            latency = f" · {r.latency_ms:.0f} ms" if r.latency_ms is not None else ""
            lines.append(
                f"{'✅' if r.ok else '❌'} <b>{html.escape(r.endpoint.name)}</b> — {html.escape(r.detail)}{latency}\n"
                f"   <code>{html.escape(r.endpoint.target)}</code>"
            )
        await reply_html(update, "\n".join(lines))

    # ── Admin commands ────────────────────────────────────────────────────────

    @command(ADMIN)
    async def bash_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await _send(update, "❌ Cú pháp: /bash <command>")
            return

        cmd = " ".join(context.args)
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            await _send(update, "⏱ Lệnh bị timeout sau 30 giây.")
            return

        out = stdout.decode(errors="replace")
        err = stderr.decode(errors="replace")
        combined = f"$ {cmd}\n\n"
        if out:
            combined += f"stdout:\n{out}\n"
        if err:
            combined += f"stderr:\n{err}\n"
        combined += f"exit code: {proc.returncode}"
        await reply_html(update, _pre_block(combined))

    @command(ADMIN)
    async def mute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        duration = parse_duration(context.args[0]) if context.args else timedelta(hours=1)
        if duration is None:
            await _send(update, "❌ Cú pháp: /mute [30m|2h|1d] (mặc định 1h)")
            return
        duration = min(duration, MAX_MUTE)
        until = deps.notifier.mute(duration)
        await reply_html(
            update,
            f"🔕 Tạm tắt cảnh báo {format_duration(duration)}, đến <b>{until:%H:%M %d/%m}</b>.\n"
            "Trạng thái vẫn được theo dõi — xem /alerts. Dùng /unmute để bật lại.",
        )

    @command(ADMIN)
    async def mute_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        muted = deps.notifier.is_muted()
        text = "🔕 Tạm tắt cảnh báo trong:"
        if muted:
            text = f"🔕 Đang tắt cảnh báo đến <b>{deps.notifier.muted_until:%H:%M %d/%m}</b>.\nGia hạn hoặc bật lại:"
        await _send(update, text, parse_mode="HTML", reply_markup=menu.mute_menu(muted))

    @command(ADMIN)
    async def unmute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        deps.notifier.unmute()
        active = deps.tracker.active()
        suffix = f"\n🔴 Đang có {len(active)} cảnh báo mở — xem /alerts" if active else ""
        await _send(update, "🔔 Đã bật lại cảnh báo." + suffix)

    @command(ADMIN)
    async def dlogs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        docker = deps.checks.docker
        if docker is None:
            await _send(update, "ℹ️ Giám sát Docker đang tắt (DOCKER_MONITOR=false).")
            return
        if not context.args:
            await _send(update, "❌ Cú pháp: /dlogs <container> [số dòng, tối đa 200]")
            return
        name = context.args[0]
        try:
            lines = max(1, min(200, int(context.args[1]))) if len(context.args) > 1 else 50
        except ValueError:
            lines = 50
        try:
            text = await asyncio.to_thread(docker.logs, name, lines)
        except Exception as exc:
            await reply_html(update, f"❌ <code>{html.escape(str(exc))[:500]}</code>")
            return
        # Keep the tail when truncating — the latest lines matter most.
        header = f"📜 <b>{html.escape(name)}</b> (tail {lines})\n"
        await reply_html(update, header + _pre_block(text or "(trống)", keep="tail"))

    def _id_admin(syntax: str, action, done: str):
        @command(ADMIN)
        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not context.args:
                await _send(update, f"❌ Cú pháp: {syntax}")
                return
            target = context.args[0]
            action(target)
            await reply_html(update, done.format(f"<code>{html.escape(target)}</code>"))
        return handler

    allow_command = _id_admin("/allow <user_id>", pm.add_allowed_user, "✅ Đã thêm user {} vào danh sách.")
    unallow_command = _id_admin("/unallow <user_id>", pm.remove_allowed_user, "✅ Đã xóa user {} khỏi danh sách.")
    addalert_command = _id_admin("/addalert <user_id>", pm.add_alert_recipient, "✅ Đã thêm {} vào danh sách alert.")
    removealert_command = _id_admin(
        "/removealert <user_id>", pm.remove_alert_recipient, "✅ Đã xóa {} khỏi danh sách alert."
    )

    @command(ADMIN)
    async def listusers_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        allowed = pm.get_allowed_users()
        alerts = pm.get_alert_recipients()
        text = (
            f"👥 <b>Authorized users:</b>\n{', '.join(allowed) or 'Trống'}\n\n"
            f"🔔 <b>Alert recipients:</b>\n{', '.join(alerts) or 'Trống'}"
        )
        await reply_html(update, text)

    # ── Button routing ────────────────────────────────────────────────────────

    async def show_submenu(update: Update, name: str) -> None:
        user_id = str(update.effective_user.id)
        if not pm.is_authorized(user_id):
            await _send(update, NOT_AUTHORIZED)
            return
        title, build = menu.SUBMENUS[name]
        await _send(update, title, reply_markup=build())

    async def menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Reply-keyboard button pressed: its label arrives as a plain text message."""
        action = menu.ACTIONS_BY_LABEL.get((update.message.text or "").strip())
        if action is None:
            if update.effective_chat.type == "private":
                await _send(update, "🤔 Không hiểu yêu cầu. Chọn chức năng ở bàn phím bên dưới hoặc gõ /help.",
                            reply_markup=menu.main_keyboard(deps, str(update.effective_user.id)))
            return
        context.args = []
        if action.key == "menu:mute":
            await handlers["mute_menu"](update, context)
        elif action.key.startswith("menu:"):
            await show_submenu(update, action.key.split(":", 1)[1])
        else:
            await handlers[action.key](update, context)

    async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Inline button pressed: callback_data "c|<command>|<arg>"."""
        query = update.callback_query
        parsed = menu.parse_cb(query.data or "")
        if parsed is None or parsed[0] not in handlers:
            await query.answer("Nút này đã hết hạn — gõ /menu để mở lại.", show_alert=False)
            return
        command_name, arg = parsed
        await query.answer("⏳ Đang xử lý..." if command_name in ("graph", "report", "top", "docker", "dlogs_menu") else None)
        context.args = [arg] if arg else []
        await handlers[command_name](update, context)

    handlers = {
        "start": start_command,
        "menu": menu_command,
        "id": id_command,
        "help": help_command,
        "status": status_command,
        "graph": graph_command,
        "ping": ping_command,
        "top": top_command,
        "alerts": alerts_command,
        "report": report_command,
        "docker": docker_command,
        "services": services_command,
        "endpoints": endpoints_command,
        "bash": bash_command,
        "mute": mute_command,
        "unmute": unmute_command,
        "mute_menu": mute_menu,
        "dlogs_menu": dlogs_menu,
        "dlogs": dlogs_command,
        "allow": allow_command,
        "unallow": unallow_command,
        "addalert": addalert_command,
        "removealert": removealert_command,
        "listusers": listusers_command,
    }
    # mute_menu is only reachable from the keyboard; callback/menu_text are not commands.
    commands = {k: v for k, v in handlers.items() if k not in ("mute_menu", "dlogs_menu")}
    return {"commands": commands, "callback": button_callback, "menu_text": menu_text}
