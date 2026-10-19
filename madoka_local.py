#!/usr/bin/env python3
"""Local-only six-controller application. Importing does not access Bluetooth."""
import argparse
import asyncio
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="Control local de Daikin Madoka · http://127.0.0.1:8090")
    parser.add_argument("--config", help="Ruta privada; por defecto se utiliza office.json existente")
    parser.add_argument("--demo", action="store_true", help="Solo simuladores, sin Bluetooth")
    args = parser.parse_args(argv)
    budget = None
    listener = None
    try:
        import uvicorn
        if not args.demo:
            from madoka_multiroom.local_runtime import native_environment
            native_environment()
        # Own the HTTP port before opening a ledger or starting native tasks.
        # A second instance cannot briefly connect and then fail its HTTP bind.
        import socket
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 8090))
        listener.listen(128)
        listener.setblocking(False)
        if args.demo:
            from madoka_multiroom.api import build_demo_app
            from madoka_multiroom.config import demo_config
            app = build_demo_app(demo_config())
        else:
            from madoka_multiroom.local_runtime import native_environment, load_local_config, default_config_path, LocalSession, LocalFleet
            from madoka_multiroom.native_access import native_state_directory
            from madoka_multiroom.native_recovery import boot_time
            from madoka_multiroom.backends.physical_sessions import PhysicalSessionBudget, ConnectionPlan
            from madoka_multiroom.api import build_local_app
            config = load_local_config(args.config or default_config_path())
            directory = native_state_directory()
            boot = boot_time()
            sessions = {d.id: LocalSession(d.address, d.id, boot) for d in config.rooms.devices}
            budget = PhysicalSessionBudget(directory / "native-sessions.sqlite",
                ConnectionPlan("retained", config.rooms.max_active_devices, config.rooms.max_concurrent_operations),
                local_sessions=sessions)
            fleet = LocalFleet(config, budget, sessions, settings_path=directory / "local-controls.json")
            app = build_local_app(config, str(directory / "office-observations.db"), fleet=fleet)
        print("Madoka DEMO · sin Bluetooth" if args.demo else f"Madoka local · {len(config.rooms.devices)} Daikin configurados")
        print("Abre http://127.0.0.1:8090 · Ctrl+C para cerrar")
        # SDK diagnostics can contain private UUIDs. The API provides safe room
        # errors; keep raw native output out of the daily-use terminal/browser.
        from madoka_readonly_canary import silence_backend_output
        with silence_backend_output():
            server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8090, workers=1,
                proxy_headers=False, access_log=False, log_level="critical", loop="asyncio",
                timeout_graceful_shutdown=60))
            asyncio.run(server.serve(sockets=[listener]))
        if not server.started:
            print("No se pudo iniciar la aplicación. Comprueba los datos locales y consulta docs/local.md.")
            return 2
        return 0
    except KeyboardInterrupt:
        return 0
    except BlockingIOError:
        print("Madoka ya está abierto. Utiliza http://127.0.0.1:8090; no se ha iniciado otra conexión.")
        return 2
    except OSError as exc:
        import errno
        if exc.errno == errno.EADDRINUSE:
            print("El puerto 8090 ya está en uso. Abre la aplicación existente o ciérrala antes de iniciar otra.")
        elif isinstance(exc, FileNotFoundError):
            print("No se encuentra office.json. Consulta la instalación breve en docs/local.md.")
        elif isinstance(exc, PermissionError):
            print("No se pueden leer los datos privados. Usa el mismo usuario de macOS que la instalación anterior.")
        else:
            print("No se pudo abrir el servidor o los datos locales; comprueba la instalación y el espacio libre.")
        return 2
    except Exception as exc:
        from madoka_multiroom.local_runtime import local_error
        print(str(exc) if isinstance(exc, ValueError) and not hasattr(exc, "errors") else local_error(exc))
        return 2
    finally:
        if listener:
            listener.close()
        if budget:
            budget.close_storage()


if __name__ == "__main__":
    sys.exit(main())
