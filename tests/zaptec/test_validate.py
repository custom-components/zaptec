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

    # Users without the Owner/Service role get a reduced installation object
    # missing Active/CurrentUserRoles/InstallationType/NetworkType (see #357).
    # api.py only ever indexes Id directly, so this must still validate.
    limited_installation = {"Id": valid_installation["Id"]}
    validate(limited_installation, single_installation_url)

    limited_installation_list = {
        "Pages": 1,
        "Data": [limited_installation],
    }
    validate(limited_installation_list, installation_list_url)

    # Id is required: Zaptec.build() indexes inst_item["Id"] directly.
    invalid_installation = valid_installation.copy()
    invalid_installation.pop("Id")
    with pytest.raises(ValidationError):
        validate(invalid_installation, single_installation_url)

    invalid_installation_list2 = {
        "Pages": 1,
        "Data": [invalid_installation],
    }
    with pytest.raises(ValidationError):
        validate(invalid_installation_list2, installation_list_url)


def test_charger_validation() -> None:
    """Check validation of /chargers and /chargers/{id} responses."""

    chargers_list_url = "chargers"
    single_charger_url = "chargers/12345678-90ab-cdef-1234567890ab"

    valid_charger = {
        "Id": "12345678-90ab-cdef-1234567890ab",
        "Name": "Garage",
        "Active": True,
        "DeviceType": 4,
    }
    validate(valid_charger, single_charger_url)
    validate({"Pages": 1, "Data": [valid_charger]}, chargers_list_url)

    # Users without the Owner role get a reduced charger object missing
    # Name/Active; only Id and DeviceType are consumed directly by api.py.
    limited_charger = {"Id": valid_charger["Id"], "DeviceType": 4}
    validate(limited_charger, single_charger_url)
    validate({"Pages": 1, "Data": [limited_charger]}, chargers_list_url)

    # DeviceType is required: Zaptec.build() indexes chg["DeviceType"] on
    # every registered charger once merged from the /chargers list.
    missing_device_type = {"Id": valid_charger["Id"]}
    with pytest.raises(ValidationError):
        validate(missing_device_type, single_charger_url)

    # Id is required: Zaptec.build() indexes charger_item["Id"] directly.
    missing_id = {"DeviceType": 4}
    with pytest.raises(ValidationError):
        validate(missing_id, single_charger_url)


def test_hierarchy_validation() -> None:
    """Check validation of installation/{id}/hierarchy responses."""

    hierarchy_url = "installation/abcdef01-2345-6789-abcd-ef0123456789/hierarchy"

    valid_hierarchy = {
        "Id": "abcdef01-2345-6789-abcd-ef0123456789",
        "Name": "Main hierarchy",
        "NetworkType": 2,
        "Circuits": [
            {
                "Id": "11111111-1111-1111-1111-111111111111",
                "Name": "Circuit 1",
                "MaxCurrent": 32.0,
                "Chargers": [
                    {"Id": "12345678-90ab-cdef-1234567890ab", "DeviceType": 4},
                ],
            },
        ],
    }
    validate(valid_hierarchy, hierarchy_url)

    hierarchy_id = "abcdef01-2345-6789-abcd-ef0123456789"

    # Name/NetworkType on the hierarchy itself aren't read by api.py, and per
    # the Zaptec API docs a circuit's Name/Chargers may be null -- all of this
    # must still validate. The hierarchy Id, however, is always present.
    minimal_hierarchy = {
        "Id": hierarchy_id,
        "Circuits": [
            {
                "Id": "11111111-1111-1111-1111-111111111111",
                "MaxCurrent": 32.0,
                "Chargers": None,
            },
        ],
    }
    validate(minimal_hierarchy, hierarchy_url)

    # The hierarchy Id is required: the response always carries it.
    missing_hierarchy_id = {
        "Circuits": [
            {"Id": "11111111-1111-1111-1111-111111111111", "MaxCurrent": 32.0},
        ],
    }
    with pytest.raises(ValidationError):
        validate(missing_hierarchy_id, hierarchy_url)

    # MaxCurrent is required: Installation.build() indexes
    # circuit["MaxCurrent"] directly with no validation coverage today --
    # exactly the class of bug #359 asks to close.
    missing_max_current = {
        "Id": hierarchy_id,
        "Circuits": [{"Id": "11111111-1111-1111-1111-111111111111"}],
    }
    with pytest.raises(ValidationError):
        validate(missing_max_current, hierarchy_url)

    # A circuit's Id is required: Installation.build() indexes circuit["Id"].
    missing_circuit_id = {
        "Id": hierarchy_id,
        "Circuits": [{"MaxCurrent": 32.0}],
    }
    with pytest.raises(ValidationError):
        validate(missing_circuit_id, hierarchy_url)

    # DeviceType is required: Zaptec.build() hard-subscripts chg["DeviceType"],
    # including hierarchy-only chargers never re-merged with the /chargers list.
    missing_device_type_in_hierarchy = {
        "Id": hierarchy_id,
        "Circuits": [
            {
                "Id": "11111111-1111-1111-1111-111111111111",
                "MaxCurrent": 32.0,
                "Chargers": [{"Id": "12345678-90ab-cdef-1234567890ab"}],
            },
        ],
    }
    with pytest.raises(ValidationError):
        validate(missing_device_type_in_hierarchy, hierarchy_url)


def test_charger_firmware_validation() -> None:
    """Check validation of chargerFirmware/installation/{id} responses."""

    firmware_url = "chargerFirmware/installation/abcdef01-2345-6789-abcd-ef0123456789"

    valid_firmware = [
        {
            "ChargerId": "12345678-90ab-cdef-1234567890ab",
            "DeviceType": 4,
            "IsOnline": True,
            "CurrentVersion": "1.2.3",
            "AvailableVersion": "1.2.4",
            "IsUpToDate": False,
        },
    ]
    validate(valid_firmware, firmware_url)

    # A charger not yet initialized reports only ChargerId; poll_firmware_info()
    # treats the rest as optional. All fields except ChargerId are nullable, so
    # validation must not reject this before that defensive code runs.
    uninitialized_firmware = [{"ChargerId": "12345678-90ab-cdef-1234567890ab"}]
    validate(uninitialized_firmware, firmware_url)

    # ChargerId is required: poll_firmware_info() indexes fm["ChargerId"] directly.
    missing_charger_id = [{"DeviceType": 4}]
    with pytest.raises(ValidationError):
        validate(missing_charger_id, firmware_url)


def test_missing_and_skipped_validation() -> None:
    """Check that unknown urls and urls setup with None as the Validation model pass."""

    # check an unknown url
    validate({}, "unknown_url")

    # Check a url that is set up with no validation check
    validate({}, "installation/123456-abcdef-0/update")
