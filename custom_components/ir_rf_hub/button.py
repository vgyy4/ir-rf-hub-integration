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
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import CommandRecord, IrRfHubApiError, IrRfHubAuthError
from .const import DOMAIN
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
        async_add_entities([IrRfHubButton(coordinator, command.id)])

    for command in coordinator.data.values():
        _add_new(command)

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_COMMAND_ADDED, _add_new))


class IrRfHubButton(IrRfHubCommandEntity, ButtonEntity):
    _attr_name = "Button"

    def __init__(self, coordinator: IrRfHubCoordinator, command_id: str) -> None:
        super().__init__(coordinator, command_id)
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
