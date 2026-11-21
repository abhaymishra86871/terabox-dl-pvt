import re

from pyrogram import filters
from pyrogram.handlers import MessageHandler

from config import OWNER_ID
from jobs import enqueue, queue_length
from resolver import check_cookie

DOMAINS = (
    r"terabox\.com|1024terabox\.com|teraboxapp\.com|teraboxlink\.com|terasharelink\.com|"
    r"1024tera\.com|4funbox\.com|mirrobox\.com|nephobox\.com|momerybox\.com|tibibox\.com|"
    r"freeterabox\.com|terafileshare\.com|terabox\.app"
)
LINK_RE = re.compile(rf"https?://(?:[\w-]+\.)*(?:{DOMAINS})/\S+", re.I)

owner_only = filters.user(OWNER_ID)


async def start_cmd(client, message):
    await message.reply_text("👋 Send me a Terabox link and I'll send you the video.\n\n/status - queue length\n/check - test the Terabox cookie")


async def status_cmd(client, message):
    try:
        n = await queue_length()
    except Exception as e:  # noqa: BLE001
        await message.reply_text(f"❌ Cannot reach Redis: {e}")
        return
    await message.reply_text(f"📋 Jobs waiting in queue: {n}")


async def check_cmd(client, message):
    try:
        await message.reply_text(await check_cookie())
    except Exception as e:  # noqa: BLE001
        await message.reply_text(f"❌ Check failed: {type(e).__name__}: {e}")


async def link_handler(client, message):
    links = list(dict.fromkeys(m.rstrip(").,;>") for m in LINK_RE.findall(message.text or "")))
    if not links:
        await message.reply_text("Send a valid Terabox link.")
        return
    for link in links:
        status = await message.reply_text("⏳ Added to queue...")
        try:
            pos = await enqueue(message.chat.id, status.id, link)
        except Exception as e:  # noqa: BLE001
            await status.edit_text(f"❌ Cannot reach Redis, so the job was not queued.\n{e}")
            continue
        if pos > 1:
            await status.edit_text(f"⏳ In queue (position {pos})")


def register(app):
    app.add_handler(MessageHandler(start_cmd, owner_only & filters.command(["start", "help"])))
    app.add_handler(MessageHandler(status_cmd, owner_only & filters.command("status")))
    app.add_handler(MessageHandler(check_cmd, owner_only & filters.command("check")))
    app.add_handler(MessageHandler(link_handler, owner_only & filters.text & ~filters.command(["start", "help", "status", "check"])))
