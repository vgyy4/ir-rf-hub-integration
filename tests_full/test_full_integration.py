"""End-to-end tests against real Home Assistant core: config flow, entity
creation/grouping, button press, switch turn_on/auto-reset, and live sync
as commands change on the App side -- everything the top-level tests/
suite explicitly couldn't cover without installing HA core. Runs only in
GitHub Actions' cloud runners (see ../.github/workflows/ci.yaml).
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest
from aiohttp.test_utils import TestServer
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ir_rf_hub.const import DOMAIN
from fake_hub_server import FIRED_KEY, WS_CLIENTS_KEY, make_app


def _encode_pairing_code(host: str, port: int, token: str) -> str:
    payload = {"h": host, "p": port, "t": token, "v": 1}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


async def _setup_entry(hass, server, token: str) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, data={"host": server.host, "port": server.port, "token": token}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entities_for(hass, entry: MockConfigEntry) -> list:
    registry = er.async_get(hass)
    return [e for e in registry.entities.values() if e.config_entry_id == entry.entry_id]


# -- config flow --------------------------------------------------------------


async def test_config_flow_valid_pairing_code_creates_entry(hass):
    app = make_app("secret-token", commands=[])
    async with TestServer(app) as server:
        code = _encode_pairing_code(server.host, server.port, "secret-token")

        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["type"] is FlowResultType.FORM

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"pairing_code": code}
        )
        assert result2["type"] is FlowResultType.CREATE_ENTRY
        assert result2["data"] == {"host": server.host, "port": server.port, "token": "secret-token"}


async def test_config_flow_garbage_code_shows_field_error(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pairing_code": "not-a-valid-code"}
    )
    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"] == {"pairing_code": "invalid_code"}


async def test_config_flow_wrong_token_shows_auth_error(hass):
    app = make_app("real-token", commands=[])
    async with TestServer(app) as server:
        code = _encode_pairing_code(server.host, server.port, "wrong-token")
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"pairing_code": code}
        )
        assert result2["type"] is FlowResultType.FORM
        assert result2["errors"] == {"base": "invalid_auth"}


async def test_config_flow_unreachable_host_shows_connect_error(hass):
    # Port 1 -- nothing listens there, connection should just fail.
    code = _encode_pairing_code("127.0.0.1", 1, "token")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pairing_code": code}
    )
    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"] == {"base": "cannot_connect"}


def _hassio_discovery(host: str, port: int, token: str) -> HassioServiceInfo:
    return HassioServiceInfo(
        config={"host": host, "port": port, "token": token},
        name="IR-RF Command Hub",
        slug="local_ir_rf_hub",
        uuid="test-uuid",
    )


async def test_config_flow_hassio_discovery_confirms_then_creates_entry(hass):
    # The zero-typing path: the App announces itself to Supervisor, Core
    # routes it straight to async_step_hassio -- no pairing_code field at
    # all, just a confirm step.
    app = make_app("secret-token", commands=[])
    async with TestServer(app) as server:
        discovery_info = _hassio_discovery(server.host, server.port, "secret-token")

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "hassio"}, data=discovery_info
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "hassio_confirm"

        result2 = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result2["type"] is FlowResultType.CREATE_ENTRY
        assert result2["data"] == {"host": server.host, "port": server.port, "token": "secret-token"}


async def test_config_flow_hassio_discovery_wrong_token_aborts(hass):
    app = make_app("real-token", commands=[])
    async with TestServer(app) as server:
        discovery_info = _hassio_discovery(server.host, server.port, "wrong-token")
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "hassio"}, data=discovery_info
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "invalid_auth"


async def test_config_flow_hassio_discovery_unreachable_aborts(hass):
    # Port 1 -- nothing listens there, connection should just fail.
    discovery_info = _hassio_discovery("127.0.0.1", 1, "token")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "hassio"}, data=discovery_info
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_config_flow_hassio_discovery_already_configured_aborts(hass):
    # _setup_entry() doesn't set unique_id (the other tests using it don't
    # need duplicate-detection), so build the pre-existing entry directly
    # with the same unique_id async_step_hassio derives (f"{host}:{port}"),
    # and no need for a real fake server since the abort happens before
    # any connectivity check.
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="127.0.0.1:9999",
        data={"host": "127.0.0.1", "port": 9999, "token": "secret-token"},
    )
    entry.add_to_hass(hass)

    discovery_info = _hassio_discovery("127.0.0.1", 9999, "secret-token")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "hassio"}, data=discovery_info
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# -- entity setup ---------------------------------------------------------------


async def test_setup_creates_button_and_switch_grouped_under_one_device(hass):
    commands = [{"id": "c1", "name": "TV Power", "type": "ir", "default_device_id": None}]
    app = make_app("secret-token", commands=commands)
    async with TestServer(app) as server:
        entry = await _setup_entry(hass, server, "secret-token")

        entities = _entities_for(hass, entry)
        assert sorted(e.entity_id.split(".")[0] for e in entities) == ["button", "switch"]

        button = next(e for e in entities if e.entity_id.startswith("button."))
        switch = next(e for e in entities if e.entity_id.startswith("switch."))
        assert button.unique_id == f"{entry.entry_id}_c1_button"
        assert switch.unique_id == f"{entry.entry_id}_c1_switch"
        # grouped under the same device
        assert button.device_id == switch.device_id
        assert button.device_id is not None


# -- firing commands --------------------------------------------------------------


async def test_button_press_fires_the_command(hass):
    commands = [{"id": "c1", "name": "TV Power", "type": "ir", "default_device_id": "d1"}]
    app = make_app("secret-token", commands=commands)
    async with TestServer(app) as server:
        entry = await _setup_entry(hass, server, "secret-token")
        button_entity_id = next(
            e.entity_id for e in _entities_for(hass, entry) if e.entity_id.startswith("button.")
        )

        await hass.services.async_call(
            "button", "press", {"entity_id": button_entity_id}, blocking=True
        )
        assert app[FIRED_KEY] == ["c1"]


async def test_switch_turn_on_fires_then_resets_to_off(hass):
    commands = [{"id": "c1", "name": "TV Power", "type": "ir", "default_device_id": "d1"}]
    app = make_app("secret-token", commands=commands)
    async with TestServer(app) as server:
        entry = await _setup_entry(hass, server, "secret-token")
        switch_entity_id = next(
            e.entity_id for e in _entities_for(hass, entry) if e.entity_id.startswith("switch.")
        )

        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": switch_entity_id}, blocking=True
        )
        assert app[FIRED_KEY] == ["c1"]
        assert hass.states.get(switch_entity_id).state == "on"

        # DEFAULT_SWITCH_RESET_DELAY_S is 1.0s -- wait past it for the
        # auto-reset, then let HA process the resulting state write.
        for _ in range(30):
            await asyncio.sleep(0.1)
            if hass.states.get(switch_entity_id).state == "off":
                break
        assert hass.states.get(switch_entity_id).state == "off"


async def test_button_press_without_default_device_raises(hass):
    commands = [{"id": "c1", "name": "No Default", "type": "ir", "default_device_id": None}]
    app = make_app("secret-token", commands=commands, fire_status={"c1": 400})
    async with TestServer(app) as server:
        entry = await _setup_entry(hass, server, "secret-token")
        button_entity_id = next(
            e.entity_id for e in _entities_for(hass, entry) if e.entity_id.startswith("button.")
        )

        with pytest.raises(Exception):  # noqa: B017 -- HomeAssistantError, avoid importing just for this
            await hass.services.async_call(
                "button", "press", {"entity_id": button_entity_id}, blocking=True
            )


# -- live sync --------------------------------------------------------------


async def test_new_command_gets_entities_without_restart(hass):
    commands = [{"id": "c1", "name": "TV Power", "type": "ir", "default_device_id": None}]
    app = make_app("secret-token", commands=commands)
    async with TestServer(app) as server:
        entry = await _setup_entry(hass, server, "secret-token")
        assert len(_entities_for(hass, entry)) == 2

        # Simulate a new command appearing on the App side and it
        # notifying over the same WS the coordinator is listening on.
        commands.append({"id": "c2", "name": "Fan", "type": "rf", "default_device_id": None})
        for ws in app[WS_CLIENTS_KEY]:
            await ws.send_json({"type": "command.created", "data": {"command_id": "c2"}})

        for _ in range(50):
            await asyncio.sleep(0.1)
            if len(_entities_for(hass, entry)) == 4:
                break
        assert len(_entities_for(hass, entry)) == 4


async def test_deleted_command_removes_its_entities(hass):
    commands = [
        {"id": "c1", "name": "TV Power", "type": "ir", "default_device_id": None},
        {"id": "c2", "name": "Fan", "type": "rf", "default_device_id": None},
    ]
    app = make_app("secret-token", commands=commands)
    async with TestServer(app) as server:
        entry = await _setup_entry(hass, server, "secret-token")
        assert len(_entities_for(hass, entry)) == 4

        commands.pop()  # remove "Fan"
        for ws in app[WS_CLIENTS_KEY]:
            await ws.send_json({"type": "command.deleted", "data": {"command_id": "c2"}})

        for _ in range(50):
            await asyncio.sleep(0.1)
            if len(_entities_for(hass, entry)) == 2:
                break
        assert len(_entities_for(hass, entry)) == 2


# -- unload --------------------------------------------------------------


async def test_unload_removes_entity_states_cleanly(hass):
    commands = [{"id": "c1", "name": "TV Power", "type": "ir", "default_device_id": None}]
    app = make_app("secret-token", commands=commands)
    async with TestServer(app) as server:
        entry = await _setup_entry(hass, server, "secret-token")
        button_entity_id = next(
            e.entity_id for e in _entities_for(hass, entry) if e.entity_id.startswith("button.")
        )
        assert hass.states.get(button_entity_id) is not None

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        # HA's platform unload deliberately keeps the entity registry entry
        # (so a reload doesn't churn history/customizations) and marks the
        # state unavailable rather than deleting it outright -- both are
        # acceptable "cleanly unloaded" outcomes, not just a None state.
        state = hass.states.get(button_entity_id)
        assert state is None or state.state == "unavailable"
