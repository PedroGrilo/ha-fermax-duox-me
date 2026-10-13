"""Config flow for Fermax DuoxMe."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FermaxApiError, FermaxAuthError, FermaxDuoxmeClient
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MIN_UPDATE_INTERVAL,
)
from .coordinator import FermaxConfigEntry

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class FermaxDuoxmeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            password = user_input[CONF_PASSWORD]
            session = async_get_clientsession(self.hass)
            client = FermaxDuoxmeClient(session)
            try:
                tokens = await client.async_login(email, password)
            except FermaxAuthError:
                errors["base"] = "invalid_auth"
            except FermaxApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during login")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(email.lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=email,
                    data={
                        CONF_EMAIL: email,
                        CONF_ACCESS_TOKEN: tokens.access_token,
                        CONF_REFRESH_TOKEN: tokens.refresh_token,
                        CONF_TOKEN_EXPIRES_AT: tokens.expires_at,
                    },
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            email = entry.data[CONF_EMAIL]
            session = async_get_clientsession(self.hass)
            client = FermaxDuoxmeClient(session)
            try:
                tokens = await client.async_login(email, user_input[CONF_PASSWORD])
            except FermaxAuthError:
                errors["base"] = "invalid_auth"
            except FermaxApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        **entry.data,
                        CONF_ACCESS_TOKEN: tokens.access_token,
                        CONF_REFRESH_TOKEN: tokens.refresh_token,
                        CONF_TOKEN_EXPIRES_AT: tokens.expires_at,
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={CONF_EMAIL: entry.data[CONF_EMAIL]},
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: FermaxConfigEntry) -> OptionsFlow:
        return FermaxOptionsFlow()


class FermaxOptionsFlow(OptionsFlow):
    """Allow tuning the polling interval."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(
            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_UPDATE_INTERVAL, default=current
                    ): vol.All(int, vol.Range(min=MIN_UPDATE_INTERVAL, max=3600)),
                }
            ),
        )
