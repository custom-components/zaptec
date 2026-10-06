"""Tests for custom_components.zaptec.manager."""

import asyncio
from collections.abc import Callable
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.zaptec import manager as manager_module
from custom_components.zaptec.manager import (
    STREAM_RECONNECT_INIT_DELAY,
    STREAM_RECONNECT_MAX_DELAY,
    STREAM_RECONNECT_STABLE_TIME,
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
async def test_stream_supervisor_resets_backoff_after_stable_connection(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A connection that stayed up past the stable time counts as a fresh outage.

    Verified via logging: a failure treated as a new outage warns again (see
    test_stream_supervisor_logs_warning_once_then_debug for the same-outage
    case, which stays at DEBUG).
    """
    install = _fake_install()
    now = 0.0
    # Fail, then hold a connection past the stable time before failing again.
    attempts = iter([False, True])

    async def stream_main(*, on_connect: Callable[[], None], **kwargs: object) -> None:
        nonlocal now
        connects = next(attempts, None)
        if connects is None:
            return  # permanent stop, ends the supervisor loop
        if connects:
            on_connect()
            now += STREAM_RECONNECT_STABLE_TIME + 1
        raise ConnectionError("boom")

    install.stream_main.side_effect = stream_main
    sleep_mock = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep_mock)
    monkeypatch.setattr(manager_module.time, "monotonic", lambda: now)
    monkeypatch.setattr(manager_module.random, "normalvariate", lambda mu, sigma: mu)  # noqa: ARG005

    with caplog.at_level(logging.DEBUG, logger="custom_components.zaptec.manager"):
        await _stream_supervisor(install, cb=AsyncMock(), ssl_context=None)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2  # noqa: PLR2004  # both failures counted as separate outages
    (last_delay,), _ = sleep_mock.await_args_list[-1]
    assert last_delay == pytest.approx(STREAM_RECONNECT_INIT_DELAY)  # backoff restarted too


@pytest.mark.asyncio
async def test_stream_supervisor_logs_reconnect_count_on_connect(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Reconnecting logs how many attempts it took; the first connect stays quiet."""
    install = _fake_install()
    outcomes = iter([ConnectionError("1"), ConnectionError("2")])

    async def stream_main(*, on_connect: Callable[[], None], **kwargs: object) -> None:
        outcome = next(outcomes, None)
        if isinstance(outcome, Exception):
            raise outcome
        on_connect()

    install.stream_main.side_effect = stream_main
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    with caplog.at_level(logging.DEBUG, logger="custom_components.zaptec.manager"):
        await _stream_supervisor(install, cb=AsyncMock(), ssl_context=None)

    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(infos) == 1
    assert "after 2 reconnect attempt(s)" in infos[0].getMessage()


@pytest.mark.asyncio
async def test_stream_supervisor_logs_traceback_only_for_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An expected disconnect warns without a traceback; anything else keeps one."""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    for error, has_traceback in ((ConnectionError("boom"), False), (ValueError("boom"), True)):
        install = _fake_install()
        install.stream_main.side_effect = [error, None]
        caplog.clear()

        with caplog.at_level(logging.DEBUG, logger="custom_components.zaptec.manager"):
            await _stream_supervisor(install, cb=AsyncMock(), ssl_context=None)

        (warning,) = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert bool(warning.exc_info) is has_traceback


@pytest.mark.asyncio
async def test_stream_supervisor_stays_quiet_when_the_first_connect_succeeds(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Connecting without ever having failed reports no reconnect count."""
    install = _fake_install()

    async def stream_main(*, on_connect: Callable[[], None], **kwargs: object) -> None:
        on_connect()

    install.stream_main.side_effect = stream_main
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    with caplog.at_level(logging.DEBUG, logger="custom_components.zaptec.manager"):
        await _stream_supervisor(install, cb=AsyncMock(), ssl_context=None)

    assert not [r for r in caplog.records if r.levelno == logging.INFO]


@pytest.mark.asyncio
async def test_stream_supervisor_does_not_reset_when_the_retry_never_connects(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The uptime of an earlier connection must not carry over to a later attempt."""
    install = _fake_install()
    now = 0.0
    # Connect and stay up past the stable time, then fail without connecting at all.
    attempts = iter([True, False])

    async def stream_main(*, on_connect: Callable[[], None], **kwargs: object) -> None:
        nonlocal now
        connects = next(attempts, None)
        if connects is None:
            return  # permanent stop, ends the supervisor loop
        if connects:
            on_connect()
        now += STREAM_RECONNECT_STABLE_TIME + 1
        raise ConnectionError("boom")

    install.stream_main.side_effect = stream_main
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(manager_module.time, "monotonic", lambda: now)

    with caplog.at_level(logging.DEBUG, logger="custom_components.zaptec.manager"):
        await _stream_supervisor(install, cb=AsyncMock(), ssl_context=None)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1  # the second failure never connected, so it is the same outage
