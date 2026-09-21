import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
Popup {
    id: popup
    objectName: "datePicker"
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(660,parent.width-48); padding: 24
    modal: true; focus: true
    property date month: new Date()
    property string first: ""
    property string last: ""
    property bool choosingEnd: false
    background: Rectangle { radius: 24; color: AppStyle.surface }
    Overlay.modal: Rectangle { color: AppStyle.dark ? "#80000000" : "#33262D44" }
    onOpened: { first=bridge.state.start; last=bridge.state.end; month=new Date(first+"T12:00:00"); choosingEnd=false }
    function pick(value) { if(!choosingEnd){first=value;last="";choosingEnd=true}else{if(value<first){last=first;first=value}else{last=value}choosingEnd=false} }
    contentItem: ColumnLayout {
        spacing: 20
        RowLayout { Layout.fillWidth: true; TextLabel { text: "Choose a period"; font.pixelSize: 18; font.weight: Font.DemiBold; Layout.fillWidth: true } SoftButton { iconName: "close"; tone: "ghost"; compact: true; onClicked: popup.close() } }
        Segmented { Layout.fillWidth: true; options: ["Today","Week","Month","Year","All time"]; selected: ["Day","Week","Month","Year","All time"].indexOf(bridge.state.preset); onChosen: function(i){bridge.choosePeriod(["Day","Week","Month","Year","All time"][i]);popup.close()} }
        RowLayout {
            Layout.fillWidth: true
            SoftButton { iconName: "left"; tone: "ghost"; compact: true; accessibleName: "Previous month"; onClicked: popup.month=new Date(popup.month.getFullYear(),popup.month.getMonth()-1,1) }
            Item { Layout.fillWidth: true }
            TextLabel { text: popup.choosingEnd ? "Choose an end date" : "Or select a date range"; color: AppStyle.muted; font.pixelSize: 12 }
            Item { Layout.fillWidth: true }
            SoftButton { iconName: "right"; tone: "ghost"; compact: true; accessibleName: "Next month"; onClicked: popup.month=new Date(popup.month.getFullYear(),popup.month.getMonth()+1,1) }
        }
        RowLayout {
            spacing: 24; Layout.fillWidth: true
            CalendarMonth { Layout.fillWidth: true; shown: popup.month; first: popup.first; last: popup.last; onPicked: function(value){popup.pick(value)} }
            CalendarMonth { Layout.fillWidth: true; shown: new Date(popup.month.getFullYear(),popup.month.getMonth()+1,1); first: popup.first; last: popup.last; onPicked: function(value){popup.pick(value)} }
        }
        RowLayout {
            Layout.fillWidth: true
            TextLabel { text: popup.first+(popup.last ? "   →   "+popup.last : ""); font.pixelSize: 12; color: AppStyle.muted; Layout.fillWidth: true }
            SoftButton { objectName: "applyDateRange"; text: "Apply range"; tone: "primary"; enabled: popup.first!==""; onClicked: if(bridge.chooseDates(popup.first,popup.last || popup.first)) popup.close() }
        }
    }
}
