"""Bundle only the LGPL Qt Quick modules this app imports, not all Qt add-ons."""
from pathlib import PurePath
from PyInstaller.utils.hooks.qt import add_qt6_dependencies, pyside6_library_info

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
# Developer profilers include optional GPL-only Quick 3D tooling. They are not
# needed by the application and must not be part of the production runtime.
binaries = [entry for entry in binaries if 'qmltooling' not in entry[0].lower()]
qml_binaries, qml_datas = pyside6_library_info.collect_qtqml_files()


def needed(entry):
    parts = PurePath(entry[1]).parts
    relative = parts[parts.index('qml') + 1:]
    if not relative:
        return True
    if relative[0] == 'QtQml':
        return len(relative) == 1 or relative[1] in {'Models', 'WorkerScript'}
    if relative[0] != 'QtQuick':
        return False
    if len(relative) > 2 and relative[1] == 'Controls':
        return relative[2] in {'Basic', 'impl'}
    return len(relative) == 1 or relative[1] in {
        'Controls', 'Templates', 'Layouts', 'Window',
    }


binaries += [entry for entry in qml_binaries if needed(entry)]
datas += [entry for entry in qml_datas if needed(entry)]
