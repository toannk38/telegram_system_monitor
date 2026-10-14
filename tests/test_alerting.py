import asyncio
from datetime import datetime, timedelta

from monitor.alerting import FIRE, RECOVER, REMIND, AlertManager, AlertTracker, Notifier
from monitor.config import Config
from tests.test_storage import make_metrics


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.sent.append((chat_id, text))

    async def send_photo(self, chat_id, photo):
        pass


def make_config(**kw):
    base = dict(BOT_TOKEN="x", ALERT_CHAT_IDS=["1"], BOT_ALLOWED_USERS=[], BOT_ADMIN_USERS=[])
    base.update(kw)
    return Config(**base)


def tracker(**kw):
    args = dict(consecutive=3, recovery_consecutive=3, remind_minutes=60, remind_max_minutes=480)
    args.update(kw)
    return AlertTracker(**args)


def feed(t, values, threshold=80, hyst=5, start=None, step=timedelta(minutes=1)):
    """Feed one value per minute; return the transitions emitted."""
    now = start or datetime(2026, 1, 1, 8, 0)
    out = []
    for v in values:
        tr = t.update("metric:memory", v > threshold, v < threshold - hyst, v, now=now)
        if tr:
            out.append(tr)
        now += step
    return out


def test_ram_full_for_12_hours_sends_few_messages():
    # 12h at 95% then recovery: 1 fire + reminders at ~1h, 3h, 7h + 1 recover.
    t = tracker()
    events = feed(t, [95] * (12 * 60) + [50] * 5)
    kinds = [e.kind for e in events]
    assert kinds == [FIRE, REMIND, REMIND, REMIND, RECOVER]
    rec = events[-1]
    assert rec.peak == 95
    assert timedelta(hours=11, minutes=50) < rec.duration < timedelta(hours=12, minutes=10)


def test_single_spike_does_not_alert():
    assert feed(tracker(), [99, 10, 99, 10, 99, 10]) == []


def test_hovering_around_threshold_does_not_flap():
    # 78-82 never drops below 75 → stays in ALERT, never recovers/re-fires.
    t = tracker(remind_minutes=0)
    events = feed(t, [85, 85, 85] + [78, 82] * 100)
    assert [e.kind for e in events] == [FIRE]


def test_recovery_requires_consecutive_clear_samples():
    t = tracker(remind_minutes=0)
    events = feed(t, [90, 90, 90, 50, 50, 90, 50, 50, 50])
    assert [e.kind for e in events] == [FIRE, RECOVER]


def test_reminders_disabled():
    events = feed(tracker(remind_minutes=0), [95] * 600)
    assert [e.kind for e in events] == [FIRE]


def test_reminder_interval_caps_at_max():
    events = feed(tracker(remind_minutes=60, remind_max_minutes=120), [95] * (8 * 60))
    reminds = [e for e in events if e.kind == REMIND]
    gaps = [(b.now - a.now) for a, b in zip(reminds, reminds[1:])]
    assert gaps and all(g <= timedelta(minutes=120) for g in gaps)


def run_manager(config, cpus, bot):
    async def go():
        notifier = Notifier(bot, config)
        manager = AlertManager(config, tracker(consecutive=config.ALERT_CONSECUTIVE), notifier)
        now = datetime.now().astimezone()
        for i, cpu in enumerate(cpus):
            m = make_metrics(ts=now + timedelta(minutes=i), cpu=cpu)
            manager.check(m)
        await notifier.drain()
    asyncio.run(go())


def test_manager_fires_once_for_sustained_cpu():
    bot = FakeBot()
    run_manager(make_config(ALERT_CONSECUTIVE=3), [99] * 10, bot)
    assert len(bot.sent) == 1
    assert "CPU cao" in bot.sent[0][1]


def test_manager_sends_recovery():
    bot = FakeBot()
    run_manager(make_config(ALERT_CONSECUTIVE=1), [99, 10, 10, 10], bot)
    texts = [t for _, t in bot.sent]
    assert "CPU cao" in texts[0] and "đã hồi phục" in texts[1]


def test_disk_alert():
    bot = FakeBot()

    async def go():
        config = make_config(ALERT_CONSECUTIVE=1)
        notifier = Notifier(bot, config)
        m = make_metrics()
        m.disk_percent = 97.0
        AlertManager(config, tracker(consecutive=1), notifier).check(m)
        await notifier.drain()

    asyncio.run(go())
    assert any("Disk cao" in t for _, t in bot.sent)


def test_mute_drops_notifications_but_keeps_state():
    bot = FakeBot()

    async def go():
        config = make_config(ALERT_CONSECUTIVE=1)
        notifier = Notifier(bot, config)
        notifier.mute(timedelta(hours=1))
        tr = tracker(consecutive=1)
        AlertManager(config, tr, notifier).check(make_metrics(cpu=99))
        await notifier.drain()
        return tr

    tr = asyncio.run(go())
    assert bot.sent == []
    assert tr.is_active("metric:cpu")


def test_recipients_resolved_dynamically():
    bot = FakeBot()
    recipients = ["1"]

    async def go():
        config = make_config(ALERT_CONSECUTIVE=1)
        notifier = Notifier(bot, config, recipients=lambda: list(recipients))
        recipients.append("2")
        AlertManager(config, tracker(consecutive=1), notifier).check(make_metrics(cpu=99))
        await notifier.drain()

    asyncio.run(go())
    assert {chat for chat, _ in bot.sent} == {"1", "2"}
