import json

import pytest
from pydantic import ValidationError

from madoka_multiroom.config import AppConfig, demo_config, load_config


@pytest.mark.parametrize("count", [0, 1, 6])
def test_zero_one_six_devices(count):
    config = demo_config().model_dump(mode="json")
    config["devices"] = config["devices"][:count]
    assert len(AppConfig.model_validate(config).devices) == count


@pytest.mark.parametrize("change", [
    {"id": "../room"}, {"id": ""}, {"id": "Room A"}, {"id": "room-2"},
    {"address": "not-a-corebluetooth-uuid"}, {"address": "00000000-0000-4000-8000-000000000002"},
    {"enabled": "true"}, {"name": " leading"}, {"name": "a\nroom"}, {"name": ""},
    {"queries": {"fields": []}}, {"queries": {"fields": ["power", "power"]}},
    {"queries": {"fields": ["unknown"]}}, {"queries": {"interval_seconds": float("nan")}},
    {"queries": {"timeout_seconds": 0}}, {"secret": "ignored?"}])
def test_invalid_or_duplicate_configuration_rejected(change):
    data = demo_config().model_dump(mode="json")
    data["devices"][0].update(change)
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


@pytest.mark.parametrize("change", [{"max_active_devices": 1}, {"max_concurrent_operations": 0},
                                      {"max_concurrent_operations": True}, {"stagger_seconds": -1},
                                      {"devices": [{}] * 7}, {"unexpected": 1}])
def test_coordination_limits_are_explicit(change):
    data = demo_config().model_dump(mode="json")
    data.update(change)
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_example_roundtrip_and_editable_name(tmp_path):
    config = load_config("config.example.json")
    data = config.model_dump(mode="json")
    data["devices"][0]["name"] = "Sala nueva"
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    updated = load_config(str(path))
    assert updated.devices[0].name == "Sala nueva"
    assert updated.devices[0].id == config.devices[0].id
    path.write_text("x" * 65537)
    with pytest.raises(ValueError, match="64 KiB"):
        load_config(str(path))
