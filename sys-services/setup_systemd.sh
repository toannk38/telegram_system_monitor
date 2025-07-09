#!/bin/bash

# System Monitor Systemd Services Setup Script
echo "🚀 Setting up System Monitor and Telegram Bot as systemd services..."


# Check if Anaconda environment exists
CONDA_ENV_PATH="/home/admin-uae/anaconda3/envs/sysmonitor"
if [ ! -d "$CONDA_ENV_PATH" ]; then
    echo "❌ Anaconda environment 'systemmonitor' not found at: $CONDA_ENV_PATH"
    echo "   Please create the environment first:"
    echo "   conda create -n sysmonitor python=3.10"
    echo "   conda activate sysmonitor"
    echo "   pip install -r requirements.txt"
    exit 1
fi

# Stop existing services if running
echo "🛑 Stopping existing services (if running)..."
sudo systemctl stop system-monitor || true
sudo systemctl stop system-telegrambot || true

# Copy service files to systemd directory
echo "📋 Installing systemd service files..."
sudo cp system-monitor.service /etc/systemd/system/
sudo cp system-telegrambot.service /etc/systemd/system/

# Set proper permissions
sudo chmod 644 /etc/systemd/system/system-monitor.service
sudo chmod 644 /etc/systemd/system/system-telegrambot.service

# Reload systemd daemon
echo "🔄 Reloading systemd daemon..."
sudo systemctl daemon-reload

# Enable services to start on boot
echo "⚡ Enabling services to start on boot..."
sudo systemctl enable system-monitor
sudo systemctl enable system-telegrambot

# Start services
echo "🚀 Starting services..."
sudo systemctl start system-monitor
sudo systemctl start system-telegrambot

# Wait a moment for services to start
sleep 5

echo "✅ Services started successfully!"

# Show status
echo ""
echo "📊 Service Status:"
sudo systemctl status system-monitor --no-pager -l
echo ""
sudo systemctl status system-telegrambot --no-pager -l

echo ""
echo "🔍 Checking service health..."
sleep 5

# Check if services are healthy
MONITOR_STATUS=$(sudo systemctl is-active system-monitor)
BOT_STATUS=$(sudo systemctl is-active system-telegrambot)

if [ "$MONITOR_STATUS" = "active" ]; then
    echo "✅ Monitor service is running"
else
    echo "❌ Monitor service failed to start"
    echo "📋 Monitor service logs:"
    sudo journalctl -u system-monitor --no-pager -l -n 20
fi

if [ "$BOT_STATUS" = "active" ]; then
    echo "✅ Telegram bot service is running"
else
    echo "❌ Telegram bot service failed to start"
    echo "📋 Telegram bot service logs:"
    sudo journalctl -u system-telegrambot --no-pager -l -n 20
fi

echo ""
echo "📝 Useful commands:"
echo "   View monitor logs:     sudo journalctl -u system-monitor -f"
echo "   View bot logs:         sudo journalctl -u system-telegrambot -f"
echo "   Stop monitor:          sudo systemctl stop system-monitor"
echo "   Stop bot:              sudo systemctl stop system-telegrambot"
echo "   Start monitor:         sudo systemctl start system-monitor"
echo "   Start bot:             sudo systemctl start system-telegrambot"
echo "   Restart monitor:       sudo systemctl restart system-monitor"
echo "   Restart bot:           sudo systemctl restart system-telegrambot"
echo "   Disable auto-start:    sudo systemctl disable system-monitor system-telegrambot"
echo "   Service status:        sudo systemctl status system-monitor system-telegrambot"

echo ""
echo "💡 Management tips:"
echo "   - Code changes require service restart"
echo "   - Services will auto-restart on failure"
echo "   - Services start automatically on system boot"
echo "   - Logs are stored in systemd journal"

echo ""
echo "📈 Log files location: ./logs/"
echo "🤖 Your Telegram bot is now ready to use!"

echo ""
echo "🔧 Quick test commands for your bot:"
echo "   /start"
echo "   /status"
echo "   /graph 1"

echo ""
echo "🧪 Test the services:"
echo "   python test_monitor.py"
echo "   python test_bot.py"
