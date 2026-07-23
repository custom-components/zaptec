# Role Write-Gating (#311) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Block every documented Owner/Service-only write call (`installation/update`, `chargers/{id}/update`, `chargers/{id}/SendCommand/{id}`) with a clear, actionable error when the configured Zaptec account lacks that role, and proactively surface a persistent Home Assistant Repair issue for any installation where that's the case — without ever refusing setup or hiding functionality that does work.

**Architecture:** A single role-check helper (`ZaptecBase._require_write_role`, `zaptec/api.py`) is consulted by every write method that hits a documented Owner/Service-only endpoint — two on `Installation`, two on `Charger` — before any of them make an HTTP request. It raises a new `InsufficientRoleError` that already flows through the existing generic exception handling in `number.py`/`services.py` into a `HomeAssistantError` shown to the user. Two `Charger` write calls (`authorize_charge`, `set_hmi_brightness`/`set_permanent_cable_lock` via `localSettings`) hit endpoints that aren't documented anywhere, so they are deliberately left ungated — there's no evidence for what role (if any) they require, and guessing wrong risks a false-positive block on something that actually works. Separately, the per-installation `ZaptecUpdateCoordinator` re-evaluates the same role signal after every poll and creates/clears a non-fixable HA Repair issue (`Settings → Repairs`) so affected users — especially ones who never touch the number entities — are told plainly what's limited and how to fix it in the Zaptec Portal. All of this reads the same already-parsed `current_user_roles` string attribute; nothing here makes a new API call.

**Tech Stack:** Python 3.14, Home Assistant custom integration conventions (`DataUpdateCoordinator`, `homeassistant.helpers.issue_registry`), pytest + pytest-asyncio with the existing `FakeSession`-based offline harness for `zaptec/api.py` and the `MagicMock`-based harness for `coordinator.py`.

## Global Constraints

- Three endpoints are confirmed via `docs.zaptec.com/reference/*.md` to require the Owner or Service role — Service is exposed via the API as the role name `Maintainer` (confirmed empirically):
  - `installation/update` — `api_installation_id_update_post.md`: "Updates installation properties (requires owner or service permissions)."
  - `chargers/{id}/update` — `api_chargers_id_update_post.md`: "Updates charger properties (requires owner or service permissions)."
  - `chargers/{id}/SendCommand/{id}` — `api_chargers_id_sendcommand_commandid_post.md`: "owner or service-level access."
  A role string containing `"Owner"` or `"Maintainer"` means the write is allowed; anything else (including `"User"`, `"None"`, or any other combination without those two) blocks it.
- `chargers/{id}/authorizecharge` (used by `Charger.authorize_charge`, including the `command("authorize_charge")` alias) and `chargers/{id}/localSettings` (used by `Charger.set_hmi_brightness`/`set_permanent_cable_lock`) are **not** in `docs.zaptec.com/reference` at all — `api.py` already comments the former as `# NOTE: Undocumented API call`. Do not gate either of these; there's no evidence for what role they require.
- If `current_user_roles` has never been observed for an object (the attribute is entirely absent, i.e. `.get("current_user_roles")` returns `None`), never block and never create/delete a Repair issue — let the request proceed and the real API's own `403` be the fallback. Do not guess from absence of data.
- Never refuse Home Assistant config-entry setup based on role. Every change in this plan is either a pre-flight check on specific write calls, or a non-fixable, informational Repair issue — nothing here prevents the integration from loading or any existing entity from being created.
- The new exception (`InsufficientRoleError`) must subclass `ZaptecApiError` so it is still caught anywhere that already catches `ZaptecApiError` or bare `Exception`.
- Repair issues: `is_fixable=False`, `severity=IssueSeverity.WARNING`, `translation_key="insufficient_role"`, one issue per affected installation keyed by `f"insufficient_role_{installation.id}"`, cleared automatically the next time that installation's coordinator observes a sufficient role. (Scoped to installations only in this plan — see the note at the end of Task 2 about the charger-level gap this leaves.)
- No entity-visibility or entity-availability changes. `ZaptecAvailableCurrentNumber`/`ZaptecThreeToOnePhaseSwitchCurrent`/`ZaptecSettingNumber`/command-backed buttons keep being created exactly as today; only actually invoking a now-gated write call changes behavior for insufficient-role accounts.
- Follow `ruff format`/`ruff check` conventions already enforced in this repo (see `CLAUDE.md`): full docstrings, type annotations, no magic numbers needing extraction here.
- Home Assistant minimum version for this integration is 2025.7 (from `README.md` `# Requirements`) — `homeassistant.helpers.issue_registry` has been stable well before that, no version gating needed.

---

## File Structure

- `custom_components/zaptec/zaptec/exceptions.py` — add `InsufficientRoleError`.
- `custom_components/zaptec/zaptec/__init__.py` — export `InsufficientRoleError`.
- `custom_components/zaptec/zaptec/api.py` — add `ZaptecBase._require_write_role()`; call it from `Installation.set_limit_current()`, `Installation.set_three_to_one_phase_switch_current()`, `Charger.set_settings()`, and `Charger.command()` (only the `SendCommand` path, not the `authorize_charge` alias).
- `tests/zaptec/test_api.py` — tests for the new gating behavior.
- `custom_components/zaptec/coordinator.py` — add `ZaptecUpdateCoordinator._check_installation_role()`, called from `_async_update_data()`.
- `custom_components/zaptec/translations/en.json` — add the `issues.insufficient_role` title/description strings.
- `tests/test_coordinator.py` — tests for Repair issue creation/clearing.
- `README.md` — document the new behavior in `# Requirements` and `# Known issues`.

---

### Task 1: Gate installation and charger write calls behind a role check

**Files:**
- Modify: `custom_components/zaptec/zaptec/exceptions.py`
- Modify: `custom_components/zaptec/zaptec/__init__.py`
- Modify: `custom_components/zaptec/zaptec/api.py:42-49` (imports), `:217-218` (new helper on `ZaptecBase`, right after `state_to_attrs`), `Installation.set_limit_current`/`set_three_to_one_phase_switch_current`, `Charger.command`/`set_settings`
- Test: `tests/zaptec/test_api.py`

**Interfaces:**
- Produces: `InsufficientRoleError(ZaptecApiError)` — importable from both `custom_components.zaptec.zaptec.exceptions` and `custom_components.zaptec.zaptec` (the package `__init__.py` re-export).
- Produces: `ZaptecBase._require_write_role(self, action: str) -> None` — raises `InsufficientRoleError` or returns `None`. Defined once on the shared base class (not duplicated on `Installation`/`Charger`) since both already inherit `.get()` and `.qual_id` from `ZaptecBase` and both carry a `current_user_roles` attribute. Not consumed outside `api.py` in this plan, but Task 2 relies on the same underlying `current_user_roles` string semantics (role string contains `"Owner"` or `"Maintainer"` → allowed).

- [ ] **Step 1: Write the failing tests**

Open `tests/zaptec/test_api.py`. Add `InsufficientRoleError` to the existing exceptions import block (alphabetical, between `AuthenticationError` and `RequestConnectionError`):

```python
from custom_components.zaptec.zaptec.exceptions import (
    AuthenticationError,
    InsufficientRoleError,
    RequestConnectionError,
    RequestDataError,
    RequestError,
    RequestRetryError,
    RequestTimeoutError,
)
```

Then append this block at the end of the file (after the existing `test_set_three_to_one_phase_switch_current` test and its neighbors — just add to the bottom of the file):

```python
# ---------------------------------------------------------------------------
#   Installation write-call role gating (#311)
# ---------------------------------------------------------------------------


@pytest.fixture
def user_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    """Populate ZCONST.UserRoles so CurrentUserRoles ints convert to role names."""
    monkeypatch.setitem(ZCONST, "UserRoles", {"User": 1, "Owner": 2, "Maintainer": 4})


@pytest.mark.asyncio
async def test_set_limit_current_blocked_for_user_only_role(user_roles: None) -> None:
    """A User-only role raises InsufficientRoleError without calling the API."""
    zap, session = _make_zaptec([])
    inst = Installation({"Id": "i1", "CurrentUserRoles": 1}, zap)

    with pytest.raises(InsufficientRoleError, match="Owner or Service"):
        await inst.set_limit_current(availableCurrent=10)
    assert session.calls == []


@pytest.mark.asyncio
async def test_set_limit_current_allowed_for_owner_role(user_roles: None) -> None:
    """An Owner role lets the call through to the API."""
    zap, session = _make_zaptec([FakeResponse(HTTPStatus.OK, json_data={})])
    inst = Installation({"Id": "i1", "CurrentUserRoles": 2}, zap)

    await inst.set_limit_current(availableCurrent=10)

    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_set_limit_current_allowed_for_maintainer_role(user_roles: None) -> None:
    """A Maintainer (Service) role lets the call through to the API."""
    zap, session = _make_zaptec([FakeResponse(HTTPStatus.OK, json_data={})])
    inst = Installation({"Id": "i1", "CurrentUserRoles": 4}, zap)

    await inst.set_limit_current(availableCurrent=10)

    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_set_limit_current_allowed_when_role_unknown() -> None:
    """No CurrentUserRoles observed yet -> fall through, let the API decide."""
    inst, session = _installation_with_session([FakeResponse(HTTPStatus.OK, json_data={})])

    await inst.set_limit_current(availableCurrent=10)

    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_set_three_to_one_phase_switch_current_blocked_for_user_only_role(
    user_roles: None,
) -> None:
    """A User-only role raises InsufficientRoleError without calling the API."""
    zap, session = _make_zaptec([])
    inst = Installation({"Id": "i1", "CurrentUserRoles": 1}, zap)

    with pytest.raises(InsufficientRoleError, match="Owner or Service"):
        await inst.set_three_to_one_phase_switch_current(10)
    assert session.calls == []


@pytest.mark.asyncio
async def test_set_three_to_one_phase_switch_current_allowed_for_owner_role(
    user_roles: None,
) -> None:
    """An Owner role lets the call through to the API."""
    zap, session = _make_zaptec([FakeResponse(HTTPStatus.OK, json_data={})])
    inst = Installation({"Id": "i1", "CurrentUserRoles": 2}, zap)

    await inst.set_three_to_one_phase_switch_current(10)

    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_set_settings_blocked_for_user_only_role(user_roles: None) -> None:
    """A User-only role raises InsufficientRoleError without calling the API."""
    zap, session = _make_zaptec([])
    charger = Charger({"Id": "c1", "CurrentUserRoles": 1}, zap)

    with pytest.raises(InsufficientRoleError, match="Owner or Service"):
        await charger.set_settings({"maxChargeCurrent": 16})
    assert session.calls == []


@pytest.mark.asyncio
async def test_set_settings_allowed_for_owner_role(user_roles: None) -> None:
    """An Owner role lets the call through to the API."""
    zap, session = _make_zaptec([FakeResponse(HTTPStatus.OK, json_data={})])
    charger = Charger({"Id": "c1", "CurrentUserRoles": 2}, zap)

    await charger.set_settings({"maxChargeCurrent": 16})

    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_set_settings_allowed_when_role_unknown() -> None:
    """No CurrentUserRoles observed yet -> fall through, let the API decide."""
    charger, session = _charger_with_session([FakeResponse(HTTPStatus.OK, json_data={})])

    await charger.set_settings({"maxChargeCurrent": 16})

    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_command_blocked_for_user_only_role(
    user_roles: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A User-only role raises InsufficientRoleError without calling the API."""
    monkeypatch.setattr(ZCONST, "commands", {"restart_charger": 102}, raising=False)
    zap, session = _make_zaptec([])
    charger = Charger({"Id": "c1", "CurrentUserRoles": 1}, zap)

    with pytest.raises(InsufficientRoleError, match="Owner or Service"):
        await charger.command("restart_charger")
    assert session.calls == []


@pytest.mark.asyncio
async def test_command_allowed_for_maintainer_role(
    user_roles: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Maintainer (Service) role lets the call through to the API."""
    monkeypatch.setattr(ZCONST, "commands", {"restart_charger": 102}, raising=False)
    zap, session = _make_zaptec([FakeResponse(HTTPStatus.OK, json_data={})])
    charger = Charger({"Id": "c1", "CurrentUserRoles": 4}, zap)

    await charger.command("restart_charger")

    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_command_allowed_when_role_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """No CurrentUserRoles observed yet -> fall through, let the API decide."""
    monkeypatch.setattr(ZCONST, "commands", {"restart_charger": 102}, raising=False)
    charger, session = _charger_with_session([FakeResponse(HTTPStatus.OK, json_data={})])

    await charger.command("restart_charger")

    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_command_authorize_charge_alias_not_gated(user_roles: None) -> None:
    """The undocumented authorize_charge alias is never role-gated, even for User-only."""
    zap, session = _make_zaptec([FakeResponse(HTTPStatus.OK, json_data={})])
    charger = Charger({"Id": "c1", "CurrentUserRoles": 1}, zap)

    await charger.command("authorize_charge")

    method, url, _ = session.calls[-1]
    assert method == "post"
    assert url.endswith("chargers/c1/authorizecharge")


@pytest.mark.asyncio
async def test_authorize_charge_not_gated_for_user_only_role(user_roles: None) -> None:
    """authorize_charge is undocumented and deliberately not role-gated."""
    zap, session = _make_zaptec([FakeResponse(HTTPStatus.OK, json_data={})])
    charger = Charger({"Id": "c1", "CurrentUserRoles": 1}, zap)

    await charger.authorize_charge()

    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_set_hmi_brightness_not_gated_for_user_only_role(user_roles: None) -> None:
    """set_hmi_brightness (localSettings) is undocumented and deliberately not role-gated."""
    zap, session = _make_zaptec([FakeResponse(HTTPStatus.OK, json_data={})])
    charger = Charger({"Id": "c1", "CurrentUserRoles": 1}, zap)

    await charger.set_hmi_brightness(0.5)

    assert len(session.calls) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/zaptec/test_api.py -k "role" -v`
Expected: `ImportError` (or collection error) because `InsufficientRoleError` doesn't exist yet in `exceptions.py`.

- [ ] **Step 3: Add `InsufficientRoleError` to `exceptions.py`**

In `custom_components/zaptec/zaptec/exceptions.py`, add a new class right after `AuthenticationError`:

```python
class AuthenticationError(ZaptecApiError):
    """Authenatication failed."""


class InsufficientRoleError(ZaptecApiError):
    """The current Zaptec user's role does not permit this action."""
```

- [ ] **Step 4: Export it from the package `__init__.py`**

In `custom_components/zaptec/zaptec/__init__.py`, update the exceptions import and `__all__`:

```python
from .exceptions import (
    AuthenticationError,
    InsufficientRoleError,
    RequestConnectionError,
    RequestDataError,
    RequestError,
    RequestRetryError,
    RequestTimeoutError,
    ZaptecApiError,
)
```

```python
__all__ = [
    "MISSING",
    "RETRYABLE_HTTP_STATUSES",
    "ZCONST",
    "AuthenticationError",
    "Charger",
    "InsufficientRoleError",
    "Installation",
    "Missing",
    "Redactor",
    "RequestConnectionError",
    "RequestDataError",
    "RequestError",
    "RequestRetryError",
    "RequestTimeoutError",
    "Zaptec",
    "ZaptecApiError",
    "ZaptecBase",
    "get_ocmf_max_reader_value",
]
```

- [ ] **Step 5: Add the role check to `api.py` and wire it into all four gated write methods**

In `custom_components/zaptec/zaptec/api.py`, update the exceptions import (currently lines 42-49) to add `InsufficientRoleError`:

```python
from .exceptions import (
    AuthenticationError,
    InsufficientRoleError,
    RequestConnectionError,
    RequestDataError,
    RequestError,
    RequestRetryError,
    RequestTimeoutError,
)
```

Then, in the shared `ZaptecBase` class, immediately after the `state_to_attrs` static method ends (currently line 217, right before `class Installation(ZaptecBase):` starts at line 220), add the new helper method. Putting it on `ZaptecBase` rather than duplicating it on `Installation` and `Charger` means both subclasses can call `self._require_write_role(...)` directly:

```python
    def _require_write_role(self, action: str) -> None:
        """Raise InsufficientRoleError if the current user lacks write access.

        `installation/update`, `chargers/{id}/update`, and
        `chargers/{id}/SendCommand/{id}` all require the Owner or Service
        (Maintainer) role (confirmed individually via docs.zaptec.com/reference
        for each of the three endpoints). If CurrentUserRoles hasn't been
        observed yet, let the request proceed and rely on the API's own 403
        response instead of guessing.

        `chargers/{id}/authorizecharge` and `chargers/{id}/localSettings` are
        deliberately not gated by any caller of this method -- they aren't
        documented anywhere, so there's no evidence for what role (if any)
        they require.
        """
        roles = self.get("current_user_roles")
        if roles is None or "Owner" in roles or "Maintainer" in roles:
            return
        raise InsufficientRoleError(
            f"{action} requires the Owner or Service role on {self.qual_id} "
            f"(current role: {roles or 'None'}). Grant Owner or Service access "
            "to this Zaptec object in the Zaptec Portal to enable this."
        )
```

Then call it as the first line of `Installation.set_limit_current`:

```python
    async def set_limit_current(self, **kwargs: Any) -> Any:
        """Set current limit for the installation.

        Set a limit now how many amps the installation can use
        Use availableCurrent for setting all phases at once. Use
        availableCurrentPhase* to set each phase individually.
        """
        self._require_write_role("Setting the installation current limit")

        has_availablecurrent = kwargs.get("availableCurrent") is not None
```

(the rest of the method body is unchanged — only the new first line is added before the existing `has_availablecurrent = ...` line).

`Installation.set_three_to_one_phase_switch_current` becomes:

```python
    async def set_three_to_one_phase_switch_current(self, current: float) -> Any:
        """Set the 3 to 1-phase switch current."""
        self._require_write_role("Setting the 3-to-1 phase switch current")
        if not (0 <= current <= DEFAULT_MAX_CURRENT):
            raise ValueError(f"Current must be between 0 and {DEFAULT_MAX_CURRENT:.0f} amps")
        return await self.zaptec.request(
            f"installation/{self.id}/update",
            method="post",
            data={"threeToOnePhaseSwitchCurrent": current},
        )
```

`Charger.set_settings` becomes:

```python
    async def set_settings(self, settings: dict[str, Any]) -> Any:
        """Set settings on the charger."""

        self._require_write_role("Setting charger parameters")

        if any(key not in ZCONST.update_params for key in settings):
            raise ValueError(f"Unknown setting '{settings}'")

        _LOGGER.debug("Settings %s", settings)
        return await self.zaptec.request(
            f"chargers/{self.id}/update", method="post", data=settings
        )
```

`Charger.command` gets the check right before the `SendCommand` request, *after* the `authorize_charge` short-circuit and the command-validity checks (so the undocumented `authorize_charge` path stays ungated, and an unknown/invalid command still raises its existing, more specific `ValueError` before role is even considered):

```python
    async def command(self, command: str | int | CommandType) -> Any:
        """Send a command to the charger.

        Any command or command id can be used. Zaptec supports a number of
        commands, which is found https://api.zaptec.com/help/index.html
        under CommandId shema. The most used commands are:

        - deauthorize_and_stop: Deauthorize the charger and stop it
        - restart_charger: Restart the charger
        - resume_charging: Resume charging
        - stop_charging_final: Stop charging and set final stop
        - upgrade_firmware: Upgrade the firmware

        Special commands which is special to this implementation:
        - authorize_charge: Authorize the charger to charge
        """

        if command in ("authorize_charge", "AuthorizeCharge"):
            return await self.authorize_charge()

        # Look up the command and its command id
        if isinstance(command, int):
            # If int, look up the command name
            cmdid = command
            command = ZCONST.commands.get(cmdid)
        else:
            # Support using the CommandName as a string
            cmdid = ZCONST.commands.get(to_under(command))

        # Make sure we have a valid command
        if not cmdid or not command:
            raise ValueError(f"Unknown command {command!r}")

        # Check that we can run the command at this time
        self.is_command_valid(command, raise_value_error_if_invalid=True)

        self._require_write_role(f"Sending the {command} command")

        _LOGGER.debug("Command %s (%s)", command, cmdid)
        return await self.zaptec.request(f"chargers/{self.id}/SendCommand/{cmdid}", method="post")
```

(only the new `self._require_write_role(f"Sending the {command} command")` line, right before the `_LOGGER.debug` line, is added — the rest of the method is unchanged).

- [ ] **Step 6: Run tests to verify they pass**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/zaptec/test_api.py -v`
Expected: PASS (all tests in the file, including every pre-existing test that builds `Installation`/`Charger` objects with no `CurrentUserRoles` key — e.g. `test_set_limit_current_*`, `test_command_posts_to_send_command_url`, `test_set_settings_valid`, `test_authorize_charge_posts` — since those all hit the "role unknown" fall-through path and are therefore unaffected).

- [ ] **Step 7: Lint**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format custom_components/zaptec/zaptec tests/zaptec --diff`
Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check custom_components/zaptec/zaptec/exceptions.py custom_components/zaptec/zaptec/__init__.py custom_components/zaptec/zaptec/api.py tests/zaptec/test_api.py`
Expected: no new errors introduced by this task (pre-existing `api.py` errors are a known baseline — see `CLAUDE.md`).

- [ ] **Step 8: Commit**

```bash
git add custom_components/zaptec/zaptec/exceptions.py custom_components/zaptec/zaptec/__init__.py custom_components/zaptec/zaptec/api.py tests/zaptec/test_api.py
git commit -m "fix: block Owner/Service-only writes when the account lacks that role"
```

---

### Task 2: Repair issue for installations with insufficient role

**Files:**
- Modify: `custom_components/zaptec/coordinator.py:1-26` (imports), `:111-122` (`_async_update_data`)
- Modify: `custom_components/zaptec/translations/en.json:195-196` (add `issues` key)
- Test: `tests/test_coordinator.py`

**Interfaces:**
- Consumes: `Installation.get("current_user_roles")` — same string semantics as Task 1 (`None` = unknown/skip, otherwise a string that may contain `"Owner"`/`"Maintainer"`/`"User"`/`"None"`/combinations).
- Produces: `ZaptecUpdateCoordinator._check_installation_role(self, installation: Installation) -> None` — called automatically from `_async_update_data()` for any coordinator whose `options.zaptec_object` is an `Installation`; not consumed elsewhere in this plan.
- Produces: HA Repair issue `f"insufficient_role_{installation.id}"` in domain `zaptec`, translation_key `"insufficient_role"`.

- [ ] **Step 1: Write the failing tests**

Open `tests/test_coordinator.py`. Add `from unittest.mock import patch` is already imported (`from unittest.mock import AsyncMock, MagicMock, patch`). Append this block at the end of the file:

```python
# ---------------------------------------------------------------------------
#   Insufficient-role Repair issue (#311)
# ---------------------------------------------------------------------------


async def test_async_update_data_creates_repair_issue_for_insufficient_role(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    """A User-only installation gets a Repair issue created after a poll."""
    manager.zaptec.poll = AsyncMock()
    installation = MagicMock(spec=Installation)
    installation.id = "inst1"
    installation.qual_id = "Installation[inst1]"
    installation.get.side_effect = lambda key, default=None: {  # noqa: ARG005
        "current_user_roles": "User",
        "name": "Home",
    }.get(key, default)
    options = make_options(zaptec_object=installation)
    coordinator = ZaptecUpdateCoordinator(
        hass, entry=config_entry, manager=manager, options=options
    )

    with patch("custom_components.zaptec.coordinator.ir") as mock_ir:
        await coordinator._async_update_data()  # noqa: SLF001

    mock_ir.async_create_issue.assert_called_once_with(
        hass,
        DOMAIN,
        "insufficient_role_inst1",
        is_fixable=False,
        severity=mock_ir.IssueSeverity.WARNING,
        translation_key="insufficient_role",
        translation_placeholders={"installation_name": "Home", "role": "User"},
        learn_more_url="https://portal.zaptec.com/",
    )
    mock_ir.async_delete_issue.assert_not_called()


async def test_async_update_data_never_deletes_issue_while_role_stays_insufficient(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    """Repeated polls with an unchanged User-only role never call async_delete_issue.

    This is a regression guard for the "don't nag aware users" requirement:
    HA's issue registry preserves a user's "Ignore" dismissal across repeat
    async_create_issue() calls for the same issue_id, but a delete+recreate
    cycle would reset it. As long as the role doesn't change, this code must
    never delete the issue between polls.
    """
    manager.zaptec.poll = AsyncMock()
    installation = MagicMock(spec=Installation)
    installation.id = "inst1"
    installation.get.side_effect = lambda key, default=None: {  # noqa: ARG005
        "current_user_roles": "User",
        "name": "Home",
    }.get(key, default)
    options = make_options(zaptec_object=installation)
    coordinator = ZaptecUpdateCoordinator(
        hass, entry=config_entry, manager=manager, options=options
    )

    with patch("custom_components.zaptec.coordinator.ir") as mock_ir:
        await coordinator._async_update_data()  # noqa: SLF001
        await coordinator._async_update_data()  # noqa: SLF001
        await coordinator._async_update_data()  # noqa: SLF001

    assert mock_ir.async_create_issue.call_count == 3  # noqa: PLR2004
    mock_ir.async_delete_issue.assert_not_called()


async def test_async_update_data_clears_repair_issue_for_owner_role(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    """An Owner-role installation deletes any existing Repair issue."""
    manager.zaptec.poll = AsyncMock()
    installation = MagicMock(spec=Installation)
    installation.id = "inst1"
    installation.get.side_effect = lambda key, default=None: {  # noqa: ARG005
        "current_user_roles": "Owner",
    }.get(key, default)
    options = make_options(zaptec_object=installation)
    coordinator = ZaptecUpdateCoordinator(
        hass, entry=config_entry, manager=manager, options=options
    )

    with patch("custom_components.zaptec.coordinator.ir") as mock_ir:
        await coordinator._async_update_data()  # noqa: SLF001

    mock_ir.async_delete_issue.assert_called_once_with(hass, DOMAIN, "insufficient_role_inst1")
    mock_ir.async_create_issue.assert_not_called()


async def test_async_update_data_skips_role_check_when_role_unknown(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    """No CurrentUserRoles observed yet -> neither create nor delete an issue."""
    manager.zaptec.poll = AsyncMock()
    installation = MagicMock(spec=Installation)
    installation.id = "inst1"
    installation.get.return_value = None
    options = make_options(zaptec_object=installation)
    coordinator = ZaptecUpdateCoordinator(
        hass, entry=config_entry, manager=manager, options=options
    )

    with patch("custom_components.zaptec.coordinator.ir") as mock_ir:
        await coordinator._async_update_data()  # noqa: SLF001

    mock_ir.async_create_issue.assert_not_called()
    mock_ir.async_delete_issue.assert_not_called()


async def test_async_update_data_skips_role_check_for_non_installation(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    """Charger/account-wide coordinators never run the installation role check."""
    manager.zaptec.poll = AsyncMock()
    charger = MagicMock(spec=Charger)
    options = make_options(zaptec_object=charger)
    coordinator = ZaptecUpdateCoordinator(
        hass, entry=config_entry, manager=manager, options=options
    )

    with patch.object(coordinator, "_check_installation_role") as mock_check:
        await coordinator._async_update_data()  # noqa: SLF001

    mock_check.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_coordinator.py -k "repair_issue or role_check" -v`
Expected: `AttributeError`/`AssertionError` — `coordinator.py` has no `ir` symbol and never calls `_check_installation_role`.

- [ ] **Step 3: Add the role check to `coordinator.py`**

In `custom_components/zaptec/coordinator.py`, update the imports (currently lines 11-21):

```python
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
from .zaptec import Charger, Installation, Zaptec, ZaptecApiError, ZaptecBase
```

Then update `_async_update_data` (currently lines 111-122) to run the check after a successful poll, and add the new helper method right after it:

```python
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
        """Create or clear a Repair issue for insufficient write access.

        `installation/update` requires the Owner or Service role
        (https://docs.zaptec.com/reference/api_installation_id_update_post).
        If CurrentUserRoles hasn't been observed yet, leave any existing issue
        alone rather than guessing.

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

        issue_id = f"insufficient_role_{installation.id}"
        if "Owner" in roles or "Maintainer" in roles:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return

        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="insufficient_role",
            translation_placeholders={
                "installation_name": str(installation.get("name", installation.qual_id)),
                "role": roles or "None",
            },
            learn_more_url="https://portal.zaptec.com/",
        )
```

- [ ] **Step 4: Add the Repair issue translation strings**

In `custom_components/zaptec/translations/en.json`, the file currently ends with:

```json
        "update": {
            "firmware_update": {
                "name": "Firmware update"
            }
        }
    }
}
```

Change the final two lines (closing the `entity` key and the root object) to add a new top-level `issues` key:

```json
        "update": {
            "firmware_update": {
                "name": "Firmware update"
            }
        }
    },
    "issues": {
        "insufficient_role": {
            "title": "Limited access to {installation_name}",
            "description": "The Zaptec account used by this integration only has the following role(s) on installation \"{installation_name}\": {role}. Changing the available current or the 3-to-1 phase switch current requires the Owner or Service role.\n\nTo enable these controls, grant Owner or Service access for this installation to this account in the [Zaptec Portal](https://portal.zaptec.com/)."
        }
    }
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_coordinator.py -v`
Expected: PASS (all tests in the file, including all pre-existing ones — none of them set `zaptec_object` to a real `MagicMock(spec=Installation)` with a `current_user_roles`-returning `.get`, so the new code path only activates in the four new tests).

- [ ] **Step 6: Validate the JSON**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -c "import json; json.load(open('custom_components/zaptec/translations/en.json', encoding='utf-8')); print('OK')"`
Expected: `OK`

- [ ] **Step 7: Lint**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format custom_components/zaptec tests --diff`
Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check custom_components/zaptec/coordinator.py tests/test_coordinator.py`
Expected: no new errors.

- [ ] **Step 8: Commit**

```bash
git add custom_components/zaptec/coordinator.py custom_components/zaptec/translations/en.json tests/test_coordinator.py
git commit -m "feat: raise a Repair issue for installations with insufficient Zaptec role"
```

**Deliberate scope boundary (decided, not a gap to fix):** Task 1 now also gates `Charger.set_settings` and `Charger.command`, but this Repair issue stays Installation-scoped only — it does not check the role reported on individual `Charger` objects, and that's intentional. Role has been confirmed to vary per-charger under one login (see the `zaptec-role-testing` memory and the #357 thread, e.g. "I have one as Owner, and one as User"), but that's evidence of *deliberate* per-charger access configuration (fleet/multi-tenant management), not user confusion — proactively firing a Repair issue per restricted charger would mostly nag the exact users who already know what they set up. The Repair issue's job is catching accounts whose *whole* installation turns out unexpectedly restricted; the per-charger case is already served proportionately by Task 1's reactive `InsufficientRoleError` at the moment someone actually tries a blocked charger control.

---

### Task 3: Document the new behavior in README

**Files:**
- Modify: `README.md:40-63` (`# Requirements` and `# Known issues` sections)

**Interfaces:**
- Consumes: nothing from earlier tasks (documentation only).

- [ ] **Step 1: Update the `# Requirements` section**

In `README.md`, the section currently reads:

```markdown
# Requirements

* Home Assistant 2025.7 or newer.
* A user with access to [Zaptec Portal](https://portal.zaptec.com/).
* If you want to use the controls at the [Installation](#zaptec-device-concept) level:
  * A Zaptec Portal user with _Owner_ or _Service_ privileges.
  * Disable [Zaptec Sense](https://help.zaptec.com/hc/en-GB/article/how-to-manage-zaptec-sense-in-the-zaptec-portal) (aka APM/Automatic Power Management).
  * Disable [stand-alone mode](https://help.zaptec.com/hc/en-GB/article/use-stand-alone-mode-for-troubleshooting-and-unstable-internet).
```

Add a note directly after that bullet list (before the `# Known issues` heading):

```markdown
# Requirements

* Home Assistant 2025.7 or newer.
* A user with access to [Zaptec Portal](https://portal.zaptec.com/).
* If you want to use the controls at the [Installation](#zaptec-device-concept) level:
  * A Zaptec Portal user with _Owner_ or _Service_ privileges.
  * Disable [Zaptec Sense](https://help.zaptec.com/hc/en-GB/article/how-to-manage-zaptec-sense-in-the-zaptec-portal) (aka APM/Automatic Power Management).
  * Disable [stand-alone mode](https://help.zaptec.com/hc/en-GB/article/use-stand-alone-mode-for-troubleshooting-and-unstable-internet).

> [!NOTE]
> If the configured account only has the _User_ role on an installation, the
> integration still sets up and works normally for everything that doesn't
> need Owner/Service access (see [Known issues](#known-issues)). Trying to
> change the available current or the 3-to-1 phase switch current will fail
> with a clear error instead of a raw HTTP 403, and Home Assistant will show
> a persistent notice under *Settings → Repairs* naming the affected
> installation and the role it needs. If this is expected for your setup,
> you can dismiss it with "Ignore" in the Repairs list — it won't come back
> unless the account's role actually changes.
```

- [ ] **Step 2: Add a bullet to `# Known issues`**

The section currently reads:

```markdown
# Known issues

* Sending a _"deauthorize_and_stop"_ command will give an error. This is due to
  Zaptec sending back error code `500` (internal server error). However, the
  command seems to execute the task, despite the error.
* Setting custom poll intervals, like described
  [here](https://www.home-assistant.io/common-tasks/general/#defining-a-custom-polling-interval),
  will have unexpected effects. If the automatic polling is turned off, not all
  the data in the integration will update properly.
* Using the _Energy Meter_ entity as an input to the Energy Dashboard will give values that are delayed by 1 hour
  in the graphs (see [issue 162](https://github.com/custom-components/zaptec/issues/162) for details).
  There is a plan to solve this in [issue 300](https://github.com/custom-components/zaptec/issues/300), but until that is implemented,
  a workaround is to use the more frequently updated _Session total charge_ entity instead. This reduces the delay-issue,
  but has a separate drawback where a restart of Home Assistant during a charging session can give a fake spike in the logged
  consumption that needs to be manually edited using "Adjust sum" in the Statistics tab of the Developer tools dashboard.
```

Add a new bullet at the end of the list:

```markdown
* A Zaptec Portal user with only the _User_ role (no _Owner_ or _Service_) has
  significantly reduced access: the installation hierarchy, firmware info,
  individual charger detail/state, and the live update stream are all blocked
  by the Zaptec API itself, and this integration additionally blocks changing
  installation-level current limits (see [Requirements](#requirements)).
  Online/offline status and operating mode keep working, since those are
  included in the basic charger list the API returns regardless of role.
```

- [ ] **Step 3: Verify the doc renders sanely**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -c "print(open('README.md', encoding='utf-8').read().count('# Requirements'))"`
Expected: `1` (confirms the section wasn't accidentally duplicated by the edit).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document the User-only role limitations and new Repair issue"
```

---

## Self-Review

**1. Spec coverage:**
- "installation/update requires owner or service privileges" → blocked client-side in Task 1 (`_require_write_role`, called from `Installation.set_limit_current`/`set_three_to_one_phase_switch_current`).
- Broadened scope (agreed after checking `docs.zaptec.com/reference` for every write endpoint `api.py` calls): `chargers/{id}/update` and `chargers/{id}/SendCommand/{id}` carry the identical documented "owner or service" requirement, so Task 1 also gates `Charger.set_settings` and `Charger.command` (excluding the `authorize_charge` alias). `chargers/{id}/authorizecharge` and `chargers/{id}/localSettings` are undocumented, so they're explicitly left ungated — verified by both a positive test (role gating works) and a negative test (`test_authorize_charge_not_gated_for_user_only_role`, `test_set_hmi_brightness_not_gated_for_user_only_role`) per endpoint pair, so a future accidental over-application of the shared helper would be caught.
- "add explanation of this requirement to readme" → Task 3.
- "add explanation of this requirement to ... error log/popup" → Task 1's `InsufficientRoleError` message flows unchanged through the existing `except Exception as exc: raise HomeAssistantError(...) from exc` wrapping already present in `number.py` (`ZaptecAvailableCurrentNumber`/`ZaptecThreeToOnePhaseSwitchCurrent`), `services.py` (`service_handle_limit_current`, `service_handle_restart_charger`, `service_handle_resume_charging`, `service_handle_stop_charging_final`, `service_handle_deauthorize_charging`, `service_handle_send_command`), and `button.py`'s command-triggering entities (all route through `Charger.command()`) — no changes needed there, verified by reading the call sites.
- steinmn's live-testing comment on #311 (hierarchy/firmware/charger-detail/state all blocked for User-only) → documented honestly in Task 3's `# Known issues` bullet, matching this session's own live-testing matrix (memory: `zaptec-role-testing`).
- User's explicit design requirement from this conversation ("not fully block User-role accounts, but if we still create the integration, users must really understand what they must change") → Task 2's Repair issue is the proactive, hard-to-miss mechanism; Task 1's error message is the reactive one for anyone who does try the blocked controls.
- User's follow-up requirement ("warn unaware users, but don't nag users for whom this is intentional") → verified against the installed Home Assistant source (`IssueRegistry.async_get_or_create`): repeat `async_create_issue()` calls for the same `issue_id` do not touch `dismissed_version`, so a user who clicks "Ignore" in Settings → Repairs stays silenced across every later poll as long as role doesn't change. Documented in `_check_installation_role`'s docstring, locked in by `test_async_update_data_never_deletes_issue_while_role_stays_insufficient` (Task 2), and surfaced to users in the Task 3 README note.
- Known, explicitly-flagged gap (not silently dropped): Task 2's Repair issue remains Installation-scoped even though Task 1 now also gates per-charger writes. Called out at the end of Task 2 rather than either silently expanding scope or silently leaving a coverage hole undocumented.

**2. Placeholder scan:** No TBD/TODO/"add appropriate" placeholders — every step shows the literal code or command to run.

**3. Type consistency:** `ZaptecBase._require_write_role(self, action: str) -> None` (Task 1, defined once on the shared base and inherited by both `Installation` and `Charger`) and `ZaptecUpdateCoordinator._check_installation_role(self, installation: Installation) -> None` (Task 2) both read `.get("current_user_roles")` the same way and apply the same `"Owner" in roles or "Maintainer" in roles` test — verified consistent between the two tasks. `InsufficientRoleError` is defined once in Task 1 and only referenced (never redefined) afterward.
