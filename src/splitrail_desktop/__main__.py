from __future__ import annotations

import argparse
import sys

from . import __version__


def main(argv: list[str] | None = None) -> int:
    from .platform_support import prepare_desktop_path
    prepare_desktop_path()
    parser = argparse.ArgumentParser(description="Splitrail desktop usage explorer")
    parser.add_argument("--onboarding", action="store_true", help="Open the guided setup")
    parser.add_argument("--demo", action="store_true", help="Try the interface with synthetic data; no account or usage access")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--self-check", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-ui", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--codex-usage", action="store_true", help="Show deduplicated Codex usage across devices")
    parser.add_argument("--export-usage", metavar="FILE", help="Export this computer's Codex usage (.json.gz recommended)")
    parser.add_argument("--import-usage", metavar="FILE", help="Merge a Codex usage export; repeat imports are safe")
    parser.add_argument("--setup-sync", metavar="OWNER/REPO", help="Configure a private GitHub repository for device sync")
    parser.add_argument("--receive-only", action="store_true", help="Receive usage from other devices; never upload this computer's usage")
    parser.add_argument("--sync-usage", action="store_true", help="Sync device usage now and exit")
    parser.add_argument('--sync-codex-only', action='store_true', help='Sync Codex totals without the Splitrail collector')
    parser.add_argument('--auto-sync', action='store_true', help='Opt in to sync on usage refresh')
    args = parser.parse_args(argv)
    if args.setup_sync or args.sync_usage:
        import json
        import os
        from .sync import configure, sync_now, SyncError
        try:
            if args.setup_sync:
                configure(args.setup_sync, scope='codex' if args.sync_codex_only else 'all',
                          automatic=args.auto_sync, receive_only=args.receive_only)
                print('Private GitHub sync configured')
            if args.sync_usage:
                print(json.dumps(sync_now(), indent=2))
        except (SyncError, OSError, ValueError, TypeError, KeyError) as exc:
            print(f"Usage sync failed: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.export_usage or args.import_usage:
        import json
        from pathlib import Path
        from .portable import import_export, scan_codex, summary, write_export
        try:
            if args.import_usage:
                print(f"Imported {import_export(Path(args.import_usage)):,} new requests")
            if args.export_usage:
                payload = scan_codex()
                write_export(Path(args.export_usage), payload)
                print(json.dumps(summary(payload), indent=2))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            print(f"Usage transfer failed: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.version:
        print(f"Splitrail Desktop {__version__}")
        return 0
    if args.self_check:
        import ijson
        from PySide6.QtCore import qVersion
        from .pricing import CATALOG
        assert len(CATALOG['models']) >= 50
        print(f"Splitrail Desktop {__version__}: self-check passed (Qt {qVersion()})")
        return 0
    try:
        import ijson  # Validate the streaming dependency before opening the GUI.
        from .qt_app import run
        return run(demo=args.demo, onboarding=args.onboarding, smoke=args.smoke_ui, codex_usage=args.codex_usage)
    except ImportError as exc:
        if 'PySide6' not in str(exc) and 'ijson' not in str(exc):
            raise
        print('Install the desktop UI with: python -m pip install "PySide6>=6.8,<7" "ijson>=3.4,<4"', file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
