import QtQuick
import QtQuick.Controls

Button {
    id: control
    property string explanation: ""
    property string label: "More information"
    implicitWidth: 26; implicitHeight: 26
    padding: 4
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    Accessible.name: label
    Accessible.description: explanation
    background: Rectangle {
        radius: 8
        color: control.hovered ? AppStyle.hover : "transparent"
        border.width: control.activeFocus ? 1 : 0
        border.color: AppStyle.accent
    }
    contentItem: Icon {name: "info"; color: control.hovered || control.activeFocus ? AppStyle.accent : AppStyle.muted}
    ToolTip {
        id: help
        objectName: control.objectName + "Tooltip"
        parent: control
        visible: control.hovered || control.activeFocus
        delay: control.activeFocus ? 0 : 300
        timeout: -1
        width: 320; padding: 12; margins: 12
        x: (control.width - width) / 2
        y: -height - 8
        text: control.explanation
        contentItem: TextLabel {
            text: help.text; textFormat: Text.PlainText
            font.pixelSize: 12; wrapMode: Text.Wrap; elide: Text.ElideNone
        }
        background: Rectangle {color: AppStyle.surface; radius: 12; border.color: AppStyle.line}
    }
    Keys.onEscapePressed: { focus = false; help.close() }
}
