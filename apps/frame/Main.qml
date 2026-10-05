import QtQuick
import QtQuick.Window

Window {
    id: root

    property int imageNo: 0
    property int shownImageNo: 0
    property bool inkShown: false
    property bool restoring: false
    property var quickSettings: ({clock: {time: "", date: ""}, items: [], menu: null})
    readonly property var setup: JSON.parse(Qt.application.arguments[1])
    readonly property string layout: setup.layout
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

    // A new screen image waits while ink is shown, so it is not drawn with the pen waveform.
    onImageNoChanged: if (!inkShown) shownImageNo = imageNo
    onInkShownChanged: if (!inkShown) shownImageNo = imageNo

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

    // The screen as the tablet is held: landscape with the thick bezel on top, portrait with it on the
    // left, both turned around when flipped. The bridge places the pad and sends menu input in these
    // coordinates.
    Rectangle {
        id: canvas
        objectName: "canvas"
        anchors.centerIn: parent
        width: setup.portrait ? parent.width : parent.height
        height: setup.portrait ? parent.height : parent.width
        rotation: (setup.portrait ? 0 : -90) + (setup.flipped ? 180 : 0)

        Rectangle {
            id: frame
            x: setup.pad[0]
            y: setup.pad[1]
            width: setup.pad[2]
            height: setup.pad[3]
            visible: width > 0
            border.width: 6

            Image {
                anchors.fill: parent
                anchors.margins: parent.border.width
                source: shownImageNo ? "image://screen/" + shownImageNo : ""
                cache: false
                smooth: false
            }
        }

        // main.cpp draws the ink in here.
        Item {
            objectName: "ink"
            anchors.fill: frame
        }

        // The menu takes the band above or below the pad. Remote has no pad and draws the menu larger.
        Item {
            id: panel

            readonly property real zoom: layout === "remote" ? 1.5 : 1

            y: setup.pad[1] > 0 ? 0 : setup.pad[3]
            width: canvas.width / zoom
            height: (canvas.height - setup.pad[3]) / zoom
            scale: zoom
            transformOrigin: Item.TopLeft

            Item {
                id: controls

                // Beside the sliders in a strip, under them in a tall menu, in the column beside the pad,
                // or none at all, which leaves only Close.
                readonly property string toggleSpot: ({
                    landscape: "beside", below: "beside", portrait: "under", remote: "under", sidebar: "side", minimal: "none"
                })[layout]

                anchors.fill: parent
                anchors.margins: gap
                visible: !quickSettings.menu

                Column {
                    id: clock
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
                        visible: controls.toggleSpot !== "none"

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
                    x: clock.width + gap
                    width: controls.toggleSpot === "beside" ? 520 : controls.width - x
                    spacing: gap / 2
                    visible: controls.toggleSpot !== "none"

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
                            height: Math.min(80, controls.height / sliders.count - gap / 2)

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

                    readonly property real underY: Math.max(clock.height, sliderColumn.height) + gap
                    readonly property rect area: ({
                        beside: Qt.rect(sliderColumn.x + sliderColumn.width + gap, 0,
                                        controls.width - sliderColumn.x - sliderColumn.width - gap, controls.height),
                        under: Qt.rect(0, underY, controls.width, controls.height - underY),
                        side: Qt.rect(0, setup.pad[1], setup.pad[0] - 2 * gap, setup.pad[3] - 2 * gap),
                        none: Qt.rect(controls.width - 300, 0, 300, controls.height),
                    })[controls.toggleSpot]
                    readonly property int cellHeight: 88
                    readonly property int availableRows: Math.max(1, Math.floor((area.height + spacing) / (cellHeight + spacing)))
                    readonly property int neededColumns: Math.max(1, Math.ceil((toggleTiles.count + 1) / availableRows),
                                                                  Math.floor((area.width + spacing) / (400 + spacing)))
                    readonly property real cellWidth: (width - (columns - 1) * spacing) / columns

                    x: area.x
                    y: area.y
                    width: area.width
                    columns: neededColumns
                    spacing: gap / 2

                    Repeater {
                        id: toggleTiles
                        model: controls.toggleSpot === "none" ? 0 : toggles

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

            // An open submenu replaces the menu; it must stay inside it because
            // input on the pad belongs to the PC.
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
                    readonly property int availableRows: Math.max(1, Math.floor((panel.height - 2 * gap + spacing) / (cellHeight + spacing)))

                    width: panel.width - 2 * gap - 300 - gap
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
