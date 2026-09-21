import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
Rectangle {
    id: table
    property var rows: []
    property var columns: []
    property string emptyText: "No usage in this period"
    radius: 18; color: AppStyle.surface; clip: true
    property real totalWeight: columns.reduce((a,c)=>a+c.weight,0)
    Flickable {
        id: horizontal; anchors.fill: parent; anchors.margins: 8; contentWidth: Math.max(width,690); contentHeight: height
        clip: true; boundsBehavior: Flickable.StopAtBounds
        ScrollBar.horizontal: SoftScrollBar {}
        ColumnLayout {
            width: horizontal.contentWidth; height: horizontal.height; spacing: 0
            Row { Layout.fillWidth: true; Layout.preferredHeight: 44
                Repeater { model: table.columns; delegate: TextLabel {
                    required property var modelData; required property int index
                    width: horizontal.contentWidth*modelData.weight/table.totalWeight; height: 44
                    text: modelData.label; color: AppStyle.muted; font.pixelSize: 11; font.weight: Font.Medium
                    leftPadding: 16; rightPadding: 16; horizontalAlignment: index===0 ? Text.AlignLeft : Text.AlignRight
                } }
            }
            ListView {
                id: list; objectName: "usageTable"; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; model: table.rows; spacing: 2
                boundsBehavior: Flickable.StopAtBounds; ScrollBar.vertical: SoftScrollBar {}
                delegate: Rectangle {
                    id: dataRow; required property var modelData; required property int index
                    width: list.width; height: 54; radius: 10; color: hover.hovered ? AppStyle.fill : "transparent"
                    HoverHandler { id: hover }
                    Row { anchors.fill: parent
                        Repeater { model: table.columns; delegate: TextLabel {
                            required property var modelData; required property int index
                            width: list.width*modelData.weight/table.totalWeight; height: 54
                            text: dataRow.modelData[modelData.key] || "—"; leftPadding: 16; rightPadding: 16
                            font.pixelSize: 13; font.weight: index===0 ? Font.Medium : Font.Normal
                            color: index===0 || modelData.key==="cost" ? AppStyle.ink : AppStyle.muted
                            horizontalAlignment: index===0 ? Text.AlignLeft : Text.AlignRight
                            ToolTip.visible: hover.hovered && truncated
                            ToolTip.text: text
                        } }
                    }
                }
            }
        }
    }
    TextLabel { visible: table.rows.length===0; text: table.emptyText; anchors.centerIn: parent; color: AppStyle.muted; font.pixelSize: 13 }
}
