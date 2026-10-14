"""HTML rendering of the /docker overview (kept compact for phone screens)."""
from __future__ import annotations

import html
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from monitor.docker_monitor import ContainerInfo, ContainerStats

NAME_WIDTH = 18
TOP_LIMIT = 15
MAX_LEN = 3800  # Telegram allows 4096; leave room for the footer
MODES = ("group", "cpu", "mem")


def compact_age(delta: Optional[timedelta]) -> str:
    """1d20h · 4h08m · 16m · <1m"""
    if delta is None:
        return "-"
    minutes = max(0, int(delta.total_seconds() // 60))
    days, rem = divmod(minutes, 1440)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days}d{hours:02d}h"
    if hours:
        return f"{hours}h{mins:02d}m"
    return f"{mins}m" if mins else "<1m"


def fmt_mem(mb: Optional[float]) -> str:
    if mb is None:
        return "-"
    return f"{mb / 1024:.1f}G" if mb >= 1024 else f"{mb:.0f}M"


def fmt_cpu(cpu: Optional[float]) -> str:
    return "-" if cpu is None else f"{cpu:.1f}%"


def shorten(name: str, width: int = NAME_WIDTH) -> str:
    """Keep the tail — it carries the distinctive part (…attachment-worker, …worker-2)."""
    if len(name) <= width:
        return name
    return "…" + name[-(width - 1):]


def strip_common_prefix(names: List[str]) -> List[str]:
    """Drop a shared dash-delimited prefix: fb-mess-api, fb-mess-sender → api, sender."""
    if len(names) < 2:
        return names
    first = names[0]
    cut = 0
    for i, ch in enumerate(first):
        if ch == "-" and all(n.startswith(first[:i + 1]) and len(n) > i + 1 for n in names):
            cut = i + 1
        elif not all(len(n) > i and n[i] == ch for n in names):
            break
    return [n[cut:] for n in names]


def _initials(project: str) -> str:
    return "".join(part[0] for part in project.split("-") if part) or project[:3]


def display_names(running: List[ContainerInfo]) -> Dict[str, str]:
    """
    Short, readable name per container: compose projects lose their shared prefix
    (fb-mess-api → api); single-container projects keep the full name.
    """
    groups: Dict[str, List[ContainerInfo]] = defaultdict(list)
    for c in running:
        groups[c.project or ""].append(c)
    names: Dict[str, str] = {}
    for project, members in groups.items():
        if not project or len(members) == 1:
            names.update({c.name: c.name for c in members})
            continue
        full = [c.name for c in members]
        stripped = strip_common_prefix(full)
        if stripped == full:
            stripped = strip_common_prefix([c.short_name for c in members])
        names.update(dict(zip(full, stripped)))
    return names


def _table(rows: List[tuple[str, Optional[ContainerStats], str]]) -> str:
    lines = [f"{'':<{NAME_WIDTH}} {'CPU':>6} {'RAM':>5} {'UP':>5}"]
    for name, st, up in rows:
        cpu = fmt_cpu(st.cpu_percent if st else None)
        mem = fmt_mem(st.mem_used_mb if st else None)
        lines.append(f"{shorten(name):<{NAME_WIDTH}} {cpu:>6} {mem:>5} {up:>5}")
    return "<pre>" + html.escape("\n".join(lines)) + "</pre>"


def render(
    containers: List[ContainerInfo],
    stats: Dict[str, ContainerStats],
    watching_since: Optional[datetime],
    mode: str = "group",
    now: Optional[datetime] = None,
) -> str:
    now = now or datetime.now().astimezone()

    def uptime(c: ContainerInfo) -> str:
        return compact_age(now - c.started_at) if c.started_at else "-"

    problems = [(c, c.problem(watching_since)) for c in containers]
    problems = [(c, p) for c, p in problems if p]
    problem_names = {c.name for c, _ in problems}
    running = [c for c in containers if c.status == "running" and c.name not in problem_names]
    stopped = [c for c in containers if c.status != "running" and c.name not in problem_names]

    cpu_total = sum((stats[c.name].cpu_percent or 0) for c in running if c.name in stats)
    mem_total = sum((stats[c.name].mem_used_mb or 0) for c in running if c.name in stats)

    head = [
        f"🐳 <b>Docker</b> · 🟢 {len(running)} chạy"
        + (f" · ❌ {len(problems)} lỗi" if problems else "")
        + (f" · ⏸ {len(stopped)} dừng" if stopped else ""),
        f"⚡ CPU {cpu_total:.1f}% · 💾 RAM {fmt_mem(mem_total)}",
    ]

    sections: List[str] = []
    if problems:
        lines = [f"\n\n❌ <b>Có vấn đề ({len(problems)})</b>"]
        for c, reason in problems:
            when = uptime(c) if c.status == "running" else (
                compact_age(now - c.finished_at) + " trước" if c.finished_at else ""
            )
            lines.append(f"• <b>{html.escape(c.name)}</b> — {html.escape(reason)}" + (f" · {when}" if when else ""))
        sections.append("\n".join(lines))

    body: List[tuple[str, int]] = []  # (html, number of containers)
    shown = display_names(running)
    if mode in ("cpu", "mem"):
        def key(c: ContainerInfo) -> float:
            st = stats.get(c.name)
            value = (st.cpu_percent if mode == "cpu" else st.mem_used_mb) if st else None
            return value if value is not None else -1

        top = sorted(running, key=key, reverse=True)[:TOP_LIMIT]
        # Flat list mixes projects: tag names that would otherwise collide (api-1 ×2).
        counts: Dict[str, int] = defaultdict(int)
        for c in running:
            counts[shown[c.name]] += 1

        def flat_name(c: ContainerInfo) -> str:
            name = shown[c.name]
            return f"{_initials(c.project)}/{name}" if counts[name] > 1 and c.project else name

        title = "⚡ Theo CPU" if mode == "cpu" else "💾 Theo RAM"
        body.append((f"\n\n<b>{title}</b> (top {len(top)}/{len(running)})\n" + _table(
            [(flat_name(c), stats.get(c.name), uptime(c)) for c in top]
        ), len(top)))
    else:
        groups: Dict[str, List[ContainerInfo]] = defaultdict(list)
        for c in running:
            groups[c.project or ""].append(c)
        # Single-container projects would each get their own table — pool them instead.
        for project in [p for p, members in groups.items() if p and len(members) == 1]:
            groups[""].extend(groups.pop(project))
        # Named compose projects first (alphabetically), the pool last.
        for project in sorted(groups, key=lambda p: (p == "", p)):
            members = sorted(groups[project], key=lambda c: c.name)
            title = f"📦 <b>{html.escape(project)}</b>" if project else "📦 <b>Khác</b>"
            names = [shown[c.name] for c in members]
            body.append((f"\n\n{title} · {len(members)}\n" + _table(
                [(n, stats.get(c.name), uptime(c)) for n, c in zip(names, members)]
            ), len(members)))

    footer = ""
    if stopped:
        names = []
        for c in sorted(stopped, key=lambda c: c.name):
            label = html.escape(c.name)
            if c.status == "exited" and c.exit_code not in (0, 137, 143):
                label += f" <i>(exit {c.exit_code})</i>"
            names.append(label)
        footer = f"\n\n⏸ <b>Đã dừng ({len(stopped)}):</b> " + ", ".join(names)

    # Assemble within Telegram's limit: header + problems always, then as many
    # running sections as fit, then the stopped list if it still fits.
    text = "\n".join(head) + "".join(sections)
    for i, (part, _) in enumerate(body):
        if len(text) + len(part) > MAX_LEN - 120:
            hidden = sum(n for _, n in body[i:])
            text += f"\n\n… còn {len(body) - i} nhóm ({hidden} container) — bấm ⚡ CPU / 💾 RAM để xem top"
            break
        text += part
    if footer:
        text += footer if len(text) + len(footer) <= MAX_LEN else f"\n⏸ <b>Đã dừng:</b> {len(stopped)} container"
    return text
