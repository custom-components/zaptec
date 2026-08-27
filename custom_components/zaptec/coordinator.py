"""HA Coordinator for Zaptec integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    REQUEST_REFRESH_DELAY,
    ZAPTEC_POLL_CHARGER_TRIGGER_DELAYS,
    ZAPTEC_POLL_INSTALLATION_TRIGGER_DELAYS,
)
from .zaptec import Charger, Installation, Zaptec, ZaptecApiError, ZaptecBase, has_write_role

if TYPE_CHECKING:
    from .manager import ZaptecConfigEntry, ZaptecManager

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class ZaptecUpdateOptions:
    """Options for the Zaptec update coordinator."""

    name: str
    update_interval: int
    charging_update_interval: int | None
    tracked_devices: set[str]
    poll_args: dict[str, bool]
    zaptec_object: ZaptecBase | None


class ZaptecUpdateCoordinator(DataUpdateCoordinator[None]):
    """Coordinator for Zaptec data updates."""

    # Override types for the base class
    config_entry: ZaptecConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        entry: ZaptecConfigEntry,
        manager: ZaptecManager,
        options: ZaptecUpdateOptions,
    ) -> None:
        """Initialize account-wide Zaptec data updater."""
        self.manager: ZaptecManager = manager
        self.options: ZaptecUpdateOptions = options
        self.zaptec: Zaptec = manager.zaptec
        self._trigger_task: asyncio.Task | None = None
        self._default_update_interval = timedelta(seconds=options.update_interval)
        self._charging_update_interval = (
            timedelta(seconds=options.charging_update_interval)
            if options.charging_update_interval is not None
            else None
        )

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}-{options.name.lower()}",
            update_interval=self._default_update_interval,
            request_refresh_debouncer=Debouncer(
                hass,
                _LOGGER,
                cooldown=REQUEST_REFRESH_DELAY,
                immediate=False,
            ),
        )

        # Install the listener to select the update interval on the state
        # of the charger. This only works with a Charger object.
        if options.charging_update_interval is not None:
            if not isinstance(options.zaptec_object, Charger):
                raise ValueError("Charging update interval requires a Charger object")
            self.async_add_listener(self.set_update_interval)

    def set_update_interval(self) -> None:
        """Set the update interval for the coordinator.

        This function is called on data updates from the coordinator.
        """
        zaptec_obj: Charger = self.options.zaptec_object  # type: ignore[assignment]
        current = self.update_interval
        want = (
            self._charging_update_interval
            if zaptec_obj.is_charging()
            else self._default_update_interval
        )
        if current != want:
            _LOGGER.debug(
                "%s is now %s, setting update interval to %s (was %s)",
                zaptec_obj.qual_id,
                zaptec_obj.get("ChargerOperationMode", "Unknown"),
                want,
                current,
            )
            self.update_interval = want
            self._schedule_refresh()

    async def _async_update_data(self) -> None:
        """Poll data from Zaptec."""

        try:
            _LOGGER.debug("--- Polling %s from Zaptec", self.options.name)
            await self.zaptec.poll(
                self.options.tracked_devices,
                **self.options.poll_args,
            )
        except ZaptecApiError as err:
            _LOGGER.exception("Fetching data failed")
            raise UpdateFailed(err) from err

        if isinstance(self.options.zaptec_object, Installation):
            self._check_installation_role(self.options.zaptec_object)

    def _check_installation_role(self, installation: Installation) -> None:
        """Create or clear the Repair issues for insufficient write access.

        `installation/update` requires the Owner or Service role
        (https://docs.zaptec.com/reference/api_installation_id_update_post),
        as do `chargers/{id}/update` and `chargers/{id}/SendCommand/{id}`.
        Roles are per object, so an account can be Owner on the installation
        and User on one of its chargers; the two issues are mutually
        exclusive, at most one per installation. If CurrentUserRoles hasn't
        been observed yet, leave any existing issue alone rather than guessing.

        Deliberately calling async_create_issue() again every poll (rather
        than only on the first observation) is safe and intentional: HA's
        issue registry replaces the existing IssueEntry in place and does not
        touch dismissed_version, so a user who has clicked "Ignore" on this
        issue in Settings > Repairs stays ignored across every subsequent
        poll as long as the role doesn't change. Only deleting the issue
        (role becomes sufficient) and later recreating it (role becomes
        insufficient again) resets that dismissal -- which is intentional,
        since a real role change deserves fresh attention.
        """
        roles = installation.get("current_user_roles")
        if roles is None:
            return

        name = str(installation.get("name", installation.qual_id))
        issue_id = f"insufficient_role_{installation.id}"
        charger_issue_id = f"insufficient_charger_role_{installation.id}"

        if has_write_role(roles) is False:
            # The installation-level warning covers the account's access to this
            # installation; naming individual chargers on top of it would only
            # repeat the same remedy.
            ir.async_delete_issue(self.hass, DOMAIN, charger_issue_id)
            self._create_role_issue(
                issue_id,
                "insufficient_role",
                {"installation_name": name, "role": roles or "None"},
            )
            return

        ir.async_delete_issue(self.hass, DOMAIN, issue_id)

        # Chargers carry their own roles, populated by Charger.poll_info(); one
        # that hasn't been polled yet reports None and is left out rather than
        # assumed restricted.
        restricted = sorted(
            str(charger.get("name", charger.qual_id))
            for charger in installation.chargers
            if has_write_role(charger.get("current_user_roles")) is False
        )
        if not restricted:
            ir.async_delete_issue(self.hass, DOMAIN, charger_issue_id)
            return

        self._create_role_issue(
            charger_issue_id,
            "insufficient_charger_role",
            {"installation_name": name, "chargers": ", ".join(restricted)},
        )

    def _create_role_issue(
        self, issue_id: str, translation_key: str, placeholders: dict[str, str]
    ) -> None:
        """Raise a non-fixable warning pointing at the Zaptec Portal."""
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=translation_key,
            translation_placeholders=placeholders,
            learn_more_url="https://portal.zaptec.com/",
        )

    async def _trigger_poll(self, zaptec_obj: ZaptecBase) -> None:
        """Trigger a poll update sequence for the given object.

        This sequence is useful to ensure that the state is fully synced after a
        HA initiated update.
        """

        children_coordinators: list[ZaptecUpdateCoordinator] = []
        if isinstance(zaptec_obj, Installation):
            delays = ZAPTEC_POLL_INSTALLATION_TRIGGER_DELAYS
            # If the installation has chargers, we also trigger the
            # coordinators for the chargers that are tracked.
            children_coordinators = [
                self.manager.device_coordinators[charger.id]
                for charger in zaptec_obj.chargers
                if charger.id in self.manager.tracked_devices
            ]
        else:
            delays = ZAPTEC_POLL_CHARGER_TRIGGER_DELAYS

        _LOGGER.debug("Triggering poll of %s after %s seconds", zaptec_obj.qual_id, delays)

        # Calculcate the deltas for the delays. E.g. [2, 5, 10] -> [2, 3, 5]
        deltas = [b - a for a, b in zip([0, *delays[:-1]], delays, strict=True)]

        for i, delta in enumerate(deltas, start=1):
            await asyncio.sleep(delta)
            _LOGGER.debug(
                "Triggering poll %s of %s after %s seconds",
                i,
                zaptec_obj.qual_id,
                delta,
            )
            await self.async_refresh()

            # Trigger the poll for the children coordinators in the first run
            if i == 1:
                for coord in children_coordinators:
                    await coord.trigger_poll()

    async def trigger_poll(self) -> None:
        """Trigger a poll update sequence."""

        zaptec_obj = self.options.zaptec_object
        if zaptec_obj is None:
            _LOGGER.debug("No zaptec object to poll, skipping")
            return

        # If there is a curent poll task running, cancel it
        if self._trigger_task is not None:
            _LOGGER.debug(
                "A poll task is already running for %s, cancelling it",
                zaptec_obj.qual_id,
            )
            self._trigger_task.cancel()
            try:
                await self._trigger_task
            except asyncio.CancelledError:
                pass
            finally:
                self._trigger_task = None

        def cleanup_task(_task: asyncio.Task) -> None:
            """Cleanup the task after it has run."""
            self._trigger_task = None

        self._trigger_task = self.config_entry.async_create_background_task(
            self.hass,
            self._trigger_poll(zaptec_obj),
            f"Zaptec Poll Update for {zaptec_obj.qual_id}",
        )
        self._trigger_task.add_done_callback(cleanup_task)
