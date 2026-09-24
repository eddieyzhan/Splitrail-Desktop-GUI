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
    property string query: ""
    readonly property bool settingsPage: page==="Settings"
    RowLayout {
        anchors.fill: parent; spacing: 0
        Rectangle {
            Layout.preferredWidth: dashboard.width<1100 ? 182 : 210; Layout.fillHeight: true; color: AppStyle.sidebar
            ColumnLayout {
                anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16; anchors.topMargin: 30; anchors.bottomMargin: 22; spacing: 8
                RowLayout { Layout.leftMargin: 12; spacing: 10; Layout.bottomMargin: 36
                    Rectangle { width: 29; height: 29; radius: 10; color: AppStyle.accent; Icon {name:"logo";color:AppStyle.accentInk;anchors.centerIn:parent;width:20;height:20} }
                    TextLabel { text:"Splitrail";font.pixelSize:19;font.weight:Font.DemiBold;font.letterSpacing:-.7 }
                }
                Repeater { model: [{title:"Overview",iconName:"grid"},{title:"Models",iconName:"models"},{title:"Tools",iconName:"tools"},{title:"History",iconName:"calendar"},{title:"Quota",iconName:"quota"}]; delegate: Button {
                    required property var modelData
                    Accessible.name: modelData.title
                    Layout.fillWidth: true; implicitHeight: 43
                    onClicked: {dashboard.page=modelData.title;dashboard.query=""}
                    background: Rectangle {radius:11;color:dashboard.page===modelData.title ? AppStyle.surface : parent.hovered ? AppStyle.hover : "transparent";border.width:parent.activeFocus ? 1 : 0;border.color:AppStyle.accent}
                    contentItem: RowLayout {spacing:12;Icon {name:modelData.iconName;color:dashboard.page===modelData.title ? AppStyle.accent : AppStyle.muted;Layout.leftMargin:8;width:18;height:18}TextLabel {text:modelData.title;color:dashboard.page===modelData.title ? AppStyle.ink : AppStyle.muted;font.pixelSize:13;font.weight:dashboard.page===modelData.title ? Font.DemiBold : Font.Normal;Layout.fillWidth:true} }
                } }
                Item { Layout.fillHeight: true }
                Rectangle {
                    Layout.fillWidth: true; height: 78; radius: 13; color: AppStyle.surface
                    MouseArea {anchors.fill:parent;cursorShape:Qt.PointingHandCursor;onClicked:dashboard.page="Settings"}
                    ColumnLayout {anchors.fill:parent;anchors.margins:14;spacing:7
                        RowLayout {spacing:7;Rectangle{width:5;height:5;radius:3;color:s.connected ? AppStyle.green : AppStyle.faint}TextLabel {text:s.connected ? "Private sync" : "On this device";font.pixelSize:11;font.weight:Font.Medium}}
                        TextLabel {text:s.connected ? (s.syncBusy ? "Syncing…" : "Connected to GitHub") : "Connect your devices";font.pixelSize:10;color:AppStyle.muted;Layout.fillWidth:true}
                    }
                }
                Button {
                    objectName: "settingsNavigation"; Accessible.name: "Settings"; Layout.fillWidth: true; Layout.topMargin: 12; implicitHeight: 43
                    onClicked: dashboard.page="Settings"
                    background: Rectangle {radius:11;color:dashboard.settingsPage ? AppStyle.surface : parent.hovered ? AppStyle.hover : "transparent";border.width:parent.activeFocus ? 1 : 0;border.color:AppStyle.accent}
                    contentItem: RowLayout {spacing:12;Icon{name:"settings";color:dashboard.settingsPage ? AppStyle.accent : AppStyle.muted;Layout.leftMargin:8;width:18;height:18}TextLabel{text:"Settings";font.pixelSize:13;color:dashboard.settingsPage ? AppStyle.ink : AppStyle.muted;font.weight:dashboard.settingsPage ? Font.DemiBold : Font.Normal;Layout.fillWidth:true}}
                }
            }
        }
        ColumnLayout {
            Layout.fillWidth:true;Layout.fillHeight:true;Layout.margins:dashboard.width<1100 ? 24 : 36;spacing:24
            RowLayout {
                Layout.fillWidth:true;spacing:12
                ColumnLayout {spacing:6;TextLabel {text:dashboard.page;font.pixelSize:28;font.weight:Font.DemiBold;font.letterSpacing:-1}TextLabel {text:dashboard.settingsPage ? "Make it yours." : s.busy ? "Updating your usage…" : clock.refreshAge;color:AppStyle.muted;font.pixelSize:12}}
                Item { Layout.fillWidth: true }
                SoftButton {objectName:"refreshButton";iconName:"refresh";tone:"ghost";visible:!dashboard.settingsPage;enabled:!s.busy && !s.quotaBusy && !s.syncBusy;accessibleName:"Refresh usage";onClicked:bridge.refresh()}
                Item {width:38;height:40
                    SoftButton {objectName:"notificationsButton";anchors.fill:parent;iconName:"bell";tone:"ghost";accessibleName:"Notifications";onClicked:notifications.open()}
                    Rectangle{visible:s.notifications.length>0;width:6;height:6;radius:3;color:AppStyle.accent;anchors.right:parent.right;anchors.rightMargin:5;anchors.top:parent.top;anchors.topMargin:5}
                }
            }
            RowLayout {
                visible:!dashboard.settingsPage && dashboard.page!=="Quota";Layout.fillWidth:true;spacing:8
                TextLabel {text:s.range+(s.hasUsage && s.displayedMode!==s.usageMode ? "  ·  Previous "+({combined:"All devices",local:"This device",codex:"Codex"})[s.displayedMode]+" data" : "");font.pixelSize:12;color:AppStyle.muted;Layout.fillWidth:true}
                SoftButton{objectName:"previousPeriod";iconName:"left";tone:"ghost";compact:true;enabled:s.canPrevious;accessibleName:"Previous time frame";onClicked:bridge.cyclePeriod(-1)}
                Segmented {objectName:"periodTabs";options:["Today","Week","Month","Year","All time"];selected:["Day","Week","Month","Year","All time"].indexOf(s.preset);onChosen:function(i){bridge.choosePeriod(["Day","Week","Month","Year","All time"][i])}}
                SoftButton{objectName:"nextPeriod";iconName:"right";tone:"ghost";compact:true;enabled:s.canNext;accessibleName:"Next time frame";onClicked:bridge.cyclePeriod(1)}
                SoftButton{objectName:"dateSelector";text:s.preset==="Custom" ? "Custom" : "";accessibleName:"Choose dates";iconName:"calendar";onClicked:dates.open()}
            }
            Loader {
                id: content;Layout.fillWidth:true;Layout.fillHeight:true
                sourceComponent: dashboard.settingsPage ? settings : dashboard.page==="Overview" ? overview : dashboard.page==="Quota" ? quotaPage : tablePage
            }
        }
    }
    Component {
        id: overview
        Flickable {
            clip:true;contentWidth:width;contentHeight:Math.max(height,overviewContent.implicitHeight);boundsBehavior:Flickable.StopAtBounds;ScrollBar.vertical:SoftScrollBar{}
            ColumnLayout {
                id:overviewContent;width:parent.width-4;spacing:16
                RowLayout {
                    Layout.fillWidth:true;spacing:16
                    Rectangle {
                        Layout.fillWidth:true;Layout.preferredWidth:2;Layout.preferredHeight:142;radius:20;color:AppStyle.surface
                        ColumnLayout {anchors.fill:parent;anchors.margins:24;spacing:8
                            TextLabel {text:"Estimated cost";font.pixelSize:12;color:AppStyle.muted}
                            Item{Layout.fillHeight:true}
                            TextLabel {text:s.hasUsage ? s.summary.cost : "—";font.pixelSize:40;font.weight:Font.DemiBold;font.letterSpacing:-1.8;Layout.fillWidth:true}
                            TextLabel {text:"USD  ·  API equivalent";font.pixelSize:11;color:AppStyle.faint}
                        }
                    }
                    Repeater {model:[{title:"Tokens",value:s.summary.tokens,detail:s.summary.exactTokens ? s.summary.exactTokens+" total" : ""},{title:"Conversations",value:s.summary.conversations,detail:s.summary.messages ? s.summary.messages+" messages" : ""}];delegate:Rectangle {
                        required property var modelData
                        Layout.fillWidth:true;Layout.preferredWidth:1;Layout.preferredHeight:142;radius:20;color:AppStyle.surface
                        ColumnLayout {anchors.fill:parent;anchors.margins:24;spacing:8;TextLabel {text:modelData.title;font.pixelSize:12;color:AppStyle.muted}Item{Layout.fillHeight:true}TextLabel {text:s.hasUsage ? modelData.value || "0" : "—";font.pixelSize:30;font.weight:Font.DemiBold;font.letterSpacing:-1;Layout.fillWidth:true}TextLabel {text:modelData.detail;font.pixelSize:11;color:AppStyle.faint;Layout.fillWidth:true}}
                    } }
                }
                RowLayout {
                    Layout.fillWidth:true;spacing:18
                    Rectangle {
                        objectName:"overviewQuotaCard"
                        Layout.fillWidth:true;Layout.fillHeight:true;Layout.preferredWidth:440;Layout.minimumWidth:420;Layout.preferredHeight:Math.max(196,quotaOverviewContent.implicitHeight+48);radius:20;color:AppStyle.surface
                        ColumnLayout {
                            id:quotaOverviewContent;anchors.fill:parent;anchors.margins:24;spacing:16
                            RowLayout {
                                Layout.fillWidth:true;spacing:10
                                TextLabel {text:"Weekly quota"+(q.stale ? " · cached" : "");font.pixelSize:15;font.weight:Font.DemiBold;Layout.fillWidth:true}
                                TextLabel {text:q.available ? Math.round(q.used)+"% used" : "Unavailable";font.pixelSize:15;font.weight:Font.Medium;color:AppStyle.muted}
                                SoftButton{iconName:"right";tone:"ghost";implicitWidth:24;implicitHeight:24;accessibleName:"Quota details";onClicked:dashboard.page="Quota"}
                            }
                            Rectangle {
                                objectName:"overviewQuotaBar"
                                Layout.fillWidth:true;height:10;radius:5;color:AppStyle.fill
                                Rectangle{width:parent.width*Math.min(100,q.used || 0)/100;height:parent.height;radius:5;color:AppStyle.teal}
                            }
                            RowLayout {
                                Layout.fillWidth:true;Layout.fillHeight:true;spacing:24
                                ColumnLayout {
                                    objectName:"overviewWeeklyColumn"
                                    Layout.fillWidth:true;Layout.preferredWidth:1;Layout.alignment:Qt.AlignTop;spacing:8
                                    TextLabel {text:"Next reset";font.pixelSize:12;color:AppStyle.muted}
                                    TextLabel {objectName:"overviewResetTime";text:q.resetParts ? q.resetParts.date+(q.resetParts.time ? ", "+q.resetParts.time : "") : "Unavailable";font.pixelSize:12;font.weight:Font.Medium;Layout.fillWidth:true;wrapMode:Text.WordWrap;elide:Text.ElideNone}
                                    TextLabel {objectName:"overviewQuotaAge";text:(q.stale ? "Last known · " : "")+(q.age || "Not yet updated");font.pixelSize:10;color:AppStyle.faint;Layout.fillWidth:true}
                                }
                                ColumnLayout {
                                    objectName:"overviewBankedColumn"
                                    Layout.fillWidth:true;Layout.preferredWidth:1;Layout.alignment:Qt.AlignTop;spacing:8
                                    TextLabel {objectName:"overviewBankedResets";text:(q.banks>=0 ? q.banks+" banked" : "Banked resets · —")+(q.banksStale ? " · cached" : "");font.pixelSize:12;color:AppStyle.muted;Layout.fillWidth:true;horizontalAlignment:Text.AlignRight;Accessible.name:q.banks>=0 ? q.banks+" banked resets available; expiry times below" : "Banked resets unavailable"}
                                    ColumnLayout {
                                        objectName:"overviewBankedExpiry";Layout.fillWidth:true;spacing:6
                                        Repeater {
                                            model:q.bankExpiryRows || []
                                            delegate:TextLabel {
                                                required property var modelData
                                                text:modelData.date+(modelData.time ? ", "+modelData.time : "")
                                                font.pixelSize:12;font.weight:Font.Medium;Layout.fillWidth:true;horizontalAlignment:Text.AlignRight;wrapMode:Text.WordWrap;elide:Text.ElideNone
                                                Accessible.name:modelData.time ? "Expires "+text : text
                                            }
                                        }
                                        TextLabel {visible:!!q.bankExpiryNotice;text:q.bankExpiryRows && q.bankExpiryRows.length>0 ? "Some expiries unavailable" : "Expiry unavailable";font.pixelSize:11;color:AppStyle.muted;Layout.fillWidth:true;horizontalAlignment:Text.AlignRight;wrapMode:Text.WordWrap;elide:Text.ElideNone}
                                    }
                                }
                            }
                        }
                    }
                    Rectangle {
                        objectName:"overviewModelsCard"
                        Layout.fillWidth:true;Layout.fillHeight:true;Layout.preferredWidth:220;Layout.minimumWidth:220;Layout.preferredHeight:modelsOverviewContent.implicitHeight+48;radius:20;color:AppStyle.surface
                        ColumnLayout {id:modelsOverviewContent;anchors.fill:parent;anchors.margins:24;spacing:16
                            RowLayout {Layout.fillWidth:true;TextLabel {text:"Top models";font.pixelSize:15;font.weight:Font.DemiBold;Layout.fillWidth:true}SoftButton{iconName:"right";implicitWidth:24;implicitHeight:24;tone:"ghost";accessibleName:"View all models";onClicked:dashboard.page="Models"}}
                            ColumnLayout {Layout.fillWidth:true;spacing:16
                                Repeater {model:s.models.slice(0,3);delegate:RowLayout{required property var modelData;required property int index;Layout.fillWidth:true;spacing:10;Rectangle{width:6;height:6;radius:3;color:[AppStyle.accent,AppStyle.teal,AppStyle.purple][index]}TextLabel {text:modelData.name;Layout.fillWidth:true;font.pixelSize:12}TextLabel {text:modelData.cost;font.pixelSize:12;font.weight:Font.Medium}}}
                            }
                            TextLabel {visible:s.models.length===0;text:"Models will appear as you use your tools.";font.pixelSize:12;color:AppStyle.muted;Layout.fillWidth:true;wrapMode:Text.WordWrap;elide:Text.ElideNone}
                            Item{Layout.fillHeight:true}
                        }
                    }
                }
                UsageChart { objectName:"activityChart";Layout.fillWidth:true;Layout.preferredHeight:Math.max(256,Math.min(290,dashboard.height-600));points:s.chart;detail:s.chartDetail;emptyMessage:s.chartEmpty }
            }
        }
    }
    Component {
        id:tablePage
        ColumnLayout {
            spacing:16
            RowLayout {Layout.fillWidth:true;TextLabel {text:dashboard.page==="Models" ? s.models.length+" models" : dashboard.page==="Tools" ? s.tools.length+" tools" : s.days.length+" days";font.pixelSize:12;color:AppStyle.muted;Layout.fillWidth:true}SoftField{Layout.preferredWidth:240;implicitHeight:38;placeholderText:"Search "+dashboard.page.toLowerCase();text:dashboard.query;onTextEdited:dashboard.query=text;Accessible.name:placeholderText}SoftButton{visible:dashboard.page==="Models";text:"Edit prices";compact:true;onClicked:pricing.open()}}
            Flow {visible:dashboard.page==="Models";Layout.fillWidth:true;spacing:18
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
        ColumnLayout {
            spacing:20
            Rectangle {Layout.fillWidth:true;height:182;radius:20;color:AppStyle.surface
                ColumnLayout{anchors.fill:parent;anchors.margins:28;spacing:14
                    RowLayout{Layout.fillWidth:true;TextLabel {text:"Weekly allowance";font.pixelSize:16;font.weight:Font.DemiBold;Layout.fillWidth:true}TextLabel {text:(q.stale ? "Last known · " : "")+(q.age || "");font.pixelSize:11;color:AppStyle.muted}}
                    RowLayout{Layout.fillWidth:true;TextLabel {text:q.available ? Math.round(q.used)+"%" : "—";font.pixelSize:38;font.weight:Font.DemiBold;font.letterSpacing:-1;Layout.fillWidth:true}TextLabel {text:q.available ? q.remaining+" remaining" : "Quota tools are optional";font.pixelSize:13;color:AppStyle.muted}}
                    Rectangle{Layout.fillWidth:true;height:7;radius:4;color:AppStyle.fill;Rectangle{width:parent.width*Math.min(100,q.used || 0)/100;height:7;radius:4;color:AppStyle.teal}}
                    TextLabel {text:q.available ? "Resets "+q.reset+"  ·  "+q.countdown : "Install quota-axi to display your Codex allowance.";font.pixelSize:12;color:AppStyle.muted;Layout.fillWidth:true}
                }
            }
            RowLayout{Layout.fillWidth:true;TextLabel {text:"Usage windows";font.pixelSize:15;font.weight:Font.DemiBold;Layout.fillWidth:true}TextLabel {text:q.banks>=0 ? q.banks+" banked resets" : "";color:AppStyle.muted;font.pixelSize:12}}
            DataTable{Layout.fillWidth:true;Layout.fillHeight:true;rows:q.windows || [];columns:[{key:"name",label:"Window",weight:3},{key:"used",label:"Used",weight:1},{key:"remaining",label:"Remaining",weight:1.4},{key:"reset",label:"Resets",weight:3}]}
            TextLabel {visible:!!q.expiries && q.expiries.length>0;text:q.expiries ? "Banked resets: "+q.expiries.join(" · ") : "";color:AppStyle.muted;font.pixelSize:11;wrapMode:Text.WordWrap;elide:Text.ElideNone;Layout.fillWidth:true}
        }
    }
    Component{id:settings;SettingsPage{onPricingRequested:pricing.open()}}
    DatePicker{id:dates}
    PriceEditor{id:pricing}
    Popup {
        id:notifications;objectName:"notificationsPopup";parent:Overlay.overlay;x:parent.width-width-28;y:72;width:370;height:Math.min(480,notices.implicitHeight+52);padding:24;focus:true;closePolicy:Popup.CloseOnEscape|Popup.CloseOnPressOutside
        background:Rectangle{radius:20;color:AppStyle.surface;border.width:1;border.color:AppStyle.line}
        contentItem:ColumnLayout{
            id:notices;spacing:16
            RowLayout{Layout.fillWidth:true;TextLabel {text:"Notifications";font.pixelSize:18;font.weight:Font.DemiBold;Layout.fillWidth:true}SoftButton{iconName:"close";compact:true;tone:"ghost";onClicked:notifications.close()}}
            TextLabel {visible:s.notifications.length===0;text:"You’re up to date.";font.pixelSize:13;color:AppStyle.muted;Layout.topMargin:14;Layout.bottomMargin:16}
            ScrollView{visible:s.notifications.length>0;Layout.fillWidth:true;Layout.fillHeight:true;implicitHeight:Math.min(340,noticeList.implicitHeight);clip:true;ScrollBar.vertical:SoftScrollBar{}ScrollBar.horizontal.policy:ScrollBar.AlwaysOff
                ColumnLayout{id:noticeList;width:parent.width;spacing:20
                    Repeater{model:s.notifications;delegate:ColumnLayout{required property var modelData;Layout.fillWidth:true;spacing:7;TextLabel {text:modelData.title;font.pixelSize:13;font.weight:Font.Medium;Layout.fillWidth:true}TextLabel {text:modelData.detail;font.pixelSize:11;color:AppStyle.muted;Layout.fillWidth:true;wrapMode:Text.WordWrap;elide:Text.ElideNone}SoftButton{visible:modelData.action==="pricing";text:"Add prices";compact:true;onClicked:{notifications.close();pricing.open()}}}}
                }
            }
        }
    }
    Shortcut{sequence:"Alt+Left";onActivated:bridge.cyclePeriod(-1)}
    Shortcut{sequence:"Alt+Right";onActivated:bridge.cyclePeriod(1)}
    Shortcut{sequence:"Ctrl+R";onActivated:bridge.refresh()}
    Shortcut{sequence:"Ctrl+,";onActivated:dashboard.page="Settings"}
}
