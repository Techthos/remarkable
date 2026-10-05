import QtQuick
import QtQuick.Window

Window {
    width: Screen.width
    height: Screen.height
    visible: true

    Text {
        anchors.centerIn: parent
        horizontalAlignment: Text.AlignHCenter
        font.pixelSize: 96
        text: "Hello World<br><br><small>tap to exit</small>"
        textFormat: Text.StyledText
    }

    MouseArea {
        anchors.fill: parent
        onPressed: Qt.quit()
    }
}
