# Hướng dẫn triển khai System Monitor Bot

Có 2 cách chạy:

| | **1. systemd** (khuyến nghị) | **2. Docker Compose** |
|---|---|---|
| `/bash` chạy ở đâu | **Trên host**, với quyền user chạy service | **Trong container** (không đụng tới host) |
| CPU / RAM / Disk / Temp / Disk IO | Host | Host (qua `/proc`, `/sys`, `/host`) |
| Network I/O | Host | Host (qua `NET_DEV_PATH`) |
| `/top`, top process trong cảnh báo | Process của host | Chỉ process trong container, trừ khi bật `pid: host` |
| Giám sát Docker | ✅ (qua socket proxy) | ✅ (qua socket proxy, profile `docker`) |
| Giám sát systemd service | ✅ | ❌ (container không có `systemctl`) |
| Endpoint, báo cáo, mute | ✅ | ✅ |
| Phụ thuộc | `uv` | Docker + Compose v2.20+ |

> Nếu cần dùng `/bash` để quản trị máy, theo dõi systemd service, hoặc xem đúng process của host,
> hãy dùng **systemd**. Docker phù hợp khi chỉ cần giám sát và cảnh báo.

---

## 0. Chuẩn bị chung

```bash
git clone <repo> system_monitor && cd system_monitor
cp .env.example .env
nano .env        # tối thiểu: BOT_TOKEN, BOT_ADMIN_USERS, ALERT_CHAT_IDS
```

Lấy Telegram ID: chat với bot, gõ `/id`.

### Biến cấu hình (`.env`)

**Telegram**

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `BOT_TOKEN` | — | Token từ @BotFather (bắt buộc) |
| `BOT_ADMIN_USERS` | — | ID admin, phân tách bằng dấu phẩy (được dùng `/bash`, `/mute`, `/dlogs`...) |
| `BOT_ALLOWED_USERS` | — | ID được xem `/status`, `/graph`, `/docker`... |
| `ALERT_CHAT_IDS` | — | Người/nhóm nhận cảnh báo (thêm/bớt bằng `/addalert`, `/removealert`) |

**Thu thập & lưu trữ**

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `SAMPLE_INTERVAL` | `5` | Lấy mẫu phụ mỗi N giây |
| `MONITOR_INTERVAL` | `60` | Gộp các mẫu phụ (avg + max) thành 1 dòng CSV mỗi N giây; đồng thời là chu kỳ kiểm tra Docker, service, endpoint |
| `LOG_DIR` | `logs` | Thư mục CSV |
| `DATA_DIR` | `data` | Thư mục `users.json` |
| `LOG_RETENTION_DAYS` | `60` | Số ngày giữ CSV |
| `DISK_PATH` | `/` | Phân vùng đo dung lượng (Compose đặt `/host`) |
| `NET_DEV_PATH` | trống | File thống kê mạng thay cho psutil (Compose đặt `/host/proc/1/net/dev`) |

**Ngưỡng và cách gửi cảnh báo**

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `THRESHOLD_CPU` / `THRESHOLD_MEMORY` / `THRESHOLD_DISK` / `THRESHOLD_TEMP` | `90` / `80` / `90` / `85` | Ngưỡng |
| `ALERT_CONSECUTIVE` | `3` | Số chu kỳ liên tiếp vượt ngưỡng thì mới cảnh báo |
| `RECOVERY_CONSECUTIVE` | `3` | Số chu kỳ liên tiếp ổn định thì mới báo hồi phục |
| `ALERT_HYSTERESIS` | `5` | Chỉ coi là hồi phục khi giá trị xuống dưới `ngưỡng − 5` |
| `ALERT_REMIND_MINUTES` | `60` | Nhắc lại khi sự cố kéo dài, khoảng nhắc tăng gấp đôi (60 → 120 → 240...); `0` = tắt |
| `ALERT_REMIND_MAX_MINUTES` | `480` | Khoảng nhắc tối đa |
| `DAILY_REPORT_TIME` | trống | Giờ gửi báo cáo 24h mỗi ngày, ví dụ `08:00`; trống = tắt |

**Tích hợp (tuỳ chọn)**

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `DOCKER_MONITOR` | `false` | Bật kết nối Docker (lệnh `/docker`, `/dlogs`) |
| `DOCKER_ALERT` | `true` | Loại sự cố gây cảnh báo: `true`/`all`, `false`, hoặc danh sách `unhealthy,crashloop,oom,exit` (xem [3.3](#33-docker-khi-nào-thì-cảnh-báo)) |
| `DOCKER_API_URL` | `unix:///var/run/docker.sock` | Địa chỉ Docker API (nên trỏ vào socket proxy) |
| `DOCKER_WATCH` | trống | Chỉ theo dõi các container này (trống = tất cả) |
| `DOCKER_IGNORE` | trống | Bỏ qua các container này |
| `DOCKER_RESTART_WINDOW_MINUTES` | `10` | Cảnh báo nếu container restart trong khoảng thời gian này |
| `SYSTEMD_SERVICES` | trống | Ví dụ `nginx,postgresql` (chỉ dùng được ở chế độ systemd) |
| `ENDPOINTS` | trống | `name=target` phân tách bằng dấu phẩy; hỗ trợ `http(s)://`, `tcp://host:port`, `ping://host` |
| `ENDPOINT_TIMEOUT` | `10` | Timeout (giây) cho mỗi lần kiểm tra endpoint |

Có thể bỏ qua một container mà không cần sửa `.env` bằng cách gắn label `system-monitor.ignore=true` cho nó.

---

## 1. Chạy bằng systemd

### 1.1. Cài uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# mở terminal mới hoặc: source ~/.local/bin/env
uv --version
```

### 1.2. Chạy thử thủ công

```bash
uv sync                 # tạo .venv từ uv.lock
uv run python main.py   # Ctrl+C để dừng
```

### 1.3. Cài service

```bash
./scripts/install-systemd.sh
```

Script sẽ:
1. Kiểm tra `.env` và `uv`.
2. `uv sync --frozen --no-dev` để tạo `.venv`.
3. Tạo `/etc/systemd/system/system-monitor.service` từ
   `deploy/systemd/system-monitor.service.template` (điền user và đường dẫn hiện tại).
4. `enable` và `restart` service.

Tuỳ chọn:

```bash
RUN_USER=monitor ./scripts/install-systemd.sh       # chạy service bằng user khác
SERVICE_NAME=sysmon-prod ./scripts/install-systemd.sh
./scripts/install-systemd.sh --uninstall            # gỡ service (giữ logs/data)
```

> **Quyền của `/bash`:** lệnh chạy với quyền của `RUN_USER`. Nếu cần `sudo` không hỏi
> mật khẩu cho một vài lệnh, thêm rule giới hạn trong `/etc/sudoers.d/`, ví dụ:
> ```
> monitor ALL=(root) NOPASSWD: /usr/bin/systemctl restart nginx
> ```
> Không nên cho `NOPASSWD: ALL`, vì token bot bị lộ đồng nghĩa với mất quyền root.

### 1.4. Bật giám sát Docker (systemd)

Không nên cho user chạy bot vào group `docker`, vì quyền truy cập `docker.sock` tương đương quyền root.
Thay vào đó, chạy một socket proxy **chỉ cho phép đọc**, lắng nghe trên `127.0.0.1`:

```bash
docker compose -f deploy/docker-socket-proxy/docker-compose.yml up -d
```

Trong `.env`:

```ini
DOCKER_MONITOR=true
DOCKER_API_URL=tcp://127.0.0.1:2375
```

Rồi restart: `sudo systemctl restart system-monitor`.

### 1.5. Giám sát systemd service

```ini
SYSTEMD_SERVICES=nginx,postgresql,docker
```

Bot cảnh báo khi service ở trạng thái `failed` hoặc `inactive`. Khi service đang `activating`/`reloading`,
bot giữ nguyên trạng thái cảnh báo hiện tại. Tên unit không tồn tại vẫn hiện trong `/services` nhưng không gây cảnh báo.

### 1.6. Vận hành

```bash
sudo systemctl status system-monitor
sudo systemctl restart system-monitor
journalctl -u system-monitor -f            # log realtime
journalctl -u system-monitor --since today
```

Cập nhật code:

```bash
git pull
./scripts/install-systemd.sh    # sync lại dependencies + restart
```

---

## 2. Chạy bằng Docker Compose

### 2.1. Chuẩn bị thư mục

Container chạy bằng user không phải root (mặc định UID/GID `1000`). Hãy tạo sẵn thư mục để chúng
thuộc user của bạn. Nếu để Docker tự tạo thì thư mục sẽ thuộc root và bot không ghi được.

```bash
mkdir -p logs data
id -u; id -g     # nếu khác 1000, thêm UID=... và GID=... vào .env
```

### 2.2. Build và chạy

```bash
docker compose up -d --build
docker compose logs -f
```

### 2.3. Bật giám sát Docker

Thêm vào `.env`:

```ini
DOCKER_MONITOR=true
COMPOSE_PROFILES=docker
```

`COMPOSE_PROFILES=docker` khởi động thêm container `docker-socket-proxy`. Proxy này chỉ cho phép đọc
thông tin container (`GET /containers/*`) và không mở port ra host. Compose tự đặt
`DOCKER_API_URL=tcp://docker-socket-proxy:2375` cho bot.

```bash
docker compose up -d      # áp dụng thay đổi
```

Để tắt: đặt `DOCKER_MONITOR=false` và xoá `COMPOSE_PROFILES`, rồi chạy `docker compose --profile docker down && docker compose up -d`.

### 2.4. Container truy cập host như thế nào

| Mount / cấu hình | Mục đích |
|---|---|
| `./logs:/app/logs`, `./data:/app/data` | Dữ liệu CSV và `users.json` được giữ lại sau khi xoá container |
| `/:/host:ro` + `DISK_PATH=/host` | Đo dung lượng ổ root của **host** (read-only) |
| `NET_DEV_PATH=/host/proc/1/net/dev` | Đọc lưu lượng mạng của host thay vì của container |
| `/etc/localtime:ro` | Timestamp và biểu đồ theo giờ của host |
| `pid: host` (đang comment) | Bật để `/top` và `/bash ps aux` thấy process của host |

CPU, RAM, Disk IO và nhiệt độ đọc từ `/proc` và `/sys` (vốn là của host), nên không cần privileged.

> **Lưu ý `/bash` trong Docker:** lệnh chạy **bên trong container** (có sẵn `bash`, `ps`,
> `ip`, `ping`, `curl`). Nó không restart được service của host hay đọc log hệ thống.

### 2.5. Vận hành

```bash
docker compose ps
docker compose restart system-monitor
docker compose down              # dừng (logs/ và data/ vẫn còn)
docker compose logs --since 1h system-monitor
```

Cập nhật code:

```bash
git pull
docker compose up -d --build
```

---

## 3. Cơ chế cảnh báo

### 3.1. Chống spike: trung bình theo cửa sổ

- Cứ mỗi `SAMPLE_INTERVAL` (5 giây) bot lấy một mẫu phụ. CPU là giá trị trung bình **kể từ mẫu trước**,
  nên không bỏ sót khoảng thời gian nào giữa hai mẫu.
- Mỗi `MONITOR_INTERVAL` (60 giây) bot gộp các mẫu phụ thành một dòng CSV gồm `avg` (dùng để cảnh báo)
  và `max` (vẽ mờ trên biểu đồ để vẫn thấy spike).
- Cần `ALERT_CONSECUTIVE` (3) cửa sổ liên tiếp vượt ngưỡng, tức tải cao **liên tục khoảng 3 phút**, mới cảnh báo.

### 3.2. Chống spam: chỉ gửi khi đổi trạng thái

```
OK ──(3 cửa sổ vượt ngưỡng)──▶ ALERT ──(3 cửa sổ < ngưỡng − 5)──▶ OK
                                 └─ nhắc lại sau 1h, 2h, 4h, 8h...
```

Ví dụ RAM đầy suốt 12 giờ: bot gửi 1 cảnh báo (kèm biểu đồ và top process), 3 tin nhắc ngắn
(sau 1h, 3h, 7h) và 1 tin hồi phục (kèm thời lượng và giá trị đỉnh), **tổng cộng 5 tin**.
Docker, service và endpoint dùng chung cơ chế này.

### 3.3. Docker: khi nào thì cảnh báo

`DOCKER_MONITOR=true` bật kết nối Docker cho các lệnh `/docker` và `/dlogs`. `DOCKER_ALERT` chọn loại sự cố nào gây cảnh báo:

| Loại | Điều kiện |
|---|---|
| `unhealthy` | Healthcheck của container báo `unhealthy` |
| `crashloop` | Container ở trạng thái `restarting`, hoặc restart trong khoảng `DOCKER_RESTART_WINDOW_MINUTES` |
| `oom` | Container bị kill do hết RAM (OOMKilled) |
| `exit` | Container `exited` với mã lỗi khác 0, hoặc ở trạng thái `dead` |

```ini
DOCKER_ALERT=true                  # tất cả (mặc định)
DOCKER_ALERT=crashloop,oom         # chỉ crash loop và OOM
DOCKER_ALERT=false                 # không cảnh báo, chỉ dùng /docker
```

Khi bật ít nhất một loại, bot còn cảnh báo nếu không kết nối được Docker API.

Với loại `oom` và `exit`, bot chỉ cảnh báo container **dừng sau khi bot bắt đầu theo dõi**. Container đã dừng
từ trước (ví dụ sót lại sau khi reboot) và container bị `docker stop` (exit 0/137/143) sẽ không gây cảnh báo.

### 3.4. Tạm tắt khi bảo trì

`/mute 2h` tạm tắt mọi cảnh báo trong 2 giờ (tối đa 7 ngày), `/unmute` để bật lại.
Trong lúc tắt, trạng thái vẫn được theo dõi và có thể xem bằng `/alerts`. Báo cáo hằng ngày vẫn được gửi.

---

## 4. Kiểm tra sau khi chạy

1. Bot gửi tin "✅ System Monitor started", trong đó liệt kê các tính năng đang bật.
2. Gõ `/status` để xem CPU, load, RAM, swap, disk, network, nhiệt độ. `Temp: N/A` nghĩa là
   không tìm thấy cảm biến CPU (thường gặp trên VM).
3. Nếu đã bật: `/docker`, `/services`, `/endpoints`.
4. Sau vài phút, `/graph 1` vẽ được biểu đồ và `/report 1` có số liệu.

## 5. Xử lý sự cố

| Triệu chứng | Nguyên nhân / cách xử lý |
|---|---|
| `BOT_TOKEN is not set. Exiting.` | Thiếu `.env`, hoặc service chạy sai `WorkingDirectory` |
| Docker: `Permission denied: 'logs/...'` | `logs/`, `data/` thuộc root. Chạy `sudo chown -R $(id -u):$(id -g) logs data` |
| `/docker`: không kết nối được Docker API | Docker Compose: thiếu `COMPOSE_PROFILES=docker`. systemd: proxy chưa chạy, hoặc sai `DOCKER_API_URL` |
| `/docker` báo `403 Forbidden` | Proxy thiếu `CONTAINERS=1` |
| `/services` báo không có systemctl | Đang chạy trong Docker; tính năng này chỉ có ở chế độ systemd |
| Temp luôn `N/A` | Máy không có `coretemp`/`k10temp`/`cpu_thermal` (VM, một số NAS). Kiểm tra bằng `sensors` |
| Không nhận cảnh báo dù CPU cao | Tải phải cao liên tục `ALERT_CONSECUTIVE` cửa sổ; kiểm tra `/alerts` xem có đang mute không |
| `/addalert` không có tác dụng | Kiểm tra `data/users.json` có được ghi không (quyền thư mục) |

## 6. Phát triển

```bash
uv sync               # cài cả nhóm dev (pytest, hypothesis)
uv run pytest -q
uv add <package>      # thêm dependency (cập nhật pyproject.toml + uv.lock)
```
