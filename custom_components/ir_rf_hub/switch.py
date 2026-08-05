"""One self-resetting SwitchEntity per Command, alongside the ButtonEntity
in button.py -- an explicit user decision to have both, even though the
native "Button pressed" trigger alone would suffice for automations.
Turning it on fires the command, then it resets back to off shortly after
the fire call resolves, so it reads as a momentary trigger rather than a
real toggle state to both dashboards and automations.
"""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .api import CommandRecord, IrRfHubApiError, IrRfHubAuthError
from .const import DEFAULT_SWITCH_RESET_DELAY_S, DOMAIN
from .coordinator import SIGNAL_COMMAND_ADDED, SIGNAL_COMMAND_REMOVED, IrRfHubCoordinator
from .entity import IrRfHubCommandEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: IrRfHubCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: dict[str, IrRfHubSwitch] = {}

    @callback
    def _add_new(command: CommandRecord) -> None:
        if command.id in entities:
            return
        entity = IrRfHubSwitch(coordinator, command.id)
        entities[command.id] = entity
        async_add_entities([entity])

    @callback
    def _remove(command_id: str) -> None:
        entity = entities.pop(command_id, None)
        if entity is None or entity.entity_id is None:
            return
        registry = er.async_get(hass)
        if registry.async_get(entity.entity_id) is not None:
            registry.async_remove(entity.entity_id)

        # See button.py's matching block -- one HA Device per Command,
        # shared with the sibling button entity, that HA never auto-
        # removes on its own. Safe from both platforms: whichever runs
        # second is the one that finds zero entities left.
        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(identifiers={(DOMAIN, command_id)})
        if device is not None and not er.async_entries_for_device(registry, device.id, include_disabled_entities=True):
            device_registry.async_remove_device(device.id)

    for command in coordinator.data.values():
        _add_new(command)

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_COMMAND_ADDED, _add_new))
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_COMMAND_REMOVED, _remove))


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

        # Scheduled as a detached callback, NOT awaited inline: a blocking
        # service call (hass.services.async_call(..., blocking=True), which
        # scripts/automations commonly use) would otherwise not return
        # until the full reset delay elapsed, and the "on" state would
        # never be observable to a caller awaiting that call at all.
        async_call_later(self.hass, DEFAULT_SWITCH_RESET_DELAY_S, self._async_reset)

    @callback
    def _async_reset(self, _now) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        # Momentary by design -- turning off manually just cancels the
        # auto-reset early, it never fires the command again.
        self._attr_is_on = False
        self.async_write_ha_state()
