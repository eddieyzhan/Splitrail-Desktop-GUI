import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: dashboard
    objectName: "dashboardView"
    property var s: bridge.state
    property var clock: bridge.clock
    property var q: clock.quota
    property string page: "Overview"
    property bool pageReady: false
    Component.onCompleted: { page = s.initialPage; pageReady = true }
    onPageChanged: if (pageReady) bridge.rememberPage(page)
    property string query: ""
    readonly property bool settingsPage: page === "Settings"
    readonly property bool showHeader: width >= 400 && height >= 320
    readonly property bool showDates: width >= 400 && height >= 320
    readonly property bool showActivity: width >= 640 && height >= 420
    readonly property bool showModels: width >= 620 && height >= 420
    readonly property bool showQuotaDetails: width >= 480 && height >= 360
    readonly property bool compact: height < 240 || (width < 380 && height < 280)
    onCompactChanged: if (compact) { dates.close(); pricing.close(); notifications.close() }

    CompactQuota { anchors.fill: parent; visible: dashboard.compact }
    RowLayout {
        objectName: "fullDashboard"
        visible: !dashboard.compact
        anchors.fill: parent
        spacing: 0
        Rectangle {
            objectName: "dashboardSidebar"
            Layout.preferredWidth: 48; Layout.fillHeight: true; color: AppStyle.sidebar
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 6; spacing: 4
                Rectangle {
                    visible: dashboard.height >= 440
                    Layout.alignment: Qt.AlignHCenter; Layout.topMargin: 6; Layout.bottomMargin: 10
                    width: 28; height: 28; radius: 9; color: AppStyle.accent
                    Icon {name: "logo"; color: AppStyle.accentInk; anchors.centerIn: parent; width: 18; height: 18}
                }
                Flickable {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    contentWidth: width; contentHeight: navigation.implicitHeight
                    clip: true; boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: SoftScrollBar {}
                    ColumnLayout {
                        id: navigation; width: parent.width; spacing: 4
                        Repeater {
                            model: [{title:"Overview",iconName:"grid"},{title:"Models",iconName:"models"},{title:"Tools",iconName:"tools"},{title:"History",iconName:"calendar"},{title:"Quota",iconName:"quota"},{title:"Limits",iconName:"limits"}]
                            delegate: Button {
                                id: navigationButton
                                required property var modelData
                                objectName: "navigation" + modelData.title
                                Accessible.name: modelData.title
                                Accessible.description: modelData.title === "Limits" && bridge.guardState.armed ? (bridge.guardState.blocked ? "Cutoff reached" : "Cutoff enabled at " + bridge.guardState.activeLimit + "%") : ""
                                Accessible.role: Accessible.PageTab
                                checkable: true; autoExclusive: true; checked: dashboard.page === modelData.title
                                Layout.fillWidth: true; implicitHeight: 34
                                hoverEnabled: true
                                ToolTip.visible: hovered || activeFocus
                                ToolTip.text: modelData.title + (Accessible.description ? " · " + Accessible.description : "")
                                ToolTip.delay: activeFocus ? 0 : 350
                                onClicked: {dashboard.page = modelData.title; dashboard.query = ""}
                                background: Rectangle {
                                    radius: 9
                                    color: navigationButton.checked ? AppStyle.surface : navigationButton.hovered ? AppStyle.hover : "transparent"
                                    border.width: navigationButton.activeFocus ? 1 : 0; border.color: AppStyle.accent
                                }
                                contentItem: Item {
                                    Icon {name: navigationButton.modelData.iconName; color: navigationButton.checked ? AppStyle.accent : AppStyle.muted; anchors.centerIn: parent; width: 18; height: 18}
                                    Rectangle {visible: navigationButton.modelData.title === "Limits" && bridge.guardState.armed; width: 5; height: 5; radius: 3; color: bridge.guardState.blocked ? AppStyle.red : AppStyle.green; anchors.right: parent.right; anchors.top: parent.top}
                                }
                            }
                        }
                    }
                }
                SoftButton {
                    visible: dashboard.height >= 360
                    objectName: "syncNavigation"; Layout.fillWidth: true; iconName: s.connected ? "cloud" : "device"; tone: "ghost"
                    accessibleName: s.connected ? (s.syncBusy ? "Syncing" : "Private sync connected") : "On this device"
                    toolTip: accessibleName + " · Open sync settings"
                    onClicked: dashboard.page = "Settings"
                }
                SoftButton {
                    objectName: "settingsNavigation"; Layout.fillWidth: true; iconName: "settings"
                    accessibleName: "Settings"; tone: dashboard.settingsPage ? "secondary" : "ghost"
                    onClicked: dashboard.page = "Settings"
                }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.margins: 12; spacing: 8
            RowLayout {
                objectName: "dashboardHeader"
                visible: dashboard.showHeader
                Layout.fillWidth: true; spacing: 6
                TextLabel {
                    visible: dashboard.page !== "Overview"
                    text: dashboard.page; font.pixelSize: 16; font.weight: Font.DemiBold
                }
                TextLabel {
                    objectName: "dashboardStatus"
                    Layout.fillWidth: true
                    text: s.busy ? "Updating usage…" : s.notifications.length ? s.notifications[0].title : clock.refreshAge
                    font.pixelSize: 11; color: s.notifications.length ? AppStyle.accent : AppStyle.muted
                    HoverHandler {id: statusHover}
                    ToolTip.visible: statusHover.hovered
                    ToolTip.text: text
                }
                SoftButton {
                    objectName: "guardStatusButton"
                    visible: bridge.guardState.armed && dashboard.page !== "Limits"; compact: true; tone: "ghost"
                    text: bridge.guardState.blocked ? "Cutoff reached" : "Cutoff " + bridge.guardState.activeLimit + "%"
                    onClicked: dashboard.page = "Limits"
                }
                SoftButton {
                    objectName: "refreshButton"; iconName: "refresh"; tone: "ghost"; compact: true
                    visible: !dashboard.settingsPage
                    enabled: !s.quotaBusy && (dashboard.page === "Limits" || dashboard.page === "Quota" || (!s.busy && !s.syncBusy))
                    accessibleName: dashboard.page === "Limits" || dashboard.page === "Quota" ? "Refresh quota" : "Refresh usage"
                    onClicked: dashboard.page === "Limits" || dashboard.page === "Quota" ? bridge.refreshQuota() : bridge.refresh()
                }
                SoftButton {
                    objectName: "notificationsButton"; iconName: "bell"; tone: "ghost"; compact: true
                    accessibleName: "Notifications" + (s.notifications.length ? ", " + s.notifications.length + " unread" : "")
                    onClicked: notifications.open()
                    Rectangle {visible: s.notifications.length > 0; width: 5; height: 5; radius: 3; color: AppStyle.accent; anchors.right: parent.right; anchors.rightMargin: 3; anchors.top: parent.top; anchors.topMargin: 3}
                }
            }
            GridLayout {
                objectName: "dashboardDates"
                visible: dashboard.showDates && !dashboard.settingsPage && dashboard.page !== "Quota" && dashboard.page !== "Limits"
                Layout.fillWidth: true; columns: dashboard.width >= 640 ? 2 : 1; columnSpacing: 8; rowSpacing: 4
                TextLabel {
                    text: s.range + (s.hasUsage && s.displayedMode !== s.usageMode ? " · Previous " + ({combined:"All devices",local:"This device",codex:"Codex"})[s.displayedMode] + " data" : "")
                    font.pixelSize: 11; color: AppStyle.muted; Layout.fillWidth: true
                    HoverHandler {id: rangeHover}
                    ToolTip.visible: rangeHover.hovered
                    ToolTip.text: text
                    Accessible.name: "Usage dates: " + text
                }
                RowLayout {
                    Layout.alignment: Qt.AlignRight; spacing: 2
                    SoftButton {objectName:"previousPeriod"; visible:dashboard.width>=600; iconName:"left"; tone:"ghost"; compact:true; enabled:s.canPrevious; accessibleName:"Previous time frame"; onClicked:bridge.cyclePeriod(-1)}
                    Segmented {objectName:"periodTabs"; compact:true; options:["Today","Week","Month","Year","All time"]; selected:["Day","Week","Month","Year","All time"].indexOf(s.preset); onChosen:function(i){bridge.choosePeriod(["Day","Week","Month","Year","All time"][i])}}
                    SoftButton {objectName:"nextPeriod"; visible:dashboard.width>=600; iconName:"right"; tone:"ghost"; compact:true; enabled:s.canNext; accessibleName:"Next time frame"; onClicked:bridge.cyclePeriod(1)}
                    SoftButton {objectName:"dateSelector"; accessibleName:"Choose dates"; toolTip:"Choose dates · " + s.range; iconName:"calendar"; compact:true; onClicked:dates.open()}
                }
            }
            Loader {
                id: content; objectName: "dashboardPageLoader"
                Layout.fillWidth: true; Layout.fillHeight: true
                // Hidden pages (including the Limits process view) must not stay alive.
                active: !dashboard.compact
                sourceComponent: dashboard.page === "Overview" ? overview : dashboard.settingsPage ? settings : dashboard.page === "Quota" ? quotaPage : dashboard.page === "Limits" ? limitsPage : tablePage
            }
        }
    }
    Component {
        id: overview
        Flickable {
            objectName: "overviewPage"
            clip: true; contentWidth: width; contentHeight: Math.max(height, overviewContent.implicitHeight)
            boundsBehavior: Flickable.StopAtBounds; ScrollBar.vertical: SoftScrollBar {}
            GridLayout {
                id: overviewContent; width: parent.width - 4; columns: 1; rowSpacing: 8
                RowLayout {
                    objectName: "overviewSummary"
                    Layout.row: dashboard.showQuotaDetails ? 0 : 1
                    Layout.fillWidth: true; spacing: 8
                    MetricCard {
                        objectName: "overviewCostCard"; Layout.fillWidth: true; Layout.preferredWidth: 1
                        label: "Cost"; value: s.hasUsage ? s.summary.cost : "—"
                        explanation: "Estimated cost: " + value + " USD at API rates. This is an API equivalent estimate, not your subscription charge or invoice."
                    }
                    MetricCard {
                        objectName: "overviewTokensCard"; Layout.fillWidth: true; Layout.preferredWidth: 1
                        label: "Tokens"; value: s.hasUsage ? s.summary.tokens : "—"
                        explanation: "Tokens: " + (s.hasUsage ? s.summary.exactTokens || value : "Unavailable") + " in this period."
                    }
                    MetricCard {
                        objectName: "overviewConversationsCard"; visible: dashboard.width >= 480
                        Layout.fillWidth: true; Layout.preferredWidth: 1
                        label: "Conversations"; value: s.hasUsage ? s.summary.conversations : "—"
                        explanation: "Conversations: " + value + (s.hasUsage ? "; " + (s.summary.messages || "0") + " messages in this period." : ". No usage data yet.")
                    }
                }
                RowLayout {
                    Layout.row: dashboard.showQuotaDetails ? 1 : 0
                    Layout.fillWidth: true; spacing: 8
                    Rectangle {
                        objectName: "overviewQuotaCard"
                        Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 370
                        Layout.preferredHeight: quotaOverviewContent.implicitHeight + 20
                        radius: 12; color: AppStyle.surface
                        ColumnLayout {
                            id: quotaOverviewContent; anchors.fill: parent; anchors.margins: 10; spacing: 6
                            RowLayout {
                                Layout.fillWidth: true; spacing: 4
                                HintLabel {objectName:"overviewQuotaUsage"; explanation:"Codex weekly quota used. " + (q.available ? Math.round(q.used) + "% used; " + q.remaining + " remaining." : "Quota is unavailable."); text:q.available ? Math.round(q.used)+"%" : "Unavailable"; font.pixelSize:18; font.weight:Font.DemiBold; Accessible.name:q.available ? Math.round(q.used)+"% of weekly quota used" : "Weekly quota unavailable"}
                                TextLabel {visible:!!q.stale; text:"Cached"; font.pixelSize:10; color:AppStyle.amber}
                                Item {Layout.fillWidth:true}
                                HintLabel {objectName:"overviewBankedResets"; explanation:(q.banks>=0 ? q.banks+" banked resets available" : "Banked resets unavailable") + (q.banksStale ? " · cached" : "") + "\n" + (q.bankExpiryDetails || "Expiry details unavailable"); visible:dashboard.showQuotaDetails; text:q.banks>=0 ? String(q.banks) : "—"; font.pixelSize:14; Accessible.name:q.banks>=0 ? q.banks+" banked resets available" : "Banked resets unavailable"}
                                SoftButton {objectName:"quotaDetailsButton"; iconName:"right"; compact:true; tone:"ghost"; accessibleName:"Quota details"; onClicked:dashboard.page="Quota"}
                            }
                            WeeklyQuotaBar {
                                objectName: "overviewQuotaBar"
                                Layout.fillWidth: true; barHeight: 7; used: q.used || 0
                                pace: q.paceUsed === null || q.paceUsed === undefined ? -1 : q.paceUsed
                            }
                            RowLayout {
                                visible: dashboard.showQuotaDetails
                                Layout.fillWidth: true; spacing: 8
                                ColumnLayout {
                                    objectName: "overviewWeeklyColumn"
                                    Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop; spacing: 3
                                    RowLayout {
                                        Layout.fillWidth: true; spacing: 4
                                        HintLabel {objectName:"overviewResetTime"; text:q.resetParts ? q.resetParts.date+(q.resetParts.time ? ", "+q.resetParts.time : "") : "Unavailable"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap; elide:Text.ElideNone; Accessible.name:"Next weekly reset: " + text; explanation:Accessible.name}
                                    }
                                }
                                ColumnLayout {
                                    objectName: "overviewBankedColumn"
                                    Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop; spacing: 3
                                    TextLabel {visible:!!q.banksStale; text:"Cached"; font.pixelSize:10; color:AppStyle.amber; Layout.alignment:Qt.AlignRight}
                                    ColumnLayout {
                                        objectName: "overviewBankedExpiry"; Layout.fillWidth: true; spacing: 3
                                        Repeater {
                                            model: q.bankExpiryRows || []
                                            delegate: HintLabel {
                                                required property var modelData
                                                required property int index
                                                objectName: "overviewBankExpiry-" + index
                                                text: modelData.date + (modelData.time ? ", " + modelData.time : "")
                                                font.pixelSize: 11; Layout.fillWidth: true; horizontalAlignment: Text.AlignRight; wrapMode: Text.WordWrap; elide: Text.ElideNone
                                                Accessible.name: modelData.time ? "Banked reset expires " + text : "Banked reset: " + text
                                                explanation: Accessible.name
                                            }
                                        }
                                        TextLabel {visible:!!q.bankExpiryNotice; text:q.bankExpiryRows && q.bankExpiryRows.length>0 ? "Some expiries unavailable" : "Expiry unavailable"; font.pixelSize:10; color:AppStyle.muted; Layout.fillWidth:true; horizontalAlignment:Text.AlignRight; wrapMode:Text.WordWrap; elide:Text.ElideNone}
                                    }
                                }
                            }
                            TextLabel {
                                objectName: "overviewQuotaAge"
                                Layout.fillWidth: true
                                text: (q.stale ? "Cached · " : "") + (q.available ? q.age || "Not yet updated" : "No weekly quota data")
                                font.pixelSize: 10; color: AppStyle.muted
                            }
                        }
                    }
                    Loader {
                        objectName: "overviewModelsLoader"
                        visible: dashboard.showModels; active: visible
                        Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 240
                        Layout.preferredHeight: item ? item.implicitHeight : 0
                        sourceComponent: Rectangle {
                            objectName: "overviewModelsCard"; implicitHeight: modelsOverviewContent.implicitHeight + 20
                            radius: 12; color: AppStyle.surface
                            ColumnLayout {
                                id: modelsOverviewContent; anchors.fill: parent; anchors.margins: 10; spacing: 6
                                RowLayout {
                                    Layout.fillWidth: true
                                    Item {Layout.fillWidth:true}
                                    SoftButton {iconName:"right"; compact:true; tone:"ghost"; accessibleName:"View all models"; onClicked:dashboard.page="Models"}
                                }
                                Repeater {
                                    model: s.models.slice(0,3)
                                    delegate: RowLayout {
                                        required property var modelData; required property int index
                                        Layout.fillWidth: true; spacing: 6
                                        Rectangle {width:5; height:5; radius:3; color:[AppStyle.accent,AppStyle.teal,AppStyle.purple][index]}
                                        TextLabel {text:modelData.name; Layout.fillWidth:true; font.pixelSize:11; HoverHandler{id:modelHover} ToolTip.visible:modelHover.hovered && truncated; ToolTip.text:text}
                                        TextLabel {text:modelData.cost; font.pixelSize:11; font.weight:Font.Medium}
                                    }
                                }
                                TextLabel {visible:s.models.length===0; text:"No models in this period"; font.pixelSize:11; color:AppStyle.muted; Layout.fillWidth:true; wrapMode:Text.WordWrap; elide:Text.ElideNone}
                                Item {Layout.fillHeight:true}
                            }
                        }
                    }
                }
                Loader {
                    objectName: "activityChartLoader"
                    visible: dashboard.showActivity; active: visible
                    Layout.row: 2; Layout.fillWidth: true
                    Layout.preferredHeight: Math.max(128, Math.min(240, dashboard.height - 310))
                    sourceComponent: UsageChart {objectName:"activityChart"; points:s.chart; detail:s.chartDetail; emptyMessage:s.chartEmpty}
                }
            }
        }
    }
    Component {
        id:tablePage
        ColumnLayout {
            objectName: "tablePage"
            spacing:8
            GridLayout {
                Layout.fillWidth:true; columns:dashboard.width<600 ? 1 : 3; columnSpacing:8; rowSpacing:4
                TextLabel {text:dashboard.page==="Models" ? s.models.length+" models" : dashboard.page==="Tools" ? s.tools.length+" tools" : s.days.length+" days"; font.pixelSize:12; color:AppStyle.muted; Layout.fillWidth:true}
                SoftField {Layout.preferredWidth:Math.min(200,dashboard.width/3); Layout.fillWidth:dashboard.width<600; implicitHeight:30; placeholderText:"Search "+dashboard.page.toLowerCase(); text:dashboard.query; onTextEdited:dashboard.query=text; Accessible.name:placeholderText}
                SoftButton {visible:dashboard.page==="Models"; text:"Edit prices"; compact:true; onClicked:pricing.open()}
            }
            Flow {visible:dashboard.page==="Models";Layout.fillWidth:true;spacing:12
                Repeater {model:[{label:"Input",value:s.summary.input},{label:"Output",value:s.summary.output},{label:"Cached",value:s.summary.cached},{label:"Reasoning",value:s.summary.reasoning},{label:"Cache read",value:s.summary.read},{label:"Cache write",value:s.summary.write}];delegate:TextLabel{required property var modelData;text:modelData.label+"  "+(modelData.value || "0");color:AppStyle.muted;font.pixelSize:11}}
            }
            DataTable {
                Layout.fillWidth:true;Layout.fillHeight:true
                rows:(dashboard.page==="Models" ? s.models : dashboard.page==="Tools" ? s.tools : s.days).filter(r=>r.name.toLowerCase().includes(dashboard.query.toLowerCase()))
                columns: dashboard.page==="Models" ? [{key:"name",label:"Model",weight:3.5},{key:"tokens",label:"Tokens",weight:2},{key:"messages",label:"Messages",weight:1.4},{key:"cost",label:"Est. cost",weight:1.4},{key:"coverage",label:"Coverage",weight:1.8}] : dashboard.page==="Tools" ? [{key:"name",label:"Tool",weight:3},{key:"tokens",label:"Tokens",weight:2},{key:"messages",label:"Messages",weight:1.4},{key:"calls",label:"Tool calls",weight:1.4},{key:"cost",label:"Est. cost",weight:1.5}] : [{key:"name",label:"Date",weight:2},{key:"tokens",label:"Tokens",weight:2},{key:"messages",label:"Messages",weight:1.3},{key:"cost",label:"Est. cost",weight:1.3},{key:"tools",label:"Tools",weight:3}]
            }
        }
    }
    Component {
        id:quotaPage
        Flickable {
            clip:true;contentWidth:width;contentHeight:Math.max(height,quotaContent.implicitHeight);boundsBehavior:Flickable.StopAtBounds;ScrollBar.vertical:SoftScrollBar{}
            ColumnLayout {
                id:quotaContent;width:parent.width-4;spacing:12
                Rectangle {Layout.fillWidth:true;Layout.preferredHeight:quotaMain.implicitHeight+28;radius:12;color:AppStyle.surface
                    ColumnLayout{id:quotaMain;anchors.fill:parent;anchors.margins:14;spacing:8
                        RowLayout{Layout.fillWidth:true;TextLabel {objectName:"quotaProviderLabel";text:"Codex weekly allowance";font.pixelSize:16;font.weight:Font.DemiBold;Layout.fillWidth:true}TextLabel {text:(q.stale ? "Last known · " : "")+(q.age || "");font.pixelSize:11;color:AppStyle.muted}}
                        RowLayout{Layout.fillWidth:true;TextLabel {text:q.available ? Math.round(q.used)+"%" : "—";font.pixelSize:38;font.weight:Font.DemiBold;font.letterSpacing:-1;Layout.fillWidth:true}TextLabel {text:q.available ? q.remaining+" remaining" : "Quota tools are optional";font.pixelSize:13;color:AppStyle.muted}}
                        WeeklyQuotaBar{objectName:"quotaPageBar";Layout.fillWidth:true;barHeight:7;used:q.used || 0;pace:q.paceUsed === null || q.paceUsed === undefined ? -1 : q.paceUsed}
                        TextLabel {text:q.available ? "Resets "+q.reset+"  ·  "+q.countdown : "Install quota-axi to display your Codex allowance.";font.pixelSize:12;color:AppStyle.muted;Layout.fillWidth:true;wrapMode:Text.WordWrap;elide:Text.ElideNone}
                    }
                }
                Rectangle {
                    Layout.fillWidth:true;Layout.preferredHeight:bankContent.implicitHeight+28;radius:12;color:AppStyle.surface
                    ColumnLayout {
                        id:bankContent;anchors.fill:parent;anchors.margins:14;spacing:8
                        RowLayout{Layout.fillWidth:true;TextLabel{text:"Banked resets";font.pixelSize:15;font.weight:Font.DemiBold;Layout.fillWidth:true}TextLabel{objectName:"bankedResetCount";text:q.banks>=0 ? q.banks+" available" : "Unavailable";font.pixelSize:12;color:AppStyle.muted}}
                        TextLabel{visible:!!q.banksStale;Layout.fillWidth:true;text:"Last known · "+q.banksAge;font.pixelSize:11;color:AppStyle.muted}
                        Repeater {
                            model:q.expiries || []
                            delegate:RowLayout {
                                required property var modelData;required property int index
                                Layout.fillWidth:true;spacing:16
                                TextLabel{text:"Reset "+(index+1);font.pixelSize:12;color:AppStyle.muted}
                                TextLabel{objectName:"bankExpiry-"+index;text:modelData==="Does not expire" ? modelData : "Expires "+modelData;Layout.fillWidth:true;font.pixelSize:12;font.weight:Font.Medium;horizontalAlignment:Text.AlignRight;wrapMode:Text.WordWrap;elide:Text.ElideNone}
                            }
                        }
                        TextLabel{objectName:"bankedResetNotice";visible:text!=="";Layout.fillWidth:true;text:q.banks<0 ? "Banked reset details are unavailable from Codex." : q.banks===0 ? "No banked resets available." : q.bankExpiryNotice || "";font.pixelSize:11;color:AppStyle.muted;wrapMode:Text.WordWrap;elide:Text.ElideNone}
                    }
                }
                RowLayout{Layout.fillWidth:true;TextLabel {text:"Usage windows";font.pixelSize:15;font.weight:Font.DemiBold;Layout.fillWidth:true}}
                DataTable{Layout.fillWidth:true;Layout.preferredHeight:220;rows:q.windows || [];columns:[{key:"name",label:"Window",weight:3},{key:"used",label:"Used",weight:1},{key:"remaining",label:"Remaining",weight:1.4},{key:"reset",label:"Resets",weight:3}]}
            }
        }
    }
    Component{id:limitsPage;LimitsPage{}}
    Component{id:settings;SettingsPage{onPricingRequested:pricing.open()}}
    DatePicker{id:dates}
    PriceEditor{id:pricing}
    Popup {
        id:notifications;objectName:"notificationsPopup";parent:Overlay.overlay;x:parent.width-width-12;y:48;width:Math.min(370,parent.width-24);height:Math.min(parent.height-60,notificationContents.item ? notificationContents.item.implicitHeight+28 : 80);padding:14;focus:true;closePolicy:Popup.CloseOnEscape|Popup.CloseOnPressOutside
        background:Rectangle{radius:20;color:AppStyle.surface;border.width:1;border.color:AppStyle.line}
        contentItem:Loader {
            id:notificationContents; objectName:"notificationsContentLoader"
            active:notifications.visible
            sourceComponent:ColumnLayout{
                id:notices;spacing:12
                RowLayout{Layout.fillWidth:true;TextLabel {text:"Notifications";font.pixelSize:18;font.weight:Font.DemiBold;Layout.fillWidth:true}SoftButton{iconName:"close";compact:true;tone:"ghost";onClicked:notifications.close()}}
                TextLabel {visible:s.notifications.length===0;text:"You’re up to date.";font.pixelSize:13;color:AppStyle.muted;Layout.topMargin:14;Layout.bottomMargin:16}
                ScrollView{visible:s.notifications.length>0;Layout.fillWidth:true;Layout.fillHeight:true;implicitHeight:Math.min(340,noticeList.implicitHeight);clip:true;ScrollBar.vertical:SoftScrollBar{}ScrollBar.horizontal.policy:ScrollBar.AlwaysOff
                    ColumnLayout{id:noticeList;width:parent.width;spacing:20
                        Repeater{model:s.notifications;delegate:ColumnLayout{required property var modelData;Layout.fillWidth:true;spacing:7;TextLabel {text:modelData.title;font.pixelSize:13;font.weight:Font.Medium;Layout.fillWidth:true}TextLabel {text:modelData.detail;font.pixelSize:11;color:AppStyle.muted;Layout.fillWidth:true;wrapMode:Text.WordWrap;elide:Text.ElideNone}SoftButton{visible:modelData.action==="pricing";text:"Add prices";compact:true;onClicked:{notifications.close();pricing.open()}}}}
                    }
                }
            }
        }
    }
    Shortcut{sequence:"Alt+Left";enabled:!dashboard.compact;onActivated:bridge.cyclePeriod(-1)}
    Shortcut{sequence:"Alt+Right";enabled:!dashboard.compact;onActivated:bridge.cyclePeriod(1)}
    Shortcut{sequence:"Ctrl+R";onActivated:dashboard.compact ? bridge.refreshQuota() : bridge.refresh()}
    Shortcut{sequence:"Ctrl+,";enabled:!dashboard.compact;onActivated:dashboard.page="Settings"}
}
