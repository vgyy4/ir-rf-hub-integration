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
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ir_rf_hub.const import CONF_DEVICE_GROUPING, DOMAIN, MODE_SPLIT_BY_TYPE, MODE_UNIFIED
from fake_hub_server import FIRE_DEVICE_IDS_KEY, FIRED_KEY, WS_CLIENTS_KEY, make_app


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


async def test_config_flow_hassio_discovery_refreshes_stale_token_on_existing_entry(hass):
    # Simulates "the App got reinstalled": Supervisor keeps the add-on's
    # internal hostname stable across a plain uninstall+reinstall (so
    # unique_id is unchanged), but the App issued a fresh pairing token.
    # The old behavior (_abort_if_unique_id_configured) left the existing
    # entry's stale token in place forever -- confirmed on a real install
    # as IrRfHubAuthError on every coordinator setup, with no obvious fix
    # short of manually deleting and re-adding the integration.
    app = make_app("new-token-after-reinstall", commands=[])
    async with TestServer(app) as server:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=f"{server.host}:{server.port}",
            data={"host": server.host, "port": server.port, "token": "stale-token-from-before-reinstall"},
        )
        entry.add_to_hass(hass)

        discovery_info = _hassio_discovery(server.host, server.port, "new-token-after-reinstall")
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "hassio"}, data=discovery_info
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"
        assert entry.data == {"host": server.host, "port": server.port, "token": "new-token-after-reinstall"}


async def test_config_flow_hassio_discovery_wrong_token_leaves_existing_entry_untouched(hass):
    # The health check runs before the existing-entry refresh, so a bad
    # announced token can't clobber a working stored one.
    app = make_app("the-real-token", commands=[])
    async with TestServer(app) as server:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=f"{server.host}:{server.port}",
            data={"host": server.host, "port": server.port, "token": "the-real-token"},
        )
        entry.add_to_hass(hass)

        discovery_info = _hassio_discovery(server.host, server.port, "some-wrong-token")
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "hassio"}, data=discovery_info
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "invalid_auth"
        assert entry.data == {"host": server.host, "port": server.port, "token": "the-real-token"}


async def test_config_flow_manual_repair_refreshes_stale_token_on_existing_entry(hass):
    # Same "App got reinstalled" scenario, but via the manual paste-a-code
    # fallback rather than automatic hassio discovery.
    app = make_app("new-token-after-reinstall", commands=[])
    async with TestServer(app) as server:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=f"{server.host}:{server.port}",
            data={"host": server.host, "port": server.port, "token": "stale-token-from-before-reinstall"},
        )
        entry.add_to_hass(hass)

        code = _encode_pairing_code(server.host, server.port, "new-token-after-reinstall")
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result2 = await hass.config_entries.flow.async_configure(result["flow_id"], {"pairing_code": code})

        assert result2["type"] is FlowResultType.ABORT
        assert result2["reason"] == "already_configured"
        assert entry.data == {"host": server.host, "port": server.port, "token": "new-token-after-reinstall"}


# -- entity setup ---------------------------------------------------------------


async def test_setup_creates_button_switch_and_select_grouped_under_one_device(hass):
    commands = [{"id": "c1", "name": "TV Power", "type": "ir", "default_device_id": None}]
    app = make_app("secret-token", commands=commands)
    async with TestServer(app) as server:
        entry = await _setup_entry(hass, server, "secret-token")

        entities = _entities_for(hass, entry)
        assert sorted(e.entity_id.split(".")[0] for e in entities) == ["button", "select", "switch"]

        button = next(e for e in entities if e.entity_id.startswith("button."))
        switch = next(e for e in entities if e.entity_id.startswith("switch."))
        select = next(e for e in entities if e.entity_id.startswith("select."))
        assert button.unique_id == f"{entry.entry_id}_c1_button"
        assert switch.unique_id == f"{entry.entry_id}_c1_switch"
        assert select.unique_id == f"{entry.entry_id}_c1_select"
        # grouped under the same device
        assert button.device_id == switch.device_id == select.device_id
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


async def test_select_option_fires_the_command_with_chosen_device(hass):
    commands = [{"id": "c1", "name": "TV Power", "type": "ir", "default_device_id": "d1"}]
    app = make_app(
        "secret-token",
        commands=commands,
        candidate_devices={"c1": [{"id": "d1", "name": "Living Room"}, {"id": "d2", "name": "Bedroom"}]},
    )
    async with TestServer(app) as server:
        entry = await _setup_entry(hass, server, "secret-token")
        select_entity_id = next(
            e.entity_id for e in _entities_for(hass, entry) if e.entity_id.startswith("select.")
        )

        state = hass.states.get(select_entity_id)
        assert sorted(state.attributes["options"]) == ["Bedroom", "Living Room"]
        # default_device_id "d1" resolves to its matching option name
        assert state.state == "Living Room"

        await hass.services.async_call(
            "select", "select_option",
            {"entity_id": select_entity_id, "option": "Bedroom"},
            blocking=True,
        )
        assert app[FIRED_KEY] == ["c1"]
        assert app[FIRE_DEVICE_IDS_KEY]["c1"] == "d2"
        assert hass.states.get(select_entity_id).state == "Bedroom"


# -- live sync --------------------------------------------------------------


async def test_new_command_gets_entities_without_restart(hass):
    commands = [{"id": "c1", "name": "TV Power", "type": "ir", "default_device_id": None}]
    app = make_app("secret-token", commands=commands)
    async with TestServer(app) as server:
        entry = await _setup_entry(hass, server, "secret-token")
        assert len(_entities_for(hass, entry)) == 3

        # Simulate a new command appearing on the App side and it
        # notifying over the same WS the coordinator is listening on.
        commands.append({"id": "c2", "name": "Fan", "type": "rf", "default_device_id": None})
        for ws in app[WS_CLIENTS_KEY]:
            await ws.send_json({"type": "command.created", "data": {"command_id": "c2"}})

        for _ in range(50):
            await asyncio.sleep(0.1)
            if len(_entities_for(hass, entry)) == 6:
                break
        assert len(_entities_for(hass, entry)) == 6


async def test_deleted_command_removes_its_entities(hass):
    commands = [
        {"id": "c1", "name": "TV Power", "type": "ir", "default_device_id": None},
        {"id": "c2", "name": "Fan", "type": "rf", "default_device_id": None},
    ]
    app = make_app("secret-token", commands=commands)
    async with TestServer(app) as server:
        entry = await _setup_entry(hass, server, "secret-token")
        assert len(_entities_for(hass, entry)) == 6

        device_registry = dr.async_get(hass)
        assert device_registry.async_get_device(identifiers={(DOMAIN, "c2")}) is not None

        commands.pop()  # remove "Fan"
        for ws in app[WS_CLIENTS_KEY]:
            await ws.send_json({"type": "command.deleted", "data": {"command_id": "c2"}})

        for _ in range(50):
            await asyncio.sleep(0.1)
            if len(_entities_for(hass, entry)) == 3:
                break
        assert len(_entities_for(hass, entry)) == 3

        # entity.py groups the button+switch+select trio under one HA
        # Device per Command -- removing the entities alone leaves a
        # permanent zero-entity ghost device behind unless
        # button.py/switch.py/select.py's _remove() also cleans up the
        # now-empty device.
        assert device_registry.async_get_device(identifiers={(DOMAIN, "c2")}) is None


async def test_setup_prunes_ghost_devices_from_commands_deleted_before_this_fix_existed(hass):
    # Simulates an install that already had a stale per-command device
    # sitting in the registry from before button.py/switch.py's
    # SIGNAL_COMMAND_REMOVED handler started cleaning up devices too --
    # that reactive path can't retroactively fix installs that hit it
    # while the bug was still present, so setup itself has to reconcile.
    app = make_app("secret-token", commands=[{"id": "c1", "name": "TV Power", "type": "ir", "default_device_id": None}])
    async with TestServer(app) as server:
        entry = MockConfigEntry(domain=DOMAIN, data={"host": server.host, "port": server.port, "token": "secret-token"})
        entry.add_to_hass(hass)

        device_registry = dr.async_get(hass)
        ghost = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, "long-deleted-command-id")}, name="Ghost"
        )

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert device_registry.async_get(ghost.id) is None
        # The shared hub device and the still-valid command's device must
        # survive the same reconciliation pass.
        assert device_registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)}) is not None
        assert device_registry.async_get_device(identifiers={(DOMAIN, "c1")}) is not None


# -- device grouping options --------------------------------------------------------------


async def test_options_flow_shows_current_mode_and_saves_new_one(hass):
    commands = [{"id": "c1", "name": "TV Power", "type": "ir", "default_device_id": None}]
    app = make_app("secret-token", commands=commands)
    async with TestServer(app) as server:
        entry = await _setup_entry(hass, server, "secret-token")

        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "init"

        result2 = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_DEVICE_GROUPING: MODE_UNIFIED}
        )
        assert result2["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options[CONF_DEVICE_GROUPING] == MODE_UNIFIED


async def test_changing_grouping_mode_reloads_and_regroups_devices(hass):
    # No add_update_listener means HA silently does nothing on an options
    # change -- this exercises the full path, not just that the option
    # value got saved.
    commands = [{"id": "c1", "name": "TV Power", "type": "ir", "default_device_id": None}]
    app = make_app("secret-token", commands=commands)
    async with TestServer(app) as server:
        entry = await _setup_entry(hass, server, "secret-token")
        device_registry = dr.async_get(hass)
        assert device_registry.async_get_device(identifiers={(DOMAIN, "c1")}) is not None

        result = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_DEVICE_GROUPING: MODE_UNIFIED}
        )
        await hass.async_block_till_done()

        assert entry.state.value == "loaded"
        # unified mode: the per-command device is gone, every entity now
        # nests under the hub device instead.
        assert device_registry.async_get_device(identifiers={(DOMAIN, "c1")}) is None
        hub_device = device_registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
        assert hub_device is not None

        entities = _entities_for(hass, entry)
        assert len(entities) == 3
        assert all(e.device_id == hub_device.id for e in entities)


async def test_split_by_type_mode_groups_buttons_and_selects_separately_from_switches(hass):
    commands = [{"id": "c1", "name": "TV Power", "type": "ir", "default_device_id": None}]
    app = make_app("secret-token", commands=commands)
    async with TestServer(app) as server:
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={"host": server.host, "port": server.port, "token": "secret-token"},
            options={CONF_DEVICE_GROUPING: MODE_SPLIT_BY_TYPE},
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        device_registry = dr.async_get(hass)
        registry = er.async_get(hass)
        entities = _entities_for(hass, entry)

        button = next(e for e in entities if e.entity_id.startswith("button."))
        select = next(e for e in entities if e.entity_id.startswith("select."))
        switch = next(e for e in entities if e.entity_id.startswith("switch."))

        assert button.device_id == select.device_id
        assert button.device_id != switch.device_id

        buttons_device = device_registry.async_get(button.device_id)
        switches_device = device_registry.async_get(switch.device_id)
        assert buttons_device.name == "Buttons"
        assert switches_device.name == "Switches"
        # no leftover per-command device from the (unused) separate mode
        assert device_registry.async_get_device(identifiers={(DOMAIN, "c1")}) is None

        # deleting the only command empties both virtual devices, which
        # should then be pruned just like a per-command device would be.
        commands.clear()
        for ws in app[WS_CLIENTS_KEY]:
            await ws.send_json({"type": "command.deleted", "data": {"command_id": "c1"}})
        for _ in range(50):
            await asyncio.sleep(0.1)
            if not _entities_for(hass, entry):
                break
        assert not _entities_for(hass, entry)
        assert device_registry.async_get(buttons_device.id) is None
        assert device_registry.async_get(switches_device.id) is None
        # the hub device itself is never touched by this cleanup
        assert device_registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)}) is not None
        assert registry.async_get(button.entity_id) is None


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
