import QtQuick
import QtQuick.Controls

Item {
    id: bar
    property real used: 0
    property real pace: -1
    property int barHeight: 10
    implicitHeight: barHeight + 4

    Rectangle {
        anchors.verticalCenter: parent.verticalCenter
        width: parent.width
        height: bar.barHeight
        radius: height / 2
        color: AppStyle.fill
        Rectangle {
            width: parent.width * Math.max(0, Math.min(100, bar.used)) / 100
            height: parent.height
            radius: parent.radius
            color: AppStyle.teal
        }
    }

    Rectangle {
        id: paceMarker
        objectName: "paceMarker"
        visible: bar.pace >= 0 && bar.pace <= 100
        width: 9
        height: 9
        radius: width / 2
        x: Math.max(0, Math.min(bar.width - width, bar.width * bar.pace / 100 - width / 2))
        anchors.verticalCenter: parent.verticalCenter
        color: AppStyle.ink
        border.color: AppStyle.surface
        border.width: 1
        Accessible.name: "Even pace: " + Math.round(bar.pace) + "% used by now"
        MouseArea {
            anchors.fill: parent
            hoverEnabled: true
            acceptedButtons: Qt.NoButton
            ToolTip.visible: containsMouse
            ToolTip.text: paceMarker.Accessible.name
        }
    }
}
