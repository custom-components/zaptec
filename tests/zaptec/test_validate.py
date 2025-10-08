"""Tests for zaptec/validate.py."""

import logging

import pytest

from custom_components.zaptec.zaptec.validate import ValidationError, validate

_LOGGER = logging.getLogger(__name__)


def test_failing_and_missing_validation() -> None:
    """Trigger the validation failing and parts of the code that test_api doesn't trigger."""
    with pytest.raises(ValidationError) as excinfo:
        validate({}, "installation")
    assert excinfo.type == ValidationError

    validate({}, "")
    validate({}, "installation/123456-abcdef-0/update")
