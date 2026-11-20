import re

from pyrogram import filters
from pyrogram.handlers import MessageHandler

from config import OWNER_ID
from jobs import enqueue, queue_length

DOMAINS = (
    r"terabox\.com|1024terabox\.com|teraboxapp\.com|teraboxlink\.com|terasharelink\.com|"
    r"1024tera\.com|4funbox\.com|mirrobox\.com|nephobox\.com|momerybox\.com|tibibox\.com|"
    r"freeterabox\.com|terafileshare\.com|terabox\.app"
)
LINK_RE = re.compile(rf"https?://(?:[\w-]+\.)*(?:{DOMAINS})/\S+", re.I)

owner_only = filters.user(OWNER_ID)


async def start_cmd(client, message):
    await message.reply_text("👋 Send me a Terabox link and I'll send you the video.\n\n/status - queue length")


async def status_cmd(client, message):
    await message.reply_text(f"📋 Jobs waiting in queue: {await queue_length()}")


async def link_handler(client, message):
    links = list(dict.fromkeys(m.rstrip(").,;>") for m in LINK_RE.findall(message.text or "")))
    if not links:
        await message.reply_text("Send a valid Terabox link.")
        return
    for link in links:
        status = await message.reply_text("⏳ Added to queue...")
        pos = await enqueue(message.chat.id, status.id, link)
        if pos > 1:
            await status.edit_text(f"⏳ In queue (position {pos})")


def register(app):
    app.add_handler(MessageHandler(start_cmd, owner_only & filters.command(["start", "help"])))
    app.add_handler(MessageHandler(status_cmd, owner_only & filters.command("status")))
    app.add_handler(MessageHandler(link_handler, owner_only & filters.text & ~filters.command(["start", "help", "status"])))
