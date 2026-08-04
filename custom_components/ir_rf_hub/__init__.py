from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import IrRfHubClient
from .const import CONF_HOST, CONF_PORT, CONF_TOKEN, DOMAIN
from .coordinator import IrRfHubCoordinator

PLATFORMS = ["button", "switch"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    client = IrRfHubClient(session, entry.data[CONF_HOST], entry.data[CONF_PORT], entry.data[CONF_TOKEN])

    coordinator = IrRfHubCoordinator(hass, entry, client)
    await coordinator.async_setup()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
