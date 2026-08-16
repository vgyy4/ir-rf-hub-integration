"""One self-resetting SwitchEntity per Command, alongside the ButtonEntity
in button.py: an explicit user decision to have both, even though the
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
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .api import IrRfHubApiError, IrRfHubAuthError
from .const import DEFAULT_SWITCH_RESET_DELAY_S
from .coordinator import IrRfHubCoordinator
from .entity import IrRfHubCommandEntity, async_setup_command_entities


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_setup_command_entities(hass, entry, async_add_entities, IrRfHubSwitch)


class IrRfHubSwitch(IrRfHubCommandEntity, SwitchEntity):
    _attr_assumed_state = True

    def __init__(self, coordinator: IrRfHubCoordinator, command_id: str) -> None:
        super().__init__(coordinator, command_id, entity_kind="switch")
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{command_id}_switch"
        self._attr_is_on = False

    @property
    def name(self) -> str:
        return self._qualified_name("Switch")

    async def async_turn_on(self, **kwargs) -> None:
        try:
            await self.coordinator.client.async_fire_command(self._command_id)
        except IrRfHubAuthError as exc:
            raise HomeAssistantError("IR/RF Hub rejected our pairing token: reconfigure the integration") from exc
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
        # Momentary by design: turning off manually just cancels the
        # auto-reset early, it never fires the command again.
        self._attr_is_on = False
        self.async_write_ha_state()
