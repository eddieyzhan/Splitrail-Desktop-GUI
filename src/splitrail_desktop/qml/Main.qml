import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
ApplicationWindow {
    id: root
    objectName: "splitrailWindow"
    visible: true
    width: 1280; height: 860
    minimumWidth: bridge.state.onboarding ? 660 : 960
    minimumHeight: 680
    title: bridge.state.demo ? "Splitrail · Demo" : "Splitrail"
    color: AppStyle.bg
    font.family: AppStyle.font
    Component.onCompleted: { AppStyle.dark = Qt.binding(function(){return bridge.state.theme==="Nord"}); AppStyle.font = appFont }
    onClosing: bridge.close()
    Loader {
        id: shell; anchors.fill: parent
        sourceComponent: bridge.state.onboarding ? setup : dashboard
    }
    Component { id: setup; Onboarding {} }
    Component { id: dashboard; Dashboard {} }
}
