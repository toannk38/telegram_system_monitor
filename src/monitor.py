#!/usr/bin/env python3
"""
System Monitor Service
Monitors CPU and RAM usage every minute, logs to CSV files with daily rotation
Sends Telegram alerts when memory usage exceeds 80%
"""

import os
import time
import csv
import psutil
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import requests
from dotenv import load_dotenv
import glob

# Import gateway functionality from bot.py
from .bot import TelegramBotGatewayRequests

# Load environment variables
load_dotenv()

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_IDS = os.getenv('TELEGRAM_CHAT_IDS', '').split(',')
GATEWAY_URL = os.getenv('GATEWAY_URL', 'https://tele.ebiz.workers.dev')
LOG_DIR = Path('logs')
MAX_DAYS_RETENTION = 30
MEMORY_THRESHOLD = 80.0
CHECK_INTERVAL = 60  # seconds

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/monitor.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class SystemMonitor:
    def __init__(self):
        self.log_dir = LOG_DIR
        self.log_dir.mkdir(exist_ok=True)
        self.last_alert_time = None
        self.alert_cooldown = 300  # 5 minutes cooldown between alerts
        
        # Initialize gateway for Telegram alerts
        self.telegram_gateway = None
        if TELEGRAM_BOT_TOKEN:
            self.telegram_gateway = TelegramBotGatewayRequests(TELEGRAM_BOT_TOKEN, GATEWAY_URL)
            logger.info(f"Telegram gateway initialized: {GATEWAY_URL}")
        else:
            logger.warning("TELEGRAM_BOT_TOKEN not configured, alerts disabled")
    
    def get_system_stats(self) -> Tuple[float, float]:
        """Get current CPU and memory usage percentages"""
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            mem_percent = memory.percent
            return cpu_percent, mem_percent
        except Exception as e:
            logger.error(f"Error getting system stats: {e}")
            return 0.0, 0.0
    
    def get_current_log_file(self) -> Path:
        """Get the current day's log file path"""
        today = datetime.now().strftime('%Y%m%d')
        return self.log_dir / f'system_stats_{today}.csv'
    
    def log_stats(self, cpu_percent: float, mem_percent: float):
        """Log stats to CSV file"""
        log_file = self.get_current_log_file()
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Check if file exists to determine if we need headers
        file_exists = log_file.exists()
        
        try:
            with open(log_file, 'a', newline='') as csvfile:
                writer = csv.writer(csvfile)
                if not file_exists:
                    writer.writerow(['timestamp', 'cpu_percent', 'mem_percent'])
                writer.writerow([timestamp, cpu_percent, mem_percent])
            logger.info(f"Logged: CPU={cpu_percent:.1f}%, RAM={mem_percent:.1f}%")
        except Exception as e:
            logger.error(f"Error logging stats: {e}")
    
    def cleanup_old_logs(self):
        """Remove log files older than MAX_DAYS_RETENTION"""
        cutoff_date = datetime.now() - timedelta(days=MAX_DAYS_RETENTION)
        
        for log_file in self.log_dir.glob('system_stats_*.csv'):
            try:
                # Extract date from filename
                date_str = log_file.stem.split('_')[-1]
                file_date = datetime.strptime(date_str, '%Y%m%d')
                
                if file_date < cutoff_date:
                    log_file.unlink()
                    logger.info(f"Removed old log file: {log_file}")
            except Exception as e:
                logger.error(f"Error cleaning up log file {log_file}: {e}")
    
    def generate_graph(self, hours: int = 12) -> Path:
        """Generate graph of CPU and memory usage for the last N hours"""
        try:
            # Get data from recent log files
            data = self.get_recent_data(hours)
            
            if not data:
                logger.warning("No data available for graph generation")
                return None
            
            # Create the plot
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))
            
            timestamps = [row[0] for row in data]
            cpu_data = [row[1] for row in data]
            mem_data = [row[2] for row in data]
            
            # CPU plot
            ax1.plot(timestamps, cpu_data, 'b-', linewidth=2, label='CPU Usage')
            ax1.set_ylabel('CPU Usage (%)')
            ax1.set_title(f'System Usage - Last {hours} Hours')
            ax1.grid(True, alpha=0.3)
            ax1.legend()
            
            # Memory plot
            ax2.plot(timestamps, mem_data, 'r-', linewidth=2, label='Memory Usage')
            ax2.axhline(y=80, color='orange', linestyle='--', alpha=0.7, label='Warning Threshold')
            ax2.set_ylabel('Memory Usage (%)')
            ax2.set_xlabel('Time')
            ax2.grid(True, alpha=0.3)
            ax2.legend()
            
            # Format x-axis
            for ax in [ax1, ax2]:
                ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
                ax.xaxis.set_major_locator(mdates.HourLocator(interval=max(1, hours//8)))
                plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)
            
            plt.tight_layout()
            
            # Save graph
            graph_path = self.log_dir / 'current_usage_graph.png'
            plt.savefig(graph_path, dpi=300, bbox_inches='tight')
            plt.close()
            
            logger.info(f"Graph generated: {graph_path}")
            return graph_path
            
        except Exception as e:
            logger.error(f"Error generating graph: {e}")
            return None
    
    def get_recent_data(self, hours: int) -> List[Tuple[datetime, float, float]]:
        """Get data from the last N hours from log files"""
        data = []
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        # Get relevant log files (today and yesterday)
        today = datetime.now().strftime('%Y%m%d')
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')
        
        log_files = []
        for date in [yesterday, today]:
            log_file = self.log_dir / f'system_stats_{date}.csv'
            if log_file.exists():
                log_files.append(log_file)
        
        for log_file in log_files:
            try:
                with open(log_file, 'r') as csvfile:
                    reader = csv.DictReader(csvfile)
                    for row in reader:
                        timestamp = datetime.strptime(row['timestamp'], '%Y-%m-%d %H:%M:%S')
                        if timestamp >= cutoff_time:
                            data.append((
                                timestamp,
                                float(row['cpu_percent']),
                                float(row['mem_percent'])
                            ))
            except Exception as e:
                logger.error(f"Error reading log file {log_file}: {e}")
        
        return sorted(data, key=lambda x: x[0])
    
    def send_telegram_alert(self, message: str, graph_path: Path = None):
        """Send alert message to Telegram using gateway"""
        if not self.telegram_gateway or not TELEGRAM_CHAT_IDS:
            logger.warning("Telegram gateway or chat IDs not configured")
            return
        
        for chat_id in TELEGRAM_CHAT_IDS:
            if not chat_id.strip():
                continue
                
            try:
                # Send text message via gateway
                result = self.telegram_gateway.send_message(chat_id.strip(), message)
                
                if result and result.get('ok'):
                    logger.info(f"Alert sent to chat {chat_id} via gateway")
                else:
                    logger.error(f"Failed to send alert to chat {chat_id}: {result}")
                
                # Send graph if provided
                if graph_path and graph_path.exists():
                    try:
                        # Use gateway's send_photo method
                        result = self.telegram_gateway.send_photo(
                            chat_id.strip(), 
                            graph_path, 
                            f"📊 System Usage Alert - Memory: {MEMORY_THRESHOLD}% threshold exceeded"
                        )
                        
                        if result and result.get('ok'):
                            logger.info(f"Graph sent to chat {chat_id} via gateway")
                        else:
                            logger.error(f"Failed to send graph to chat {chat_id}: {result}")
                            
                    except Exception as photo_error:
                        logger.error(f"Error sending photo to {chat_id}: {photo_error}")
                
            except Exception as e:
                logger.error(f"Error sending Telegram alert to {chat_id}: {e}")
    
    def check_memory_threshold(self, mem_percent: float):
        """Check if memory usage exceeds threshold and send alert if needed"""
        if mem_percent > MEMORY_THRESHOLD:
            now = datetime.now()
            
            # Check cooldown
            if (self.last_alert_time is None or 
                (now - self.last_alert_time).total_seconds() > self.alert_cooldown):
                
                message = f"🚨 <b>Memory Usage Alert</b> 🚨\n\n"
                message += f"Memory usage is at <b>{mem_percent:.1f}%</b>\n"
                message += f"Threshold: {MEMORY_THRESHOLD}%\n"
                message += f"Time: {now.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                message += "Please check system resources!"
                
                # Generate graph for the alert
                graph_path = self.generate_graph(12)
                try:
                    self.send_telegram_alert(message, graph_path)
                    self.last_alert_time = now
                except Exception as e:
                    logger.error(f"Error sending alert: {e}")
                
                logger.warning(f"Memory threshold exceeded: {mem_percent:.1f}%")
    
    async def run(self):
        """Main monitoring loop"""
        logger.info("Starting system monitor...")
        
        while True:
            try:
                # Get system stats
                cpu_percent, mem_percent = self.get_system_stats()
                
                # Log stats
                self.log_stats(cpu_percent, mem_percent)
                
                # Check memory threshold
                self.check_memory_threshold(mem_percent)
                
                # Cleanup old logs (run once per day)
                if datetime.now().hour == 0 and datetime.now().minute == 0:
                    self.cleanup_old_logs()
                
                # Wait for next check
                await asyncio.sleep(CHECK_INTERVAL)
                
            except KeyboardInterrupt:
                logger.info("Monitor stopped by user")
                break
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    monitor = SystemMonitor()
    
    # Test gateway connection and send startup message
    if monitor.telegram_gateway:
        if monitor.telegram_gateway.test_gateway():
            logger.info("Gateway test successful, sending startup notification...")
            monitor.send_telegram_alert("🚀 <b>System Monitor Service Started</b>\n\n📊 Monitoring CPU and RAM usage every minute\n🌐 Using Telegram Gateway for alerts", None)
        else:
            logger.error("Gateway test failed, alerts may not work properly")
    
    try:
        asyncio.run(monitor.run())
    except KeyboardInterrupt:
        logger.info("Monitor service stopped")