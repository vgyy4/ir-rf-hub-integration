"""Thin client for the App's /api/integration/* REST surface and the
general /api/ws event fan-out. See the App's api/rest/integration.py for
the server side of the auth story: /api/integration/* requires the bearer
token embedded in the pairing code, /api/ws does not (see that file's
docstring for why that split is fine).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import aiohttp

logger = logging.getLogger(__name__)

# The App is always on Supervisor's own internal network (see
# ARCHITECTURE.md's Pairing section on the App side), so a slow response
# here means the App is hung, not that it's a slow WAN request -- without
# an explicit floor, a caller (a button press, a config-flow health check)
# would otherwise hang on the aiohttp session's own default, which HA's
# shared ClientSession doesn't set to anything short. Only applied to plain
# request/response calls: the WS connection in async_listen_events is
# meant to stay open indefinitely and already has its own liveness check
# via `heartbeat=30`.
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)


class IrRfHubApiError(Exception):
    """Generic API error."""


class IrRfHubAuthError(IrRfHubApiError):
    """The bearer token was rejected -- the App was likely reinstalled and
    issued a fresh pairing code."""


@dataclass
class CommandRecord:
    id: str
    name: str
    type: str  # "ir" | "rf"
    default_device_id: str | None


@dataclass
class DeviceOption:
    id: str
    name: str


class IrRfHubClient:
    def __init__(self, session: aiohttp.ClientSession, host: str, port: int, token: str) -> None:
        self._session = session
        self._base_url = f"http://{host}:{port}/api/integration"
        self._ws_url = f"ws://{host}:{port}/api/ws"
        self._headers = {"Authorization": f"Bearer {token}"}

    async def async_get_health(self) -> dict:
        return await self._get("/health")

    async def async_get_commands(self) -> list[CommandRecord]:
        data = await self._get("/commands")
        return [
            CommandRecord(id=c["id"], name=c["name"], type=c["type"], default_device_id=c.get("default_device_id"))
            for c in data
        ]

    async def async_report_discovered_devices(self, devices: list[dict]) -> None:
        try:
            async with self._session.post(
                f"{self._base_url}/discovered-devices", headers=self._headers, json=devices, timeout=_REQUEST_TIMEOUT
            ) as resp:
                await self._raise_for_status(resp)
        except aiohttp.ClientError as exc:
            raise IrRfHubApiError(str(exc)) from exc

    async def async_fire_command(self, command_id: str, device_id: str | None = None) -> None:
        """device_id is explicit-choice-only (see select.py): a bare
        button/switch press omits it and relies on the App's own
        default/single-candidate-transmitter fallback, matching a plain
        POST with no body.
        """
        try:
            async with self._session.post(
                f"{self._base_url}/commands/{command_id}/fire",
                headers=self._headers,
                json={"device_id": device_id} if device_id is not None else None,
                timeout=_REQUEST_TIMEOUT,
            ) as resp:
                await self._raise_for_status(resp)
        except aiohttp.ClientError as exc:
            # Covers connection-refused/DNS/timeout failures below the HTTP
            # layer -- _raise_for_status only ever sees a response that
            # actually arrived. Without this, a plain aiohttp.ClientError
            # escapes uncaught past every `except IrRfHubApiError` in
            # config_flow.py/button.py/switch.py.
            raise IrRfHubApiError(str(exc)) from exc

    async def async_get_candidate_devices(self, command_id: str) -> list[DeviceOption]:
        data = await self._get(f"/commands/{command_id}/candidate-devices")
        return [DeviceOption(id=d["id"], name=d["name"]) for d in data]

    async def _get(self, path: str):
        try:
            async with self._session.get(
                f"{self._base_url}{path}", headers=self._headers, timeout=_REQUEST_TIMEOUT
            ) as resp:
                await self._raise_for_status(resp)
                return await resp.json()
        except aiohttp.ClientError as exc:
            raise IrRfHubApiError(str(exc)) from exc

    async def _raise_for_status(self, resp: aiohttp.ClientResponse) -> None:
        if resp.status == 401:
            raise IrRfHubAuthError("The App rejected our pairing token")
        if resp.status >= 400:
            detail = await resp.text()
            raise IrRfHubApiError(f"{resp.status}: {detail}")

    async def async_listen_events(
        self, on_event: Callable[[dict], Awaitable[None]], on_reconnect: Callable[[], Awaitable[None]]
    ) -> None:
        """Runs forever (until cancelled), reconnecting with backoff.
        Calls `on_reconnect` after every successful (re)connect so the
        coordinator can do a full REST resync -- WS is the fast path, this
        callback is the correctness backstop for anything missed while
        disconnected.
        """
        backoff = 1.0
        while True:
            try:
                async with self._session.ws_connect(self._ws_url, heartbeat=30) as ws:
                    backoff = 1.0
                    await on_reconnect()
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await on_event(msg.json())
                        elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                            break
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 -- any connection error, just retry
                logger.debug("ir_rf_hub event socket error, retrying in %.1fs", backoff, exc_info=True)

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
