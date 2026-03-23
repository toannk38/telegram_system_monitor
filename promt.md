# Prompt cho AI Agentic Coding: System Monitor + Telegram Bot Service

> **Mục đích:** Dùng prompt này để yêu cầu AI agentic coding (Cursor, Claude Code, Windsurf, Kiro, v.v.) lập kế hoạch và triển khai toàn bộ service monitor hệ thống tích hợp Telegram bot.

---

## Tài liệu tham khảo (đính kèm cùng prompt)

Đính kèm hai file sau vào context trước khi chạy prompt:
- `monitor.py` — service monitor gốc (chỉ dùng tham chiếu, không còn sử dụng)
- `bot.py` — Telegram bot gốc (chỉ dùng tham chiếu, không còn sử dụng)

---

## PROMPT

```
# Nhiệm vụ: Xây dựng System Monitor Service + Telegram Bot

## Mô tả tổng quan

Xây dựng một Python service hoàn chỉnh gồm hai thành phần chạy song song:
1. **System Monitor**: Thu thập metrics hệ thống định kỳ, ghi log CSV, gửi alert Telegram khi vượt ngưỡng.
2. **Telegram Bot**: Bot nhận lệnh từ người dùng để xem trạng thái, biểu đồ, chạy bash, quản lý quyền user.

Dựa trên hai file tham chiếu đính kèm (`monitor.py` và `bot.py`) nhưng **loại bỏ hoàn toàn API gateway** — gọi Telegram API trực tiếp qua thư viện `python-telegram-bot`.

---

## Phần 1: System Monitor Service

### 1.1 Metrics cần thu thập mỗi chu kỳ

| Metric | Thư viện | Xử lý khi không hỗ trợ |
|---|---|---|
| CPU usage (%) | `psutil.cpu_percent()` | Bắt buộc |
| RAM usage (%) và used/total (MB) | `psutil.virtual_memory()` | Bắt buộc |
| CPU temperature (°C) | `psutil.sensors_temperatures()` | Ghi `null` nếu không hỗ trợ |

### 1.2 Cấu hình qua file `.env`

```env
# === Telegram Configuration ===
# Token của Telegram Bot (lấy từ @BotFather)
BOT_TOKEN=your_bot_token_here

# Danh sách Telegram user ID nhận alert tự động từ monitor (phân cách bằng dấu phẩy)
ALERT_CHAT_IDS=user_id1,user_id2,user_id3

# Danh sách Telegram user ID được phép tương tác với bot (phân cách bằng dấu phẩy)
BOT_ALLOWED_USERS=user_id1,user_id2,user_id3

# Danh sách Telegram user ID có quyền admin (quản lý user, chạy bash) (phân cách bằng dấu phẩy)
BOT_ADMIN_USERS=admin_user_id

# === Monitor Configuration ===
# Chu kỳ thu thập metrics (giây)
MONITOR_INTERVAL=60

# Thư mục lưu log CSV
LOG_DIR=logs

# Số ngày lưu trữ log tối đa
LOG_RETENTION_DAYS=60

# === Alert Thresholds ===
# Ngưỡng cảnh báo RAM (%)
THRESHOLD_MEMORY=80.0

# Ngưỡng cảnh báo CPU (%)
THRESHOLD_CPU=90.0

# Ngưỡng cảnh báo nhiệt độ CPU (°C)
THRESHOLD_TEMP=85.0

# Thời gian chờ giữa các lần alert cùng loại (giây)
ALERT_COOLDOWN_SECONDS=300
```

### 1.3 Log rotation

- Mỗi ngày một file CSV: `logs/system_stats_YYYYMMDD.csv`
- Rotation xảy ra đúng **lúc nửa đêm (midnight)** — không theo kích thước file
- Tự động xóa file cũ hơn `LOG_RETENTION_DAYS` ngày
- Cột CSV: `timestamp, cpu_percent, mem_percent, mem_used_mb, mem_total_mb, cpu_temp_c`
- Metric không hỗ trợ → ghi chuỗi rỗng, không được crash service

### 1.4 Alert Telegram

- Gửi alert khi bất kỳ metric nào vượt ngưỡng
- Mỗi loại metric có cooldown độc lập (CPU cooldown ≠ RAM cooldown ≠ nhiệt độ cooldown)
- Alert kèm biểu đồ 12 giờ gần nhất (CPU %, RAM %, CPU Temp °C)
- Khi service khởi động: gửi thông báo startup tới tất cả `ALERT_CHAT_IDS`

---

## Phần 2: Telegram Bot
### 2.1 Yêu cầu chung

- **Loại bỏ hoàn toàn** class `TelegramBotGatewayRequests` và mọi logic gateway từ code gốc
- Gọi Telegram API trực tiếp thông qua `python-telegram-bot` (phiên bản 20+, async)
- Bot và monitor service **chạy song song** trong cùng một tiến trình bằng `asyncio`

### 2.2 Hệ thống phân quyền

**Ba cấp độ quyền:**

| Cấp | Biến env | Quyền |
|---|---|---|
| Admin | `BOT_ADMIN_USERS` | Toàn quyền: thêm/xóa authorized user (lệnh cho admin), chạy bash (lệnh cho admin), xem status, xem graph |
| Authorized User | `BOT_ALLOWED_USERS` | Xem status, xem graph (và các lệnh sau này thêm vào ngoại trừ lệnh cho Admin) |
| Không được phép | — | Chỉ được dùng `/id` và `/help`, bị từ chối tất cả lệnh khác |

**Lưu ý quan trọng:**
- `ALERT_CHAT_IDS`: danh sách user nhận alert tự động từ monitor (không phải quyền tương tác bot)
- `BOT_ALLOWED_USERS`: danh sách user được phép tương tác đầy đủ với bot (không được chạy bash, thêm/xóa users)
- `BOT_ADMIN_USERS` được load từ `.env` khi khởi động, không thể thay đổi runtime
- Admin quản lý `BOT_ALLOWED_USERS` và `ALERT_CHAT_IDS` **độc lập** qua các lệnh riêng biệt (`/allow`, `/unallow`, `/addalert`, `/removealert`)
- Thay đổi runtime được persist vào `data/users.json` để giữ qua restart
- User chưa được phép vẫn dùng được `/id` và `/help` để lấy ID của mình

### 2.3 Danh sách lệnh bot

#### Lệnh công khai (mọi user, kể cả chưa được phép)

| Lệnh | Mô tả |
|---|---|
| `/start` | Hiển thị lời chào, hướng dẫn cơ bản, kèm inline keyboard (nút bấm nhanh) |
| `/id` | Xem user ID của mình (hoặc group ID nếu trong group) |
| `/help` | Hiển thị danh sách lệnh theo quyền của user, kèm inline keyboard |

#### Lệnh cho Authorized User (và Admin)

| Lệnh | Mô tả |
|---|---|
| `/status` | Xem CPU, RAM, Disk, nhiệt độ CPU, uptime hiện tại |
| `/graph [hours]` | Gửi biểu đồ N giờ gần nhất (mặc định 12, tối đa 48). Biểu đồ gồm 3 subplot: CPU %, RAM %, CPU Temp °C |
| `/ping` | Đo response time của bot |

#### Lệnh chỉ dành cho Admin

| Lệnh | Mô tả |
|---|---|
| `/bash <command>` | Thực thi lệnh bash trên server (timeout 30s), trả về stdout/stderr/exit code. Output dài hơn 4096 ký tự sẽ bị cắt |
| `/allow <user_id>` | Thêm user vào danh sách `BOT_ALLOWED_USERS` |
| `/unallow <user_id>` | Xóa user khỏi danh sách `BOT_ALLOWED_USERS` |
| `/addalert <user_id>` | Thêm user vào danh sách `ALERT_CHAT_IDS` |
| `/removealert <user_id>` | Xóa user khỏi danh sách `ALERT_CHAT_IDS` |
| `/listusers` | Xem danh sách tất cả authorized users và alert users hiện tại |

### 2.4 Bot UI: Menu Commands + Inline Keyboard

> **Lưu ý:** Telegram không hỗ trợ inline keyboard hiển thị "mặc định" cố định trong chat.
> Inline keyboard chỉ gắn vào một message cụ thể. Vì vậy dùng kết hợp 2 cách sau:

#### A. Menu Commands (hiển thị cố định ở ô chat)

Khi bot khởi động, gọi `bot.set_my_commands()` để đăng ký danh sách lệnh gợi ý.
User sẽ thấy menu này khi bấm nút "/" hoặc icon menu ở ô nhập tin nhắn.

```python
await bot.set_my_commands([
    BotCommand("start", "Hiển thị menu và nút bấm nhanh"),
    BotCommand("status", "Xem trạng thái hệ thống"),
    BotCommand("graph", "Biểu đồ 12h gần nhất"),
    BotCommand("help", "Trợ giúp"),
    BotCommand("id", "Xem user/group ID"),
    BotCommand("ping", "Đo response time"),
])
```

#### B. Inline Keyboard (hiển thị khi gõ `/start` hoặc `/help`)

Khi user gửi `/start` hoặc `/help`, bot reply message kèm **inline keyboard** gồm các nút:

| Nút | Callback data | Hành vi |
|---|---|---|
| 📊 Status | `btn_status` | Tương đương gửi `/status` |
| 📈 Graph 24h | `btn_graph_24` | Tương đương gửi `/graph 24` |
| ❓ Help | `btn_help` | Tương đương gửi `/help` |

- Sử dụng `InlineKeyboardMarkup` + `InlineKeyboardButton` từ `python-telegram-bot`
- Xử lý callback qua `CallbackQueryHandler`
- Kiểm tra quyền khi bấm nút: nếu user chưa được phép bấm Status hoặc Graph → reply "Bạn chưa được cấp quyền, gửi `/id` để lấy ID rồi liên hệ admin"
- Nút Help cho mọi user

### 2.5 Yêu cầu biểu đồ (`/graph`)

Biểu đồ phải có **3 subplot xếp dọc**:
1. CPU Usage (%) — màu xanh dương, đường ngưỡng `THRESHOLD_CPU` màu cam nét đứt
2. RAM Usage (%) — màu đỏ, đường ngưỡng `THRESHOLD_MEMORY` màu cam nét đứt
3. CPU Temperature (°C) — màu tím, đường ngưỡng `THRESHOLD_TEMP` màu cam nét đứt (ẩn subplot này nếu không có dữ liệu nhiệt độ)

Trục X format: `MM-DD HH:MM`, xoay 45°. Lưu ảnh tạm, gửi xong thì xóa.

---
## Phần 3: Cấu trúc thư mục yêu cầu
```
project/
├── monitor/
│   ├── __init__.py
│   ├── config.py          # Load toàn bộ env vars, hằng số
│   ├── collector.py       # MetricsCollector — psutil, platform detection
│   ├── storage.py         # CSVLogger — ghi, rotate, cleanup
│   ├── alerting.py        # AlertManager — kiểm tra ngưỡng, cooldown per metric
│   └── grapher.py         # GraphGenerator — tạo biểu đồ matplotlib
├── bot/
│   ├── __init__.py
│   ├── handlers.py        # Tất cả command handlers
│   ├── permissions.py     # Kiểm tra quyền, quản lý user runtime
│   └── graph_sender.py    # Tạo và gửi graph qua bot
├── data/
│   └── users.json         # Lưu authorized users + alert users (persist qua restart)
├── logs/                  # CSV logs tự động tạo
├── main.py                # Entry point — chạy monitor + bot song song
├── .env.example
├── requirements.txt
└── monitor.service        # Systemd unit file mẫu
```

---

## Phần 4: Yêu cầu kỹ thuật

### Error handling

- Mỗi subsystem (collector, storage, alerting, bot handler) phải **fail độc lập** — lỗi ở một module không được crash toàn bộ service
- Log tất cả lỗi với full stack trace bằng Python `logging`
- Bot handler gặp exception → reply lỗi cho user, không crash

### Chạy song song

```python
# main.py — asyncio concurrent tasks
async def main():
    monitor_task = asyncio.create_task(monitor.run())
    bot_task = asyncio.create_task(bot.run())
    await asyncio.gather(monitor_task, bot_task)
```

### Format `data/users.json`

```json
{
  "allowed_users": ["123456789", "987654321"],
  "alert_chat_ids": ["123456789", "555555555"]
}
```

- Khi khởi động: merge danh sách từ `.env` + `users.json` (union, không trùng lặp)
- Khi admin thêm/xóa user runtime → cập nhật file này
- `BOT_ADMIN_USERS` chỉ load từ `.env`, không lưu vào `users.json`

### Entry point

```bash
python -m main
# hoặc
python main.py
```

---

## Phần 5: Deliverables — Kế hoạch triển khai

**Trước khi viết code**, hãy xuất ra một kế hoạch đánh số gồm các mục sau:

1. Sơ đồ luồng dữ liệu: thu thập → lưu trữ → kiểm tra ngưỡng → alert
2. Cách implement log rotation đúng lúc midnight (dùng scheduler hay check-on-write?)
3. Cách track cooldown per metric độc lập
4. Chiến lược phát hiện platform cho nhiệt độ CPU (Linux vs macOS vs Windows)
5. Cách chạy monitor + bot song song bằng asyncio mà không bị block nhau
6. Cách persist `BOT_ALLOWED_USERS` qua runtime mà vẫn đồng bộ với `.env`
7. Danh sách đầy đủ thư viện cần cài (`requirements.txt`)

**Sau khi có kế hoạch**, triển khai từng module theo thứ tự:
`config.py` → `collector.py` → `storage.py` → `alerting.py` → `grapher.py` → `permissions.py` → `handlers.py` → `graph_sender.py` → `main.py` → `.env.example` → `requirements.txt` → `monitor.service`

Hiển thị nội dung đầy đủ của từng file.
```
---

## Tóm tắt thay đổi so với code gốc

| Hạng mục | Code gốc | Yêu cầu mới |
|---|---|---|
| API Gateway | Có `TelegramBotGatewayRequests` | **Loại bỏ hoàn toàn** |
| Metrics | CPU, RAM | CPU, RAM, Nhiệt độ |
| Log retention | 30 ngày | **60 ngày** |
| Log rotation | Check thủ công | **Midnight rotation** |
| Alert cooldown | Chung cho tất cả | **Độc lập per metric** |
| Graph | 2 subplot (CPU, RAM) | **3 subplot (CPU, RAM, Temp)** |
| Phân quyền | `AUTHORIZED_USERS` duy nhất | **Admin + Authorized + Public** |
| Quản lý user | Tĩnh trong `.env` | **Dynamic runtime + persist** |
| Kiến trúc | Hai file riêng lẻ | **Package cấu trúc rõ ràng** |
| Entry point | Chạy riêng từng file | **Chạy song song asyncio** |

---

## Bảng ánh xạ tên biến `.env`

| Tên cũ (code gốc) | Tên mới | Lý do đổi |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `BOT_TOKEN` | Ngắn gọn hơn, prefix `TELEGRAM_` thừa vì context đã rõ |
| `TELEGRAM_ALERT_IDS` | `ALERT_CHAT_IDS` | Rõ nghĩa hơn: đây là chat ID nhận alert, không phải user ID tương tác |
| `AUTHORIZED_USERS` | `BOT_ALLOWED_USERS` | Phân biệt rõ với admin, prefix `BOT_` cho nhất quán |
| `ADMIN_USERS` | `BOT_ADMIN_USERS` | Prefix `BOT_` cho nhất quán với nhóm biến bot |
| `CHECK_INTERVAL` | `MONITOR_INTERVAL` | Rõ ràng hơn: interval của monitor, không phải check chung chung |
| `MAX_DAYS_RETENTION` | `LOG_RETENTION_DAYS` | Rõ nghĩa: số ngày giữ log, đơn vị nằm trong tên |
| `MEMORY_THRESHOLD` | `THRESHOLD_MEMORY` | Prefix `THRESHOLD_` nhóm các ngưỡng lại với nhau |
| `CPU_THRESHOLD` | `THRESHOLD_CPU` | Prefix `THRESHOLD_` nhóm các ngưỡng lại với nhau |
| `TEMP_THRESHOLD` | `THRESHOLD_TEMP` | Prefix `THRESHOLD_` nhóm các ngưỡng lại với nhau |
| `ALERT_COOLDOWN` | `ALERT_COOLDOWN_SECONDS` | Thêm đơn vị `_SECONDS` để tránh nhầm lẫn |
