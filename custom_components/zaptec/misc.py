"""Misc helper stuff."""
from __future__ import annotations

import re


def to_under(word: str) -> str:
    """helper to convert TurnOnThisButton to turn_on_this_button."""
    # Ripped from inflection
    word = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", word)
    word = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", word)
    word = word.replace("-", "_")
    return word.lower()


def mc_nbfx_decoder(msg: bytes) -> None:
    """Decoder of .NET Binary Format XML Data structures."""

    # https://learn.microsoft.com/en-us/openspecs/windows_protocols/mc-nbfx

    def data_producer(data: bytes):
        """Generator of data."""
        count = 0
        while data:
            block = data[:count]
            data = data[count:]
            count = yield block

    prod = data_producer(msg)
    next(prod)

    END_ELEMENT = 0x01
    SHORT_XMLNS_ATTRIBUTE = 0x08
    SHORT_ELEMENT = 0x40
    CHARS8TEXT = 0x98
    CHARS16TEXT = 0x9A

    def read_string(bits16=False):
        """Read a string."""
        if bits16:
            b = prod.send(2)
            length = b[0] + (b[1] << 8)
        else:
            length = prod.send(1)[0]
        return prod.send(length).decode("utf-8")

    def frame_decoder():
        """Decode the stream."""
        while True:
            try:
                record_type = prod.send(1)[0]
            except StopIteration:
                return

            if record_type in (
                SHORT_ELEMENT,
                SHORT_XMLNS_ATTRIBUTE,
                CHARS8TEXT,
                CHARS16TEXT,
            ):
                yield record_type, read_string(bits16=(record_type == CHARS16TEXT))
            elif record_type == END_ELEMENT:
                yield record_type, None
            else:
                raise AttributeError(f"Unknown record type {hex(record_type)}")

    root = []
    frame = frame_decoder()
    while True:
        try:
            record_type, text = next(frame)
        except StopIteration:
            return root

        # Build the composite object
        if record_type == SHORT_ELEMENT:
            element = {}
            element["name"] = text
            root.append(element)
            while True:
                record_type, text = next(frame)
                if record_type == SHORT_XMLNS_ATTRIBUTE:
                    element["xmlns"] = text
                elif record_type in (CHARS8TEXT, CHARS16TEXT):
                    element["text"] = text
                elif record_type == END_ELEMENT:
                    break
                else:
                    raise AttributeError(f"Unknown record type {hex(record_type)}")
        else:
            raise AttributeError(f"Unknown record type {hex(record_type)}")


if __name__ == "__main__":
    from pprint import pprint

    data = b'@\x06string\x083http://schemas.microsoft.com/2003/10/Serialization/\x98\xa5{"DeviceId":"ZAP000000","DeviceType":4,"ChargerId":"xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx","StateId":523,"Timestamp":"2023-07-28T09:07:19.23","ValueAsString":"0.000"}\x01'
    data = b'@\x06string\x083http://schemas.microsoft.com/2003/10/Serialization/\x9a\x87\x01{"DeviceId":"ZAP000000","DeviceType":4,"ChargerId":"xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx","StateId":554,"Timestamp":"2023-08-03T00:00:00.0617Z","ValueAsString":"OCMF|{\\"FV\\":\\"1.0\\",\\"GI\\":\\"ZAPTEC GO\\",\\"GS\\":\\"ZAP000000\\",\\"GV\\":\\"2.1.0.4\\",\\"PG\\":\\"F1\\",\\"RD\\":[{\\"TM\\":\\"2023-08-03T00:00:00,000+00:00 R\\",\\"RV\\":179.715,\\"RI\\":\\"1-0:1.8.0\\",\\"RU\\":\\"kWh\\",\\"RT\\":\\"AC\\",\\"ST\\":\\"G\\"}]}"}\x01'

    out = mc_nbfx_decoder(data)
    pprint(out)
