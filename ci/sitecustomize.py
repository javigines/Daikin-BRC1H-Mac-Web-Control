"""Fail offline jobs before importing a real Bluetooth driver.

Closed tests may inject a fake module into sys.modules; no installed Bleak module
can be resolved while this guard is installed, even if an environment flag is
temporarily removed to exercise the attended preflight with fictitious inputs.
"""
import importlib.abc
import sys


class OfflineBluetoothGuard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ("bleak", "CoreBluetooth", "Foundation", "objc") or fullname.startswith("bleak."):
            raise RuntimeError("Real Bluetooth imports are forbidden in offline CI")


sys.meta_path.insert(0, OfflineBluetoothGuard())
