# Requirements Document

## Introduction

Xây dựng một Python service hoàn chỉnh gồm hai thành phần chạy song song trong cùng một tiến trình bằng `asyncio`:

1. **System Monitor**: Thu thập metrics hệ thống (CPU, RAM, nhiệt độ CPU) định kỳ, ghi log CSV với daily rotation, gửi alert Telegram khi vượt ngưỡng.
2. **Telegram Bot**: Bot nhận lệnh từ người dùng qua `python-telegram-bot` (v20+, async) để xem trạng thái, biểu đồ, chạy bash, quản lý quyền user.

Loại bỏ hoàn toàn API gateway — gọi Telegram API trực tiếp qua thư viện `python-telegram-bot`.

## Glossary

- **Monitor**: Thành phần thu thập metrics hệ thống định kỳ, ghi log CSV, và gửi alert khi vượt ngưỡng
- **Bot**: Thành phần Telegram Bot nhận lệnh từ người dùng, sử dụng thư viện `python-telegram-bot` v20+ async
- **MetricsCollector**: Module thu thập CPU usage, RAM usage, và CPU temperature từ `psutil`
- **CSVLogger**: Module ghi metrics ra file CSV với daily rotation và cleanup
- **AlertManager**: Module kiểm tra ngưỡng metrics, quản lý cooldown per metric, và gửi alert Telegram
- **GraphGenerator**: Module tạo biểu đồ matplotlib từ dữ liệu CSV
- **PermissionManager**: Module quản lý phân quyền 3 cấp (Admin, Authorized, Public) và persist runtime changes
- **Admin**: User có ID trong `BOT_ADMIN_USERS`, có toàn quyền bao gồm quản lý user và chạy bash
- **Authorized_User**: User có ID trong `BOT_ALLOWED_USERS`, được xem status và graph
- **Public_User**: User không có quyền, chỉ dùng được `/id` và `/help`
- **Alert_Recipient**: User có ID trong `ALERT_CHAT_IDS`, nhận alert tự động từ Monitor
- **Cooldown**: Khoảng thời gian chờ giữa hai lần alert cùng loại metric
- **Daily_Rotation**: Cơ chế tạo file CSV mới mỗi ngày vào lúc nửa đêm

## Requirements

### Requirement 1: Thu thập Metrics hệ thống

**User Story:** As a system administrator, I want the Monitor to collect system metrics periodically, so that I can track system health over time.

#### Acceptance Criteria

1. WHEN a monitoring cycle occurs, THE MetricsCollector SHALL collect CPU usage percentage using `psutil.cpu_percent()`
2. WHEN a monitoring cycle occurs, THE MetricsCollector SHALL collect RAM usage percentage and used/total in MB using `psutil.virtual_memory()`
3. WHEN a monitoring cycle occurs, THE MetricsCollector SHALL attempt to collect CPU temperature using `psutil.sensors_temperatures()`
4. WHEN CPU temperature is not supported by the platform, THE MetricsCollector SHALL return a null value for temperature without raising an exception
5. THE Monitor SHALL execute a monitoring cycle every `MONITOR_INTERVAL` seconds as configured in the `.env` file

### Requirement 2: Ghi log CSV với Daily Rotation

**User Story:** As a system administrator, I want metrics to be logged to daily CSV files with automatic rotation, so that I can review historical data without manual file management.

#### Acceptance Criteria

1. THE CSVLogger SHALL write each metrics record as a row with columns: `timestamp, cpu_percent, mem_percent, mem_used_mb, mem_total_mb, cpu_temp_c`
2. THE CSVLogger SHALL create a new CSV file named `logs/system_stats_YYYYMMDD.csv` for each calendar day
3. WHEN the date changes at midnight, THE CSVLogger SHALL begin writing to a new daily file
4. WHEN a metric value is not available, THE CSVLogger SHALL write an empty string for that column
5. THE CSVLogger SHALL automatically delete CSV files older than `LOG_RETENTION_DAYS` days
6. IF the CSVLogger encounters a write error, THEN THE CSVLogger SHALL log the error and continue operation without crashing the Monitor

### Requirement 3: Alert Telegram khi vượt ngưỡng

**User Story:** As a system administrator, I want to receive Telegram alerts when system metrics exceed thresholds, so that I can respond to issues promptly.

#### Acceptance Criteria

1. WHEN any metric (CPU, RAM, or temperature) exceeds its configured threshold, THE AlertManager SHALL send an alert message to all `ALERT_CHAT_IDS`
2. THE AlertManager SHALL maintain independent cooldown timers for each metric type (CPU, RAM, temperature)
3. WHILE a cooldown timer for a specific metric is active, THE AlertManager SHALL suppress additional alerts for that same metric
4. WHEN an alert is triggered, THE AlertManager SHALL include a 12-hour graph showing CPU %, RAM %, and CPU Temp °C
5. WHEN the Monitor service starts, THE AlertManager SHALL send a startup notification to all `ALERT_CHAT_IDS`

### Requirement 4: Cấu hình qua file `.env`

**User Story:** As a system administrator, I want all configuration to be loaded from a `.env` file, so that I can adjust settings without modifying code.

#### Acceptance Criteria

1. THE Config module SHALL load all configuration values from the `.env` file at startup
2. THE Config module SHALL provide default values for: `MONITOR_INTERVAL` (60), `LOG_DIR` ("logs"), `LOG_RETENTION_DAYS` (60), `THRESHOLD_MEMORY` (80.0), `THRESHOLD_CPU` (90.0), `THRESHOLD_TEMP` (85.0), `ALERT_COOLDOWN_SECONDS` (300)
3. THE Config module SHALL parse comma-separated user ID lists for `ALERT_CHAT_IDS`, `BOT_ALLOWED_USERS`, and `BOT_ADMIN_USERS`

### Requirement 5: Hệ thống phân quyền 3 cấp

**User Story:** As a bot operator, I want a three-tier permission system, so that I can control who can access which bot features.

#### Acceptance Criteria

1. THE PermissionManager SHALL classify users into three tiers: Admin (from `BOT_ADMIN_USERS`), Authorized_User (from `BOT_ALLOWED_USERS`), and Public_User (all others)
2. WHEN a Public_User sends a command other than `/start`, `/id`, or `/help`, THE Bot SHALL reply with a denial message
3. WHEN an Authorized_User sends an admin-only command (`/bash`, `/allow`, `/unallow`, `/addalert`, `/removealert`, `/listusers`), THE Bot SHALL reply with a denial message
4. THE PermissionManager SHALL load `BOT_ADMIN_USERS` from `.env` at startup and treat the list as immutable at runtime
5. WHEN the service starts, THE PermissionManager SHALL merge user lists from `.env` and `data/users.json` using a union operation without duplicates
6. WHEN an Admin adds or removes a user at runtime, THE PermissionManager SHALL persist the updated lists to `data/users.json`
7. THE PermissionManager SHALL store `data/users.json` in the format: `{"allowed_users": [...], "alert_chat_ids": [...]}`

### Requirement 6: Lệnh công khai cho mọi user

**User Story:** As any Telegram user, I want to use basic commands to get my ID and see help, so that I can identify myself and learn about the bot.

#### Acceptance Criteria

1. WHEN any user sends `/start`, THE Bot SHALL reply with a greeting message, basic instructions, and an inline keyboard with quick-action buttons
2. WHEN any user sends `/id` in a private chat, THE Bot SHALL reply with the user's Telegram user ID
3. WHEN any user sends `/id` in a group chat, THE Bot SHALL reply with both the group ID and the user's ID
4. WHEN any user sends `/help`, THE Bot SHALL reply with a list of commands appropriate to the user's permission level, along with an inline keyboard

### Requirement 7: Lệnh cho Authorized User

**User Story:** As an authorized user, I want to view system status and graphs, so that I can monitor the server remotely.

#### Acceptance Criteria

1. WHEN an Authorized_User sends `/status`, THE Bot SHALL reply with current CPU usage, RAM usage, disk usage, CPU temperature, and system uptime
2. WHEN an Authorized_User sends `/graph [hours]`, THE Bot SHALL generate and send a graph for the specified number of hours (default 12, maximum 48)
3. WHEN the hours parameter for `/graph` is less than 1 or greater than 48, THE Bot SHALL reply with an error message
4. WHEN an Authorized_User sends `/ping`, THE Bot SHALL measure and reply with the bot's response time in milliseconds

### Requirement 8: Lệnh Admin

**User Story:** As an admin, I want to execute bash commands and manage user permissions remotely, so that I can administer the server and bot access.

#### Acceptance Criteria

1. WHEN an Admin sends `/bash <command>`, THE Bot SHALL execute the command with a 30-second timeout and reply with stdout, stderr, and exit code
2. WHEN bash command output exceeds 4096 characters, THE Bot SHALL truncate the output
3. WHEN an Admin sends `/allow <user_id>`, THE PermissionManager SHALL add the user to `BOT_ALLOWED_USERS` and persist the change
4. WHEN an Admin sends `/unallow <user_id>`, THE PermissionManager SHALL remove the user from `BOT_ALLOWED_USERS` and persist the change
5. WHEN an Admin sends `/addalert <user_id>`, THE PermissionManager SHALL add the user to `ALERT_CHAT_IDS` and persist the change
6. WHEN an Admin sends `/removealert <user_id>`, THE PermissionManager SHALL remove the user from `ALERT_CHAT_IDS` and persist the change
7. WHEN an Admin sends `/listusers`, THE Bot SHALL reply with the current lists of authorized users and alert recipients

### Requirement 9: Biểu đồ hệ thống

**User Story:** As a user viewing system graphs, I want clear multi-metric charts with threshold indicators, so that I can quickly assess system health.

#### Acceptance Criteria

1. THE GraphGenerator SHALL produce a figure with 3 vertically stacked subplots: CPU Usage (%), RAM Usage (%), and CPU Temperature (°C)
2. THE GraphGenerator SHALL draw CPU data in blue, RAM data in red, and temperature data in purple
3. THE GraphGenerator SHALL draw a dashed orange threshold line on each subplot at the configured threshold value
4. WHEN no temperature data is available, THE GraphGenerator SHALL hide the temperature subplot
5. THE GraphGenerator SHALL format the X-axis as `MM-DD HH:MM` with labels rotated 45 degrees
6. WHEN a graph image has been sent, THE GraphGenerator SHALL delete the temporary image file

### Requirement 10: Bot UI — Menu Commands và Inline Keyboard

**User Story:** As a Telegram user, I want menu commands and inline buttons, so that I can interact with the bot conveniently.

#### Acceptance Criteria

1. WHEN the Bot starts, THE Bot SHALL register menu commands via `bot.set_my_commands()` with: start, status, graph, help, id, ping
2. WHEN a user sends `/start` or `/help`, THE Bot SHALL include an inline keyboard with buttons: 📊 Status (`btn_status`), 📈 Graph 24h (`btn_graph_24`), ❓ Help (`btn_help`)
3. WHEN an unauthorized user presses the Status or Graph inline button, THE Bot SHALL reply with a denial message instructing them to use `/id` and contact an admin
4. WHEN any user presses the Help inline button, THE Bot SHALL display the help message

### Requirement 11: Chạy song song và Error Handling

**User Story:** As a system administrator, I want the Monitor and Bot to run concurrently and handle errors independently, so that a failure in one component does not crash the other.

#### Acceptance Criteria

1. THE main entry point SHALL run the Monitor and Bot as concurrent asyncio tasks using `asyncio.gather()`
2. IF the Monitor encounters an error during a cycle, THEN THE Monitor SHALL log the error with full stack trace and continue to the next cycle
3. IF a Bot command handler encounters an exception, THEN THE Bot SHALL reply with an error message to the user and continue operation
4. IF a subsystem (MetricsCollector, CSVLogger, AlertManager, or Bot handler) raises an exception, THEN THE service SHALL log the error and continue running other subsystems
