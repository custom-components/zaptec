# Design: Test coverage for platform entity files

## Problem

`sensor.py`, `switch.py`, `number.py`, `button.py`, `binary_sensor.py`, and
`update.py` are all at 0% coverage (`--cov-branch` run against `tests/` on
current `master`). Each defines one or more `ZaptecBaseEntity` subclasses with
real logic — value transforms, availability overrides, command dispatch to
the Zaptec API with `HomeAssistantError` wrapping — none of which is
exercised by any existing test.

PR #394 (`test/coordinator-entity-coverage`, currently open) brought
`coordinator.py`/`entity.py` from ~27-29% to near-full coverage using
hand-rolled fakes (`hass`, `config_entry` fixtures in `conftest.py`) instead
of `pytest-homeassistant-custom-component`, which doesn't run on native
Windows in this dev environment (see `CLAUDE.md`). This pass extends that
same approach to the platform files.

## Approach

Stack a new branch on top of `test/coordinator-entity-coverage` (PR #394),
reusing its `hass`/`config_entry` fixtures (`conftest.py`) and the
`ZaptecBaseEntity(coordinator, zaptec_obj, entity_description, device_info)`
construction pattern from its `test_entity.py`. This is a stacked PR: it
depends on #394's branch content but does not require #394 to be merged
first — it can be rebased once #394 lands.

One new test file per platform, each with its own `coordinator` fixture
(thin reuse of `hass`/`config_entry`) and a `zaptec_obj` built as
`MagicMock(spec=Charger)` / `MagicMock(spec=Installation)` — not the
`FakeZaptecObj` used for `entity.py`'s tests, because these entities call
real API-shaped methods (`is_command_valid`, `command`,
`set_permanent_cable_lock`, `set_limit_current`, `set_settings`,
`set_hmi_brightness`, `asdict`), which `spec=` lets `MagicMock` validate.
`.get()` is wired to a small backing dict so `_get_zaptec_value()` still
resolves real values through `ZaptecBaseEntity`.

Tests target only real behavior:
- `_update_from_zaptec` (value transforms, icon maps, OCMF max-of-two logic)
- `_post_init` overrides
- overridden `available` properties
- command methods (`async_turn_on/off`, `async_press`,
  `async_set_native_value`, `async_install`): success path (underlying
  `zaptec_obj` method called, `trigger_poll()` awaited) and failure path
  (exception from the API call → wrapped and re-raised as
  `HomeAssistantError`)

Out of scope: the `INSTALLATION_ENTITIES`/`CHARGER_ENTITIES` description-list
constants and `async_setup_entry()` — both declarative/thin-delegation code,
same call PR #394 made for `entity.py`'s equivalent thin wrappers.

## Per-file test surface

- **`sensor.py`**: `ZaptecSensor._update_from_zaptec`;
  `ZaptecSensorTranslate._post_init` (lower-cases `entity_description.options`)
  and lower-cased value; `ZaptecChargeSensor` icon map (a known mode and the
  `"unknown"` fallback); `ZaptecEnengySensor` OCMF max-of-two-readings logic
  (meter-only, meter+session where session wins, non-dict `completed_session`
  logs and defaults session reading to `0.0`).
- **`switch.py`**: `ZaptecSwitch._update_from_zaptec`;
  `ZaptecChargeSwitch.available` (checks `is_command_valid` with the correct
  command name in both on/off states) plus `async_turn_on`/`async_turn_off`
  success and `HomeAssistantError`-wrapped failure; `ZaptecCableLockSwitch`
  `async_turn_on`/`async_turn_off` success and failure.
- **`number.py`**: `ZaptecNumber._update_from_zaptec`;
  `ZaptecAvailableCurrentNumber._post_init` (`native_max_value` sourced from
  `zaptec_obj.get("MaxCurrent", 32)`) plus `async_set_native_value` success and
  failure; `ZaptecThreeToOnePhaseSwitchCurrent.async_set_native_value` success
  and failure; `ZaptecSettingNumber` missing-`setting`
  `HomeAssistantError` guard plus success and failure with `setting` present;
  `ZaptecHmiBrightness` ×100 read-scaling and ÷100 write-scaling plus success
  and failure.
- **`button.py`**: `ZaptecButton.available` (delegates to
  `zaptec_obj.is_command_valid(self.key)`); `async_press` success and
  `HomeAssistantError`-wrapped failure.
- **`binary_sensor.py`**: `ZaptecBinarySensor._update_from_zaptec`;
  `ZaptecBinarySensorWithAttrs._post_init` (`extra_state_attributes` from
  `zaptec_obj.asdict()`, `unique_id` overridden to `zaptec_obj.id`).
- **`update.py`**: `ZaptecUpdate._update_from_zaptec` (both
  `firmware_current_version`/`firmware_available_version` keys populate
  `_attr_installed_version`/`_attr_latest_version`); `async_install` success
  and `HomeAssistantError`-wrapped failure.

## Testing

Each behavior gets both a success-path test and, where the method wraps a
`zaptec_obj` call in `try`/`except Exception as exc: raise
HomeAssistantError(...) from exc`, a failure-path test asserting
`HomeAssistantError` is raised (and, where practical, that `trigger_poll()`
is *not* called on the failure path, matching the source's `await
self.trigger_poll()` placement after the `try`/`except`). All async command
tests use `pytest-asyncio`'s existing `asyncio_mode = "auto"` config — no new
test dependency.

Verification command per task and for the full pass:
```bash
SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests -q
SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest --cov=./custom_components/zaptec --cov-branch --cov-report=term-missing tests -q
```
Lint gate (must stay clean for new/touched files):
```bash
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format custom_components tests --diff
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check custom_components tests
```

## Task/commit structure

One task per platform file (6 tasks total: sensor, switch, number, button,
binary_sensor, update), each: write the test file → run it → lint → commit.
Same granularity PR #394 used, so work stays easy to review and pause
between commits (per `CLAUDE.md`: never commit without explicit approval).

## Scope

Six new test files (`tests/test_sensor.py`, `test_switch.py`,
`test_number.py`, `test_button.py`, `test_binary_sensor.py`,
`test_update.py`). No production code changes. No new test dependencies.
Branches from / stacks on `test/coordinator-entity-coverage` (PR #394).
