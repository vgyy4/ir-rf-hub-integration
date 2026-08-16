"""One RemoteEntity per config entry: deliberately NOT one per Command
like button.py/switch.py/select.py. HA's `remote` domain conventionally
represents one whole physical remote control device, addressing individual
commands by *name* via remote.send_command's `command` list (the same
pattern Broadlink/Xiaomi Miio's own remote platforms use for a learned-IR
hub), not one entity per button: a per-command remote entity would have
no sensible meaning for what `command` should contain.

A free side effect of addressing by name: remote.send_command(command=
["TV Power", "AV Receiver On"]) fires multiple commands in one service
call, in order: a lightweight sequence/macro with no dedicated feature
needed, for whoever's scripting it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from homeassistant.components.remote import RemoteEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import IrRfHubApiError, IrRfHubAuthError
from .const import DOMAIN
from .coordinator import IrRfHubCoordinator

logger = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: IrRfHubCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([IrRfHubRemote(coordinator)])


class IrRfHubRemote(CoordinatorEntity[IrRfHubCoordinator], RemoteEntity):
    _attr_has_entity_name = True
    _attr_name = None  # unnamed: takes the device's own name (has_entity_name), there's only one
    _attr_assumed_state = True

    def __init__(self, coordinator: IrRfHubCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_remote"
        # No real hardware on/off state exists for a command hub: always
        # "on" so send_command is never blocked by a state this entity
        # can't actually track. turn_on/off exist only because RemoteEntity
        # requires them.
        self._attr_is_on = True

    @property
    def device_info(self) -> DeviceInfo:
        # Always the shared hub device (see __init__.py's
        # device_registry.async_get_or_create), regardless of the
        # per-command device-grouping option: there's exactly one of
        # this entity, so grouping mode doesn't apply to it.
        return DeviceInfo(identifiers={(DOMAIN, self.coordinator.entry.entry_id)})

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        names = list(command)
        by_name = {c.name: c.id for c in self.coordinator.data.values()}
        unknown = [name for name in names if name not in by_name]
        if unknown:
            raise HomeAssistantError(
                f"Unknown command(s): {', '.join(unknown)}: names must match exactly (case-sensitive)"
            )
        for name in names:
            try:
                await self.coordinator.client.async_fire_command(by_name[name])
            except IrRfHubAuthError as exc:
                raise HomeAssistantError(
                    "IR/RF Hub rejected our pairing token: reconfigure the integration"
                ) from exc
            except IrRfHubApiError as exc:
                raise HomeAssistantError(f'Could not fire command "{name}": {exc}') from exc
