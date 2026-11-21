"""
Downloads a file to disk. Uses several parallel connections (like IDM / aria2),
because Terabox slows down every single connection. Falls back to one connection if needed.
"""
import asyncio
import logging
import os
import shutil

import aiohttp

log = logging.getLogger("downloader")

CONNECTIONS = max(1, int(os.getenv("DL_CONNECTIONS", "8")))  # set DL_CONNECTIONS=1 to turn parallel off
PART_SIZE = 4 * 1024 * 1024
MB = 1024 * 1024


def _http_error(status):
    hint = {
        401: "not logged in",
        403: "Terabox refused it: link expired or this server's IP is blocked",
        404: "file not found / link expired",
        429: "too many requests, try later",
    }.get(status, "unexpected answer from the download server")
    return f"Download failed (HTTP {status}: {hint})"


def _check_space(path, total):
    free = shutil.disk_usage(os.path.dirname(path)).free
    if total > free - 50 * MB:
        raise RuntimeError("Not enough disk space on the server for this file")


async def _probe(session, info):
    """Asks for the first byte only, to learn the size, the final CDN address and whether ranges work."""
    async with session.get(info.url, headers={"Range": "bytes=0-0"}, allow_redirects=True) as r:
        if r.status not in (200, 206):
            raise RuntimeError(_http_error(r.status))
        if "text/html" in r.headers.get("Content-Type", ""):
            raise RuntimeError("Download link returned a web page instead of a file (link expired?)")
        final_url = str(r.url)
        if r.status == 206:
            tail = r.headers.get("Content-Range", "").rsplit("/", 1)[-1]
            total = int(tail) if tail.isdigit() else 0
            return total, final_url, total > 0
        return int(r.headers.get("Content-Length") or info.size or 0), final_url, False


async def _single(session, url, path, total, on_progress):
    async with session.get(url, allow_redirects=True) as r:
        if r.status != 200:
            raise RuntimeError(_http_error(r.status))
        done = 0
        with open(path, "wb") as f:
            async for chunk in r.content.iter_chunked(MB):
                f.write(chunk)
                done += len(chunk)
                await on_progress(done, total)
    if done == 0:
        raise RuntimeError("Downloaded file is empty")
    return done


async def _parallel(session, url, path, total, on_progress):
    with open(path, "wb") as f:
        f.truncate(total)

    queue = asyncio.Queue()
    for start in range(0, total, PART_SIZE):
        queue.put_nowait((start, min(start + PART_SIZE, total) - 1))
    state = {"done": 0}

    async def worker():
        with open(path, "r+b") as f:
            while True:
                try:
                    start, end = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                for attempt in range(1, 4):
                    pos = start
                    try:
                        async with session.get(url, headers={"Range": f"bytes={start}-{end}"}) as r:
                            if r.status != 206:
                                raise RuntimeError(f"range request answered HTTP {r.status}")
                            async for chunk in r.content.iter_chunked(256 * 1024):
                                f.seek(pos)
                                f.write(chunk)
                                pos += len(chunk)
                                state["done"] += len(chunk)
                                await on_progress(state["done"], total)
                        if pos != end + 1:
                            raise RuntimeError("a part ended early")
                        break
                    except Exception:
                        state["done"] -= pos - start  # take back what this part had counted
                        if attempt == 3:
                            raise
                        await asyncio.sleep(attempt * 2)

    tasks = [asyncio.create_task(worker()) for _ in range(min(CONNECTIONS, queue.qsize()))]
    try:
        await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return total


async def download(info, path, on_progress):
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=120)
    connector = aiohttp.TCPConnector(limit=CONNECTIONS + 2)
    async with aiohttp.ClientSession(headers=info.headers, timeout=timeout, connector=connector) as s:
        total, url, ranged = await _probe(s, info)
        if total:
            _check_space(path, total)
        if ranged and CONNECTIONS > 1:
            try:
                return await _parallel(s, url, path, total, on_progress)
            except Exception as e:  # noqa: BLE001
                log.warning("Parallel download failed (%s). Trying one connection.", e)
        return await _single(s, url, path, total, on_progress)
