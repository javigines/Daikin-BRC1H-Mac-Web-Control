"""Chromium HTTP loopback -> actual local API -> six closed SDKs -> SQLite.

Fixed-port ownership and a private fixture marker are checked before any action.
No real driver, private configuration, authorization or hardware is accepted.
"""
import importlib.abc
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = "http://127.0.0.1:8090"


class NoDriver(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ("bleak", "CoreBluetooth", "Foundation", "objc") or fullname.startswith("bleak."):
            raise RuntimeError("Physical driver forbidden in closed local E2E")


sys.meta_path.insert(0, NoDriver())


def serve(directory):
    import uvicorn
    from integration.office_mock import closed_office, synthetic_setpoint_block, synthetic_setpoint_ack
    from madoka_multiroom.api import build_local_app
    from madoka_multiroom.office_access import Controls
    with closed_office(directory, local=True) as local:
        from madoka_protocol import CMD_SET_POWER, CMD_GET_POWER, CMD_GET_SETPOINT, CMD_SET_SETPOINT
        for state in local.states.values():
            state[CMD_GET_SETPOINT] = synthetic_setpoint_block(extended=True)
        local.states['room-6'][CMD_GET_POWER][0x20] = b'\x00'
        def reply(room, command, objects, wrote):
            if command == CMD_SET_POWER: return {0x20: b'\x00'}
            if command == CMD_SET_SETPOINT: return synthetic_setpoint_ack()
            if command == CMD_GET_SETPOINT and wrote: objects[0x9B] = b'\x08'
            return objects
        local.reply_hook = reply
        if not local.fleet.settings_path.exists():
            for backend in local.fleet.backends.values(): backend.controls=Controls()
        local.failed_effect_rooms.add("room-3")
        local.stage, local.room, local.delay = "write", "room-4", .5
        app=build_local_app(local.config,str(Path(directory)/"history.db"),fleet=local.fleet)
        @app.get("/__closed")
        async def fixture_marker(): return {"fixture":Path(directory).name}
        @app.get("/__closed/setpoint-evidence")
        async def setpoint_evidence():
            return {"sets": [c[0] for c in local.calls if len(c) == 3 and c[1] == CMD_SET_SETPOINT],
                    "blocks": {r: {str(k): v.hex() for k, v in state[CMD_GET_SETPOINT].items()}
                               for r, state in local.states.items()}}
        try:
            uvicorn.run(app,host="127.0.0.1",port=8090,workers=1,proxy_headers=False,
                access_log=False,log_level="critical",loop="asyncio")
        except KeyboardInterrupt: pass
        assert not local.budget.owners,"Mock shutdown did not release application ownership"


def main():
    import json
    from playwright.sync_api import sync_playwright, expect
    with tempfile.TemporaryDirectory(prefix="madoka-local-browser-") as directory:
        # Never browse/operate an existing process on this fixed production port.
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            sock.bind(("127.0.0.1",8090))
        process=None
        def start():
            nonlocal process
            process=subprocess.Popen([sys.executable,__file__,"--serve-closed",directory],cwd=ROOT,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            deadline=time.monotonic()+15
            while True:
                if process.poll() is not None: raise RuntimeError("Closed local server failed")
                try:
                    result=json.loads(urlopen(BASE+"/__closed",timeout=.5).read())
                    assert result=={"fixture":Path(directory).name},"Refusing non-fixture local server"
                    return
                except OSError: pass
                if time.monotonic()>deadline: raise RuntimeError("Closed local server unavailable")
                time.sleep(.05)
        def stop():
            if process and process.poll() is None:
                process.send_signal(signal.SIGINT);process.wait(timeout=20)
            assert process.returncode==0,"Local shutdown failed"
        try:
            start()
            with sync_playwright() as pw:
                browser=pw.chromium.launch()
                page=browser.new_page(viewport={"width":1440,"height":1100})
                errors=[];page.on("pageerror",lambda exc:errors.append(str(exc)))
                page.goto(BASE)
                expect(page.locator("#online-count")).to_have_text("6 de 6 salas disponibles",timeout=15000)
                expect(page.locator(".demo-label")).to_have_text("Bluetooth local")
                for view in ("admin","lab"): expect(page.locator(f'[data-view="{view}"]')).to_be_hidden()
                def card(i): return page.locator(f'.room[data-id="room-{i}"]')
                for i in range(1,7):
                    expect(card(i).locator(".connection")).to_have_text("Conectado")
                    expect(card(i).locator(".power")).to_be_enabled()
                    expect(card(i).locator(".increase")).to_be_enabled()
                    expect(card(i).locator(".decrease")).to_be_enabled()
                    expect(card(i).locator(".mode")).to_be_disabled()
                    expect(card(i).locator(".fan")).to_be_disabled()
                baseline=page.request.get(BASE+"/__closed/setpoint-evidence").json()
                card(1).locator(".increase").click()
                expect(page.locator("#confirm-dialog")).not_to_be_visible()
                expect(card(1).locator(".setpoint")).to_have_text("23°")
                expect(card(1).locator(".operation-status")).to_have_text("Cambio confirmado")
                evidence=page.request.get(BASE+"/__closed/setpoint-evidence").json()
                assert evidence['sets']==['room-1']
                expected=json.loads(json.dumps(baseline['blocks']))
                expected['room-1']['32']='0b80'  # synthetic 23 degrees * 128
                assert evidence['blocks']==expected
                card(1).locator(".decrease").click()
                expect(card(1).locator(".setpoint")).to_have_text("22°")
                card(2).locator(".local-configure").click()
                dialog=page.locator("#local-controls-dialog")
                for mode in (3,4): dialog.locator(f'input[name="modes"][value="{mode}"]').check()
                for speed in range(1,6): dialog.locator(f'input[name="fans"][value="{speed}"]').check()
                dialog.get_by_role("button",name="Guardar controles").click()
                expect(card(2).locator(".fan")).to_be_enabled(timeout=10000)
                card(2).locator(".mode").select_option("4")
                expect(card(2).locator(".setpoint")).to_have_text("21°")
                card(2).locator(".fan").select_option("4")
                expect(card(2).locator(".fan")).to_have_value("4")
                expect(card(2).locator(".operation-status")).to_have_text("Cambio confirmado")
                card(2).locator(".power").click()
                expect(card(2).locator(".power")).to_have_attribute("aria-pressed","false")
                expect(page.locator("#online-count")).to_have_text("6 de 6 salas disponibles")
                # An HTTP rejection is not evidence of a radio disconnect.
                manual_route = '**/api/controllers/room-6/manual'
                page.route(manual_route, lambda route: route.fulfill(status=409, content_type='application/json', body='{"detail":"Daikin ocupado"}'))
                card(6).locator('.power').click()
                expect(card(6).locator('.operation-status')).to_have_text('Daikin ocupado')
                expect(card(6).locator('.connection')).to_have_text('Conectado')
                expect(page.locator('#online-count')).to_have_text('6 de 6 salas disponibles')
                page.unroute(manual_route)
                card(6).locator('.power').click()
                expect(card(6).locator('.power')).to_have_attribute('aria-pressed', 'true')
                expect(card(6).locator('.operation-status')).to_have_text('Cambio confirmado')
                card(4).locator(".increase").click()
                expect(card(4)).to_have_attribute("aria-busy","true")
                expect(card(5).locator(".power")).to_be_enabled()
                expect(card(4).locator(".operation-status")).to_have_text("Cambio confirmado")
                card(3).locator(".increase").click()
                expect(card(3).locator(".operation-status")).to_contain_text("No se pudo confirmar")
                expect(card(3).locator(".increase")).to_be_disabled()
                expect(card(3).locator('.connection')).to_have_text('Conectado')
                expect(page.locator('#online-count')).to_have_text('6 de 6 salas disponibles')
                expect(card(6).locator(".increase")).to_be_enabled()
                card(3).locator(".native-recover").click()
                expect(card(3).locator(".operation-status")).to_contain_text("cambio anterior no se confirmó",timeout=12000)
                expect(card(3).locator(".setpoint")).to_have_text("22°")
                card(1).locator(".details").click();page.locator("#detail-content .history").click()
                expect(page.locator("#history-rows tr").first).to_be_visible()
                page.locator("#close-history").click();page.locator("#close-detail").click()
                card(6).locator('.details').click()
                page.locator('.local-diagnostics summary').click()
                expect(page.locator('.local-diagnostics pre')).to_contain_text('ack_validate')
                expect(page.locator('.local-diagnostics pre')).to_contain_text('confirmed')
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.locator('#close-detail').click()
                page.set_viewport_size({'width':1440,'height':1100})
                output=ROOT/"artifacts";output.mkdir(exist_ok=True)
                page.screenshot(path=str(output/"local-desktop.png"),full_page=True)
                page.set_viewport_size({"width":390,"height":844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output/"local-mobile.png"),full_page=True)
                card(6).locator(".increase").click()
                expect(card(6).locator(".setpoint")).to_have_text("23°")
                before=page.request.get(BASE+"/api/controllers/room-1/history").json()["samples"]
                stop();start();page.reload()
                expect(page.locator("#online-count")).to_have_text("6 de 6 salas disponibles",timeout=15000)
                after=page.request.get(BASE+"/api/controllers/room-1/history").json()["samples"]
                assert {s["id"] for s in before} <= {s["id"] for s in after}
                expect(card(2).locator(".mode")).to_be_enabled()
                expect(card(2).locator(".fan")).to_be_enabled()
                assert errors==[],errors
                browser.close()
        finally: stop()
        print("PASS Chromium local HTTP: six synthetic extended setpoint blocks, nonboolean symbols, compact ACK plus strict GET, single SET and byte isolation, direct controls, capability settings, uncertain SET without replay, history/settings/restart, desktop/mobile; 0 JS errors; real drivers forbidden")


if __name__=="__main__":
    if len(sys.argv)==3 and sys.argv[1]=="--serve-closed": serve(sys.argv[2])
    elif len(sys.argv)==1: main()
    else: raise SystemExit("Closed harness accepts no hardware configuration")
