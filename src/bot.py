#!/usr/bin/env python3
"""
Telegram Bot Class
Main bot functionality for system monitoring using Gateway
"""

import os
import subprocess
import asyncio
import logging
import csv
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple, Optional
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import psutil
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest
import httpx
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
AUTHORIZED_USERS = os.getenv('AUTHORIZED_USERS', '').split(',')
GATEWAY_URL = os.getenv('GATEWAY_URL', 'https://api.telegram.org')
LOG_DIR = Path('logs')

logger = logging.getLogger(__name__)


# Gateway implementation for direct Telegram API access
class TelegramBotGatewayRequests:
    def __init__(self, bot_token, gateway_url="https://tele.ebiz.workers.dev"):
        self.bot_token = bot_token
        self.gateway_url = gateway_url.rstrip('/')
        self.api_url = f"{self.gateway_url}/bot{bot_token}"
        
        # Configure requests session for gateway
        self.session = requests.Session()
        self.session.timeout = 30
        self.session.headers.update({
            'User-Agent': 'TelegramBot/1.0',
            'Content-Type': 'application/json'
        })
    
    def test_gateway(self):
        """Test gateway connectivity"""
        try:
            response = self.session.get(f"{self.api_url}/getMe")
            response.raise_for_status()
            result = response.json()
            if result.get('ok'):
                logger.info(f"Gateway test successful: {result['result']['first_name']}")
                return True
            else:
                logger.error(f"Gateway test failed: {result}")
                return False
        except requests.exceptions.RequestException as e:
            logger.error(f"Gateway connectivity error: {e}")
            return False
    
    def send_message(self, chat_id, text):
        """Send a message via gateway"""
        url = f"{self.api_url}/sendMessage"
        data = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'HTML'
        }
        
        try:
            response = self.session.post(url, json=data)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error sending message via gateway: {e}")
            return None
    
    def send_photo(self, chat_id, photo_path, caption=None):
        """Send a photo via gateway"""
        url = f"{self.api_url}/sendPhoto"
        
        try:
            with open(photo_path, 'rb') as photo:
                files = {'photo': photo}
                data = {'chat_id': chat_id}
                if caption:
                    data['caption'] = caption
                
                response = self.session.post(url, data=data, files=files)
                response.raise_for_status()
                return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error sending photo via gateway: {e}")
            return None
        except IOError as e:
            logger.error(f"Error reading photo file {photo_path}: {e}")
            return None
    
    def get_updates(self, offset=None, timeout=30):
        """Get updates via gateway"""
        url = f"{self.api_url}/getUpdates"
        params = {
            'offset': offset or 0,
            'timeout': timeout,
            'allowed_updates': ['message', 'callback_query']
        }
        
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting updates via gateway: {e}")
            return None


class TelegramBot:
    def __init__(self):
        self.log_dir = LOG_DIR
        self.log_dir.mkdir(exist_ok=True)
        
        # Initialize gateway instead of proxy manager
        self.gateway = TelegramBotGatewayRequests(TELEGRAM_BOT_TOKEN, GATEWAY_URL)
        
        # Create application with gateway support
        self.application = self._create_application()
        
        # Register handlers
        self._register_handlers()
    
    def _create_application(self):
        """Create Telegram application with gateway support"""
        # Set up gateway request with proper configuration
        gateway_request = HTTPXRequest(
            connection_pool_size=8,
            read_timeout=30.0,
            write_timeout=30.0,
            connect_timeout=30.0,
            pool_timeout=30.0
        )
        
        # Create application builder
        builder = Application.builder().token(TELEGRAM_BOT_TOKEN)
        
        # Test gateway connectivity first
        if self.gateway.test_gateway():
            logger.info(f"Using gateway: {GATEWAY_URL}")
            # Configure for gateway with proper URL format - don't use {token} placeholder
            builder = (builder
                      .base_url(f"{GATEWAY_URL}/bot")
                      .base_file_url(f"{GATEWAY_URL}/file/bot")
                      .request(gateway_request))
        else:
            logger.warning("Gateway test failed, using direct connection")
            # Use direct connection with custom request settings
            builder = builder.request(gateway_request)
        
        return builder.build()
    
    def _register_handlers(self):
        """Register all command handlers"""
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("id", self.id_command))
        self.application.add_handler(CommandHandler("test_gateway", self.test_gateway_command))
        self.application.add_handler(CommandHandler("bash", self.bash_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("graph", self.graph_command))
        self.application.add_handler(CommandHandler("ping", self.ping_command))
        
        # Add handler for unknown commands
        self.application.add_handler(MessageHandler(filters.COMMAND, self.unknown_command))
        
        # Add error handler
        self.application.add_error_handler(self.error_handler)
    
    def _test_gateway_connectivity(self):
        """Test gateway connectivity and recreate application if needed"""
        try:
            if self.gateway.test_gateway():
                logger.info("Gateway connectivity test successful")
                return True
            else:
                logger.warning("Gateway connectivity test failed")
                return False
        except Exception as e:
            logger.error(f"Error testing gateway connectivity: {e}")
            return False
    
    def is_authorized(self, user_id: str) -> bool:
        """Check if user is authorized to use the bot"""
        if not AUTHORIZED_USERS or not AUTHORIZED_USERS[0]:
            logger.warning("No authorized users configured!")
            return False
        
        return str(user_id) in AUTHORIZED_USERS
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user_id = update.effective_user.id
        logger.info(f"User {user_id} started the bot")
        # if not self.is_authorized(user_id):
        #     await update.message.reply_text("❌ You are not authorized to use this bot.")
        #     return
        
        message = (
            "🤖 <b>System Monitor Bot</b>\n\n"
            "Available commands:\n"
            "/status - Get current CPU and RAM usage\n"
            "/graph &lt;hours&gt; - Get usage graph for recent hours (max 48)\n"
            "/bash &lt;command&gt; - Execute bash command\n"
            "/id - Get user ID (private) or group ID (group)\n"
            "/test_gateway - Test gateway connection\n"
            "/ping - Test gateway response time\n"
            "/help - Show this help message"
        )
        
        await update.message.reply_text(message, parse_mode='HTML')
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        await self.start_command(update, context)
    
    async def id_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /id command - returns user ID in private chat or group ID in group chat"""
        user_id = update.effective_user.id
        logger.info(f"User {user_id} requested ID information")
        # if not self.is_authorized(user_id):
        #     await update.message.reply_text("❌ You are not authorized to use this bot.")
        #     return
        
        chat = update.effective_chat
        
        if chat.type == 'private':
            # Private chat - return user ID
            message = (
                f"👤 <b>User Information</b>\n\n"
                f"🆔 <b>Your User ID:</b> <code>{user_id}</code>\n"
                f"👤 <b>Username:</b> @{update.effective_user.username or 'N/A'}\n"
                f"📝 <b>Full Name:</b> {update.effective_user.full_name or 'N/A'}\n"
                f"💬 <b>Chat Type:</b> Private"
            )
        else:
            # Group chat - return group ID
            group_id = chat.id
            message = (
                f"👥 <b>Group Information</b>\n\n"
                f"🆔 <b>Group ID:</b> <code>{group_id}</code>\n"
                f"📝 <b>Group Title:</b> {chat.title or 'N/A'}\n"
                f"💬 <b>Chat Type:</b> {chat.type.title()}\n\n"
                f"👤 <b>Your User ID:</b> <code>{user_id}</code>\n"
                f"👤 <b>Your Username:</b> @{update.effective_user.username or 'N/A'}"
            )
        
        await update.message.reply_text(message, parse_mode='HTML')
    
    async def test_gateway_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /test_gateway command"""
        user_id = update.effective_user.id
        
        if not self.is_authorized(user_id):
            await update.message.reply_text("❌ You are not authorized to use this bot.")
            return
        
        try:
            await update.message.reply_text("🔍 Testing gateway connection...")
            
            if self.gateway.test_gateway():
                await update.message.reply_text(
                    f"✅ <b>Gateway Test Successful!</b>\n\n"
                    f"🌐 <b>Gateway URL:</b> <code>{GATEWAY_URL}</code>\n"
                    f"⏱️ <b>Test Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    parse_mode='HTML'
                )
                logger.info(f"Gateway test successful for {GATEWAY_URL}")
            else:
                await update.message.reply_text(
                    f"❌ <b>Gateway Test Failed!</b>\n\n"
                    f"🌐 <b>Gateway URL:</b> <code>{GATEWAY_URL}</code>\n"
                    f"⚠️ <b>Status:</b> Connection failed or gateway unreachable",
                    parse_mode='HTML'
                )
                logger.warning(f"Gateway test failed for {GATEWAY_URL}")
                
        except Exception as e:
            logger.error(f"Gateway test error: {e}")
            await update.message.reply_text(f"❌ Gateway test error: {str(e)}")
    
    async def ping_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /ping command to test gateway response time"""
        user_id = update.effective_user.id
        
        if not self.is_authorized(user_id):
            await update.message.reply_text("❌ You are not authorized to use this bot.")
            return
        
        try:
            import time
            start_time = time.time()
            
            # Send a test message to measure response time
            sent_message = await update.message.reply_text("🏓 Pinging gateway...")
            
            end_time = time.time()
            response_time = round((end_time - start_time) * 1000, 2)
            
            await sent_message.edit_text(
                f"🏓 Pong!\n"
                f"⏱️ <b>Response time:</b> {response_time}ms\n"
                f"🌐 <b>Gateway:</b> <code>{GATEWAY_URL}</code>\n"
                f"✅ <b>Status:</b> Connection successful",
                parse_mode='HTML'
            )
            logger.info(f"Ping successful: {response_time}ms")
            
        except Exception as e:
            logger.error(f"Ping error: {e}")
            await update.message.reply_text(f"❌ Ping error: {str(e)}")
    
    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"Exception while handling an update: {context.error}")
        
        if isinstance(update, Update) and update.message:
            try:
                await update.message.reply_text(
                    f"❌ Sorry, something went wrong.\n"
                    f"The gateway might be experiencing issues.\n"
                    f"Please try again in a moment."
                )
            except Exception as reply_error:
                logger.error(f"Error sending error message: {reply_error}")
    
    async def bash_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /bash command"""
        user_id = update.effective_user.id
        
        if not self.is_authorized(user_id):
            await update.message.reply_text("❌ You are not authorized to use this bot.")
            return
        
        if not context.args:
            await update.message.reply_text("❌ Please provide a command to execute.\nUsage: /bash &lt;command&gt;", parse_mode='HTML')
            return
        
        command = ' '.join(context.args)
        
        try:
            # Log the command execution
            logger.info(f"User {user_id} executing command: {command}")
            
            # Execute the command
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            stdout, stderr = process.communicate()
            
            # Prepare response
            response = f"🔧 <b>Command:</b> <code>{command}</code>\n\n"
            
            if stdout:
                # Limit output length
                if len(stdout) > 3000:
                    stdout = stdout[:3000] + "... (truncated)"
                response += f"✅ <b>Output:</b>\n<pre>{stdout}</pre>\n"
            
            if stderr:
                if len(stderr) > 1000:
                    stderr = stderr[:1000] + "... (truncated)"
                response += f"❌ <b>Error:</b>\n<pre>{stderr}</pre>\n"
            
            response += f"🔄 <b>Exit Code:</b> {process.returncode}"
            
            await update.message.reply_text(response, parse_mode='HTML')
            
        except subprocess.TimeoutExpired:
            await update.message.reply_text("❌ Command timed out (30 seconds limit)")
        except Exception as e:
            logger.error(f"Error executing command: {e}")
            await update.message.reply_text(f"❌ Error executing command: {str(e)}")
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command"""
        user_id = update.effective_user.id
        
        # if not self.is_authorized(user_id):
        #     await update.message.reply_text("❌ You are not authorized to use this bot.")
        #     return
        
        try:
            # Get current system stats
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            mem_percent = memory.percent
            
            # Get additional system info
            disk = psutil.disk_usage('/')
            disk_percent = disk.percent
            
            # Get load average
            load_avg = os.getloadavg()
            
            # Get uptime
            boot_time = psutil.boot_time()
            uptime = datetime.now() - datetime.fromtimestamp(boot_time)
            
            # Format uptime
            days = uptime.days
            hours, remainder = divmod(uptime.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            
            uptime_str = f"{days}d {hours}h {minutes}m"
            
            # Create status message
            message = (
                f"📊 <b>System Status</b>\n\n"
                f"🖥️ <b>CPU Usage:</b> {cpu_percent:.1f}%\n"
                f"🧠 <b>Memory Usage:</b> {mem_percent:.1f}%\n"
                f"💾 <b>Disk Usage:</b> {disk_percent:.1f}%\n"
                f"📈 <b>Load Average:</b> {load_avg[0]:.2f} {load_avg[1]:.2f} {load_avg[2]:.2f}\n"
                f"⏱️ <b>Uptime:</b> {uptime_str}\n"
                f"🕒 <b>Current Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            
            # Add warning if memory is high
            if mem_percent > 80:
                message += f"\n\n⚠️ <b>Warning:</b> Memory usage is high!"
            
            await update.message.reply_text(message, parse_mode='HTML')
            
        except Exception as e:
            logger.error(f"Error getting system status: {e}")
            await update.message.reply_text(f"❌ Error getting system status: {str(e)}")
    
    async def graph_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /graph command"""
        user_id = update.effective_user.id
        
        if not self.is_authorized(user_id):
            await update.message.reply_text("❌ You are not authorized to use this bot.")
            return
        
        # Parse hours parameter
        hours = 12  # default
        if context.args:
            try:
                hours = int(context.args[0])
                if hours < 1 or hours > 48:
                    await update.message.reply_text("❌ Hours must be between 1 and 48")
                    return
            except ValueError:
                await update.message.reply_text("❌ Invalid hours value. Please provide a number.")
                return
        
        try:
            # Send typing indicator
            await update.message.reply_text("📊 Generating graph...")
            
            # Get data from log files
            data = self.get_recent_data(hours)
            
            if not data:
                await update.message.reply_text("❌ No data available for the requested time period.")
                return
            
            # Generate graph
            graph_path = await self.generate_graph(data, hours)
            
            if graph_path and graph_path.exists():
                with open(graph_path, 'rb') as photo:
                    caption = f"📈 System usage graph for the last {hours} hours"
                    await update.message.reply_photo(photo=photo, caption=caption)
                
                # Clean up temporary file
                graph_path.unlink()
            else:
                await update.message.reply_text("❌ Failed to generate graph.")
                
        except Exception as e:
            logger.error(f"Error generating graph: {e}")
            await update.message.reply_text(f"❌ Error generating graph: {str(e)}")
    
    def get_recent_data(self, hours: int) -> List[Tuple[datetime, float, float]]:
        """Get data from the last N hours from log files"""
        data = []
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        # Get up to 3 recent log files
        log_files = []
        for i in range(3):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y%m%d')
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
    
    async def generate_graph(self, data: List[Tuple[datetime, float, float]], hours: int) -> Path:
        """Generate graph from data"""
        try:
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
            ax1.set_ylim(0, 100)
            
            # Memory plot
            ax2.plot(timestamps, mem_data, 'r-', linewidth=2, label='Memory Usage')
            ax2.axhline(y=80, color='orange', linestyle='--', alpha=0.7, label='Warning Threshold')
            ax2.set_ylabel('Memory Usage (%)')
            ax2.set_xlabel('Time')
            ax2.grid(True, alpha=0.3)
            ax2.legend()
            ax2.set_ylim(0, 100)
            
            # Format x-axis
            for ax in [ax1, ax2]:
                ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
                ax.xaxis.set_major_locator(mdates.HourLocator(interval=max(1, hours//8)))
                plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)
            
            plt.tight_layout()
            
            # Save graph
            graph_path = self.log_dir / f'bot_graph_{datetime.now().strftime("%Y%m%d_%H%M%S")}.png'
            plt.savefig(graph_path, dpi=300, bbox_inches='tight')
            plt.close()
            
            return graph_path
            
        except Exception as e:
            logger.error(f"Error generating graph: {e}")
            return None
    
    async def unknown_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle unknown commands"""
        await update.message.reply_text(
            "❌ Unknown command. Use /help to see available commands."
        )
    
    async def run(self):
        """Run the bot with gateway error handling"""
        logger.info("Starting Telegram bot with gateway...")
        
        max_retries = 5
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                # Test gateway connectivity before starting
                if not self.gateway.test_gateway():
                    logger.warning("Gateway test failed, but continuing with bot startup...")
                
                # Start the bot
                await self.application.initialize()
                await self.application.start()
                
                # Start polling with improved configuration
                await self.application.updater.start_polling(
                    drop_pending_updates=True,
                    allowed_updates=["message", "callback_query", "inline_query"]
                )
                
                logger.info("Bot is running and polling for updates...")
                logger.info(f"Using gateway: {GATEWAY_URL}")
                
                # Keep the bot running - use a simple infinite loop instead of idle()
                try:
                    while True:
                        await asyncio.sleep(1)
                except asyncio.CancelledError:
                    logger.info("Bot polling cancelled")
                    break
                    
            except KeyboardInterrupt:
                logger.info("Bot stopped by user")
                break
            except Exception as e:
                error_str = str(e).lower()
                logger.error(f"Error running bot: {e}")
                retry_count += 1
                
                # Check for connection related errors
                is_connection_error = any(keyword in error_str for keyword in [
                    "connection", "timeout", "network", "unreachable", "refused", "reset"
                ])
                
                if is_connection_error:
                    logger.warning("Connection error detected, testing gateway...")
                    if not self.gateway.test_gateway():
                        logger.warning("Gateway test failed, continuing with retry...")
                
                if retry_count < max_retries:
                    wait_time = min(retry_count * 10, 60)  # Max 60 seconds wait
                    logger.info(f"Retrying in {wait_time} seconds... (attempt {retry_count}/{max_retries})")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error("Max retries reached, stopping bot")
                    break
            finally:
                try:
                    if self.application:
                        await self.application.stop()
                        await self.application.shutdown()
                except Exception as stop_error:
                    logger.debug(f"Error stopping application: {stop_error}")
        
        logger.info("Bot run loop ended")


# Main execution
if __name__ == "__main__":
    async def main():
        """Main function to run the bot"""
        if not TELEGRAM_BOT_TOKEN:
            logger.error("TELEGRAM_BOT_TOKEN not found in environment variables!")
            logger.error("Please set TELEGRAM_BOT_TOKEN in your .env file")
            return
        
        logger.info(f"🚀 Starting Telegram Bot with Gateway: {GATEWAY_URL}")
        logger.info("This gateway will route all requests to the Telegram API")
        
        # Create and run the bot
        bot = TelegramBot()
        await bot.run()

    # Run the main function
    asyncio.run(main())
