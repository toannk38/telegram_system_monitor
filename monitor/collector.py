from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from statistics import fmean
from typing import List, Optional

import psutil

logger = logging.getLogger(__name__)

# Sensor chips in order of preference: Intel, AMD, ARM/Raspberry Pi, misc.
_PREFERRED_CHIPS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "cpu-thermal", "soc_thermal")
# Within a chip, labels that represent the whole CPU package rather than one core.
_PREFERRED_LABELS = ("package id 0", "tctl", "tdie")
# GPU chips exposed through hwmon (NVIDIA proprietary driver needs nvidia-smi instead).
_GPU_CHIPS = ("amdgpu", "radeon", "nouveau")
_GPU_LABELS = ("edge", "junction")
# Virtual interfaces: loopback, and Docker/bridge/veth links whose traffic is
# already counted on the physical NIC (counting both would double it).
_VIRTUAL_NIC_PREFIXES = ("lo", "veth", "docker", "br-", "virbr", "cni", "flannel", "cali", "tun", "tap")


def _physical_nic(name: str) -> bool:
    return not name.startswith(_VIRTUAL_NIC_PREFIXES)


def read_net_dev(path: str) -> Optional[tuple[int, int]]:
    """Sum rx/tx bytes of physical NICs from a /proc/<pid>/net/dev file."""
    rx = tx = 0
    with open(path, encoding="utf-8") as f:
        for line in f.readlines()[2:]:
            name, _, data = line.partition(":")
            name = name.strip()
            if not data or not _physical_nic(name):
                continue
            fields = data.split()
            rx += int(fields[0])
            tx += int(fields[8])
    return rx, tx


@dataclass
class Sample:
    """One short sub-sample (every SAMPLE_INTERVAL seconds)."""

    timestamp: datetime
    cpu_percent: float  # average since the previous sample, not a 1s snapshot
    mem_percent: float
    cpu_temp_c: Optional[float]


@dataclass
class SystemMetrics:
    """Aggregate of all samples in one MONITOR_INTERVAL window (one CSV row)."""

    timestamp: datetime
    cpu_percent: float  # window average — used for alerting
    mem_percent: float  # window average
    mem_used_mb: float
    mem_total_mb: float
    disk_percent: float
    disk_used_gb: float
    disk_total_gb: float
    cpu_temp_c: Optional[float]  # window average
    cpu_max: Optional[float] = None  # highest sub-sample — keeps spikes visible
    mem_max: Optional[float] = None
    cpu_temp_max: Optional[float] = None
    swap_percent: Optional[float] = None
    load1: Optional[float] = None
    load5: Optional[float] = None
    load15: Optional[float] = None
    gpu_temp_c: Optional[float] = None
    net_rx_bps: Optional[float] = None
    net_tx_bps: Optional[float] = None
    disk_read_bps: Optional[float] = None
    disk_write_bps: Optional[float] = None


def _rate(new: Optional[int], old: Optional[int], seconds: float) -> Optional[float]:
    """Counter delta per second; None when unknown or the counter was reset."""
    if new is None or old is None or seconds <= 0 or new < old:
        return None
    return round((new - old) / seconds, 1)


class MetricsCollector:
    """
    Two-level sampling to avoid single-point spikes:

    - `sample()` every SAMPLE_INTERVAL seconds. CPU uses cpu_percent(interval=None),
      i.e. the average since the previous call, so no time slice is skipped and
      nothing blocks.
    - `flush()` every MONITOR_INTERVAL seconds aggregates the samples (avg + max)
      and computes network / disk I/O rates over the whole window.

    Both are cheap (a few ms) but touch /proc and /sys — call them via
    `asyncio.to_thread` from the event loop.
    """

    def __init__(self, disk_path: str = "/", net_dev_path: str = "") -> None:
        self._disk_path = disk_path
        # In a container /proc/net/dev only shows the container's own network
        # namespace; point this at the host's (e.g. /host/proc/1/net/dev) instead.
        self._net_dev_path = net_dev_path
        self._lock = threading.Lock()
        self._samples: List[Sample] = []
        self._latest: Optional[Sample] = None
        self.last_metrics: Optional[SystemMetrics] = None

        psutil.cpu_percent(interval=None)  # prime: first call always returns 0.0
        self._last_cpu_call = time.monotonic()
        self._last_flush = time.monotonic()
        self._last_net = self._net_counters()
        self._last_disk_io = self._disk_counters()

    # ── Sampling ──────────────────────────────────────────────────────────────

    def _cpu_since_last_call(self) -> float:
        # A near-zero interval gives meaningless values (often 0 or 100%):
        # wait out a minimal window in that case.
        elapsed = time.monotonic() - self._last_cpu_call
        value = psutil.cpu_percent(interval=0.5 if elapsed < 0.5 else None)
        self._last_cpu_call = time.monotonic()
        return value

    def sample(self) -> Sample:
        s = Sample(
            timestamp=datetime.now().astimezone(),
            cpu_percent=round(self._cpu_since_last_call(), 1),
            mem_percent=round(psutil.virtual_memory().percent, 1),
            cpu_temp_c=self.get_cpu_temperature(),
        )
        with self._lock:
            self._samples.append(s)
            self._latest = s
        return s

    def latest(self) -> Sample:
        """Most recent sample (takes one if none exists yet)."""
        with self._lock:
            latest = self._latest
        return latest or self.sample()

    def flush(self) -> SystemMetrics:
        """Aggregate the current window into one SystemMetrics and start a new window."""
        with self._lock:
            samples, self._samples = self._samples, []
        if not samples:
            samples = [self.sample()]
            with self._lock:
                self._samples.clear()

        now = time.monotonic()
        elapsed = now - self._last_flush
        self._last_flush = now

        net = self._net_counters()
        disk_io = self._disk_counters()
        net_rx = _rate(net[0], self._last_net[0], elapsed) if net else None
        net_tx = _rate(net[1], self._last_net[1], elapsed) if net else None
        disk_r = _rate(disk_io[0], self._last_disk_io[0], elapsed) if disk_io else None
        disk_w = _rate(disk_io[1], self._last_disk_io[1], elapsed) if disk_io else None
        self._last_net = net or (None, None)
        self._last_disk_io = disk_io or (None, None)

        mem = psutil.virtual_memory()
        # `percent` is computed from `available`, so derive "used" the same way.
        mem_used = mem.total - mem.available
        disk = psutil.disk_usage(self._disk_path)
        temps = [s.cpu_temp_c for s in samples if s.cpu_temp_c is not None]
        load1, load5, load15 = os.getloadavg()

        metrics = SystemMetrics(
            # Timestamp marks the start of the window.
            timestamp=samples[0].timestamp,
            cpu_percent=round(fmean(s.cpu_percent for s in samples), 1),
            cpu_max=max(s.cpu_percent for s in samples),
            mem_percent=round(fmean(s.mem_percent for s in samples), 1),
            mem_max=max(s.mem_percent for s in samples),
            mem_used_mb=round(mem_used / 1024**2, 1),
            mem_total_mb=round(mem.total / 1024**2, 1),
            swap_percent=round(psutil.swap_memory().percent, 1),
            load1=round(load1, 2),
            load5=round(load5, 2),
            load15=round(load15, 2),
            disk_percent=round(disk.percent, 1),
            disk_used_gb=round(disk.used / 1024**3, 2),
            disk_total_gb=round(disk.total / 1024**3, 2),
            cpu_temp_c=round(fmean(temps), 1) if temps else None,
            cpu_temp_max=max(temps) if temps else None,
            gpu_temp_c=self.get_gpu_temperature(),
            net_rx_bps=net_rx,
            net_tx_bps=net_tx,
            disk_read_bps=disk_r,
            disk_write_bps=disk_w,
        )
        self.last_metrics = metrics
        return metrics

    def snapshot(self) -> SystemMetrics:
        """
        Current state for /status without touching the aggregation window:
        CPU from the latest sample, memory/disk/load read live, I/O rates and
        averages from the last completed window.
        """
        s = self.latest()
        last = self.last_metrics
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage(self._disk_path)
        load1, load5, load15 = os.getloadavg()
        return SystemMetrics(
            timestamp=s.timestamp,
            cpu_percent=s.cpu_percent,
            mem_percent=round(mem.percent, 1),
            mem_used_mb=round((mem.total - mem.available) / 1024**2, 1),
            mem_total_mb=round(mem.total / 1024**2, 1),
            swap_percent=round(psutil.swap_memory().percent, 1),
            load1=round(load1, 2),
            load5=round(load5, 2),
            load15=round(load15, 2),
            disk_percent=round(disk.percent, 1),
            disk_used_gb=round(disk.used / 1024**3, 2),
            disk_total_gb=round(disk.total / 1024**3, 2),
            cpu_temp_c=s.cpu_temp_c,
            gpu_temp_c=last.gpu_temp_c if last else None,
            net_rx_bps=last.net_rx_bps if last else None,
            net_tx_bps=last.net_tx_bps if last else None,
            disk_read_bps=last.disk_read_bps if last else None,
            disk_write_bps=last.disk_write_bps if last else None,
        )

    # ── Counters ──────────────────────────────────────────────────────────────

    def _net_counters(self) -> Optional[tuple[int, int]]:
        try:
            if self._net_dev_path:
                return read_net_dev(self._net_dev_path)
            per_nic = psutil.net_io_counters(pernic=True)
            rx = sum(c.bytes_recv for nic, c in per_nic.items() if _physical_nic(nic))
            tx = sum(c.bytes_sent for nic, c in per_nic.items() if _physical_nic(nic))
            return rx, tx
        except Exception as exc:
            logger.debug("network counters failed: %s", exc)
            return None

    @staticmethod
    def _disk_counters() -> Optional[tuple[int, int]]:
        try:
            io = psutil.disk_io_counters()
            return (io.read_bytes, io.write_bytes) if io else None
        except Exception as exc:
            logger.debug("disk_io_counters failed: %s", exc)
            return None

    # ── Temperatures ──────────────────────────────────────────────────────────

    @staticmethod
    def _sensors() -> dict:
        try:
            if not hasattr(psutil, "sensors_temperatures"):
                return {}
            return psutil.sensors_temperatures() or {}
        except Exception as exc:
            logger.warning("Could not read temperature sensors: %s", exc)
            return {}

    @classmethod
    def get_cpu_temperature(cls) -> Optional[float]:
        """Return CPU package temperature, or None if no CPU sensor is found."""
        return pick_cpu_temperature(cls._sensors())

    @classmethod
    def get_gpu_temperature(cls) -> Optional[float]:
        """GPU temperature from hwmon (AMD/nouveau) or nvidia-smi; None if no GPU."""
        temp = pick_gpu_temperature(cls._sensors())
        if temp is not None:
            return temp
        if shutil.which("nvidia-smi"):
            try:
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5, check=True,
                ).stdout
                values = [float(v) for v in out.split() if v.strip()]
                return max(values) if values else None
            except Exception as exc:
                logger.debug("nvidia-smi failed: %s", exc)
        return None


def _pick(temps: dict, chips: tuple, labels: tuple) -> Optional[float]:
    for chip in chips:
        entries = temps.get(chip)
        if not entries:
            continue
        for wanted in labels:
            for entry in entries:
                if (entry.label or "").lower() == wanted:
                    return round(entry.current, 1)
        # No package-level label: use the hottest reading on this chip.
        return round(max(entry.current for entry in entries), 1)
    return None


def pick_cpu_temperature(temps: dict) -> Optional[float]:
    """
    Choose the CPU temperature from psutil.sensors_temperatures() output.

    Only known CPU chips are considered: falling back to an arbitrary sensor
    (ACPI zone, NVMe, Wi-Fi...) would log a value that is not the CPU's.
    """
    if not temps:
        return None
    return _pick(temps, _PREFERRED_CHIPS, _PREFERRED_LABELS)


def pick_gpu_temperature(temps: dict) -> Optional[float]:
    if not temps:
        return None
    return _pick(temps, _GPU_CHIPS, _GPU_LABELS)
