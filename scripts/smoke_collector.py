"""Exercise native discovery and All tools sync without reading real user logs."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path


def check_collector(executable: Path, *, cwd: Path) -> None:
    with tempfile.TemporaryDirectory(prefix='splitrail-collector-') as directory:
        root = Path(directory)
        tokens = dict(inputTokens=1000000, outputTokens=100000, reasoningTokens=50000,
                      cachedTokens=500000, cacheReadTokens=500000, cost=0)
        payload = {'analyzer_stats': [{'analyzer_name': 'Codex CLI', 'num_conversations': 1,
                   'daily_stats': {'2026-09-30': {'stats': tokens,
                       'models': {'gpt-6.1-sol': 1}, 'model_stats': {'gpt-6.1-sol': tokens}}},
                   'messages': [{'date': '2026-09-30T10:00:00Z', 'role': 'assistant',
                                 'model': 'gpt-6.1-sol', 'stats': tokens}]}]}
        script = root / 'collector.py'
        script.write_text(
            'import sys\n'
            'if sys.argv[1:] == ["--version"]:\n'
            '    print("splitrail 3.10.3", file=sys.stderr)\n'
            'elif sys.argv[1:] == ["stats", "--include-messages"]:\n'
            f'    print({json.dumps(payload)!r})\n'
            '    print("Unknown model: gpt-6.1-sol. Defaulting to $0.", file=sys.stderr)\n'
            'else:\n    raise SystemExit(2)\n', encoding='utf-8')
        if os.name == 'nt':
            collector = root / 'splitrail.cmd'
            collector.write_text('@echo off\n' + subprocess.list2cmdline([sys.executable, str(script)]) + ' %*\n')
        else:
            collector = root / 'splitrail'
            collector.write_text('#!/bin/sh\nexec ' + shlex.join([sys.executable, str(script)]) + ' "$@"\n')
            collector.chmod(0o755)
        environment = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ.get('PATH', os.defpath),
                       'LOCALAPPDATA': str(root / 'data'), 'XDG_DATA_HOME': str(root / 'data')}
        environment.pop('SPLITRAIL_BIN', None)
        subprocess.run([str(executable.resolve()), '--smoke-collector'], cwd=cwd,
                       env=environment, timeout=30, check=True)
