"""Actual browser -> HTTP -> six simulators -> SQLite. Optional offline E2E.
Run: python integration/browser_demo.py (after installing Playwright Chromium).
No real BLE, remote browser, backend substitution or external services.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from madoka_multiroom.config import demo_config


def main():
    with tempfile.TemporaryDirectory(prefix="madoka-browser-") as temp:
        config = demo_config().model_dump(mode="json")
        config["stagger_seconds"] = 0
        config["stale_seconds"] = 5.0
        config["devices"][0]["name"] = '<img src=x onerror="window.injected=true">'
        for device in config["devices"]:
            device["queries"]["interval_seconds"] = 2.0
        config_path = Path(temp) / "config.json"
        config_path.write_text(json.dumps(config))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        process = None
        def start():
            nonlocal process
            process = subprocess.Popen([sys.executable, "multiroom_app.py", "--demo", "--port", str(port),
                "--config", str(config_path), "--db", str(Path(temp) / "demo.db")],
                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    with urlopen(base + "/api/controllers", timeout=.3) as response:
                        data = json.load(response)
                        if all(r["connected"] for r in data["controllers"]):
                            return
                except (URLError, TimeoutError, ConnectionError):
                    pass
                if process.poll() is not None:
                    raise RuntimeError("DEMO process failed to start")
                time.sleep(.05)
            raise RuntimeError("DEMO did not become ready")
        def stop():
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        try:
            start()
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 1100})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base)
                expect(page.locator(".room")).to_have_count(6)
                expect(page.locator(".room-name").first).to_have_text(config["devices"][0]["name"])
                assert page.evaluate("window.injected") is None
                assert page.locator(".room-name img").count() == 0
                def card(i):
                    return page.locator(f'.room[data-id="room-{i}"]')
                for i in range(1, 7):
                    room = card(i)
                    expect(room.locator(".increase")).to_be_enabled()
                    room.locator(".increase").click()
                    expect(room.locator(".setpoint")).to_have_text("23°")
                    room.locator(".mode").select_option(str(i % 6))
                    expect(room.locator(".mode")).to_be_enabled()
                    room.locator(".fan").select_option(str(i % 5 + 1))
                    expect(room.locator(".fan")).to_be_enabled()
                    room.locator(".power").click()
                    expect(room.locator(".power")).to_be_enabled()
                data = page.request.get(base + "/api/controllers").json()["controllers"]
                assert [r["state"]["setpoint"] for r in data] == [23] * 6
                assert [r["state"]["mode_id"] for r in data] == [i % 6 for i in range(1, 7)]
                assert [r["state"]["fan_id"] for r in data] == [i % 5 + 1 for i in range(1, 7)]
                # Three policies through actual admin forms; external change never updates desired.
                for i, mode in [(1, "FREE"), (2, "MONITOR"), (3, "ENFORCE")]:
                    room = card(i)
                    room.locator(".protection-admin summary").click()
                    room.locator(".policy-mode").select_option(mode)
                    room.locator('.protected-fields input[value="setpoint"]').check()
                    room.locator(".stabilization").fill("1")
                    room.locator(".cooldown").fill("1")
                    room.locator(".save-policy").click()
                    expect(room.locator(".save-policy")).to_be_enabled()
                    expect(room.locator(".policy-badge")).to_have_text(mode)
                    room.locator(".external summary").click()
                    room.locator(".external-temp").fill("20")
                    room.locator(".apply-external").click()
                    expect(room.locator(".apply-external")).to_be_enabled()
                    if mode == "ENFORCE":
                        expect(room.locator(".setpoint")).to_have_text("23°", timeout=8000)
                    else:
                        expect(room.locator(".setpoint")).to_have_text("20°")
                expect(card(2).locator(".deviations")).to_contain_text("aviso")
                expect(card(1).locator(".deviations")).to_contain_text("aceptada")
                card(3).locator(".events").click()
                expect(page.locator("#events-list")).to_contain_text("correction_observed")
                page.locator("#close-events").click()
                card(6).locator(".history").click()
                expect(page.locator("#history-rows tr").first).to_be_visible()
                expect(page.locator("#history-title")).to_contain_text("Oficina norte")
                page.locator("#close-history").click()
                # One room fails and recovers, while other controls remain usable.
                room = card(4)
                room.locator(".simulation:not(.external) summary").click()
                room.locator(".sim-status").select_option("error")
                room.locator(".apply-simulation").click()
                expect(room.locator(".power")).to_be_disabled()
                expect(card(5).locator(".power")).to_be_enabled()
                room.locator(".sim-status").select_option("online")
                room.locator(".apply-simulation").click()
                expect(room.locator(".power")).to_be_enabled()
                expect(room.locator(".deviations")).to_contain_text("STOP")
                # Browser remains open over restart: polling gets a fresh mutation token.
                old_token = page.request.get(base + "/api/session").json()["token"]
                stop()
                expect(card(5).locator(".power")).to_be_disabled(timeout=8000)
                start()
                new_token = page.request.get(base + "/api/session").json()["token"]
                assert old_token != new_token
                expect(card(5).locator(".power")).to_be_enabled(timeout=8000)
                card(5).locator(".increase").click()
                expect(card(5).locator(".setpoint")).to_have_text("23°")
                expect(card(5).locator(".room-error")).to_have_text("")
                # Distinct configurable stale threshold, even when HTTP keeps succeeding.
                page.route("**/api/controllers", lambda route: route.fulfill(json={"controllers": []}))
                expect(card(5).locator(".power")).to_be_disabled(timeout=8000)
                page.unroute("**/api/controllers")
                expect(card(5).locator(".power")).to_be_enabled(timeout=8000)
                # Local failure status is rendered as text, including hostile strings.
                page.route("**/api/controllers/room-6/power", lambda route: route.fulfill(status=503,
                    json={"detail": '<img src=x onerror="window.injected=true">'}))
                card(6).locator(".power").click()
                expect(card(6).locator(".room-error")).to_contain_text("<img")
                assert card(6).locator(".room-error img").count() == 0
                page.unroute("**/api/controllers/room-6/power")
                # Collapse admin/lab panels for overview screenshots.
                page.evaluate("document.querySelectorAll('details').forEach(el => el.open = false)")
                output = ROOT / "artifacts"
                output.mkdir(exist_ok=True)
                page.screenshot(path=str(output / "demo-desktop.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                card(3).locator(".protection-admin summary").click()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output / "demo-mobile.png"), full_page=True)
                assert errors == [], errors
                browser.close()
                print("PASS browser: six rooms/all controls, FREE/MONITOR/ENFORCE, events/history, error isolation/recovery, restart token, stale5s, XSS, desktop/mobile; 0 JS errors")
        finally:
            stop()


if __name__ == "__main__":
    main()
