import QtQuick
import QtQuick.Controls
Switch {
    id: control
    implicitHeight: 34
    implicitWidth: 44
    padding: 0
    indicator: Rectangle {
        width: 38; height: 23; y: (control.height-height)/2
        radius: 12; color: control.checked ? AppStyle.accent : AppStyle.line
        border.color: control.activeFocus ? AppStyle.accent : "transparent"; border.width: 2
        Rectangle { x: control.checked ? 17 : 3; y: 3; width: 17; height: 17; radius: 9; color: control.checked ? AppStyle.accentInk : AppStyle.surface; Behavior on x { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } } }
        Behavior on color { ColorAnimation { duration: 140 } }
    }
    contentItem: Item {}
}
