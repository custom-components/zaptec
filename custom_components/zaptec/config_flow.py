"""Adds config flow for zaptec."""
from __future__ import annotations

import logging
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import data_entry_flow
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    Account,
    AuthenticationError,
    Charger,
    RequestConnectionError,
    RequestDataError,
    RequestRetryError,
    RequestTimeoutError,
)
from .const import CONF_CHARGERS, CONF_MANUAL_SELECT, CONF_PREFIX, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class ZaptecFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Blueprint."""

    VERSION = 1

    def __init__(self):
        """Initialize."""
        self._account: Account | None = None
        self._input: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Handle a flow initiated by the user."""
        errors: dict[str, str] = {}

        if self._async_current_entries():
            return self.async_abort(reason="already_exists")

        if user_input is not None:
            valid_login = False
            try:
                valid_login = await Account.check_login(
                    username=user_input[CONF_USERNAME],
                    password=user_input[CONF_PASSWORD],
                )
            except (RequestConnectionError, RequestTimeoutError):
                errors["base"] = "cannot_connect"
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

            if valid_login:
                unique_id = user_input[CONF_USERNAME].lower()
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                if user_input.get(CONF_MANUAL_SELECT, False):
                    self._input = user_input
                    return await self.async_step_chargers()

                return self.async_create_entry(
                    title=DOMAIN.capitalize(), data=user_input
                )

        data = {
            vol.Required(CONF_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Optional(CONF_PREFIX): str,
            vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.Coerce(
                int
            ),
            vol.Optional(CONF_MANUAL_SELECT): bool,
        }

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(data),
            errors=errors,
        )

    async def async_step_chargers(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Handle login steps"""
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input.get(CONF_CHARGERS):
                self._input.update(user_input)

                return self.async_create_entry(
                    title=DOMAIN.capitalize(), data=self._input
                )

            errors["base"] = "no_chargers_selected"

        try:
            if not self._account:
                self._account = Account(
                    username=self._input[CONF_USERNAME],
                    password=self._input[CONF_PASSWORD],
                    client=async_get_clientsession(self.hass),
                )

            # Build the hierarchy, but don't fetch any detailed data yet
            if not self._account.is_built:
                await self._account.build()

            # Get all chargers
            chargers = self._account.get_chargers()
        except (RequestConnectionError, RequestTimeoutError, RequestDataError):
            errors["base"] = "cannot_connect"
            chargers = []
        except (AuthenticationError, RequestRetryError):
            errors["base"] = "invalid_auth"
            chargers = []
        except Exception:
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
            chargers = []

        def charger_text(charger: Charger):
            text = f"{charger.name} ({getattr(charger, 'device_id', '-')})"
            circuit = charger.circuit
            if circuit:
                text += f" in {circuit.name} circuit"
                if circuit.installation:
                    text += f" of {circuit.installation.name} installation"
            return text

        data = {
            vol.Required(CONF_CHARGERS): cv.multi_select(
                {charger.id: charger_text(charger) for charger in chargers},
            ),
        }

        return self.async_show_form(
            step_id="chargers",
            data_schema=vol.Schema(data),
            errors=errors,
        )

    async def async_step_import(self, user_input):  # pylint: disable=unused-argument
        """Import a config entry.
        Special type of import, we're not actually going to store any data.
        Instead, we're going to rely on the values that are in config file.
        """
        return await self.async_step_user(user_input)
