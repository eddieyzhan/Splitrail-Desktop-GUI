import QtQuick
import QtQuick.Controls

Button {
    id: control
    property string explanation: ""
    property string label: "More information"
    implicitWidth: 26; implicitHeight: 26
    leftPadding: 3; rightPadding: 3; topPadding: 3; bottomPadding: 3
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    Accessible.name: label
    Accessible.description: explanation
    background: Item {
        Rectangle {
            anchors.centerIn: parent
            width: Math.min(parent.width, parent.height); height: width
            radius: width / 2
            color: control.hovered ? AppStyle.hover : "transparent"
            border.width: control.activeFocus ? 1 : 0
            border.color: AppStyle.accent
        }
    }
    contentItem: Item {
        Icon {
            anchors.centerIn: parent
            width: 20; height: 20; name: "info"
            color: control.hovered || control.activeFocus ? AppStyle.accent : AppStyle.muted
        }
    }
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
