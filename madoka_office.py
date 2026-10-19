#!/usr/bin/env python3
"""Default OFFLINE. Explicitly authorized HTTPS macOS office service entrypoint."""
import argparse
import asyncio
import sys

from madoka_readonly_canary import silence_backend_output


def main(argv=None):
    parser = argparse.ArgumentParser(description="Default OFFLINE; office runtime requires fresh source/config-bound owner authorization")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--authorize-service", action="store_true")
    modes.add_argument("--serve", action="store_true")
    modes.add_argument("--recover-after-reboot", action="store_true")
    parser.add_argument("--config")
    parser.add_argument("--reviewed-sha")
    parser.add_argument("--authorization-reference")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--operator")
    parser.add_argument("--independent-reviewer")
    args = parser.parse_args(argv)
    if not any((args.authorize_service, args.serve, args.recover_after_reboot)):
        print("OFFLINE: no native driver initialized")
        return 0
    budget = None
    try:
        from madoka_multiroom.office_access import load_office_config, authorize_service, service_grants, read_private
        from madoka_multiroom.native_access import native_state_directory
        from madoka_multiroom.backends.physical_sessions import PhysicalSessionBudget, ConnectionPlan
        from madoka_multiroom.backends.office_native import OfficeFleet
        from madoka_multiroom.api import build_office_app
        config = load_office_config(args.config)
        if args.authorize_service:
            password = authorize_service(config, args.reviewed_sha, args.authorization_reference, args.days)
            print("Office HTTPS username: operator")
            print("Private office password (store securely): " + password)
            print("Authorization recorded. No Bluetooth operations performed. Existing ownership unchanged.")
            return 0
        grants, password = service_grants(config)
        if not grants:
            raise ValueError("At least one configured controller required")
        directory = native_state_directory()
        budget = PhysicalSessionBudget(directory / "native-sessions.sqlite",
            ConnectionPlan("retained", config.rooms.max_active_devices, config.rooms.max_concurrent_operations),
            native_authorization=next(iter(grants.values())), native_grants=grants)
        if args.recover_after_reboot:
            from madoka_multiroom.native_recovery import recover_after_reboot
            with silence_backend_output():
                asyncio.run(recover_after_reboot(budget, grants, operator=args.operator,
                    reviewer=args.independent_reviewer, reference=args.authorization_reference))
            print("Application ownership released with recorded reboot/native evidence. No SET or connection performed.")
            return 0
        if budget.owners:
            print("Unresolved rooms remain blocked and retain capacity; other authorized rooms may operate within the remaining limit")
        # Key and certificate are explicit private local files. Uvicorn serves
        # TLS directly; unencrypted LAN/proxy/forwarded-header paths do not exist.
        read_private(config.private_key)
        read_private(config.certificate)
        fleet = OfficeFleet(config, budget, grants)
        app = build_office_app(config, str(directory / "office-observations.db"), fleet=fleet, password=password)
        with silence_backend_output():
            import uvicorn
            try:
                uvicorn.run(app, host=config.host, port=config.port, workers=1, proxy_headers=False,
                    access_log=False, log_level="critical", loop="asyncio", timeout_graceful_shutdown=60,
                    ssl_certfile=config.certificate, ssl_keyfile=config.private_key)
            except KeyboardInterrupt:
                pass
        print("Office service stopped; inspect durable native ownership and operation outcomes before restarting")
        return 3 if budget.owners else 0
    except (Exception, KeyboardInterrupt):
        print("STOP: office authorization, ownership or execution failed. Preserve native records; no automatic write retry.")
        return 2
    finally:
        if budget:
            budget.close_storage()


if __name__ == "__main__":
    sys.exit(main())
