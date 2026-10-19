"""Authenticated Chromium -> native factory -> closed fake SDK. No real BLE."""
import importlib.abc
import json
from pathlib import Path
import socket
import signal
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PASSWORD = "offline-browser-only-" + "x" * 43


class NoDriver(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "bleak" or fullname.startswith("bleak."):
            raise RuntimeError("Real driver forbidden in native mock harness")


sys.meta_path.insert(0, NoDriver())


def serve(directory, port):
    import uvicorn
    from integration.native_mock import closed_native
    from madoka_multiroom.api import build_native_readonly_app
    with closed_native(directory) as native:
        app = build_native_readonly_app(native.config, str(Path(directory) / "history.db"),
                                        fleet=native.fleet, password=PASSWORD)
        try:
            uvicorn.run(app, host="127.0.0.1", port=port, workers=1, proxy_headers=False,
                        access_log=False, log_level="critical", loop="asyncio")
        except KeyboardInterrupt:
            pass
        assert native.calls.count("connect") == native.calls.count("disconnect") == 1
        assert len(native.budget.quarantined) == 1


def main():
    from playwright.sync_api import sync_playwright, expect
    with tempfile.TemporaryDirectory(prefix="madoka-native-mock-") as directory:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        process = subprocess.Popen([sys.executable, __file__, "--serve-mock", directory, str(port)],
                                   cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    urlopen(base + "/api/session", timeout=.3)
                    raise AssertionError("Native session exposed without authentication")
                except HTTPError as exc:
                    if exc.code == 401: break
                    raise
                except OSError: pass
                if time.monotonic() > deadline or process.poll() is not None:
                    raise RuntimeError("Native mock server failed to start")
                time.sleep(.05)
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                context = browser.new_context(http_credentials={"username":"operator", "password":PASSWORD},
                                              viewport={"width":1440,"height":1100})
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda exc: errors.append(str(exc)))
                page.goto(base)
                expect(page.locator(".room")).to_have_count(6)
                expect(page.locator(".demo-label")).to_have_text("Bluetooth · solo lectura supervisada")
                expect(page.locator('[data-view="admin"]')).to_be_hidden()
                expect(page.locator('[data-view="lab"]')).to_be_hidden()
                data = page.request.get(base + "/api/controllers").json()["controllers"]
                assert all(r["stale"] and r["session_state"] == "new" for r in data)
                expect(page.locator("#native-connect")).to_be_enabled()
                expect(page.locator("#native-selected")).to_have_text("Sala autorizada: Recepción")
                page.on("dialog", lambda dialog: dialog.accept())
                page.locator("#native-connect").click()
                expect(page.locator("#native-status")).to_contain_text("Sesión preparada", timeout=10000)
                card = page.locator('.room[data-id="room-1"]')
                expect(card.locator(".setpoint")).to_have_text("22°", timeout=15000)
                expect(card.locator(".increase")).to_be_disabled()
                expect(card.locator(".power")).to_be_disabled()
                expect(page.locator("#native-connect")).to_be_disabled()
                (ROOT / "artifacts").mkdir(exist_ok=True)
                page.screenshot(path=str(ROOT / "artifacts/native-readonly-desktop.png"), full_page=True)
                card.locator(".details").click()
                expect(page.locator("#detail-content .mode")).to_be_disabled()
                expect(page.locator("#detail-content .fan")).to_be_disabled()
                expect(page.locator("#detail-content .updated")).not_to_contain_text("Sin lecturas")
                page.locator("#detail-content .history").click()
                expect(page.locator("#history-rows tr").first).to_be_visible()
                page.locator("#close-history").click()
                page.locator("#close-detail").click()
                data = page.request.get(base + "/api/controllers").json()["controllers"]
                assert data[0]["connected"] and all(r["stale"] for r in data[1:])
                assert all(not r["controls_enabled"] and not r["physical_enforce_enabled"] for r in data)
                session = page.request.get(base + "/api/session").json()
                response = page.request.post(base + "/api/controllers/room-1/power", data={"on":False},
                    headers={"X-Demo-Token":session["token"]})
                assert response.status == 403
                page.set_viewport_size({"width":390,"height":844})
                expect(card).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                page.screenshot(path=str(ROOT / "artifacts/native-readonly-mobile.png"), full_page=True)
                page.reload()
                expect(page.locator("#native-connect")).to_be_disabled()
                assert errors == [], errors
                browser.close()
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                process.wait(timeout=20)
            if process.returncode != 0:
                raise RuntimeError("Native mock shutdown failed")
        print("PASS Chromium native mock: authentication, explicit admission, one real-mode room/five unavailable, GET-only controls/history, desktop/mobile, 0 JS errors; real driver forbidden")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--serve-mock":
        serve(sys.argv[2], int(sys.argv[3]))
    elif len(sys.argv) == 1:
        main()
    else:
        raise SystemExit("Closed mock harness accepts no hardware configuration")
