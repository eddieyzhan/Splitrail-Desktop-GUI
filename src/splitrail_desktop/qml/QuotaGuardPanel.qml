import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: panel
    property var g: bridge.guardState
    spacing: 20
    Component.onCompleted: bridge.setProcessViewVisible(true)
    Component.onDestruction: bridge.setProcessViewVisible(false)

    Rectangle {
        Layout.fillWidth: true
        implicitHeight: controls.implicitHeight + 40
        radius: 20; color: AppStyle.surface
        ColumnLayout {
            id: controls
            anchors.fill: parent; anchors.margins: 20; spacing: 14
            RowLayout {
                Layout.fillWidth: true
                TextLabel {text: "Weekly usage cutoff"; font.pixelSize: 16; font.weight: Font.DemiBold; Layout.fillWidth: true}
                TextLabel {text: g.blocked ? "Limit reached" : g.armed ? "Armed" : "Off"; color: g.armed ? AppStyle.accent : AppStyle.muted; font.pixelSize: 12}
            }
            RowLayout {
                Layout.fillWidth: true; spacing: 20
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 7
                    TextLabel {text: "Stop at % used"; font.pixelSize: 12; color: AppStyle.muted}
                    SoftField {
                        id: currentLimit; objectName: "quotaCurrentLimit"
                        Layout.fillWidth: true; text: String(g.currentLimit); enabled: !g.armed
                        Accessible.name: "Current weekly cutoff percentage"
                        inputMethodHints: Qt.ImhFormattedNumbersOnly
                        validator: DoubleValidator {bottom: 0; top: 100; decimals: 2; locale: "C"}
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 7
                    TextLabel {text: "After a reset, stop at % used"; font.pixelSize: 12; color: AppStyle.muted}
                    SoftField {
                        id: resetLimit; objectName: "quotaResetLimit"
                        Layout.fillWidth: true; text: String(g.resetLimit); enabled: !g.armed
                        Accessible.name: "Weekly cutoff percentage after a reset"
                        inputMethodHints: Qt.ImhFormattedNumbersOnly
                        validator: DoubleValidator {bottom: 0; top: 100; decimals: 2; locale: "C"}
                    }
                }
            }
            TextLabel {
                Layout.fillWidth: true; font.pixelSize: 12; color: AppStyle.muted
                text: "Example: at 75% used, a 95% cutoff leaves 20 percentage points. If usage resets, a 5% cutoff allows up to 5% of the fresh allowance. The reset limit stays in force until you disarm."
                wrapMode: Text.WordWrap; elide: Text.ElideNone
            }
            RowLayout {
                Layout.fillWidth: true
                SoftSwitch {id: includeNew; objectName: "guardIncludeNew"; checked: g.includeNew; enabled: !g.armed; Accessible.name: "Also protect newly opened Codex processes"}
                TextLabel {text: "Also protect newly opened Codex processes"; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone}
            }
            TextLabel {
                Layout.fillWidth: true; font.pixelSize: 11; color: AppStyle.muted
                text: "Arming authorizes Splitrail to terminate the selected Codex engines at the cutoff, or if quota stays unavailable for two minutes. An app server may contain several chats. Keep Splitrail open; closing it disables protection."
                wrapMode: Text.WordWrap; elide: Text.ElideNone
            }
            RowLayout {
                Layout.fillWidth: true; spacing: 14
                SoftButton {
                    objectName: "armQuotaGuard"
                    text: g.armed ? "Disarm cutoff" : "Arm cutoff"
                    enabled: !bridge.state.demo && (g.armed || (!g.busy && !g.stopping))
                    onClicked: g.armed ? bridge.disarmQuotaGuard() : bridge.armQuotaGuard(currentLimit.text, resetLimit.text, includeNew.checked)
                }
                TextLabel {
                    objectName: "quotaGuardStatus"; text: g.status; Layout.fillWidth: true
                    font.pixelSize: 12; color: g.armed ? AppStyle.accent : AppStyle.muted
                    wrapMode: Text.WordWrap; elide: Text.ElideNone
                }
            }
            TextLabel {
                visible: g.stoppedCount > 0 || g.stopping; Layout.fillWidth: true
                text: g.stopping ? "Stopping protected processes…" : g.stoppedCount + " processes stopped. Disarm before restarting them."
                font.pixelSize: 12; wrapMode: Text.WordWrap; elide: Text.ElideNone
            }
            TextLabel {objectName: "quotaGuardError"; visible: text !== ""; text: g.error; Layout.fillWidth: true; font.pixelSize: 12; color: AppStyle.accent; wrapMode: Text.WordWrap; elide: Text.ElideNone}
            TextLabel {visible: text !== ""; text: g.stopErrors; Layout.fillWidth: true; font.pixelSize: 12; color: AppStyle.accent; wrapMode: Text.WordWrap; elide: Text.ElideNone}
            TextLabel {
                Layout.fillWidth: true; font.pixelSize: 11; color: AppStyle.faint
                text: "Best effort: quota is checked each minute; reporting delays and in-flight requests can exceed the limit. Leave headroom. Only the quota tool’s account is measured; processes using other accounts cannot be matched automatically. Cloud jobs, other computers and independent tool commands are outside this cutoff."
                wrapMode: Text.WordWrap; elide: Text.ElideNone
            }
        }
    }
    Rectangle {
        Layout.fillWidth: true
        implicitHeight: processContent.implicitHeight + 40
        radius: 20; color: AppStyle.surface
        ColumnLayout {
            id: processContent
            anchors.fill: parent; anchors.margins: 20; spacing: 12
            RowLayout {
                Layout.fillWidth: true
                TextLabel {text: "Open Codex processes · " + g.processes.length; font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true}
                SoftButton {text: "Refresh"; compact: true; tone: "ghost"; enabled: !g.busy; onClicked: bridge.refreshProcesses()}
            }
            TextLabel {
                Layout.fillWidth: true; font.pixelSize: 11; color: AppStyle.muted
                text: "Select the engines to protect before arming. These are local processes owned by your OS user, not individual chats. OS “sleeping” status does not mean a chat is idle."
                wrapMode: Text.WordWrap; elide: Text.ElideNone
            }
            Repeater {
                model: g.processes
                delegate: ColumnLayout {
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true; spacing: 6
                    Rectangle {Layout.fillWidth: true; height: 1; color: AppStyle.line}
                    RowLayout {
                        Layout.fillWidth: true; spacing: 12
                        SoftSwitch {
                            objectName: "protectProcess-" + index
                            checked: modelData.protected; enabled: !g.armed
                            Accessible.name: "Protect Codex process " + modelData.pid
                            onClicked: bridge.protectProcess(modelData.key, checked)
                        }
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 4
                            TextLabel {text: modelData.kind + " · PID " + modelData.pid; font.pixelSize: 13; font.weight: Font.Medium; Layout.fillWidth: true}
                            TextLabel {text: modelData.directory; font.pixelSize: 12; color: AppStyle.muted; Layout.fillWidth: true; wrapMode: Text.WrapAnywhere; elide: Text.ElideNone}
                            TextLabel {text: "Started " + modelData.started + " · " + modelData.status; font.pixelSize: 10; color: AppStyle.faint; Layout.fillWidth: true}
                            TextLabel {text: modelData.executable; font.pixelSize: 10; color: AppStyle.faint; Layout.fillWidth: true; wrapMode: Text.WrapAnywhere; elide: Text.ElideNone}
                        }
                    }
                }
            }
            TextLabel {visible: g.processes.length === 0; text: g.busy ? "Checking local processes…" : "No accessible native Codex engines found."; Layout.fillWidth: true; font.pixelSize: 12; color: AppStyle.muted}
            TextLabel {text: "Checked " + (g.checked || "when this page opens") + " · refreshes every 10 seconds while visible or armed"; Layout.fillWidth: true; font.pixelSize: 10; color: AppStyle.faint; wrapMode: Text.WordWrap; elide: Text.ElideNone}
        }
    }
}
