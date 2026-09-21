import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
Rectangle {
    id: segments
    property var options: []
    property int selected: 0
    signal chosen(int index)
    implicitHeight: 36
    implicitWidth: row.implicitWidth + 6
    color: AppStyle.fill; radius: 11
    RowLayout {
        id: row; anchors.fill: parent; anchors.margins: 3; spacing: 2
        Repeater {
            model: segments.options
            delegate: Button {
                required property string modelData; required property int index
                Layout.fillWidth: true; Layout.fillHeight: true; implicitWidth: label.implicitWidth+24
                onClicked: segments.chosen(index)
                background: Rectangle { radius: 8; color: segments.selected===index ? AppStyle.surface : parent.hovered ? AppStyle.hover : "transparent"; border.width: parent.activeFocus ? 1 : 0; border.color: AppStyle.accent; Behavior on color { ColorAnimation { duration: 120 } } }
                contentItem: TextLabel { id: label; text: modelData; font.pixelSize: 12; font.weight: segments.selected===index ? Font.DemiBold : Font.Normal; color: segments.selected===index ? AppStyle.ink : AppStyle.muted; horizontalAlignment: Text.AlignHCenter }
            }
        }
    }
}
