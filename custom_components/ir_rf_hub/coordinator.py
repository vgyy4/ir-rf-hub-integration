"""Holds the live command list and drives dynamic entity add/update/remove
as the App's library changes. Push-driven (no polling interval) -- the App's
/api/ws tells us *something* changed, and rather than parsing which
event type and doing a granular add/update/remove, we just do a full
REST resync every time. The command list is small (dozens, not
thousands), so this trades a marginally larger REST call for a
meaningfully simpler, harder-to-get-wrong implementation -- no risk of a
missed/duplicate event leaving an entity dangling. WS reconnect (handled
inside IrRfHubClient.async_listen_events) triggers the same resync path,
which is also what makes it the correctness backstop the design calls for.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import CommandRecord, IrRfHubClient
from .const import DOMAIN
from .sync import diff_ids

logger = logging.getLogger(__name__)

SIGNAL_COMMAND_ADDED = f"{DOMAIN}_command_added"
SIGNAL_COMMAND_REMOVED = f"{DOMAIN}_command_removed"


class IrRfHubCoordinator(DataUpdateCoordinator[dict[str, CommandRecord]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: IrRfHubClient) -> None:
        super().__init__(hass, logger, name=DOMAIN, update_interval=None)
        self.entry = entry
        self.client = client
        self.data = {}

    async def async_setup(self) -> None:
        await self._async_full_resync()
        self.entry.async_create_background_task(
            self.hass,
            self.client.async_listen_events(self._on_event, self._async_full_resync),
            name=f"{DOMAIN}_event_listener_{self.entry.entry_id}",
        )

    async def _on_event(self, event: dict) -> None:
        if event.get("type") in ("command.created", "command.updated", "command.deleted"):
            await self._async_full_resync()

    async def _async_full_resync(self) -> None:
        commands = await self.client.async_get_commands()
        new_data = {c.id: c for c in commands}
        old_ids = set(self.data.keys()) if self.data else set()
        added, removed = diff_ids(old_ids, set(new_data.keys()))

        self.async_set_updated_data(new_data)

        for added_id in added:
            async_dispatcher_send(self.hass, SIGNAL_COMMAND_ADDED, new_data[added_id])
        for removed_id in removed:
            async_dispatcher_send(self.hass, SIGNAL_COMMAND_REMOVED, removed_id)
