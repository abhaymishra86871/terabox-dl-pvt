import asyncio
import json
import logging
import os
import re
import time

import redis.asyncio as aioredis

from config import DOWNLOAD_DIR, MAX_FILE_MB, OWNER_ID, REDIS_URL
from downloader import download
from resolver import resolve

log = logging.getLogger("jobs")

QUEUE = "terabox:queue"
CURRENT = "terabox:current"
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"}

r = aioredis.from_url(
    REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=10,
    socket_timeout=20,
    health_check_interval=30,
)
wake = asyncio.Event()


def fmt_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024


def bar(done, total, width=12):
    if not total:
        return ""
    filled = int(width * done / total)
    return f"[{'█' * filled}{'░' * (width - filled)}] {done * 100 // total}%"


class Status:
    """One status message, edited at most every 4 seconds (avoids Telegram flood limits)."""

    def __init__(self, app, chat_id, msg_id):
        self.app, self.chat_id, self.msg_id = app, chat_id, msg_id
        self.last = 0.0
        self.last_text = ""

    async def set(self, text, force=False):
        now = time.monotonic()
        if text == self.last_text or (not force and now - self.last < 4):
            return
        self.last = now
        try:
            await self.app.edit_message_text(self.chat_id, self.msg_id, text)
            self.last_text = text
        except Exception:  # noqa: BLE001
            pass


async def enqueue(chat_id, msg_id, url):
    await r.rpush(QUEUE, json.dumps({"chat_id": chat_id, "msg_id": msg_id, "url": url}))
    wake.set()
    return await r.llen(QUEUE)


async def queue_length():
    return await r.llen(QUEUE)


async def check_redis():
    await r.ping()


async def process(app, job):
    chat_id, url = job["chat_id"], job["url"]
    status = Status(app, chat_id, job["msg_id"])

    await status.set("🔎 Getting file info...", force=True)
    try:
        files = await resolve(url)
    except Exception as e:  # noqa: BLE001
        await status.set(f"❌ Could not read this link.\n{str(e)[:1500]}", force=True)
        return

    for i, info in enumerate(files, 1):
        label = f"({i}/{len(files)}) " if len(files) > 1 else ""
        name = re.sub(r'[\\/:*?"<>|]', "_", info.name)[:150]
        if MAX_FILE_MB and info.size and info.size > MAX_FILE_MB * 1024 * 1024:
            await app.send_message(chat_id, f"⚠️ Skipped {name}: {fmt_size(info.size)} is over the {MAX_FILE_MB} MB limit.")
            continue

        path = os.path.join(DOWNLOAD_DIR, f"{int(time.time())}_{name}")
        try:
            async def dl_progress(done, total):
                text = f"⬇️ {label}Downloading\n{name}\n{fmt_size(done)}"
                if total:
                    text += f" / {fmt_size(total)}\n{bar(done, total)}"
                await status.set(text)

            await download(info, path, dl_progress)

            size = os.path.getsize(path)
            caption = f"🎬 {name}\n📦 {fmt_size(size)}"

            async def up_progress(done, total):
                await status.set(f"⬆️ {label}Uploading\n{name}\n{fmt_size(done)} / {fmt_size(total)}\n{bar(done, total)}")

            await status.set(f"⬆️ {label}Uploading\n{name}", force=True)
            ext = os.path.splitext(name)[1].lower()
            sent = False
            if ext in VIDEO_EXT:
                try:
                    await app.send_video(chat_id, path, caption=caption, supports_streaming=True, progress=up_progress)
                    sent = True
                except Exception as e:  # noqa: BLE001
                    log.warning("send_video failed, falling back to document: %s", e)
            if not sent:
                await app.send_document(chat_id, path, caption=caption, progress=up_progress)
        except Exception as e:  # noqa: BLE001
            log.exception("job failed")
            await app.send_message(chat_id, f"❌ Failed: {name}\n{type(e).__name__}: {str(e)[:1500]}")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    try:
        await app.delete_messages(chat_id, job["msg_id"])
    except Exception:  # noqa: BLE001
        pass


async def _worker_loop(app):
    # A job that was running when the server restarted is lost: tell the owner.
    stale = await r.get(CURRENT)
    if stale:
        await r.delete(CURRENT)
        try:
            old = json.loads(stale)
            await app.send_message(old["chat_id"], f"⚠️ The server restarted during a job. Please resend:\n{old['url']}")
        except Exception:  # noqa: BLE001
            pass

    wake.set()  # drain anything already queued
    while True:
        try:
            await asyncio.wait_for(wake.wait(), timeout=600)
        except asyncio.TimeoutError:
            pass
        wake.clear()

        while True:
            raw = await r.lpop(QUEUE)
            if not raw:
                break
            await r.set(CURRENT, raw)
            try:
                await process(app, json.loads(raw))
            except Exception:  # noqa: BLE001
                log.exception("worker error")
            finally:
                await r.delete(CURRENT)


async def worker(app):
    """Runs the worker forever: if anything fails (e.g. Redis), log it and try again."""
    while True:
        try:
            await _worker_loop(app)
        except Exception:  # noqa: BLE001
            log.exception("Worker crashed, restarting in 10 seconds")
            await asyncio.sleep(10)
