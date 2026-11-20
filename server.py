from aiohttp import web

from config import PORT


async def start_server():
    """Tiny web server so Render sees a web service and UptimeRobot has a URL to ping."""

    async def ok(_request):
        return web.Response(text="Bot is running")

    app = web.Application()
    app.router.add_get("/", ok)
    app.router.add_get("/health", ok)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
