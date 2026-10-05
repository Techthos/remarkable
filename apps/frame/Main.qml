import QtQuick
import QtQuick.Window

Window {
    id: root

    property int imageNo: 0
    property bool restoring: false
    property var quickSettings: ({clock: {time: "", date: ""}, items: [], menu: null})
    readonly property real aspect: Number(Qt.application.arguments[1]) || 16 / 9
    readonly property bool flipped: Qt.application.arguments[4] === "1"
    readonly property int gap: 16

    width: Screen.width
    height: Screen.height
    visible: true
    color: "#a0a0a0"

    // Updates the models in place, so unchanged tiles are not rebuilt and redrawn on the e-paper.
    function sync(model, items) {
        items.forEach((item, i) => i < model.count ? model.set(i, item) : model.append(item))
        if (model.count > items.length)
            model.remove(items.length, model.count - items.length)
    }

    function iconSource(key, inverted) {
        return key ? "image://icon/" + key + (inverted ? "/inverted" : "") : ""
    }

    onQuickSettingsChanged: {
        sync(toggles, quickSettings.items.filter(item => item.kind === "toggle"))
        sync(sliders, quickSettings.items.filter(item => item.kind === "slider"))
        sync(buttons, quickSettings.items.filter(item => item.kind === "button"))
        sync(menuItems, quickSettings.menu ? quickSettings.menu.items : [])
    }

    ListModel { id: toggles }
    ListModel { id: sliders }
    ListModel { id: buttons }
    ListModel { id: menuItems }

    component Tile: Rectangle {
        id: tile

        property bool checked
        property string iconKey
        property string title
        property string subtitle
        property bool hasMenu
        signal clicked
        signal menuClicked

        color: checked ? "black" : "white"
        border.width: 3
        radius: 16

        Image {
            id: tileIcon
            x: 16
            anchors.verticalCenter: parent.verticalCenter
            width: tile.iconKey ? 40 : 0
            height: 40
            sourceSize: Qt.size(40, 40)
            source: root.iconSource(tile.iconKey, tile.checked)
        }

        Column {
            anchors.left: tileIcon.right
            anchors.leftMargin: 12
            anchors.right: arrow.left
            anchors.rightMargin: 8
            anchors.verticalCenter: parent.verticalCenter

            Text {
                width: parent.width
                text: tile.title
                font.pixelSize: 24
                font.bold: true
                elide: Text.ElideRight
                color: tile.checked ? "white" : "black"
            }
            Text {
                width: parent.width
                text: tile.subtitle
                visible: text !== ""
                font.pixelSize: 19
                elide: Text.ElideRight
                color: tile.checked ? "white" : "black"
            }
        }

        MouseArea {
            anchors.fill: parent
            onClicked: tile.clicked()
        }

        Item {
            id: arrow
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: tile.hasMenu ? 56 : 0
            visible: tile.hasMenu

            Rectangle {
                width: 3
                height: parent.height - 24
                anchors.verticalCenter: parent.verticalCenter
                color: tile.checked ? "white" : "black"
            }
            Text {
                anchors.centerIn: parent
                text: "›"
                font.pixelSize: 44
                color: tile.checked ? "white" : "black"
            }
            MouseArea {
                anchors.fill: parent
                onClicked: tile.menuClicked()
            }
        }
    }

    // Landscape canvas: the tablet is held with the thick bezel (portrait left) on top,
    // or at the bottom when flipped. The bridge sends menu input in these coordinates.
    Item {
        objectName: "canvas"
        width: parent.height
        height: parent.width
        x: flipped ? parent.width : 0
        y: flipped ? 0 : parent.height
        rotation: flipped ? 90 : -90
        transformOrigin: Item.TopLeft

        Rectangle {
            id: frame
            anchors.bottom: parent.bottom
            width: parent.width
            height: parent.width / aspect
            border.width: 6

            Image {
                anchors.fill: parent
                anchors.margins: parent.border.width
                source: imageNo ? "image://screen/" + imageNo : ""
                cache: false
                smooth: false
            }
        }

        Rectangle {
            id: strip
            width: parent.width
            height: parent.height - frame.height
            color: "white"

            Row {
                anchors.fill: parent
                anchors.margins: gap
                spacing: gap
                visible: !quickSettings.menu

                Column {
                    width: 300
                    spacing: 4

                    Text {
                        text: quickSettings.clock.time
                        font.pixelSize: 64
                        font.bold: true
                    }
                    Text {
                        text: quickSettings.clock.date
                        font.pixelSize: 24
                    }
                    Item { width: 1; height: gap }
                    Row {
                        spacing: 12

                        Repeater {
                            model: buttons

                            Rectangle {
                                width: 64
                                height: 64
                                radius: 32
                                border.width: 3

                                Image {
                                    anchors.centerIn: parent
                                    sourceSize: Qt.size(36, 36)
                                    source: root.iconSource(model.icon, false)
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    onClicked: host.send({type: "click", id: model.id})
                                }
                            }
                        }
                    }
                }

                Column {
                    id: sliderColumn
                    width: 520
                    spacing: gap / 2

                    Repeater {
                        model: sliders

                        Item {
                            id: sliderRow

                            property real dragValue: model.value
                            readonly property real shown: area.pressed ? dragValue : model.value

                            function valueAt(x) {
                                return Math.max(0, Math.min(1, (x - track.x) / track.width))
                            }

                            width: sliderColumn.width
                            height: Math.min(80, (strip.height - 2 * gap) / sliders.count - gap / 2)

                            Image {
                                id: sliderIcon
                                anchors.verticalCenter: parent.verticalCenter
                                width: 48
                                height: 48
                                sourceSize: Qt.size(48, 48)
                                source: root.iconSource(model.icon, false)

                                MouseArea {
                                    anchors.fill: parent
                                    enabled: model.iconReactive
                                    onClicked: host.send({type: "iconClick", id: model.id})
                                }
                            }

                            Rectangle {
                                id: track
                                anchors.left: sliderIcon.right
                                anchors.leftMargin: 24
                                anchors.right: sliderArrow.left
                                anchors.rightMargin: 24
                                anchors.verticalCenter: parent.verticalCenter
                                height: 14
                                radius: 7
                                border.width: 3

                                Rectangle {
                                    width: parent.width * sliderRow.shown
                                    height: parent.height
                                    radius: 7
                                    color: "black"
                                }
                                Rectangle {
                                    x: parent.width * sliderRow.shown - width / 2
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: 36
                                    height: 36
                                    radius: 18
                                    color: "black"
                                }
                            }

                            MouseArea {
                                id: area
                                anchors.left: track.left
                                anchors.right: track.right
                                anchors.leftMargin: -24
                                anchors.rightMargin: -24
                                height: parent.height
                                onPressed: mouse => sliderRow.dragValue = sliderRow.valueAt(mouse.x + x)
                                onPositionChanged: mouse => sliderRow.dragValue = sliderRow.valueAt(mouse.x + x)
                                onReleased: host.send({type: "value", id: model.id, value: sliderRow.dragValue})
                            }

                            // Live preview while dragging, at most a few changes per second.
                            Timer {
                                interval: 200
                                repeat: true
                                running: area.pressed
                                onTriggered: host.send({type: "value", id: model.id, value: sliderRow.dragValue})
                            }

                            Text {
                                id: sliderArrow
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                width: model.menu ? 40 : 0
                                visible: model.menu
                                text: "›"
                                font.pixelSize: 44

                                MouseArea {
                                    anchors.fill: parent
                                    anchors.margins: -12
                                    onClicked: host.send({type: "openMenu", id: model.id})
                                }
                            }
                        }
                    }
                }

                Grid {
                    id: toggleGrid

                    readonly property int cellHeight: 88
                    readonly property int availableRows: Math.max(1, Math.floor((strip.height - 2 * gap + spacing) / (cellHeight + spacing)))
                    readonly property int neededColumns: Math.max(1, Math.ceil((toggles.count + 1) / availableRows))
                    readonly property real cellWidth: (width - (columns - 1) * spacing) / columns

                    width: strip.width - 2 * gap - 300 - 520 - 2 * gap
                    columns: neededColumns
                    spacing: gap / 2

                    Repeater {
                        model: toggles

                        Tile {
                            width: toggleGrid.cellWidth
                            height: toggleGrid.cellHeight
                            checked: model.checked
                            iconKey: model.icon
                            title: model.title
                            subtitle: model.subtitle
                            hasMenu: model.menu
                            onClicked: host.send({type: "click", id: model.id})
                            onMenuClicked: host.send({type: "openMenu", id: model.id})
                        }
                    }

                    Tile {
                        width: toggleGrid.cellWidth
                        height: toggleGrid.cellHeight
                        title: "Close"
                        subtitle: "Back to reMarkable"
                        onClicked: host.send({type: "close"})
                    }
                }
            }

            // An open submenu replaces the strip; it must stay inside the strip because
            // input below it belongs to the PC.
            Row {
                anchors.fill: parent
                anchors.margins: gap
                spacing: gap
                visible: !!quickSettings.menu

                Column {
                    width: 300
                    spacing: gap

                    Text {
                        width: parent.width
                        text: quickSettings.menu ? quickSettings.menu.title : ""
                        font.pixelSize: 32
                        font.bold: true
                        wrapMode: Text.Wrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                    }
                    Tile {
                        width: 200
                        height: 72
                        title: "Close"
                        onClicked: host.send({type: "closeMenu"})
                    }
                }

                Grid {
                    id: menuGrid

                    readonly property int cellHeight: 72
                    readonly property int availableRows: Math.max(1, Math.floor((strip.height - 2 * gap + spacing) / (cellHeight + spacing)))

                    width: strip.width - 2 * gap - 300 - gap
                    columns: Math.max(1, Math.ceil(menuItems.count / availableRows))
                    spacing: gap / 2

                    Repeater {
                        model: menuItems

                        Tile {
                            width: (menuGrid.width - (menuGrid.columns - 1) * menuGrid.spacing) / menuGrid.columns
                            height: menuGrid.cellHeight
                            checked: model.checked
                            iconKey: model.icon
                            title: model.label
                            opacity: model.sensitive ? 1 : 0.4
                            enabled: model.sensitive
                            onClicked: host.send({type: "menuItem", id: model.id})
                        }
                    }
                }
            }
        }
    }

    Image {
        anchors.fill: parent
        z: 1
        visible: restoring
        source: restoring ? "image://restore/xochitl" : ""
        cache: false
        smooth: false
    }
}
