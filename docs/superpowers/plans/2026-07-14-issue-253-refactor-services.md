# Issue #253 Refactor services.py Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Register zaptec's services once at Home Assistant startup (`async_setup`) instead of once per config entry (`async_setup_entry`), per HA's [action-setup quality-scale rule](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/action-setup/), while fixing the multi-account bug this uncovered: today a service call only ever resolves devices against whichever config entry's manager happened to register services first.

**Architecture:** `custom_components/zaptec/services.py` currently defines `iter_objects` and all eight `service_handle_*` coroutines as closures nested inside `async_setup_services(hass, manager)`, captured over one specific `ZaptecManager`. They move to module level. `iter_objects` stops taking a `manager` argument; instead it reads `service_call.hass` (a real attribute already carried by every `ServiceCall`) and resolves the target uid against **every currently loaded zaptec config entry** via `hass.config_entries.async_entries(DOMAIN)` + `entry.runtime_data`, so a second (or third) Zaptec account's chargers are reachable too. `async_setup_services(hass)` drops the `manager` parameter entirely and moves from `async_setup_entry` to a new module-level `async_setup(hass, config)` in `__init__.py`, called once regardless of how many config entries exist. `async_unload_services` is deleted — HA never unloads a domain's services just because one config entry unloads, and nothing else in the codebase calls it.

**Tech Stack:** Python 3.14, Home Assistant `homeassistant.config_entries`/`service`/`device_registry`/`entity_registry`, `voluptuous`, pytest + `pytest-asyncio` + `unittest.mock`.

## Background (already confirmed during analysis — do not re-derive)

- Upstream issue: https://github.com/custom-components/zaptec/issues/253 ("Refactor services.py"), OPEN, filed by steinmn, 2025-07-27. Body: move `async_setup_services` call from `async_setup_entry` to `async_setup`; move the nested function definitions out of `async_setup_services`; investigate whether `iter_objects` belongs on `ZaptecManager`; remove `async_unload_services`; prerequisite is good test coverage before refactoring.
- `custom_components/zaptec/services.py:119` already carries `# noqa: C901 Too complex, will be fixed in .../issues/253` on `async_setup_services` — this refactor is expected to make that noqa unnecessary (verify with `ruff check` in Task 3, not by assuming).
- Test coverage prerequisite is already satisfied: `tests/test_services.py` has 44 passing tests covering every handler, every `iter_objects` error path, schema validation, and services.yaml/registered-name consistency (verified 2026-07-14).
- No PR currently references or closes #253 (checked GitHub search + issue timeline cross-references) — it's unclaimed.
- Confirmed bug driving the design here: `ZaptecManager` is per-config-entry (`entry.runtime_data = manager`, set at `__init__.py:205`); there is no `hass.data[DOMAIN]` registry anywhere in the integration (grepped, zero hits). `async_setup_services(hass, manager)` closes over one entry's `manager`. Registration is guarded by `hass.services.has_service(DOMAIN, name)`, so on a second config entry's setup the guard is already true and registration is skipped — the handlers stay bound to the *first* entry's manager forever. A user with two Zaptec accounts configured can never target the second account's chargers via `zaptec.*` services. This has not been reported upstream as far as I could find.
- `ServiceCall` (`homeassistant/core.py:2444`) carries `.hass` as a real constructor-set attribute, not something added at call time — safe to rely on inside `iter_objects` and every handler.
- `ConfigEntry.runtime_data` (`homeassistant/config_entries.py:398`) has no default; HA core itself checks presence via `hasattr(self, "runtime_data")` before touching it (see `config_entries.py:1014`, on unload) — that is the correct, HA-idiomatic way to detect an entry that is not currently loaded, and what this plan's `_iter_managers` uses.
- `Zaptec` (`custom_components/zaptec/zaptec/api.py:805`) is `Mapping[str, ZaptecBase]`, so `manager.zaptec.get(uid)` keeps working exactly as it does today; no change needed there.
- Issue #253 explicitly asks to "investigate if `iter_objects` can be simplified and/or makes more sense as a function inside `ZaptecManager`." Decision: no — once services register globally instead of per entry, `iter_objects` has to search *across* every loaded entry's manager, so it can no longer be a method on any single `ZaptecManager` instance without that instance reaching outside itself to its siblings (which nothing else in the codebase does, and would be a bigger, riskier change than this issue asks for). It stays a module-level function in `services.py`, but the "simplify" half of the ask is satisfied: it drops from a closure nested 3 levels deep to a plain top-level function with an explicit `service_call` argument.
- This repo's `hass` test fixture (`tests/conftest.py:48`) is a bare `MagicMock` with only `.loop`/`.is_stopping` pinned — `hass.config_entries.async_entries(...)` needs to be explicitly stubbed per test via `hass.config_entries.async_entries.return_value = [...]`. Fake config entries should be built with `types.SimpleNamespace(runtime_data=manager)` (already the pattern this test file uses for fake device/entity registry entries), **not** a bare `MagicMock()`, because a bare `MagicMock` auto-creates any attribute you access — `hasattr(MagicMock(), "runtime_data")` is always `True`, which would silently defeat the "skip entries without runtime_data" test case.

## Global Constraints

- Run tests with: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests -q`
- Format/lint gates that must be clean before this is done (see `CLAUDE.md`):
  - `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format custom_components tests --diff`
  - `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check --exclude custom_components/zaptec/zaptec/api.py` (this one gates CI; the plain `ruff check custom_components tests` with `--exit-zero` semantics does not, and pre-existing errors confined to `zaptec/api.py` are out of scope)
- Work happens on a dedicated branch, not `master`. Branch name: `fix/issue-253-refactor-services`.
- Never commit automatically without explicit up-front approval for the run's commit policy — this plan's steps stage and commit locally as part of the task cycle (per this repo's usual TDD flow), but do not push or open a PR without explicit approval.
- Do not touch unrelated files. In particular, do not touch `services.yaml` (service names/fields are unchanged) or any platform file (`sensor.py`, `switch.py`, etc.) — they don't reference services.py.

---

### Task 0: Create the working branch

**Files:** none (git only)

- [ ] **Step 1: Confirm a clean working tree and branch from master**

```bash
git status
git checkout master
git pull
git checkout -b fix/issue-253-refactor-services
```

Expected: new branch created, working tree clean.

---

### Task 1: Rewrite services.py — module-level functions, multi-account resolution

This is the core change. It both satisfies the refactor ask (no more nested closures) and fixes the multi-account bug, since the two are inseparable: you cannot pull `iter_objects` out of the closure without deciding what it resolves against, and "every loaded config entry" is the correct answer.

**Files:**
- Modify: `custom_components/zaptec/services.py` (whole file below `_LOGGER = logging.getLogger(__name__)`, i.e. everything from `TServiceHandler = ...` onward)
- Test: `tests/test_services.py`

**Interfaces:**
- Produces (module-level, in `custom_components/zaptec/services.py`):
  - `iter_objects(service_call: ServiceCall, mustbe: type[T]) -> Generator[tuple[ZaptecUpdateCoordinator, T]]` — same name and call signature every existing call site already uses (`iter_objects(service_call, Charger)`, `iter_objects(service_call, mustbe=Charger)`, etc.), so the eight handler bodies below need zero changes to their `iter_objects(...)` call lines.
  - `service_handle_stop_charging`, `service_handle_resume_charging`, `service_handle_authorize_charging`, `service_handle_deauthorize_charging`, `service_handle_restart_charger`, `service_handle_upgrade_firmware`, `service_handle_limit_current`, `service_handle_send_command` — each `async def handler(service_call: ServiceCall) -> None`, module level, bodies unchanged from today except they no longer close over `iter_objects`/`hass`/`manager` (they already only ever called module-scope-reachable `iter_objects` and `_LOGGER`, so the bodies are copy-paste identical).
  - `async_setup_services(hass: HomeAssistant) -> None` — same name, **manager parameter removed**.
- Consumes: nothing new from other tasks. `ZaptecManager` is only referenced under `if TYPE_CHECKING` (already imported that way today), so there is no new circular-import risk.

- [ ] **Step 1: Update the fixtures and add two new regression tests to `tests/test_services.py` (red)**

Replace the `handlers` fixture (currently at `tests/test_services.py:110-115`) with:

```python
@pytest.fixture
async def handlers(hass: MagicMock, manager: MagicMock) -> dict[str, Any]:
    """Register zaptec services and return {name: handler} for direct invocation."""
    hass.config_entries.async_entries.return_value = [SimpleNamespace(runtime_data=manager)]
    hass.services.has_service = MagicMock(return_value=False)
    await async_setup_services(hass)
    return {call.args[1]: call.args[2] for call in hass.services.async_register.call_args_list}
```

Update the two setup-only tests (`tests/test_services.py:123-155`) to drop the now-removed `manager` argument:

```python
async def test_async_setup_services_registers_all_services(hass: MagicMock) -> None:
    """All eight zaptec services get registered under the zaptec domain."""
    hass.services.has_service = MagicMock(return_value=False)

    await async_setup_services(hass)

    registered = {call.args[1] for call in hass.services.async_register.call_args_list}
    assert registered == {
        "stop_charging",
        "resume_charging",
        "authorize_charging",
        "deauthorize_charging",
        "restart_charger",
        "upgrade_firmware",
        "limit_current",
        "send_command",
    }
    assert all(call.args[0] == DOMAIN for call in hass.services.async_register.call_args_list)


async def test_async_setup_services_skips_already_registered(hass: MagicMock) -> None:
    """A service that has_service reports as already present is not re-registered."""
    hass.services.has_service = MagicMock(
        side_effect=lambda _domain, name: name == "stop_charging"
    )

    await async_setup_services(hass)

    registered = {call.args[1] for call in hass.services.async_register.call_args_list}
    assert "stop_charging" not in registered
    assert "resume_charging" in registered
```

Delete `test_async_unload_services_removes_all_domain_services` (`tests/test_services.py:158-170`) entirely — `async_unload_services` is being removed.

Update the import block (`tests/test_services.py:18-24`) to drop `async_unload_services`:

```python
from custom_components.zaptec.services import (
    CHARGER_ID_SCHEMA,
    LIMIT_CURRENT_SCHEMA,
    SEND_COMMAND_SCHEMA,
    async_setup_services,
)
```

Update `test_services_yaml_keys_match_registered_service_names` (`tests/test_services.py:671-685`) to drop the removed `manager` argument:

```python
async def test_services_yaml_keys_match_registered_service_names(hass: MagicMock) -> None:
    """services.yaml documents exactly the services async_setup_services registers.

    A key mismatch here (e.g. a typo) means HA's UI silently falls back to an
    undocumented, field-less form for the real service, while the yaml entry
    documents a service that doesn't exist.
    """
    hass.services.has_service = MagicMock(return_value=False)
    await async_setup_services(hass)
    registered = {call.args[1] for call in hass.services.async_register.call_args_list}

    documented = set(yaml.safe_load(SERVICES_YAML_PATH.read_text()))

    assert documented == registered
```

Append two new tests to the "iter_objects resolution / error paths" section (after `test_multiple_chargers_in_one_call_are_all_processed`, i.e. after line 332 in the current file):

```python
async def test_resolves_across_multiple_config_entries(
    hass: MagicMock, add_charger: Any, handlers: dict[str, Any]
) -> None:
    """A service call resolves a charger that lives under a second config entry's manager.

    Regression test for the bug this refactor fixes: services now register
    once at HA startup instead of once per config entry, so iter_objects must
    search every loaded entry's manager, not just the one that happened to
    exist when async_setup_services first ran.
    """
    charger1, _coordinator1 = add_charger("charger1")

    other_manager = MagicMock()
    other_charger = make_charger("charger2")
    other_coordinator = MagicMock()
    other_coordinator.trigger_poll = AsyncMock()
    other_manager.zaptec = {"charger2": other_charger}
    other_manager.device_coordinators = {"charger2": other_coordinator}

    hass.config_entries.async_entries.return_value.append(
        SimpleNamespace(runtime_data=other_manager)
    )

    await handlers["stop_charging"](make_call(hass, {"charger_id": "charger2"}))

    other_charger.command.assert_awaited_once_with("stop_charging_final")
    other_coordinator.trigger_poll.assert_awaited_once()
    charger1.command.assert_not_awaited()


async def test_unloaded_config_entry_without_runtime_data_is_skipped(
    hass: MagicMock, handlers: dict[str, Any]
) -> None:
    """An entry with no runtime_data (not currently loaded) is skipped, not crashed on."""
    hass.config_entries.async_entries.return_value.append(SimpleNamespace())

    with pytest.raises(HomeAssistantError, match="Unable to find zaptec object"):
        await handlers["stop_charging"](make_call(hass, {"charger_id": "charger_missing"}))
```

- [ ] **Step 2: Run the test file to confirm it now fails**

```bash
SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_services.py -q
```

Expected: failures — `async_setup_services()` still requires a positional `manager` argument (`TypeError: async_setup_services() missing 1 required positional argument: 'manager'`) and `async_unload_services` is still importable but unused by the new test file (import error only if you also removed it from services.py already — at this point services.py is untouched, so the failures should be purely `TypeError`s from the dropped `manager` argument across most tests). This confirms the test changes exercise the not-yet-written new signature.

- [ ] **Step 3: Rewrite `custom_components/zaptec/services.py`**

Replace everything from `TServiceHandler = Callable[[ServiceCall], Awaitable[None]]` (current line 24) through the end of the file (current line 344) with:

```python
TServiceHandler = Callable[[ServiceCall], Awaitable[None]]
T = TypeVar("T")

CHARGER_ID_SCHEMA = vol.Schema(
    vol.All(
        vol.Schema(
            {
                vol.Required(
                    vol.Any("charger_id", "device_id", "entity_id"),
                    msg=(
                        "At leas one of 'charger_id', 'device_id' or "
                        "'entity_id' must be specified"
                    ),
                ): object,
            },
            extra=vol.ALLOW_EXTRA,
        ),
        vol.Schema(
            {
                vol.Optional("charger_id"): str,
                vol.Optional("device_id"): vol.All(cv.ensure_list, [str]),
                vol.Optional("entity_id"): vol.All(cv.ensure_list, [str]),
            }
        ),
    )
)

LIMIT_CURRENT_SCHEMA = vol.Schema(
    vol.All(
        vol.Schema(
            {
                vol.Required(
                    vol.Any("installation_id", "device_id", "entity_id"),
                    msg=(
                        "At least one of 'installation_id', 'device_id' or "
                        "'entity_id' must be specified"
                    ),
                ): object,
            },
            extra=vol.ALLOW_EXTRA,
        ),
        vol.Any(
            vol.Schema(
                {
                    vol.Optional("installation_id"): str,
                    vol.Optional("device_id"): vol.All(cv.ensure_list, [str]),
                    vol.Optional("entity_id"): vol.All(cv.ensure_list, [str]),
                    vol.Required("available_current"): int,
                },
            ),
            vol.Schema(
                {
                    vol.Optional("installation_id"): str,
                    vol.Optional("device_id"): vol.All(cv.ensure_list, [str]),
                    vol.Optional("entity_id"): vol.All(cv.ensure_list, [str]),
                    vol.Required("available_current_phase1"): int,
                    vol.Required("available_current_phase2"): int,
                    vol.Required("available_current_phase3"): int,
                },
            ),
            msg=(
                "Either 'available_current' or all three of "
                "'available_current_phase1', 'available_current_phase2' "
                "and 'available_current_phase3' must be set."
            ),
        ),
    )
)

SEND_COMMAND_SCHEMA = vol.Schema(
    vol.All(
        vol.Schema(
            {
                vol.Required(
                    vol.Any("charger_id", "device_id", "entity_id"),
                    msg=(
                        "At leas one of 'charger_id', 'device_id' or "
                        "'entity_id' must be specified"
                    ),
                ): object,
            },
            extra=vol.ALLOW_EXTRA,
        ),
        vol.Schema(
            {
                vol.Optional("charger_id"): str,
                vol.Optional("device_id"): vol.All(cv.ensure_list, [str]),
                vol.Optional("entity_id"): vol.All(cv.ensure_list, [str]),
                vol.Required("command"): vol.Union(str, int),
            }
        ),
    )
)


def _get_as_set(service_call: ServiceCall, key: str) -> set[str]:
    """Return the given service-call field as a set of strings."""
    data = service_call.data.get(key, [])
    if not isinstance(data, list):
        data = [data]
    return set(data)


def _iter_managers(hass: HomeAssistant) -> Generator[ZaptecManager]:
    """Yield the manager for every currently loaded zaptec config entry."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if hasattr(entry, "runtime_data"):
            yield entry.runtime_data


def iter_objects(
    service_call: ServiceCall, mustbe: type[T]
) -> Generator[tuple[ZaptecUpdateCoordinator, T]]:
    """Resolve the devices/entities targeted by a service call to zaptec objects.

    Devices are looked up across every loaded zaptec config entry, not just
    one, so a service call still resolves correctly when multiple Zaptec
    accounts are configured.
    """
    hass = service_call.hass
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    device_ids = _get_as_set(service_call, "device_id")
    lookup: dict[str, str] = {}

    # Parse all entities and find their device ids which is appended to the
    # list of devices.
    for entity_id in _get_as_set(service_call, "entity_id"):
        entity_entry = ent_reg.async_get(entity_id)
        if entity_entry is None:
            raise HomeAssistantError(f"Unable to find entity '{entity_id}'")
        if not entity_entry.device_id:
            raise HomeAssistantError(f"Entity '{entity_id}' doesn't have a device")
        device_ids.add(entity_entry.device_id)
        lookup[entity_entry.device_id] = f"entity '{entity_id}'"

    # Parse all device ids and find the uid for each device
    uids: set[str] = set()
    for device_id in device_ids:
        device_entry = dev_reg.async_get(device_id)
        err_device = lookup.get(device_id, f"device '{device_id}'")
        if device_entry is None:
            raise HomeAssistantError(f"Unable to find device {err_device}")
        err_device = lookup.get(device_id, f"device {device_entry.name}")
        if not device_entry.identifiers:
            raise HomeAssistantError(f"Unable to find identifiers for {err_device}")
        for domain, uid in device_entry.identifiers:
            if domain != DOMAIN:
                raise HomeAssistantError(f"Non-zaptec device specified {err_device}")
            uids.add(uid)
            lookup[uid] = err_device

    # Append any legacy charger_id or installation_id that might be specified
    field = None
    if mustbe is Charger:
        field = "charger_id"
    elif mustbe is Installation:
        field = "installation_id"
    if field:
        uids.update(_get_as_set(service_call, field))

    # Any uid specified at all?
    if not uids:
        suffix = f". Missing field '{field}'" if field else ""
        raise HomeAssistantError(f"No zaptec devices specified{suffix}")

    managers = list(_iter_managers(hass))

    # Loop through every uid and find the object, searching every manager
    for uid in uids:
        # Set the human readable identifier for the error message
        err_device = f"{lookup[uid]} ({uid})" if uid in lookup else f"id {uid}"

        zaptec_object = None
        coordinator = None
        for manager in managers:
            zaptec_object = manager.zaptec.get(uid)
            if zaptec_object is not None:
                coordinator = manager.device_coordinators.get(uid)
                break

        if zaptec_object is None:
            raise HomeAssistantError(f"Unable to find zaptec object for {err_device}")
        if not isinstance(zaptec_object, mustbe):
            raise HomeAssistantError(f"{err_device} is not a {mustbe.__name__}")
        if coordinator is None:
            raise HomeAssistantError(f"{err_device} is not available")

        yield coordinator, zaptec_object


async def service_handle_stop_charging(service_call: ServiceCall) -> None:
    _LOGGER.debug("Called stop charging")
    _LOGGER.warning(
        "The 'stop_charging' action is deprecated and will be removed in a future release. "
        "Use the 'Stop charging' button entity instead"
    )
    for coordinator, obj in iter_objects(service_call, Charger):
        _LOGGER.debug("  >> to %s", obj.id)
        try:
            await obj.command("stop_charging_final")
        except Exception as exc:
            raise HomeAssistantError(f"Command 'stop_charging_final' failed: {exc}") from exc
        await coordinator.trigger_poll()


async def service_handle_resume_charging(service_call: ServiceCall) -> None:
    _LOGGER.debug("Called resume charging")
    _LOGGER.warning(
        "The 'resume_charging' action is deprecated and will be removed in a future release. "
        "Use the 'Resume charging' button entity instead"
    )
    for coordinator, obj in iter_objects(service_call, mustbe=Charger):
        _LOGGER.debug("  >> to %s", obj.id)
        try:
            await obj.command("resume_charging")
        except Exception as exc:
            raise HomeAssistantError(f"Command 'resume_charging' failed: {exc}") from exc
        await coordinator.trigger_poll()


async def service_handle_authorize_charging(service_call: ServiceCall) -> None:
    _LOGGER.debug("Called authorize charging")
    _LOGGER.warning(
        "The 'authorize_charging' action is deprecated and will be removed in a future "
        "release. Use the 'Authorize charging' button entity instead"
    )
    for coordinator, obj in iter_objects(service_call, mustbe=Charger):
        _LOGGER.debug("  >> to %s", obj.id)
        try:
            await obj.authorize_charge()
        except Exception as exc:
            raise HomeAssistantError(f"Command 'authorize_charge' failed: {exc}") from exc
        await coordinator.trigger_poll()


async def service_handle_deauthorize_charging(service_call: ServiceCall) -> None:
    _LOGGER.debug("Called deauthorize charging and stop")
    _LOGGER.warning(
        "The 'deauthorize_charging' action is deprecated and will be removed in a future "
        "release. Use the 'Deauthorize charging' button entity instead"
    )
    for coordinator, obj in iter_objects(service_call, mustbe=Charger):
        _LOGGER.debug("  >> to %s", obj.id)
        try:
            await obj.command("deauthorize_and_stop")
        except Exception as exc:
            raise HomeAssistantError(f"Command 'deauthorize_and_stop' failed: {exc}") from exc
        await coordinator.trigger_poll()


async def service_handle_restart_charger(service_call: ServiceCall) -> None:
    _LOGGER.debug("Called restart charger")
    _LOGGER.warning(
        "The 'restart_charger' action is deprecated and will be removed in a future release. "
        "Use the 'Restart charger' button entity instead"
    )
    for coordinator, obj in iter_objects(service_call, mustbe=Charger):
        _LOGGER.debug("  >> to %s", obj.id)
        try:
            await obj.command("restart_charger")
        except Exception as exc:
            raise HomeAssistantError(f"Command 'restart_charger' failed: {exc}") from exc
        await coordinator.trigger_poll()


async def service_handle_upgrade_firmware(service_call: ServiceCall) -> None:
    _LOGGER.debug("Called update firmware")
    _LOGGER.warning(
        "The 'upgrade_firmware' action is deprecated and will be removed in a future "
        "release. Use the 'Upgrade firmware' button entity instead"
    )
    for coordinator, obj in iter_objects(service_call, mustbe=Charger):
        _LOGGER.debug("  >> to %s", obj.id)
        try:
            await obj.command("upgrade_firmware")
        except Exception as exc:
            raise HomeAssistantError(f"Command 'upgrade_firmware' failed: {exc}") from exc
        await coordinator.trigger_poll()


async def service_handle_limit_current(service_call: ServiceCall) -> None:
    _LOGGER.debug("Called set current limit")
    limit_args = {}
    # only add the relevant arguments if they are not None
    if (available_current := service_call.data.get("available_current")) is not None:
        limit_args["availableCurrent"] = available_current
    if (
        available_current_phase1 := service_call.data.get("available_current_phase1")
    ) is not None:
        limit_args["availableCurrentPhase1"] = available_current_phase1
    if (
        available_current_phase2 := service_call.data.get("available_current_phase2")
    ) is not None:
        limit_args["availableCurrentPhase2"] = available_current_phase2
    if (
        available_current_phase3 := service_call.data.get("available_current_phase3")
    ) is not None:
        limit_args["availableCurrentPhase3"] = available_current_phase3
    for coordinator, obj in iter_objects(service_call, mustbe=Installation):
        _LOGGER.debug("  >> to %s", obj.id)
        try:
            await obj.set_limit_current(**limit_args)
        except Exception as exc:
            raise HomeAssistantError(f"Limit current failed: {exc}") from exc
        await coordinator.trigger_poll()


async def service_handle_send_command(service_call: ServiceCall) -> None:
    _LOGGER.debug("Called send command")
    for coordinator, obj in iter_objects(service_call, mustbe=Charger):
        _LOGGER.debug("  >> to %s", obj.id)
        command = service_call.data.get("command")
        if command is None:
            raise HomeAssistantError("No Command received")
        try:
            await obj.command(command)
        except Exception as exc:
            raise HomeAssistantError(f"Command '{command}' failed: {exc}") from exc
        await coordinator.trigger_poll()


async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up services for zaptec."""
    services: list[tuple[str, vol.Schema, TServiceHandler]] = [
        ("stop_charging", CHARGER_ID_SCHEMA, service_handle_stop_charging),
        ("resume_charging", CHARGER_ID_SCHEMA, service_handle_resume_charging),
        ("authorize_charging", CHARGER_ID_SCHEMA, service_handle_authorize_charging),
        (
            "deauthorize_charging",
            CHARGER_ID_SCHEMA,
            service_handle_deauthorize_charging,
        ),
        ("restart_charger", CHARGER_ID_SCHEMA, service_handle_restart_charger),
        ("upgrade_firmware", CHARGER_ID_SCHEMA, service_handle_upgrade_firmware),
        ("limit_current", LIMIT_CURRENT_SCHEMA, service_handle_limit_current),
        ("send_command", SEND_COMMAND_SCHEMA, service_handle_send_command),
    ]

    # Register the services
    for name, schema, handler in services:
        if not hass.services.has_service(DOMAIN, name):
            hass.services.async_register(DOMAIN, name, handler, schema=schema)
```

Leave the file's header untouched (`"""Zaptec components services."""`, `from __future__ import annotations`, the import block, `_LOGGER = logging.getLogger(__name__)`) — no changes needed there; `Generator` and `TYPE_CHECKING`/`ZaptecManager`/`ZaptecUpdateCoordinator` imports are already present and are still used (by `_iter_managers`, `iter_objects`).

- [ ] **Step 4: Run the test file to confirm it now passes**

```bash
SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_services.py -v
```

Expected: all tests pass, including the two new ones (`test_resolves_across_multiple_config_entries`, `test_unloaded_config_entry_without_runtime_data_is_skipped`).

- [ ] **Step 5: Commit**

```bash
git add custom_components/zaptec/services.py tests/test_services.py
git commit -m "$(cat <<'EOF'
refactor: register zaptec services once, resolve across all config entries

Moves iter_objects and the eight service_handle_* coroutines out of the
closure inside async_setup_services (issue #253) and drops the manager
parameter: iter_objects now reads service_call.hass and searches every
loaded config entry's manager instead of one captured at registration
time. This also fixes a latent bug where a second Zaptec account's
chargers were unreachable via zaptec.* services, since registration was
guarded by hass.services.has_service and so only ever bound to whichever
entry set up services first.
EOF
)"
```

---

### Task 2: Wire `async_setup` in `__init__.py`, remove `async_unload_services`

**Files:**
- Modify: `custom_components/zaptec/__init__.py:27` (import line), `:207-208` (drop the `async_setup_services` call from `async_setup_entry`), `:285` (drop the `async_unload_services` call from `async_unload_entry`)
- Test: `tests/test_init.py`

**Interfaces:**
- Consumes: `async_setup_services(hass: HomeAssistant) -> None` from Task 1.
- Produces: `async_setup(hass: HomeAssistant, _config: ConfigType) -> bool`, module level in `custom_components/zaptec/__init__.py`. HA calls this once at startup for any domain backing a config entry, before `async_setup_entry` runs for any of that domain's entries.

- [ ] **Step 1: Write a failing test for `async_setup`**

Add to `tests/test_init.py` (near the top, alongside the existing imports):

```python
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.zaptec import async_setup
```

Append to the end of `tests/test_init.py`:

```python
async def test_async_setup_registers_services() -> None:
    """async_setup wires up zaptec's services once, independent of any config entry."""
    hass = MagicMock()

    with patch(
        "custom_components.zaptec.async_setup_services", new=AsyncMock()
    ) as mock_setup_services:
        result = await async_setup(hass, {})

    assert result is True
    mock_setup_services.assert_awaited_once_with(hass)
```

- [ ] **Step 2: Run it to confirm it fails**

```bash
SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_init.py::test_async_setup_registers_services -v
```

Expected: FAIL — `ImportError: cannot import name 'async_setup' from 'custom_components.zaptec'`.

- [ ] **Step 3: Add `async_setup` and remove the old call sites**

In `custom_components/zaptec/__init__.py`, change the import line (currently line 27):

```python
from .services import async_setup_services, async_unload_services
```

to:

```python
from .services import async_setup_services
```

Add `ConfigType` to the typing import — insert this import near the other `homeassistant.helpers` imports (after the `from homeassistant.helpers.aiohttp_client import async_get_clientsession` line):

```python
from homeassistant.helpers.typing import ConfigType
```

Add a new `async_setup` function directly above `async def async_setup_entry(` (currently line 72):

```python
async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    """Set up the zaptec integration's services.

    Called once at Home Assistant startup, independent of how many zaptec
    config entries exist, so services are available regardless of which
    (or how many) accounts are configured.
    """
    await async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
```

Remove the service setup call from `async_setup_entry` (currently lines 207-208):

```python
    # Setup services
    await async_setup_services(hass, manager)

    # Setup all platforms
```

becomes:

```python
    # Setup all platforms
```

Remove the unload call from `async_unload_entry` (currently line 285):

```python
    manager = entry.runtime_data
    await manager.cancel_streams()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await async_unload_services(hass)
    return unload_ok
```

becomes:

```python
    manager = entry.runtime_data
    await manager.cancel_streams()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
```

- [ ] **Step 4: Run the new test, then the full suite, to confirm everything passes**

```bash
SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_init.py::test_async_setup_registers_services -v
SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests -q
```

Expected: both green, no other test file references `async_setup_services`/`async_unload_services` (confirmed during analysis — only `tests/test_services.py` did, and Task 1 already updated it).

- [ ] **Step 5: Commit**

```bash
git add custom_components/zaptec/__init__.py tests/test_init.py
git commit -m "$(cat <<'EOF'
refactor: call async_setup_services from async_setup, drop async_unload_services

Per HA's action-setup quality-scale rule, services should be registered
in async_setup (once per HA start) rather than async_setup_entry (once
per config entry). async_unload_services is removed along with its call
site in async_unload_entry: nothing else in the codebase calls it, and
services now live for the lifetime of HA, not any one config entry.

Closes upstream issue #253.
EOF
)"
```

---

### Task 3: Full verification pass

**Files:** none (verification only)

- [ ] **Step 1: Format check**

```bash
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format custom_components tests --diff
```

Expected: no diff. If there is one, run without `--diff` to apply it, review the change, then amend the relevant commit from Task 1 or 2 (whichever file it touched) with a fresh `git add` + `git commit --amend` only if that commit hasn't been superseded — otherwise fold into a new small commit.

- [ ] **Step 2: CI-gating lint check**

```bash
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check --exclude custom_components/zaptec/zaptec/api.py
```

Expected: no errors in `custom_components/zaptec/services.py` or `custom_components/zaptec/__init__.py`. In particular, confirm the `# noqa: C901` this refactor was meant to make unnecessary is gone from `services.py` (it no longer exists after Task 1's rewrite) and that `iter_objects` itself doesn't newly trip C901 now that it's a standalone top-level function — if it does, the fix is to extract the device/entity-id-resolution loop (the block building `uids` from `device_ids`) into its own module-level helper, not to re-add a suppression.

- [ ] **Step 3: Full test suite with coverage**

```bash
SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest --cov=./custom_components/zaptec --cov-branch tests
```

Expected: all tests pass; `services.py` and `__init__.py` coverage should not regress from before this refactor (spot-check the per-file coverage line in the report).

- [ ] **Step 4: Report status**

Summarize: branch name, commits made, lint/test results, and confirmation that the working tree is clean (`git status`). Stop here — do not push or open a PR without explicit approval, per this repo's CLAUDE.md.
