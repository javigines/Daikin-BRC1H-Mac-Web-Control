"""Synthetic, connection-free fan evidence analysis; no capability grants."""
import copy
import json
import subprocess
import sys

import pytest

from madoka_multiroom.fan_observation import classify_fan_response, main


@pytest.mark.parametrize("mode,objects,channel,code", [
    (3, {0x20: b"\x03", 0x21: b"\xff"}, "cooling", 3),
    (4, {0x20: b"\xff", 0x21: b"\x05"}, "heating", 5),
    (3, {0x20: b"\x01"}, "cooling", 1),
    (4, {0x21: b"\x02"}, "heating", 2),
])
def test_legible_active_field_survives_unknown_or_absent_inactive_field(mode, objects, channel, code):
    report = classify_fan_response(objects, mode)
    assert report.response_status == "observed"
    assert report.active_channel == channel
    assert report.active.code == code and report.active.readable
    assert not report.legacy_pair_readable
    assert report.diagnostic()["physical_capability"] == "not_established"
    assert report.diagnostic()["write_authority"] == "not_established"


@pytest.mark.parametrize("code", range(1, 6))
def test_each_protocol_manual_code_is_observed_without_granting_distinct_levels(code):
    report = classify_fan_response({0x20: bytes([code]), 0x21: b"\x03"}, 3)
    assert report.cooling.status == "manual_observed" and report.cooling.code == code
    assert report.legacy_pair_readable
    assert "supported_levels" not in report.diagnostic()


def test_auto_is_a_public_protocol_observation_but_does_not_pass_runtime_gate():
    report = classify_fan_response({0x20: b"\x00", 0x21: b"\x03"}, 3)
    assert report.active.status == "automatic_observed" and report.active.code == 0
    assert report.active.readable and not report.legacy_pair_readable
    assert report.diagnostic()["write_authority"] == "not_established"


@pytest.mark.parametrize("value,reason", [
    (b"\xff", "unrecognized_code"), (b"\x06", "unrecognized_code"),
    (b"", "invalid_width"), (b"\x01\x00", "invalid_width"),
    (bytearray([1]), "invalid_type"), (1, "invalid_type"), (None, "invalid_type"),
])
def test_unknowns_are_never_coerced_to_auto_or_unavailable(value, reason):
    report = classify_fan_response({0x20: value, 0x21: b"\x03"}, 3)
    assert report.cooling.status == "unknown" and report.cooling.reason == reason
    assert report.cooling.code is None and not report.active.readable
    assert report.heating.status == "manual_observed"
    assert not report.legacy_pair_readable


@pytest.mark.parametrize("mode", [0, 1, 2, 5])
def test_policy_restriction_is_not_physical_incompatibility(mode):
    report = classify_fan_response({0x20: b"\x03", 0x21: b"\x05"}, mode)
    assert report.mode_policy == "not_admitted_by_current_fan_policy"
    assert report.active is None and report.legacy_pair_readable
    assert report.diagnostic()["physical_capability"] == "not_established"


@pytest.mark.parametrize("mode", [None, True, "3", 6, -1, []])
def test_unknown_mode_never_selects_active_channel(mode):
    report = classify_fan_response({0x20: b"\x03", 0x21: b"\x05"}, mode)
    assert report.mode_policy == "unknown_mode" and report.active is None


def test_not_queried_ignores_stale_fields_and_missing_response_is_distinct():
    stale = classify_fan_response({0x20: b"\x03", 0x21: b"\x05"}, 3, queried=False)
    missing = classify_fan_response(None, 3)
    absent = classify_fan_response({}, 3)
    assert stale.response_status == stale.cooling.status == stale.heating.status == "not_queried"
    assert stale.cooling.code is None
    assert missing.response_status == missing.cooling.reason == "response_missing"
    assert absent.response_status == "observed" and absent.cooling.reason == "field_missing"
    assert absent.cooling.status == missing.cooling.status == "not_observed"


def test_valid_extensions_are_counted_without_echo_or_effect_on_known_fields():
    objects = {0x20: b"\x03", 0x21: b"\x05", 0x7E: b"synthetic-extension"}
    original = copy.deepcopy(objects)
    report = classify_fan_response(objects, 3)
    assert report.legacy_pair_readable and report.extension_count == 1
    assert objects == original
    assert "synthetic-extension" not in json.dumps(report.diagnostic())
    assert "126" not in json.dumps(report.diagnostic())


@pytest.mark.parametrize("objects", [[], "raw", {"0x20": b"\x03"}, {True: b"\x03"},
                                          {256: b"\x03"}, {0x20: b"\x03", 0x7E: "raw"}])
def test_malformed_response_does_not_produce_usable_observation(objects):
    report = classify_fan_response(objects, 3)
    assert report.response_status == "malformed"
    assert report.active.status == "unknown" and not report.active.readable
    assert not report.legacy_pair_readable


def test_six_synthetic_controllers_are_independent_and_analysis_never_mutates_input():
    objects = [{0x20: bytes([code]), 0x21: b"\x03"} for code in (1, 2, 3, 4, 5, 0)]
    original = copy.deepcopy(objects)
    reports = [classify_fan_response(response, 3) for response in objects]
    changed = dict(objects[0])
    changed[0x20] = b"\x05"
    updated = classify_fan_response(changed, 3)
    assert updated.cooling.code == 5 and reports[0].cooling.code == 1
    assert [report.cooling.code for report in reports[1:]] == [2, 3, 4, 5, 0]
    assert objects == original


def test_cli_analyzes_only_explicit_export_with_six_synthetic_records(tmp_path, capsys):
    path = tmp_path / "synthetic-fan.json"
    path.write_text(json.dumps([
        {"mode": 3, "queried": True, "objects": {"0x20": f"{code:02x}", "0x21": "03"}}
        for code in (1, 2, 3, 4, 5, 0)
    ]))
    assert main([str(path)]) == 0
    reports = json.loads(capsys.readouterr().out)
    assert len(reports) == 6
    assert [report["cooling"]["code"] for report in reports] == [1, 2, 3, 4, 5, 0]
    assert all(report["write_authority"] == "not_established" for report in reports)


@pytest.mark.parametrize("content", [
    "private-synthetic-invalid-json", '{"inventory":"private-synthetic-inventory"}',
    '[{"mode":3,"queried":true,"objects":{"0x20":"zz"}}]',
    '[{"mode":3,"queried":true,"objects":{},"identifier":"private-synthetic-id"}]',
    '[{"mode":3,"queried":true,"objects":{"0x2a":"01","0x2A":"02"}}]',
    '[{"mode":3,"queried":true,"objects":{"0x20":"01","0x20":"02"}}]',
    '[{"mode":3,"queried":true,"objects":{"0x+1":"01"}}]',
])
def test_cli_rejects_malformed_or_nonminimal_exports_without_data_leak(tmp_path, capsys, content):
    path = tmp_path / "private-synthetic-path.json"
    path.write_text(content)
    assert main([str(path)]) == 2
    assert json.loads(capsys.readouterr().out) == {"error": "invalid_or_unreadable_fan_export"}


def test_cli_unreadable_export_does_not_echo_path(tmp_path, capsys):
    assert main([str(tmp_path / "private-synthetic-path.json")]) == 2
    assert "private" not in capsys.readouterr().out


def test_cli_can_run_with_ble_and_sqlite_imports_forbidden(tmp_path):
    path = tmp_path / "synthetic-fan.json"
    path.write_text('[{"mode":3,"queried":true,"objects":{"0x20":"03","0x21":"ff"}}]')
    result = subprocess.run([sys.executable, "-c", """
import runpy, sys
sys.modules['bleak'] = None
sys.modules['sqlite3'] = None
sys.argv = ['fan_observation', sys.argv[1]]
runpy.run_module('madoka_multiroom.fan_observation', run_name='__main__')
""", str(path)], capture_output=True, text=True, check=True)
    report, = json.loads(result.stdout)
    assert report["active_readable"] and not report["legacy_pair_readable"]
    assert report["heating"]["status"] == "unknown"
