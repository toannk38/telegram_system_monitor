from collections import namedtuple

from monitor.collector import MetricsCollector, pick_cpu_temperature

Temp = namedtuple("Temp", "label current high critical")


def t(label, current):
    return Temp(label, current, None, None)


def test_prefers_intel_package_over_cores():
    temps = {
        "acpitz": [t("", 27.8)],
        "coretemp": [t("Core 0", 50.0), t("Package id 0", 55.0), t("Core 1", 60.0)],
    }
    assert pick_cpu_temperature(temps) == 55.0


def test_amd_k10temp_tctl_used_instead_of_first_sensor():
    temps = {
        "acpitz": [t("", 27.8)],
        "nvme": [t("Composite", 39.9)],
        "k10temp": [t("Tccd1", 61.0), t("Tctl", 63.5)],
    }
    assert pick_cpu_temperature(temps) == 63.5


def test_chip_without_package_label_uses_hottest():
    assert pick_cpu_temperature({"cpu_thermal": [t("", 48.2), t("", 51.0)]}) == 51.0


def test_unknown_sensors_only_returns_none():
    temps = {"acpitz": [t("", 27.8)], "nvme": [t("Composite", 39.9)], "iwlwifi_1": [t("", 50.0)]}
    assert pick_cpu_temperature(temps) is None


def test_empty_returns_none():
    assert pick_cpu_temperature({}) is None


def test_collect_returns_consistent_metrics():
    c = MetricsCollector("/")
    c.sample()
    m = c.flush()
    assert m.timestamp.tzinfo is not None
    assert 0 <= m.cpu_percent <= 100
    assert 0 <= m.mem_percent <= 100
    # used MB is derived from `available`, so it must match the percentage
    assert abs(m.mem_used_mb / m.mem_total_mb * 100 - m.mem_percent) < 1.0
    assert 0 <= m.disk_percent <= 100


def test_gpu_amdgpu_edge():
    from monitor.collector import pick_gpu_temperature
    temps = {"coretemp": [t("Package id 0", 50.0)], "amdgpu": [t("edge", 61.0), t("junction", 70.0)]}
    assert pick_gpu_temperature(temps) == 61.0
    assert pick_gpu_temperature({"coretemp": [t("Package id 0", 50.0)]}) is None


def test_read_net_dev_skips_virtual_interfaces(tmp_path):
    from monitor.collector import read_net_dev
    f = tmp_path / "dev"
    f.write_text(
        "Inter-|   Receive |  Transmit\n"
        " face |bytes packets errs drop fifo frame compressed multicast|bytes packets ...\n"
        "    lo: 999 1 0 0 0 0 0 0 999 1 0 0 0 0 0 0\n"
        "enp2s0: 100 1 0 0 0 0 0 0 50 1 0 0 0 0 0 0\n"
        "wlp3s0: 10 1 0 0 0 0 0 0 5 1 0 0 0 0 0 0\n"
        "docker0: 7 1 0 0 0 0 0 0 7 1 0 0 0 0 0 0\n"
        "veth12ab: 7 1 0 0 0 0 0 0 7 1 0 0 0 0 0 0\n"
    )
    assert read_net_dev(str(f)) == (110, 55)
