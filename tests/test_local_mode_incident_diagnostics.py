"""Regression classification for uncertain Local mode operations (offline only)."""
import pytest

from madoka_multiroom.local_modes import LocalControls, validate_mode_effect
from madoka_multiroom.backends.daikin import WriteNotConfirmed
from madoka_protocol import CMD_GET_MODE, CMD_GET_POWER, CMD_GET_SETPOINT, CMD_GET_FAN


def baseline():
    return {
        CMD_GET_MODE: {0x20: b"\x03", 0x21: b"\x40"},
        CMD_GET_POWER: {0x20: b"\x01"},
        CMD_GET_SETPOINT: {0x20: b"cool", 0x21: b"heat"},
        CMD_GET_FAN: {0x20: b"\x03", 0x21: b"\x05"},
    }


@pytest.mark.parametrize("mode,extension,classification,changed,observed", [
    (b"\x03", b"\x40", "mode_code_different", [], 3),
    (b"\x04", b"\x41", "mode_metadata_changed", ["0x20", "0x21"], 4),
    (b"\x04", b"\x40", None, [], None),
    (b"", b"\x40", "mode_readback_invalid", ["0x20"], None),
])
def test_mode_discrepancy_is_specific_and_never_accepted_without_proof(
        mode, extension, classification, changed, observed):
    original = baseline()
    after = {cmd: dict(objects) for cmd, objects in original.items()}
    after[CMD_GET_MODE] = {0x20: mode, 0x21: extension}
    if classification is None:
        validate_mode_effect(LocalControls(), 4, original, after)
        return
    with pytest.raises(WriteNotConfirmed) as caught:
        validate_mode_effect(LocalControls(), 4, original, after)
    error = caught.value
    assert error.reason == "mode_state_mismatch"
    assert error.fields == ["mode"]
    assert error.mode_detail == classification
    assert error.mode_observed == observed
    assert error.mode_changed_ids == changed
