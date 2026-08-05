"""One SelectEntity per Command, alongside the button/switch pair in
button.py/switch.py -- picking an option fires the command through that
specific ESP device. Neither a bare button press nor a switch turn_on
has any way to expose "which device" as a choice; this does, and since
select.select_option is a standard HA service every select entity
already supports for free, it's pickable when building an automation/
script/scene's action, not just from a dashboard card or this
integration's own entity page.
"""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
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

logger = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: IrRfHubCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: dict[str, IrRfHubDeviceSelect] = {}

    @callback
    def _add_new(command: CommandRecord) -> None:
        if command.id in entities:
            return
        entity = IrRfHubDeviceSelect(coordinator, command.id)
        entities[command.id] = entity
        async_add_entities([entity])

    @callback
    def _remove(command_id: str) -> None:
        entity = entities.pop(command_id, None)
        if entity is None or entity.entity_id is None:
            return
        # See button.py's matching block: explicit registry removal is
        # what actually makes a deleted command's entities disappear for
        # good, and device grouping is mode-dependent -- look the device
        # up by the entity's own device_id and never remove the hub
        # device. Safe to run from all three platforms independently,
        # whichever runs last is the one that finds zero entities
        # remaining.
        registry = er.async_get(hass)
        registry_entry = registry.async_get(entity.entity_id)
        device_id = registry_entry.device_id if registry_entry is not None else None
        if registry_entry is not None:
            registry.async_remove(entity.entity_id)

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


class IrRfHubDeviceSelect(IrRfHubCommandEntity, SelectEntity):
    def __init__(self, coordinator: IrRfHubCoordinator, command_id: str) -> None:
        super().__init__(coordinator, command_id, entity_kind="select")
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{command_id}_select"
        self._attr_options: list[str] = []
        self._device_ids_by_name: dict[str, str] = {}

    @property
    def name(self) -> str:
        return self._qualified_name("Send via")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._async_refresh_options()

    async def _async_refresh_options(self) -> None:
        """Fetched once when the entity is added, not kept continuously
        live -- add or remove an ESP device afterward and this won't
        notice until the integration reloads. The App's candidate-
        devices endpoint already restricts this to devices with a
        transmitter matching the command's IR/RF type, same as the
        App's own "which ESP?" picker.
        """
        try:
            candidates = await self.coordinator.client.async_get_candidate_devices(self._command_id)
        except (IrRfHubAuthError, IrRfHubApiError):
            logger.debug("Could not fetch candidate devices for %s", self._command_id, exc_info=True)
            return

        self._device_ids_by_name = {c.name: c.id for c in candidates}
        self._attr_options = list(self._device_ids_by_name.keys())

        command = self._command
        default_device_id = command.default_device_id if command else None
        self._attr_current_option = next(
            (name for name, device_id in self._device_ids_by_name.items() if device_id == default_device_id),
            None,
        )
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        device_id = self._device_ids_by_name.get(option)
        if device_id is None:
            raise HomeAssistantError(f'Unknown device "{option}"')
        try:
            await self.coordinator.client.async_fire_command(self._command_id, device_id=device_id)
        except IrRfHubAuthError as exc:
            raise HomeAssistantError("IR/RF Command Hub rejected our pairing token -- reconfigure the integration") from exc
        except IrRfHubApiError as exc:
            raise HomeAssistantError(f"Could not fire command: {exc}") from exc

        self._attr_current_option = option
        self.async_write_ha_state()
