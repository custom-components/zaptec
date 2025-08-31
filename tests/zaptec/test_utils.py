"""Tests for zaptec/utils.py."""

import pytest

from custom_components.zaptec.zaptec.utils import (
    data_producer,
    get_ocmf_max_reader_value,
    mc_nbfx_decoder,
    to_under,
)


def test_utils_data_producer() -> None:
    """Test the data_producer function."""
    data = b"Hello, World!"
    producer = data_producer(data)
    next(producer)  # Prime the generator

    assert producer.send(5) == b"Hello"
    assert producer.send(2) == b", "
    assert producer.send(6) == b"World!"
    with pytest.raises(StopIteration):
        producer.send(1)  # Data is exhausted

    # Test with empty data
    data = b""
    producer = data_producer(data)
    next(producer)  # Prime the generator
    with pytest.raises(StopIteration):
        producer.send(5)  # Data is exhausted


def test_utils_get_ocmf_max_reader_value() -> None:
    """Test the get_ocmf_max_reader_value function."""
    data = {
        "RD": [
            {"RV": 100.0},
            {"RV": 150.5},
            {"RV": 120.3},
        ]
    }
    assert get_ocmf_max_reader_value(data) == 150.5  # noqa: PLR2004

    data_none = None
    assert get_ocmf_max_reader_value(data_none) == 0.0

    data_empty = {"RD": []}
    assert get_ocmf_max_reader_value(data_empty) == 0.0

    data_no_rd = {}
    assert get_ocmf_max_reader_value(data_no_rd) == 0.0

    data_missing_rv = {"RD": [{"RI": "1-0:1.8.0"}]}
    assert get_ocmf_max_reader_value(data_missing_rv) == 0.0


def test_utils_mc_nbfx_decoder() -> None:
    """Test the mc_nbfx_decoder function."""
    # Example NBFX encoded message (hex)
    nbfx_message = bytes.fromhex(
        "40 05 48 65 6c 6c 6f"  # SHORT_ELEMENT "Hello"
        "08 03 78 6d 6c"  # SHORT_XMLNS_ATTRIBUTE "xml"
        "98 05 57 6f 72 6c 64"  # CHARS8TEXT "World"
        "01"  # END_ELEMENT
    )

    expected_output = [{"name": "Hello", "xmlns": "xml", "text": "World"}]

    result = mc_nbfx_decoder(nbfx_message)
    assert result == expected_output


def test_utils_mc_nbfx_decoder_example1() -> None:
    """Test the mc_nbfx_decoder function with a real-world example."""
    nbfx_message = (
        b"@\x06string\x083http://schemas.microsoft.com/2003/10/Serialization/"
        b'\x98\xa5{"DeviceId":"ZAP000000","DeviceType":4,'
        b'"ChargerId":"xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx","StateId":523,'
        b'"Timestamp":"2023-07-28T09:07:19.23","ValueAsString":"0.000"}\x01'
    )

    expected_output = [
        {
            "name": "string",
            "text": (
                '{"DeviceId":"ZAP000000","DeviceType":4,'
                '"ChargerId":"xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx","StateId":523,'
                '"Timestamp":"2023-07-28T09:07:19.23","ValueAsString":"0.000"}'
            ),
            "xmlns": "http://schemas.microsoft.com/2003/10/Serialization/",
        }
    ]

    result = mc_nbfx_decoder(nbfx_message)
    assert result == expected_output, f"Expected {expected_output}, got {result}"


def test_utils_mc_nbfx_decoder_example2() -> None:
    """Test the mc_nbfx_decoder function with another real-world example."""
    nbfx_message = (
        b"@\x06string\x083http://schemas.microsoft.com/2003/10/Serialization/"
        b'\x9a\x87\x01{"DeviceId":"ZAP000000","DeviceType":4,'
        b'"ChargerId":"xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx","StateId":554,'
        b'"Timestamp":"2023-08-03T00:00:00.0617Z","ValueAsString":"OCMF|'
        b'{\\"FV\\":\\"1.0\\",\\"GI\\":\\"ZAPTEC GO\\",'
        b'\\"GS\\":\\"ZAP000000\\",\\"GV\\":\\"2.1.0.4\\",\\"PG\\":\\"F1\\",'
        b'\\"RD\\":[{\\"TM\\":\\"2023-08-03T00:00:00,000+00:00 R\\",'
        b'\\"RV\\":179.715,\\"RI\\":\\"1-0:1.8.0\\",\\"RU\\":\\"kWh\\",'
        b'\\"RT\\":\\"AC\\",\\"ST\\":\\"G\\"}]}"}\x01'
    )

    expected_output = [
        {
            "name": "string",
            "text": (
                '{"DeviceId":"ZAP000000","DeviceType":4,'
                '"ChargerId":"xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx","StateId":554,'
                '"Timestamp":"2023-08-03T00:00:00.0617Z","ValueAsString":"OCMF|'
                '{\\"FV\\":\\"1.0\\",\\"GI\\":\\"ZAPTEC GO\\",'
                '\\"GS\\":\\"ZAP000000\\",\\"GV\\":\\"2.1.0.4\\",\\"PG\\":\\"F1\\",'
                '\\"RD\\":[{\\"TM\\":\\"2023-08-03T00:00:00,000+00:00 R\\",'
                '\\"RV\\":179.715,\\"RI\\":\\"1-0:1.8.0\\",\\"RU\\":\\"kWh\\",'
                '\\"RT\\":\\"AC\\",\\"ST\\":\\"G\\"}]}"}'
            ),
            "xmlns": "http://schemas.microsoft.com/2003/10/Serialization/",
        }
    ]

    result = mc_nbfx_decoder(nbfx_message)
    assert result == expected_output, f"Expected {expected_output}, got {result}"


def test_utils_to_under() -> None:
    """Test the to_under function."""
    assert to_under("HelloWorld") == "hello_world"
    assert to_under("helloWorld") == "hello_world"
    assert to_under("Hello") == "hello"
    assert to_under("H") == "h"
    assert to_under("") == ""
    assert to_under("ThisIsATest") == "this_is_a_test"
    assert to_under("already_under") == "already_under"
    assert to_under("with123Numbers") == "with123_numbers"
    assert to_under("with_Special$Chars!") == "with_special$chars!"
