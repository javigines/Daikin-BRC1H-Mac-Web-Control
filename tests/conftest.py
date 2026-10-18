"""Never load a real BLE backend in the offline suite."""
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def no_real_ble(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Real BLE access forbidden in offline tests")

    monkeypatch.setitem(sys.modules, "bleak", SimpleNamespace(
        BleakClient=forbidden,
        BleakScanner=SimpleNamespace(find_device_by_address=forbidden, discover=forbidden),
    ))
