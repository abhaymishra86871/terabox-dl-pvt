"""
Turns a Terabox share link into direct download info.
This is the part that breaks most often. Everything else only calls resolve().

Flow (same calls the Terabox web player makes):
  /main -> jsToken -> /api/shorturlinfo -> /share/list -> (/share/download) -> dlink
Every step writes a short note, so when it fails the bot tells you exactly where.
"""
import json
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, quote, unquote, urlparse

import aiohttp

from config import RESOLVER_API_URL, TERABOX_COOKIE

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
APP_ID = "250528"
HOSTS = ["www.terabox.com", "www.1024terabox.com", "www.terabox.app"]

ERRNO_HINTS = {
    400210: "Terabox asks for human verification (it often blocks server IPs like Render's)",
    400141: "Terabox asks for verification / token rejected",
    4000020: "Terabox token rejected",
    -9: "This share needs a password or is private",
    -6: "Terabox says you are not logged in (cookie missing, wrong or expired)",
    -7: "This share was cancelled or you have no access",
    105: "Link is invalid or expired",
    2: "Terabox refused the request (most often: the login cookie is missing, wrong, expired or cut short)",
}


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
    raise ResolveError("Could not find the share id in this link (is it a full Terabox share link?)")


# ---------- Option A: third-party resolver API ----------
async def _from_api(link):
    url = RESOLVER_API_URL.replace("{url}", quote(link, safe=""))
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(headers={"User-Agent": UA}, timeout=timeout) as s:
        async with s.get(url) as r:
            text = await r.text()
            try:
                data = json.loads(text)
            except ValueError:
                raise ResolveError(f"API did not return JSON (HTTP {r.status}): {text[:150]}")

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
        raise ResolveError(f"API returned no download link. Response started with: {text[:150]}")
    return out


# ---------- Option B: built-in scraper ----------
TOKEN_PATTERNS = [
    r'fn\(["\']([0-9A-Fa-f]{20,})["\']\)',
    r"fn%28%22([0-9A-Fa-f]{20,})%22%29",
    r'"jsToken"\s*:\s*"([^"]{20,})"',
    r"jsToken=([0-9A-Za-z%]{20,})",
]


def _find_token(html):
    for pat in TOKEN_PATTERNS:
        m = re.search(pat, html or "")
        if m:
            return m.group(1)
    return ""


def _parse_cookie(raw):
    raw = (raw or "").strip().strip('"').strip("'")
    if not raw:
        return {}
    if "=" not in raw:
        return {"ndus": raw}
    out = {}
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class _Terabox:
    def __init__(self, host, session):
        self.base = f"https://{host}"
        self.s = session
        self.user_cookies = _parse_cookie(TERABOX_COOKIE)
        self.cookies = dict(self.user_cookies)

    def headers(self):
        h = {"User-Agent": UA, "Referer": self.base + "/", "Accept": "application/json, text/plain, */*"}
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        return h

    async def get(self, path, params=None):
        async with self.s.get(self.base + path, params=params, headers=self.headers()) as r:
            for k, c in r.cookies.items():
                if k not in self.user_cookies:  # keep YOUR login cookie, never let a guest one replace it
                    self.cookies[k] = c.value
            return r.status, await r.text()

    async def get_json(self, path, params=None):
        status, text = await self.get(path, params)
        try:
            return json.loads(text)
        except ValueError:
            return {"errno": "not-json", "errmsg": f"HTTP {status}, not JSON: {text[:120]!r}"}


def _why(data):
    errno = data.get("errno")
    hint = ERRNO_HINTS.get(errno) if isinstance(errno, int) else None
    extra = data.get("errmsg") or data.get("show_msg") or ""
    return f"errno {errno}: {hint or extra or 'unknown'}"


async def _account_status(tb, common):
    if not tb.cookies.get("ndus"):
        return "no ndus cookie is being sent (TERABOX_COOKIE empty or wrong format)"
    params = dict(common)
    try:
        _, html = await tb.get("/main", {"category": "all"})
        token = _find_token(html)
        if token:
            params["jsToken"] = token
    except Exception:  # noqa: BLE001
        pass
    params.update({"dir": "/", "num": "1", "page": "1", "order": "time", "desc": "1"})
    data = await tb.get_json("/api/list", params)
    errno = data.get("errno")
    if errno == 0:
        return "✅ logged in: Terabox accepts the cookie"
    if errno == "not-json":
        return f"⚠️ inconclusive: the check got a web page instead of data ({data.get('errmsg')})"
    if errno in (-6, 4000023):
        return f"❌ Terabox says NOT logged in: the cookie is rejected (errno {errno})"
    return f"⚠️ unclear answer from Terabox: {_why(data)}"


async def check_cookie():
    """Used by the /check command: tells you if the cookie in Render works."""
    if not TERABOX_COOKIE:
        return "❌ TERABOX_COOKIE is not set in Render."
    ck = _parse_cookie(TERABOX_COOKIE)
    ndus = ck.get("ndus", "")
    if not ndus:
        return f"❌ No 'ndus' value found. Cookie names seen: {', '.join(ck) or 'none'}"
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout, cookie_jar=aiohttp.DummyCookieJar()) as s:
        tb = _Terabox("www.terabox.com", s)
        common = {"app_id": APP_ID, "web": "1", "channel": "dubox", "clienttype": "0"}
        status = await _account_status(tb, common)
    return f"Cookie length: {len(ndus)} characters\nResult: {status}"


async def _list_dir(tb, common, short, path):
    params = {**common, "shorturl": short, "dir": path, "root": "0", "page": "1", "num": "100", "order": "asc"}
    data = await tb.get_json("/share/list", params)
    return data.get("list") or []


async def _scrape_host(host, full, short, trace):
    timeout = aiohttp.ClientTimeout(total=45)
    async with aiohttp.ClientSession(timeout=timeout, cookie_jar=aiohttp.DummyCookieJar()) as s:
        tb = _Terabox(host, s)

        # 1) jsToken from the site
        token = ""
        for path, params in (("/main", {"category": "all"}), ("/sharing/link", {"surl": short})):
            try:
                _, html = await tb.get(path, params)
                token = _find_token(html)
            except Exception as e:  # noqa: BLE001
                trace.append(f"{host} {path}: {type(e).__name__}: {e}")
            if token:
                break
        if not token:
            trace.append(f"{host}: no jsToken found (continuing without it)")

        common = {"app_id": APP_ID, "web": "1", "channel": "dubox", "clienttype": "0"}
        if token:
            common["jsToken"] = token

        # 2) share info
        info = await tb.get_json("/api/shorturlinfo", {**common, "shorturl": full, "root": "1"})
        files, meta = [], {}
        if info.get("errno") == 0:
            files = info.get("list") or []
            meta = {k: info.get(k) for k in ("shareid", "uk", "sign", "timestamp", "randsk") if info.get(k)}
        else:
            trace.append(f"{host} shorturlinfo -> {_why(info)}")

        # 3) share list (signed if we have the info, anonymous otherwise)
        if not any(f.get("dlink") for f in files):
            params = {**common, "shorturl": short, "root": "1", "page": "1", "num": "100", "order": "asc"}
            for k in ("shareid", "uk", "sign", "timestamp"):
                if meta.get(k):
                    params[k] = meta[k]
            lst = await tb.get_json("/share/list", params)
            if lst.get("errno") == 0:
                files = lst.get("list") or files
            else:
                trace.append(f"{host} share/list -> {_why(lst)}")

        # 4) open folders (one level)
        expanded = []
        for f in files:
            if str(f.get("isdir", "0")) == "1" and f.get("path"):
                expanded.extend(await _list_dir(tb, common, short, f["path"]))
            else:
                expanded.append(f)
        files = [f for f in expanded if str(f.get("isdir", "0")) != "1"]
        if not files:
            trace.append(f"{host}: Terabox returned no files for this link")
            return []

        # 5) make sure each file has a download link
        out = []
        for f in files:
            name = f.get("server_filename") or "video.mp4"
            dl = f.get("dlink")
            if not dl and meta.get("sign") and f.get("fs_id"):
                params = {
                    **common,
                    "encrypt": "0",
                    "product": "share",
                    "sign": meta["sign"],
                    "timestamp": meta.get("timestamp", ""),
                    "shareid": meta.get("shareid", ""),
                    "primaryid": meta.get("shareid", ""),
                    "uk": meta.get("uk", ""),
                    "nozip": "0",
                    "fid_list": json.dumps([_to_int(f["fs_id"])]),
                }
                if meta.get("randsk"):
                    params["extra"] = json.dumps({"sekey": unquote(str(meta["randsk"]))}, separators=(",", ":"))
                d = await tb.get_json("/share/download", params)
                if d.get("errno") == 0:
                    dl = d.get("dlink")
                    if isinstance(dl, list):
                        dl = dl[0].get("dlink") if dl and isinstance(dl[0], dict) else None
                    if not dl:
                        items = d.get("list") or d.get("info") or []
                        if items and isinstance(items[0], dict):
                            dl = items[0].get("dlink")
                else:
                    trace.append(f"{host} share/download -> {_why(d)}")
            if not dl:
                trace.append(f"{host}: found '{name}' ({_to_int(f.get('size'))} bytes) but Terabox gave no download link")
                continue
            out.append(FileInfo(name, _to_int(f.get("size")), dl, tb.headers()))
        if not out and not any("account check" in t for t in trace):
            trace.append(f"{host} account check -> {await _account_status(tb, common)}")
        return out


async def _from_scraper(link, trace):
    full, short = extract_surl(link)
    for host in HOSTS:
        try:
            files = await _scrape_host(host, full, short, trace)
        except Exception as e:  # noqa: BLE001
            trace.append(f"{host}: {type(e).__name__}: {e}")
            continue
        if files:
            return files
    raise ResolveError("\n".join(trace[:8]) or "Terabox gave no usable answer")


async def resolve(link):
    errors = []
    if RESOLVER_API_URL:
        try:
            return await _from_api(link)
        except Exception as e:  # noqa: BLE001
            errors.append(f"Resolver API failed: {e}")
    trace = []
    try:
        return await _from_scraper(link, trace)
    except Exception as e:  # noqa: BLE001
        errors.append(f"Built-in scraper failed:\n{e}")
    if not TERABOX_COOKIE:
        errors.append("Tip: TERABOX_COOKIE is not set in Render.")
    raise ResolveError("\n".join(errors))
