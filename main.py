import asyncio
import logging
import os
import shutil

from pyrogram import Client, idle

from config import API_HASH, API_ID, BOT_TOKEN, DOWNLOAD_DIR
from handlers import register
from jobs import worker
from server import start_server


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    shutil.rmtree(DOWNLOAD_DIR, ignore_errors=True)  # wipe leftovers from a crash
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    await start_server()

    app = Client("terabox_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
    register(app)
    await app.start()
    asyncio.create_task(worker(app))
    logging.info("Bot started")
    await idle()
    await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
