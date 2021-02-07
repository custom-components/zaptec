"""misc helper stuff"""
import re


def to_under(word) -> str:
    """helper to convert TurnOnThisButton to turn_on_this_button."""
    # Ripped from inflection
    word = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", word)
    word = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", word)
    word = word.replace("-", "_")
    return word.lower()
