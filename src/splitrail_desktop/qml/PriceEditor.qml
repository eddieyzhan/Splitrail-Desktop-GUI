import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
Popup {
    id: dialog
    objectName: "priceEditor"
    parent: Overlay.overlay; anchors.centerIn: parent
    width: Math.min(760,parent.width-24); height: Math.min(520,parent.height-24); padding: 12
    modal: true; focus: true
    property var selected: ({})
    property int filter: 0
    function select(row) { if (editorLoader.item) editorLoader.item.select(row) }
    onOpened: {bridge.loadPrices(); select(bridge.state.prices.find(r=>r.name===selected.name) || {})}
    background: Rectangle { radius: 24; color: AppStyle.surface }
    Overlay.modal: Rectangle { color: AppStyle.dark ? "#80000000" : "#33262D44" }
    contentItem: Loader {
        id: editorLoader; objectName: "priceEditorContentLoader"
        active: dialog.visible
        sourceComponent: Flickable {
            clip: true; contentWidth: Math.max(width,600); contentHeight: Math.max(height,320)
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: SoftScrollBar {}
            ScrollBar.horizontal: SoftScrollBar {}
            function select(row) {dialog.selected=row;name.text=row.name || "";input.text=row.input || "";output.text=row.output || "";read.text=row.cache_read || "";write.text=row.cache_write || ""}
            ColumnLayout {
                width: parent.contentWidth; height: parent.contentHeight; spacing: 10
                RowLayout { Layout.fillWidth: true; ColumnLayout { spacing: 6; TextLabel { text: "Model pricing"; font.pixelSize: 18; font.weight: Font.DemiBold; font.letterSpacing: -.5 } TextLabel { text: "USD per million tokens"; color: AppStyle.muted; font.pixelSize: 12 } } Item { Layout.fillWidth: true } SoftButton { iconName: "close"; tone: "ghost"; onClicked: dialog.close() } }
                RowLayout {
                    Layout.fillHeight: true; Layout.fillWidth: true; spacing: 6
                    ColumnLayout {
                        Layout.preferredWidth: Math.max(180, dialog.availableWidth * .40); Layout.fillHeight: true; spacing: 6
                        SoftField { implicitHeight: 30; id: search; Layout.fillWidth: true; placeholderText: "Search models"; Accessible.name: "Search models" }
                        Segmented { Layout.fillWidth: true; compact: true; options: ["All","Unpriced","Custom"]; selected: dialog.filter; onChosen: function(i){dialog.filter=i} }
                        ListView {
                            Layout.fillHeight: true; Layout.fillWidth: true; clip: true; spacing: 3; boundsBehavior: Flickable.StopAtBounds
                            model: bridge.state.prices.filter(r=>(dialog.filter===0 || dialog.filter===1 && r.missing || dialog.filter===2 && r.custom) && r.name.toLowerCase().includes(search.text.toLowerCase()))
                            ScrollBar.vertical: SoftScrollBar {}
                            delegate: ItemDelegate {
                                required property var modelData
                                width: ListView.view.width; height: 42
                                onClicked: dialog.select(modelData)
                                background: Rectangle { radius: 10; color: dialog.selected.name===modelData.name ? AppStyle.accentFill : parent.hovered ? AppStyle.fill : "transparent" }
                                contentItem: ColumnLayout { spacing: 4; TextLabel { text: modelData.name; font.pixelSize: 12; font.weight: Font.Medium; Layout.fillWidth: true } TextLabel { text: modelData.custom ? "Custom price" : modelData.missing ? modelData.priceNote || "Needs pricing" : modelData.provider; font.pixelSize: 10; color: AppStyle.muted } }
                            }
                        }
                        SoftButton { text: "Add model"; iconName: "plus"; Layout.fillWidth: true; onClicked: {dialog.select({});name.forceActiveFocus()} }
                    }
                    ColumnLayout {
                        Layout.fillHeight: true; Layout.fillWidth: true; spacing: 6
                        TextLabel { text: "Model ID"; font.pixelSize: 12; color: AppStyle.muted }
                        SoftField { implicitHeight: 30; id: name; objectName: "priceModel"; Layout.fillWidth: true; placeholderText: "e.g. my-model-v1"; Accessible.name: "Model ID" }
                        GridLayout {
                            Layout.fillWidth: true; columns: 2; columnSpacing: 12; rowSpacing: 4; Layout.topMargin: 4
                            TextLabel { text: "Input"; color: AppStyle.muted; font.pixelSize: 12 }
                            TextLabel { text: "Output"; color: AppStyle.muted; font.pixelSize: 12 }
                            SoftField { implicitHeight: 30; id: input; objectName: "priceInput"; Layout.fillWidth: true; Layout.preferredWidth: 130; placeholderText: "0.00"; Accessible.name: "Input price" }
                            SoftField { implicitHeight: 30; id: output; objectName: "priceOutput"; Layout.fillWidth: true; Layout.preferredWidth: 130; placeholderText: "0.00"; Accessible.name: "Output price" }
                            TextLabel { text: "Cache read"; color: AppStyle.muted; font.pixelSize: 12; Layout.topMargin: 4 }
                            TextLabel { text: "Cache write"; color: AppStyle.muted; font.pixelSize: 12; Layout.topMargin: 4 }
                            SoftField { implicitHeight: 30; id: read; Layout.fillWidth: true; Layout.preferredWidth: 130; placeholderText: "Optional"; Accessible.name: "Cache read price" }
                            SoftField { implicitHeight: 30; id: write; Layout.fillWidth: true; Layout.preferredWidth: 130; placeholderText: "Optional"; Accessible.name: "Cache write price" }
                        }
                        TextLabel { text: "Enter 0 for free tokens. Leave unused cache prices blank."; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; font.pixelSize: 11; color: AppStyle.muted; Layout.topMargin: 6 }
                        Item { Layout.fillHeight: true }
                        TextLabel { text: bridge.state.priceError || bridge.state.priceNotice; visible: text!==""; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; color: bridge.state.priceError ? AppStyle.red : AppStyle.green; font.pixelSize: 12 }
                        SoftButton { objectName: "savePrice"; compact: true; text: "Save price"; tone: "primary"; Layout.fillWidth: true; enabled: name.text.trim()!==""; onClicked: if(bridge.savePrice(name.text,input.text,output.text,read.text,write.text)) dialog.selected=bridge.state.prices.find(r=>r.name===name.text) || {} }
                        SoftButton { text: "Restore built-in price"; tone: "ghost"; visible: !!dialog.selected.custom; Layout.fillWidth: true; onClicked: { bridge.removePrice(name.text); dialog.select(bridge.state.prices.find(r=>r.name===name.text) || {}) } }
                    }
                }
            }
        }
    }
}
