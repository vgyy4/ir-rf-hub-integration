"""A minimal aiohttp.web server standing in for the App's
/api/integration/* + /api/ws surface, so api.IrRfHubClient can be tested
against a real HTTP/WS server without needing the actual App or Home
Assistant installed.
"""

from __future__ import annotations

from aiohttp import web

FIRED_KEY = web.AppKey("fired", list)
FIRE_DEVICE_IDS_KEY = web.AppKey("fire_device_ids", dict)
WS_CLIENTS_KEY = web.AppKey("ws_clients", list)
DISCOVERED_REPORTS_KEY = web.AppKey("discovered_reports", list)
# Mutable (unlike a closed-over parameter), so a test can flip it mid-run to
# simulate the App reissuing a token (e.g. test_full_integration.py's
# coordinator-auth-failure-creates-a-repair-issue test) without needing a
# second server.
EXPECTED_TOKEN_KEY = web.AppKey("expected_token", str)


def make_app(
    expected_token: str,
    commands: list[dict],
    fire_status: dict[str, int] | None = None,
    candidate_devices: dict[str, list[dict]] | None = None,
) -> web.Application:
    app = web.Application()
    app[FIRED_KEY] = []
    app[FIRE_DEVICE_IDS_KEY] = {}
    app[WS_CLIENTS_KEY] = []
    app[DISCOVERED_REPORTS_KEY] = []
    app[EXPECTED_TOKEN_KEY] = expected_token
    fire_status = fire_status or {}
    candidate_devices = candidate_devices or {}

    def _authorized(request: web.Request) -> bool:
        return request.headers.get("Authorization") == f"Bearer {request.app[EXPECTED_TOKEN_KEY]}"

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
        device_id = None
        try:
            payload = await request.json()
            device_id = (payload or {}).get("device_id")
        except Exception:  # noqa: BLE001 -- a bare press posts no body at all
            pass
        app[FIRE_DEVICE_IDS_KEY][command_id] = device_id
        status = fire_status.get(command_id, 204)
        if status == 204:
            return web.Response(status=204)
        return web.json_response({"detail": "no default device"}, status=status)

    async def get_candidate_devices(request: web.Request) -> web.Response:
        if not _authorized(request):
            return web.json_response({"detail": "unauthorized"}, status=401)
        command_id = request.match_info["command_id"]
        return web.json_response(candidate_devices.get(command_id, []))

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
    app.router.add_get("/api/integration/commands/{command_id}/candidate-devices", get_candidate_devices)
    app.router.add_post("/api/integration/discovered-devices", discovered_devices)
    app.router.add_get("/api/ws", ws_handler)
    return app
