"""Diagnostics support: Settings > Devices & services > IR/RF Hub
> the entry's ⋮ menu > Download diagnostics. Useful for troubleshooting
without asking someone to dig through logs or paste their pairing token.

The token is the only thing worth redacting here: host/port are
Supervisor-internal (never reachable from outside the box, see
ARCHITECTURE.md's Pairing section on the App side), and command
id/name/type/default-device-set-or-not are the same information already
visible in the entity list itself.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_TOKEN, DOMAIN
from .coordinator import IrRfHubCoordinator

TO_REDACT = {CONF_TOKEN}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    coordinator: IrRfHubCoordinator = hass.data[DOMAIN][entry.entry_id]
    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "entry_options": dict(entry.options),
        "command_count": len(coordinator.data),
        "commands": [
            {
                "id": command.id,
                "name": command.name,
                "type": command.type,
                "has_default_device": command.default_device_id is not None,
            }
            for command in coordinator.data.values()
        ],
        "last_update_success": coordinator.last_update_success,
    }
