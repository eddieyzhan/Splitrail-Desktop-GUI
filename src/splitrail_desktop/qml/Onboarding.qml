import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
Item {
    id: setup
    objectName: "onboardingView"
    property var s: bridge.state
    property bool compactLayout: height < 760
    property string stage: s.setupStage
    property bool existing: s.repoExisting
    Connections { target: bridge; function onSetupEvent(kind, value, generation) { if(kind==="created" && s.repoExisting && s.repoName===value) { setup.existing=true; repo.text=value } } }
    property bool automatic: false
    property int step: stage==="welcome" ? 0 : stage==="account" || stage==="auth" ? 1 : stage==="repository" ? 2 : 3
    Rectangle {
        width: 640; height: 640; radius: 320; anchors.centerIn: parent
        color: AppStyle.accentFill; opacity: AppStyle.dark ? .1 : .35
    }
    Flickable {
        anchors.fill: parent; clip: true; contentWidth: width; contentHeight: Math.max(height, setupColumn.implicitHeight+32); boundsBehavior: Flickable.StopAtBounds; ScrollBar.vertical: SoftScrollBar {}
    ColumnLayout {
        id: setupColumn; x: (parent.width-width)/2; y: Math.max(16, (parent.height-height)/2)
        width: Math.min(parent.width-80, 520)
        spacing: 0
        RowLayout {
            Layout.alignment: Qt.AlignHCenter; spacing: 10; Layout.bottomMargin: setup.compactLayout ? 16 : 24
            Rectangle { width: 30; height: 30; radius: 10; color: AppStyle.accent; Icon { anchors.centerIn: parent; name: "logo"; color: AppStyle.accentInk; width: 21; height: 21 } }
            TextLabel { text: "Splitrail"; font.pixelSize: 17; font.weight: Font.DemiBold; font.letterSpacing: -.4 }
        }
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: body.implicitHeight+(setup.compactLayout ? 48 : 72)
            radius: 28; color: AppStyle.surface
            ColumnLayout {
                id: body; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: setup.compactLayout ? 24 : 36
                spacing: 0
                RowLayout {
                    Layout.fillWidth: true; Layout.bottomMargin: setup.compactLayout ? 14 : 26
                    SoftButton { objectName: "setupBack"; iconName: "left"; tone: "ghost"; compact: true; visible: step>0 && step<3; enabled: !s.setupBusy || stage==="auth"; accessibleName: "Back"; onClicked: bridge.setupBack() }
                    Item { Layout.fillWidth: true }
                    Row { spacing: 6; Repeater { model: 3; Rectangle { required property int index; width: step===index ? 20 : 6; height: 6; radius: 3; color: step>=index ? AppStyle.accent : AppStyle.line; Behavior on width { NumberAnimation { duration: 180 } } } } }
                }
                Rectangle {
                    Layout.alignment: Qt.AlignHCenter; width: setup.compactLayout ? 56 : 72; height: width; radius: setup.compactLayout ? 18 : 23; color: AppStyle.accentFill; Layout.bottomMargin: setup.compactLayout ? 16 : 24
                    Icon { anchors.centerIn: parent; width: 34; height: 34; color: AppStyle.accent; name: step===0 ? "chart" : step===1 ? "github" : step===2 ? "lock" : "check" }
                }
                TextLabel {
                    Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter
                    font.pixelSize: setup.compactLayout ? 26 : 30; font.weight: Font.DemiBold; font.letterSpacing: -1
                    text: stage==="welcome" ? "Your usage, together." : stage==="account" ? (s.ghAvailable ? "Connect your devices." : "One small step.") : stage==="auth" ? "Over to GitHub." : stage==="repository" ? "Make it private." : "You’re all set."
                    Layout.bottomMargin: 12
                }
                TextLabel {
                    Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter
                    color: AppStyle.muted; font.pixelSize: 14; wrapMode: Text.WordWrap; elide: Text.ElideNone; lineHeight: 1.35
                    text: stage==="welcome" ? "See your AI tokens and costs in one place." : stage==="account" ? (s.ghAvailable ? (s.setupLogin ? "Continue with your GitHub account." : "Use GitHub to keep your usage in sync.") : "Install GitHub CLI to connect securely.") : stage==="auth" ? "Enter this one-time code to finish signing in." : stage==="repository" ? "Your usage lives in a repository only you control." : "Your private sync is ready. Let’s take a look."
                    Layout.bottomMargin: setup.compactLayout ? 20 : 28
                }
                Rectangle {
                    visible: stage==="account" && s.setupLogin!==""; Layout.fillWidth: true; height: 60; radius: 13; color: AppStyle.fill; Layout.bottomMargin: 20
                    RowLayout { anchors.fill: parent; anchors.margins: 15; spacing: 12; Icon { name: "github"; color: AppStyle.muted } TextLabel { text: s.setupLogin; font.weight: Font.Medium; Layout.fillWidth: true } Icon { name: "check"; color: AppStyle.green } }
                }
                ColumnLayout {
                    visible: stage==="auth"; Layout.fillWidth: true; spacing: 14; Layout.bottomMargin: 12
                    Rectangle {
                        Layout.fillWidth: true; height: 70; radius: 14; color: AppStyle.fill
                        RowLayout { anchors.centerIn: parent; spacing: 18
                            TextLabel { text: s.setupCode || "Waiting for GitHub…"; font.pixelSize: s.setupCode ? 25 : 15; font.letterSpacing: s.setupCode ? 4 : 0; font.weight: Font.Medium }
                            SoftButton { iconName: "copy"; compact: true; tone: "ghost"; visible: s.setupCode!==""; accessibleName: "Copy code"; onClicked: bridge.copy(s.setupCode) }
                        }
                    }
                    SoftButton { text: "Open GitHub"; iconName: "external"; tone: "primary"; large: true; Layout.fillWidth: true; enabled: s.setupCode!==""; onClicked: bridge.openLink("https://github.com/login/device") }
                }
                ColumnLayout {
                    visible: stage==="repository"; Layout.fillWidth: true; spacing: 16; Layout.bottomMargin: 20
                    Segmented { Layout.fillWidth: true; options: ["Create new", "Use existing"]; selected: setup.existing ? 1 : 0; onChosen: function(index) { setup.existing=index===1; repo.text=index===1 ? s.setupLogin+"/splitrail-usage" : "splitrail-usage" } }
                    SoftField { id: repo; objectName: "repositoryField"; Layout.fillWidth: true; text: s.repoName; placeholderText: setup.existing ? "owner/repository" : "Repository name"; enabled: !s.setupBusy; Accessible.name: "Private repository"; onAccepted: if(text && !s.setupBusy) bridge.connectRepository(text,setup.existing,setup.automatic) }
                    RowLayout { Layout.fillWidth: true; TextLabel { text: "Keep devices up to date automatically"; font.pixelSize: 13; Layout.fillWidth: true } SoftSwitch { objectName: "automaticSync"; checked: setup.automatic; enabled: !s.setupBusy; onToggled: setup.automatic=checked; Accessible.name: "Sync automatically" } }
                }
                TextLabel { visible: s.setupError!==""; text: s.setupError; Layout.fillWidth: true; color: AppStyle.red; font.pixelSize: 12; wrapMode: Text.WordWrap; elide: Text.ElideNone; horizontalAlignment: Text.AlignHCenter; Layout.bottomMargin: 16 }
                SoftButton {
                    objectName: "setupPrimary"; visible: stage!=="auth"; Layout.fillWidth: true; tone: "primary"; large: true; enabled: !s.setupBusy
                    text: s.setupBusy ? (stage==="repository" ? "Connecting…" : "Checking your account…") : stage==="welcome" ? "Get started" : stage==="account" ? (!s.ghAvailable ? "Get GitHub CLI" : s.setupLogin ? "Continue" : "Sign in with GitHub") : stage==="repository" ? "Enable private sync" : "Open Splitrail"
                    onClicked: stage==="repository" ? bridge.connectRepository(repo.text,setup.existing,setup.automatic) : bridge.setupContinue()
                }
                TextLabel { visible: stage==="repository"; text: "Only usage totals and costs. Never your conversations."; Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; color: AppStyle.muted; font.pixelSize: 11; Layout.topMargin: 14; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                SoftButton { visible: stage==="account" && !s.ghAvailable; text: "I’ve installed it"; tone: "ghost"; Layout.alignment: Qt.AlignHCenter; Layout.topMargin: 8; enabled: !s.setupBusy; onClicked: bridge.checkGitHub() }
                SoftButton { visible: stage==="account" && s.setupLogin!==""; text: "Use another account"; tone: "ghost"; Layout.alignment: Qt.AlignHCenter; Layout.topMargin: 8; enabled: !s.setupBusy; onClicked: bridge.signIn() }
            }
        }
        SoftButton { objectName: "setupSkip"; visible: step<3; text: step===0 ? "Use on this device" : "Set up later"; tone: "ghost"; Layout.alignment: Qt.AlignHCenter; Layout.topMargin: setup.compactLayout ? 12 : 18; enabled: !s.setupBusy || stage==="auth"; onClicked: bridge.finishSetup() }
    }
    }
}
