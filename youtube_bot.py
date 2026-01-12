#!/usr/bin/env python3
"""
YouTube Downloader Bot - Deployable on Render
"""

import os
import re
import sys
import asyncio
import logging
import aiohttp
from aiohttp import web
from pyrogram import Client, filters
from pyrogram.types import Message
import yt_dlp
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('youtube_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Environment variables
API_ID = int(os.getenv('API_ID', 0))
API_HASH = os.getenv('API_HASH', '')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
OWNER_ID = int(os.getenv('OWNER_ID', 0))
PORT = int(os.getenv('PORT', 8080))  # Render provides this
HOST = os.getenv('HOST', '0.0.0.0')
CREDIT = os.getenv('CREDIT', 'YouTube Downloader Bot')

# Global variables
processing_request = False
cancel_requested = False
DOWNLOAD_PATH = "./downloads"
COOKIES_FILE = "youtube_cookies.txt"

# Create necessary directories
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

# Initialize bot
bot = Client(
    "youtube_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=100
)

# ============================================================================
# Progress Hook for yt-dlp
# ============================================================================

def progress_hook(d, message, status_message_id):
    """
    Progress hook for yt-dlp downloads
    """
    global cancel_requested
    
    if cancel_requested:
        raise Exception("Download cancelled by user")
    
    if d['status'] == 'downloading':
        try:
            total = d.get('total_bytes', 0) or d.get('total_bytes_estimate', 0)
            downloaded = d.get('downloaded_bytes', 0)
            speed = d.get('speed', 0)
            
            if total and downloaded:
                percentage = (downloaded / total) * 100
                
                # Format speed
                if speed:
                    if speed > 1024 * 1024:
                        speed_str = f"{speed / (1024 * 1024):.1f} MB/s"
                    elif speed > 1024:
                        speed_str = f"{speed / 1024:.1f} KB/s"
                    else:
                        speed_str = f"{speed:.1f} B/s"
                else:
                    speed_str = "Calculating..."
                
                # Create progress bar
                bar_length = 20
                filled_length = int(bar_length * percentage // 100)
                bar = '█' * filled_length + '░' * (bar_length - filled_length)
                
                progress_text = (
                    f"📥 **Downloading YouTube Video**\n\n"
                    f"**Progress:** [{bar}] {percentage:.1f}%\n"
                    f"**Downloaded:** {downloaded / (1024 * 1024):.1f} MB / {total / (1024 * 1024):.1f} MB\n"
                    f"**Speed:** {speed_str}\n\n"
                    f"🛑 Send `/cancel` to stop"
                )
                
                # Update message
                asyncio.create_task(
                    bot.edit_message_text(
                        chat_id=message.chat.id,
                        message_id=status_message_id,
                        text=progress_text
                    )
                )
                
        except Exception as e:
            logger.error(f"Progress hook error: {e}")

# ============================================================================
# Download YouTube Video
# ============================================================================

async def download_youtube_video(url, chat_id, resolution="720"):
    """
    Download YouTube video with specified resolution
    """
    global processing_request, cancel_requested
    
    try:
        # Map resolution to yt-dlp format
        resolution_map = {
            "144": "bv*[height<=144][ext=mp4]+ba[ext=m4a]/b[height<=144]",
            "240": "bv*[height<=240][ext=mp4]+ba[ext=m4a]/b[height<=240]",
            "360": "bv*[height<=360][ext=mp4]+ba[ext=m4a]/b[height<=360]",
            "480": "bv*[height<=480][ext=mp4]+ba[ext=m4a]/b[height<=480]",
            "720": "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720]",
            "1080": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]",
            "best": "bv*[ext=mp4]+ba[ext=m4a]/b"
        }
        
        ytf = resolution_map.get(resolution, resolution_map["720"])
        res_display = f"{resolution}p" if resolution != "best" else "Best Quality"
        
        # Get video info
        ydl_opts_info = {
            'quiet': True,
            'no_warnings': True,
        }
        
        # Add cookies if available
        if os.path.exists(COOKIES_FILE):
            ydl_opts_info['cookiefile'] = COOKIES_FILE
        
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = ydl.extract_info(url, download=False)
            video_title = info.get('title', 'YouTube_Video')
            video_title_clean = re.sub(r'[<>:"/\\|?*]', '', video_title)[:50]
        
        # Prepare download options
        filename = f"{DOWNLOAD_PATH}/{video_title_clean}_{res_display}.%(ext)s"
        
        ydl_opts = {
            'format': ytf,
            'outtmpl': filename,
            'quiet': False,
            'no_warnings': False,
            'merge_output_format': 'mp4',
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
        }
        
        # Add cookies if available
        if os.path.exists(COOKIES_FILE):
            ydl_opts['cookiefile'] = COOKIES_FILE
        
        # Send status message
        status_message = await bot.send_message(
            chat_id=chat_id,
            text=f"📥 **Downloading YouTube Video**\n\n"
                 f"**Title:** {video_title_clean}\n"
                 f"**Resolution:** {res_display}\n"
                 f"**Status:** Starting download...\n\n"
                 f"🛑 Send `/cancel` to stop"
        )
        
        # Custom progress hook
        def custom_progress_hook(d):
            progress_hook(d, status_message, status_message.id)
        
        ydl_opts['progress_hooks'] = [custom_progress_hook]
        
        # Start download
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            # Get downloaded file path
            downloaded_file = ydl.prepare_filename(info)
            if downloaded_file.endswith('.webm'):
                downloaded_file = downloaded_file.replace('.webm', '.mp4')
            elif downloaded_file.endswith('.mkv'):
                downloaded_file = downloaded_file.replace('.mkv', '.mp4')
            
            # Find actual file
            if not os.path.exists(downloaded_file):
                base_name = downloaded_file.rsplit('.', 1)[0]
                for ext in ['.mp4', '.mkv', '.webm']:
                    if os.path.exists(base_name + ext):
                        downloaded_file = base_name + ext
                        break
            
            # Update status
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_message.id,
                text="📤 **Uploading to Telegram...**"
            )
            
            # Prepare caption
            caption = (
                f"**🎬 YouTube Video**\n"
                f"**Title:** {info.get('title', 'Unknown')}\n"
                f"**Resolution:** {res_display}\n"
                f"**Duration:** {info.get('duration', 0) // 60}:{info.get('duration', 0) % 60:02d}\n"
                f"**Channel:** {info.get('uploader', 'Unknown')}\n\n"
                f"**Downloaded by:** {CREDIT}"
            )
            
            # Upload video
            await bot.send_video(
                chat_id=chat_id,
                video=downloaded_file,
                caption=caption,
                supports_streaming=True,
                progress=lambda current, total: asyncio.sleep(0.1)  # Simple progress
            )
            
            # Delete status message
            await bot.delete_messages(chat_id, status_message.id)
            
            # Clean up
            if os.path.exists(downloaded_file):
                os.remove(downloaded_file)
            
            return True, "✅ **Download Complete!**"
            
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        if "Private video" in error_msg:
            return False, "🔒 **Private Video**\nThis video is private and cannot be downloaded."
        elif "Members-only" in error_msg:
            return False, "👥 **Members-Only Video**\nThis video is for channel members only."
        elif "Sign in" in error_msg or "login" in error_msg.lower():
            return False, (
                "🔑 **Authentication Required**\n\n"
                "This video requires login. Please:\n"
                "1. Export cookies from your browser\n"
                "2. Send them using `/cookies` command\n"
                "3. Try downloading again"
            )
        else:
            return False, f"❌ **Download Failed**\n\nError: {error_msg[:200]}"
    
    except Exception as e:
        return False, f"❌ **Unexpected Error**\n\n{str(e)[:200]}"
    
    finally:
        processing_request = False
        cancel_requested = False

# ============================================================================
# Bot Handlers
# ============================================================================

@bot.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    """Start command handler"""
    await message.reply_text(
        f"🎬 **YouTube Downloader Bot**\n\n"
        f"**Features:**\n"
        f"• Download YouTube videos\n"
        f"• Multiple resolutions (144p to 1080p)\n"
        f"• Members-only videos support (with cookies)\n"
        f"• Fast downloads\n\n"
        f"**Commands:**\n"
        f"• Send YouTube URL directly\n"
        f"• `/download [url]` - Download video\n"
        f"• `/cookies` - Upload cookies file\n"
        f"• `/getcookies` - Get current cookies\n"
        f"• `/cancel` - Cancel download\n"
        f"• `/status` - Check bot status\n\n"
        f"**Made by:** {CREDIT}"
    )

@bot.on_message(filters.command("download") & filters.private)
async def download_command(client: Client, message: Message):
    """Handle /download command"""
    global processing_request
    
    if processing_request:
        await message.reply_text("⏳ **Please wait**, another download is in progress.")
        return
    
    if len(message.command) < 2:
        await message.reply_text(
            "📝 **Usage:**\n\n"
            "Send YouTube URL directly or use:\n"
            "`/download [youtube_url]`\n\n"
            "Example:\n"
            "`/download https://youtu.be/dQw4w9WgXcQ`"
        )
        return
    
    url = message.command[1]
    await handle_youtube_url(message, url)

@bot.on_message(filters.command("cookies") & filters.private)
async def cookies_handler(client: Client, message: Message):
    """Handle cookies upload"""
    if not message.reply_to_message or not message.reply_to_message.document:
        await message.reply_text(
            "📁 **Please reply to a cookies file with this command.**\n\n"
            "How to get cookies:\n"
            "1. Install 'Get cookies.txt LOCALLY' extension\n"
            "2. Go to YouTube and login\n"
            "3. Export cookies as .txt file\n"
            "4. Send it here with /cookies command"
        )
        return
    
    doc = message.reply_to_message.document
    if not doc.file_name.endswith('.txt'):
        await message.reply_text("❌ **Invalid file type.** Please upload a .txt file.")
        return
    
    try:
        # Download the file
        await message.reply_text("📥 Downloading cookies file...")
        file_path = await client.download_media(doc)
        
        # Read and save cookies
        with open(file_path, 'r') as f:
            cookies_content = f.read()
        
        with open(COOKIES_FILE, 'w') as f:
            f.write(cookies_content)
        
        # Clean up
        os.remove(file_path)
        
        await message.reply_text(
            "✅ **Cookies updated successfully!**\n\n"
            "You can now download members-only/private videos."
        )
        
    except Exception as e:
        await message.reply_text(f"❌ **Failed to update cookies:** {str(e)}")

@bot.on_message(filters.command("getcookies") & filters.private)
async def get_cookies_handler(client: Client, message: Message):
    """Get current cookies file"""
    if not os.path.exists(COOKIES_FILE):
        await message.reply_text("📭 **No cookies file found.**")
        return
    
    try:
        await client.send_document(
            chat_id=message.chat.id,
            document=COOKIES_FILE,
            caption="🔐 **YouTube Cookies File**"
        )
    except Exception as e:
        await message.reply_text(f"❌ **Error:** {str(e)}")

@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_handler(client: Client, message: Message):
    """Cancel current download"""
    global cancel_requested, processing_request
    
    if processing_request:
        cancel_requested = True
        await message.reply_text("🛑 **Cancellation requested...**")
    else:
        await message.reply_text("ℹ️ **No active download to cancel.**")

@bot.on_message(filters.command("status") & filters.private)
async def status_handler(client: Client, message: Message):
    """Check bot status"""
    global processing_request
    
    status_text = "🟢 **Bot is running**\n\n"
    status_text += f"**Active Downloads:** {1 if processing_request else 0}\n"
    status_text += f"**Cookies File:** {'✅ Present' if os.path.exists(COOKIES_FILE) else '❌ Missing'}\n"
    status_text += f"**Storage:** {round(os.path.getsize(DOWNLOAD_PATH) / (1024*1024), 2)} MB used\n\n"
    status_text += f"**Credit:** {CREDIT}"
    
    await message.reply_text(status_text)

@bot.on_message(filters.regex(r'(youtube\.com|youtu\.be)') & filters.private)
async def youtube_url_handler(client: Client, message: Message):
    """Auto-detect YouTube URLs"""
    global processing_request
    
    if processing_request:
        await message.reply_text("⏳ **Please wait**, another download is in progress.")
        return
    
    url = message.text.strip()
    await handle_youtube_url(message, url)

async def handle_youtube_url(message: Message, url: str):
    """Handle YouTube URL download"""
    global processing_request
    
    processing_request = True
    
    # Ask for resolution
    keyboard = [
        ["144p", "240p", "360p"],
        ["480p", "720p", "1080p"],
        ["Best Quality"]
    ]
    
    reply_markup = {
        "keyboard": keyboard,
        "resize_keyboard": True,
        "one_time_keyboard": True
    }
    
    # Send resolution selection
    resolution_msg = await message.reply_text(
        "📏 **Select Resolution:**\n\n"
        "Choose your preferred video quality:",
        reply_markup=reply_markup
    )
    
    # Wait for resolution selection
    try:
        resolution_response = await bot.listen(
            chat_id=message.chat.id,
            filters=filters.text & filters.user(message.from_user.id),
            timeout=30
        )
        resolution = resolution_response.text.lower().replace('p', '').replace('best quality', 'best')
        
        # Remove keyboard
        await resolution_msg.delete()
        await resolution_response.delete()
        
        # Start download
        success, result = await download_youtube_video(
            url=url,
            chat_id=message.chat.id,
            resolution=resolution
        )
        
        await message.reply_text(result)
        
    except asyncio.TimeoutError:
        await resolution_msg.delete()
        await message.reply_text("⏱️ **Timeout** - No resolution selected.")
    except Exception as e:
        await message.reply_text(f"❌ **Error:** {str(e)}")
    finally:
        processing_request = False

# ============================================================================
# Web Server for Render
# ============================================================================

async def health_check(request):
    """Health check endpoint for Render"""
    return web.Response(text="YouTube Bot is running!", status=200)

async def start_web_server():
    """Start aiohttp web server for Render"""
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, HOST, PORT)
    await site.start()
    
    logger.info(f"Web server started on http://{HOST}:{PORT}")
    return runner

# ============================================================================
# Main Function
# ============================================================================

async def main():
    """Main function to run bot and web server"""
    logger.info("Starting YouTube Downloader Bot...")
    
    # Validate environment variables
    if not all([API_ID, API_HASH, BOT_TOKEN]):
        logger.error("Missing required environment variables!")
        logger.error("Please set API_ID, API_HASH, and BOT_TOKEN")
        sys.exit(1)
    
    try:
        # Start web server for Render
        web_runner = await start_web_server()
        
        # Start the bot
        await bot.start()
        
        # Get bot info
        bot_info = await bot.get_me()
        logger.info(f"Bot started: @{bot_info.username}")
        logger.info(f"Bot ID: {bot_info.id}")
        
        # Keep running
        await asyncio.Event().wait()
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
    finally:
        # Cleanup
        await bot.stop()
        await web_runner.cleanup()

if __name__ == "__main__":
    # Run the bot
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")