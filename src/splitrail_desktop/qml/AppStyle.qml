pragma Singleton
import QtQuick
QtObject {
    property bool dark: false
    property string font: "Inter"
    readonly property color bg: dark ? "#242932" : "#F5F6FA"
    readonly property color surface: dark ? "#303743" : "#FFFFFF"
    readonly property color sidebar: dark ? "#282E38" : "#ECEEF4"
    readonly property color ink: dark ? "#F0F2F7" : "#202331"
    readonly property color muted: dark ? "#A4ADBF" : "#797E90"
    readonly property color faint: dark ? "#7F899C" : "#A4A9B8"
    readonly property color line: dark ? "#414959" : "#EAECF3"
    readonly property color fill: dark ? "#3B4352" : "#F0F2F8"
    readonly property color hover: dark ? "#414B5D" : "#E7EAF4"
    readonly property color accent: dark ? "#A7B8FF" : "#5268DF"
    readonly property color accentFill: dark ? "#3E4866" : "#E9EDFF"
    readonly property color accentInk: dark ? "#212838" : "#FFFFFF"
    readonly property color green: dark ? "#9DCCB1" : "#3E9577"
    readonly property color red: dark ? "#EE9FAD" : "#BC4960"
    readonly property color teal: dark ? "#8FCBCD" : "#61A8A8"
    readonly property color purple: dark ? "#C1ACE8" : "#A68AC9"
    readonly property color amber: dark ? "#D6BD92" : "#C2975E"
}
