# System Monitor Bot

Giám sát máy chủ (CPU, RAM, Disk, Network, nhiệt độ, Docker, systemd service, endpoint) và gửi cảnh báo
qua **Telegram**. Bạn có thể xem trạng thái, biểu đồ, báo cáo và chạy lệnh quản trị ngay trong khung chat.

## Tính năng

- 📊 **Metrics hệ thống**: CPU, load average, RAM, swap, Disk, Disk I/O, Network I/O, nhiệt độ CPU/GPU.
- 🎯 **Chống spike**: lấy mẫu phụ mỗi 5 giây rồi gộp theo cửa sổ 60 giây (avg để cảnh báo, max để thấy spike).
- 🔕 **Cảnh báo không spam**: chỉ gửi khi trạng thái thay đổi; có hysteresis, nhắc lại thưa dần, báo **hồi phục**,
  và `/mute` khi bảo trì. Ví dụ RAM đầy 12 giờ chỉ tạo 5 tin, thay vì hơn 100.
- 🐳 **Giám sát Docker** (bật/tắt bằng ENV): CPU/RAM và log từng container; cảnh báo unhealthy, crash loop, OOM,
  exit lỗi (chọn loại cảnh báo bằng `DOCKER_ALERT`).
  Bot truy cập Docker qua **socket proxy chỉ cho phép đọc**.
- ⚙️ **systemd service**: cảnh báo khi service `failed`/`inactive`.
- 🌐 **Endpoint**: kiểm tra HTTP(S), TCP port, ping và cảnh báo khi down.
- 📋 **Báo cáo định kỳ**: min/avg/max trong 24h kèm biểu đồ, gửi mỗi sáng.
- 🔥 **Top process**: tự động gửi kèm cảnh báo CPU/RAM, hoặc xem bằng `/top`.
- 💾 **Lịch sử** lưu dạng CSV theo ngày, tự xoá file cũ, tự chuyển đổi định dạng cũ.
- 🖥 **Chạy lệnh từ xa** với `/bash`, phân quyền 3 cấp (admin / user / người lạ).

## Kiến trúc

```
main.py ── asyncio event loop
 ├─ sampling loop (5s)  MetricsCollector.sample()  → mẫu phụ (CPU avg kể từ mẫu trước, RAM, temp)
 │     mỗi 60s          MetricsCollector.flush()   → avg/max + I/O rate → CSVLogger (logs/*.csv)
 │                      AlertManager.check()       → AlertTracker ─┐
 ├─ checks loop (60s)   ChecksRunner: Docker · systemd · endpoint ─┤→ Notifier → Telegram
 ├─ report loop         báo cáo 24h lúc DAILY_REPORT_TIME ─────────┘   (mute, top process, biểu đồ)
 └─ Telegram bot        /status /graph /docker /alerts ...
```

| Thư mục / file | Nội dung |
|---|---|
| `monitor/collector.py` | Lấy mẫu và gộp metrics (psutil) |
| `monitor/alerting.py` | `AlertTracker` (máy trạng thái), `AlertManager`, `Notifier` |
| `monitor/checks.py`, `docker_monitor.py`, `services.py`, `endpoints.py` | Kiểm tra Docker / systemd / endpoint |
| `monitor/storage.py`, `grapher.py`, `report.py`, `processes.py` | CSV, biểu đồ, báo cáo, top process |
| `bot/` | Handler lệnh Telegram, phân quyền |
| `deploy/`, `scripts/`, `Dockerfile`, `docker-compose.yml` | Triển khai |
| `tests/` | Unit test (pytest) |

## Lệnh bot

Gõ `/start` hoặc `/menu` để mở **menu dạng nút bấm ngay trong khung chat**. Mỗi người chỉ thấy nút đúng với quyền của mình.
Bấm một nút thì **chính tin nhắn đó được cập nhật** sang màn hình tương ứng. Mỗi màn hình có nút riêng
(🔄 làm mới, chọn 1h/6h/12h/24h/48h, CPU/RAM, ...) và nút **☰ Menu** để quay lại. Chat luôn chỉ giữ một panel,
panel cũ được xoá khi mở panel mới.

Bàn phím dưới khung chat chỉ có hai lối tắt: **📊 Trạng thái** và **📈 Biểu đồ** (12h).
Vẫn có thể gõ lệnh như bình thường:

| Lệnh | Quyền | Mô tả |
|---|---|---|
| `/start`, `/menu` | Mọi người | Mở menu nút bấm |
| `/help`, `/id` | Mọi người | Danh sách lệnh, xem Telegram ID |
| `/status` | User | CPU, load, RAM, swap, disk, network, nhiệt độ, uptime |
| `/graph [1-48]` | User | Biểu đồ CPU (avg/max), RAM, nhiệt độ, network, disk I/O (mặc định 12h) |
| `/top [cpu\|mem]` | User | Top 10 process |
| `/alerts` | User | Cảnh báo đang mở và thời gian kéo dài |
| `/report [1-48]` | User | Báo cáo min/avg/max kèm biểu đồ |
| `/docker` | User | Trạng thái và CPU/RAM từng container *(khi bật Docker)* |
| `/services` | User | Trạng thái systemd service *(khi cấu hình)* |
| `/endpoints` | User | Kiểm tra endpoint *(khi cấu hình)* |
| `/ping` | User | Đo độ trễ bot |
| `/mute [30m\|2h\|1d]`, `/unmute` | Admin | Tạm tắt / bật lại cảnh báo |
| `/dlogs <container> [n]` | Admin | Xem log container |
| `/bash <cmd>` | Admin | Chạy lệnh shell (timeout 30s) |
| `/allow`, `/unallow <id>` | Admin | Cấp/thu quyền user |
| `/addalert`, `/removealert <id>` | Admin | Thêm/bớt người nhận cảnh báo |
| `/listusers` | Admin | Danh sách user và người nhận cảnh báo |

## Bắt đầu nhanh

Cần có [uv](https://docs.astral.sh/uv/) và một bot token lấy từ [@BotFather](https://t.me/BotFather).

```bash
cp .env.example .env     # điền BOT_TOKEN, BOT_ADMIN_USERS, ALERT_CHAT_IDS
uv sync
uv run python main.py
```

## Triển khai

| | systemd | Docker Compose |
|---|---|---|
| Lệnh | `./scripts/install-systemd.sh` | `docker compose up -d --build` |
| `/bash`, `/top` | Trên **host** | Trong container |
| Giám sát systemd service | ✅ | ❌ |
| Giám sát Docker | ✅ (proxy trên `127.0.0.1`) | ✅ (`COMPOSE_PROFILES=docker`) |
| Phù hợp khi | Cần quản trị máy qua bot | Chỉ cần giám sát và cảnh báo |

👉 **Hướng dẫn chi tiết (cấu hình, cơ chế cảnh báo, vận hành, xử lý sự cố): [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**

## Cấu hình

Toàn bộ cấu hình nằm trong `.env`. Danh sách đầy đủ có chú thích nằm trong [`.env.example`](.env.example) và
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#biến-cấu-hình-env). Một số biến hay dùng:

```ini
THRESHOLD_CPU=90
THRESHOLD_MEMORY=80
THRESHOLD_DISK=90
DAILY_REPORT_TIME=08:00
DOCKER_MONITOR=true
DOCKER_ALERT=unhealthy,crashloop,oom,exit   # hoặc true / false
SYSTEMD_SERVICES=nginx,postgresql
ENDPOINTS=web=https://example.com,db=tcp://10.0.0.5:5432,gw=ping://192.168.1.1
```

## Phát triển

```bash
uv sync              # gồm cả pytest, hypothesis
uv run pytest -q
```

## Bảo mật

- `/bash` cho phép admin chạy lệnh tuỳ ý với quyền của user chạy bot. Hãy giữ kín `BOT_TOKEN`,
  không commit `.env`, chỉ đưa ID tin cậy vào `BOT_ADMIN_USERS`, và chạy service bằng user ít quyền.
- Bot **không** truy cập trực tiếp `docker.sock` (vốn tương đương quyền root). Bot dùng
  [docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy), proxy này chỉ cho phép đọc
  thông tin container, không thể start/stop/exec.
- `/dlogs` chỉ dành cho admin vì log có thể chứa thông tin nhạy cảm.
