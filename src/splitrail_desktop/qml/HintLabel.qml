import QtQuick
import QtQuick.Controls

TextLabel {
    id: label
    property string explanation: ""
    activeFocusOnTab: explanation !== ""
    Accessible.description: explanation
    HoverHandler {id: hover}
    ToolTip {
        id: hint
        objectName: label.objectName + "Tooltip"
        visible: explanation !== "" && (hover.hovered || label.activeFocus)
        delay: label.activeFocus ? 0 : 300
        text: label.explanation
        width: Math.min(300, Overlay.overlay ? Overlay.overlay.width - 24 : 300)
        contentItem: TextLabel {text: hint.text; font.pixelSize: 12; wrapMode: Text.WordWrap; elide: Text.ElideNone}
        background: Rectangle {radius: 10; color: AppStyle.surface; border.color: AppStyle.line}
    }
    Keys.onEscapePressed: { focus = false; hint.close() }
}
