"""Tests for custom_components.zaptec.manager."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.zaptec import manager as manager_module
from custom_components.zaptec.manager import (
    STREAM_RECONNECT_INIT_DELAY,
    STREAM_RECONNECT_MAX_DELAY,
    _stream_supervisor,
)


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
    """A raised exception is retried, not left dead, using the initial backoff delay."""
    install = _fake_install()
    install.stream_main.side_effect = [ConnectionError("boom"), None]
    sleep_mock = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep_mock)
    # Neutralize jitter so the first delay is deterministically the init delay.
    monkeypatch.setattr(manager_module.random, "normalvariate", lambda mu, sigma: mu)  # noqa: ARG005

    await _stream_supervisor(install, cb=AsyncMock(), ssl_context=None)

    assert install.stream_main.await_count == 2  # noqa: PLR2004
    sleep_mock.assert_awaited_once()
    (delay,), _ = sleep_mock.await_args
    assert delay == pytest.approx(STREAM_RECONNECT_INIT_DELAY)


@pytest.mark.asyncio
async def test_stream_supervisor_backoff_never_exceeds_max_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Across many consecutive failures, every sleep delay stays within the cap."""
    install = _fake_install()
    install.stream_main.side_effect = [ConnectionError("boom")] * 10 + [None]
    sleep_mock = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep_mock)

    await _stream_supervisor(install, cb=AsyncMock(), ssl_context=None)

    assert install.stream_main.await_count == 11  # noqa: PLR2004
    assert sleep_mock.await_count == 10  # noqa: PLR2004
    for (delay,), _ in sleep_mock.await_args_list:
        assert delay <= STREAM_RECONNECT_MAX_DELAY


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

    Verified via logging: a failure treated as a new outage warns again (see
    test_stream_supervisor_logs_warning_once_then_debug for the same-outage
    case, which stays at DEBUG).
    """
    install = _fake_install()
    install.stream_main.side_effect = [ConnectionError("1"), ConnectionError("2"), None]
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(manager_module, "STREAM_RECONNECT_MAX_DELAY", 100.0)
    # 5 monotonic() calls: connected_at + failure-check per failed attempt (x2),
    # then connected_at for the successful 3rd. Attempt 2's gap (0.0 -> 200.0)
    # exceeds MAX_DELAY (100.0), so it counts as a new outage.
    # Fallback (not bare next(clock)): Windows' ProactorEventLoop calls
    # monotonic() more times during teardown, after the coroutine has returned.
    clock = iter([0.0, 0.0, 0.0, 200.0, 500.0])
    monkeypatch.setattr(manager_module.time, "monotonic", lambda: next(clock, 500.0))

    with caplog.at_level(logging.DEBUG, logger="custom_components.zaptec.manager"):
        await _stream_supervisor(install, cb=AsyncMock(), ssl_context=None)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2  # noqa: PLR2004  # both failures counted as separate outages
