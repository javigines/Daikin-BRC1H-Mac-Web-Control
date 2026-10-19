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
                # Navigation before room data arrives must hydrate the active view.
                for section in ("admin","lab"):
                    early = browser.new_page()
                    early.route("**/api/controllers",lambda route: route.fulfill(json={"controllers":[]}))
                    early.goto(base)
                    early.locator(f'[data-view="{section}"]').click()
                    early.unroute("**/api/controllers")
                    expect(early.locator(f'#{section}-content .selected-room')).to_have_text(config["devices"][0]["name"],timeout=8000)
                    early.close()
                page.goto(base)
                expect(page.locator(".room")).to_have_count(6)
                expect(page.locator(".room-name").first).to_have_text(config["devices"][0]["name"])
                assert page.evaluate("window.injected") is None
                assert page.locator(".room-name img").count() == 0
                def card(i):
                    return page.locator(f'.room[data-id="room-{i}"]')
                def nav(name):
                    page.locator(f'[data-view="{name}"]').click()
                def choose(section, i):
                    nav(section)
                    page.locator(f'#{section}-room').select_option(f"room-{i}")
                def confirm(reason="Owner reviewed offline test"):
                    page.locator("#confirm-reason").fill(reason)
                    page.locator("#accept-confirm").click()
                # Every existing control remains reachable through the detail.
                for i in range(1, 7):
                    room = card(i)
                    expect(room.locator(".increase")).to_be_enabled()
                    room.locator(".increase").click()
                    expect(room.locator(".setpoint")).to_have_text("23°")
                    room.locator(".details").click()
                    detail = page.locator("#detail-content")
                    detail.locator(".mode").select_option(str(i % 6))
                    expect(detail.locator(".mode")).to_be_enabled()
                    detail.locator(".fan").select_option(str(i % 5 + 1))
                    expect(detail.locator(".fan")).to_be_enabled()
                    detail.locator(".power").click()
                    expect(detail.locator(".power")).to_be_enabled()
                    expect(detail.locator(".updated")).to_contain_text("Última lectura")
                    if i == 6:
                        detail.locator(".history").click()
                        expect(page.locator("#history-rows tr").first).to_be_visible()
                        expect(page.locator("#history-title")).to_contain_text("Oficina norte")
                        page.locator("#close-history").click()
                    page.locator("#close-detail").click()
                data = page.request.get(base + "/api/controllers").json()["controllers"]
                assert [r["state"]["setpoint"] for r in data] == [23] * 6
                assert [r["state"]["mode_id"] for r in data] == [i % 6 for i in range(1, 7)]
                assert [r["state"]["fan_id"] for r in data] == [i % 5 + 1 for i in range(1, 7)]
                assert page.locator(".room select,.room input,.room details").count() == 0
                for i, mode in [(1,"FREE"),(2,"MONITOR"),(3,"ENFORCE")]:
                    choose("admin",i)
                    admin = page.locator("#admin-content")
                    admin.locator(".policy-mode").select_option(mode)
                    admin.locator('.protected-fields input[value="setpoint"]').check()
                    admin.locator(".advanced summary").click()
                    assert not admin.locator(".allow-auto-on").is_checked()
                    admin.locator(".stabilization").fill("1")
                    admin.locator(".cooldown").fill("1")
                    admin.locator(".save-policy").click()
                    expect(admin.locator(".save-policy")).to_be_enabled()
                    choose("lab",i)
                    lab = page.locator("#lab-content")
                    lab.locator(".external-temp").fill("20")
                    lab.locator(".apply-external").click()
                    expect(lab.locator(".apply-external")).to_be_enabled()
                    nav("rooms")
                    expect(card(i).locator(".setpoint")).to_have_text("23°" if mode == "ENFORCE" else "20°",timeout=8000)
                expect(card(2).locator(".protection-notice")).to_contain_text("Cambios detectados")
                expect(card(1).locator(".protection-notice")).to_be_hidden()
                choose("lab",3)
                page.locator(".events").click()
                expect(page.locator("#events-list")).to_contain_text("correction_observed")
                page.locator("#close-events").click()
                # Independent administrative actions require reasons and confirmation.
                choose("admin",3)
                admin = page.locator("#admin-content")
                admin.locator(".toggle-maintenance").click()
                page.locator("#cancel-confirm").click()
                assert not page.request.get(base+"/api/controllers/room-3").json()["protection"]["maintenance"]
                admin.locator(".toggle-maintenance").click()
                confirm()
                expect(admin.locator(".toggle-maintenance")).to_have_text("Finalizar mantenimiento")
                admin.locator(".suspend-seconds").fill("300")
                admin.locator(".suspend").click()
                confirm()
                expect(admin.locator(".restrictions-status")).to_contain_text("Suspensión hasta")
                admin.locator(".toggle-maintenance").click()
                # A remote suspension changed while the dialog was open must be
                # preserved; waiting for a reason must not extend its deadline.
                token = page.request.get(base+"/api/session").json()["token"]
                remote = page.request.post(base+"/api/controllers/room-3/restrictions",
                    headers={"X-Demo-Token":token},data={"maintenance":True,"suspend_seconds":180,"reason":"Other local admin changed suspension"}).json()
                deadline = remote["protection"]["suspended_until"]
                page.wait_for_timeout(1300)
                confirm()
                expect(admin.locator(".toggle-maintenance")).to_have_text("Activar mantenimiento")
                current = page.request.get(base+"/api/controllers/room-3").json()["protection"]
                assert abs(current["suspended_until"]-deadline) <= 1.1
                expect(admin.locator(".restrictions-status")).to_contain_text("Mantenimiento inactivo")
                expect(admin.locator(".restrictions-status")).to_contain_text("Suspensión hasta")
                admin.locator(".resume").click()
                confirm()
                expect(admin.locator(".restrictions-status")).to_contain_text("Sin suspensión")
                admin.locator(".reset-budget").click()
                confirm()
                expect(admin.locator(".reset-budget")).to_be_enabled()
                # Faults and disconnection isolate a room; recovery does not reset policy STOP.
                for status in ["error","disconnected"]:
                    choose("lab",4)
                    page.locator(".sim-status").select_option(status)
                    page.locator(".apply-simulation").click()
                    expect(page.locator(".apply-simulation")).to_be_enabled()
                    nav("rooms")
                    expect(card(4).locator(".power")).to_be_disabled()
                    expect(card(5).locator(".power")).to_be_enabled()
                    choose("lab",4)
                    page.locator(".sim-status").select_option("online")
                    page.locator(".apply-simulation").click()
                    expect(page.locator(".apply-simulation")).to_be_enabled()
                    nav("rooms")
                    expect(card(4).locator(".power")).to_be_enabled()
                    expect(card(4).locator(".protection-notice")).to_contain_text("detenida")
                choose("lab",5)
                page.locator(".sim-delay").fill("800")
                page.locator(".apply-simulation").click()
                expect(page.locator(".apply-simulation")).to_be_enabled()
                nav("rooms")
                card(5).locator(".power").click()
                expect(card(5)).to_have_attribute("aria-busy","true")
                expect(card(5).locator(".increase")).to_be_disabled()
                expect(card(6).locator(".increase")).to_be_enabled()
                expect(card(5)).to_have_attribute("aria-busy","false",timeout=8000)
                choose("lab",5)
                page.locator(".sim-delay").fill("0")
                page.locator(".apply-simulation").click()
                expect(page.locator(".apply-simulation")).to_be_enabled()
                nav("rooms")
                old_token = page.request.get(base+"/api/session").json()["token"]
                stop()
                expect(card(5).locator(".power")).to_be_disabled(timeout=8000)
                start()
                assert old_token != page.request.get(base+"/api/session").json()["token"]
                expect(card(5).locator(".power")).to_be_enabled(timeout=8000)
                card(5).locator(".increase").click()
                expect(card(5).locator(".setpoint")).to_have_text("23°")
                expect(card(5).locator(".operation-status")).to_have_text("")
                page.route("**/api/controllers",lambda route: route.fulfill(json={"controllers":[]}))
                expect(card(5).locator(".power")).to_be_disabled(timeout=8000)
                page.unroute("**/api/controllers")
                expect(card(5).locator(".power")).to_be_enabled(timeout=8000)
                page.route("**/api/controllers/room-6/power",lambda route: route.fulfill(status=503,json={"detail":'<img src=x onerror="window.injected=true">'}))
                card(6).locator(".power").click()
                expect(card(6).locator(".operation-status")).to_contain_text("<img")
                assert card(6).locator(".operation-status img").count() == 0
                page.unroute("**/api/controllers/room-6/power")
                # Keyboard entry, modal focus containment, Escape and focus return.
                button = card(2).locator(".details")
                button.focus()
                page.keyboard.press("Enter")
                expect(page.locator("#close-detail")).to_be_focused()
                page.keyboard.press("Tab")
                assert page.evaluate("document.activeElement.closest('#detail-dialog') !== null")
                page.keyboard.press("Escape")
                expect(button).to_be_focused()
                # Capture normal product content after hostile-string regression tests.
                stop()
                config["devices"][0]["name"] = "Recepción"
                config_path.write_text(json.dumps(config))
                start()
                page.reload()
                expect(page.locator(".room-name").first).to_have_text("Recepción")
                expect(page.locator("#online-count")).to_have_text("6 de 6 salas disponibles")
                output = ROOT / "docs" / "images"
                output.mkdir(exist_ok=True)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output/"iteration-3-desktop.png"),full_page=True)
                page.set_viewport_size({"width":390,"height":844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output/"iteration-3-mobile.png"),full_page=True)
                choose("admin",3)
                page.locator(".advanced summary").click()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output/"iteration-3-admin-mobile.png"),full_page=True)
                choose("lab",3)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert errors == [],errors
                browser.close()
                print("PASS Chromium: six rooms/all controls, separated detail/admin/lab, three policies, administrative confirmations/reasons, events/history, busy/fault isolation, restart/token, stale5s, XSS, keyboard, desktop/mobile; 0 JS errors")
        finally:
            stop()


if __name__ == "__main__":
    main()
