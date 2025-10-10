"""Tests for redact.py in the Zaptec custom component."""

from __future__ import annotations

import pytest

from custom_components.zaptec.zaptec.redact import Redactor
from custom_components.zaptec.zaptec.zconst import ZCONST


def test_call_no_redact_returns_original() -> None:
    """Test that __call__ returns the original object when do_redact=False."""
    no_redactor = Redactor(do_redact=False)
    no_redactor.add("secret_value", key="KeyName", ctx="context")
    data = {"KeyName": "secret_value"}
    assert no_redactor(data) == data

    redactor = Redactor(do_redact=True)
    redactor.add("secret_value", key="KeyName", ctx="context")
    data = {"KeyName": "secret_value"}
    assert redactor(data) != data


@pytest.fixture
def redactor(zaptec_constants: dict) -> Redactor:
    """Make sure zconst is properly initialized and return initialize redactor."""
    ZCONST.clear()
    ZCONST.update(zaptec_constants)
    ZCONST.update_ids_from_schema(None)
    return Redactor(do_redact=True)


def test_add_and_dumps_creates_entries(redactor: Redactor) -> None:
    """Test that add() and dumps() correctly register and output redactions."""
    replacement = redactor.add("secret_value", key="KeyName", ctx="context")

    assert "Redact" in replacement
    assert "secret_value" in redactor.redacts
    assert replacement in redactor.redact_info

    dump_output = redactor.dumps()
    assert "secret_value" in dump_output


def test_add_uid_uses_uid_tail(redactor: Redactor) -> None:
    """Test that add_uid() produces correct placeholder with UID suffix."""
    uid = "1234567890abcdef"

    replacement = redactor.add_uid(uid, "User", ctx="ctx")
    assert replacement.startswith("<--User[")
    assert uid[-6:] in replacement
    assert replacement in redactor.redact_info


def test_call_handles_lists_and_tuples(redactor: Redactor) -> None:
    """Test that lists, tuples, and sets are iterated and redacted."""
    result = redactor(["a", "b"], key="Address", ctx="ctx")
    assert isinstance(result, list)

    result_tuple = redactor(("a", "b"), key="City", ctx="ctx")
    assert isinstance(result_tuple, list)

    result_set = redactor({"a", "b"}, key="City", ctx="ctx")
    assert isinstance(result_set, list)


def test_call_handles_dict_and_second_pass(redactor: Redactor) -> None:
    """Test that dictionaries are processed and redacted correctly."""
    needs_redaction = "123 main"
    obj = {"Address": needs_redaction, "Other": "notredacted"}
    result = redactor(obj)
    assert any("Redact" in str(v) for v in result.values())
    assert not any(needs_redaction in str(v) for v in result.values())

    nested = {"Address": {"City": "A"}}
    result_2 = redactor(nested, second_pass=True)
    assert isinstance(result_2, dict)


def test_call_existing_redacted_reused(redactor: Redactor) -> None:
    """Test that previously redacted values are reused."""
    replacement = redactor.add("foo", ctx="ctx")
    assert redactor("foo") == replacement


def test_call_never_redact_values(redactor: Redactor) -> None:
    """Test that values in NEVER_REDACT are not redacted."""
    for val in redactor.NEVER_REDACT:
        out = redactor(val, key="Address")
        assert out == val


def test_call_string_containing_redacted_replacement(redactor: Redactor) -> None:
    """Test that strings containing previously redacted substrings are replaced."""
    replacement = redactor.add("secret", ctx="ctx")
    text = "prefix secret suffix"

    result = redactor(text)
    assert replacement in result
    assert "secret" not in result


def test_call_no_redact_case(redactor: Redactor) -> None:
    """Test that non-sensitive keys are not redacted."""
    val = "notredacted"
    result = redactor(val, key="NonSensitive")
    assert result == val


def test_redact_statelist_redacts_when_keyv_in_redact_keys(redactor: Redactor) -> None:
    """Test that redact_statelist() redacts correctly when observation matches."""
    # 950=MacMain is one of the states that needs redaction
    objs = [{"StateId": 950, "Value": "abc"}]
    out = redactor.redact_statelist(objs, ctx="ctx")

    assert out[0]["StateId"] == "950 (MacMain)"
    assert "<--" in out[0]["Value"]
    assert "abc" not in out[0]["Value"]

    # 702=ChargeMode is not one of the states that needs redaction
    objs_2 = [{"StateId": 702, "Value": "should_stay"}]
    out_2 = redactor.redact_statelist(objs_2)
    assert out_2[0]["StateId"] == "702 (ChargeMode)"
    assert out_2[0]["Value"] == "should_stay"

    objs_3 = [{"OtherKey": 5}]
    assert redactor.redact_statelist(objs_3) == objs_3

    unknown_stateid = 5
    obj_4 = [{"StateId": unknown_stateid, "Value": "unknown_stateid_should_stay"}]
    obj_4 = redactor.redact_statelist(obj_4)
    assert obj_4[0]["StateId"] == unknown_stateid
    assert obj_4[0]["Value"] == "unknown_stateid_should_stay"


def test_redact_statelist_handles_value_as_string(redactor: Redactor) -> None:
    """Test that redact_statelist() handles ValueAsString fields correctly."""
    # 950=MacMain is one of the states that needs redaction
    objs = [{"StateId": 950, "ValueAsString": "abc"}]
    out = redactor.redact_statelist(objs, ctx="ctx")

    assert out[0]["StateId"] == "950 (MacMain)"
    assert "<--" in out[0]["ValueAsString"]
    assert "abc" not in out[0]["ValueAsString"]

    # 702=ChargeMode is not one of the states that needs redaction
    objs_2 = [{"StateId": 702, "ValueAsString": "should_stay"}]
    out_2 = redactor.redact_statelist(objs_2)
    assert out_2[0]["StateId"] == "702 (ChargeMode)"
    assert out_2[0]["ValueAsString"] == "should_stay"

    objs_3 = [{"OtherKey": 5}]
    assert redactor.redact_statelist(objs_3) == objs_3

    unknown_stateid = 5
    obj_4 = [{"StateId": unknown_stateid, "ValueAsString": "unknown_stateid_should_stay"}]
    obj_4 = redactor.redact_statelist(obj_4)
    assert obj_4[0]["StateId"] == unknown_stateid
    assert obj_4[0]["ValueAsString"] == "unknown_stateid_should_stay"
