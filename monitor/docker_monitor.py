"""
Read-only Docker monitoring through the Docker API (ideally via a socket proxy
that only allows GET /containers/*). All calls are blocking — use asyncio.to_thread.
"""
from __future__ import annotations

import logging
import re
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

IGNORE_LABEL = "system-monitor.ignore"
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# Exit codes produced by `docker stop` (SIGTERM handled / SIGKILL after timeout).
_STOP_EXIT_CODES = {0, 137, 143}


@dataclass
class ContainerInfo:
    name: str
    image: str
    status: str  # created / running / restarting / exited / paused / dead
    health: Optional[str]  # healthy / unhealthy / starting / None
    restart_count: int
    exit_code: int
    oom_killed: bool
    started_at: Optional[datetime]
    finished_at: Optional[datetime]

    def issues(self, watching_since: Optional[datetime] = None) -> List[Tuple[str, str]]:
        """
        All current problems as (kind, message); kind is one of config.DOCKER_ALERT_KINDS.
        Restarts within the window ("crashloop" from history) are added by the caller.

        Containers that exited before `watching_since` (e.g. stopped long ago, or
        left over from a host reboot) are not reported — only new failures are.
        """
        found: List[Tuple[str, str]] = []
        if self.health == "unhealthy":
            found.append(("unhealthy", "healthcheck: unhealthy"))
        if self.status == "restarting":
            found.append(("crashloop", "đang restart liên tục"))
        if self.status == "dead":
            found.append(("exit", "trạng thái dead"))
        if self.status == "exited":
            if watching_since and (self.finished_at is None or self.finished_at < watching_since):
                return found
            if self.oom_killed:
                found.append(("oom", "bị OOM kill (hết RAM)"))
            elif self.exit_code not in _STOP_EXIT_CODES:
                found.append(("exit", f"exited với mã lỗi {self.exit_code}"))
        return found

    def problem(self, watching_since: Optional[datetime] = None) -> Optional[str]:
        """First problem message (for display), or None if fine."""
        found = self.issues(watching_since)
        return found[0][1] if found else None


@dataclass
class ContainerStats:
    cpu_percent: Optional[float]
    mem_used_mb: Optional[float]
    mem_limit_mb: Optional[float]


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value or value.startswith("0001-"):
        return None
    try:
        # Docker uses RFC3339 with nanoseconds: trim to microseconds for fromisoformat.
        head, _, frac = value.rstrip("Z").partition(".")
        frac = frac[:6]
        return datetime.fromisoformat(f"{head}.{frac}+00:00" if frac else f"{head}+00:00")
    except ValueError:
        return None


class DockerMonitor:
    def __init__(self, base_url: str, watch: List[str], ignore: List[str], restart_window_minutes: int) -> None:
        self._base_url = base_url
        self._watch = set(watch)
        self._ignore = set(ignore)
        self._restart_window = timedelta(minutes=restart_window_minutes)
        self._client = None
        # Only exits after this moment are reported (see ContainerInfo.problem).
        self.watching_since = datetime.now().astimezone()
        # name -> list of (time seen, restart_count) to detect restarts within the window
        self._restart_history: Dict[str, Deque[Tuple[datetime, int]]] = {}

    def _get_client(self):
        if self._client is None:
            import docker

            self._client = docker.DockerClient(base_url=self._base_url, timeout=15)
        return self._client

    def _selected(self, name: str, labels: Dict[str, str]) -> bool:
        if (labels or {}).get(IGNORE_LABEL, "").lower() in ("1", "true", "yes"):
            return False
        if name in self._ignore:
            return False
        return not self._watch or name in self._watch

    def list_containers(self) -> List[ContainerInfo]:
        client = self._get_client()
        result = []
        for c in client.containers.list(all=True):
            attrs = c.attrs
            name = attrs.get("Name", c.name).lstrip("/")
            labels = attrs.get("Config", {}).get("Labels") or {}
            if not self._selected(name, labels):
                continue
            state = attrs.get("State", {})
            health = (state.get("Health") or {}).get("Status")
            result.append(
                ContainerInfo(
                    name=name,
                    image=attrs.get("Config", {}).get("Image", ""),
                    status=state.get("Status", "unknown"),
                    health=health,
                    restart_count=int(attrs.get("RestartCount", 0)),
                    exit_code=int(state.get("ExitCode", 0)),
                    oom_killed=bool(state.get("OOMKilled", False)),
                    started_at=_parse_time(state.get("StartedAt")),
                    finished_at=_parse_time(state.get("FinishedAt")),
                )
            )
        result.sort(key=lambda x: x.name)
        return result

    def recent_restarts(self, info: ContainerInfo, now: Optional[datetime] = None) -> int:
        """Number of restarts observed within the restart window (updates history)."""
        now = now or datetime.now()
        hist = self._restart_history.setdefault(info.name, deque())
        hist.append((now, info.restart_count))
        while hist and now - hist[0][0] > self._restart_window:
            hist.popleft()
        oldest = hist[0][1]
        # restart_count resets when the container is recreated — treat that as 0.
        return max(0, info.restart_count - oldest)

    def forget(self, name: str) -> None:
        self._restart_history.pop(name, None)

    def stats(self, names: List[str]) -> Dict[str, ContainerStats]:
        """CPU/RAM per running container (each call samples ~1-2s, done in parallel)."""
        client = self._get_client()

        def one(name: str) -> Tuple[str, ContainerStats]:
            try:
                s = client.containers.get(name).stats(stream=False)
                return name, _parse_stats(s)
            except Exception as exc:
                logger.debug("stats for %s failed: %s", name, exc)
                return name, ContainerStats(None, None, None)

        if not names:
            return {}
        with ThreadPoolExecutor(max_workers=min(8, len(names))) as pool:
            return dict(pool.map(one, names))

    def logs(self, name: str, lines: int) -> str:
        container = self._get_client().containers.get(name)
        text = container.logs(tail=lines, timestamps=False).decode(errors="replace")
        return _ANSI.sub("", text)


def _parse_stats(s: dict) -> ContainerStats:
    cpu = None
    try:
        cpu_delta = s["cpu_stats"]["cpu_usage"]["total_usage"] - s["precpu_stats"]["cpu_usage"]["total_usage"]
        sys_delta = s["cpu_stats"]["system_cpu_usage"] - s["precpu_stats"]["system_cpu_usage"]
        ncpu = s["cpu_stats"].get("online_cpus") or len(s["cpu_stats"]["cpu_usage"].get("percpu_usage") or [1])
        if sys_delta > 0 and cpu_delta >= 0:
            cpu = round(cpu_delta / sys_delta * ncpu * 100, 1)
    except (KeyError, TypeError):
        pass

    mem_used = mem_limit = None
    mem = s.get("memory_stats") or {}
    if "usage" in mem:
        # Same as `docker stats`: exclude page cache.
        stats = mem.get("stats") or {}
        cache = stats.get("inactive_file", stats.get("total_inactive_file", 0))
        mem_used = round((mem["usage"] - cache) / 1024**2, 1)
        mem_limit = round(mem.get("limit", 0) / 1024**2, 1) or None
    return ContainerStats(cpu, mem_used, mem_limit)


def format_uptime(info: ContainerInfo, now: Optional[datetime] = None) -> str:
    from monitor.alerting import format_duration

    now = now or datetime.now().astimezone()
    if info.status == "running" and info.started_at:
        return "up " + format_duration(now - info.started_at)
    if info.finished_at:
        return format_duration(now - info.finished_at) + " trước"
    return ""
