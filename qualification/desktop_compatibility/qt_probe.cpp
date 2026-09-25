// Unpublished Wayland Qt input feasibility fixture. No support claim or ABI
// compatibility is inferred from compilation or D-Bus transport completion.
#include <qpa/qplatforminputcontextplugin_p.h>
#include <qpa/qplatforminputcontext.h>
#include <qpa/qwindowsysteminterface.h>
#include <QGuiApplication>
#include <QInputMethodEvent>
#include <QKeyEvent>
#include <QDBusConnection>
#include <QDBusInterface>
#include <QDBusMessage>
#include <QEventLoop>
#include <QPointer>
#include <QTimer>
#include <QWindow>

class FixtureContext : public QPlatformInputContext {
    Q_OBJECT
    QDBusInterface broker{qEnvironmentVariable("FLOE_PROBE_NATIVE_SERVICE"),
        "/org/floegence/ClientInput", "org.floegence.ClientInput",
        QDBusConnection::sessionBus()};
    bool registered = false;
public:
    FixtureContext() {
        registered = broker.call(QDBus::Block, "Register").type() == QDBusMessage::ReplyMessage;
    }
    bool isValid() const override { return registered; }
    bool filterEvent(const QEvent *event) override {
        if (event->type() != QEvent::KeyPress && event->type() != QEvent::KeyRelease)
            return false;
        const auto key = static_cast<const QKeyEvent *>(event);
        const uint code = key->nativeScanCode() - 8;
        if (code < 2048 || code >= 2080)
            return false;
        if (event->type() == QEvent::KeyRelease) {
            broker.asyncCall("Released", code);
            return true;
        }
        // Same event-queue boundary as the existing released X11 module.
        QWindowSystemInterface::sendWindowSystemEvents(QEventLoop::AllEvents);
        QPointer<QObject> target = QGuiApplication::focusObject();
        if (!target || !QGuiApplication::focusWindow() || !inputMethodAccepted())
            return true;
        const auto reply = broker.call(QDBus::Block, "Take", code);
        if (reply.type() != QDBusMessage::ReplyMessage || reply.arguments().size() != 2)
            return true;
        const uint sequence = reply.arguments()[0].toUInt();
        QInputMethodEvent commit;
        commit.setCommitString(reply.arguments()[1].toString());
        QCoreApplication::sendEvent(target, &commit);
        QTimer::singleShot(0, this, [this, sequence] { broker.asyncCall("Done", sequence); });
        return true;
    }
};

class FixturePlugin : public QPlatformInputContextPlugin {
    Q_OBJECT
    Q_PLUGIN_METADATA(IID QPlatformInputContextFactoryInterface_iid FILE "qt_probe.json")
public:
    QPlatformInputContext *create(const QString &key, const QStringList &) override {
        return key == QStringLiteral("floe-prototype") ? new FixtureContext : nullptr;
    }
};
#include "qt_probe.moc"
