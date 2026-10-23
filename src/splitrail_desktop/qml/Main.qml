import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
ApplicationWindow {
    id: root
    objectName: "splitrailWindow"
    visible: true
    width: 1280; height: 860
    minimumWidth: bridge.state.onboarding ? 660 : 280
    minimumHeight: bridge.state.onboarding ? 680 : 110
    title: bridge.state.demo ? "Splitrail · Demo" : "Splitrail"
    color: AppStyle.bg
    font.family: AppStyle.font
    Component.onCompleted: { AppStyle.dark = Qt.binding(function(){return bridge.state.theme==="Nord"}); AppStyle.font = appFont }
    readonly property bool backgrounded: !visible || visibility === Window.Minimized
    onBackgroundedChanged: bridge.setWindowVisible(!backgrounded)
    onClosing: function(close) {
        if (bridge.state.menuBarMode) { close.accepted = false; root.hide() }
        else bridge.close()
    }
    Loader {
        id: shell; anchors.fill: parent
        active: !root.backgrounded
        sourceComponent: bridge.state.onboarding ? setup : dashboard
    }
    Component { id: setup; Onboarding {} }
    Component { id: dashboard; Dashboard {} }
}
