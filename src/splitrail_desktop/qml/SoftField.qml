import QtQuick
import QtQuick.Controls
TextField {
    id: field
    implicitHeight: 44
    font.family: AppStyle.font; font.pixelSize: 14
    color: AppStyle.ink; placeholderTextColor: AppStyle.faint
    selectionColor: AppStyle.accent; selectedTextColor: AppStyle.accentInk
    leftPadding: 14; rightPadding: 14
    selectByMouse: true
    background: Rectangle { radius: 11; color: AppStyle.fill; border.width: field.activeFocus ? 1.5 : 0; border.color: AppStyle.accent }
}
