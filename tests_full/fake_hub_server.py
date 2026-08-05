"""A minimal aiohttp.web server standing in for the App's
/api/integration/* + /api/ws surface, so api.IrRfHubClient can be tested
against a real HTTP/WS server without needing the actual App or Home
Assistant installed.
"""

from __future__ import annotations

from aiohttp import web

FIRED_KEY = web.AppKey("fired", list)
WS_CLIENTS_KEY = web.AppKey("ws_clients", list)
DISCOVERED_REPORTS_KEY = web.AppKey("discovered_reports", list)


def make_app(expected_token: str, commands: list[dict], fire_status: dict[str, int] | None = None) -> web.Application:
    app = web.Application()
    app[FIRED_KEY] = []
    app[WS_CLIENTS_KEY] = []
    app[DISCOVERED_REPORTS_KEY] = []
    fire_status = fire_status or {}

    def _authorized(request: web.Request) -> bool:
        return request.headers.get("Authorization") == f"Bearer {expected_token}"

    async def health(request: web.Request) -> web.Response:
        if not _authorized(request):
            return web.json_response({"detail": "unauthorized"}, status=401)
        return web.json_response({"status": "ok", "version": "0.1.0"})

    async def list_commands(request: web.Request) -> web.Response:
        if not _authorized(request):
            return web.json_response({"detail": "unauthorized"}, status=401)
        return web.json_response(commands)

    async def fire(request: web.Request) -> web.Response:
        if not _authorized(request):
            return web.json_response({"detail": "unauthorized"}, status=401)
        command_id = request.match_info["command_id"]
        app[FIRED_KEY].append(command_id)
        status = fire_status.get(command_id, 204)
        if status == 204:
            return web.Response(status=204)
        return web.json_response({"detail": "no default device"}, status=status)

    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        app[WS_CLIENTS_KEY].append(ws)
        async for _msg in ws:
            pass
        return ws

    async def discovered_devices(request: web.Request) -> web.Response:
        if not _authorized(request):
            return web.json_response({"detail": "unauthorized"}, status=401)
        app[DISCOVERED_REPORTS_KEY].append(await request.json())
        return web.Response(status=204)

    app.router.add_get("/api/integration/health", health)
    app.router.add_get("/api/integration/commands", list_commands)
    app.router.add_post("/api/integration/commands/{command_id}/fire", fire)
    app.router.add_post("/api/integration/discovered-devices", discovered_devices)
    app.router.add_get("/api/ws", ws_handler)
    return app
