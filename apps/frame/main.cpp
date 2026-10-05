#include <QBuffer>
#include <QFile>
#include <QGuiApplication>
#include <QImage>
#include <QImageReader>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLibrary>
#include <QLibraryInfo>
#include <QPainter>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQuickImageProvider>
#include <QQuickItem>
#include <QQuickPaintedItem>
#include <QQuickWindow>
#include <QSocketNotifier>
#include <QTimer>
#include <QtEndian>
#include <cstdio>
#include <fcntl.h>
#include <linux/input.h>
#include <sys/stat.h>
#include <unistd.h>

static bool usbConnected()
{
    QFile state("/sys/class/udc/ci_hdrc.0/state");
    return state.open(QIODevice::ReadOnly) && state.readAll().trimmed() == "configured";
}

// The dump is the memory mapped after xochitl's /dev/fb0; its RGBA screen buffer
// begins at the first opaque pixel.
static QImage xochitlScreen(const QString &path)
{
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly))
        return {};
    const QByteArray dump = file.readAll();
    const qsizetype size = 1404 * 1872 * 4;
    for (qsizetype i = 0; i + size <= dump.size(); i += 4)
        if (uchar(dump[i + 3]) == 255)
            return QImage(reinterpret_cast<const uchar *>(dump.constData() + i), 1404, 1872, QImage::Format_RGBA8888).copy();
    return {};
}

class ScreenProvider : public QQuickImageProvider
{
public:
    ScreenProvider() : QQuickImageProvider(QQuickImageProvider::Image) {}

    QImage requestImage(const QString &, QSize *size, const QSize &) override
    {
        if (size)
            *size = image.size();
        return image;
    }

    QImage image;
};

// Icons arrive as SVG or PNG bytes; "<key>/inverted" draws them light for dark buttons.
class IconProvider : public QQuickImageProvider
{
public:
    IconProvider() : QQuickImageProvider(QQuickImageProvider::Image) {}

    QImage requestImage(const QString &id, QSize *size, const QSize &requestedSize) override
    {
        const bool inverted = id.endsWith("/inverted");
        QByteArray data = icons.value(id.section('/', 0, 0));
        QBuffer buffer(&data);
        QImageReader reader(&buffer);
        if (requestedSize.isValid())
            reader.setScaledSize(requestedSize);
        QImage image = reader.read();
        if (inverted)
            image.invertPixels();
        if (size)
            *size = image.size();
        return image;
    }

    QHash<QString, QByteArray> icons;
};

class Host : public QObject
{
    Q_OBJECT

public:
    Q_INVOKABLE void send(const QVariantMap &message)
    {
        const QByteArray line = QJsonDocument(QJsonObject::fromVariantMap(message)).toJson(QJsonDocument::Compact) + '\n';
        fwrite(line.constData(), 1, line.size(), stdout);
        fflush(stdout);
    }
};

// The bridge decides which pen and touch input belongs to the menu and sends it as
// pointer messages, so the app's own input from the panels is dropped.
class DropRealInput : public QObject
{
    bool eventFilter(QObject *, QEvent *event) override { return event->spontaneous() && event->isPointerEvent(); }
};

static void point(QQuickWindow *window, QQuickItem *canvas, const QJsonObject &message)
{
    const QString kind = message["pointer"].toString();
    const auto type = kind == "press" ? QEvent::MouseButtonPress
        : kind == "release" ? QEvent::MouseButtonRelease : QEvent::MouseMove;
    const QPointF position = canvas->mapToScene(QPointF(message["x"].toDouble(), message["y"].toDouble()));
    QMouseEvent event(type, position, window->mapToGlobal(position),
                      type == QEvent::MouseMove ? Qt::NoButton : Qt::LeftButton,
                      type == QEvent::MouseButtonRelease ? Qt::NoButton : Qt::LeftButton, Qt::NoModifier);
    QCoreApplication::sendEvent(window, &event);
}

// The e-paper plugin exports the item that sets the waveform for the screen region under it, but registers
// no QML type for it. It is a QQuickItem with one int, the mode, so the app builds one through the plugin's
// constructor and gives it the fast pen waveform, hidden until ink is shown.
static QQuickItem *penWaveform(QQuickItem *area)
{
    QLibrary plugin(QLibraryInfo::path(QLibraryInfo::PluginsPath) + "/scenegraph/qsgepaper");
    const auto construct = reinterpret_cast<void (*)(void *, QQuickItem *)>(
        plugin.resolve("_ZN16EPScreenModeItemC1EP10QQuickItem"));
    if (!construct)
        return nullptr;
    auto *item = static_cast<QQuickItem *>(::operator new(sizeof(QQuickItem) + sizeof(int)));
    construct(item, area);
    item->setSize(area->size());
    item->setProperty("mode", "Pen");
    item->setVisible(false);
    return item;
}

class InkTile : public QQuickPaintedItem
{
public:
    static constexpr int size = 64;

    InkTile(QQuickItem *pad, QPoint cell) : QQuickPaintedItem(pad), image(size, size, QImage::Format_ARGB32_Premultiplied)
    {
        image.fill(Qt::transparent);
        setPosition(cell * size);
        setSize(image.size());
    }

    void paint(QPainter *painter) override { painter->drawImage(0, 0, image); }

    QImage image;
};

// Ink on the pad, painted into tiles that are created where the pen draws. Only the touched tiles redraw,
// so a stroke costs the same however much ink is already on screen.
struct Ink
{
    QQuickItem *pad;
    QHash<QPoint, InkTile *> tiles;

    void line(QPointF from, QPointF to)
    {
        const QRect bounds = QRectF(from, to).normalized().adjusted(-2, -2, 2, 2).toAlignedRect();
        for (int y = bounds.top() / InkTile::size; y <= bounds.bottom() / InkTile::size; ++y) {
            for (int x = bounds.left() / InkTile::size; x <= bounds.right() / InkTile::size; ++x) {
                InkTile *&tile = tiles[QPoint(x, y)];
                if (!tile)
                    tile = new InkTile(pad, QPoint(x, y));
                QPainter painter(&tile->image);
                painter.translate(-tile->position());
                painter.setPen(QPen(Qt::black, 4, Qt::SolidLine, Qt::RoundCap));
                painter.drawLine(from, to);
                tile->update();
            }
        }
    }

    void clear()
    {
        qDeleteAll(tiles);
        tiles.clear();
    }
};

// Pen digitizer units to scene pixels: its X runs up the portrait screen, its Y to the right.
static QPointF penToScene(const QPointF &pen, const QQuickWindow *window)
{
    return {pen.y() / 15725 * window->width(), (1 - pen.x() / 20966) * window->height()};
}

// Arguments: layout as JSON (see the bridge), image width, image height, xochitl screen dump, pen event FIFO
// that evgrab copies the pen to while ink is on. Stdin carries framed
// messages: a type byte ('I' raw GRAY8 image of that size, 'M' JSON), a little endian
// uint32 length, the payload. Stdout carries JSON lines with the actions tapped in the menu.
// The app quits when stdin closes or the USB cable is unplugged, after drawing the screen
// xochitl had, so the thawed xochitl finds the e-paper as it left it.
int main(int argc, char *argv[])
{
    QGuiApplication app(argc, argv);
    const int width = app.arguments().value(2).toInt();
    const int height = app.arguments().value(3).toInt();

    DropRealInput dropRealInput;
    app.installEventFilter(&dropRealInput);

    QQmlApplicationEngine engine;
    auto *screen = new ScreenProvider;
    auto *restore = new ScreenProvider;
    auto *icons = new IconProvider;
    engine.addImageProvider("screen", screen);
    engine.addImageProvider("restore", restore);
    engine.addImageProvider("icon", icons);
    Host host;
    engine.rootContext()->setContextProperty("host", &host);

    QObject::connect(&engine, &QQmlApplicationEngine::objectCreationFailed,
                     &app, []() { QCoreApplication::exit(-1); }, Qt::QueuedConnection);
    engine.loadFromModule("app", "Main");
    if (engine.rootObjects().isEmpty())
        return -1;
    auto *window = qobject_cast<QQuickWindow *>(engine.rootObjects().constFirst());
    auto *canvas = window->findChild<QQuickItem *>("canvas");

    auto handle = [&](const QJsonObject &message) {
        if (message.contains("pointer")) {
            point(window, canvas, message);
        } else if (message.contains("icons")) {
            const QJsonObject data = message["icons"].toObject();
            for (auto it = data.begin(); it != data.end(); ++it)
                icons->icons[it.key()] = QByteArray::fromBase64(it.value().toString().toLatin1());
        } else if (message.contains("state")) {
            window->setProperty("quickSettings", message["state"].toVariant());
        }
    };

    QSocketNotifier stdinWatch(STDIN_FILENO, QSocketNotifier::Read);
    QTimer usbWatch;
    auto finish = [&] {
        stdinWatch.setEnabled(false);
        usbWatch.stop();
        restore->image = xochitlScreen(app.arguments().value(4));
        if (restore->image.isNull()) {
            QCoreApplication::quit();
            return;
        }
        window->setProperty("restoring", true);
        QObject::connect(window, &QQuickWindow::frameSwapped, &app, &QCoreApplication::quit, Qt::QueuedConnection);
        QTimer::singleShot(5000, &app, &QCoreApplication::quit);
    };

    int imageNo = 0;
    QByteArray pending;
    QObject::connect(&stdinWatch, &QSocketNotifier::activated, [&] {
        char buf[65536];
        const ssize_t n = read(STDIN_FILENO, buf, sizeof buf);
        if (n <= 0) {
            finish();
            return;
        }
        pending.append(buf, n);
        QByteArray image;
        qsizetype offset = 0;
        while (pending.size() - offset >= 5) {
            const qsizetype length = qFromLittleEndian<quint32>(pending.constData() + offset + 1);
            if (pending.size() - offset - 5 < length)
                break;
            const QByteArray payload = pending.mid(offset + 5, length);
            if (pending[offset] == 'I')
                image = payload;
            else
                handle(QJsonDocument::fromJson(payload).object());
            offset += 5 + length;
        }
        pending.remove(0, offset);
        if (image.size() == qsizetype(width) * height) {
            screen->image = QImage(reinterpret_cast<const uchar *>(image.constData()), width, height, width,
                                   QImage::Format_Grayscale8).copy();
            window->setProperty("imageNo", ++imageNo);
        }
    });

    // The ink is drawn from the pen events right here, without the round trip through the PC. It clears
    // once the pen has been out of range for the ink delay; coming back into range stops the count.
    Ink ink{window->findChild<QQuickItem *>("ink")};
    QQuickItem *waveform = penWaveform(ink.pad);
    QTimer inkClear;
    inkClear.setSingleShot(true);
    inkClear.setInterval(QJsonDocument::fromJson(app.arguments().value(1).toUtf8())["inkDelay"].toDouble() * 1000);
    auto showInk = [&](bool shown) {
        if (waveform)
            waveform->setVisible(shown);
        window->setProperty("inkShown", shown);
    };
    QObject::connect(&inkClear, &QTimer::timeout, [&] {
        ink.clear();
        showInk(false);
    });

    // The FIFO is opened for writing too, so it never reports end of file while evgrab has not opened it yet.
    const QByteArray penCopy = app.arguments().value(5).toLocal8Bit();
    mkfifo(penCopy, 0600);
    QSocketNotifier penWatch(open(penCopy, O_RDWR | O_NONBLOCK), QSocketNotifier::Read);
    QPointF pen, last;
    bool touching = false, stroking = false;
    QObject::connect(&penWatch, &QSocketNotifier::activated, [&] {
        input_event events[64];
        const ssize_t n = read(penWatch.socket(), events, sizeof events);
        for (ssize_t i = 0; i < n / ssize_t(sizeof *events); ++i) {
            const input_event &e = events[i];
            if (e.type == EV_ABS && e.code == ABS_X) {
                pen.setX(e.value);
            } else if (e.type == EV_ABS && e.code == ABS_Y) {
                pen.setY(e.value);
            } else if (e.type == EV_KEY && e.code == BTN_TOUCH) {
                touching = e.value;
            } else if (e.type == EV_KEY && (e.code == BTN_TOOL_PEN || e.code == BTN_TOOL_RUBBER)) {
                // Out of range is never touching, even if the copy of the lift was dropped.
                touching = touching && e.value;
                if (e.value)
                    inkClear.stop();
                else
                    inkClear.start();
            } else if (e.type == EV_SYN && e.code == SYN_REPORT) {
                const QPointF point = ink.pad->mapFromScene(penToScene(pen, window));
                const bool drawing = touching && ink.pad->contains(point);
                if (drawing) {
                    ink.line(stroking ? last : point, point);
                    showInk(true);
                }
                stroking = drawing;
                last = point;
            }
        }
    });

    QObject::connect(&usbWatch, &QTimer::timeout, [&] {
        if (!usbConnected())
            finish();
    });
    usbWatch.start(2000);

    host.send({{"type", "ready"}});
    return app.exec();
}

#include "main.moc"
