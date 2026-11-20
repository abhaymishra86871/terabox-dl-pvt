import os
import shutil

import aiohttp


async def download(info, path, on_progress):
    """Streams the file to disk in 1 MB chunks (never holds it in RAM)."""
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=120)
    async with aiohttp.ClientSession(headers=info.headers, timeout=timeout) as s:
        async with s.get(info.url, allow_redirects=True) as r:
            if r.status != 200:
                raise RuntimeError(f"Download failed (HTTP {r.status})")
            if "text/html" in r.headers.get("Content-Type", ""):
                raise RuntimeError("Download link returned a web page instead of a file (link expired?)")

            total = int(r.headers.get("Content-Length") or info.size or 0)
            if total:
                free = shutil.disk_usage(os.path.dirname(path)).free
                if total > free - 50 * 1024 * 1024:
                    raise RuntimeError("Not enough disk space on the server for this file")

            done = 0
            with open(path, "wb") as f:
                async for chunk in r.content.iter_chunked(1024 * 1024):
                    f.write(chunk)
                    done += len(chunk)
                    await on_progress(done, total)
    if done == 0:
        raise RuntimeError("Downloaded file is empty")
    return done
