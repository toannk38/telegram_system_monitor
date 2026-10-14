import asyncio
from datetime import datetime, timedelta

from bot.handlers import parse_duration
from monitor.docker_monitor import ContainerInfo, DockerMonitor, _parse_stats, _parse_time
from monitor.endpoints import Endpoint, check_endpoint, parse_endpoints
from monitor.report import build_report
from monitor.services import ServiceStatus


def container(**kw):
    base = dict(name="web", image="nginx", status="running", health=None, restart_count=0,
                exit_code=0, oom_killed=False, started_at=None, finished_at=None)
    base.update(kw)
    return ContainerInfo(**base)


def kinds(c, since=None):
    return [k for k, _ in c.issues(since)]


def test_container_problems():
    assert container().problem() is None
    assert kinds(container(health="unhealthy")) == ["unhealthy"]
    assert kinds(container(status="restarting")) == ["crashloop"]
    assert kinds(container(status="exited", exit_code=137, oom_killed=True)) == ["oom"]
    assert kinds(container(status="exited", exit_code=1)) == ["exit"]
    assert kinds(container(status="dead")) == ["exit"]
    assert "OOM" in container(status="exited", exit_code=137, oom_killed=True).problem()
    # docker stop → not a problem
    assert container(status="exited", exit_code=143).problem() is None
    assert container(status="exited", exit_code=0).problem() is None


def test_restart_window():
    d = DockerMonitor("unix:///x", [], [], restart_window_minutes=10)
    t0 = datetime(2026, 1, 1, 8, 0)
    assert d.recent_restarts(container(restart_count=5), t0) == 0
    assert d.recent_restarts(container(restart_count=7), t0 + timedelta(minutes=2)) == 2
    # after the window passes without new restarts → 0
    assert d.recent_restarts(container(restart_count=7), t0 + timedelta(minutes=15)) == 0


def test_container_filtering():
    d = DockerMonitor("unix:///x", watch=["a", "b"], ignore=["b"], restart_window_minutes=10)
    assert d._selected("a", {})
    assert not d._selected("b", {})
    assert not d._selected("c", {})
    assert not d._selected("a", {"system-monitor.ignore": "true"})


def test_parse_docker_time_and_stats():
    assert _parse_time("2026-10-14T02:28:58.123456789Z").year == 2026
    assert _parse_time("0001-01-01T00:00:00Z") is None
    stats = {
        "cpu_stats": {"cpu_usage": {"total_usage": 2_000}, "system_cpu_usage": 20_000, "online_cpus": 4},
        "precpu_stats": {"cpu_usage": {"total_usage": 1_000}, "system_cpu_usage": 10_000},
        "memory_stats": {"usage": 300 * 1024**2, "limit": 1024**3, "stats": {"inactive_file": 100 * 1024**2}},
    }
    s = _parse_stats(stats)
    assert s.cpu_percent == 40.0
    assert s.mem_used_mb == 200.0


def test_service_status():
    assert ServiceStatus("nginx", "loaded", "active", "running", "success").ok
    assert ServiceStatus("nginx", "loaded", "failed", "failed", "exit-code").failed
    assert ServiceStatus("nginx", "loaded", "inactive", "dead", "success").failed
    s = ServiceStatus("nginx", "loaded", "activating", "start", "")
    assert not s.ok and not s.failed
    assert not ServiceStatus("nope", "not-found", "inactive", "dead", "").failed


def test_parse_endpoints():
    eps = parse_endpoints(["web=https://example.com", "db=tcp://10.0.0.5:5432", "bad=ftp://x", "gw=ping://1.1.1.1"])
    assert [e.name for e in eps] == ["web", "db", "gw"]


def test_tcp_endpoint_closed_port():
    r = asyncio.run(check_endpoint(Endpoint("x", "tcp://127.0.0.1:1"), timeout=2))
    assert not r.ok


def test_parse_duration():
    assert parse_duration("30m") == timedelta(minutes=30)
    assert parse_duration("2h") == timedelta(hours=2)
    assert parse_duration("1d") == timedelta(days=1)
    assert parse_duration("90") == timedelta(minutes=90)
    assert parse_duration("abc") is None
    assert parse_duration("0") is None


def test_report():
    rows = [
        {"timestamp": "2026-10-14 08:00:00+07:00", "cpu_percent": "10", "cpu_max": "50", "mem_percent": "40",
         "net_rx_bps": "1024"},
        {"timestamp": "2026-10-14 08:01:00+07:00", "cpu_percent": "30", "cpu_max": "70", "mem_percent": "60",
         "net_rx_bps": "1024"},
    ]
    text = build_report(rows, 24, 60, ["metric:cpu"])
    assert "CPU" in text and "70.0" in text  # max uses cpu_max
    assert "Net ↓: 120.0 KB" in text
    assert "metric:cpu" in text
    assert "Không có dữ liệu" in build_report([], 24, 60, [])


def test_old_exits_are_not_reported():
    from datetime import timezone
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    old = container(status="exited", exit_code=255, finished_at=since - timedelta(days=3))
    new = container(status="exited", exit_code=255, finished_at=since + timedelta(minutes=1))
    assert old.problem(since) is None
    assert new.problem(since)
    assert container(health="unhealthy").problem(since)


def test_pre_block_never_breaks_entities():
    from bot.handlers import _pre_block
    for text, keep in [("<" * 5000, "head"), ("x" * 2000 + "&" * 3000, "tail"), ("ok", "head")]:
        block = _pre_block(text, keep=keep)
        assert len(block) <= 3800
        assert block.startswith("<pre>") and block.endswith("</pre>")
        inner = block[5:-6]
        assert "<" not in inner and ">" not in inner
        # every & starts a complete entity
        import re
        assert all(m.group(0) in ("&lt;", "&gt;", "&amp;") for m in re.finditer(r"&[a-z]*;?", inner))


def test_parse_docker_alert():
    from monitor.config import DOCKER_ALERT_KINDS, _parse_docker_alert
    assert _parse_docker_alert("true") == set(DOCKER_ALERT_KINDS)
    assert _parse_docker_alert("all") == set(DOCKER_ALERT_KINDS)
    assert _parse_docker_alert("false") == set()
    assert _parse_docker_alert("") == set()
    assert _parse_docker_alert("oom, Exit,bogus") == {"oom", "exit"}


class FakeDocker(DockerMonitor):
    def __init__(self, containers):
        super().__init__("unix:///x", [], [], restart_window_minutes=10)
        self._containers = containers

    def list_containers(self):
        return self._containers


class Collect:
    def __init__(self):
        self.sent = []

    def dispatch(self, n, force=False):
        self.sent.append(n.text)


def run_docker_check(docker_alert, containers):
    from monitor.alerting import AlertTracker
    from monitor.checks import ChecksRunner
    from monitor.config import Config, _parse_docker_alert
    cfg = Config(BOT_TOKEN="x", ALERT_CHAT_IDS=[], BOT_ALLOWED_USERS=[], BOT_ADMIN_USERS=[],
                 DOCKER_ALERT=_parse_docker_alert(docker_alert))
    notifier = Collect()
    runner = ChecksRunner(cfg, AlertTracker(1, 1, 0, 480), notifier, FakeDocker(containers))
    asyncio.run(runner.run_once())
    return runner, notifier.sent


def test_docker_alert_filters_kinds():
    cs = [container(name="sick", health="unhealthy"), container(name="loop", status="restarting")]
    _, sent = run_docker_check("crashloop", cs)
    assert len(sent) == 1 and "loop" in sent[0]
    _, sent = run_docker_check("true", cs)
    assert len(sent) == 2


def test_docker_alert_off_keeps_commands_but_no_checks():
    runner, sent = run_docker_check("false", [container(name="sick", health="unhealthy")])
    assert sent == []
    assert runner.docker is not None  # /docker still available
    assert not runner.enabled
