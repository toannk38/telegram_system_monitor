from monitor.collector import MetricsCollector


def test_flush_aggregates_samples(monkeypatch):
    c = MetricsCollector("/")
    values = iter([10.0, 90.0, 20.0])
    monkeypatch.setattr("monitor.collector.psutil.cpu_percent", lambda interval=None: next(values))
    for _ in range(3):
        c.sample()
    m = c.flush()
    assert m.cpu_percent == 40.0  # average — a single spike doesn't dominate
    assert m.cpu_max == 90.0      # but stays visible
    assert c.last_metrics is m
    assert m.load1 is not None and m.swap_percent is not None


def test_flush_without_samples_takes_one():
    c = MetricsCollector("/")
    m = c.flush()
    assert 0 <= m.cpu_percent <= 100


def test_io_rates_after_second_flush():
    c = MetricsCollector("/")
    c.sample()
    c.flush()
    c.sample()
    m = c.flush()
    assert m.net_rx_bps is None or m.net_rx_bps >= 0
    assert m.disk_write_bps is None or m.disk_write_bps >= 0


def test_snapshot_does_not_consume_window():
    c = MetricsCollector("/")
    c.sample()
    c.snapshot()
    assert len(c._samples) == 1
