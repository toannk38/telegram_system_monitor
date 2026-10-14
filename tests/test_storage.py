import csv
from datetime import datetime, timedelta

from monitor.collector import SystemMetrics
from monitor.storage import CSV_COLUMNS, CSVLogger, parse_timestamp


def make_metrics(ts=None, cpu=10.0, temp=None):
    return SystemMetrics(
        timestamp=ts or datetime.now().astimezone(),
        cpu_percent=cpu,
        mem_percent=20.0,
        mem_used_mb=1000.0,
        mem_total_mb=5000.0,
        disk_percent=30.0,
        disk_used_gb=10.0,
        disk_total_gb=100.0,
        cpu_temp_c=temp,
    )


def read_all(path):
    with path.open(newline="") as f:
        return list(csv.reader(f))


def test_new_file_gets_header_and_row(tmp_path):
    logger = CSVLogger(str(tmp_path))
    logger.write(make_metrics(temp=45.0))
    [path] = tmp_path.glob("system_stats_*.csv")
    rows = read_all(path)
    assert rows[0] == CSV_COLUMNS
    assert len(rows) == 2


def test_empty_existing_file_gets_header(tmp_path):
    m = make_metrics()
    path = tmp_path / f"system_stats_{m.timestamp:%Y%m%d}.csv"
    path.touch()
    CSVLogger(str(tmp_path)).write(m)
    assert read_all(path)[0] == CSV_COLUMNS


def test_legacy_file_is_migrated_to_current_columns(tmp_path):
    m = make_metrics()
    path = tmp_path / f"system_stats_{m.timestamp:%Y%m%d}.csv"
    legacy = ["timestamp", "cpu_percent", "mem_percent", "mem_used_mb", "mem_total_mb", "cpu_temp_c"]
    old_ts = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    path.write_text(",".join(legacy) + "\n" + f"{old_ts},1.0,2.0,3.0,4.0,\n")

    logger = CSVLogger(str(tmp_path))
    logger.write(m)

    rows = read_all(path)
    assert rows[0] == CSV_COLUMNS
    assert all(len(r) == len(CSV_COLUMNS) for r in rows)
    # legacy naive and new aware timestamps are both readable
    recent = logger.read_recent(1)
    assert len(recent) == 2
    assert recent[0]["cpu_percent"] == "1.0" and recent[0]["disk_percent"] == ""


def test_read_recent_filters_and_sorts(tmp_path):
    logger = CSVLogger(str(tmp_path))
    now = datetime.now().astimezone()
    for minutes, cpu in [(10, 2.0), (300, 9.0), (30, 1.0)]:
        ts = now - timedelta(minutes=minutes)
        if ts.date() == now.date():
            logger.write(make_metrics(ts=ts, cpu=cpu))
    rows = logger.read_recent(1)
    timestamps = [parse_timestamp(r["timestamp"]) for r in rows]
    assert timestamps == sorted(timestamps)
    assert all(ts >= now - timedelta(hours=1) for ts in timestamps)


def test_parse_timestamp_formats():
    aware = parse_timestamp("2026-10-14 10:00:00+07:00")
    naive = parse_timestamp("2026-10-14 10:00:00")
    assert aware.tzinfo is not None and naive.tzinfo is not None
