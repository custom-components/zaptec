# Stream Reconnect on Transient Failure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Zaptec live-update stream (`Installation.stream_main()`) automatically reconnect with exponential backoff after a transient connection failure (e.g. a home internet outage), instead of permanently dying until the user reloads Home Assistant.

**Architecture:** `stream_main()` stops swallowing its own exceptions, so a transient failure now propagates to its caller instead of returning silently. A new module-level `_stream_supervisor()` coroutine in `manager.py` becomes the actual background-task body (replacing the direct `stream_main()` call): it calls `stream_main()` in a loop, retries with exponential backoff+jitter on any raised exception, and stops for good if `stream_main()` ever returns normally (its existing signal for "no permission to the stream", HTTP 403).

**Tech Stack:** Python 3.14, Home Assistant custom integration, `asyncio`, `azure-servicebus` (vendored stream client), `pytest` + `pytest-asyncio` (plain pytest harness, no `pytest-homeassistant-custom-component` yet in this repo).

## Global Constraints

- Follow `ruff format` / `ruff check` (repo uses `select = ["ALL"]` in `.ruff.toml`) — run both before considering a task done.
- Tests run via: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest <path> -p no:homeassistant -q` — the `-p no:homeassistant` flag is required on this Windows dev setup (avoids an `fcntl`-dependent pytest plugin that doesn't exist on Windows); never add a compatibility shim instead, just pass the flag.
- Never commit without the user's explicit go-ahead (existing session convention — ask before every `git commit`).
- `asyncio.CancelledError` is a `BaseException`, not an `Exception`, on the Python version this repo targets — `except Exception` blocks must not (and currently do not) catch it. Don't add a bare `except:` or `except BaseException` anywhere in this work.

---

### Task 1: Stop `stream_main()` swallowing its own exceptions

**Files:**
- Modify: `custom_components/zaptec/zaptec/api.py:397` and `:474-475` (the `try:`/`except Exception:` wrapping `Installation.stream_main()`'s body)
- Test: `tests/zaptec/test_api.py`

**Interfaces:**
- Consumes: nothing new — this task only changes existing `Installation.stream_main()` control flow.
- Produces: `Installation.stream_main()` now propagates any exception raised while fetching stream connection details or consuming the stream, instead of catching it and returning `None`. The existing 403/Forbidden case is unchanged — it still logs a warning and returns `None` (this is the "permanent stop, don't retry" signal Task 2's supervisor relies on).

Current code (for reference — do not copy verbatim, this is what you're changing):

```python
        try:
            self._stream_running = True

            # Get connection details
            try:
                conf = await self.live_stream_connection_details()
            except RequestError as err:
                if err.error_code != HTTPStatus.FORBIDDEN:
                    raise
                _LOGGER.warning(
                    "Failed to get live stream info. "
                    "Check if user have access in the zaptec portal"
                )
                return

            # ... (connection setup and the `async for msg in receiver:` loop, unchanged) ...

        except Exception:
            # Do this in order to show the error in the log.
            _LOGGER.exception("Stream failed")
        finally:
            self._stream_receiver = None
            self._stream_running = False
            _LOGGER.info("Servicebus stream stopped for %s", self.qual_id)
```

- [ ] **Step 1: Write the failing tests**

Add to the bottom of `tests/zaptec/test_api.py` (after the existing `Installation.stream_update routing` section, i.e. after the `test_stream_update_zero_guid_is_ignored` test):

```python
# ---------------------------------------------------------------------------
#   Installation.stream_main error propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_main_propagates_non_forbidden_error() -> None:
    """A non-403 error fetching stream connection details now propagates.

    Previously this was swallowed internally (logged, then stream_main()
    returned None) so a caller had no way to distinguish "transient
    failure, please retry" from "stream ended cleanly". See issue #417.
    """
    inst = Installation({"Id": "inst-1"}, _fake_owner())
    inst.live_stream_connection_details = AsyncMock(  # type: ignore[method-assign]
        side_effect=RequestError("server error", HTTPStatus.BAD_GATEWAY)
    )
    with pytest.raises(RequestError):
        await inst.stream_main()


@pytest.mark.asyncio
async def test_stream_main_forbidden_returns_none() -> None:
    """A 403 fetching stream connection details still returns cleanly.

    This remains the "permanent stop, don't retry" signal the stream
    supervisor (manager.py) relies on.
    """
    inst = Installation({"Id": "inst-1"}, _fake_owner())
    inst.live_stream_connection_details = AsyncMock(  # type: ignore[method-assign]
        side_effect=RequestError("no access", HTTPStatus.FORBIDDEN)
    )
    result = await inst.stream_main()
    assert result is None
```

- [ ] **Step 2: Run tests to verify the first one fails**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/zaptec/test_api.py -k stream_main -p no:homeassistant -v`

Expected: `test_stream_main_propagates_non_forbidden_error` FAILS (no exception raised — it's currently swallowed). `test_stream_main_forbidden_returns_none` PASSES already (existing behavior, unaffected by this task — it's here as a regression guard).

- [ ] **Step 3: Remove the outer `except Exception` in `stream_main()`**

In `custom_components/zaptec/zaptec/api.py`, change:

```python
        except Exception:
            # Do this in order to show the error in the log.
            _LOGGER.exception("Stream failed")
        finally:
```

to:

```python
        finally:
```

(i.e. delete the `except Exception:` block entirely, keeping the `try:` / `finally:` around the same body unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/zaptec/test_api.py -p no:homeassistant -v`

Expected: PASS (full file, to confirm nothing else in `test_api.py` broke).

- [ ] **Step 5: Lint**

Run:
```
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format custom_components/zaptec/zaptec/api.py tests/zaptec/test_api.py --diff
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check custom_components/zaptec/zaptec/api.py tests/zaptec/test_api.py
```
Expected: no diff, no lint errors. If `ruff format --diff` shows a diff, run it without `--diff` to apply, then re-run `ruff check`.

- [ ] **Step 6: Commit**

Ask the user for explicit go-ahead first (per this repo's convention — never commit automatically). Once approved:

```bash
git add custom_components/zaptec/zaptec/api.py tests/zaptec/test_api.py
git commit -m "$(cat <<'EOF'
Let stream_main() propagate transient failures instead of swallowing them

Only the existing 403/Forbidden case still returns cleanly; any other
failure now raises so a caller can distinguish "retry me" from
"permanent stop". Prep for issue #417's reconnect supervisor.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Add reconnect-with-backoff supervisor and wire it into `create_streams()`

**Files:**
- Modify: `custom_components/zaptec/const.py` (add `STREAM_RECONNECT_*` constants)
- Modify: `custom_components/zaptec/manager.py` (add module-level `_stream_supervisor()`, update imports, update `create_streams()`)
- Test: `tests/test_manager.py` (new file)

**Interfaces:**
- Consumes: `Installation.stream_main(cb, ssl_context) -> None` from Task 1 (raises on transient failure, returns `None` on permanent stop or normal completion).
- Produces: `_stream_supervisor(install: Installation, cb: Callable[[dict], Awaitable[None]], ssl_context: ssl.SSLContext | None) -> None` — a module-level coroutine in `manager.py` (not a `ZaptecManager` method, so it's testable without constructing a full manager/config-entry). `create_streams()` now schedules `_stream_supervisor(...)` as the background task instead of calling `install.stream_main(...)` directly. No other public interface changes — `cancel_streams()` is untouched and still works because `task.cancel()` interrupts whatever `_stream_supervisor` is currently awaiting (either inside `stream_main()`, or the backoff `asyncio.sleep`).

- [ ] **Step 1: Add the new constants**

In `custom_components/zaptec/const.py`, after the existing `ZAPTEC_POLL_INSTALLATION_TRIGGER_DELAYS` constant (and before `REQUEST_REFRESH_DELAY`), add:

```python
STREAM_RECONNECT_INIT_DELAY = 1.0
"""Initial delay in seconds before the first stream reconnect attempt."""

STREAM_RECONNECT_FACTOR = 2.0
"""Exponential backoff multiplier applied between stream reconnect attempts."""

STREAM_RECONNECT_JITTER = 0.1
"""Relative jitter applied to the stream reconnect backoff delay."""

STREAM_RECONNECT_MAX_DELAY = 300.0
"""Maximum delay in seconds between stream reconnect attempts (5 minutes)."""
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_manager.py`:

```python
"""Tests for custom_components.zaptec.manager."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.zaptec.manager import _stream_supervisor


def _fake_install() -> SimpleNamespace:
    """Return a stand-in for Installation carrying only what _stream_supervisor uses."""
    return SimpleNamespace(qual_id="Installation[nst-1]", stream_main=AsyncMock())


@pytest.mark.asyncio
async def test_stream_supervisor_stops_when_stream_main_returns_normally() -> None:
    """stream_main() returning None (e.g. 403/Forbidden) is a permanent stop."""
    install = _fake_install()
    install.stream_main.return_value = None

    await _stream_supervisor(install, cb=AsyncMock(), ssl_context=None)

    install.stream_main.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_supervisor_retries_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raised exception is retried, not left dead."""
    install = _fake_install()
    install.stream_main.side_effect = [ConnectionError("boom"), None]
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    await _stream_supervisor(install, cb=AsyncMock(), ssl_context=None)

    assert install.stream_main.await_count == 2


@pytest.mark.asyncio
async def test_stream_supervisor_propagates_cancelled_error_without_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task cancellation (integration unload/reload) is not treated as a retryable failure."""
    install = _fake_install()
    install.stream_main.side_effect = asyncio.CancelledError()
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    with pytest.raises(asyncio.CancelledError):
        await _stream_supervisor(install, cb=AsyncMock(), ssl_context=None)

    install.stream_main.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_supervisor_logs_warning_once_then_debug(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Only the first failure of an outage logs at warning; the rest log at debug."""
    install = _fake_install()
    install.stream_main.side_effect = [ConnectionError("1"), ConnectionError("2"), None]
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    with caplog.at_level(logging.DEBUG, logger="custom_components.zaptec.manager"):
        await _stream_supervisor(install, cb=AsyncMock(), ssl_context=None)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(warnings) == 1
    assert len(debugs) == 1


@pytest.mark.asyncio
async def test_stream_supervisor_resets_backoff_after_long_lived_connection(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A connection that outlived the max backoff delay counts as a fresh outage.

    Verified indirectly through logging: each failure that's treated as a
    *new* outage logs at WARNING (the "warned" flag resets alongside the
    delay). If the reset didn't happen, the second failure would log at
    DEBUG instead (see test_stream_supervisor_logs_warning_once_then_debug
    for that same-outage case).
    """
    from custom_components.zaptec import manager as manager_module

    install = _fake_install()
    install.stream_main.side_effect = [ConnectionError("1"), ConnectionError("2"), None]
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(manager_module, "STREAM_RECONNECT_MAX_DELAY", 100.0)
    # 5 monotonic() calls total: connected_at + failure-check for each of the
    # 2 failed attempts, plus connected_at for the 3rd (successful) attempt.
    # The gap between attempt 2's connected_at (0.0) and its failure-check
    # (200.0) exceeds MAX_DELAY (100.0), so that failure counts as a new
    # outage rather than a continuation of the first.
    clock = iter([0.0, 0.0, 0.0, 200.0, 500.0])
    monkeypatch.setattr(manager_module.time, "monotonic", lambda: next(clock))

    with caplog.at_level(logging.DEBUG, logger="custom_components.zaptec.manager"):
        await _stream_supervisor(install, cb=AsyncMock(), ssl_context=None)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2  # both failures counted as separate outages
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_manager.py -p no:homeassistant -v`

Expected: FAIL with `ModuleNotFoundError`/`ImportError` (`_stream_supervisor` doesn't exist yet).

- [ ] **Step 4: Update `manager.py` imports**

Change the import block at the top of `custom_components/zaptec/manager.py` from:

```python
import asyncio
from collections.abc import Iterable
import contextlib
from copy import copy
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityDescription
from homeassistant.util.ssl import get_default_context

from .const import DOMAIN, KEYS_TO_SKIP_ENTITY_AVAILABILITY_CHECK, MANUFACTURER
from .coordinator import ZaptecUpdateCoordinator
from .entity import KeyUnavailableError, ZaptecBaseEntity
from .zaptec import Charger, Installation, Zaptec, ZaptecBase
```

to:

```python
import asyncio
from collections.abc import Awaitable, Callable, Iterable
import contextlib
from copy import copy
from dataclasses import dataclass
import logging
import random
import ssl
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityDescription
from homeassistant.util.ssl import get_default_context

from .const import (
    DOMAIN,
    KEYS_TO_SKIP_ENTITY_AVAILABILITY_CHECK,
    MANUFACTURER,
    STREAM_RECONNECT_FACTOR,
    STREAM_RECONNECT_INIT_DELAY,
    STREAM_RECONNECT_JITTER,
    STREAM_RECONNECT_MAX_DELAY,
)
from .coordinator import ZaptecUpdateCoordinator
from .entity import KeyUnavailableError, ZaptecBaseEntity
from .zaptec import Charger, Installation, Zaptec, ZaptecBase
```

- [ ] **Step 5: Add `_stream_supervisor()`**

In `custom_components/zaptec/manager.py`, add this module-level function right before `class ZaptecManager:`:

```python
async def _stream_supervisor(
    install: Installation,
    cb: Callable[[dict], Awaitable[None]],
    ssl_context: ssl.SSLContext | None,
) -> None:
    """Run install.stream_main(), reconnecting after a transient failure.

    stream_main() returning normally means a permanent stop (e.g. no
    permission to the live stream) -- this loop ends without retrying. It
    raising means a transient failure to retry with exponential backoff.
    asyncio.CancelledError is a BaseException, not an Exception, so it is
    never caught here: cancelling the task (integration unload/reload)
    still stops this immediately, whether currently inside stream_main()
    or in the backoff sleep below.
    """
    delay = STREAM_RECONNECT_INIT_DELAY
    warned = False
    while True:
        connected_at = time.monotonic()
        try:
            await install.stream_main(cb=cb, ssl_context=ssl_context)
            return
        except Exception:
            if time.monotonic() - connected_at >= STREAM_RECONNECT_MAX_DELAY:
                # The previous connection lived long enough to count this
                # as a fresh outage rather than a continuation of the last.
                delay = STREAM_RECONNECT_INIT_DELAY
                warned = False
            if not warned:
                _LOGGER.warning(
                    "Stream for %s disconnected, reconnecting", install.qual_id, exc_info=True
                )
                warned = True
            else:
                _LOGGER.debug(
                    "Stream for %s still reconnecting", install.qual_id, exc_info=True
                )
            await asyncio.sleep(delay)
            delay = delay * STREAM_RECONNECT_FACTOR
            delay = random.normalvariate(delay, delay * STREAM_RECONNECT_JITTER)
            delay = min(delay, STREAM_RECONNECT_MAX_DELAY)
```

- [ ] **Step 6: Wire it into `create_streams()`**

In `custom_components/zaptec/manager.py`, change:

```python
    def create_streams(self) -> None:
        """Create the streams for all installations."""
        for install in self.zaptec.installations:
            if install.id in self.zaptec:
                task = self.config_entry.async_create_background_task(
                    self.hass,
                    install.stream_main(
                        cb=self.stream_callback,
                        ssl_context=get_default_context(),
                    ),
                    name=f"Zaptec Stream for {install.qual_id}",
                )
                self.streams.append((task, install))
```

to:

```python
    def create_streams(self) -> None:
        """Create the streams for all installations."""
        for install in self.zaptec.installations:
            if install.id in self.zaptec:
                task = self.config_entry.async_create_background_task(
                    self.hass,
                    _stream_supervisor(
                        install,
                        cb=self.stream_callback,
                        ssl_context=get_default_context(),
                    ),
                    name=f"Zaptec Stream for {install.qual_id}",
                )
                self.streams.append((task, install))
```

(`cancel_streams()` below it is unchanged — no edit needed there.)

- [ ] **Step 7: Run tests to verify they pass**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_manager.py -p no:homeassistant -v`

Expected: all 5 tests PASS.

- [ ] **Step 8: Lint**

Run:
```
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format custom_components/zaptec/const.py custom_components/zaptec/manager.py tests/test_manager.py --diff
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check custom_components/zaptec/const.py custom_components/zaptec/manager.py tests/test_manager.py
```
Expected: no diff, no lint errors. If `ruff format --diff` shows a diff, run it without `--diff` to apply, then re-run `ruff check`.

- [ ] **Step 9: Commit**

Ask the user for explicit go-ahead first. Once approved:

```bash
git add custom_components/zaptec/const.py custom_components/zaptec/manager.py tests/test_manager.py
git commit -m "$(cat <<'EOF'
Add reconnect-with-backoff supervisor for the live update stream

Wraps stream_main() in an exponential-backoff retry loop so a transient
connection failure (e.g. a brief home-internet outage) reconnects
automatically instead of permanently killing live updates until the
user reloads the integration. Fixes #417.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Full verification

**Files:** none (verification only)

**Interfaces:**
- Consumes: everything from Tasks 1-2.
- Produces: nothing new — confirms the full suite and lint are green together, not just per-file.

- [ ] **Step 1: Run the full test suite**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests -p no:homeassistant -q`

Expected: all tests pass (aside from any pre-existing `SKIP_ZAPTEC_API_TEST`/live-network skips unrelated to this change).

- [ ] **Step 2: Run full-repo lint**

Run:
```
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format . --diff
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check
```
Expected: no diff, no errors. This matches what CI's `lint-ruff` job runs (see `CLAUDE.md`), scoped to the whole repo, not just the files touched here.

- [ ] **Step 3: Report results to the user**

Summarize pass/fail counts and any lint findings. Do not open a PR or push — per this repo's AI-policy convention, that's a separate, explicit ask.

---

## Design doc

Full rationale, alternatives considered, and error-handling table:
`docs/superpowers/specs/2026-07-28-stream-reconnect-design.md`
