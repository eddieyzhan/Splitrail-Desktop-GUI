# Native distribution notices

Splitrail Desktop application code is MIT licensed; see LICENSE. Native downloads include unmodified, dynamically loaded Python and Qt libraries. The source-only `.pyz` does not bundle these dependencies.

- Python: Python Software Foundation license, [source and license](https://www.python.org/downloads/source/).
- PySide6, Shiboken6 and Qt 6.11.2: LGPLv3/GPL/commercial terms as applicable. This application uses the LGPL option. [Qt for Python licenses](https://doc.qt.io/qtforpython-6/licenses.html), [Qt source archives](https://download.qt.io/archive/qt/6.11/6.11.2/), [PySide/Shiboken source](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/).
- ijson 3.5.1 and bundled YAJL: BSD/ISC terms; [ijson source](https://github.com/ICRAR/ijson/tree/v3.5.1), [YAJL source](https://github.com/lloyd/yajl).
- PyInstaller 6.22.3 bootloader: GPL with the bootloader exception permitting distribution of non-GPL applications; [license](https://pyinstaller.org/en/stable/license.html).

Upstream package license files are included under `licenses/` and in the runtime's Qt license resources. Keep these notices with redistributed copies. The libraries remain separate files; users can replace compatible libraries or rebuild the app from its public source using `scripts/build_native.py`. Any local macOS signature must be refreshed after modifying a bundle. No restriction on reverse engineering for debugging modifications to LGPL libraries is imposed by this application.
