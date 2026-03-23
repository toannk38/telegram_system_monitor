from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import psutil
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

if TYPE_CHECKING:
    from monitor.config import Config
    from monitor.storage import CSVLogger
    from bot.permissions import PermissionManager

logger = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Status", callback_data="btn_status"),
            InlineKeyboardButton("📈 Graph 24h", callback_data="btn_graph_24"),
            InlineKeyboardButton("❓ Help", callback_data="btn_help"),
        ]
    ])


def _truncate(text: str, limit: int = 4096) -> str:
    if len(text) <= limit:
        return text
    return text[:limit - 20] + "\n...[output truncated]"


def _uptime() -> str:
    boot = psutil.boot_time()
    elapsed = time.time() - boot
    days = int(elapsed // 86400)
    hours = int((elapsed % 86400) // 3600)
    minutes = int((elapsed % 3600) // 60)
    return f"{days}d {hours}h {minutes}m"


# ── Factory ───────────────────────────────────────────────────────────────────

def make_handlers(permission_manager: "PermissionManager", csv_logger: "CSVLogger", config: "Config"):
    """Return a dict of handler coroutines bound to the given dependencies."""

    # ── Public commands ───────────────────────────────────────────────────────

    async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            text = (
                "👋 Xin chào! Tôi là System Monitor Bot.\n\n"
                "Dùng /help để xem danh sách lệnh.\n"
                "Dùng /id để lấy Telegram ID của bạn."
            )
            await update.message.reply_text(text, reply_markup=_inline_keyboard())
        except Exception as exc:
            logger.error("start_command error: %s", exc, exc_info=True)
            await update.message.reply_text("❌ Đã xảy ra lỗi.")

    async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            user_id = update.effective_user.id
            chat = update.effective_chat
            if chat.type == "private":
                await update.message.reply_text(f"🆔 Your Telegram ID: `{user_id}`", parse_mode="Markdown")
            else:
                await update.message.reply_text(
                    f"🆔 Group ID: `{chat.id}`\n👤 Your ID: `{user_id}`", parse_mode="Markdown"
                )
        except Exception as exc:
            logger.error("id_command error: %s", exc, exc_info=True)
            await update.message.reply_text("❌ Đã xảy ra lỗi.")

    async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            user_id = str(update.effective_user.id)
            lines = [
                "📋 *Danh sách lệnh:*\n",
                "/start — Chào mừng",
                "/id — Xem Telegram ID",
                "/help — Trợ giúp",
            ]
            if permission_manager.is_authorized(user_id):
                lines += ["/status — Trạng thái hệ thống", "/graph [hours] — Biểu đồ (1-48h)", "/ping — Kiểm tra độ trễ"]
            if permission_manager.is_admin(user_id):
                lines += [
                    "/bash <cmd> — Chạy lệnh bash",
                    "/allow <id> — Thêm user",
                    "/unallow <id> — Xóa user",
                    "/addalert <id> — Thêm alert recipient",
                    "/removealert <id> — Xóa alert recipient",
                    "/listusers — Danh sách users",
                ]
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=_inline_keyboard())
        except Exception as exc:
            logger.error("help_command error: %s", exc, exc_info=True)
            await update.message.reply_text("❌ Đã xảy ra lỗi.")

    # ── Authorized commands ───────────────────────────────────────────────────

    async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            user_id = str(update.effective_user.id)
            if not permission_manager.is_authorized(user_id):
                await update.message.reply_text("❌ Bạn chưa được cấp quyền. Dùng /id và liên hệ admin.")
                return

            cpu = psutil.cpu_percent(interval=1)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("/")

            from monitor.collector import MetricsCollector
            temp_str = "N/A"
            try:
                temp = MetricsCollector()._get_cpu_temperature()
                if temp is not None:
                    temp_str = f"{temp:.1f}°C"
            except Exception:
                pass

            text = (
                f"📊 *System Status*\n\n"
                f"🖥 CPU: `{cpu:.1f}%`\n"
                f"💾 RAM: `{mem.percent:.1f}%` ({mem.used // 1024**2}/{mem.total // 1024**2} MB)\n"
                f"💿 Disk: `{disk.percent:.1f}%` ({disk.used // 1024**3}/{disk.total // 1024**3} GB)\n"
                f"🌡 Temp: `{temp_str}`\n"
                f"⏱ Uptime: `{_uptime()}`"
            )
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception as exc:
            logger.error("status_command error: %s", exc, exc_info=True)
            await update.message.reply_text("❌ Đã xảy ra lỗi.")

    async def graph_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            user_id = str(update.effective_user.id)
            if not permission_manager.is_authorized(user_id):
                await update.message.reply_text("❌ Bạn chưa được cấp quyền. Dùng /id và liên hệ admin.")
                return

            hours = 12
            if context.args:
                try:
                    hours = int(context.args[0])
                except ValueError:
                    await update.message.reply_text("❌ Tham số hours phải là số nguyên.")
                    return

            if hours < 1 or hours > 48:
                await update.message.reply_text("❌ Số giờ phải trong khoảng 1–48.")
                return

            from bot.graph_sender import send_graph
            await send_graph(context.bot, str(update.effective_chat.id), hours, csv_logger, config)
        except Exception as exc:
            logger.error("graph_command error: %s", exc, exc_info=True)
            await update.message.reply_text("❌ Đã xảy ra lỗi khi tạo biểu đồ.")

    async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            user_id = str(update.effective_user.id)
            if not permission_manager.is_authorized(user_id):
                await update.message.reply_text("❌ Bạn chưa được cấp quyền.")
                return
            start = time.monotonic()
            msg = await update.message.reply_text("🏓 Pong!")
            elapsed_ms = (time.monotonic() - start) * 1000
            await msg.edit_text(f"🏓 Pong! `{elapsed_ms:.0f}ms`", parse_mode="Markdown")
        except Exception as exc:
            logger.error("ping_command error: %s", exc, exc_info=True)
            await update.message.reply_text("❌ Đã xảy ra lỗi.")

    # ── Admin commands ────────────────────────────────────────────────────────

    async def bash_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            user_id = str(update.effective_user.id)
            if not permission_manager.is_admin(user_id):
                await update.message.reply_text("❌ Lệnh này chỉ dành cho Admin.")
                return

            if not context.args:
                await update.message.reply_text("❌ Cú pháp: /bash <command>")
                return

            cmd = " ".join(context.args)
            try:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                await update.message.reply_text("⏱ Lệnh bị timeout sau 30 giây.")
                return

            out = stdout.decode(errors="replace")
            err = stderr.decode(errors="replace")
            combined = f"$ {cmd}\n\n"
            if out:
                combined += f"stdout:\n{out}\n"
            if err:
                combined += f"stderr:\n{err}\n"
            combined += f"exit code: {proc.returncode}"

            await update.message.reply_text(f"```\n{_truncate(combined)}\n```", parse_mode="Markdown")
        except Exception as exc:
            logger.error("bash_command error: %s", exc, exc_info=True)
            await update.message.reply_text("❌ Đã xảy ra lỗi.")

    async def allow_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            user_id = str(update.effective_user.id)
            if not permission_manager.is_admin(user_id):
                await update.message.reply_text("❌ Lệnh này chỉ dành cho Admin.")
                return
            if not context.args:
                await update.message.reply_text("❌ Cú pháp: /allow <user_id>")
                return
            target = context.args[0]
            permission_manager.add_allowed_user(target)
            await update.message.reply_text(f"✅ Đã thêm user `{target}` vào danh sách.", parse_mode="Markdown")
        except Exception as exc:
            logger.error("allow_command error: %s", exc, exc_info=True)
            await update.message.reply_text("❌ Đã xảy ra lỗi.")

    async def unallow_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            user_id = str(update.effective_user.id)
            if not permission_manager.is_admin(user_id):
                await update.message.reply_text("❌ Lệnh này chỉ dành cho Admin.")
                return
            if not context.args:
                await update.message.reply_text("❌ Cú pháp: /unallow <user_id>")
                return
            target = context.args[0]
            permission_manager.remove_allowed_user(target)
            await update.message.reply_text(f"✅ Đã xóa user `{target}` khỏi danh sách.", parse_mode="Markdown")
        except Exception as exc:
            logger.error("unallow_command error: %s", exc, exc_info=True)
            await update.message.reply_text("❌ Đã xảy ra lỗi.")

    async def addalert_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            user_id = str(update.effective_user.id)
            if not permission_manager.is_admin(user_id):
                await update.message.reply_text("❌ Lệnh này chỉ dành cho Admin.")
                return
            if not context.args:
                await update.message.reply_text("❌ Cú pháp: /addalert <user_id>")
                return
            target = context.args[0]
            permission_manager.add_alert_recipient(target)
            await update.message.reply_text(f"✅ Đã thêm `{target}` vào danh sách alert.", parse_mode="Markdown")
        except Exception as exc:
            logger.error("addalert_command error: %s", exc, exc_info=True)
            await update.message.reply_text("❌ Đã xảy ra lỗi.")

    async def removealert_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            user_id = str(update.effective_user.id)
            if not permission_manager.is_admin(user_id):
                await update.message.reply_text("❌ Lệnh này chỉ dành cho Admin.")
                return
            if not context.args:
                await update.message.reply_text("❌ Cú pháp: /removealert <user_id>")
                return
            target = context.args[0]
            permission_manager.remove_alert_recipient(target)
            await update.message.reply_text(f"✅ Đã xóa `{target}` khỏi danh sách alert.", parse_mode="Markdown")
        except Exception as exc:
            logger.error("removealert_command error: %s", exc, exc_info=True)
            await update.message.reply_text("❌ Đã xảy ra lỗi.")

    async def listusers_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            user_id = str(update.effective_user.id)
            if not permission_manager.is_admin(user_id):
                await update.message.reply_text("❌ Lệnh này chỉ dành cho Admin.")
                return
            allowed = permission_manager.get_allowed_users()
            alerts = permission_manager.get_alert_recipients()
            text = (
                f"👥 *Authorized users:*\n{', '.join(allowed) or 'Trống'}\n\n"
                f"🔔 *Alert recipients:*\n{', '.join(alerts) or 'Trống'}"
            )
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception as exc:
            logger.error("listusers_command error: %s", exc, exc_info=True)
            await update.message.reply_text("❌ Đã xảy ra lỗi.")

    # ── Callback handler ──────────────────────────────────────────────────────

    async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            query = update.callback_query
            await query.answer()
            user_id = str(query.from_user.id)
            data = query.data

            if data == "btn_help":
                lines = [
                    "📋 *Danh sách lệnh:*\n",
                    "/start — Chào mừng",
                    "/id — Xem Telegram ID",
                    "/help — Trợ giúp",
                ]
                if permission_manager.is_authorized(user_id):
                    lines += ["/status — Trạng thái hệ thống", "/graph [hours] — Biểu đồ (1-48h)", "/ping — Kiểm tra độ trễ"]
                if permission_manager.is_admin(user_id):
                    lines += ["/bash <cmd> — Chạy lệnh bash", "/listusers — Danh sách users"]
                await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=_inline_keyboard())

            elif data in ("btn_status", "btn_graph_24"):
                if not permission_manager.is_authorized(user_id):
                    await query.edit_message_text(
                        "❌ Bạn chưa được cấp quyền. Dùng /id và liên hệ admin.",
                        reply_markup=_inline_keyboard(),
                    )
                    return

                if data == "btn_status":
                    # Reuse status logic
                    cpu = psutil.cpu_percent(interval=1)
                    mem = psutil.virtual_memory()
                    disk = psutil.disk_usage("/")
                    from monitor.collector import MetricsCollector
                    temp_str = "N/A"
                    try:
                        temp = MetricsCollector()._get_cpu_temperature()
                        if temp is not None:
                            temp_str = f"{temp:.1f}°C"
                    except Exception:
                        pass
                    text = (
                        f"📊 *System Status*\n\n"
                        f"🖥 CPU: `{cpu:.1f}%`\n"
                        f"💾 RAM: `{mem.percent:.1f}%` ({mem.used // 1024**2}/{mem.total // 1024**2} MB)\n"
                        f"💿 Disk: `{disk.percent:.1f}%` ({disk.used // 1024**3}/{disk.total // 1024**3} GB)\n"
                        f"🌡 Temp: `{temp_str}`\n"
                        f"⏱ Uptime: `{_uptime()}`"
                    )
                    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=_inline_keyboard())

                elif data == "btn_graph_24":
                    await query.edit_message_text("📈 Đang tạo biểu đồ 24h...", reply_markup=_inline_keyboard())
                    from bot.graph_sender import send_graph
                    await send_graph(context.bot, str(query.message.chat_id), 24, csv_logger, config)

        except Exception as exc:
            logger.error("button_callback error: %s", exc, exc_info=True)

    return {
        "start": start_command,
        "id": id_command,
        "help": help_command,
        "status": status_command,
        "graph": graph_command,
        "ping": ping_command,
        "bash": bash_command,
        "allow": allow_command,
        "unallow": unallow_command,
        "addalert": addalert_command,
        "removealert": removealert_command,
        "listusers": listusers_command,
        "callback": button_callback,
    }
