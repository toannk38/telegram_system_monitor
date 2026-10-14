"""systemd service checks (`systemctl show`). Only meaningful when running on the host."""
from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from typing import Dict, List

logger = logging.getLogger(__name__)

# States that are neither healthy nor failed — don't change alert state while in them.
_TRANSITIONAL = {"activating", "deactivating", "reloading", "refreshing"}


@dataclass
class ServiceStatus:
    name: str
    load_state: str
    active_state: str
    sub_state: str
    result: str

    @property
    def ok(self) -> bool:
        return self.active_state == "active"

    @property
    def failed(self) -> bool:
        if self.load_state != "loaded":
            return False  # unknown unit is a config problem, reported in /services only
        return self.active_state not in _TRANSITIONAL and self.active_state != "active"

    @property
    def summary(self) -> str:
        s = f"{self.active_state} ({self.sub_state})"
        if self.result and self.result != "success":
            s += f", result={self.result}"
        if self.load_state != "loaded":
            s = f"{self.load_state}"
        return s


def systemctl_available() -> bool:
    return shutil.which("systemctl") is not None


async def check_service(name: str) -> ServiceStatus:
    proc = await asyncio.create_subprocess_exec(
        "systemctl", "show", "--property=LoadState,ActiveState,SubState,Result", "--", name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    props: Dict[str, str] = {}
    for line in stdout.decode(errors="replace").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            props[k] = v
    return ServiceStatus(
        name=name,
        load_state=props.get("LoadState", "unknown"),
        active_state=props.get("ActiveState", "unknown"),
        sub_state=props.get("SubState", "unknown"),
        result=props.get("Result", ""),
    )


async def check_services(names: List[str]) -> List[ServiceStatus]:
    results = await asyncio.gather(*(check_service(n) for n in names), return_exceptions=True)
    statuses = []
    for name, r in zip(names, results):
        if isinstance(r, Exception):
            logger.warning("systemctl show %s failed: %s", name, r)
            statuses.append(ServiceStatus(name, "error", "unknown", str(r), ""))
        else:
            statuses.append(r)
    return statuses
