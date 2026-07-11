"""Tests for zaptec/api.py."""

from http import HTTPStatus
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest

from custom_components.zaptec.zaptec.api import Charger, Installation, Zaptec, ZaptecBase
from custom_components.zaptec.zaptec.const import API_RETRIES
from custom_components.zaptec.zaptec.exceptions import (
    AuthenticationError,
    RequestConnectionError,
    RequestDataError,
    RequestError,
    RequestRetryError,
    RequestTimeoutError,
)
from custom_components.zaptec.zaptec.redact import Redactor
from custom_components.zaptec.zaptec.zconst import ZCONST

_LOGGER = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_api(zaptec_username: str, zaptec_password: str) -> None:
    """
    Test the Zaptec API.

    Does not run when testing in Github actions, since it requires login credentials.
    """

    logging.basicConfig(level=logging.DEBUG)
    logging.getLogger("azure").setLevel(logging.WARNING)

    async with Zaptec(zaptec_username, zaptec_password) as zaptec:
        # Builds the interface.
        await zaptec.login()
        await zaptec.build()
        await zaptec.poll(info=True, state=True, firmware=True)

        # Dump redaction database
        _LOGGER.info("Redaction database:")
        _LOGGER.info(zaptec.redact.dumps())

        # Print all the attributes.
        for obj in zaptec.objects():
            _LOGGER.info(obj.asdict())


# ===========================================================================
#   Offline unit tests (no network / no live login required)
# ===========================================================================
#
# These exercise the pure logic and the request/retry machinery using a fake
# aiohttp ClientSession, so they run without credentials or DNS access.


class FakeResponse:
    """Minimal stand-in for aiohttp.ClientResponse."""

    def __init__(
        self,
        status: int,
        *,
        json_data: object = None,
        read_data: bytes = b"",
        text_data: str = "",
        headers: dict | None = None,
    ) -> None:
        """Store the canned response data."""
        self.status = status
        self._json_data = json_data
        self._read_data = read_data
        self._text_data = text_data
        self.headers = headers or {}

    async def json(self, content_type: str | None = None) -> object:
        """Return the canned JSON body, or raise if none was configured."""
        if self._json_data is None:
            raise json.JSONDecodeError("no json body", "", 0)
        return self._json_data

    async def read(self) -> bytes:
        """Return the canned raw body."""
        return self._read_data

    async def text(self) -> str:
        """Return the canned text body."""
        return self._text_data


class _FakeRequestCM:
    """Async context manager returned by FakeSession.request()."""

    def __init__(
        self, *, response: FakeResponse | None = None, exc: BaseException | None = None
    ) -> None:
        """Store the response to yield, or the exception to raise on enter."""
        self._response = response
        self._exc = exc

    async def __aenter__(self) -> FakeResponse:
        """Raise the configured exception, or return the response."""
        if self._exc is not None:
            raise self._exc
        assert self._response is not None
        return self._response

    async def __aexit__(self, *exc_info: object) -> bool:
        """Never suppress exceptions."""
        return False


class FakeSession:
    """Minimal aiohttp.ClientSession stand-in driven by a list of outcomes.

    Each call to request() consumes the next outcome (a FakeResponse to yield
    or an exception to raise); the last outcome repeats for further calls.
    """

    def __init__(self, outcomes: list[FakeResponse | BaseException]) -> None:
        """Store the sequence of per-call outcomes."""
        self._outcomes = outcomes
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, *, method: str, url: str, **kwargs: object) -> _FakeRequestCM:
        """Record the call and return the matching outcome."""
        idx = len(self.calls)
        self.calls.append((method, url, kwargs))
        outcome = self._outcomes[min(idx, len(self._outcomes) - 1)]
        if isinstance(outcome, BaseException):
            return _FakeRequestCM(exc=outcome)
        return _FakeRequestCM(response=outcome)

    async def close(self) -> None:
        """No-op close."""


def _make_zaptec(
    outcomes: list[FakeResponse | BaseException], **kwargs: object
) -> tuple[Zaptec, FakeSession]:
    """Build a Zaptec client backed by a FakeSession, returning both."""
    session = FakeSession(outcomes)
    zap = Zaptec("user", "pass", client=session, redact_logs=False, **kwargs)
    return zap, session


def _fake_owner() -> SimpleNamespace:
    """Return a stand-in for the `zaptec` owner used by set_attributes."""
    return SimpleNamespace(redact=Redactor(do_redact=False), show_all_updates=False)


# ---------------------------------------------------------------------------
#   Zaptec.request status handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_ok_returns_json() -> None:
    """A 200 response returns the decoded JSON payload."""
    payload = {"value": "answer"}
    zap, session = _make_zaptec([FakeResponse(HTTPStatus.OK, json_data=payload)])
    result = await zap.request("unregistered/url")
    assert result == payload
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_request_no_content_returns_bytes() -> None:
    """A 204/201 response returns the raw body bytes."""
    zap, _ = _make_zaptec([FakeResponse(HTTPStatus.NO_CONTENT, read_data=b"done")])
    result = await zap.request("unregistered/url", method="post")
    assert result == b"done"


@pytest.mark.asyncio
async def test_request_invalid_json_raises_data_error() -> None:
    """A 200 with an undecodable body raises RequestDataError."""
    zap, _ = _make_zaptec([FakeResponse(HTTPStatus.OK, json_data=None)])
    with pytest.raises(RequestDataError):
        await zap.request("unregistered/url")


@pytest.mark.asyncio
async def test_request_error_status_raises_with_code() -> None:
    """A non-retryable error status raises RequestError carrying the code."""
    zap, session = _make_zaptec([FakeResponse(HTTPStatus.NOT_FOUND)])
    with pytest.raises(RequestError) as excinfo:
        await zap.request("unregistered/url")
    assert excinfo.value.error_code == HTTPStatus.NOT_FOUND
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_request_500_on_post_raises_immediately() -> None:
    """A 500 on a POST is not retried and raises RequestError immediately."""
    zap, session = _make_zaptec(
        [FakeResponse(HTTPStatus.INTERNAL_SERVER_ERROR, text_data="server error")]
    )
    with pytest.raises(RequestError) as excinfo:
        await zap.request("unregistered/url", method="post")
    assert excinfo.value.error_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_request_500_on_get_retries_then_raises_retry_error() -> None:
    """A persistent 500 on a GET is retried until exhaustion (RequestRetryError)."""
    zap, session = _make_zaptec([FakeResponse(HTTPStatus.INTERNAL_SERVER_ERROR)], max_time=0.001)
    with pytest.raises(RequestRetryError):
        await zap.request("unregistered/url")
    assert len(session.calls) == API_RETRIES


@pytest.mark.asyncio
async def test_request_401_refreshes_token_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 401 triggers a token refresh and the request is retried."""
    payload = {"ok": "yes"}
    zap, _ = _make_zaptec(
        [FakeResponse(HTTPStatus.UNAUTHORIZED), FakeResponse(HTTPStatus.OK, json_data=payload)]
    )
    refresh = AsyncMock()
    monkeypatch.setattr(zap, "_refresh_token", refresh)

    result = await zap.request("unregistered/url")
    # Reaching the 200 payload after a 401 proves the request was retried.
    assert result == payload
    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_request_connection_error_retried_then_raises() -> None:
    """Connection errors are retried and finally raise RequestConnectionError."""
    zap, session = _make_zaptec([aiohttp.ClientConnectionError("boom")], max_time=0.001)
    with pytest.raises(RequestConnectionError):
        await zap.request("unregistered/url")
    assert len(session.calls) == API_RETRIES


@pytest.mark.asyncio
async def test_request_timeout_retried_then_raises() -> None:
    """Timeouts are retried and finally raise RequestTimeoutError."""
    zap, session = _make_zaptec([TimeoutError()], max_time=0.001)
    with pytest.raises(RequestTimeoutError):
        await zap.request("unregistered/url")
    assert len(session.calls) == API_RETRIES


# ---------------------------------------------------------------------------
#   ZaptecBase.state_to_attrs
# ---------------------------------------------------------------------------


def test_state_to_attrs_maps_and_prefers_value() -> None:
    """Values are mapped via keydict; `Value` wins over `ValueAsString`."""
    keydict = {"1": "current", "2": "voltage"}
    data = [
        {"StateId": "1", "ValueAsString": "10"},
        {"StateId": "2", "Value": "230", "ValueAsString": "ignored"},
    ]
    out = ZaptecBase.state_to_attrs(data, "StateId", keydict)
    assert out == {"current": "10", "voltage": "230"}


def test_state_to_attrs_unknown_key_uses_fallback_name() -> None:
    """A StateId missing from keydict falls back to '<key> <id>'."""
    out = ZaptecBase.state_to_attrs([{"StateId": "99", "Value": "x"}], "StateId", {})
    assert out == {"StateId 99": "x"}


def test_state_to_attrs_skips_missing_key_and_missing_value() -> None:
    """Entries without the key, or without any value, are skipped."""
    data = [
        {"NoStateId": "1", "Value": "x"},  # missing key -> skipped
        {"StateId": "1"},  # no Value/ValueAsString -> skipped
    ]
    out = ZaptecBase.state_to_attrs(data, "StateId", {"1": "current"})
    assert out == {}


def test_state_to_attrs_excludes() -> None:
    """Excluded ids are dropped."""
    data = [
        {"StateId": "1", "Value": "a"},
        {"StateId": "2", "Value": "b"},
    ]
    out = ZaptecBase.state_to_attrs(data, "StateId", {"1": "one", "2": "two"}, excludes={"2"})
    assert out == {"one": "a"}


def test_state_to_attrs_duplicate_last_wins() -> None:
    """When two entries map to the same attribute, the last one wins."""
    data = [
        {"StateId": "1", "Value": "first"},
        {"StateId": "1", "Value": "second"},
    ]
    out = ZaptecBase.state_to_attrs(data, "StateId", {"1": "current"})
    assert out == {"current": "second"}


# ---------------------------------------------------------------------------
#   ZaptecBase.set_attributes type conversion
# ---------------------------------------------------------------------------


def test_set_attributes_applies_type_conversion() -> None:
    """Known attributes are converted per ATTR_TYPES; keys become snake_case."""
    chg = Charger(
        {"ChargerMaxCurrent": "16", "IsOnline": "true", "Name": "Garage"}, _fake_owner()
    )
    assert chg["ChargerMaxCurrent"] == float("16")
    assert chg["IsOnline"] is True
    assert chg["Name"] == "Garage"


def test_set_attributes_unknown_key_passthrough() -> None:
    """Unknown attributes are stored unchanged under a snake_case key."""
    chg = Charger({"SomeUnknownKey": "value"}, _fake_owner())
    assert chg["SomeUnknownKey"] == "value"
    assert "some_unknown_key" in chg.asdict()


def test_set_attributes_conversion_failure_falls_back_to_raw() -> None:
    """A failing type conversion keeps the raw value instead of raising."""
    chg = Charger({"ChargerMaxCurrent": "not-a-number"}, _fake_owner())
    assert chg["ChargerMaxCurrent"] == "not-a-number"


def test_set_attributes_updates_existing_value() -> None:
    """Re-setting an attribute overwrites the previous value."""
    chg = Charger({"ChargerMaxCurrent": "16"}, _fake_owner())
    chg.set_attributes({"ChargerMaxCurrent": "32"})
    assert chg["ChargerMaxCurrent"] == float("32")


# ---------------------------------------------------------------------------
#   Charger.is_command_valid
# ---------------------------------------------------------------------------


def _charger_with_state(
    *, operation_mode: str | None = None, final_stop_active: str | None = None
) -> Charger:
    """Build a Charger carrying the state attributes is_command_valid reads."""
    data: dict[str, str] = {"Id": "chg-1"}
    if operation_mode is not None:
        data["ChargerOperationMode"] = operation_mode
    if final_stop_active is not None:
        data["FinalStopActive"] = final_stop_active
    return Charger(data, _fake_owner())


def test_is_command_valid_unrelated_command_is_always_valid() -> None:
    """Commands other than resume/stop are always valid (no state needed)."""
    chg = _charger_with_state()
    assert chg.is_command_valid("restart_charger", raise_value_error_if_invalid=True) is True


def test_is_command_valid_resume_when_paused_is_valid() -> None:
    """Resume is allowed only when the charger is paused."""
    chg = _charger_with_state(operation_mode="Connected_Finished", final_stop_active="1")
    assert chg.is_command_valid("resume_charging") is True


def test_is_command_valid_resume_when_not_paused_is_invalid() -> None:
    """Resume is rejected when not paused, and raises when requested."""
    chg = _charger_with_state(operation_mode="Connected_Charging", final_stop_active="0")
    assert chg.is_command_valid("resume_charging") is False
    with pytest.raises(ValueError, match="not paused"):
        chg.is_command_valid("resume_charging", raise_value_error_if_invalid=True)


def test_is_command_valid_stop_when_paused_is_invalid() -> None:
    """Stop/pause is rejected when already paused."""
    chg = _charger_with_state(operation_mode="Connected_Finished", final_stop_active="1")
    assert chg.is_command_valid("stop_charging_final") is False


def test_is_command_valid_stop_when_disconnected_is_invalid() -> None:
    """Stop/pause is rejected when disconnected."""
    chg = _charger_with_state(operation_mode="Disconnected", final_stop_active="0")
    assert chg.is_command_valid("stop_charging_final") is False


def test_is_command_valid_stop_when_charging_is_valid() -> None:
    """Stop/pause is allowed while actively charging."""
    chg = _charger_with_state(operation_mode="Connected_Charging", final_stop_active="0")
    assert chg.is_command_valid("stop_charging_final") is True


def test_is_command_valid_missing_final_stop_raises_type_error() -> None:
    """KNOWN ISSUE (Phase 3): a missing FinalStopActive makes int(None) raise.

    Characterizes current behavior; the correctness cleanup should guard this.
    """
    chg = _charger_with_state(operation_mode="Connected_Finished")
    with pytest.raises(TypeError):
        chg.is_command_valid("resume_charging")


# ---------------------------------------------------------------------------
#   Installation.stream_update routing
# ---------------------------------------------------------------------------


def _installation_with_charger() -> tuple[Installation, Charger]:
    """Build an installation owning a single charger spy."""
    owner = _fake_owner()
    inst = Installation({"Id": "inst-1"}, owner)
    charger = Charger({"Id": "chg-1"}, owner)
    charger.set_attributes = Mock()  # spy on the routed update
    inst.chargers = [charger]
    return inst, charger


def test_stream_update_routes_to_matching_charger(monkeypatch: pytest.MonkeyPatch) -> None:
    """A message with a known ChargerId updates that charger."""
    # observations is populated by build(); inject it for this offline test.
    monkeypatch.setattr(ZCONST, "observations", {"1": "current"}, raising=False)
    inst, charger = _installation_with_charger()
    inst.stream_update({"ChargerId": "chg-1", "StateId": "1", "ValueAsString": "5"})
    charger.set_attributes.assert_called_once()


def test_stream_update_unknown_charger_is_ignored() -> None:
    """A message for an unknown charger does not update anything."""
    inst, charger = _installation_with_charger()
    inst.stream_update({"ChargerId": "other", "StateId": "1", "ValueAsString": "5"})
    charger.set_attributes.assert_not_called()


def test_stream_update_missing_charger_id_is_ignored() -> None:
    """A message without a ChargerId is ignored."""
    inst, charger = _installation_with_charger()
    inst.stream_update({"StateId": "1", "ValueAsString": "5"})
    charger.set_attributes.assert_not_called()


def test_stream_update_zero_guid_is_ignored() -> None:
    """The all-zero charger id is explicitly ignored."""
    inst, charger = _installation_with_charger()
    inst.stream_update({"ChargerId": "00000000-0000-0000-0000-000000000000"})
    charger.set_attributes.assert_not_called()


# ---------------------------------------------------------------------------
#   Zaptec mapping / registry + poll dispatch
# ---------------------------------------------------------------------------


def test_zaptec_register_and_contains() -> None:
    """register/unregister and __contains__ handle both ids and objects."""
    zap, _ = _make_zaptec([])
    charger = Charger({"Id": "c1"}, zap)

    zap.register("c1", charger)
    assert "c1" in zap  # by id (str)
    assert charger in zap  # by object (ZaptecBase branch)
    assert "nope" not in zap
    assert object() not in zap  # arbitrary object -> not present

    zap.unregister("c1")
    assert "c1" not in zap


def test_zaptec_register_duplicate_raises() -> None:
    """Registering the same id twice raises."""
    zap, _ = _make_zaptec([])
    charger = Charger({"Id": "c1"}, zap)
    zap.register("c1", charger)
    with pytest.raises(ValueError, match="already registered"):
        zap.register("c1", charger)


def test_zaptec_qual_id_unknown_returns_id() -> None:
    """qual_id returns the raw id for an unknown object."""
    zap, _ = _make_zaptec([])
    assert zap.qual_id("missing-id") == "missing-id"


@pytest.mark.asyncio
async def test_poll_dispatches_info_and_state() -> None:
    """poll() calls poll_info/poll_state on the selected objects."""
    zap, _ = _make_zaptec([])
    obj = Mock()
    obj.poll_info = AsyncMock()
    obj.poll_state = AsyncMock()
    zap.register("x", obj)

    await zap.poll(["x"], info=True, state=True)
    obj.poll_info.assert_awaited_once()
    obj.poll_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_unknown_object_raises() -> None:
    """poll() raises for an unregistered object id."""
    zap, _ = _make_zaptec([])
    with pytest.raises(ValueError, match="not found"):
        await zap.poll(["missing"])


# ---------------------------------------------------------------------------
#   Charger command / settings wrappers
# ---------------------------------------------------------------------------


def _charger_with_session(
    outcomes: list[FakeResponse | BaseException],
) -> tuple[Charger, FakeSession]:
    """Build a charger whose owner performs requests against a FakeSession."""
    zap, session = _make_zaptec(outcomes)
    return Charger({"Id": "c1"}, zap), session


def _installation_with_session(
    outcomes: list[FakeResponse | BaseException],
) -> tuple[Installation, FakeSession]:
    """Build an installation whose owner performs requests against a FakeSession."""
    zap, session = _make_zaptec(outcomes)
    return Installation({"Id": "i1"}, zap), session


@pytest.mark.asyncio
async def test_command_posts_to_send_command_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """A named command resolves to its id and POSTs to SendCommand."""
    monkeypatch.setattr(ZCONST, "commands", {"restart_charger": 102}, raising=False)
    charger, session = _charger_with_session([FakeResponse(HTTPStatus.OK, json_data={})])

    await charger.command("restart_charger")

    method, url, _ = session.calls[-1]
    assert method == "post"
    assert url.endswith("chargers/c1/SendCommand/102")


@pytest.mark.asyncio
async def test_command_authorize_charge_alias() -> None:
    """The authorize_charge alias POSTs to the authorizecharge endpoint."""
    charger, session = _charger_with_session([FakeResponse(HTTPStatus.OK, json_data={})])

    await charger.command("authorize_charge")

    method, url, _ = session.calls[-1]
    assert method == "post"
    assert url.endswith("chargers/c1/authorizecharge")


@pytest.mark.asyncio
async def test_command_unknown_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown command raises without issuing a request."""
    monkeypatch.setattr(ZCONST, "commands", {}, raising=False)
    charger, session = _charger_with_session([])

    with pytest.raises(ValueError, match="Unknown command"):
        await charger.command("does_not_exist")
    assert session.calls == []


@pytest.mark.asyncio
async def test_set_settings_valid() -> None:
    """Valid settings are POSTed to the charger update endpoint."""
    settings = {"maxChargeCurrent": 16}
    charger, session = _charger_with_session([FakeResponse(HTTPStatus.OK, json_data={})])

    await charger.set_settings(settings)

    method, url, kwargs = session.calls[-1]
    assert method == "post"
    assert url.endswith("chargers/c1/update")
    assert kwargs["json"] == settings


@pytest.mark.asyncio
async def test_set_settings_unknown_key_raises() -> None:
    """An unknown setting key raises without issuing a request."""
    charger, session = _charger_with_session([])
    with pytest.raises(ValueError, match="Unknown setting"):
        await charger.set_settings({"bogusKey": 1})
    assert session.calls == []


@pytest.mark.asyncio
async def test_authorize_charge_posts() -> None:
    """authorize_charge POSTs to the authorizecharge endpoint."""
    charger, session = _charger_with_session([FakeResponse(HTTPStatus.OK, json_data={})])

    await charger.authorize_charge()

    method, url, _ = session.calls[-1]
    assert method == "post"
    assert url.endswith("chargers/c1/authorizecharge")


@pytest.mark.asyncio
async def test_set_permanent_cable_lock_payload() -> None:
    """The permanent cable lock is sent under Cable.PermanentLock."""
    expected = {"Cable": {"PermanentLock": True}}
    charger, session = _charger_with_session([FakeResponse(HTTPStatus.OK, json_data={})])

    await charger.set_permanent_cable_lock(lock=True)

    method, url, kwargs = session.calls[-1]
    assert method == "post"
    assert url.endswith("chargers/c1/localSettings")
    assert kwargs["json"] == expected


@pytest.mark.asyncio
async def test_set_hmi_brightness_payload() -> None:
    """The HMI brightness is sent under Device.HmiBrightness."""
    brightness = 0.5
    expected = {"Device": {"HmiBrightness": brightness}}
    charger, session = _charger_with_session([FakeResponse(HTTPStatus.OK, json_data={})])

    await charger.set_hmi_brightness(brightness)

    method, url, kwargs = session.calls[-1]
    assert method == "post"
    assert url.endswith("chargers/c1/localSettings")
    assert kwargs["json"] == expected


# ---------------------------------------------------------------------------
#   Installation current-limit setters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_limit_current_available_current() -> None:
    """A single availableCurrent limit is POSTed to the installation update."""
    expected = {"availableCurrent": 16}
    inst, session = _installation_with_session([FakeResponse(HTTPStatus.OK, json_data={})])

    await inst.set_limit_current(**expected)

    method, url, kwargs = session.calls[-1]
    assert method == "post"
    assert url.endswith("installation/i1/update")
    assert kwargs["json"] == expected


@pytest.mark.asyncio
async def test_set_limit_current_requires_current_argument() -> None:
    """Calling without any current argument raises."""
    inst, session = _installation_with_session([])
    with pytest.raises(ValueError, match="availableCurrent"):
        await inst.set_limit_current()
    assert session.calls == []


@pytest.mark.asyncio
async def test_set_limit_current_partial_phases_raise() -> None:
    """Providing availableCurrent with only some per-phase currents raises."""
    inst, session = _installation_with_session([])
    with pytest.raises(ValueError, match="all of them must be set"):
        await inst.set_limit_current(availableCurrent=10, availableCurrentPhase1=10)
    assert session.calls == []


@pytest.mark.asyncio
async def test_set_limit_current_out_of_range_raises() -> None:
    """A current above the installation maximum raises."""
    inst, session = _installation_with_session([])
    with pytest.raises(ValueError, match="between 0 and"):
        await inst.set_limit_current(availableCurrent=1000)
    assert session.calls == []


@pytest.mark.asyncio
async def test_set_three_to_one_phase_switch_current() -> None:
    """The 3-to-1 phase switch current is POSTed to the installation update."""
    current = 16
    expected = {"threeToOnePhaseSwitchCurrent": current}
    inst, session = _installation_with_session([FakeResponse(HTTPStatus.OK, json_data={})])

    await inst.set_three_to_one_phase_switch_current(current)

    method, url, kwargs = session.calls[-1]
    assert method == "post"
    assert url.endswith("installation/i1/update")
    assert kwargs["json"] == expected


@pytest.mark.asyncio
async def test_set_three_to_one_phase_switch_current_out_of_range_raises() -> None:
    """An out-of-range 3-to-1 phase switch current raises."""
    inst, session = _installation_with_session([])
    with pytest.raises(ValueError, match="between 0 and"):
        await inst.set_three_to_one_phase_switch_current(1000)
    assert session.calls == []


# ---------------------------------------------------------------------------
#   Charger.poll_info / poll_state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_info_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """poll_info fetches the charger and applies the attributes."""
    # Payload validation has its own tests; bypass it here.
    monkeypatch.setattr("custom_components.zaptec.zaptec.api.validate", Mock())
    charger, _ = _charger_with_session([FakeResponse(HTTPStatus.OK, json_data={"Id": "c1"})])
    charger.set_attributes = Mock()

    await charger.poll_info()

    charger.set_attributes.assert_called_once()


@pytest.mark.asyncio
async def test_poll_info_falls_back_to_charger_list_on_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 403 on the charger endpoint falls back to the chargers list."""
    monkeypatch.setattr("custom_components.zaptec.zaptec.api.validate", Mock())
    charger, _ = _charger_with_session(
        [
            FakeResponse(HTTPStatus.FORBIDDEN),
            FakeResponse(HTTPStatus.OK, json_data={"Data": [{"Id": "c1", "Name": "x"}]}),
        ]
    )
    charger.set_attributes = Mock()

    await charger.poll_info()

    # Reached only via the fallback branch, since the first request raised.
    charger.set_attributes.assert_called_once()


@pytest.mark.asyncio
async def test_poll_info_non_forbidden_error_propagates() -> None:
    """A non-403 error is re-raised rather than falling back."""
    charger, _ = _charger_with_session([FakeResponse(HTTPStatus.NOT_FOUND)])
    with pytest.raises(RequestError):
        await charger.poll_info()


@pytest.mark.asyncio
async def test_poll_state_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """poll_state fetches the state list and applies the mapped attributes."""
    monkeypatch.setattr(ZCONST, "observations", {"1": "current"}, raising=False)
    monkeypatch.setattr("custom_components.zaptec.zaptec.api.validate", Mock())
    charger, session = _charger_with_session(
        [FakeResponse(HTTPStatus.OK, json_data=[{"StateId": "1", "ValueAsString": "5"}])]
    )
    charger.set_attributes = Mock()

    await charger.poll_state()

    _, url, _ = session.calls[-1]
    assert url.endswith("chargers/c1/state")
    charger.set_attributes.assert_called_once()


@pytest.mark.asyncio
async def test_poll_state_forbidden_is_ignored() -> None:
    """A 403 on the state endpoint is swallowed (no attribute update)."""
    charger, _ = _charger_with_session([FakeResponse(HTTPStatus.FORBIDDEN)])
    charger.set_attributes = Mock()

    await charger.poll_state()

    charger.set_attributes.assert_not_called()


# ---------------------------------------------------------------------------
#   Zaptec._refresh_token / login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_stores_and_uses_access_token() -> None:
    """A successful login obtains a token and sends it on later requests."""
    zap, session = _make_zaptec(
        [
            FakeResponse(HTTPStatus.OK, json_data={"access_token": "abc"}),
            FakeResponse(HTTPStatus.OK, json_data={}),
        ]
    )

    await zap.login()
    await zap.request("some/url")

    _, _, kwargs = session.calls[-1]
    assert kwargs["headers"]["Authorization"] == "Bearer abc"


@pytest.mark.asyncio
async def test_login_bad_credentials_raises_authentication_error() -> None:
    """A 400 from the token endpoint raises AuthenticationError."""
    zap, _ = _make_zaptec(
        [FakeResponse(HTTPStatus.BAD_REQUEST, json_data={"error_description": "nope"})]
    )
    with pytest.raises(AuthenticationError):
        await zap.login()


# ---------------------------------------------------------------------------
#   Assorted small accessors / lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_command_by_numeric_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """A numeric command id is sent directly to SendCommand."""
    monkeypatch.setattr(ZCONST, "commands", {102: "restart_charger"}, raising=False)
    charger, session = _charger_with_session([FakeResponse(HTTPStatus.OK, json_data={})])

    await charger.command(102)

    _, url, _ = session.calls[-1]
    assert url.endswith("chargers/c1/SendCommand/102")


@pytest.mark.asyncio
async def test_installation_poll_info_strips_logo(monkeypatch: pytest.MonkeyPatch) -> None:
    """poll_info removes the bulky SupportGroup logo before storing attributes."""
    monkeypatch.setattr("custom_components.zaptec.zaptec.api.validate", Mock())
    inst, _ = _installation_with_session(
        [FakeResponse(HTTPStatus.OK, json_data={"SupportGroup": {"LogoBase64": "AAAA"}})]
    )
    inst.set_attributes = Mock()

    await inst.poll_info()

    inst.set_attributes.assert_called_once()
    stored = inst.set_attributes.call_args[0][0]
    assert stored["SupportGroup"]["LogoBase64"].startswith("<Removed")


def test_charger_is_charging() -> None:
    """is_charging reflects the operation mode."""
    assert Charger({"ChargerOperationMode": "Connected_Charging"}, _fake_owner()).is_charging()
    assert not Charger({"ChargerOperationMode": "Disconnected"}, _fake_owner()).is_charging()


def test_charger_model_from_device_id() -> None:
    """The model is derived from the DeviceId prefix."""
    chg = Charger({"DeviceId": "ZAP123456"}, _fake_owner())
    assert chg.model_prefix == "ZAP"
    assert chg.model == "Zaptec Go"


def test_zaptec_collections_and_accessors() -> None:
    """objects/installations/chargers and iteration reflect the registry."""
    zap, _ = _make_zaptec([])
    inst = Installation({"Id": "i1"}, zap)
    chg = Charger({"Id": "c1"}, zap)
    zap.register("i1", inst)
    zap.register("c1", chg)

    ids = {"i1", "c1"}
    assert set(zap) == ids  # __iter__
    assert len(zap) == len(ids)  # __len__
    # ZaptecBase subclasses Mapping (unhashable), so compare as lists.
    objs = list(zap.objects())
    assert inst in objs
    assert chg in objs
    assert list(zap.installations) == [inst]
    assert list(zap.chargers) == [chg]

    zap.unregister("c1")
    assert set(zap) == {"i1"}


@pytest.mark.asyncio
async def test_zaptec_async_context_manager_closes_internal_client() -> None:
    """Entering/exiting the context manager works with an internally-created client."""
    async with Zaptec("user", "pass") as zap:
        assert isinstance(zap, Zaptec)
