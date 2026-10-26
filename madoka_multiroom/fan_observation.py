"""Offline GET_FAN analysis; no device capabilities or write permission inferred.

This module deliberately has no runtime/backend integration. It can inspect an
explicit export of already parsed GET_FAN objects without importing BLE or a
ledger. Codes describe protocol observations, not distinct physical speeds.
"""
from collections.abc import Mapping
from dataclasses import asdict, dataclass
import argparse
import json
from pathlib import Path


@dataclass(frozen=True)
class FanChannel:
    status: str
    reason: str | None = None
    code: int | None = None

    @property
    def readable(self):
        return self.status in ("manual_observed", "automatic_observed")


@dataclass(frozen=True)
class FanObservation:
    response_status: str
    cooling: FanChannel
    heating: FanChannel
    active_channel: str | None
    mode_policy: str
    extension_count: int

    @property
    def active(self):
        return getattr(self, self.active_channel) if self.active_channel else None

    @property
    def legacy_pair_readable(self):
        """Whether the existing runtime's two-manual-code gate would pass."""
        return all(channel.status == "manual_observed"
                   for channel in (self.cooling, self.heating))

    def diagnostic(self):
        """Return structural findings, never raw objects or device identifiers."""
        return {
            **asdict(self),
            "active_readable": bool(self.active and self.active.readable),
            "legacy_pair_readable": self.legacy_pair_readable,
            "physical_capability": "not_established",
            "write_authority": "not_established",
        }


def _channel(objects, key):
    if key not in objects:
        return FanChannel("not_observed", "field_missing")
    value = objects[key]
    if type(value) is not bytes:
        return FanChannel("unknown", "invalid_type")
    if len(value) != 1:
        return FanChannel("unknown", "invalid_width")
    code = value[0]
    if code == 0:
        return FanChannel("automatic_observed", code=code)
    if 1 <= code <= 5:
        return FanChannel("manual_observed", code=code)
    return FanChannel("unknown", "unrecognized_code")


def classify_fan_response(objects, mode, *, queried=True):
    """Classify cooling/heating independently without weakening runtime gates.

    Only Cool (3) and Heat (4) select an active field under the current software
    policy. Other protocol modes are not proof of a physically incompatible
    fan. In particular, GET alone cannot establish unavailable speed, write
    support, permission, or the number of distinct physical fan levels.
    """
    if type(queried) is not bool:
        raise ValueError("queried must be boolean")
    active_channel = {3: "cooling", 4: "heating"}.get(mode) if type(mode) is int else None
    if active_channel:
        mode_policy = "admitted_by_current_fan_policy"
    elif type(mode) is int and mode in (0, 1, 2, 5):
        mode_policy = "not_admitted_by_current_fan_policy"
    else:
        mode_policy = "unknown_mode"
    if not queried:
        channel = FanChannel("not_queried")
        return FanObservation("not_queried", channel, channel, active_channel, mode_policy, 0)
    if objects is None:
        channel = FanChannel("not_observed", "response_missing")
        return FanObservation("response_missing", channel, channel, active_channel, mode_policy, 0)
    if not isinstance(objects, Mapping) or any(type(key) is not int or not 0 <= key <= 255
                                             for key in objects):
        channel = FanChannel("unknown", "invalid_response_structure")
        return FanObservation("malformed", channel, channel, active_channel, mode_policy, 0)
    extension_count = sum(key not in (0x20, 0x21) for key in objects)
    # A malformed extension is not silently accepted as a parsed GET response.
    if any(type(value) is not bytes for key, value in objects.items() if key not in (0x20, 0x21)):
        channel = FanChannel("unknown", "invalid_response_structure")
        return FanObservation("malformed", channel, channel, active_channel, mode_policy, extension_count)
    return FanObservation("observed", _channel(objects, 0x20), _channel(objects, 0x21),
                          active_channel, mode_policy, extension_count)


def _export_record(record):
    """Read only the explicit minimal export schema; do not accept inventories."""
    if type(record) is not dict or set(record) != {"mode", "queried", "objects"}:
        raise ValueError("invalid export record")
    if type(record["mode"]) is not int or type(record["queried"]) is not bool:
        raise ValueError("invalid export metadata")
    objects = record["objects"]
    if objects is not None:
        if type(objects) is not dict:
            raise ValueError("invalid exported objects")
        decoded = {}
        for key, value in objects.items():
            if (type(key) is not str or len(key) != 4 or not key.startswith("0x")
                    or any(char not in "0123456789abcdefABCDEF" for char in key[2:])
                    or type(value) is not str):
                raise ValueError("invalid exported object")
            object_id = int(key[2:], 16)
            if object_id in decoded:
                raise ValueError("duplicate exported object")
            decoded[object_id] = bytes.fromhex(value)
        objects = decoded
    return classify_fan_response(objects, record["mode"], queried=record["queried"]).diagnostic()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate exported field")
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path, help="explicit minimal GET_FAN JSON export")
    args = parser.parse_args(argv)
    try:
        records = json.loads(args.export.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        if type(records) is not list:
            raise ValueError("invalid export document")
        results = [_export_record(record) for record in records]
    except (OSError, ValueError, TypeError):
        # Do not echo paths, input excerpts, physical identifiers or raw data.
        print(json.dumps({"error": "invalid_or_unreadable_fan_export"}))
        return 2
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
