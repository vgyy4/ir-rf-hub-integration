"""Tests custom_components/ir_rf_hub/api.py: imported via conftest.py's
sys.path shim (bare `import api`), no Home Assistant involved. Runs
against a real aiohttp HTTP+WS server (fake_hub_server.py), not mocks, so
this exercises the actual wire behavior (headers, status codes, WS
framing).
"""

from __future__ import annotations

import asyncio

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from api import IrRfHubApiError, IrRfHubAuthError, IrRfHubClient
from fake_hub_server import DISCOVERED_REPORTS_KEY, FIRE_DEVICE_IDS_KEY, FIRED_KEY, WS_CLIENTS_KEY, make_app


async def test_get_health(unused_tcp_port=None):
    app = make_app("secret-token", commands=[])
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        client = IrRfHubClient(session, server.host, server.port, "secret-token")
        health = await client.async_get_health()
        assert health["status"] == "ok"


async def test_wrong_token_raises_auth_error():
    app = make_app("secret-token", commands=[])
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        client = IrRfHubClient(session, server.host, server.port, "wrong-token")
        with pytest.raises(IrRfHubAuthError):
            await client.async_get_health()


async def test_get_commands_parses_into_records():
    commands = [
        {"id": "c1", "name": "TV Power", "type": "ir", "default_device_id": "d1"},
        {"id": "c2", "name": "Fan", "type": "rf", "default_device_id": None},
    ]
    app = make_app("secret-token", commands=commands)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        client = IrRfHubClient(session, server.host, server.port, "secret-token")
        records = await client.async_get_commands()
        assert [r.id for r in records] == ["c1", "c2"]
        assert records[0].name == "TV Power"
        assert records[0].default_device_id == "d1"
        assert records[1].default_device_id is None


async def test_fire_command_success():
    app = make_app("secret-token", commands=[])
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        client = IrRfHubClient(session, server.host, server.port, "secret-token")
        await client.async_fire_command("c1")
        assert app[FIRED_KEY] == ["c1"]


async def test_fire_command_error_status_raises_api_error():
    app = make_app("secret-token", commands=[], fire_status={"c1": 400})
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        client = IrRfHubClient(session, server.host, server.port, "secret-token")
        with pytest.raises(IrRfHubApiError):
            await client.async_fire_command("c1")
        # the request still reached the server: this is a rejected
        # fire (e.g. no default device), not a connection failure
        assert app[FIRED_KEY] == ["c1"]


async def test_fire_command_without_device_id_posts_no_body():
    app = make_app("secret-token", commands=[])
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        client = IrRfHubClient(session, server.host, server.port, "secret-token")
        await client.async_fire_command("c1")
        assert app[FIRE_DEVICE_IDS_KEY]["c1"] is None


async def test_fire_command_with_device_id_posts_it():
    app = make_app("secret-token", commands=[])
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        client = IrRfHubClient(session, server.host, server.port, "secret-token")
        await client.async_fire_command("c1", device_id="d1")
        assert app[FIRE_DEVICE_IDS_KEY]["c1"] == "d1"


async def test_get_candidate_devices_parses_into_options():
    app = make_app(
        "secret-token",
        commands=[],
        candidate_devices={"c1": [{"id": "d1", "name": "Living Room"}, {"id": "d2", "name": "Bedroom"}]},
    )
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        client = IrRfHubClient(session, server.host, server.port, "secret-token")
        options = await client.async_get_candidate_devices("c1")
        assert [(o.id, o.name) for o in options] == [("d1", "Living Room"), ("d2", "Bedroom")]


async def test_report_discovered_devices_posts_the_list():
    app = make_app("secret-token", commands=[])
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        client = IrRfHubClient(session, server.host, server.port, "secret-token")
        devices = [{"name": "living-room-esp", "host": "10.0.0.9", "port": 6053}]
        await client.async_report_discovered_devices(devices)
        assert app[DISCOVERED_REPORTS_KEY] == [devices]


async def test_report_discovered_devices_wrong_token_raises_auth_error():
    app = make_app("secret-token", commands=[])
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        client = IrRfHubClient(session, server.host, server.port, "wrong-token")
        with pytest.raises(IrRfHubAuthError):
            await client.async_report_discovered_devices([])


async def test_listen_events_reconnect_callback_and_event_delivery():
    app = make_app("secret-token", commands=[])
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        client = IrRfHubClient(session, server.host, server.port, "secret-token")

        events: list[dict] = []
        reconnects = 0

        async def on_event(event: dict) -> None:
            events.append(event)

        async def on_reconnect() -> None:
            nonlocal reconnects
            reconnects += 1

        task = asyncio.ensure_future(client.async_listen_events(on_event, on_reconnect))
        try:
            for _ in range(50):
                if app[WS_CLIENTS_KEY]:
                    break
                await asyncio.sleep(0.05)
            assert app[WS_CLIENTS_KEY], "client never connected"
            assert reconnects == 1

            await app[WS_CLIENTS_KEY][0].send_json({"type": "command.created", "data": {"command_id": "c1"}})
            for _ in range(50):
                if events:
                    break
                await asyncio.sleep(0.05)
            assert events == [{"type": "command.created", "data": {"command_id": "c1"}}]
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
