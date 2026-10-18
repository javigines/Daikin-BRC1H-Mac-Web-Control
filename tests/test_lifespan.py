import asyncio
import sqlite3

import pytest

import daikin_server as d


@pytest.mark.parametrize("poller_fails,disconnect_fails", [(False, False), (True, False), (False, True), (True, True)])
def test_shutdown_always_disconnects_and_closes_store(monkeypatch, tmp_path, poller_fails, disconnect_fails):
    async def case():
        started = asyncio.Event()
        stopped = asyncio.Event()
        class Controller:
            disconnected = False
            async def disconnect(self):
                self.disconnected = True
                assert stopped.is_set()
                if disconnect_fails:
                    raise ValueError("disconnect failed")

        async def poller(controller, store):
            started.set()
            try:
                if poller_fails:
                    raise RuntimeError("poller failed")
                await asyncio.Event().wait()
            finally:
                stopped.set()

        monkeypatch.setattr(d, "run_poller", poller)
        ctrl = Controller()
        store = d.HistoryStore(str(tmp_path / "history.db"))
        app = d.build_app(ctrl, store)
        async def use_app():
            async with app.router.lifespan_context(app):
                await started.wait()
        if poller_fails or disconnect_fails:
            with pytest.raises((ValueError, RuntimeError)):
                await use_app()
        else:
            await use_app()
        assert ctrl.disconnected and stopped.is_set()
        with pytest.raises(sqlite3.ProgrammingError):
            store.query_since(0)
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(case())
