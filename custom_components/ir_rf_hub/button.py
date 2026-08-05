"""One ButtonEntity per Command. async_press fires it; the native
'Button pressed' automation trigger and any dashboard button card work
automatically since this is a stock ButtonEntity -- no custom trigger
platform needed.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import CommandRecord, IrRfHubApiError, IrRfHubAuthError
from .const import DOMAIN
from .coordinator import SIGNAL_COMMAND_ADDED, SIGNAL_COMMAND_REMOVED, IrRfHubCoordinator
from .entity import IrRfHubCommandEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: IrRfHubCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: dict[str, IrRfHubButton] = {}

    @callback
    def _add_new(command: CommandRecord) -> None:
        if command.id in entities:
            return
        entity = IrRfHubButton(coordinator, command.id)
        entities[command.id] = entity
        async_add_entities([entity])

    @callback
    def _remove(command_id: str) -> None:
        entity = entities.pop(command_id, None)
        if entity is None or entity.entity_id is None:
            return
        # entity.async_remove() alone tears down the live entity/state but
        # doesn't reliably purge the entity *registry* entry -- explicit
        # registry removal is what actually makes a deleted command's
        # entities disappear for good, not just go unavailable.
        registry = er.async_get(hass)
        registry_entry = registry.async_get(entity.entity_id)
        device_id = registry_entry.device_id if registry_entry is not None else None
        if registry_entry is not None:
            registry.async_remove(entity.entity_id)

        # Depending on the device-grouping option (see device_grouping.py),
        # this device may be per-command, shared across many commands
        # (split-by-type), or the permanent hub device itself (unified) --
        # look it up by the entity's own device_id rather than assuming an
        # identifier, and never remove the hub device. HA never auto-
        # removes a device just because its entities are gone, so without
        # this a deleted command (or the last entity of a shared device)
        # leaves a permanent zero-entity ghost behind. Safe to run from
        # button.py/switch.py/select.py's _remove independently: whichever
        # runs last is the one that actually finds zero entities left.
        if device_id is None:
            return
        device_registry = dr.async_get(hass)
        device = device_registry.async_get(device_id)
        if device is None or (DOMAIN, entry.entry_id) in device.identifiers:
            return
        if not er.async_entries_for_device(registry, device.id, include_disabled_entities=True):
            device_registry.async_remove_device(device.id)

    for command in coordinator.data.values():
        _add_new(command)

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_COMMAND_ADDED, _add_new))
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_COMMAND_REMOVED, _remove))


class IrRfHubButton(IrRfHubCommandEntity, ButtonEntity):
    _attr_name = "Button"

    def __init__(self, coordinator: IrRfHubCoordinator, command_id: str) -> None:
        super().__init__(coordinator, command_id, entity_kind="button")
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{command_id}_button"

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.async_fire_command(self._command_id)
        except IrRfHubAuthError as exc:
            raise HomeAssistantError("IR/RF Command Hub rejected our pairing token -- reconfigure the integration") from exc
        except IrRfHubApiError as exc:
            # Deliberately loud: a button press with no human present to
            # ask "which ESP?" (e.g. this command has no default device)
            # should fail visibly in the automation/logbook, not no-op.
            raise HomeAssistantError(f"Could not fire command: {exc}") from exc
