# System Monitor & Telegram Bot

A comprehensive system monitoring solution with Telegram bot integration that monitors CPU and RAM usage, sends alerts, and provides remote system management capabilities.

## Features

### System Monitor Service
- **Real-time monitoring**: Checks CPU and RAM usage every minute
- **CSV logging**: Logs data to daily CSV files with automatic rotation
- **Data retention**: Keeps 30 days of historical data
- **Alert system**: Sends Telegram alerts when memory usage exceeds 80%
- **Visual graphs**: Generates usage graphs for alerts

### Telegram Bot
- **System status**: Get current CPU, RAM, disk usage and system info
- **Usage graphs**: Generate and view usage graphs for specified time periods
- **Remote commands**: Execute bash commands remotely (authorized users only)
- **Gateway support**: Connect through Telegram API gateways for improved connectivity
- **User authorization**: Only authorized users can access bot features
- **Response time testing**: Built-in ping command to test gateway performance

## Quick Start

### 1. Prerequisites
- **For Docker setup**: Docker and Docker Compose installed
- **For Systemd setup**: Anaconda/Miniconda with Python 3.10+ environment
- Telegram bot token (get from @BotFather)
- Your Telegram user ID

### 2. Installation

#### Option A: Docker Setup (Development/Testing)
```bash
# Clone or download the files
git clone <repository-url>
cd system-monitor

# Use setup script
chmod +x setup.sh
./setup.sh

# Or use Makefile (recommended)
make setup
```

#### Option B: Systemd Setup (Production)
```bash
# Clone or download the files
git clone <repository-url>
cd system-monitor

# Create Anaconda environment
conda create -n systemmonitor python=3.10
conda activate systemmonitor
pip install -r requirements.txt

# Use systemd setup script
chmod +x setup_systemd.sh
./setup_systemd.sh
```

### 3. Configuration

Edit the `.env` file with your settings:

```env
# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_IDS=123456789,987654321
AUTHORIZED_USERS=123456789,987654321

# Gateway Configuration (optional)
GATEWAY_URL=https://tele.ebiz.workers.dev
```

### 4. Get Your Telegram User ID

Send a message to your bot and check the logs:
```bash
docker-compose logs telegram-bot
```

Or use @userinfobot on Telegram.

### 5. Test Gateway Connection

Before running the full bot, test the gateway connectivity:
```bash
python test_bot.py
```

## Usage

### Telegram Bot Commands

- `/start` - Get welcome message and command list
- `/status` - Get current system status (CPU, RAM, disk, uptime)
- `/graph <hours>` - Generate usage graph for recent hours (1-48)
- `/bash <command>` - Execute bash command (authorized users only)
- `/help` - Show help message

### Examples

```
/status
/graph 24
/bash df -h
/bash top -bn1 | head -20
```

## File Structure

```
system-monitor/
├── monitor.py              # Main monitoring service
├── telegram_bot.py         # Telegram bot service
├── requirements.txt        # Python dependencies
├── docker-compose.yml      # Docker services configuration
├── Dockerfile             # Unified Docker image
├── .dockerignore          # Docker ignore patterns
├── .env.example           # Environment variables template
├── setup.sh              # Setup script
├── Makefile              # Management commands
├── README.md             # This file
└── logs/                 # Log files directory (auto-created)
    ├── system_stats_YYYYMMDD.csv
    ├── monitor.log
    └── telegram_bot.log
```

## Architecture Benefits

### Single Docker Image
- **Optimized**: One base image for both services
- **Fast builds**: Shared dependencies and layers
- **Consistent**: Same Python environment for both services
- **Efficient**: Reduced storage and network usage

### Code Mounting vs Copying
- **Live development**: Code changes without rebuilds
- **Fast iteration**: Edit files and restart containers
- **Volume persistence**: Logs persist across container restarts
- **Easy debugging**: Direct file access on host

## Management Commands

### Using Makefile (Recommended)
```bash
# Initial setup
make setup

# Start/stop services
make up
make down
make restart

# View logs
make logs           # All logs
make logs-m         # Monitor logs only
make logs-b         # Bot logs only

# Development
make shell          # Access container shell
make status         # Check service status
make clean          # Clean up everything

# Quick reference
make help           # Show all commands
```

### Using Docker Compose
```bash
# Start services
docker-compose up -d

# Stop services
docker-compose down

# View logs
docker-compose logs -f

# Check status
docker-compose ps

# Restart services
docker-compose restart
```

## Alternative: Systemd Services (Recommended for Production)

For production environments, you can run the services as systemd services instead of Docker containers.

### Prerequisites for Systemd Setup
- Anaconda/Miniconda installed
- Python environment created: `conda create -n systemmonitor python=3.10`
- Dependencies installed: `conda activate systemmonitor && pip install -r requirements.txt`

### Installation
```bash
# Use the systemd setup script
chmod +x setup_systemd.sh
./setup_systemd.sh
```

### Manual Systemd Setup
```bash
# Copy service files
sudo cp system-monitor.service /etc/systemd/system/
sudo cp system-telegrambot.service /etc/systemd/system/

# Reload systemd and enable services
sudo systemctl daemon-reload
sudo systemctl enable system-monitor system-telegrambot

# Start services
sudo systemctl start system-monitor system-telegrambot
```

### Systemd Management Commands
```bash
# Check service status
sudo systemctl status system-monitor system-telegrambot

# View logs
sudo journalctl -u system-monitor -f      # Monitor service logs
sudo journalctl -u system-telegrambot -f  # Bot service logs

# Control services
sudo systemctl start system-monitor       # Start monitor
sudo systemctl stop system-monitor        # Stop monitor
sudo systemctl restart system-monitor     # Restart monitor
sudo systemctl start system-telegrambot   # Start bot
sudo systemctl stop system-telegrambot    # Stop bot
sudo systemctl restart system-telegrambot # Restart bot

# Disable auto-start
sudo systemctl disable system-monitor system-telegrambot
```

### Uninstall Systemd Services
```bash
chmod +x uninstall_systemd.sh
./uninstall_systemd.sh
```

## Configuration Details

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `TELEGRAM_BOT_TOKEN` | Your Telegram bot token | Yes |
| `TELEGRAM_CHAT_IDS` | Comma-separated list of chat IDs for alerts | Yes |
| `AUTHORIZED_USERS` | Comma-separated list of user IDs who can use bot | Yes |
| `GATEWAY_URL` | Gateway URL for Telegram API routing | No |

### Monitoring Configuration

The monitor service can be configured by editing `monitor.py`:

- `CHECK_INTERVAL`: How often to check system stats (default: 60 seconds)
- `MEMORY_THRESHOLD`: Memory usage threshold for alerts (default: 80%)
- `MAX_DAYS_RETENTION`: How many days to keep logs (default: 30)

## Log Files

### System Stats CSV Format
```csv
timestamp,cpu_percent,mem_percent
2025-01-15 10:30:00,25.5,45.2
2025-01-15 10:31:00,23.1,46.8
```

### Log Rotation
- New CSV file created daily
- Old files automatically deleted after 30 days
- Service logs written to `monitor.log` and `telegram_bot.log`

## Security Considerations

- **User Authorization**: Only specified users can access bot features
- **Command Execution**: Bash commands limited to authorized users with timeout
- **Proxy Support**: Secure connection through SOCKS5/HTTP proxies
- **Log Protection**: Logs stored in protected Docker volumes

## Troubleshooting

### Common Issues

1. **Bot not responding**
   - Check bot token in `.env` file
   - Verify user ID in `AUTHORIZED_USERS`
   - Check proxy settings if using proxy

2. **No data in graphs**
   - Ensure monitor service is running
   - Check if CSV files exist in `logs/` directory
   - Verify file permissions

3. **Memory alerts not working**
   - Check `TELEGRAM_CHAT_IDS` configuration
   - Verify bot token and permissions
   - Check proxy settings

### Debug Commands
```bash
# Check service status
make status

# View detailed logs
make logs-m         # Monitor logs
make logs-b         # Bot logs

# Access container for debugging
make shell          # Monitor container
make shell-b        # Bot container

# Test configuration
make test

# Clean and rebuild
make clean
make build
make up
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

This project is open source and available under the [MIT License](LICENSE).


# Development Guide

## Architecture Overview

This system uses a **single optimized Docker image** with **mounted code** for efficient development and deployment.

### Key Design Decisions

1. **Single Dockerfile**: Both services use the same base image with all dependencies
2. **Code Mounting**: Source code is mounted as volumes, not copied into images
3. **Shared Dependencies**: All Python packages installed once in the base image
4. **Development-Friendly**: Live code changes without rebuilds

## Development Workflow

### 1. Initial Setup
```bash
# Clone the repository
git clone <repo-url>
cd system-monitor

# Quick setup
make setup
```

### 2. Development Cycle
```bash
# Start development environment
make up

# Edit code directly on host
nano monitor.py          # Changes are immediately available
nano telegram_bot.py     # No rebuild needed

# Restart services to apply changes
make restart

# View logs to debug
make logs
```

### 3. Testing Changes
```bash
# Test monitor service
make logs-m

# Test bot service  
make logs-b

# Access container for debugging
make shell
```

## Code Structure

### Monitor Service (`monitor.py`)
```python
class SystemMonitor:
    def __init__(self):          # Initialize monitoring
    def get_system_stats(self):  # Get CPU/RAM stats
    def log_stats(self):         # Write to CSV
    def cleanup_old_logs(self):  # Manage log retention
    def generate_graph(self):    # Create matplotlib charts
    def send_telegram_alert(self): # Send alerts
    def check_memory_threshold(self): # Monitor thresholds
    async def run(self):         # Main monitoring loop
```

### Bot Service (`telegram_bot.py`)
```python
class TelegramBot:
    def __init__(self):              # Initialize bot
    def is_authorized(self):         # Check user permissions
    async def status_command(self):  # /status command
    async def graph_command(self):   # /graph command
    async def bash_command(self):    # /bash command
    def get_recent_data(self):       # Read CSV logs
    async def generate_graph(self):  # Create charts
    async def run(self):             # Main bot loop
```

## File System Layout

### Mounted Volumes
```
Host Directory          Container Mount
./                  ->  /app/              (source code)
./logs/             ->  /app/logs/         (persistent logs)
./.env              ->  /app/.env          (configuration)
```

### Generated Files
```
logs/
├── system_stats_20250709.csv    # Daily CSV logs
├── system_stats_20250708.csv    # Previous day
├── monitor.log                  # Monitor service logs
├── telegram_bot.log             # Bot service logs
└── *.png                       # Temporary graph files
```

## Configuration Management

### Environment Variables (.env)
```bash
# Required
TELEGRAM_BOT_TOKEN=1234567890:ABC...
TELEGRAM_CHAT_IDS=123456789,987654321
AUTHORIZED_USERS=123456789,987654321

# Optional
PROXY_URL=socks5://user:pass@proxy:1080
```

### Service Configuration
Both services read from the same `.env` file and share log directories.

## Debugging Guide

### 1. Service Health Checks
```bash
# Quick status
make status

# Detailed health
docker-compose ps
```

### 2. Log Analysis
```bash
# Real-time monitoring
make logs

# Service-specific logs
make logs-m    # Monitor only
make logs-b    # Bot only

# Historical logs
tail -f logs/monitor.log
tail -f logs/telegram_bot.log
```

### 3. Container Access
```bash
# Access monitor container
make shell-m

# Access bot container  
make shell-b

# Inside container - useful commands
ps aux                    # Check running processes
python monitor.py --test # Test monitor service
python -c "import telegram; print('OK')"  # Test bot deps
```

### 4. Data Inspection
```bash
# Check CSV logs
ls -la logs/
head -20 logs/system_stats_$(date +%Y%m%d).csv

# Check system stats
python -c "import psutil; print(f'CPU: {psutil.cpu_percent()}%, RAM: {psutil.virtual_memory().percent}%')"
```

## Performance Optimization

### Docker Image Optimization
- **Multi-stage builds**: Not needed due to mounting strategy
- **Layer caching**: Requirements.txt copied first for better caching
- **Minimal base**: python:3.10.14-slim for smaller image size
- **Single image**: Shared dependencies reduce total size

### Runtime Optimization
- **Memory efficient**: Matplotlib figures cleaned up after use
- **Log rotation**: Automatic cleanup of old files
- **Error handling**: Graceful degradation on failures
- **Resource monitoring**: Built-in CPU/memory tracking

## Security Considerations

### Container Security
- **Non-root user**: Services run as `appuser` (UID 1000)
- **Read-only mounts**: Code mounted as read-only (optional)
- **Network isolation**: Services in dedicated Docker network
- **Resource limits**: Can be added to docker-compose.yml

### Bot Security
- **User authorization**: Only specified users can use commands
- **Command validation**: Input sanitization for bash commands
- **Timeout limits**: 30-second timeout for command execution
- **Proxy support**: Secure connection through proxies

## Extending the System

### Adding New Commands
```python
# In telegram_bot.py
async def new_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not self.is_authorized(update.effective_user.id):
        await update.message.reply_text("❌ Unauthorized")
        return
    
    # Your command logic here
    
# Register in __init__
self.application.add_handler(CommandHandler("newcmd", self.new_command))
```

### Adding New Metrics
```python
# In monitor.py
def get_additional_stats(self):
    disk_usage = psutil.disk_usage('/').percent
    network_io = psutil.net_io_counters()
    return disk_usage, network_io.bytes_sent, network_io.bytes_recv

# Update CSV headers and logging
```

### Custom Alerts
```python
# Add to SystemMonitor class
def check_custom_threshold(self, metric_value):
    if metric_value > CUSTOM_THRESHOLD:
        self.send_telegram_alert(f"Custom alert: {metric_value}")
```

## Troubleshooting Common Issues

### 1. Permission Errors
```bash
# Fix log directory permissions
sudo chown -R $USER:$USER logs/
chmod 755 logs/
```

### 2. Network Issues
```bash
# Check Docker network
docker network ls
docker network inspect system-monitor_monitoring
```

### 3. Bot Connection Issues
```bash
# Test bot token
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getMe"

# Test with proxy
curl --proxy "$PROXY_URL" "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getMe"
```

### 4. Memory Issues
```bash
# Check container resources
docker stats

# Check host resources
free -h
df -h
```

## Contributing

### Code Style
- Follow PEP 8 for Python code
- Use type hints where appropriate
- Add docstrings for public methods
- Handle exceptions gracefully

### Testing Changes
```bash
# Test configuration
make test

# Test services individually
docker-compose up monitor
docker-compose up telegram-bot

# Integration testing
make up
# Use bot commands to test functionality
```

### Submitting Changes
1. Test your changes thoroughly
2. Update documentation if needed
3. Ensure backward compatibility
4. Submit pull request with description