import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
Button {
    id: control
    property string iconName: ""
    property string tone: "secondary"
    property bool compact: false
    property bool large: false
    implicitHeight: large ? 48 : compact ? 34 : 40
    implicitWidth: Math.max(iconName && !text ? implicitHeight : 0, contentItem.implicitWidth + (iconName && !text ? 0 : 30))
    hoverEnabled: true
    opacity: enabled ? 1 : .42
    scale: down ? .98 : 1
    Behavior on scale { NumberAnimation { duration: 90 } }
    Accessible.name: text || accessibleName
    property string accessibleName: iconName
    background: Rectangle {
        radius: control.large ? 13 : 10
        color: control.tone==="primary" ? AppStyle.accent : (control.tone==="ghost" || control.tone==="link") ? (control.hovered ? AppStyle.hover : "transparent") : control.hovered ? AppStyle.hover : AppStyle.fill
        border.width: control.activeFocus ? 2 : 0; border.color: AppStyle.accent
        opacity: control.down ? .8 : 1
        Behavior on color { ColorAnimation { duration: 100 } }
    }
    contentItem: RowLayout {
        spacing: 8
        Icon { visible: control.iconName!==""; name: control.iconName; color: control.tone==="primary" ? AppStyle.accentInk : AppStyle.ink; Layout.preferredWidth: 18; Layout.preferredHeight: 18; Layout.alignment: Qt.AlignVCenter }
        TextLabel { visible: control.text!==""; text: control.text; color: control.tone==="primary" ? AppStyle.accentInk : control.tone==="link" ? AppStyle.accent : AppStyle.ink; font.pixelSize: 13; font.weight: Font.Medium; horizontalAlignment: Text.AlignHCenter; Layout.fillWidth: true }
    }
    ToolTip.visible: hovered && text==="" && accessibleName!==""
    ToolTip.text: accessibleName
    ToolTip.delay: 600
}
