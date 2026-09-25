import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
Flickable {
    id: page
    property var s: bridge.state
    signal pricingRequested()
    property bool advanced: false
    contentWidth: width; contentHeight: content.implicitHeight+12
    clip: true; boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: SoftScrollBar {}
    ColumnLayout {
        id: content; width: Math.min(page.width-8,850); spacing: 24
        Rectangle {
            Layout.fillWidth: true; implicitHeight: appearance.implicitHeight+48; radius: 20; color: AppStyle.surface
            ColumnLayout {
                id: appearance; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 24; spacing: 20
                TextLabel { text: "Appearance"; font.pixelSize: 16; font.weight: Font.DemiBold }
                RowLayout { Layout.fillWidth: true; spacing: 18
                    Repeater { model: ["Pearl","Nord"]; delegate: Button {
                        id: themeChoice
                        required property string modelData
                        Layout.fillWidth: true; Layout.preferredWidth: 1; implicitHeight: 142
                        onClicked: bridge.setTheme(modelData)
                        background: Rectangle { radius: 14; color: AppStyle.fill; border.width: s.theme===modelData ? 2 : 0; border.color: AppStyle.accent }
                        contentItem: ColumnLayout { spacing: 10
                            Rectangle {
                                Layout.fillWidth: true; Layout.fillHeight: true; Layout.margins: 5; radius: 9; color: modelData==="Pearl" ? "#F7F8FA" : "#242932"; clip: true
                                Rectangle { x: 0; y: 0; height: parent.height; width: parent.width*.22; color: modelData==="Pearl" ? "#E8EBF2" : "#303743" }
                                Rectangle { x: parent.width*.28; y: 14; width: parent.width*.24; height: 6; radius: 3; color: modelData==="Pearl" ? "#CAD0E5" : "#68758E" }
                                Row { x: parent.width*.28; y: 34; width: parent.width*.65; height: parent.height-48; spacing: 7; Repeater { model: 3; Rectangle { width: (parent.width-14)/3; height: parent.height; radius: 5; color: themeChoice.modelData==="Pearl" ? "white" : "#394353" } } }
                            }
                            RowLayout { Layout.fillWidth: true; Layout.leftMargin: 8; Layout.rightMargin: 8; Layout.bottomMargin: 3; TextLabel { text: modelData; font.pixelSize: 13; font.weight: Font.Medium; Layout.fillWidth: true } Icon { name: "check"; visible: s.theme===modelData; color: AppStyle.accent; width: 17; height: 17 } }
                        }
                    } }
                }
            }
        }
        Rectangle {
            Layout.fillWidth: true; implicitHeight: syncContent.implicitHeight+48; radius: 20; color: AppStyle.surface
            ColumnLayout {
                id: syncContent; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 24; spacing: 18
                RowLayout {
                    Layout.fillWidth: true; spacing: 16
                    Rectangle { width: 42; height: 42; radius: 13; color: AppStyle.accentFill; Icon { name: "cloud"; color: AppStyle.accent; anchors.centerIn: parent } }
                    ColumnLayout { spacing: 5; Layout.fillWidth: true; TextLabel { text: "GitHub sync"; font.pixelSize: 16; font.weight: Font.DemiBold } TextLabel { text: s.connected ? s.repository : "Your devices, together. Your data, private."; color: AppStyle.muted; font.pixelSize: 12; Layout.fillWidth: true } }
                    SoftButton { text: s.connected ? (s.syncBusy ? "Syncing…" : "Sync now") : "Connect"; tone: "primary"; enabled: !s.syncBusy && !s.busy; onClicked: s.connected ? bridge.syncNow() : bridge.beginSetup() }
                }
                ColumnLayout {
                    visible: s.connected; Layout.fillWidth: true; spacing: 14
                    RowLayout { Layout.fillWidth: true; TextLabel { text: "Sync automatically"; font.pixelSize: 13; Layout.fillWidth: true } SoftSwitch { checked: s.automatic; enabled: !s.syncBusy; Accessible.name: "Automatic sync"; onToggled: bridge.updateSync(checked,s.receiveOnly,s.syncScope) } }
                    RowLayout { Layout.fillWidth: true; TextLabel { text: s.syncStatus; color: AppStyle.muted; font.pixelSize: 11; Layout.fillWidth: true } SoftButton { text: page.advanced ? "Fewer options" : "Options"; iconName: "down"; tone: "ghost"; compact: true; onClicked: page.advanced=!page.advanced } }
                    ColumnLayout {
                        visible: page.advanced; Layout.fillWidth: true; spacing: 14
                        RowLayout { Layout.fillWidth: true; TextLabel { text: "Download only"; font.pixelSize: 13; Layout.fillWidth: true } SoftSwitch { checked: s.receiveOnly; enabled: !s.syncBusy; onToggled: bridge.updateSync(s.automatic,checked,s.syncScope) } }
                        RowLayout { Layout.fillWidth: true; TextLabel { text: "Share from this device"; font.pixelSize: 13; Layout.fillWidth: true } Segmented { options: ["All tools","Codex"]; selected: s.syncScope==="all" ? 0 : 1; enabled: !s.syncBusy; onChosen: function(i){bridge.updateSync(s.automatic,s.receiveOnly,i===0 ? "all" : "codex")} } }
                        TextLabel { visible: !s.receiveOnly; text: s.syncScope==="all" ? "All tools sync requires the separate Splitrail collector on this device." : "Codex sync reads local usage directly; no collector is needed."; color: AppStyle.muted; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                        SoftButton { text: "Disconnect"; tone: "ghost"; enabled: !s.syncBusy; onClicked: disconnectConfirm.open() }
                    }
                }
                TextLabel { visible: s.settingsError!==""; text: s.settingsError; color: AppStyle.red; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                SoftButton { visible: s.settingsError!=="" && !s.connected; text: "Reset sync settings"; onClicked: bridge.disconnect() }
            }
        }
        Rectangle {
            Layout.fillWidth: true; height: 92; radius: 20; color: AppStyle.surface
            RowLayout { anchors.fill: parent; anchors.margins: 24; spacing: 20
                ColumnLayout { spacing: 6; TextLabel { text: "Model pricing"; font.pixelSize: 16; font.weight: Font.DemiBold } TextLabel { text: "Built-in rates, with room for your own."; font.pixelSize: 12; color: AppStyle.muted } }
                Item { Layout.fillWidth: true }
                SoftButton { text: "Manage prices"; onClicked: page.pricingRequested() }
            }
        }
        Rectangle {
            Layout.fillWidth: true; implicitHeight: dataContent.implicitHeight+48; radius: 20; color: AppStyle.surface
            ColumnLayout {
                id: dataContent; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 24; spacing: 20
                TextLabel { text: "Usage & data"; font.pixelSize: 16; font.weight: Font.DemiBold }
                RowLayout { Layout.fillWidth: true; TextLabel { text: "Dashboard source"; Layout.fillWidth: true; font.pixelSize: 13 } Segmented { options: ["All devices","This device","Codex"]; selected: ["combined","local","codex"].indexOf(s.usageMode); onChosen: function(i){bridge.setUsageMode(["combined","local","codex"][i])} } }
                RowLayout { Layout.fillWidth: true; TextLabel { text: "Codex file transfer"; font.pixelSize: 13; Layout.fillWidth: true } SoftButton { text: "Import"; compact: true; enabled: !s.transferBusy; onClicked: bridge.transfer("import") } SoftButton { text: "Export"; compact: true; enabled: !s.transferBusy; onClicked: bridge.transfer("export") } }
                RowLayout { Layout.fillWidth: true; SoftButton { text: "Get the Splitrail collector"; iconName: "external"; tone: "ghost"; compact: true; onClicked: bridge.openLink("https://github.com/Piebald-AI/splitrail") } Item { Layout.fillWidth: true } SoftButton { text: "Remove imports"; tone: "ghost"; compact: true; enabled: !s.transferBusy; onClicked: importsConfirm.open() } }
            }
        }
        TextLabel { text: "Splitrail " + s.appVersion + "  ·  Local first. No telemetry."; color: AppStyle.faint; font.pixelSize: 11; Layout.alignment: Qt.AlignHCenter; Layout.topMargin: 2 }
    }
    Popup {
        id: disconnectConfirm; parent: Overlay.overlay; anchors.centerIn: parent; width: 410; padding: 28; modal: true
        background: Rectangle {radius: 20;color: AppStyle.surface}
        contentItem: ColumnLayout { spacing: 18; TextLabel {text:"Disconnect GitHub?";font.pixelSize:20;font.weight:Font.DemiBold}TextLabel {text:"Downloaded totals will be removed from this device. Your private repository will stay as it is.";Layout.fillWidth:true;wrapMode:Text.WordWrap;elide:Text.ElideNone;color:AppStyle.muted;font.pixelSize:13}RowLayout {Layout.fillWidth:true;Item{Layout.fillWidth:true}SoftButton{text:"Cancel";onClicked:disconnectConfirm.close()}SoftButton{text:"Disconnect";tone:"primary";onClicked:{bridge.disconnect();disconnectConfirm.close()}}} }
    }
    Popup {
        id: importsConfirm; parent: Overlay.overlay; anchors.centerIn: parent; width: 410; padding: 28; modal: true
        background: Rectangle {radius:20;color:AppStyle.surface}
        contentItem:ColumnLayout{spacing:18;TextLabel {text:"Remove imported usage?";font.pixelSize:20;font.weight:Font.DemiBold}TextLabel {text:"Your original logs and exported files will remain on this device.";Layout.fillWidth:true;wrapMode:Text.WordWrap;elide:Text.ElideNone;color:AppStyle.muted;font.pixelSize:13}RowLayout{Layout.fillWidth:true;Item{Layout.fillWidth:true}SoftButton{text:"Cancel";onClicked:importsConfirm.close()}SoftButton{text:"Remove";tone:"primary";onClicked:{bridge.removeImports();importsConfirm.close()}}}}
    }
}
