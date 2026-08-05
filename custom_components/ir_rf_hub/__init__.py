from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import IrRfHubClient
from .const import CONF_HOST, CONF_PORT, CONF_TOKEN, DOMAIN
from .coordinator import IrRfHubCoordinator
from .esphome_discovery import async_report_esphome_devices_forever

PLATFORMS = ["button", "switch", "select"]


def _async_prune_orphaned_command_devices(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: IrRfHubCoordinator
) -> None:
    """One-time reconciliation at setup, not just reactive: catches ghost
    per-command devices left behind by commands deleted *before*
    button.py/switch.py's SIGNAL_COMMAND_REMOVED handler started also
    removing the device (or from that live path being missed for any
    other reason, e.g. the App unreachable at the exact moment of
    deletion) -- runs against every existing entry on every setup, so it
    self-heals regardless of when the fix actually landed for a given
    install.
    """
    device_registry = dr.async_get(hass)
    valid_ids = set(coordinator.data.keys()) | {entry.entry_id}
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        identifiers = {identifier for domain, identifier in device.identifiers if domain == DOMAIN}
        if identifiers and not identifiers & valid_ids:
            device_registry.async_remove_device(device.id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    client = IrRfHubClient(session, entry.data[CONF_HOST], entry.data[CONF_PORT], entry.data[CONF_TOKEN])

    coordinator = IrRfHubCoordinator(hass, entry, client)
    await coordinator.async_setup()
    _async_prune_orphaned_command_devices(hass, entry, coordinator)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Best-effort: lets the App discover ESPHome devices for its "add a
    # device" UI via Home Assistant Core's reliable zeroconf instead of
    # its own (not always reachable) local mDNS -- see
    # esphome_discovery.py. Tied to the entry's lifecycle, so it's
    # cancelled automatically on unload.
    entry.async_create_background_task(
        hass,
        async_report_esphome_devices_forever(hass, client),
        name=f"{DOMAIN}_esphome_discovery_{entry.entry_id}",
    )

    # The umbrella "hub" device every per-command device nests under via
    # via_device (see entity.py) -- must actually exist in the registry
    # before any entity references it, or HA logs a warning today and will
    # hard-fail in 2025.12.0.
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="IR/RF Command Hub",
        manufacturer="IR/RF Command Hub",
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
