"""Tests for zaptec/zconst.py."""

import asyncio

import pytest

from custom_components.zaptec.zaptec.api import Zaptec
from custom_components.zaptec.zaptec.zconst import ZCONST


@pytest.fixture(scope="module")  # Only runs once
def zaptec_constants() -> dict:
    """Get latest constants from Zaptec API."""

    async def get_zaptec_constants() -> dict:
        async with Zaptec("N/A", "N/A") as zaptec:
            # the constants API endpoint does not require login
            const: dict = await zaptec.request("constants")
            return const

    return asyncio.run(get_zaptec_constants())


@pytest.fixture(autouse=True)  # runs for every test
def init_zconst(zaptec_constants: dict) -> None:
    """Initialize the ZCONST instance."""
    ZCONST.clear()
    ZCONST.update(zaptec_constants)
    ZCONST.update_ids_from_schema(None)


def test_init_zconst(zaptec_constants: dict) -> None:
    """Test the initialization of the ZCONST instance."""
    ZCONST.clear()
    ZCONST.update(zaptec_constants)
    ZCONST.update_ids_from_schema(None)


def test_charger_operation_modes() -> None:
    """Test the contents of charger_operation_modes_list."""
    assert "Unknown" in ZCONST.charger_operation_modes_list
    assert "Disconnected" in ZCONST.charger_operation_modes_list
    assert "Connected_Requesting" in ZCONST.charger_operation_modes_list
    assert "Connected_Charging" in ZCONST.charger_operation_modes_list
    assert "Connected_Finished" in ZCONST.charger_operation_modes_list
    assert len(ZCONST.charger_operation_modes_list) == 5  # noqa: PLR2004

    assert ZCONST.type_charger_operation_mode(1) == "Disconnected"
    assert ZCONST.type_charger_operation_mode(2) == "Connected_Requesting"
    assert ZCONST.type_charger_operation_mode(3) == "Connected_Charging"
    assert ZCONST.type_charger_operation_mode(5) == "Connected_Finished"


def test_device_types() -> None:
    """Test the contents of device_types_list."""
    assert "Unknown" in ZCONST.device_types_list
    assert "Smart" in ZCONST.device_types_list
    assert "Portable" in ZCONST.device_types_list
    assert "HomeApm" in ZCONST.device_types_list
    assert "Apollo" in ZCONST.device_types_list
    assert "OtherApm" in ZCONST.device_types_list
    assert "GenericApm" in ZCONST.device_types_list
    assert "HanApm" in ZCONST.device_types_list

    assert ZCONST.type_device_type(1) == "Smart"
    assert ZCONST.type_device_type(4) == "Apollo"


def test_user_roles() -> None:
    """Test the user roles conversion."""
    assert ZCONST.type_user_roles(0) == "None"
    assert ZCONST.type_user_roles(1) == "User"
    assert ZCONST.type_user_roles(2) == "Owner"
    user_role_3 = ZCONST.type_user_roles(3)
    assert "Owner" in user_role_3
    assert "User" in user_role_3
    assert ZCONST.type_user_roles(4) == "Maintainer"
    user_role_5 = ZCONST.type_user_roles(5)
    assert "User" in user_role_5
    assert "Maintainer" in user_role_5


def test_authentication_types() -> None:
    """Test the authentication types."""
    assert "Ocpp" in ZCONST.installation_authentication_type_list
    assert "Native" in ZCONST.installation_authentication_type_list
    assert "WebHooks" in ZCONST.installation_authentication_type_list
    assert "OcppNative" in ZCONST.installation_authentication_type_list

    assert ZCONST.type_authentication_type(2) == "Ocpp"


def test_serial_to_model() -> None:
    """Test the serial to model conversion."""
    assert ZCONST.serial_to_model.get("ZAP000042"[0:3]) == "Zaptec Go"
    assert ZCONST.serial_to_model.get("ZCH314159"[0:3]) == "Zaptec Pro"
    assert ZCONST.serial_to_model.get("GPG271828"[0:3]) == "Zaptec Go2"
    assert ZCONST.serial_to_model.get("APM306090"[0:3]) == "Zaptec Sense"


def test_network_types() -> None:
    """Test the network types."""
    assert "IT_1_Phase" in ZCONST.network_types_list
    assert "IT_3_Phase" in ZCONST.network_types_list
    assert "TN_1_Phase" in ZCONST.network_types_list
    assert "TN_3_Phase" in ZCONST.network_types_list

    assert ZCONST.type_network_type(1) == "IT_1_Phase"
    assert ZCONST.type_network_type(2) == "IT_3_Phase"
    assert ZCONST.type_network_type(3) == "TN_1_Phase"
    assert ZCONST.type_network_type(4) == "TN_3_Phase"


def test_installation_types() -> None:
    """Test the installation types."""
    assert "Pro" in ZCONST.installation_types_list
    assert "Smart" in ZCONST.installation_types_list

    assert ZCONST.type_installation_type(0) == "Pro"
    assert ZCONST.type_installation_type(1) == "Smart"


def test_completed_session() -> None:
    """Test the completed_session conversion."""
    completed_session = (
        r'{"SessionId":"01234567-89ab-cdef-0123-456789abcdef","Energy":27.432,"StartDateTime":'
        r'"2025-08-28T15:02:55.188048Z","EndDateTime":"2025-09-01T10:32:50.517055Z",'
        r'"ReliableClock":true,"StoppedByRFID":false,"AuthenticationCode":"",'
        r'"FirstAuthenticatedDateTime":"","SignedSession":"OCMF|{\"FV\":\"1.0\",\"GI\":'
        r"\"ZAPTEC GO\",\"GS\":\"ZAP000042\",\"GV\":\"2.4.2.4\",\"PG\":\"T1\",\"RD\":[{\"TM\":"
        r"\"2025-08-28T15:02:55,000+00:00 R\",\"TX\":\"B\",\"RV\":1472.07,\"RI\":\"1-0:1.8.0\","
        r"\"RU\":\"kWh\",\"RT\":\"AC\",\"ST\":\"G\"},{\"TM\":\"2025-08-29T12:00:00,000+00:00 R\","
        r"\"TX\":\"T\",\"RV\":1472.29,\"RI\":\"1-0:1.8.0\",\"RU\":\"kWh\",\"RT\":\"AC\",\"ST\":"
        r'\"G\"}]}"}'
    )
    expected_completed_session_dict = {
        "SessionId": "01234567-89ab-cdef-0123-456789abcdef",
        "Energy": 27.432,
        "StartDateTime": "2025-08-28T15:02:55.188048Z",
        "EndDateTime": "2025-09-01T10:32:50.517055Z",
        "ReliableClock": True,
        "StoppedByRFID": False,
        "AuthenticationCode": "",
        "FirstAuthenticatedDateTime": "",
        "SignedSession": {
            "FV": "1.0",
            "GI": "ZAPTEC GO",
            "GS": "ZAP000042",
            "GV": "2.4.2.4",
            "PG": "T1",
            "RD": [
                {
                    "TM": "2025-08-28T15:02:55,000+00:00 R",
                    "TX": "B",
                    "RV": 1472.07,
                    "RI": "1-0:1.8.0",
                    "RU": "kWh",
                    "RT": "AC",
                    "ST": "G",
                },
                {
                    "TM": "2025-08-29T12:00:00,000+00:00 R",
                    "TX": "T",
                    "RV": 1472.29,
                    "RI": "1-0:1.8.0",
                    "RU": "kWh",
                    "RT": "AC",
                    "ST": "G",
                },
            ],
        },
    }
    assert ZCONST.type_completed_session(completed_session) == expected_completed_session_dict


def test_update_ids_from_schema() -> None:
    """Test update_ids_from_schema."""
    ZCONST.update_ids_from_schema(set("Apollo"))
