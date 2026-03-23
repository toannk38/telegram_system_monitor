# Implementation Plan: System Monitor + Telegram Bot

## Overview

Triển khai từng module theo thứ tự dependency: config → collector → storage → grapher → alerting → permissions → handlers → graph_sender → main. Mỗi bước build trên bước trước, kết thúc bằng wiring tất cả trong `main.py`.

## Tasks

- [x] 1. Set up project structure and config module
  - [x] 1.1 Create directory structure (`monitor/`, `bot/`, `data/`, `logs/`) with `__init__.py` files, `.env.example`, and `requirements.txt`
    - `requirements.txt`: python-telegram-bot>=20.0, psutil, python-dotenv, matplotlib, pytest, hypothesis
    - `.env.example`: tất cả biến env với giá trị mẫu
    - _Requirements: 4.1, 4.2, 4.3_
  - [x] 1.2 Implement `monitor/config.py` — Config dataclass, load from env, parse comma-separated lists, default values
    - Load `.env` via `python-dotenv`
    - Parse `ALERT_CHAT_IDS`, `BOT_ALLOWED_USERS`, `BOT_ADMIN_USERS` as comma-separated lists
    - Provide defaults: MONITOR_INTERVAL=60, LOG_DIR="logs", LOG_RETENTION_DAYS=60, THRESHOLD_MEMORY=80.0, THRESHOLD_CPU=90.0, THRESHOLD_TEMP=85.0, ALERT_COOLDOWN_SECONDS=300
    - _Requirements: 4.1, 4.2, 4.3_
  - [ ]* 1.3 Write property test for Config parsing
    - **Property 8: Config parsing with defaults**
    - **Validates: Requirements 4.1, 4.2, 4.3**

- [x] 2. Implement MetricsCollector
  - [x] 2.1 Implement `monitor/collector.py` — SystemMetrics dataclass and MetricsCollector class
    - `SystemMetrics`: timestamp, cpu_percent, mem_percent, mem_used_mb, mem_total_mb, cpu_temp_c (Optional[float])
    - `collect()`: use psutil, platform detection for temperature (Linux coretemp/cpu_thermal, fallback None)
    - Wrap temperature collection in try/except, return None on failure
    - _Requirements: 1.1, 1.2, 1.3, 1.4_
  - [ ]* 2.2 Write property test for MetricsCollector output validity
    - **Property 1: MetricsCollector output validity**
    - **Validates: Requirements 1.1, 1.2, 1.3, 1.4**

- [x] 3. Implement CSVLogger
  - [x] 3.1 Implement `monitor/storage.py` — CSVLogger class
    - `write(metrics)`: append to daily CSV file, create with header if new
    - Daily rotation via check-on-write (compare current date with file date)
    - `cleanup_old_files(retention_days)`: delete files older than retention period
    - `read_recent(hours)`: read data from recent CSV files within time window
    - None values → empty string in CSV
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_
  - [ ]* 3.2 Write property tests for CSVLogger
    - **Property 2: CSV write/read round-trip**
    - **Property 3: Daily file rotation by date**
    - **Property 4: None metric serialized as empty string**
    - **Property 5: Log cleanup respects retention period**
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5**

- [x] 4. Implement GraphGenerator
  - [x] 4.1 Implement `monitor/grapher.py` — GraphGenerator class
    - `generate(data, hours, config)`: create matplotlib figure with 3 subplots (CPU blue, RAM red, Temp purple)
    - Threshold lines: dashed orange on each subplot
    - Hide temp subplot when no temp data available
    - X-axis format: MM-DD HH:MM, rotated 45°
    - Save to temp file, return Path
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_
  - [ ]* 4.2 Write property test for graph subplot count
    - **Property 14: Graph subplot count based on temperature data**
    - **Validates: Requirements 9.1, 9.4**

- [x] 5. Implement AlertManager
  - [x] 5.1 Implement `monitor/alerting.py` — AlertManager class
    - `check_and_alert(metrics)`: check each metric against threshold, respect per-metric cooldown
    - Independent cooldown tracking: `_last_alert` dict with keys "cpu", "memory", "temperature"
    - `send_startup_notification()`: send startup message to all ALERT_CHAT_IDS
    - Use bot instance to send messages and photos
    - Include 12h graph with alerts
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_
  - [ ]* 5.2 Write property tests for AlertManager
    - **Property 6: Alert threshold detection**
    - **Property 7: Independent cooldown per metric**
    - **Validates: Requirements 3.1, 3.2, 3.3**

- [x] 6. Checkpoint — Core monitor modules
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Implement PermissionManager
  - [x] 7.1 Implement `bot/permissions.py` — PermissionManager class
    - Three-tier classification: Admin, Authorized, Public
    - Load admin list from env (immutable)
    - Merge allowed_users and alert_chat_ids from env + users.json (union, no duplicates)
    - `add_allowed_user()`, `remove_allowed_user()`, `add_alert_recipient()`, `remove_alert_recipient()`
    - Persist changes to `data/users.json`
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_
  - [ ]* 7.2 Write property tests for PermissionManager
    - **Property 9: Permission tier classification**
    - **Property 10: User list merge is union without duplicates**
    - **Property 11: User persistence round-trip**
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.5, 5.6, 5.7**

- [x] 8. Implement Bot handlers and graph sender
  - [x] 8.1 Implement `bot/handlers.py` — all command handlers and callback handler
    - Public commands: start_command (greeting + inline keyboard), id_command, help_command
    - Authorized commands: status_command, graph_command (validate hours 1-48), ping_command
    - Admin commands: bash_command (asyncio subprocess, 30s timeout, truncate at 4096), allow_command, unallow_command, addalert_command, removealert_command, listusers_command
    - Callback handler: button_callback for inline keyboard (btn_status, btn_graph_24, btn_help)
    - Permission check before each restricted command
    - Error handling: try/except in each handler, reply error to user
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 7.1, 7.2, 7.3, 7.4, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 10.2, 10.3, 10.4_
  - [x] 8.2 Implement `bot/graph_sender.py` — send_graph function
    - Read data from CSVLogger → generate graph → send photo via bot → delete temp file
    - _Requirements: 9.6_
  - [ ]* 8.3 Write property tests for handlers
    - **Property 12: Graph hours validation**
    - **Property 13: Bash output truncation**
    - **Validates: Requirements 7.2, 7.3, 8.2**

- [x] 9. Checkpoint — Bot modules
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Wire everything together in main.py
  - [x] 10.1 Implement `main.py` — entry point
    - Load config
    - Init CSVLogger, PermissionManager, MetricsCollector
    - Build Application, register handlers, set_my_commands
    - Manual bot lifecycle: initialize → start → start_polling
    - Send startup notification
    - Run monitor loop as main task
    - Graceful shutdown in finally block
    - _Requirements: 10.1, 11.1, 11.2, 11.3, 11.4_
  - [x] 10.2 Create `monitor.service` — systemd unit file
    - _Requirements: 11.1_

- [x] 11. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties using `hypothesis`
- Unit tests validate specific examples and edge cases using `pytest`
