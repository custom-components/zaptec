"""Zaptec utilities."""

from __future__ import annotations

from collections.abc import Generator
import re

# Precompile the patterns for performance
RE_TO_UNDER1 = re.compile(r"([A-Z]+)([A-Z][a-z])")
RE_TO_UNDER2 = re.compile(r"([a-z\d])([A-Z])")


def to_under(word: str) -> str:
    """Convert TurnOnThisButton to turn_on_this_button."""
    # Ripped from inflection
    word = RE_TO_UNDER1.sub(r"\1_\2", word)
    word = RE_TO_UNDER2.sub(r"\1_\2", word)
    word = word.replace("-", "_")
    return word.lower()


# This Generator[] cannot be shortened to the new 3.13 style, since it use
# values from send(). Ignoring UP043
def data_producer(data: bytes) -> Generator[bytes, int, None]:  # noqa: UP043
    """Generate data segments from input bytes.

    Use successive send(size) to get the next block of 'size' bytes.
    """
    size = 0
    first = True  # Make sure the first next() goes through even with empty data
    while first or data:
        first = False
        block = data[:size]
        data = data[size:]
        # Send the block of data of size bytes
        size = yield block


# NBFX Record Types
END_ELEMENT = 0x01
SHORT_XMLNS_ATTRIBUTE = 0x08
SHORT_ELEMENT = 0x40
CHARS8TEXT = 0x98
CHARS16TEXT = 0x9A


def mc_nbfx_decoder(msg: bytes) -> list[dict[str, str]]:
    """Decode .NET Binary Format XML Data structures."""

    # https://learn.microsoft.com/en-us/openspecs/windows_protocols/mc-nbfx

    prod = data_producer(msg)
    next(prod)

    def read_string(record_type: int) -> str:
        """Read a string."""
        if record_type == CHARS16TEXT:
            b = prod.send(2)
            length = b[0] + (b[1] << 8)
        else:
            length = prod.send(1)[0]
        return prod.send(length).decode("utf-8")

    def frame_decoder() -> Generator[tuple[int, str | None]]:
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
                yield record_type, read_string(record_type)
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


def get_ocmf_max_reader_value(data: dict) -> float:
    """Return the maximum reader value from OCMF data."""

    if not isinstance(data, dict):
        return 0.0
    rds = data.get("RD", [])
    if not rds:
        return 0.0
    return max(float(reading.get("RV", 0.0)) for reading in rds)
