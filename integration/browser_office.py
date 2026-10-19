"""Chromium HTTPS -> actual office API -> six CLOSED native SDK mocks -> SQLite.

No hardware arguments accepted. Imports of real Bleak/CoreBluetooth are trapped.
"""
import importlib.abc
from pathlib import Path
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PASSWORD = "closed-office-browser-only-" + "x" * 43


class NoDriver(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ("bleak", "CoreBluetooth", "Foundation") or fullname.startswith("bleak."):
            raise RuntimeError("Physical driver forbidden in closed office E2E")


sys.meta_path.insert(0, NoDriver())


def serve(directory, port):
    import uvicorn
    from integration.office_mock import closed_office
    from madoka_multiroom.api import build_office_app
    with closed_office(directory, port=port) as office:
        office.failed_effect_rooms.add("room-3")
        office.stage, office.room, office.delay = "write", "room-4", .5
        app = build_office_app(office.config, str(Path(directory)/"history.db"), fleet=office.fleet, password=PASSWORD)
        try:
            uvicorn.run(app, host="127.0.0.1", port=port, workers=1, proxy_headers=False, access_log=False,
                log_level="critical", loop="asyncio", ssl_certfile=str(Path(directory)/"cert.pem"),
                ssl_keyfile=str(Path(directory)/"key.pem"))
        except KeyboardInterrupt:
            pass
        assert not office.budget.owners, "Closed mock native shutdown did not verify application release"


def main():
    from playwright.sync_api import sync_playwright, expect
    with tempfile.TemporaryDirectory(prefix="madoka-office-browser-") as directory:
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(Path(directory)/"key.pem"),
            "-out", str(Path(directory)/"cert.pem"), "-days", "1", "-subj", "/CN=localhost"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"https://127.0.0.1:{port}"
        process = None
        def start():
            nonlocal process
            process = subprocess.Popen([sys.executable, __file__, "--serve-closed", directory, str(port)], cwd=ROOT,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic()+15
            while True:
                try:
                    urlopen(base+"/api/session", timeout=.5, context=ssl._create_unverified_context())
                    raise AssertionError("Office API exposed without authentication")
                except HTTPError as exc:
                    if exc.code == 401: return
                    raise
                except OSError: pass
                if time.monotonic() > deadline or process.poll() is not None:
                    raise RuntimeError("Closed HTTPS office server failed to start")
                time.sleep(.05)
        def stop():
            if process and process.poll() is None:
                process.send_signal(signal.SIGINT)
                process.wait(timeout=20)
            assert process.returncode == 0, "Office shutdown failed"
        try:
            start()
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                context = browser.new_context(ignore_https_errors=True,
                    http_credentials={"username": "operator", "password": PASSWORD}, viewport={"width":1440,"height":1100})
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda exc: errors.append(str(exc)))
                page.goto(base)
                expect(page.locator("#online-count")).to_have_text("6 de 6 salas disponibles", timeout=15000)
                expect(page.locator(".demo-label")).to_have_text("Bluetooth · controles manuales")
                expect(page.locator('[data-view="lab"]')).to_be_hidden()
                def card(i): return page.locator(f'.room[data-id="room-{i}"]')
                def confirm():
                    page.locator("#confirm-reason").fill("Owner authorized closed office test")
                    page.locator("#accept-confirm").click()
                for i in range(1,7):
                    expect(card(i).locator(".power")).to_be_enabled()
                    expect(card(i).locator(".mode")).to_be_enabled()
                    expect(card(i).locator(".fan")).to_be_enabled()
                    expect(card(i).locator(".last-read")).not_to_contain_text("Sin lecturas")
                card(1).locator(".increase").click()
                page.locator("#cancel-confirm").click()
                assert page.request.get(base+"/api/controllers/room-1").json()["last_operation"] is None
                card(1).locator(".increase").click(); confirm()
                expect(card(1).locator(".setpoint")).to_have_text("23°")
                expect(card(1).locator(".operation-status")).to_contain_text("confirmado")
                card(1).locator(".decrease").click(); confirm()
                expect(card(1).locator(".setpoint")).to_have_text("22°")
                card(2).locator(".mode").select_option("4"); confirm()
                expect(card(2).locator(".mode")).to_have_value("4")
                expect(card(2).locator(".setpoint")).to_have_text("21°")
                card(2).locator(".fan").select_option("4"); confirm()
                expect(card(2).locator(".fan")).to_have_value("4")
                card(2).locator(".power").click(); confirm()
                expect(card(2).locator(".power")).to_have_attribute("aria-pressed", "false")
                card(4).locator(".increase").click(); confirm()
                expect(card(4)).to_have_attribute("aria-busy", "true")
                expect(card(5).locator(".power")).to_be_enabled()
                expect(card(4).locator(".operation-status")).to_contain_text("confirmado")
                # ACK without effect becomes durable uncertain, never optimistic success.
                card(3).locator(".increase").click(); confirm()
                expect(card(3).locator(".operation-status")).to_contain_text("STOP")
                expect(card(3).locator(".increase")).to_be_disabled()
                expect(card(6).locator(".increase")).to_be_enabled()
                card(3).locator(".native-recover").click(); confirm()
                expect(card(3).locator(".operation-status")).to_contain_text("sigue sin confirmar", timeout=12000)
                expect(card(3).locator(".setpoint")).to_have_text("22°")
                card(1).locator(".details").click()
                page.locator("#detail-content .history").click()
                expect(page.locator("#history-rows tr").first).to_be_visible()
                page.locator("#close-history").click(); page.locator("#close-detail").click()
                output = ROOT / "artifacts"
                output.mkdir(exist_ok=True)
                page.screenshot(path=str(output/"office-desktop.png"), full_page=True)
                page.set_viewport_size({"width":390,"height":844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output/"office-mobile.png"), full_page=True)
                # Mobile commands use the same protected confirmation path.
                card(6).locator(".increase").click(); confirm()
                expect(card(6).locator(".setpoint")).to_have_text("23°")
                before = page.request.get(base+"/api/controllers/room-1/history").json()["samples"]
                stop(); start(); page.reload()
                expect(page.locator("#online-count")).to_have_text("6 de 6 salas disponibles", timeout=15000)
                after = page.request.get(base+"/api/controllers/room-1/history").json()["samples"]
                assert {s["id"] for s in before} <= {s["id"] for s in after}
                assert errors == [], errors
                browser.close()
        finally:
            stop()
        print("PASS Chromium HTTPS office: six native mocks, all manual controls, explicit confirmation/cancel/restore, pending isolation, ACK-no-effect STOP/review, durable history/restart, desktop/mobile; 0 JS errors; real native drivers forbidden")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--serve-closed":
        serve(sys.argv[2], int(sys.argv[3]))
    elif len(sys.argv) == 1:
        main()
    else:
        raise SystemExit("This closed harness accepts no physical configuration")
