"""
State-based alerting.

Every alert source (metric, container, service, endpoint) is tracked by a key in
`AlertTracker`. A message is sent only on state *transitions*, never per sample:

    OK ──(N consecutive breaches)──▶ ALERT ──(M consecutive clears)──▶ OK
                                       └─ reminders with doubling interval

Numeric metrics use hysteresis: breach above the threshold, clear only below
(threshold - ALERT_HYSTERESIS), so a value hovering at the threshold doesn't flap.
"""
from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable, Dict, List, Optional

from monitor.collector import SystemMetrics
from monitor.grapher import GraphGenerator

if TYPE_CHECKING:
    from monitor.config import Config

logger = logging.getLogger(__name__)


def describe_send_error(chat_id, exc: Exception) -> Optional[str]:
    """
    One-line explanation for expected delivery failures (wrong ID, bot never
    started / blocked). Returns None for unexpected errors, which deserve a traceback.
    """
    from telegram.error import BadRequest, Forbidden

    text = str(exc)
    if isinstance(exc, BadRequest) and "chat not found" in text.lower():
        return f"Chat {chat_id} not found — user must /start the bot or the ID is wrong"
    if isinstance(exc, Forbidden):
        return f"Chat {chat_id} unreachable — {text} (user blocked the bot or the bot was removed from the group)"
    return None


FIRE, REMIND, RECOVER = "fire", "remind", "recover"


def format_duration(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    days, rem = divmod(minutes, 1440)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {mins}m"
    if hours:
        return f"{hours}h {mins:02d}m"
    return f"{mins}m"


# ── Tracker ───────────────────────────────────────────────────────────────────


@dataclass
class AlertState:
    active: bool = False
    breach_count: int = 0
    clear_count: int = 0
    since: Optional[datetime] = None
    peak: Optional[float] = None
    value: Optional[float] = None
    detail: str = ""
    next_remind: Optional[datetime] = None
    remind_interval: Optional[timedelta] = None


@dataclass
class Transition:
    kind: str  # FIRE | REMIND | RECOVER
    key: str
    since: datetime
    now: datetime
    value: Optional[float] = None
    peak: Optional[float] = None
    detail: str = ""

    @property
    def duration(self) -> timedelta:
        return self.now - self.since


class AlertTracker:
    def __init__(
        self,
        consecutive: int,
        recovery_consecutive: int,
        remind_minutes: int,
        remind_max_minutes: int,
    ) -> None:
        self._consecutive = consecutive
        self._recovery = recovery_consecutive
        self._remind = timedelta(minutes=remind_minutes) if remind_minutes > 0 else None
        self._remind_max = timedelta(minutes=max(remind_minutes, remind_max_minutes))
        self._states: Dict[str, AlertState] = {}

    def update(
        self,
        key: str,
        breached: bool,
        cleared: bool,
        value: Optional[float] = None,
        detail: str = "",
        now: Optional[datetime] = None,
    ) -> Optional[Transition]:
        """
        Feed one observation. `breached` and `cleared` may both be False (inside the
        hysteresis band, or no data) — then counters reset and the state is kept.
        """
        now = now or datetime.now()
        st = self._states.setdefault(key, AlertState())
        st.value = value
        if detail:
            st.detail = detail

        if not st.active:
            st.breach_count = st.breach_count + 1 if breached else 0
            if st.breach_count < self._consecutive:
                return None
            st.active = True
            st.breach_count = 0
            st.clear_count = 0
            st.since = now
            st.peak = value
            st.remind_interval = self._remind
            st.next_remind = now + self._remind if self._remind else None
            return Transition(FIRE, key, now, now, value, value, st.detail)

        if value is not None and (st.peak is None or value > st.peak):
            st.peak = value

        st.clear_count = st.clear_count + 1 if cleared else 0
        if st.clear_count >= self._recovery:
            transition = Transition(RECOVER, key, st.since, now, value, st.peak, st.detail)
            self._states[key] = AlertState()
            return transition

        if breached and st.next_remind is not None and now >= st.next_remind:
            st.remind_interval = min(st.remind_interval * 2, self._remind_max)
            st.next_remind = now + st.remind_interval
            return Transition(REMIND, key, st.since, now, value, st.peak, st.detail)
        return None

    def active(self, prefix: str = "") -> Dict[str, AlertState]:
        return {k: s for k, s in self._states.items() if s.active and k.startswith(prefix)}

    def is_active(self, key: str) -> bool:
        st = self._states.get(key)
        return bool(st and st.active)

    def keys(self, prefix: str = "") -> List[str]:
        return [k for k in self._states if k.startswith(prefix)]

    def forget(self, key: str) -> None:
        self._states.pop(key, None)


# ── Metric rules ──────────────────────────────────────────────────────────────


@dataclass
class MetricRule:
    key: str
    label: str
    unit: str
    threshold: float
    getter: Callable[[SystemMetrics], Optional[float]]
    with_top: bool = False


def metric_rules(config: "Config") -> List[MetricRule]:
    return [
        MetricRule("cpu", "CPU", "%", config.THRESHOLD_CPU, lambda m: m.cpu_percent, with_top=True),
        MetricRule("memory", "RAM", "%", config.THRESHOLD_MEMORY, lambda m: m.mem_percent, with_top=True),
        MetricRule("disk", "Disk", "%", config.THRESHOLD_DISK, lambda m: m.disk_percent),
        MetricRule("temperature", "CPU temp", "°C", config.THRESHOLD_TEMP, lambda m: m.cpu_temp_c),
    ]


# ── Notifier ──────────────────────────────────────────────────────────────────


@dataclass
class Notification:
    text: str  # HTML
    with_graph: bool = False
    top_sort: Optional[str] = None  # "cpu" / "memory" → append top processes
    extra: List[str] = field(default_factory=list)


class Notifier:
    """Sends notifications to alert recipients, honouring /mute."""

    def __init__(
        self,
        bot,
        config: "Config",
        csv_logger=None,
        recipients: Optional[Callable[[], List[str]]] = None,
        top_provider: Optional[Callable[[str], str]] = None,
    ) -> None:
        self._bot = bot
        self._config = config
        self._csv_logger = csv_logger
        # Resolved on every send so /addalert and /removealert take effect immediately.
        self._recipients = recipients or (lambda: list(config.ALERT_CHAT_IDS))
        self._top_provider = top_provider
        self._grapher = GraphGenerator()
        self.muted_until: Optional[datetime] = None
        self._tasks: set[asyncio.Task] = set()

    # ── Mute ──────────────────────────────────────────────────────────────────

    def mute(self, duration: timedelta) -> datetime:
        self.muted_until = datetime.now() + duration
        return self.muted_until

    def unmute(self) -> None:
        self.muted_until = None

    def is_muted(self) -> bool:
        if self.muted_until and datetime.now() >= self.muted_until:
            self.muted_until = None
        return self.muted_until is not None

    # ── Sending ───────────────────────────────────────────────────────────────

    def dispatch(self, notification: Notification, force: bool = False) -> None:
        """Send in the background so slow Telegram calls never stall sampling."""
        if not force and self.is_muted():
            logger.info("Muted — dropped notification: %s", notification.text.splitlines()[0])
            return
        task = asyncio.create_task(self._send(notification))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _build_graph(self, hours: int):
        data = self._csv_logger.read_recent(hours)
        return self._grapher.generate(data, hours, self._config)

    async def _send(self, n: Notification, graph_hours: int = 12) -> None:
        text = n.text
        if n.top_sort and self._top_provider is not None:
            try:
                top = await asyncio.to_thread(self._top_provider, n.top_sort)
                if top:
                    text += "\n\n" + top
            except Exception as exc:
                logger.warning("Top processes failed: %s", exc)

        graph_path = None
        if n.with_graph and self._csv_logger is not None:
            try:
                graph_path = await asyncio.to_thread(self._build_graph, graph_hours)
            except Exception as exc:
                logger.error("Failed to generate alert graph: %s", exc, exc_info=True)

        try:
            for chat_id in self._recipients():
                try:
                    await self._bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
                    if graph_path and graph_path.exists():
                        with open(graph_path, "rb") as f:
                            await self._bot.send_photo(chat_id=chat_id, photo=f)
                except Exception as exc:
                    # Delivery problems never stop the service — fix the ID / /start the bot and it recovers.
                    reason = describe_send_error(chat_id, exc)
                    if reason:
                        logger.warning(reason)
                    else:
                        logger.error("Failed to send notification to %s: %s", chat_id, exc, exc_info=True)
        finally:
            if graph_path and graph_path.exists():
                try:
                    graph_path.unlink()
                except OSError:
                    pass

    async def send_report(self, text: str, hours: int) -> None:
        await self._send(Notification(text=text, with_graph=True), graph_hours=hours)

    async def drain(self) -> None:
        """Wait for in-flight notifications (used on shutdown and in tests)."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


# ── Metric alerts ─────────────────────────────────────────────────────────────


def _fmt(value: Optional[float], unit: str) -> str:
    return "N/A" if value is None else f"{value:.1f}{unit}"


class AlertManager:
    """Evaluates SystemMetrics against thresholds and emits state-change notifications."""

    def __init__(self, config: "Config", tracker: AlertTracker, notifier: Notifier) -> None:
        self._config = config
        self._tracker = tracker
        self._notifier = notifier
        self._rules = metric_rules(config)

    def check(self, metrics: SystemMetrics) -> List[Transition]:
        transitions = []
        hyst = self._config.ALERT_HYSTERESIS
        for rule in self._rules:
            value = rule.getter(metrics)
            breached = value is not None and value > rule.threshold
            cleared = value is not None and value < rule.threshold - hyst
            t = self._tracker.update(f"metric:{rule.key}", breached, cleared, value, now=metrics.timestamp.replace(tzinfo=None))
            if t is None:
                continue
            transitions.append(t)
            self._notifier.dispatch(self._format(rule, t))
        return transitions

    def _format(self, rule: MetricRule, t: Transition) -> Notification:
        cfg = self._config
        sustained = f"{cfg.ALERT_CONSECUTIVE * cfg.MONITOR_INTERVAL // 60 or 1} phút" if cfg.ALERT_CONSECUTIVE > 1 else ""
        since = t.since.strftime("%H:%M %d/%m")
        if t.kind == FIRE:
            text = (
                f"🔴 <b>{rule.label} cao</b>\n"
                f"Hiện tại: <b>{_fmt(t.value, rule.unit)}</b> (ngưỡng {rule.threshold:g}{rule.unit})"
                + (f", liên tục ≥ {sustained}" if sustained else "")
                + f"\nBắt đầu: {since}"
            )
            return Notification(text, with_graph=True, top_sort=rule.key if rule.with_top else None)
        if t.kind == REMIND:
            text = (
                f"⏰ <b>{rule.label} vẫn cao</b> — đã kéo dài {format_duration(t.duration)}\n"
                f"Hiện tại: {_fmt(t.value, rule.unit)} · Đỉnh: {_fmt(t.peak, rule.unit)}"
            )
            return Notification(text)
        text = (
            f"✅ <b>{rule.label} đã hồi phục</b>\n"
            f"Kéo dài: {format_duration(t.duration)} ({since} → {t.now:%H:%M %d/%m})\n"
            f"Đỉnh: {_fmt(t.peak, rule.unit)} · Hiện tại: {_fmt(t.value, rule.unit)}"
        )
        return Notification(text)


def check_notification(kind: str, title: str, detail: str, t: Transition) -> Notification:
    """Shared formatter for non-metric checks (containers, services, endpoints)."""
    title = html.escape(title)
    detail = html.escape(detail)
    if t.kind == FIRE:
        return Notification(f"🔴 <b>{kind} lỗi: {title}</b>\n{detail}\nBắt đầu: {t.since:%H:%M %d/%m}")
    if t.kind == REMIND:
        return Notification(f"⏰ <b>{kind} vẫn lỗi: {title}</b> — {format_duration(t.duration)}\n{detail}")
    return Notification(
        f"✅ <b>{kind} đã hồi phục: {title}</b>\n"
        f"Gián đoạn: {format_duration(t.duration)} ({t.since:%H:%M} → {t.now:%H:%M %d/%m})\n{detail}"
    )
