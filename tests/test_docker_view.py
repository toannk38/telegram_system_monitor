from datetime import datetime, timedelta, timezone

from bot.docker_view import compact_age, display_names, render, shorten, strip_common_prefix
from monitor.docker_monitor import ContainerInfo, ContainerStats

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def c(name, project="", status="running", health=None, exit_code=0, age=timedelta(hours=5), finished=None):
    return ContainerInfo(name=name, image="img", status=status, health=health, restart_count=0,
                         exit_code=exit_code, oom_killed=False, started_at=NOW - age,
                         finished_at=finished, project=project)


def test_stale_unhealthy_on_stopped_container_is_not_a_problem():
    # Docker keeps the last health status after the container stops.
    stopped = c("pg", status="exited", health="unhealthy", finished=NOW - timedelta(days=41))
    assert stopped.problem() is None
    assert c("pg", health="unhealthy").problem() == "healthcheck: unhealthy"


def test_name_helpers():
    assert strip_common_prefix(["fb-mess-api", "fb-mess-sender", "fb-mess-service-redis-1"]) == [
        "api", "sender", "service-redis-1"]
    assert strip_common_prefix(["solo"]) == ["solo"]
    assert strip_common_prefix(["a-x", "b-y"]) == ["a-x", "b-y"]
    assert shorten("fb-mess-attachment-worker", 18) == "…attachment-worker"
    assert compact_age(timedelta(days=1, hours=20, minutes=5)) == "1d20h"
    assert compact_age(timedelta(hours=4, minutes=8)) == "4h08m"
    assert compact_age(timedelta(seconds=10)) == "<1m"


def test_display_names_per_project():
    running = [c("p1-api-1", "p1"), c("p1-web-1", "p1"), c("lonely", "single")]
    assert display_names(running) == {"p1-api-1": "api-1", "p1-web-1": "web-1", "lonely": "lonely"}


def test_render_group_mode():
    containers = [
        c("shop-api-1", "shop"), c("shop-web-1", "shop"),
        c("minio", "minio"),
        c("broken", health="unhealthy"),
        c("old", status="exited", exit_code=255, finished=NOW - timedelta(days=3)),
    ]
    stats = {"shop-api-1": ContainerStats(12.5, 300, 1024), "shop-web-1": ContainerStats(1.0, 50, None),
             "minio": ContainerStats(0.2, 120, None)}
    text = render(containers, stats, watching_since=NOW - timedelta(hours=1), mode="group", now=NOW)
    assert "🟢 3 chạy · ❌ 1 lỗi · ⏸ 1 dừng" in text
    assert "❌ <b>Có vấn đề (1)</b>" in text and "broken" in text
    assert "📦 <b>shop</b> · 2" in text and "api-1" in text
    assert "📦 <b>Khác</b> · 1" in text  # single-container project pooled
    assert "old <i>(exit 255)</i>" in text
    assert text.count("<pre>") == text.count("</pre>") == 2


def test_render_flat_mode_tags_colliding_names():
    containers = [c("a-api-1", "alpha-app"), c("a-web-1", "alpha-app"), c("b-api-1", "beta"), c("b-db-1", "beta")]
    stats = {n.name: ContainerStats(i, 10 * i, None) for i, n in enumerate(containers)}
    text = render(containers, stats, None, mode="cpu", now=NOW)
    assert "aa/api-1" in text and "b/api-1" in text and "web-1" in text


def test_render_stays_under_telegram_limit():
    containers = [c(f"proj{i // 10}-service-number-{i}", f"proj{i // 10}") for i in range(300)]
    text = render(containers, {}, None, mode="group", now=NOW)
    assert len(text) <= 3800
    assert "… còn" in text
    assert text.count("<pre>") == text.count("</pre>")
