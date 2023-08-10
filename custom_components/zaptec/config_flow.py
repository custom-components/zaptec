"""Adds config flow for zaptec."""
from __future__ import annotations

import logging

import aiohttp
import voluptuous as vol
from homeassistant import config_entries

from .api import Account, AuthorizationError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class ZaptecFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Blueprint."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    def __init__(self):
        """Initialize."""
        self._errors = {}

    async def async_step_user(
        self, user_input=None
    ):  # pylint: disable=dangerous-default-value
        """Handle a flow initialized by the user."""
        errors = {}

        entries = self.hass.config_entries.async_entries(DOMAIN)
        if entries:
            return self.async_abort(reason="already_setup")

        data_schema = {
            vol.Required("username", default=""): str,
            vol.Required("password", default=""): str,
            vol.Optional("scan_interval", default=30): vol.Coerce(int),
        }

        placeholders = {
            "username": "firstname.lastname@gmail.com",
            "password": "your password",
            "scan_interval": 30,
        }

        if user_input is not None:
            valid_login = False
            try:
                valid_login = await Account.check_login(
                    user_input["username"], user_input["password"]
                )
            except aiohttp.ClientConnectorError:
                errors["base"] = "connection_failure"
            except AuthorizationError:
                errors["base"] = "auth_failure"

            if valid_login:
                unique_id = user_input["username"].lower()
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=DOMAIN.capitalize(), data=user_input
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(data_schema),
            description_placeholders=placeholders,
            errors=errors,
        )

    async def async_step_import(self, user_input):  # pylint: disable=unused-argument
        """Import a config entry.
        Special type of import, we're not actually going to store any data.
        Instead, we're going to rely on the values that are in config file.
        """
        return self.async_create_entry(title="configuration.yaml", data={})
