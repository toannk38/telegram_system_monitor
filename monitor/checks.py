"""Periodic non-metric checks: Docker containers, systemd services, endpoints."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

from monitor.alerting import AlertTracker, Notifier, check_notification
from monitor.endpoints import check_endpoints, parse_endpoints
from monitor.services import check_services, systemctl_available

if TYPE_CHECKING:
    from monitor.config import Config
    from monitor.docker_monitor import DockerMonitor

logger = logging.getLogger(__name__)


class ChecksRunner:
    def __init__(
        self,
        config: "Config",
        tracker: AlertTracker,
        notifier: Notifier,
        docker: Optional["DockerMonitor"] = None,
    ) -> None:
        self._config = config
        self._tracker = tracker
        self._notifier = notifier
        self.docker = docker
        self.endpoints = parse_endpoints(config.ENDPOINTS)
        self.services_enabled = bool(config.SYSTEMD_SERVICES) and systemctl_available()
        if config.SYSTEMD_SERVICES and not self.services_enabled:
            logger.warning("SYSTEMD_SERVICES is set but systemctl is not available (Docker?) — service checks disabled")

    @property
    def enabled(self) -> bool:
        return bool((self.docker and self._config.DOCKER_ALERT) or self.services_enabled or self.endpoints)

    async def run_once(self) -> None:
        jobs = []
        # /docker and /dlogs work with DOCKER_MONITOR alone; alerts need DOCKER_ALERT.
        if self.docker and self._config.DOCKER_ALERT:
            jobs.append(self._check_docker())
        if self.services_enabled:
            jobs.append(self._check_services())
        if self.endpoints:
            jobs.append(self._check_endpoints())
        for result in await asyncio.gather(*jobs, return_exceptions=True):
            if isinstance(result, Exception):
                logger.error("Check failed: %s", result, exc_info=result)

    def _emit(self, kind: str, key: str, title: str, breached: bool, detail: str) -> None:
        t = self._tracker.update(key, breached, not breached, detail=detail)
        if t:
            self._notifier.dispatch(check_notification(kind, title, detail, t))

    # ── Docker ────────────────────────────────────────────────────────────────

    async def _check_docker(self) -> None:
        try:
            containers = await asyncio.to_thread(self.docker.list_containers)
        except Exception as exc:
            self._emit("Docker", "docker:@daemon", "Docker API", True, f"Không kết nối được: {exc}"[:300])
            return
        self._emit("Docker", "docker:@daemon", "Docker API", False, "Kết nối lại thành công")

        seen = set()
        for c in containers:
            key = f"docker:{c.name}"
            seen.add(key)
            issues = c.issues(self.docker.watching_since)
            restarts = self.docker.recent_restarts(c)
            if restarts:
                window = self._config.DOCKER_RESTART_WINDOW_MINUTES
                issues.append(("crashloop", f"restart {restarts} lần trong {window} phút"))
            # Only kinds enabled in DOCKER_ALERT count as an alert.
            alerting = [msg for kind, msg in issues if kind in self._config.DOCKER_ALERT]
            problem = "; ".join(alerting) or None
            detail = problem or f"{c.status}" + (f" ({c.health})" if c.health else "")
            self._emit("Container", key, c.name, problem is not None, detail)

        # Containers that disappeared (removed / compose down): clear and forget them.
        for key in self._tracker.keys("docker:"):
            if key in seen or key == "docker:@daemon":
                continue
            name = key.split(":", 1)[1]
            if self._tracker.is_active(key):
                self._emit("Container", key, name, False, "container đã bị xoá")
            else:
                self._tracker.forget(key)
                self.docker.forget(name)

    # ── systemd ───────────────────────────────────────────────────────────────

    async def _check_services(self) -> None:
        for s in await check_services(self._config.SYSTEMD_SERVICES):
            if s.load_state != "loaded":
                continue
            if not s.failed and not s.ok:
                continue  # transitional (activating/reloading): keep current state
            self._emit("Service", f"service:{s.name}", s.name, s.failed, s.summary)

    # ── Endpoints ─────────────────────────────────────────────────────────────

    async def _check_endpoints(self) -> None:
        for r in await check_endpoints(self.endpoints, self._config.ENDPOINT_TIMEOUT):
            detail = f"{r.endpoint.target} — {r.detail}"
            if r.latency_ms is not None:
                detail += f" ({r.latency_ms:.0f} ms)"
            self._emit("Endpoint", f"endpoint:{r.endpoint.name}", r.endpoint.name, not r.ok, detail)
