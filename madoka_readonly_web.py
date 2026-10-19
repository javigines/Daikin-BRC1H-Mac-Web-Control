#!/usr/bin/env python3
"""Default OFFLINE. Dedicated attended, authenticated one-controller GET web app."""
import argparse
import os
import secrets
import sys

from madoka_readonly_canary import PreflightError, silence_backend_output, protocol_selftest


def main(argv=None):
    parser = argparse.ArgumentParser(description="Default OFFLINE; new owner GO required for native web use")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--selftest", action="store_true")
    modes.add_argument("--serve-read-only", action="store_true")
    parser.add_argument("--inventory")
    parser.add_argument("--config", help="private six-room config with exact selected inventory mapping")
    parser.add_argument("--controller-id")
    parser.add_argument("--reviewed-sha")
    parser.add_argument("--authorization-reference")
    parser.add_argument("--port", type=int, default=8091)
    args = parser.parse_args(argv)
    if args.selftest:
        return protocol_selftest()
    if not args.serve_read_only:
        print("OFFLINE: no Bluetooth initialized. Use --selftest.")
        return 0
    budget = None
    try:
        if not 1024 <= args.port <= 65535:
            raise PreflightError("Use an unprivileged loopback port")
        from madoka_multiroom.native_access import authorize, native_state_directory, load_private_config
        from madoka_multiroom.api import build_native_readonly_app
        from madoka_multiroom.backends.physical_sessions import PhysicalSessionBudget
        from madoka_multiroom.backends.native_fleet import NativeReadonlyFleet
        grant = authorize(inventory=args.inventory, controller_id=args.controller_id,
            reviewed_sha=args.reviewed_sha, authorization_reference=args.authorization_reference)
        config = load_private_config(args.config)
        directory = native_state_directory()
        budget = PhysicalSessionBudget(directory / "native-sessions.sqlite", native_authorization=grant)
        if budget.owners:
            raise PreflightError("Unresolved native ownership; no new attempt permitted")
        fleet = NativeReadonlyFleet(config, budget, grant)
        password = secrets.token_urlsafe(32)
        app = build_native_readonly_app(config, str(directory / "observations.db"), fleet=fleet, password=password)
        print(f"Open http://127.0.0.1:{args.port}/ with username operator")
        print(f"Ephemeral password (keep private): {password}")
        print("No connection at startup. Connect once in the attended browser. Ctrl-C stops; ownership remains quarantined.")
        # UUID-bearing SDK/native output must never reach shared server logs.
        with silence_backend_output():
            import uvicorn
            try:
                uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1,
                            proxy_headers=False, access_log=False, log_level="critical",
                            loop="asyncio", timeout_graceful_shutdown=30)
            except KeyboardInterrupt:
                # Uvicorn re-raises SIGINT after its lifespan has drained.
                # Durable ownership still decides whether review is required.
                pass
        print("STOP: preserve native records; public cleanup is not proof of native release.")
        return 0 if not fleet.attempted else 3
    except (Exception, KeyboardInterrupt):
        print("STOP: native web preflight or execution failed. Preserve records; no automatic retry.")
        return 2
    finally:
        if budget is not None:
            budget.close_storage()


if __name__ == "__main__":
    sys.exit(main())
