#!/usr/bin/env python3
"""Explicitly local multiroom DEMO launcher; never imports or initializes Bleak."""
import argparse
import logging

from madoka_multiroom.config import demo_config, load_config


def main():
    parser = argparse.ArgumentParser(description="Madoka multiroom offline DEMO")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true", help="run isolated simulated controllers")
    mode.add_argument("--real", action="store_true", help="reserved; rejected in iteration 2")
    mode.add_argument("--migrate-history", metavar="SOURCE", help="offline copy/import of legacy SQLite")
    parser.add_argument("--config", help="validated local JSON; otherwise six fictional demo rooms")
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1"])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default="demo_history.db", help="DEMO database; never use legacy/real data")
    parser.add_argument("--output", help="new destination for offline history migration")
    parser.add_argument("--legacy-controller-id", help="explicit logical owner of legacy history")
    args = parser.parse_args()
    if args.real:
        parser.error("Real BLE is disabled. A separate physical authorization and reviewed implementation are required.")
    if args.migrate_history:
        if not args.output or not args.legacy_controller_id:
            parser.error("migration requires --output NEW_FILE and --legacy-controller-id")
        from madoka_multiroom.storage import migrate_copy
        migrate_copy(args.migrate_history, args.output, args.legacy_controller_id)
        print("Offline copy migrated. Source unchanged. Output is real-mode data, not a DEMO database.")
        return
    if args.output or args.legacy_controller_id:
        parser.error("migration options require --migrate-history")
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    config = load_config(args.config) if args.config else demo_config()
    from madoka_multiroom.api import build_demo_app
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    print(f"DEMO only · no Bluetooth · http://127.0.0.1:{args.port}", flush=True)
    uvicorn.run(build_demo_app(config, args.db), host=args.host, port=args.port,
                workers=1, proxy_headers=False, limit_concurrency=32, timeout_keep_alive=5)


if __name__ == "__main__":
    main()
