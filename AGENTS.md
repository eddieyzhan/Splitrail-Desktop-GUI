# Project notes

- Python 3.11+ desktop application using PySide6/Qt Quick. Install with `python -m pip install .`. See README.md for setup and architecture.
- Never commit runtime usage, credentials, local settings, session logs, or screenshots of real accounts. Fixtures and public screenshots must be synthetic.
- GitHub usage sync is opt-in and must verify repository privacy before every transfer. Never create GitHub Actions workflows in usage repositories.
- Run `PYTHONPATH=src python3 -m unittest discover -s tests -v`, `python3 scripts/build.py`, and the built application's `--self-check` after behavior changes. UI tests can use the offscreen Qt platform; use a real display or Xvfb for visual checks. QML resources must be included in both wheel and zipapp builds.
- Install `.[test]` for timezone fixtures on Windows. Native release builds use `scripts/requirements-release.txt` and `scripts/build_native.py`; packaging is allowlisted and smoke-tested. Codemagic is manual-only: run once after local checks, without paid usage or automatic triggers.
