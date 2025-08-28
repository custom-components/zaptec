"""Adds config flow for zaptec."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import SOURCE_RECONFIGURE, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType
import voluptuous as vol

from .const import CONF_CHARGERS, CONF_MANUAL_SELECT, CONF_PREFIX, DOMAIN
from .zaptec import (
    AuthenticationError,
    Charger,
    RequestConnectionError,
    RequestDataError,
    RequestRetryError,
    RequestTimeoutError,
    Zaptec,
)

_LOGGER = logging.getLogger(__name__)


class ZaptecFlowHandler(ConfigFlow, domain=DOMAIN):
    """Config flow for Zaptec."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow handler."""
        self.zaptec: Zaptec | None = None
        self.user_input: dict[str, Any] = {}
        self.chargers: tuple[dict[str, str], dict[str, str]] | None = None

    async def _validate_account(self, user_input: dict[str, Any]) -> dict[str, str]:
        """Validate the Zaptec account credentials and return any errors."""
        errors: dict[str, str] = {}

        try:
            self.zaptec = Zaptec(
                username=user_input[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
                client=async_get_clientsession(self.hass),
            )
            await self.zaptec.login()
        except (RequestConnectionError, RequestTimeoutError):
            errors["base"] = "cannot_connect"
        except AuthenticationError:
            errors["base"] = "invalid_auth"
        except Exception:
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"

        return errors

    async def _get_chargers(self) -> tuple[dict[str, str], dict[str, str]]:
        """Fetch a list of chargers from the Zaptec API."""
        errors: dict[str, str] = {}
        chargers: list[Charger] = []

        # Cache the chargers to avoid multiple API calls
        if self.chargers is not None:
            return self.chargers

        # This sets up a new account if it doesn't exist
        if not self.zaptec:
            errors = await self._validate_account(self.user_input)

        try:
            if not errors:
                # Build the hierarchy, but don't fetch any detailed data yet
                if not self.zaptec.is_built:
                    await self.zaptec.build()

                # Get all chargers
                chargers = list(self.zaptec.chargers)

        except (RequestConnectionError, RequestTimeoutError, RequestDataError):
            errors["base"] = "cannot_connect"
        except (AuthenticationError, RequestRetryError):
            errors["base"] = "invalid_auth"
        except Exception:
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"

        def charger_text(charger: Charger) -> str:
            """Format the charger text for display."""
            text = f"{charger.name} ({charger.get('DeviceId', '-')})"
            if circuit := charger.get("CircuitName"):
                text += f" in {circuit} circuit"
            if charger.installation:
                text += f" of {charger.installation.name} installation"
            return text

        result = {charger.id: charger_text(charger) for charger in chargers}, errors
        self.chargers = result
        return result

    def get_suggested_values(self) -> dict[str, Any]:
        """Get suggested values for the user input."""
        suggested_values: dict[str, Any] = {}
        if self.source == SOURCE_RECONFIGURE:
            reconfigure_entry = self._get_reconfigure_entry()
            suggested_values = reconfigure_entry.data
        return suggested_values

    def finalize_user_input(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        """Finalize the user input."""
        # Add suggested values to the user input
        if self.source != SOURCE_RECONFIGURE:
            return self.async_create_entry(title=DOMAIN.capitalize(), data=user_input)

        # Reconfiguring
        reconfigure_entry = self._get_reconfigure_entry()
        return self.async_update_reload_and_abort(
            reconfigure_entry,
            data={
                **reconfigure_entry.data,
                **user_input,
            },
            reload_even_if_entry_is_unchanged=False,
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle a configuration flow initiated by the user."""
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = await self._validate_account(user_input)

            if not errors:
                # Validate the unique ID
                unique_id = user_input[CONF_USERNAME].lower()
                await self.async_set_unique_id(unique_id)

                if self.source == SOURCE_RECONFIGURE:
                    self._abort_if_unique_id_mismatch()
                else:
                    self._abort_if_unique_id_configured()

                self.user_input = user_input

                # Display the optional charger selection form
                if user_input.get(CONF_MANUAL_SELECT, False):
                    return await self.async_step_chargers()

                # Finalize the user input
                return self.finalize_user_input(self.user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): TextSelector(
                    TextSelectorConfig(
                        type=TextSelectorType.EMAIL,
                        autocomplete="email",
                    ),
                ),
                vol.Required(CONF_PASSWORD): TextSelector(
                    TextSelectorConfig(
                        type=TextSelectorType.PASSWORD,
                        autocomplete="current-password",
                    ),
                ),
                vol.Optional(CONF_PREFIX): str,
                vol.Optional(CONF_MANUAL_SELECT): bool,
            }
        )

        return self.async_show_form(
            step_id="reconfigure" if self.source == SOURCE_RECONFIGURE else "user",
            data_schema=self.add_suggested_values_to_schema(
                data_schema=schema,
                suggested_values=self.get_suggested_values(),
            ),
            errors=errors,
        )

    async def async_step_chargers(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle configuration flow for selecting the chargers."""
        errors: dict[str, str] = {}

        _LOGGER.debug("async_step_chargers called with user_input: %s", user_input)

        if user_input is not None:
            if user_input.get(CONF_CHARGERS):
                self.user_input.update(user_input)

                # Finalize the user input
                return self.finalize_user_input(self.user_input)

            errors["base"] = "no_chargers_selected"

        chargers, _errors = await self._get_chargers()
        errors.update(_errors)

        schema = vol.Schema(
            {
                vol.Required(CONF_CHARGERS): cv.multi_select(chargers),
            }
        )

        return self.async_show_form(
            step_id="chargers",
            data_schema=self.add_suggested_values_to_schema(
                data_schema=schema,
                suggested_values=self.get_suggested_values(),
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure an existing config entry."""
        return await self.async_step_user(user_input)

    async def async_step_reauth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a reauthorization flow request."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauth dialog."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        _LOGGER.debug("async_step_reauth_confirm called with user_input: %s", user_input)

        if user_input is not None:
            entries = {
                **reauth_entry.data,
                **user_input,
            }
            errors = await self._validate_account(entries)

            if not errors:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data=entries,
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_PASSWORD): TextSelector(
                    TextSelectorConfig(
                        type=TextSelectorType.PASSWORD,
                        autocomplete="current-password",
                    ),
                ),
            }
        )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
        )
