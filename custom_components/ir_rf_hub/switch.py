"""One self-resetting SwitchEntity per Command, alongside the ButtonEntity
in button.py -- an explicit user decision to have both, even though the
native "Button pressed" trigger alone would suffice for automations.
Turning it on fires the command, then it resets back to off shortly after
the fire call resolves, so it reads as a momentary trigger rather than a
real toggle state to both dashboards and automations.
"""

from __future__ import annotations

import asyncio

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import CommandRecord, IrRfHubApiError, IrRfHubAuthError
from .const import DEFAULT_SWITCH_RESET_DELAY_S, DOMAIN
from .coordinator import SIGNAL_COMMAND_ADDED, IrRfHubCoordinator
from .entity import IrRfHubCommandEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: IrRfHubCoordinator = hass.data[DOMAIN][entry.entry_id]
    added: set[str] = set()

    @callback
    def _add_new(command: CommandRecord) -> None:
        if command.id in added:
            return
        added.add(command.id)
        async_add_entities([IrRfHubSwitch(coordinator, command.id)])

    for command in coordinator.data.values():
        _add_new(command)

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_COMMAND_ADDED, _add_new))


class IrRfHubSwitch(IrRfHubCommandEntity, SwitchEntity):
    _attr_name = "Switch"
    _attr_assumed_state = True

    def __init__(self, coordinator: IrRfHubCoordinator, command_id: str) -> None:
        super().__init__(coordinator, command_id)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{command_id}_switch"
        self._attr_is_on = False

    async def async_turn_on(self, **kwargs) -> None:
        try:
            await self.coordinator.client.async_fire_command(self._command_id)
        except IrRfHubAuthError as exc:
            raise HomeAssistantError("IR/RF Command Hub rejected our pairing token -- reconfigure the integration") from exc
        except IrRfHubApiError as exc:
            raise HomeAssistantError(f"Could not fire command: {exc}") from exc

        self._attr_is_on = True
        self.async_write_ha_state()
        await asyncio.sleep(DEFAULT_SWITCH_RESET_DELAY_S)
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        # Momentary by design -- turning off manually just cancels the
        # auto-reset early, it never fires the command again.
        self._attr_is_on = False
        self.async_write_ha_state()
