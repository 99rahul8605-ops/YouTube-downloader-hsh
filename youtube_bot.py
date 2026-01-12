#!/usr/bin/env python3
"""
YouTube Downloader Bot with FFmpeg - Fixed for Render
"""

import os
import re
import sys
import json
import asyncio
import logging
import subprocess
from pathlib import Path
from aiohttp import web
from pyrogram import Client, filters, idle
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Environment variables
API_ID = int(os.getenv('API_ID', 0))
API_HASH = os.getenv('API_HASH', '')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
OWNER_ID = int(os.getenv('OWNER_ID', 0))
PORT = int(os.getenv('PORT', 8080))
HOST = os.getenv('HOST', '0.0.0.0')
CREDIT = os.getenv('CREDIT', 'YouTube Downloader Bot')

# Global variables
processing_request = False
cancel_requested = False
current_tasks = {}

# Create necessary directories
DOWNLOAD_PATH = Path("./downloads")
COOKIES_FILE = Path("youtube_cookies.txt")
DOWNLOAD_PATH.mkdir(exist_ok=True)

# Check FFmpeg installation
def check_ffmpeg():
    """Check if FFmpeg is installed"""
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        logger.info("✅ FFmpeg is installed")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.error("❌ FFmpeg is not installed")
        # Try to install FFmpeg on Render
        try:
            subprocess.run(['apt-get', 'update'], capture_output=True)
            subprocess.run(['apt-get', 'install', '-y', 'ffmpeg'], capture_output=True)
            logger.info("✅ FFmpeg installed via apt-get")
            return True
        except Exception as e:
            logger.error(f"Failed to install FFmpeg: {e}")
            return False

# Initialize bot with better settings
bot = Client(
    name="youtube_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=20,
    sleep_threshold=60,
    plugins=dict(root="bot_plugins")
)

# ============================================================================
# Helper Functions
# ============================================================================

def clean_filename(filename):
    """Clean filename for safe filesystem use"""
    # Remove invalid characters
    cleaned = re.sub(r'[<>:"/\\|?*]', '', filename)
    # Limit length
    cleaned = cleaned[:100]
    return cleaned

async def get_video_info(url, use_cookies=False):
    """Get YouTube video info"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
    }
    
    if use_cookies and COOKIES_FILE.exists():
        ydl_opts['cookiefile'] = str(COOKIES_FILE)
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info
    except Exception as e:
        logger.error(f"Error getting video info: {e}")
        return None

def get_available_formats(info):
    """Get available video formats"""
    formats = []
    if 'formats' in info:
        for fmt in info['formats']:
            if fmt.get('vcodec') != 'none' and fmt.get('acodec') != 'none':
                height = fmt.get('height', 0)
                if height:
                    formats.append({
                        'height': height,
                        'ext': fmt.get('ext', 'mp4'),
                        'format_note': fmt.get('format_note', ''),
                        'filesize': fmt.get('filesize', 0)
                    })
    
    # Remove duplicates and sort
    unique_formats = {}
    for fmt in formats:
        if fmt['height'] not in unique_formats:
            unique_formats[fmt['height']] = fmt
    
    return sorted(unique_formats.values(), key=lambda x: x['height'])

# ============================================================================
# Download Functions with FFmpeg
# ============================================================================

async def download_video_with_progress(url, resolution, chat_id, message_id):
    """Download video with progress updates"""
    global cancel_requested
    
    try:
        # Get video info first
        info = await get_video_info(url, COOKIES_FILE.exists())
        if not info:
            return False, "Failed to get video information"
        
        video_title = clean_filename(info.get('title', 'video'))
        
        # Prepare yt-dlp options
        ydl_opts = {
            'format': f'bestvideo[height<={resolution}]+bestaudio/best[height<={resolution}]',
            'outtmpl': f'{DOWNLOAD_PATH}/{video_title}.%(ext)s',
            'quiet': False,
            'no_warnings': False,
            'merge_output_format': 'mp4',
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
            'ffmpeg_location': '/usr/bin/ffmpeg',
            'progress_hooks': [lambda d: asyncio.create_task(
                progress_callback(d, chat_id, message_id)
            )],
        }
        
        if COOKIES_FILE.exists():
            ydl_opts['cookiefile'] = str(COOKIES_FILE)
        
        # Start download
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
            
            # Get output file
            downloaded_file = ydl.prepare_filename(info)
            final_file = downloaded_file.replace('.webm', '.mp4').replace('.mkv', '.mp4')
            
            if not os.path.exists(final_file):
                # Try to find the file
                for ext in ['.mp4', '.mkv', '.webm']:
                    test_file = downloaded_file.rsplit('.', 1)[0] + ext
                    if os.path.exists(test_file):
                        final_file = test_file
                        break
            
            if os.path.exists(final_file):
                return True, final_file
            else:
                return False, "Downloaded file not found"
                
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        if "Private video" in error_msg:
            return False, "🔒 Private video - Login required"
        elif "Members-only" in error_msg:
            return False, "👥 Members-only video"
        elif "Sign in" in error_msg:
            return False, "🔑 Login required - Use /cookies to upload cookies"
        else:
            return False, f"Download error: {error_msg[:200]}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"

async def progress_callback(d, chat_id, message_id):
    """Progress callback for yt-dlp"""
    if d['status'] == 'downloading':
        try:
            total = d.get('total_bytes', 0) or d.get('total_bytes_estimate', 0)
            downloaded = d.get('downloaded_bytes', 0)
            speed = d.get('speed', 0)
            
            if total and downloaded:
                percentage = (downloaded / total) * 100
                
                # Create progress bar
                bar_length = 10
                filled = int(bar_length * percentage // 100)
                bar = '▓' * filled + '░' * (bar_length - filled)
                
                progress_text = (
                    f"⬇️ **Downloading...**\n"
                    f"```[{bar}] {percentage:.1f}%```\n"
                    f"📊 `{downloaded/(1024*1024):.1f}MB / {total/(1024*1024):.1f}MB`\n"
                    f"⚡ `{speed/(1024*1024):.1f} MB/s`\n\n"
                    f"_Click /cancel to stop_"
                )
                
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=progress_text
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Progress callback error: {e}")

# ============================================================================
# Bot Handlers
# ============================================================================

@bot.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    """Start command"""
    await message.reply_text(
        f"🎬 **YouTube Downloader Bot**\n\n"
        f"**Bot Status:** ✅ Online\n"
        f"**FFmpeg:** {'✅ Installed' if check_ffmpeg() else '❌ Missing'}\n\n"
        f"**Commands:**\n"
        f"• Send any YouTube link\n"
        f"• /cookies - Upload cookies.txt\n"
        f"• /quality - Check available qualities\n"
        f"• /status - Bot status\n"
        f"• /cancel - Cancel download\n\n"
        f"**Developer:** {CREDIT}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📖 Help", callback_data="help"),
             InlineKeyboardButton("🔧 Support", url="https://t.me/example")]
        ])
    )

@bot.on_message(filters.command("cookies") & filters.private)
async def cookies_handler(client: Client, message: Message):
    """Handle cookies upload"""
    if not message.reply_to_message or not message.reply_to_message.document:
        await message.reply_text(
            "📁 **How to upload cookies:**\n\n"
            "1. Install 'Get cookies.txt' browser extension\n"
            "2. Login to YouTube in browser\n"
            "3. Export cookies as .txt file\n"
            "4. Reply to that file with /cookies\n\n"
            "This allows downloading private/members-only videos."
        )
        return
    
    doc = message.reply_to_message.document
    if not doc.file_name.endswith('.txt'):
        await message.reply_text("❌ Please upload a .txt file")
        return
    
    try:
        # Download file
        temp_path = await client.download_media(doc)
        
        # Validate it's a cookies file
        with open(temp_path, 'r') as f:
            content = f.read()
            if 'youtube.com' not in content and '# Netscape HTTP Cookie File' not in content:
                os.remove(temp_path)
                await message.reply_text("❌ This doesn't look like a valid cookies.txt file")
                return
        
        # Save to cookies file
        with open(COOKIES_FILE, 'w') as f:
            f.write(content)
        
        os.remove(temp_path)
        
        await message.reply_text(
            "✅ **Cookies uploaded successfully!**\n\n"
            "You can now download private/members-only videos.\n"
            f"File saved as: `{COOKIES_FILE.name}`"
        )
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")

@bot.on_message(filters.command("quality") & filters.private)
async def quality_handler(client: Client, message: Message):
    """Check available qualities for a video"""
    if len(message.command) < 2:
        await message.reply_text(
            "📝 **Usage:** `/quality [youtube_url]`\n\n"
            "Example: `/quality https://youtu.be/example`"
        )
        return
    
    url = message.command[1]
    status_msg = await message.reply_text("🔍 Checking available qualities...")
    
    try:
        info = await get_video_info(url, COOKIES_FILE.exists())
        if not info:
            await status_msg.edit("❌ Failed to fetch video info")
            return
        
        formats = get_available_formats(info)
        if not formats:
            await status_msg.edit("❌ No video formats found")
            return
        
        quality_text = f"📊 **Available Qualities for:**\n`{info.get('title', 'Unknown')[:50]}`\n\n"
        for fmt in formats:
            size = f"{fmt['filesize']/(1024*1024):.1f}MB" if fmt['filesize'] else "Unknown"
            quality_text += f"• **{fmt['height']}p** - {fmt['ext']} - {size}\n"
        
        quality_text += f"\n**Total Duration:** {info.get('duration', 0)//60}:{info.get('duration', 0)%60:02d}"
        
        await status_msg.edit(quality_text)
        
    except Exception as e:
        await status_msg.edit(f"❌ Error: {str(e)}")

@bot.on_message(filters.command("status") & filters.private)
async def status_handler(client: Client, message: Message):
    """Bot status"""
    import psutil
    import platform
    
    # System info
    cpu_percent = psutil.cpu_percent()
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    
    # Bot info
    total_downloads = len(list(DOWNLOAD_PATH.glob('*.mp4')))
    
    status_text = (
        f"🤖 **Bot Status**\n\n"
        f"**System:**\n"
        f"• CPU: {cpu_percent}%\n"
        f"• RAM: {memory.percent}%\n"
        f"• Disk: {disk.percent}%\n"
        f"• Python: {platform.python_version()}\n\n"
        f"**Bot:**\n"
        f"• FFmpeg: {'✅' if check_ffmpeg() else '❌'}\n"
        f"• Cookies: {'✅' if COOKIES_FILE.exists() else '❌'}\n"
        f"• Downloads: {total_downloads}\n"
        f"• Active: {len(current_tasks)}\n\n"
        f"**Developer:** {CREDIT}"
    )
    
    await message.reply_text(status_text)

@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_handler(client: Client, message: Message):
    """Cancel current download"""
    global cancel_requested
    
    user_id = message.from_user.id
    if user_id in current_tasks:
        cancel_requested = True
        current_tasks[user_id].cancel()
        await message.reply_text("🛑 Download cancelled")
    else:
        await message.reply_text("ℹ️ No active download to cancel")

@bot.on_message(filters.regex(r'(https?://(?:www\.)?(?:youtube\.com|youtu\.be)/[^\s]+)') & filters.private)
async def youtube_handler(client: Client, message: Message):
    """Handle YouTube URLs"""
    global processing_request
    
    if processing_request:
        await message.reply_text("⏳ Please wait, another download is in progress...")
        return
    
    url = message.matches[0].group(0)
    user_id = message.from_user.id
    
    # Check if already downloading
    if user_id in current_tasks:
        await message.reply_text("⚠️ You already have a download in progress")
        return
    
    processing_request = True
    current_tasks[user_id] = asyncio.current_task()
    
    try:
        # Ask for quality
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("144p", callback_data=f"quality_144_{url}"),
                InlineKeyboardButton("360p", callback_data=f"quality_360_{url}"),
                InlineKeyboardButton("720p", callback_data=f"quality_720_{url}")
            ],
            [
                InlineKeyboardButton("1080p", callback_data=f"quality_1080_{url}"),
                InlineKeyboardButton("Best", callback_data=f"quality_best_{url}")
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_download")]
        ])
        
        await message.reply_text(
            "📏 **Select Video Quality:**\n\n"
            "Choose the resolution you want to download:",
            reply_markup=keyboard
        )
        
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")
        processing_request = False
        current_tasks.pop(user_id, None)

@bot.on_callback_query(filters.regex(r'^quality_'))
async def quality_callback(client, callback_query):
    """Handle quality selection"""
    global cancel_requested
    
    data = callback_query.data
    parts = data.split('_')
    
    if len(parts) < 3:
        await callback_query.answer("Invalid selection")
        return
    
    quality = parts[1]
    url = '_'.join(parts[2:])  # Reconstruct URL
    
    await callback_query.message.edit_text(f"⏳ Starting download at {quality}p...")
    
    user_id = callback_query.from_user.id
    chat_id = callback_query.message.chat.id
    message_id = callback_query.message.id
    
    try:
        # Download video
        success, result = await download_video_with_progress(
            url=url,
            resolution=quality if quality != 'best' else 2160,
            chat_id=chat_id,
            message_id=message_id
        )
        
        if success and isinstance(result, str) and os.path.exists(result):
            # Upload to Telegram
            await callback_query.message.edit_text("📤 Uploading to Telegram...")
            
            # Get video info for caption
            info = await get_video_info(url, COOKIES_FILE.exists())
            title = info.get('title', 'YouTube Video') if info else "YouTube Video"
            duration = info.get('duration', 0) if info else 0
            
            caption = (
                f"🎬 **{title[:50]}**\n\n"
                f"📊 Quality: {quality}p\n"
                f"⏱ Duration: {duration//60}:{duration%60:02d}\n"
                f"👤 Downloaded by: {callback_query.from_user.mention}\n\n"
                f"🔗 **Original URL:** [Click Here]({url})"
            )
            
            # Send video
            await client.send_video(
                chat_id=chat_id,
                video=result,
                caption=caption,
                supports_streaming=True,
                progress=progress_bar,
                progress_args=(client, callback_query.message, "📤 Uploading...")
            )
            
            # Clean up
            os.remove(result)
            await callback_query.message.delete()
            
        else:
            await callback_query.message.edit_text(f"❌ Download failed:\n{result}")
            
    except asyncio.CancelledError:
        await callback_query.message.edit_text("🛑 Download cancelled by user")
    except Exception as e:
        logger.error(f"Download error: {e}")
        await callback_query.message.edit_text(f"❌ Error: {str(e)[:200]}")
    finally:
        global processing_request
        processing_request = False
        current_tasks.pop(user_id, None)
        cancel_requested = False

@bot.on_callback_query(filters.regex(r'^cancel_download'))
async def cancel_callback(client, callback_query):
    """Cancel download from inline keyboard"""
    global cancel_requested
    cancel_requested = True
    await callback_query.message.edit_text("🛑 Download cancelled")
    await callback_query.answer("Cancelled")

async def progress_bar(current, total, client, message, status):
    """Progress bar for upload"""
    try:
        percentage = (current / total) * 100
        bar_length = 10
        filled = int(bar_length * percentage // 100)
        bar = '▓' * filled + '░' * (bar_length - filled)
        
        await client.edit_message_text(
            chat_id=message.chat.id,
            message_id=message.id,
            text=f"{status}\n```[{bar}] {percentage:.1f}%```"
        )
    except Exception:
        pass

# ============================================================================
# Web Server for Render
# ============================================================================

async def health_check(request):
    """Health check endpoint"""
    return web.Response(text="Bot is running!", status=200)

async def bot_info(request):
    """Bot info endpoint"""
    try:
        me = await bot.get_me()
        info = {
            "status": "online",
            "bot_username": me.username,
            "bot_id": me.id,
            "ffmpeg": check_ffmpeg(),
            "cookies": COOKIES_FILE.exists()
        }
        return web.json_response(info)
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)

async def start_web_server():
    """Start aiohttp web server"""
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    app.router.add_get('/info', bot_info)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, HOST, PORT)
    await site.start()
    
    logger.info(f"✅ Web server running on http://{HOST}:{PORT}")
    return runner

# ============================================================================
# Main Function
# ============================================================================

async def main():
    """Main function"""
    logger.info("🚀 Starting YouTube Downloader Bot...")
    
    # Check environment
    missing_vars = []
    if not API_ID:
        missing_vars.append("API_ID")
    if not API_HASH:
        missing_vars.append("API_HASH")
    if not BOT_TOKEN:
        missing_vars.append("BOT_TOKEN")
    
    if missing_vars:
        logger.error(f"❌ Missing environment variables: {', '.join(missing_vars)}")
        sys.exit(1)
    
    # Check FFmpeg
    if not check_ffmpeg():
        logger.warning("⚠️ FFmpeg not found. Some features may not work.")
    
    # Start web server first (for Render health checks)
    try:
        web_runner = await start_web_server()
    except Exception as e:
        logger.error(f"❌ Failed to start web server: {e}")
        web_runner = None
    
    # Start the bot
    try:
        await bot.start()
        bot_info = await bot.get_me()
        logger.info(f"✅ Bot started: @{bot_info.username}")
        
        # Set bot commands
        await bot.set_bot_commands([
            ("start", "Start the bot"),
            ("cookies", "Upload cookies.txt"),
            ("quality", "Check available qualities"),
            ("status", "Bot status"),
            ("cancel", "Cancel download")
        ])
        
        # Keep running
        await idle()
        
    except Exception as e:
        logger.error(f"❌ Failed to start bot: {e}")
    finally:
        # Cleanup
        await bot.stop()
        if web_runner:
            await web_runner.cleanup()
        logger.info("👋 Bot stopped")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")