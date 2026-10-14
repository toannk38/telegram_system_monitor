"""
Endpoint checks configured via ENDPOINTS, comma-separated `name=target`:

    ENDPOINTS=web=https://example.com,db=tcp://10.0.0.5:5432,gw=ping://192.168.1.1

HTTP(S) is healthy for status < 400, TCP when the port accepts a connection,
ping when one ICMP echo is answered.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


@dataclass
class Endpoint:
    name: str
    target: str

    @property
    def scheme(self) -> str:
        return urlparse(self.target).scheme.lower()


@dataclass
class EndpointResult:
    endpoint: Endpoint
    ok: bool
    latency_ms: Optional[float]
    detail: str


def parse_endpoints(items: List[str]) -> List[Endpoint]:
    endpoints = []
    for item in items:
        name, sep, target = item.partition("=")
        if not sep:
            name, target = item, item
        name, target = name.strip(), target.strip()
        if urlparse(target).scheme.lower() not in ("http", "https", "tcp", "ping"):
            logger.warning("Ignoring endpoint %r: target must start with http(s)://, tcp:// or ping://", item)
            continue
        endpoints.append(Endpoint(name, target))
    return endpoints


async def _check_http(ep: Endpoint, timeout: float) -> EndpointResult:
    start = time.monotonic()
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(ep.target)
    latency = (time.monotonic() - start) * 1000
    return EndpointResult(ep, resp.status_code < 400, latency, f"HTTP {resp.status_code}")


async def _check_tcp(ep: Endpoint, timeout: float) -> EndpointResult:
    url = urlparse(ep.target)
    if not url.hostname or not url.port:
        return EndpointResult(ep, False, None, "tcp:// cần host:port")
    start = time.monotonic()
    _, writer = await asyncio.wait_for(asyncio.open_connection(url.hostname, url.port), timeout)
    latency = (time.monotonic() - start) * 1000
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return EndpointResult(ep, True, latency, f"port {url.port} mở")


async def _check_ping(ep: Endpoint, timeout: float) -> EndpointResult:
    host = urlparse(ep.target).hostname or ""
    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        "ping", "-c", "1", "-W", str(max(1, int(timeout))), host,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    code = await asyncio.wait_for(proc.wait(), timeout + 2)
    latency = (time.monotonic() - start) * 1000
    return EndpointResult(ep, code == 0, latency if code == 0 else None, "reply" if code == 0 else "no reply")


async def check_endpoint(ep: Endpoint, timeout: float) -> EndpointResult:
    checker = {"http": _check_http, "https": _check_http, "tcp": _check_tcp, "ping": _check_ping}[ep.scheme]
    try:
        return await checker(ep, timeout)
    except asyncio.TimeoutError:
        return EndpointResult(ep, False, None, f"timeout sau {timeout:g}s")
    except Exception as exc:
        return EndpointResult(ep, False, None, f"{type(exc).__name__}: {exc}"[:200])


async def check_endpoints(endpoints: List[Endpoint], timeout: float) -> List[EndpointResult]:
    return list(await asyncio.gather(*(check_endpoint(ep, timeout) for ep in endpoints)))
