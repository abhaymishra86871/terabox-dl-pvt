"""
Turns a Terabox share link into direct download info.
This is the part that breaks most often. Everything else only calls resolve().
"""
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, quote, urlparse

import aiohttp

from config import RESOLVER_API_URL, TERABOX_COOKIE

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class ResolveError(Exception):
    pass


@dataclass
class FileInfo:
    name: str
    size: int  # 0 = unknown
    url: str
    headers: dict = field(default_factory=dict)


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _pick(data, keys):
    if isinstance(data, dict):
        for k in keys:
            if data.get(k):
                return data[k]
    return None


def extract_surl(link):
    parsed = urlparse(link)
    match = re.search(r"/s/([\w-]+)", parsed.path)
    if match:
        full = match.group(1)
        short = full[1:] if full.startswith("1") else full
        return full, short
    query = parse_qs(parsed.query)
    if "surl" in query:
        short = query["surl"][0]
        return "1" + short, short
    raise ResolveError("Could not find the share id in this link")


# ---------- Option A: third-party resolver API ----------
async def _from_api(link):
    url = RESOLVER_API_URL.replace("{url}", quote(link, safe=""))
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(headers={"User-Agent": UA}, timeout=timeout) as s:
        async with s.get(url) as r:
            data = await r.json(content_type=None)

    items = data
    if isinstance(data, dict):
        items = _pick(data, ["list", "files", "data", "result", "response", "links"]) or [data]
    if isinstance(items, dict):
        items = [items]

    out = []
    for it in items or []:
        dl = _pick(it, ["download_url", "direct_link", "dlink", "download", "fast_download_link", "url", "link"])
        if not dl:
            continue
        name = _pick(it, ["file_name", "filename", "server_filename", "name", "title"]) or "video.mp4"
        size = _to_int(_pick(it, ["size", "file_size", "filesize"]))
        out.append(FileInfo(str(name), size, dl, {"User-Agent": UA}))
    if not out:
        raise ResolveError("Resolver API returned no downloadable link")
    return out


# ---------- Option B: built-in scraper (best effort) ----------
async def _from_scraper(link):
    full, short = extract_surl(link)
    headers = {"User-Agent": UA, "Referer": "https://www.terabox.com/"}
    if TERABOX_COOKIE:
        cookie = TERABOX_COOKIE if "=" in TERABOX_COOKIE else f"ndus={TERABOX_COOKIE}"
        headers["Cookie"] = cookie

    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as s:
        params = {"app_id": "250528", "shorturl": full, "root": "1"}
        async with s.get("https://www.terabox.com/api/shorturlinfo", params=params) as r:
            info = await r.json(content_type=None)
        if info.get("errno") not in (0, None):
            raise ResolveError(f"Terabox returned error {info.get('errno')}")

        files = info.get("list") or []
        if not any(f.get("dlink") for f in files):
            params = {"app_id": "250528", "shorturl": short, "root": "1", "page": "1", "num": "100"}
            async with s.get("https://www.terabox.com/share/list", params=params) as r:
                data = await r.json(content_type=None)
            files = data.get("list") or files

    out = []
    for f in files:
        if str(f.get("isdir", "0")) == "1":
            continue
        dl = f.get("dlink")
        if not dl:
            continue
        dl_headers = {"User-Agent": UA}
        if "Cookie" in headers:
            dl_headers["Cookie"] = headers["Cookie"]
        out.append(FileInfo(f.get("server_filename", "video.mp4"), _to_int(f.get("size")), dl, dl_headers))
    if not out:
        raise ResolveError("No downloadable file found (link expired, private, or Terabox blocked it)")
    return out


async def resolve(link):
    errors = []
    if RESOLVER_API_URL:
        try:
            return await _from_api(link)
        except Exception as e:  # noqa: BLE001
            errors.append(f"API: {e}")
    try:
        return await _from_scraper(link)
    except Exception as e:  # noqa: BLE001
        errors.append(f"Scraper: {e}")
    raise ResolveError(" | ".join(errors))
