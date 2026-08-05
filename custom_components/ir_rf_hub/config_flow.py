"""Two ways into this flow:

- async_step_hassio -- the normal path. The App announces itself to
  Supervisor's Discovery API as soon as it has a pairing token (see its
  supervisor_discovery.py), which Home Assistant Core turns into a
  "Discovered" card here automatically: no code to copy, no field to
  fill in, just a confirm button. Mirrors the mechanism Music
  Assistant's own App+integration pair uses for the same problem.
- async_step_user -- manual fallback for installs where that hand-off
  can't happen: the App running outside Supervisor entirely (plain
  Docker, no SUPERVISOR_TOKEN), or this integration installed *after*
  the App already announced and gave up. Paste the pairing code shown
  on the App's blocking first-run screen; decodes to {host, port,
  token} via pairing.py, which mirrors the App's security.py encoder.

Both converge on the same connectivity+auth check before creating an
entry -- see ARCHITECTURE.md's Pairing section.
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState, ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .api import IrRfHubApiError, IrRfHubAuthError, IrRfHubClient
from .const import CONF_HOST, CONF_PORT, CONF_TOKEN, DOMAIN
from .pairing import PairingCodeError, decode_pairing_code

logger = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema({vol.Required("pairing_code"): str})


class IrRfHubConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovered_data: dict[str, str | int] | None = None

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                host, port, token = decode_pairing_code(user_input["pairing_code"])
            except PairingCodeError:
                errors["pairing_code"] = "invalid_code"
            else:
                session = async_get_clientsession(self.hass)
                client = IrRfHubClient(session, host, port, token)
                try:
                    await client.async_get_health()
                except IrRfHubAuthError:
                    errors["base"] = "invalid_auth"
                except IrRfHubApiError:
                    errors["base"] = "cannot_connect"
                else:
                    if result := await self._async_update_existing_entry(host, port, token):
                        return result
                    return self.async_create_entry(
                        title="IR/RF Command Hub",
                        data={CONF_HOST: host, CONF_PORT: port, CONF_TOKEN: token},
                    )

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    async def async_step_hassio(self, discovery_info: HassioServiceInfo) -> ConfigFlowResult:
        config = discovery_info.config
        host, port, token = config["host"], config["port"], config["token"]

        session = async_get_clientsession(self.hass)
        client = IrRfHubClient(session, host, port, token)
        try:
            await client.async_get_health()
        except IrRfHubAuthError:
            return self.async_abort(reason="invalid_auth")
        except IrRfHubApiError:
            return self.async_abort(reason="cannot_connect")

        if result := await self._async_update_existing_entry(host, port, token):
            return result

        self._discovered_data = {CONF_HOST: host, CONF_PORT: port, CONF_TOKEN: token}
        return await self.async_step_hassio_confirm()

    async def _async_update_existing_entry(self, host: str, port: int, token: str) -> ConfigFlowResult | None:
        """Refreshes a matching existing entry's data and reloads it,
        instead of _abort_if_unique_id_configured()'s default of
        silently rejecting re-pairing. The App issues a fresh pairing
        token on every reinstall, while Supervisor keeps the add-on's
        internal hostname stable across a plain uninstall+reinstall of
        the same add-on -- so re-pairing with the same host:port but a
        new token is the normal "the App got reinstalled" case, not a
        duplicate to reject. Leaving the old token in place strands the
        user with a permanently-failing entry (coordinator setup keeps
        raising IrRfHubAuthError forever) and no obvious way to fix it
        short of manually deleting and re-adding the integration --
        confirmed on a real install.

        Returns an abort result if a match was found (already updated
        and reload scheduled); None if this is a genuinely new pairing,
        so the caller should proceed to create an entry.
        """
        entry = await self.async_set_unique_id(f"{host}:{port}")
        if entry is None:
            return None

        if self.hass.config_entries.async_update_entry(
            entry, data={CONF_HOST: host, CONF_PORT: port, CONF_TOKEN: token}
        ):
            if entry.state in (
                ConfigEntryState.LOADED,
                ConfigEntryState.SETUP_ERROR,
                ConfigEntryState.SETUP_RETRY,
            ):
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return self.async_abort(reason="already_configured")

    async def async_step_hassio_confirm(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            assert self._discovered_data is not None
            return self.async_create_entry(title="IR/RF Command Hub", data=self._discovered_data)

        self._set_confirm_only()
        return self.async_show_form(step_id="hassio_confirm")
