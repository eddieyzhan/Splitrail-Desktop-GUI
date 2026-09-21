import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
Rectangle {
    id: card
    property var points: []
    property int metric: 0
    property real maximum: Math.max(1, ...points.map(p=>metric===0 ? p.tokens : p.cost))
    radius: 20; color: AppStyle.surface
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 24; spacing: 20
        RowLayout { Layout.fillWidth: true; TextLabel { text: "Activity"; font.pixelSize: 16; font.weight: Font.DemiBold; Layout.fillWidth: true } Segmented { options: ["Tokens", "Cost"]; selected: card.metric; onChosen: function(i){card.metric=i} } }
        Item {
            id: plot; Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 150
            property int hovered: -1
            function number(v) {return card.metric===1 ? "$"+v.toFixed(v<10 ? 2 : 0) : v>=1e9 ? (v/1e9).toFixed(1)+"B" : v>=1e6 ? (v/1e6).toFixed(1)+"M" : v>=1000 ? (v/1000).toFixed(0)+"K" : v.toFixed(0)}
            Repeater { model: 4; delegate: Item {
                required property int index; y: index*(plot.height-28)/3; width: plot.width; height: 1
                TextLabel { text: plot.number(card.maximum*(1-index/3)); color: AppStyle.faint; font.pixelSize: 10; width: 44; y: -7 }
                Rectangle { x: 48; width: parent.width-48; height: 1; color: AppStyle.line; opacity: .8 }
            } }
            Row {
                id: bars; anchors.left: parent.left; anchors.leftMargin: 56; anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.bottomMargin: 28
                spacing: 0
                Repeater { model: card.points; delegate: Item {
                    required property var modelData; required property int index
                    width: bars.width/Math.max(1,card.points.length); height: bars.height
                    Rectangle {
                        width: Math.max(3,Math.min(24,parent.width*.65)); anchors.horizontalCenter: parent.horizontalCenter; anchors.bottom: parent.bottom
                        height: Math.max(0,(card.metric===0 ? modelData.tokens : modelData.cost)/card.maximum*(parent.height-6))
                        radius: Math.min(4,width/3); color: plot.hovered===index ? AppStyle.accent : AppStyle.dark ? "#8093D0" : "#A2AFE9"
                        Behavior on height { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
                        Behavior on color { ColorAnimation { duration: 100 } }
                    }
                    MouseArea { anchors.fill: parent; hoverEnabled: true; onEntered: plot.hovered=index; onExited: plot.hovered=-1 }
                } }
            }
            Repeater { model: Math.min(5,card.points.length); delegate: TextLabel {
                required property int index
                property int pointIndex: Math.round(index*(card.points.length-1)/Math.max(1,Math.min(5,card.points.length)-1))
                text: card.points[pointIndex] ? card.points[pointIndex].label : ""
                x: 56+index*(plot.width-88)/Math.max(1,Math.min(5,card.points.length)-1)-width/2; y: plot.height-16
                color: AppStyle.faint; font.pixelSize: 10
            } }
            TextLabel { anchors.centerIn: parent; visible: card.points.length===0; text: "Your activity will appear here"; color: AppStyle.muted }
            Rectangle {
                visible: plot.hovered>=0; z: 3; width: hoverText.implicitWidth+24; height: 50; radius: 10; color: AppStyle.ink
                x: Math.min(plot.width-width, Math.max(40,56+(plot.hovered+.5)*(plot.width-56)/Math.max(1,card.points.length)-width/2)); y: -12
                TextLabel { id: hoverText; anchors.centerIn: parent; color: AppStyle.surface; font.pixelSize: 11; text: plot.hovered>=0 && card.points[plot.hovered] ? card.points[plot.hovered].label+"\n"+plot.number(card.metric===0 ? card.points[plot.hovered].tokens : card.points[plot.hovered].cost) : ""; horizontalAlignment: Text.AlignHCenter; lineHeight: 1.3 }
            }
        }
    }
}
