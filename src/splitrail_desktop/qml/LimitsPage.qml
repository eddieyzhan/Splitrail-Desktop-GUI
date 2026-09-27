import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Flickable {
    id: page
    objectName: "limitsPage"
    property var g: bridge.guardState
    property var q: bridge.clock.quota
    clip: true; contentWidth: width; contentHeight: Math.max(height, content.implicitHeight)
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: SoftScrollBar {}
    Component.onCompleted: bridge.setProcessViewVisible(true)
    Component.onDestruction: bridge.setProcessViewVisible(false)

    function folderName(directory) {
        const parts = directory.replace(/\\/g, "/").split("/").filter(part => part.length > 0)
        return parts.length ? parts[parts.length - 1] : directory
    }

    ColumnLayout {
        id: content
        width: parent.width - 4; spacing: 20
        Rectangle {
            Layout.fillWidth: true; implicitHeight: controls.implicitHeight + 40
            radius: 20; color: AppStyle.surface
            ColumnLayout {
                id: controls
                anchors.fill: parent; anchors.margins: 20; spacing: 18
                RowLayout {
                    Layout.fillWidth: true; spacing: 8
                    TextLabel {text: "Weekly cutoff"; font.pixelSize: 16; font.weight: Font.DemiBold}
                    InfoButton {
                        objectName: "cutoffInfo"; label: "How the cutoff works"
                        explanation: "Stops selected local Codex processes at or above the limit. A reading of 96% or 97% still triggers a 95% cutoff.\n\nQuota is checked each minute, so usage can overshoot. If fresh data is unavailable for two minutes, protected processes stop. Keep Splitrail open."
                    }
                    Item {Layout.fillWidth: true}
                    TextLabel {text: q.available ? q.used + "% used" + (q.stale ? " · cached" : "") : "Quota unavailable"; font.pixelSize: 12; color: AppStyle.muted}
                    Rectangle {
                        implicitWidth: stateLabel.implicitWidth + 20; implicitHeight: 28
                        radius: 9; color: AppStyle.fill
                        TextLabel {id: stateLabel; anchors.centerIn: parent; text: g.blocked ? "Blocked" : g.armed ? "On" : "Off"; color: g.armed ? AppStyle.accent : AppStyle.muted; font.pixelSize: 12}
                    }
                }
                RowLayout {
                    Layout.fillWidth: true; spacing: 24
                    ColumnLayout {
                        Layout.fillWidth: true; Layout.preferredWidth: 1; spacing: 6
                        RowLayout {
                            TextLabel {text: "Stop at"; font.pixelSize: 12; color: AppStyle.muted}
                            InfoButton {objectName: "currentLimitInfo"; label: "Weekly limit"; explanation: "The total weekly percentage used, not an additional allowance. At 75% used, a 95% limit leaves 20 percentage points."}
                        }
                        SoftField {
                            id: currentLimit; objectName: "quotaCurrentLimit"
                            Layout.fillWidth: true; text: String(g.currentLimit); enabled: !g.armed
                            rightPadding: 36; Accessible.name: "Current weekly cutoff percentage"
                            inputMethodHints: Qt.ImhFormattedNumbersOnly
                            validator: DoubleValidator {bottom: 0; top: 100; decimals: 2; locale: "C"}
                            TextLabel {anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; text: "%"; color: AppStyle.muted}
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true; Layout.preferredWidth: 1; spacing: 6
                        RowLayout {
                            TextLabel {text: "After reset"; font.pixelSize: 12; color: AppStyle.muted}
                            InfoButton {objectName: "resetLimitInfo"; label: "Limit after a reset"; explanation: "After a detected scheduled or unexpected reset, this becomes the weekly cutoff. 5% means up to 5% of the fresh allowance, including usage already spent. It stays active until you disable the cutoff."}
                        }
                        SoftField {
                            id: resetLimit; objectName: "quotaResetLimit"
                            Layout.fillWidth: true; text: String(g.resetLimit); enabled: !g.armed
                            rightPadding: 36; Accessible.name: "Weekly cutoff percentage after a reset"
                            inputMethodHints: Qt.ImhFormattedNumbersOnly
                            validator: DoubleValidator {bottom: 0; top: 100; decimals: 2; locale: "C"}
                            TextLabel {anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; text: "%"; color: AppStyle.muted}
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true; spacing: 8
                    SoftSwitch {id: includeNew; objectName: "guardIncludeNew"; checked: g.includeNew; enabled: !g.armed; Accessible.name: "Include new Codex processes"}
                    TextLabel {text: "Include new processes"; font.pixelSize: 12}
                    InfoButton {objectName: "newProcessesInfo"; label: "New process protection"; explanation: "Also protect Codex engines opened after enabling the cutoff. Once triggered, they will be stopped too. With this off, only the selected existing processes are covered."}
                    Item {Layout.fillWidth: true}
                    SoftButton {
                        objectName: "armQuotaGuard"
                        text: g.armed ? "Disable cutoff" : "Enable cutoff"
                        tone: g.armed ? "secondary" : "primary"
                        enabled: !bridge.state.demo && (g.armed || (!g.busy && !g.stopping))
                        onClicked: g.armed ? bridge.disarmQuotaGuard() : bridge.armQuotaGuard(currentLimit.text, resetLimit.text, includeNew.checked)
                    }
                }
                RowLayout {
                    Layout.fillWidth: true; spacing: 8
                    TextLabel {
                        objectName: "quotaGuardStatus"; Layout.fillWidth: true
                        text: g.stopping ? "Stopping…" : g.blocked ? "Cutoff reached · " + g.stoppedCount + " stopped" : g.afterReset && g.armed ? "Reset limit active · " + g.activeLimit + "%" : "Stops selected processes"
                        font.pixelSize: 11; color: AppStyle.muted
                    }
                    InfoButton {
                        objectName: "guardStatusInfo"; label: g.blocked ? "Why the cutoff triggered" : "Cutoff scope"
                        explanation: g.blocked ? g.status + "\n\nDisable the cutoff before restarting processes." : "Only the quota tool’s account is measured; processes cannot be matched to accounts automatically. Cloud jobs, other computers and independent tool commands are outside this cutoff. Closing Splitrail turns protection off."
                    }
                }
                TextLabel {objectName: "quotaGuardError"; visible: text !== ""; text: g.error; Layout.fillWidth: true; font.pixelSize: 12; color: AppStyle.accent; wrapMode: Text.WordWrap; elide: Text.ElideNone}
                TextLabel {visible: text !== ""; text: g.stopErrors; Layout.fillWidth: true; font.pixelSize: 12; color: AppStyle.accent; wrapMode: Text.WordWrap; elide: Text.ElideNone}
            }
        }
        Rectangle {
            Layout.fillWidth: true; implicitHeight: processContent.implicitHeight + 40
            radius: 20; color: AppStyle.surface
            ColumnLayout {
                id: processContent
                anchors.fill: parent; anchors.margins: 20; spacing: 12
                RowLayout {
                    Layout.fillWidth: true; spacing: 8
                    TextLabel {text: "Processes · " + g.processes.length; font.pixelSize: 15; font.weight: Font.DemiBold}
                    InfoButton {objectName: "processListInfo"; label: "About these processes"; explanation: "Choose the local Codex engines to protect before enabling the cutoff. An app server can host several chats; stopping it can interrupt all of them. OS status does not tell you which chat is generating.\n\nRefreshes every ten seconds while this page is open or the cutoff is on."}
                    Item {Layout.fillWidth: true}
                    SoftButton {iconName: "refresh"; accessibleName: "Refresh processes"; compact: true; tone: "ghost"; enabled: !g.busy; onClicked: bridge.refreshProcesses()}
                }
                Repeater {
                    model: g.processes
                    delegate: ColumnLayout {
                        required property var modelData
                        required property int index
                        Layout.fillWidth: true; spacing: 10
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
                                Layout.fillWidth: true; spacing: 5
                                TextLabel {text: page.folderName(modelData.directory); font.pixelSize: 13; font.weight: Font.Medium; Layout.fillWidth: true}
                                TextLabel {text: modelData.directory; font.pixelSize: 11; color: AppStyle.muted; Layout.fillWidth: true; elide: Text.ElideMiddle}
                            }
                            TextLabel {text: modelData.kind.split(" · ")[0]; font.pixelSize: 11; color: AppStyle.muted}
                            TextLabel {text: "PID " + modelData.pid; font.pixelSize: 11; color: AppStyle.muted}
                            InfoButton {
                                objectName: "processInfo-" + index; label: "Details for process " + modelData.pid
                                explanation: modelData.kind + " · PID " + modelData.pid + "\nStarted " + modelData.started + "\nOS status: " + modelData.status + "\n\nFolder: " + modelData.directory + "\nExecutable: " + modelData.executable
                            }
                        }
                    }
                }
                TextLabel {visible: g.processes.length === 0; text: g.busy ? "Checking…" : "No Codex processes"; Layout.fillWidth: true; font.pixelSize: 12; color: AppStyle.muted}
            }
        }
    }
}
