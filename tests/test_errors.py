"""Tests for custom_components.zaptec.errors."""

from http import HTTPStatus

import pytest

from custom_components.zaptec.const import DOMAIN
from custom_components.zaptec.errors import api_call_error
from custom_components.zaptec.zaptec.exceptions import RequestError


def _request_error(zaptec_code: int | None, details: str | None = None) -> RequestError:
    """Return a RequestError as api.py raises it for an HTTP 500."""
    return RequestError(
        "POST request failed",
        HTTPStatus.INTERNAL_SERVER_ERROR,
        zaptec_code=zaptec_code,
        zaptec_details=details,
    )


def test_details_are_shown_when_zaptec_sends_them() -> None:
    """A 500 carrying Details surfaces that text verbatim."""
    err = api_call_error(_request_error(527, "Some other reason"), "Set X")

    assert err.translation_domain == DOMAIN
    assert err.translation_key == "api_error_details"
    assert err.translation_placeholders == {"action": "Set X", "details": "Some other reason"}


def test_apm_is_reworded_as_zaptec_sense() -> None:
    """Issue #363: "APM" is Zaptec-internal jargon users won't recognise."""
    err = api_call_error(
        _request_error(527, "Cannot update installation when using APM"), "Set X"
    )

    assert err.translation_key == "api_error_apm"
    assert err.translation_placeholders == {"action": "Set X"}


def test_non_string_details_do_not_crash() -> None:
    """Details is server-controlled; a non-string must not break error handling."""
    err = api_call_error(_request_error(999999, 123), "Set X")  # type: ignore[arg-type]

    assert err.translation_key == "api_error_code"
    assert err.translation_placeholders == {"action": "Set X", "reason": "999999"}


def test_scheduled_charging_has_its_own_message() -> None:
    """Scheduled charging blocks installation updates with its own 527 reason."""
    err = api_call_error(
        _request_error(527, "Cannot update installation when using scheduled power management"),
        "Set X",
    )

    assert err.translation_key == "api_error_scheduled"
    assert err.translation_placeholders == {"action": "Set X"}


def test_unrelated_apm_mention_is_not_claimed_to_be_sense() -> None:
    """Only the known sentence maps to Sense; other APM reasons show verbatim."""
    err = api_call_error(_request_error(527, "APM device is offline"), "Set X")

    assert err.translation_key == "api_error_details"
    assert err.translation_placeholders == {"action": "Set X", "details": "APM device is offline"}


def test_command_rejected_is_hedged() -> None:
    """528 gets its own key, since the command may still have been carried out."""
    err = api_call_error(_request_error(528), "Press Y")

    assert err.translation_key == "api_error_command_rejected"
    assert err.translation_placeholders == {"action": "Press Y"}


def test_charger_state_has_its_own_message() -> None:
    """538 means the charger's state forbids the operation."""
    err = api_call_error(_request_error(538), "Press Y")

    assert err.translation_key == "api_error_charger_state"
    assert err.translation_placeholders == {"action": "Press Y"}


def test_unknown_zaptec_code_falls_back_to_the_decoded_name() -> None:
    """An unrecognised code still names itself rather than vanishing."""
    err = api_call_error(_request_error(999999), "Set X")

    assert err.translation_key == "api_error_code"
    assert err.translation_placeholders == {"action": "Set X", "reason": "999999"}


@pytest.mark.parametrize(
    "exc",
    [
        RequestError("not found", HTTPStatus.NOT_FOUND),
        ValueError("something else entirely"),
    ],
)
def test_errors_without_a_zaptec_code_use_the_plain_message(exc: Exception) -> None:
    """Non-500 and non-API failures keep today's generic wording."""
    err = api_call_error(exc, "Set X")

    assert err.translation_key == "api_error"
    assert err.translation_placeholders == {"action": "Set X"}
