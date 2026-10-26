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
    from integration.office_mock import (closed_office, synthetic_setpoint_block, synthetic_setpoint_ack,
        synthetic_fan_blocks, synthetic_coordinated_block, synthetic_coordinated_profile)
    from madoka_multiroom.api import build_local_app
    from madoka_multiroom.office_access import Controls
    with closed_office(directory, local=True) as local:
        from madoka_protocol import CMD_SET_POWER, CMD_GET_POWER, CMD_GET_MODE, CMD_GET_FAN, CMD_SET_FAN, CMD_SET_MODE, CMD_GET_SETPOINT, CMD_SET_SETPOINT
        for state in local.states.values():
            state[CMD_GET_SETPOINT] = synthetic_setpoint_block(extended=True)
        local.states['room-6'][CMD_GET_POWER][0x20] = b'\x00'
        setpoint_rooms = set()
        mode_fault = {"unknown_ack": False}
        def reply(room, command, objects, wrote):
            if command == CMD_SET_POWER: return {0x20: b'\x00'}
            if command == CMD_SET_SETPOINT:
                setpoint_rooms.add(room)
                return synthetic_setpoint_ack()
            if command == CMD_SET_MODE:
                return {0x20: b'\xff' if mode_fault['unknown_ack'] else b'\x03'}
            if command == CMD_GET_SETPOINT and room in setpoint_rooms: objects[0x9B] = b'\x08'
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
        @app.get("/__closed/coordinated-fixture/{scenario}")
        async def coordinated_fixture(scenario: str):
            assert scenario in ('baseline', 'stale', 'coexistence', 'external-dry')
            room = 'room-5'
            if scenario in ('baseline', 'coexistence'):
                local.states[room][CMD_GET_SETPOINT] = synthetic_coordinated_block()
                local.states[room][CMD_GET_MODE][0x20] = b'\x03'
                backend = local.fleet.backends[room]
                backend.controls = Controls(power=True,setpoint=True)
                backend.coordinated_setpoint_profile = synthetic_coordinated_profile(local.grants[room].identity)
                if scenario == 'coexistence':
                    from madoka_multiroom.local_modes import LocalControls
                    now = time.time()
                    backend.controls = LocalControls(power=True,setpoint=True,modes=(0,1,3,4),fans=(1,3,5),
                        mode_commissioning=dict(wall_modes=[0,1,3,4],checked_at=now-1,expires_at=now+3600,
                            restrictions='unlocked',physical_scope='independent'))
                    mode_fault['unknown_ack'] = False
            elif scenario == 'external-dry':
                local.states[room][CMD_GET_MODE][0x20] = b'\x01'
            else:
                local.states[room][CMD_GET_SETPOINT][0x99] = b'\x09'
            async with app.state.manager.room(room).lock:
                pass
            await app.state.manager.operate(room)
            return {"fixture":Path(directory).name}
        @app.get("/__closed/fan-fixture/{scenario}")
        async def fan_fixture(scenario: str):
            # Test-only external observations, never device commands or a route
            # in the product. The private marker is checked before using it.
            assert scenario in ('cool', 'heat', 'unknown', 'missing', 'auto-mode')
            for (room, state), block in zip(local.states.items(), synthetic_fan_blocks()):
                state[CMD_GET_FAN] = block
                state[CMD_GET_MODE][0x20] = bytes([4 if scenario == 'heat' else 2 if scenario == 'auto-mode' else 3])
                local.fleet.backends[room].controls = Controls(power=True, setpoint=True,
                    modes=(2, 3, 4), fans=(1, 2, 3, 4, 5))
            if scenario == 'unknown': local.states['room-1'][CMD_GET_FAN][0x20] = b'\xff'
            if scenario == 'missing': local.states['room-1'][CMD_GET_FAN].pop(0x20)
            # Refresh the actual API through its ordinary GET-only backend path.
            for room in local.states:
                async with app.state.manager.room(room).lock:
                    pass  # Let an in-flight fixture poll finish first.
                await app.state.manager.operate(room)
            return {"fixture": Path(directory).name}
        @app.get("/__closed/fan-evidence")
        async def fan_evidence():
            return {"sets": [(c[0], c[1]) for c in local.calls if len(c) == 3 and
                             c[1] in (CMD_SET_POWER, CMD_SET_MODE, CMD_SET_SETPOINT, CMD_SET_FAN)],
                    "retained": {room: backend.raw.get(CMD_GET_FAN) == local.states[room][CMD_GET_FAN]
                                 for room, backend in local.fleet.backends.items()}}
        @app.get("/__closed/mode-fixture/{scenario}")
        async def mode_fixture(scenario: str):
            assert scenario in ('reset', 'unknown-ack', 'expired', 'persistent-old')
            mode_fault['unknown_ack'] = scenario == 'unknown-ack'
            if scenario in ('expired', 'persistent-old'):
                from madoka_multiroom.local_modes import LocalControls
                backend = local.fleet.backends['room-1']
                data = backend.controls.model_dump()
                data['mode_commissioning'].update(
                    checked_at=time.time()-60*86400,
                    expires_at=time.time()-3600 if scenario == 'expired' else None)
                backend.controls = LocalControls.model_validate(data)
                local.fleet.configure_controls('room-1', backend.controls)
                await app.state.manager.operate('room-1')
            if scenario == 'reset':
                for room, state in local.states.items():
                    state[CMD_GET_MODE] = {0x20:b'\x03'}
                    state[CMD_GET_FAN] = {0x20:b'\x03', 0x21:b'\x02'}
                    local.fleet.backends[room].controls = Controls(power=True, setpoint=True, modes=(2,3,4), fans=(1,2,3,4,5))
                for room in local.states: await app.state.manager.operate(room)
            return {"fixture":Path(directory).name}
        try:
            uvicorn.run(app,host="127.0.0.1",port=8090,workers=1,proxy_headers=False,
                access_log=False,log_level="critical",loop="asyncio")
        except KeyboardInterrupt: pass
        assert not local.budget.owners,"Mock shutdown did not release application ownership"


def main():
    import json
    from uuid import uuid4
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
                # Real Local API -> six synthetic independent fan banks. No SET
                # may result from rendering, opening detail, polling or refresh.
                fan_baseline = page.request.get(BASE+"/__closed/fan-evidence").json()['sets']
                for viewport in ({'width':1440,'height':1100}, {'width':390,'height':844}):
                    page.set_viewport_size(viewport)
                    for scenario, expected in (('cool', ['1','3','4','5','0','2']),
                                               ('heat', ['1','0','0','0','2','5'])):
                        assert page.request.get(BASE+"/__closed/fan-fixture/"+scenario).ok
                        page.reload()
                        for i, speed in enumerate(expected, 1):
                            fan = card(i).locator('.fan')
                            expect(fan).to_have_value(speed)
                            expect(fan.locator('option[value="0"]')).to_have_js_property('disabled', True)
                            if speed == '0': expect(fan.locator('option:checked')).to_have_text('Auto')
                            if i in (1, 6): expect(fan).to_be_enabled()
                            else: expect(fan).to_be_disabled()
                        card(5).locator('.details').click()
                        expect(page.locator('#detail-content .fan')).to_have_value(expected[4])
                        expect(page.locator('#detail-content .fan')).to_be_disabled()
                        page.locator('#close-detail').click()
                        token = page.request.get(BASE+'/api/session').json()['token']
                        for i, speed in [(i, 0) for i in range(1, 7)] + [(i, 1) for i in (2, 3, 4, 5)]:
                            response = page.request.post(BASE+f'/api/controllers/room-{i}/manual',
                                headers={'X-Demo-Token': token, 'Origin': BASE},
                                data={'operation_id':str(uuid4()), 'action':'fan', 'value':speed})
                            assert response.status == 422
                    for scenario, text in (('unknown', 'Lectura desconocida'),
                                           ('missing', 'Sin lectura'),
                                           ('auto-mode', 'No aplicable en este modo')):
                        assert page.request.get(BASE+"/__closed/fan-fixture/"+scenario).ok
                        page.reload()
                        expect(card(1).locator('.fan')).to_have_value('')
                        expect(card(1).locator('.fan option:checked')).to_have_text(text)
                        expect(card(1).locator('.fan')).to_be_disabled()
                    evidence = page.request.get(BASE+"/__closed/fan-evidence").json()
                    assert evidence['sets'] == fan_baseline and all(evidence['retained'].values())
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                # Commission Fan/Dry via the product UI; ventilation absent is
                # independent. Exercise derived target changes and durable STOP.
                assert page.request.get(BASE+'/__closed/mode-fixture/reset').ok
                page.reload()
                token = page.request.get(BASE+'/api/session').json()['token']
                header = {'X-Demo-Token':token, 'Origin':BASE}
                baseline_modes = page.request.get(BASE+'/__closed/fan-evidence').json()['sets']
                rejected = page.request.post(BASE+'/api/controllers/room-1/local-controls', headers=header,
                    data={'modes':[0,1,3,4]})
                assert rejected.status == 422
                card(1).locator('.local-configure').click()
                dialog = page.locator('#local-controls-dialog')
                dialog.locator('[name="mode_revoked"]').check()
                dialog.get_by_role('button',name='Guardar controles').click()
                expect(card(1).locator('.mode')).to_be_disabled()
                initial_block = page.request.get(BASE+'/api/controllers/room-1').json()['local_controls']['mode_commissioning']
                assert initial_block['wall_modes'] == [] and initial_block['revoked']
                assert page.request.get(BASE+'/__closed/fan-evidence').json()['sets'] == baseline_modes
                card(1).locator('.local-configure').click()
                for mode in (0,1): dialog.locator(f'[name="modes"][value="{mode}"]').check()
                dialog.locator('[name="mode_revoked"]').uncheck()
                dialog.locator('[name="wall_verified"]').check()
                dialog.locator('[name="restrictions"]').select_option('unlocked')
                dialog.locator('[name="physical_scope"]').select_option('independent')
                dialog.get_by_role('button',name='Guardar controles').click()
                expect(card(1).locator('.mode')).to_be_enabled()
                assert page.request.get(BASE+'/__closed/fan-evidence').json()['sets'] == baseline_modes
                settings = page.request.get(BASE+'/api/controllers/room-1').json()['local_controls']
                assert settings['mode_commissioning']['expires_at'] is None
                # Persistent authority still applies after 60 days; explicit
                # legacy expiry disables only extra targets, including in HTTP.
                assert page.request.get(BASE+'/__closed/mode-fixture/persistent-old').ok
                page.reload()
                card(1).locator('.mode').select_option('1')
                expect(card(1).locator('.operation-status')).to_have_text('Cambio confirmado')
                assert page.request.get(BASE+'/__closed/mode-fixture/expired').ok
                page.reload()
                for mode in (0,1,5):
                    expect(card(1).locator(f'.mode option[value="{mode}"]')).to_have_js_property('disabled',True)
                for mode in (2,3,4):
                    expect(card(1).locator(f'.mode option[value="{mode}"]')).to_have_js_property('disabled',False)
                count = len(page.request.get(BASE+'/__closed/fan-evidence').json()['sets'])
                token = page.request.get(BASE+'/api/session').json()['token']
                for mode in (0,1,5):
                    rejected = page.request.post(BASE+'/api/controllers/room-1/manual',
                        headers={'X-Demo-Token':token, 'Origin':BASE},
                        data={'operation_id':str(uuid4()),'action':'mode','value':mode,'expected_mode':1})
                    assert rejected.status == 422
                assert len(page.request.get(BASE+'/__closed/fan-evidence').json()['sets']) == count
                card(1).locator('.mode').select_option('4')
                expect(card(1).locator('.operation-status')).to_have_text('Cambio confirmado')
                assert len(page.request.get(BASE+'/__closed/fan-evidence').json()['sets']) == count+1
                expired_evidence = page.request.get(BASE+'/api/controllers/room-1').json()['local_controls']['mode_commissioning']
                card(1).locator('.local-configure').click()
                dialog.locator('[name="fans"][value="5"]').uncheck()
                with page.expect_response(lambda r: r.url.endswith('/room-1/local-controls') and r.request.method == 'POST') as saved:
                    dialog.get_by_role('button',name='Guardar controles').click()
                assert saved.value.ok
                assert page.request.get(BASE+'/api/controllers/room-1').json()['local_controls']['mode_commissioning'] == expired_evidence
                # Renew explicitly, then record revocation without wall refresh.
                card(1).locator('.local-configure').click()
                dialog.locator('[name="wall_verified"]').check()
                dialog.get_by_role('button',name='Guardar controles').click()
                expect(card(1).locator('.mode option[value="0"]')).to_have_js_property('disabled',False)
                renewed = page.request.get(BASE+'/api/controllers/room-1').json()['local_controls']['mode_commissioning']
                card(1).locator('.local-configure').click()
                dialog.locator('[name="mode_revoked"]').check()
                dialog.get_by_role('button',name='Guardar controles').click()
                expect(card(1).locator('.mode')).to_be_disabled()
                revoked = page.request.get(BASE+'/api/controllers/room-1').json()['local_controls']['mode_commissioning']
                assert revoked['revoked'] and revoked['checked_at'] == renewed['checked_at']
                assert len(page.request.get(BASE+'/__closed/fan-evidence').json()['sets']) == count+1
                page.reload()
                expect(card(1).locator('.mode')).to_be_disabled()
                card(1).locator('.local-configure').click()
                dialog.locator('[name="mode_revoked"]').uncheck()
                dialog.locator('[name="wall_verified"]').check()
                dialog.get_by_role('button',name='Guardar controles').click()
                expect(card(1).locator('.mode option[value="0"]')).to_have_js_property('disabled',False)
                others = [page.request.get(BASE+f'/api/controllers/room-{i}').json()['state'] for i in range(2,7)]
                for viewport in ({'width':1440,'height':1100}, {'width':390,'height':844}):
                    page.set_viewport_size(viewport)
                    for mode in (0,1,4,3):
                        card(1).locator('.mode').select_option(str(mode))
                        expect(card(1).locator('.operation-status')).to_have_text('Cambio confirmado')
                        expect(card(1).locator('.mode')).to_have_value(str(mode))
                        if mode in (0,1):
                            expect(card(1).locator('.setpoint')).to_have_text('—')
                            expect(card(1).locator('.increase')).to_be_disabled()
                            expect(card(1).locator('.fan')).to_be_disabled()
                        card(1).locator('.details').click()
                        expect(page.locator('#detail-content .mode')).to_have_value(str(mode))
                        expect(page.locator('#detail-content .local-targets')).to_contain_text('Objetivos guardados')
                        if mode == 1:
                            expect(page.locator('#detail-content .power')).to_be_disabled()
                            expect(page.locator('#detail-content .mode-notice')).to_contain_text('automáticos')
                        page.locator('#close-detail').click()
                    expect(card(1).locator('.mode option[value="5"]')).to_have_js_property('disabled',True)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert [page.request.get(BASE+f'/api/controllers/room-{i}').json()['state'] for i in range(2,7)] == others
                # A separate synthetic ventilation unit can opt in explicitly.
                # This does not enable ventilation in the Fan/Dry room.
                card(2).locator('.local-configure').click()
                dialog.locator('[name="modes"][value="5"]').check()
                dialog.locator('[name="wall_verified"]').check()
                dialog.locator('[name="restrictions"]').select_option('unlocked')
                dialog.locator('[name="physical_scope"]').select_option('independent')
                dialog.locator('[name="ventilation_hardware"]').check()
                dialog.get_by_role('button',name='Guardar controles').click()
                expect(card(2).locator('.mode option[value="5"]')).to_have_js_property('disabled',False)
                card(2).locator('.mode').select_option('5')
                expect(card(2).locator('.operation-status')).to_have_text('Cambio confirmado')
                expect(card(2).locator('.setpoint')).to_have_text('—')
                expect(card(2).locator('.increase')).to_be_disabled()
                expect(card(2).locator('.fan')).to_be_disabled()
                card(2).locator('.details').click()
                expect(page.locator('#detail-content .mode')).to_have_value('5')
                expect(page.locator('#detail-content .mode-notice')).to_contain_text('equipo compatible')
                page.locator('#close-detail').click()
                expect(card(1).locator('.mode option[value="5"]')).to_have_js_property('disabled',True)
                assert page.request.get(BASE+'/__closed/mode-fixture/unknown-ack').ok
                card(1).locator('.mode').select_option('1')
                expect(card(1).locator('.operation-status')).to_contain_text('No se pudo confirmar')
                count = len(page.request.get(BASE+'/__closed/fan-evidence').json()['sets'])
                card(1).locator('.native-recover').click()
                expect(card(1).locator('.mode')).to_be_disabled()
                page.reload()
                expect(card(1).locator('.mode')).to_be_disabled()
                assert len(page.request.get(BASE+'/__closed/fan-evidence').json()['sets']) == count
                # Consent/cancel/stale proposals on both viewports. No replay.
                for viewport in ({'width':1440,'height':1100}, {'width':390,'height':844}):
                    page.set_viewport_size(viewport)
                    assert page.request.get(BASE+'/__closed/coordinated-fixture/baseline').ok
                    page.reload()
                    expect(card(5).locator('.setpoint')).to_have_text('26°')
                    baseline = page.request.get(BASE+'/__closed/setpoint-evidence').json()
                    card(5).locator('.decrease').click()
                    dialog = page.locator('#setpoint-confirm-dialog')
                    expect(dialog).to_be_visible()
                    expect(dialog).to_contain_text('Frío 26 → 25 °C; Calor 25 → 24 °C')
                    expect(dialog.locator('#cancel-setpoint-confirm')).to_be_focused()
                    dialog.locator('#cancel-setpoint-confirm').click()
                    expect(dialog).not_to_be_visible()
                    assert page.request.get(BASE+'/__closed/setpoint-evidence').json() == baseline
                    card(5).locator('.decrease').click()
                    assert page.request.get(BASE+'/__closed/coordinated-fixture/stale').ok
                    dialog.locator('#accept-setpoint-confirm').click()
                    expect(card(5).locator('.operation-status')).to_contain_text('no está disponible')
                    assert page.request.get(BASE+'/__closed/setpoint-evidence').json()['sets'] == baseline['sets']
                    page.reload()
                    before = page.request.get(BASE+'/__closed/setpoint-evidence').json()
                    card(5).locator('.decrease').click()
                    dialog.locator('#accept-setpoint-confirm').click()
                    expect(card(5).locator('.setpoint')).to_have_text('25°')
                    expect(card(5).locator('.operation-status')).to_have_text('Cambio confirmado')
                    evidence = page.request.get(BASE+'/__closed/setpoint-evidence').json()
                    assert evidence['sets'] == before['sets'] + ['room-5']
                    expected = json.loads(json.dumps(before['blocks']))
                    expected['room-5'].update({'32':'0c80','33':'0c00'})
                    assert evidence['blocks'] == expected
                    view = page.request.get(BASE+'/api/controllers/room-5').json()
                    assert view['state']['heating_setpoint'] == 24
                    assert view['last_operation']['before_state']['setpoint_authorization']['targets'] == [32,33]
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                # Both contracts in the same room: an external mode change
                # invalidates pair consent; a confirmed pair survives mode SET.
                for viewport in ({'width':1440,'height':1100}, {'width':390,'height':844}):
                    page.set_viewport_size(viewport)
                    assert page.request.get(BASE+'/__closed/coordinated-fixture/coexistence').ok
                    page.reload()
                    expect(card(5).locator('.setpoint')).to_have_text('26°')
                    before = page.request.get(BASE+'/__closed/setpoint-evidence').json()
                    card(5).locator('.decrease').click()
                    expect(dialog).to_be_visible()
                    assert page.request.get(BASE+'/__closed/coordinated-fixture/external-dry').ok
                    dialog.locator('#accept-setpoint-confirm').click()
                    expect(card(5).locator('.operation-status')).to_contain_text('no está disponible')
                    assert page.request.get(BASE+'/__closed/setpoint-evidence').json()['sets'] == before['sets']
                    assert page.request.get(BASE+'/__closed/coordinated-fixture/coexistence').ok
                    page.reload()
                    card(5).locator('.decrease').click()
                    expect(dialog).to_contain_text('Frío 26 → 25 °C; Calor 25 → 24 °C')
                    dialog.locator('#accept-setpoint-confirm').click()
                    expect(card(5).locator('.operation-status')).to_have_text('Cambio confirmado')
                    retained = page.request.get(BASE+'/__closed/setpoint-evidence').json()
                    for mode in (0,1,4,3):
                        card(5).locator('.mode').select_option(str(mode))
                        expect(card(5).locator('.operation-status')).to_have_text('Cambio confirmado')
                        expect(card(5).locator('.mode')).to_have_value(str(mode))
                        expect(card(5).locator('.setpoint')).to_have_text('24°' if mode == 4 else '25°' if mode == 3 else '—')
                        assert page.request.get(BASE+'/__closed/setpoint-evidence').json() == retained
                        view = page.request.get(BASE+'/api/controllers/room-5').json()
                        assert view['manual_controls']['setpoint_proposals'] == []
                        assert view['last_operation']['before_state']['mode_contract']['to_mode'] == mode
                        assert view['state']['heating_setpoint'] == 24
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert errors==[],errors
                browser.close()
        finally: stop()
        print("PASS Chromium local HTTP: six synthetic setpoint/fan structures, independent Cool/Heat banks and Auto display without new writes, unknown/missing fields, raw extensions, strict GET, Fan/Dry and conditional Ventilation commissioning/controls, durable mode STOP, coordinated-pair consent/cancel/stale rejection, single SET and byte isolation, capability settings, uncertain without replay, history/restart, desktop/mobile; 0 JS errors; real drivers forbidden")


if __name__=="__main__":
    if len(sys.argv)==3 and sys.argv[1]=="--serve-closed": serve(sys.argv[2])
    elif len(sys.argv)==1: main()
    else: raise SystemExit("Closed harness accepts no hardware configuration")
