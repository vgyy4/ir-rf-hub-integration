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
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ir_rf_hub.const import CONF_DEVICE_GROUPING, DOMAIN, MODE_SPLIT_BY_TYPE, MODE_UNIFIED
from custom_components.ir_rf_hub.diagnostics import async_get_config_entry_diagnostics
from fake_hub_server import EXPECTED_TOKEN_KEY, FIRE_DEVICE_IDS_KEY, FIRED_KEY, WS_CLIENTS_KEY, make_app


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
        name="IR-RF Hub",
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


async def test_setup_migrates_a_pre_rename_entry_title(hass):
    # ConfigFlow's title= only applies at entry creation, so an install
    # paired before the "Command" rename would otherwise keep the old
    # title forever, even after updating -- see __init__.py's self-heal.
    app = make_app("secret-token", commands=[])
    async with TestServer(app) as server:
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="IR/RF Command Hub",
            data={"host": server.host, "port": server.port, "token": "secret-token"},
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.title == "IR/RF Hub"


async def test_setup_creates_button_switch_and_select_grouped_under_one_device(hass):
    commands = [{"id": "c1", "name": "TV Power", "type": "ir", "default_device_id": None}]
    app = make_app("secret-token", commands=commands)
    async with TestServer(app) as server:
        entry = await _setup_entry(hass, server, "secret-token")

        entities = _entities_for(hass, entry)
        # "remote" is the odd one out: unlike button/select/switch it's a
        # singleton (one per config entry, not one per Command) -- see
        # remote.py's own docstring for why -- so it's checked separately
        # below rather than folded into the per-command grouping assertion.
        assert sorted(e.entity_id.split(".")[0] for e in entities) == ["button", "remote", "select", "switch"]

        button = next(e for e in entities if e.entity_id.startswith("button."))
        switch = next(e for e in entities if e.entity_id.startswith("switch."))
        select = next(e for e in entities if e.entity_id.startswith("select."))
        remote = next(e for e in entities if e.entity_id.startswith("remote."))
        assert button.unique_id == f"{entry.entry_id}_c1_button"
        assert switch.unique_id == f"{entry.entry_id}_c1_switch"
        assert select.unique_id == f"{entry.entry_id}_c1_select"
        assert remote.unique_id == f"{entry.entry_id}_remote"
        # grouped under the same per-command device ("separate" mode)
        assert button.device_id == switch.device_id == select.device_id
        assert button.device_id is not None
        # the remote entity always lives on the shared hub device instead,
        # regardless of the per-command device-grouping mode -- there's
        # only one of it, so per-command grouping doesn't apply.
        device_registry = dr.async_get(hass)
        hub_device = device_registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
        assert remote.device_id == hub_device.id
        assert remote.device_id != button.device_id

        # "separate" mode: the device is already named after the command,
        # so the plain per-type suffix is enough -- has_entity_name
        # combines it with the device name for the full friendly_name.
        assert hass.states.get(button.entity_id).attributes["friendly_name"] == "TV Power Button"
        assert hass.states.get(switch.entity_id).attributes["friendly_name"] == "TV Power Switch"
        assert hass.states.get(select.entity_id).attributes["friendly_name"] == "TV Power Send via"


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


async def test_remote_send_command_fires_named_commands_in_order(hass):
    commands = [
        {"id": "c1", "name": "TV Power", "type": "ir", "default_device_id": None},
        {"id": "c2", "name": "AC On", "type": "ir", "default_device_id": None},
    ]
    app = make_app("secret-token", commands=commands)
    async with TestServer(app) as server:
        entry = await _setup_entry(hass, server, "secret-token")
        remote_entity_id = next(
            e.entity_id for e in _entities_for(hass, entry) if e.entity_id.startswith("remote.")
        )

        # One remote entity total, addressing both commands by name -- not
        # one remote entity per command.
        assert len([e for e in _entities_for(hass, entry) if e.entity_id.startswith("remote.")]) == 1

        await hass.services.async_call(
            "remote",
            "send_command",
            {"entity_id": remote_entity_id, "command": ["TV Power", "AC On"]},
            blocking=True,
        )
        assert app[FIRED_KEY] == ["c1", "c2"]


async def test_remote_send_command_rejects_an_unknown_name(hass):
    commands = [{"id": "c1", "name": "TV Power", "type": "ir", "default_device_id": None}]
    app = make_app("secret-token", commands=commands)
    async with TestServer(app) as server:
        entry = await _setup_entry(hass, server, "secret-token")
        remote_entity_id = next(
            e.entity_id for e in _entities_for(hass, entry) if e.entity_id.startswith("remote.")
        )

        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                "remote",
                "send_command",
                {"entity_id": remote_entity_id, "command": ["Does Not Exist"]},
                blocking=True,
            )
        assert app[FIRED_KEY] == []


# -- live sync --------------------------------------------------------------


async def test_new_command_gets_entities_without_restart(hass):
    commands = [{"id": "c1", "name": "TV Power", "type": "ir", "default_device_id": None}]
    app = make_app("secret-token", commands=commands)
    async with TestServer(app) as server:
        entry = await _setup_entry(hass, server, "secret-token")
        # 3 per command (button/switch/select) + 1 singleton remote entity.
        assert len(_entities_for(hass, entry)) == 4

        # Simulate a new command appearing on the App side and it
        # notifying over the same WS the coordinator is listening on.
        commands.append({"id": "c2", "name": "Fan", "type": "rf", "default_device_id": None})
        for ws in app[WS_CLIENTS_KEY]:
            await ws.send_json({"type": "command.created", "data": {"command_id": "c2"}})

        for _ in range(50):
            await asyncio.sleep(0.1)
            if len(_entities_for(hass, entry)) == 7:
                break
        assert len(_entities_for(hass, entry)) == 7


async def test_deleted_command_removes_its_entities(hass):
    commands = [
        {"id": "c1", "name": "TV Power", "type": "ir", "default_device_id": None},
        {"id": "c2", "name": "Fan", "type": "rf", "default_device_id": None},
    ]
    app = make_app("secret-token", commands=commands)
    async with TestServer(app) as server:
        entry = await _setup_entry(hass, server, "secret-token")
        assert len(_entities_for(hass, entry)) == 7  # 3 x 2 commands + 1 singleton remote entity

        device_registry = dr.async_get(hass)
        assert device_registry.async_get_device(identifiers={(DOMAIN, "c2")}) is not None

        commands.pop()  # remove "Fan"
        for ws in app[WS_CLIENTS_KEY]:
            await ws.send_json({"type": "command.deleted", "data": {"command_id": "c2"}})

        for _ in range(50):
            await asyncio.sleep(0.1)
            if len(_entities_for(hass, entry)) == 4:
                break
        assert len(_entities_for(hass, entry)) == 4  # 3 for "TV Power" + the remote entity, which never goes away

        # entity.py groups the button+switch+select trio under one HA
        # Device per Command -- removing the entities alone leaves a
        # permanent zero-entity ghost device behind unless
        # button.py/switch.py/select.py's _remove() also cleans up the
        # now-empty device.
        assert device_registry.async_get_device(identifiers={(DOMAIN, "c2")}) is None


async def test_auth_failure_during_live_resync_creates_a_repair_issue_and_clears_on_recovery(hass):
    # Simulates the App getting reinstalled mid-session: it issues a fresh
    # pairing token, so this integration's saved one starts getting 401s.
    # Previously that vanished into a debug log inside async_listen_events'
    # own retry loop with zero user-visible indication -- see
    # coordinator.py's _async_full_resync.
    commands = [{"id": "c1", "name": "TV Power", "type": "ir", "default_device_id": None}]
    app = make_app("secret-token", commands=commands)
    async with TestServer(app) as server:
        entry = await _setup_entry(hass, server, "secret-token")
        issue_registry = ir.async_get(hass)
        issue_id = f"auth_failed_{entry.entry_id}"
        assert issue_registry.async_get_issue(DOMAIN, issue_id) is None

        app[EXPECTED_TOKEN_KEY] = "a-new-token-after-reinstall"
        for ws in app[WS_CLIENTS_KEY]:
            await ws.send_json({"type": "command.updated", "data": {"command_id": "c1"}})

        for _ in range(50):
            await asyncio.sleep(0.1)
            if issue_registry.async_get_issue(DOMAIN, issue_id) is not None:
                break
        issue = issue_registry.async_get_issue(DOMAIN, issue_id)
        assert issue is not None
        assert issue.translation_key == "auth_failed"
        assert issue.severity == ir.IssueSeverity.ERROR

        # Recovery: re-pairing (or the token happening to match again)
        # should clear the issue on the next successful resync, not leave
        # a stale Repair card behind forever.
        app[EXPECTED_TOKEN_KEY] = "secret-token"
        for ws in app[WS_CLIENTS_KEY]:
            await ws.send_json({"type": "command.updated", "data": {"command_id": "c1"}})

        for _ in range(50):
            await asyncio.sleep(0.1)
            if issue_registry.async_get_issue(DOMAIN, issue_id) is None:
                break
        assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_diagnostics_redacts_token_and_lists_commands(hass):
    commands = [{"id": "c1", "name": "TV Power", "type": "ir", "default_device_id": "d1"}]
    app = make_app("secret-token", commands=commands)
    async with TestServer(app) as server:
        entry = await _setup_entry(hass, server, "secret-token")

        diagnostics = await async_get_config_entry_diagnostics(hass, entry)

        assert diagnostics["entry_data"]["token"] == "**REDACTED**"
        assert diagnostics["entry_data"]["host"] == server.host  # not sensitive, not redacted
        assert diagnostics["command_count"] == 1
        assert diagnostics["commands"] == [
            {"id": "c1", "name": "TV Power", "type": "ir", "has_default_device": True}
        ]
        assert diagnostics["last_update_success"] is True


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
        assert len(entities) == 4  # button/switch/select for "TV Power" + the singleton remote entity
        assert all(e.device_id == hub_device.id for e in entities)

        # unified mode: the hub device's own name ("IR/RF Hub")
        # no longer disambiguates between commands sharing it, so each
        # entity's own name must be qualified with the command name --
        # otherwise every command's Button/Switch/Send via would be
        # identically named and indistinguishable on the device's page.
        button = next(e for e in entities if e.entity_id.startswith("button."))
        assert hass.states.get(button.entity_id).attributes["friendly_name"] == "IR/RF Hub TV Power Button"


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

        # split-by-type mode: "Buttons"/"Switches" are shared across every
        # command, so each entity's own name must still carry the command
        # name to stay distinguishable from another command's button.
        assert hass.states.get(button.entity_id).attributes["friendly_name"] == "Buttons TV Power Button"
        assert hass.states.get(select.entity_id).attributes["friendly_name"] == "Buttons TV Power Send via"
        assert hass.states.get(switch.entity_id).attributes["friendly_name"] == "Switches TV Power Switch"

        # deleting the only command empties both virtual devices, which
        # should then be pruned just like a per-command device would be.
        commands.clear()
        for ws in app[WS_CLIENTS_KEY]:
            await ws.send_json({"type": "command.deleted", "data": {"command_id": "c1"}})
        for _ in range(50):
            await asyncio.sleep(0.1)
            if len(_entities_for(hass, entry)) == 1:
                break
        # Only the singleton remote entity survives -- it's never tied to
        # any one command's lifecycle, unlike button/select/switch.
        remaining = _entities_for(hass, entry)
        assert [e.entity_id.split(".")[0] for e in remaining] == ["remote"]
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
