"""Single-field setup: paste the pairing code shown on the App's Settings
page. Decodes to {host, port, token} -- see pairing.py, which mirrors the
App's security.py encoder. No separate host/port entry, no zeroconf
dependency, per the deliberate design choice to keep the App on the
isolated Docker network (see ARCHITECTURE.md).
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import IrRfHubApiError, IrRfHubAuthError, IrRfHubClient
from .const import CONF_HOST, CONF_PORT, CONF_TOKEN, DOMAIN
from .pairing import PairingCodeError, decode_pairing_code

logger = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema({vol.Required("pairing_code"): str})


class IrRfHubConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

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
                    await self.async_set_unique_id(f"{host}:{port}")
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title="IR/RF Command Hub",
                        data={CONF_HOST: host, CONF_PORT: port, CONF_TOKEN: token},
                    )

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)
