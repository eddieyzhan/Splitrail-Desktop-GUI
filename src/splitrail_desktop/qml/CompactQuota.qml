import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: compact
    objectName: "compactQuotaView"
    property var q: bridge.clock.quota
    color: AppStyle.surface

    ColumnLayout {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.margins: 16
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            TextLabel {
                text: "Weekly quota"
                Layout.fillWidth: true
                font.pixelSize: 13
                color: AppStyle.muted
            }
            TextLabel {
                objectName: "compactQuotaUsage"
                text: compact.q.available ? Math.round(compact.q.used) + "% used" : "Unavailable"
                font.pixelSize: 18
                font.weight: Font.DemiBold
            }
        }
        WeeklyQuotaBar {
            objectName: "compactQuotaBar"
            Layout.fillWidth: true
            barHeight: 8
            used: compact.q.available ? compact.q.used : 0
            pace: compact.q.available && compact.q.paceUsed !== null && compact.q.paceUsed !== undefined ? compact.q.paceUsed : -1
        }
        TextLabel {
            objectName: "compactQuotaAge"
            Layout.fillWidth: true
            font.pixelSize: 11
            color: AppStyle.muted
            text: (compact.q.stale ? "Cached - " : "") + (compact.q.available
                ? compact.q.age || "Not yet updated"
                : bridge.state.quotaBusy ? "Updating quota..." : "No weekly quota data")
            MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                acceptedButtons: Qt.NoButton
                ToolTip.visible: containsMouse
                ToolTip.text: compact.q.available ? "Resets " + compact.q.reset + "\n" + compact.q.age : "Enlarge the window for quota details."
            }
        }
    }
}
