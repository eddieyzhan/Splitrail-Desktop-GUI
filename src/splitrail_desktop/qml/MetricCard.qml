import QtQuick
import QtQuick.Controls

Control {
    id: card
    property string label: ""
    property string value: "—"
    property string explanation: ""
    implicitHeight: 58
    implicitWidth: 150
    padding: width < 120 ? 8 : 12
    hoverEnabled: true
    focusPolicy: Qt.TabFocus
    Accessible.role: Accessible.StaticText
    Accessible.name: label + ": " + value
    Accessible.description: explanation
    background: Rectangle {
        radius: 12; color: AppStyle.surface
        border.color: AppStyle.accent; border.width: card.activeFocus ? 1 : 0
    }
    contentItem: TextLabel {
        objectName: "overview" + card.label + "Value"
        text: card.value
        font.pixelSize: 24; minimumPixelSize: 14; fontSizeMode: Text.HorizontalFit
        font.weight: Font.DemiBold; font.letterSpacing: -.6
        Accessible.ignored: true
    }
    ToolTip {
        id: hint
        objectName: card.objectName + "Tooltip"
        visible: card.hovered || card.activeFocus
        delay: card.activeFocus ? 0 : 300
        text: card.explanation
        width: Math.min(300, Overlay.overlay ? Overlay.overlay.width - 24 : 300)
        contentItem: TextLabel {text: hint.text; font.pixelSize: 12; wrapMode: Text.WordWrap; elide: Text.ElideNone}
        background: Rectangle {radius: 10; color: AppStyle.surface; border.color: AppStyle.line}
    }
    Keys.onEscapePressed: { focus = false; hint.close() }
}
