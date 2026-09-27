// Confirmed text enters the currently focused native Qt object exactly once.
// The private broker owns process/surface admission and input ordering.
#include <qpa/qplatforminputcontextplugin_p.h>
#include <qpa/qplatforminputcontext.h>
#include <qpa/qplatformnativeinterface.h>
#include <qpa/qwindowsysteminterface.h>
#include <QDBusConnection>
#include <QDBusInterface>
#include <QDBusMessage>
#include <QEventLoop>
#include <QGuiApplication>
#include <QInputMethodEvent>
#include <QKeyEvent>
#include <QPointer>
#include <QTimer>
#include <QWindow>
#include <wayland-client-core.h>

class FloeNativeContext : public QPlatformInputContext {
    Q_OBJECT
    QDBusInterface broker{qEnvironmentVariable("FLOE_NATIVE_DESKTOP_INPUT", "org.floegence.DesktopInput"),
        QStringLiteral("/org/floegence/DesktopInput"), QStringLiteral("org.floegence.DesktopInput"),
        QDBusConnection::sessionBus()};
    bool registered = false;

    static uint surfaceId(QWindow *window) {
        auto native = QGuiApplication::platformNativeInterface();
        const auto platform = QGuiApplication::platformName();
        if (!window || !native)
            return 0;
        if (platform == QStringLiteral("xcb"))
            return uint(window->winId());
        if (platform != QStringLiteral("wayland") && platform != QStringLiteral("wayland-egl"))
            return 0;
        auto surface = static_cast<wl_proxy *>(native->nativeResourceForWindow(QByteArrayLiteral("surface"), window));
        return surface ? wl_proxy_get_id(surface) : 0;
    }
public:
    FloeNativeContext() {
        const QString toolkit = QT_VERSION_MAJOR == 6 ? QStringLiteral("qt6-native") : QStringLiteral("qt5-native");
        registered = broker.call(QDBus::Block, QStringLiteral("Register"), uint(1), toolkit).type() == QDBusMessage::ReplyMessage;
    }

    bool isValid() const override { return registered; }

    bool filterEvent(const QEvent *event) override {
        if (event->type() != QEvent::KeyPress && event->type() != QEvent::KeyRelease)
            return false;
        const auto key = static_cast<const QKeyEvent *>(event);
        const uint code = key->nativeScanCode() - 8;
        const bool x11 = QGuiApplication::platformName() == QStringLiteral("xcb");
        if (x11 ? code != 0 : code < 2048 || code >= 2080)
            return false;
        if (event->type() == QEvent::KeyRelease) {
            broker.asyncCall(QStringLiteral("Released"), code);
            return true;
        }
        QWindowSystemInterface::sendWindowSystemEvents(QEventLoop::AllEvents);
        QPointer<QObject> target = QGuiApplication::focusObject();
        QPointer<QWindow> window = QGuiApplication::focusWindow();
        const uint surface = surfaceId(window);
        if (!target || !surface || !inputMethodAccepted()) {
            // Retire this exact press without claiming an editable surface.
            // The matching release can then free its bounded marker slot.
            broker.asyncCall(QStringLiteral("Take"), code, uint(0));
            return true;
        }
        const auto reply = broker.call(QDBus::Block, QStringLiteral("Take"), code, surface);
        if (reply.type() != QDBusMessage::ReplyMessage || reply.arguments().size() != 2)
            return true;
        const uint sequence = reply.arguments()[0].toUInt();
        if (!target || target != QGuiApplication::focusObject() || !window ||
                window != QGuiApplication::focusWindow() || surface != surfaceId(window)) {
            broker.asyncCall(QStringLiteral("Failed"), sequence);
            return true;
        }
        QInputMethodEvent commit;
        commit.setCommitString(reply.arguments()[1].toString());
        QCoreApplication::sendEvent(target, &commit);
        QTimer::singleShot(0, this, [this, sequence] { broker.asyncCall(QStringLiteral("Done"), sequence); });
        return true;
    }
};

class FloeNativePlugin : public QPlatformInputContextPlugin {
    Q_OBJECT
    Q_PLUGIN_METADATA(IID QPlatformInputContextFactoryInterface_iid FILE "qt_native.json")
public:
    QPlatformInputContext *create(const QString &key, const QStringList &) override {
        return key == QStringLiteral("floe-client-native") ? new FloeNativeContext : nullptr;
    }
};
#include "qt_native.moc"
