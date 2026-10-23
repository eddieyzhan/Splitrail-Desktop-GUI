"""macOS menu-bar entry point. The dashboard can sleep while private sync continues."""
from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


def rail_icon():
    pixmap = QPixmap(36, 36)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor('black'), 2.2, Qt.SolidLine, Qt.RoundCap))
    painter.drawLine(5, 3, 5, 15)
    painter.drawLine(13, 3, 13, 15)
    painter.drawLine(5, 7, 13, 7)
    painter.drawLine(5, 11, 13, 11)
    painter.end()
    icon = QIcon(pixmap)
    icon.setIsMask(True)  # Native template icon adapts to light and dark menu bars.
    return icon


class MenuBar(QObject):
    def __init__(self, app, dashboard, controller):
        super().__init__(app)
        self.app, self.dashboard, self.controller = app, dashboard, controller
        self.menu = QMenu()
        self.status = self.menu.addAction('Weekly quota')
        self.status.setEnabled(False)
        self.updated = self.menu.addAction('')
        self.updated.setEnabled(False)
        self.menu.addSeparator()
        self.menu.addAction('Open Splitrail', self.show_window)
        self.menu.addAction('Refresh usage', controller.refresh)
        self.sync_action = self.menu.addAction('Sync now', controller.syncNow)
        self.menu.addSeparator()
        self.menu.addAction('Quit Splitrail', self.quit)
        self.menu.aboutToShow.connect(self.update_status)
        self.icon = QSystemTrayIcon(rail_icon(), self)
        self.icon.setToolTip('Splitrail')
        self.icon.setContextMenu(self.menu)
        self.icon.show()
        app.setQuitOnLastWindowClosed(False)
        controller._state['menuBarMode'] = True
        controller._emit()
        dashboard.release_when_hidden = True
        app.aboutToQuit.connect(self.icon.hide)

    def update_status(self):
        self.controller.tick()
        quota = self.controller.clock['quota']
        if quota.get('available'):
            suffix = ' · cached' if quota.get('stale') else ''
            self.status.setText(f"Weekly quota: {quota['used']:g}% used{suffix}")
            self.updated.setText('Updated ' + quota.get('age', 'just now'))
        else:
            self.status.setText('Weekly quota unavailable')
            self.updated.setText('Open Splitrail for details')
        self.sync_action.setEnabled(self.controller.state['connected'] and not self.controller.state['syncBusy'])

    def show_window(self):
        self.dashboard.show()

    def quit(self):
        self.controller._state['menuBarMode'] = False
        self.controller.close()
        self.app.quit()

    def close(self):
        self.icon.hide()
        self.menu.deleteLater()

