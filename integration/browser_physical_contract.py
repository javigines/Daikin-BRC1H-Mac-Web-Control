"""Chromium -> real HTTP -> six closed wire models -> hardened Controller -> SQLite.

This is an offline test harness, never a physical server or runtime factory.
"""
import importlib.abc
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class NoBleak(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith("bleak"):
            raise RuntimeError("Real BLE import forbidden in offline contract harness")


sys.meta_path.insert(0, NoBleak())


def serve(directory, port):
    import uvicorn
    from madoka_multiroom.api import build_demo_app
    from madoka_multiroom.config import demo_config
    from madoka_multiroom.backends.controller_bridge import ScriptedRadio
    from madoka_multiroom.backends.physical_fleet import ScriptedPhysicalFleet
    from madoka_multiroom.backends.physical_sessions import PhysicalSessionBudget, ConnectionPlan
    config = demo_config()
    budget = PhysicalSessionBudget(Path(directory) / "sessions.sqlite", ConnectionPlan("retained", 6, 2))
    fleet = ScriptedPhysicalFleet(config, budget,
        {d.id: ScriptedRadio(d.address) for d in config.devices},
        initial_admissions=tuple(d.id for d in config.devices))
    try:
        uvicorn.run(build_demo_app(config, str(Path(directory) / "history.sqlite"), offline_fleet=fleet),
                    host="127.0.0.1", port=port, workers=1, log_level="critical", proxy_headers=False)
    finally:
        budget.close_storage()


def main():
    from playwright.sync_api import sync_playwright, expect
    with tempfile.TemporaryDirectory(prefix="madoka-contract-browser-") as directory:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        process = subprocess.Popen([sys.executable, __file__, "--serve-scripted", directory, str(port)],
                                   cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    with urlopen(base + "/api/controllers", timeout=.3) as response:
                        if all(r["connected"] for r in json.load(response)["controllers"]):
                            break
                except (OSError, TimeoutError):
                    pass
                if time.monotonic() > deadline or process.poll() is not None:
                    raise RuntimeError("Offline wire fleet did not become ready")
                time.sleep(.05)
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page(viewport={"width":1440,"height":1100})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base)
                expect(page.locator(".room")).to_have_count(6)
                for i in range(1, 7):
                    card = page.locator(f'.room[data-id="room-{i}"]')
                    expect(card.locator(".increase")).to_be_enabled()
                    card.locator(".increase").click()
                    expect(card.locator(".setpoint")).to_have_text("23°")
                    card.locator(".details").click()
                    detail = page.locator("#detail-content")
                    detail.locator(".mode").select_option(str(i % 6))
                    expect(detail.locator(".mode")).to_be_enabled()
                    detail.locator(".fan").select_option(str(i % 5 + 1))
                    expect(detail.locator(".fan")).to_be_enabled()
                    detail.locator(".power").click()
                    expect(detail.locator(".power")).to_be_enabled()
                    detail.locator(".history").click()
                    expect(page.locator("#history-rows tr").first).to_be_visible()
                    page.locator("#close-history").click()
                    page.locator("#close-detail").click()
                rooms = page.request.get(base + "/api/controllers").json()["controllers"]
                assert [r["state"]["setpoint"] for r in rooms] == [23] * 6
                assert [r["state"]["mode_id"] for r in rooms] == [i % 6 for i in range(1, 7)]
                assert all(r["state"]["power_on"] is False for r in rooms)
                assert [r["state"]["fan_id"] for r in rooms] == [i % 5 + 1 for i in range(1, 7)]
                assert all(r["transport"] == "offline-controller-contract" and not r["hardware_enabled"]
                           and not r["physical_enforce_enabled"] for r in rooms)
                page.set_viewport_size({"width":390,"height":844})
                expect(page.locator(".room").first).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                page.locator('.room[data-id="room-6"] .details').click()
                expect(page.locator("#detail-content .history")).to_be_visible()
                page.locator("#close-detail").click()
                assert errors == [], errors
                browser.close()
            print("PASS Chromium wire contract: six identities/four commands/readback/history, desktop/mobile, 0 JS errors; Bleak imports forbidden")
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--serve-scripted":
        serve(sys.argv[2], int(sys.argv[3]))
    elif len(sys.argv) == 1:
        main()
    else:
        raise SystemExit("Offline harness accepts no hardware configuration")
