import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
ColumnLayout {
    id: month
    property date shown: new Date()
    property string first: ""
    property string last: ""
    signal picked(string value)
    readonly property int leading: (new Date(shown.getFullYear(),shown.getMonth(),1).getDay()+6)%7
    readonly property int dayCount: new Date(shown.getFullYear(),shown.getMonth()+1,0).getDate()
    function iso(day) {return shown.getFullYear()+"-"+String(shown.getMonth()+1).padStart(2,"0")+"-"+String(day).padStart(2,"0")}
    spacing: 10
    TextLabel { text: Qt.formatDate(month.shown,"MMMM yyyy"); font.pixelSize: 14; font.weight: Font.DemiBold; Layout.alignment: Qt.AlignHCenter; Layout.bottomMargin: 6 }
    GridLayout {
        Layout.fillWidth: true; columns: 7; rowSpacing: 4; columnSpacing: 2
        Repeater { model: ["M","T","W","T","F","S","S"]; TextLabel { required property string modelData; text: modelData; Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; color: AppStyle.faint; font.pixelSize: 11; Layout.preferredHeight: 24 } }
        Repeater { model: 42; delegate: Button {
            required property int index
            property int day: index-month.leading+1
            property bool realDay: day>=1 && day<=month.dayCount
            property string value: realDay ? month.iso(day) : ""
            property bool endpoint: realDay && (value===month.first || value===month.last)
            property bool inRange: realDay && month.first!=="" && month.last!=="" && value>month.first && value<month.last
            Layout.fillWidth: true; Layout.preferredWidth: 35; Layout.preferredHeight: 35
            enabled: realDay
            Accessible.name: value
            onClicked: month.picked(value)
            background: Rectangle { radius: 10; color: parent.endpoint ? AppStyle.accent : parent.inRange ? AppStyle.accentFill : parent.hovered && parent.realDay ? AppStyle.fill : "transparent"; border.width: parent.activeFocus ? 1 : 0; border.color: AppStyle.accent }
            contentItem: TextLabel { text: parent.realDay ? parent.day : ""; horizontalAlignment: Text.AlignHCenter; color: parent.endpoint ? AppStyle.accentInk : AppStyle.ink; font.pixelSize: 12 }
        } }
    }
}
