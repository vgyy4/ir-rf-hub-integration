"""Periodically browses for ESPHome devices via Home Assistant Core's
shared Zeroconf instance and reports them to the App's
POST /api/integration/discovered-devices.

Why this lives here instead of the App just doing it itself: the App's
container sits on Supervisor's isolated internal network, and whether it
can actually see real LAN mDNS/multicast traffic depends on Supervisor's
Multicast plugin reaching it: not guaranteed for every install. Home
Assistant Core (where this integration runs) has reliable zeroconf
discovery regardless: it's the same mechanism the built-in `esphome`
integration itself relies on. So the integration browses independently
using Core's *shared* Zeroconf instance (async_get_async_instance --
never close a shared instance, HA Core owns its lifecycle) and reports
what it finds; the App merges that with its own best-effort local
attempt (see esphome/integration_discovery.py on the App side).

Deliberately an independent reimplementation of the App's own
esphome/discovery.py rather than a shared import, matching this
project's existing App/integration split (see pairing.py).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging

from homeassistant.components import zeroconf as ha_zeroconf
from homeassistant.core import HomeAssistant
from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo

from .api import IrRfHubClient

logger = logging.getLogger(__name__)

_SERVICE_TYPE = "_esphomelib._tcp.local."
_BROWSE_INTERVAL_S = 30
_BROWSE_WINDOW_S = 3.0
_RESOLVE_TIMEOUT_MS = 2000


async def async_report_esphome_devices_forever(hass: HomeAssistant, client: IrRfHubClient) -> None:
    """Runs for the lifetime of the config entry (started via
    entry.async_create_background_task in __init__.py, which cancels it
    automatically on unload): browse, report, sleep, repeat. Never lets
    a failed cycle kill the loop; just retries next interval.
    """
    logger.info("ESPHome discovery: starting background browse (service %s, every %ss)", _SERVICE_TYPE, _BROWSE_INTERVAL_S)
    aiozc = await ha_zeroconf.async_get_async_instance(hass)

    while True:
        try:
            devices = await _browse_once(aiozc.zeroconf)
            logger.debug("ESPHome discovery: browse cycle found %d device(s)", len(devices))
            if devices:
                await client.async_report_discovered_devices(devices)
                logger.info("ESPHome discovery: reported %d device(s) to the App", len(devices))
        except Exception:  # noqa: BLE001: background loop, one bad cycle shouldn't kill it
            logger.warning("ESPHome discovery report cycle failed, will retry", exc_info=True)
        await asyncio.sleep(_BROWSE_INTERVAL_S)


async def _browse_once(zeroconf) -> list[dict]:
    found: dict[str, dict] = {}
    pending: set[asyncio.Task] = set()

    async def _resolve(name: str) -> None:
        info = AsyncServiceInfo(_SERVICE_TYPE, name)
        if await info.async_request(zeroconf, _RESOLVE_TIMEOUT_MS):
            addresses = info.parsed_scoped_addresses()
            if not addresses:
                return
            host = next(
                (a for a in addresses if not ipaddress.ip_address(a.split("%")[0]).is_link_local),
                addresses[0],
            )
            found[name] = {
                "name": info.server.rstrip(".") if info.server else name,
                "host": host,
                "port": info.port or 6053,
            }

    # First param must be named exactly `zeroconf`: the zeroconf
    # library's Signal.fire() invokes listeners with keyword arguments
    # matching this name; anything else raises
    # "got an unexpected keyword argument 'zeroconf'" (confirmed by a
    # real install's logs, since this went uncaught by every test here
    #: none of them exercise a real Signal.fire() call).
    def _on_change(zeroconf, service_type, name, state_change) -> None:  # noqa: ANN001
        if state_change is ServiceStateChange.Added:
            pending.add(asyncio.ensure_future(_resolve(name)))

    browser = AsyncServiceBrowser(zeroconf, _SERVICE_TYPE, handlers=[_on_change])
    try:
        await asyncio.sleep(_BROWSE_WINDOW_S)
        if pending:
            await asyncio.wait(pending, timeout=2.0)
    finally:
        await browser.async_cancel()

    return list(found.values())
