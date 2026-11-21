import os


def _req(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


API_ID = int(_req("API_ID"))
API_HASH = _req("API_HASH")
BOT_TOKEN = _req("BOT_TOKEN")
# One or more Telegram user IDs, separated by commas, e.g. 123456789,987654321
OWNER_IDS = [int(x) for x in _req("OWNER_ID").replace(" ", "").split(",") if x]
OWNER_ID = OWNER_IDS[0]  # the first one also receives the startup message
REDIS_URL = _req("REDIS_URL")

# 0 = no limit (the server may crash on very big files and Render will restart it)
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "0"))

PORT = int(os.getenv("PORT", "10000"))
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/tmp/downloads")

# Optional: a resolver API URL with {url} placeholder, e.g. https://example.com/api?url={url}
RESOLVER_API_URL = os.getenv("RESOLVER_API_URL", "")
# Optional: your Terabox login cookie (the "ndus" value) for the built-in scraper
TERABOX_COOKIE = os.getenv("TERABOX_COOKIE", "")

# Optional: the Terabox site your cookie belongs to, e.g. www.1024terabox.com (it is tried first)
TERABOX_HOST = os.getenv("TERABOX_HOST", "").strip()
