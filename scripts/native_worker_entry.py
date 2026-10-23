"""Headless Mac worker: deliberately never import PySide6 or the Qt controller."""
import sys
from splitrail_desktop.native_worker import main

if len(sys.argv) == 2 and sys.argv[1] == '--smoke-collector':
    from splitrail_desktop.__main__ import main as portable_main
    raise SystemExit(portable_main(['--smoke-collector']))
if len(sys.argv) == 4 and sys.argv[1] == '--worker':
    from splitrail_desktop.worker import worker_main
    raise SystemExit(worker_main(*sys.argv[2:]))
raise SystemExit(main(*sys.argv[1:]))
