import QtQuick
import QtQuick.Controls
ScrollBar {
    id: bar
    padding: 2
    visible: size < 1 && policy !== ScrollBar.AlwaysOff
    hoverEnabled: true
    policy: ScrollBar.AsNeeded
    implicitWidth: orientation===Qt.Vertical ? 9 : 0
    implicitHeight: orientation===Qt.Horizontal ? 9 : 0
    contentItem: Rectangle { implicitWidth: 5; implicitHeight: 5; radius: 3; color: AppStyle.muted; opacity: bar.pressed ? .7 : bar.active || bar.hovered ? .4 : .16; Behavior on opacity { NumberAnimation { duration: 160 } } }
    background: Item {}
}
