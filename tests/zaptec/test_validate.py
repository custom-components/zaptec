"""Tests for zaptec/validate.py."""

import logging

import pytest

from custom_components.zaptec.zaptec.validate import ValidationError, validate

_LOGGER = logging.getLogger(__name__)


def test_charger_states_validation() -> None:
    """Check validation of charger states responses."""

    charger_states_url = "chargers/12345678-90ab-cdef-1234567890ab/state"
    valid_charger_states_response = [
        {
            "ChargerId": "12345678-90ab-cdef-1234567890ab",
            "StateId": -2,
            "Timestamp": "2025-10-04T03:41:43.57",
            "ValueAsString": "1",
        },
        {
            "ChargerId": "12345678-90ab-cdef-1234567890ab",
            "StateId": -1,
            "Timestamp": "2025-10-08T19:55:56.47",
        },
        {
            "ChargerId": "12345678-90ab-cdef-1234567890ab",
            "StateId": 1,
            "Timestamp": "2024-08-01T18:15:29.91",
            "ValueAsString": "0",
        },
        {
            "ChargerId": "12345678-90ab-cdef-1234567890ab",
            "StateId": 150,
            "Timestamp": "2024-08-01T18:17:49.513",
            "ValueAsString": "Wifi",
        },
        {
            "ChargerId": "12345678-90ab-cdef-1234567890ab",
            "StateId": 151,
            "Timestamp": "2025-07-21T19:26:27.933",
            "ValueAsString": "1",
        },
        {
            "ChargerId": "12345678-90ab-cdef-1234567890ab",
            "StateId": 153,
            "Timestamp": "2024-08-01T18:30:19.29",
            "ValueAsString": "0.247",
        },
    ]
    validate(valid_charger_states_response, charger_states_url)

    # check that including a state without StateId causes validation to fail
    invalid_charger_state = {"Random key": "random value", "No StateId": "missing"}
    invalid_charger_states_response = [*valid_charger_states_response, invalid_charger_state]
    with pytest.raises(ValidationError):
        validate(invalid_charger_states_response, charger_states_url)


def test_installation_validation() -> None:
    """Check validation of installation responses."""

    installation_list_url = "installation"
    single_installation_url = "installation/abcdef01-2345-6789-abcd-ef0123456789"

    valid_installation = {
        "Id": "abcdef01-2345-6789-abcd-ef0123456789",
        "InstallationType": 1,
        "MaxCurrent": 32.0,
        "Active": True,
        "NetworkType": 2,
        "CurrentUserRoles": 3,
        "AuthenticationType": 0,
    }
    validate(valid_installation, single_installation_url)

    valid_installation_list = {
        "Pages": 1,
        "Data": [valid_installation],
    }
    validate(valid_installation_list, installation_list_url)

    # check that any invalid object in the list of installations triggers validation fail
    invalid_installation_list = {
        "Pages": 1,
        "Data": [valid_installation, {}],
    }
    with pytest.raises(ValidationError):
        validate(invalid_installation_list, installation_list_url)

    # check that an installation missing NetworkType fails validation
    invalid_installation = valid_installation.copy()
    invalid_installation.pop("NetworkType")
    with pytest.raises(ValidationError):
        validate(invalid_installation, single_installation_url)

    invalid_installation_list2 = {
        "Pages": 1,
        "Data": [invalid_installation],
    }
    with pytest.raises(ValidationError):
        validate(invalid_installation_list2, installation_list_url)


def test_missing_and_skipped_validation() -> None:
    """Check that unknown urls and urls setup with None as the Validation model pass."""

    # check an unknown url
    validate({}, "unknown_url")

    # Check a url that is set up with no validation check
    validate({}, "installation/123456-abcdef-0/update")
