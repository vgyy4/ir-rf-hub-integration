"""One SelectEntity per Command, alongside the button/switch pair in
button.py/switch.py: picking an option fires the command through that
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
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import IrRfHubApiError, IrRfHubAuthError
from .coordinator import IrRfHubCoordinator
from .entity import IrRfHubCommandEntity, async_setup_command_entities

logger = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_setup_command_entities(hass, entry, async_add_entities, IrRfHubDeviceSelect)


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
        live: add or remove an ESP device afterward and this won't
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
            raise HomeAssistantError("IR/RF Hub rejected our pairing token: reconfigure the integration") from exc
        except IrRfHubApiError as exc:
            raise HomeAssistantError(f"Could not fire command: {exc}") from exc

        self._attr_current_option = option
        self.async_write_ha_state()
