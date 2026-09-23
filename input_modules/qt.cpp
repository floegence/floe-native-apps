// Commit-only Qt input module. The private X11 event carries the operation ID.
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

class FloeContext : public QPlatformInputContext {
    Q_OBJECT
    QDBusInterface broker{"org.floegence.ClientInput", "/org/floegence/ClientInput",
                          "org.floegence.ClientInput", QDBusConnection::sessionBus()};
    bool registered = false;
public:
    FloeContext() {
        const auto reply = broker.call(QDBus::Block, "Register", uint(1),
            QT_VERSION_MAJOR == 5 ? QStringLiteral("qt5") : QStringLiteral("qt6"));
        registered = reply.type() == QDBusMessage::ReplyMessage;
    }
    bool isValid() const override { return registered; }
    bool filterEvent(const QEvent *event) override {
        if (event->type() != QEvent::KeyPress && event->type() != QEvent::KeyRelease)
            return false;
        const auto key = static_cast<const QKeyEvent *>(event);
        if (key->nativeScanCode() != 8)
            return false;
        if (event->type() == QEvent::KeyRelease)
            return true;
        // Qt queues pointer and ordinary key events separately from its native
        // IM filter. Deliver those earlier events before resolving the target.
        QWindowSystemInterface::sendWindowSystemEvents(QEventLoop::AllEvents);
        QPointer<QObject> target = QGuiApplication::focusObject();
        const auto window = QGuiApplication::focusWindow();
        if (!target || !window || !inputMethodAccepted())
            return true;
        const auto reply = broker.call(QDBus::Block, "Take", uint(key->timestamp()), uint(window->winId()));
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

class FloePlugin : public QPlatformInputContextPlugin {
    Q_OBJECT
    Q_PLUGIN_METADATA(IID QPlatformInputContextFactoryInterface_iid FILE "qt.json")
public:
    QPlatformInputContext *create(const QString &key, const QStringList &) override {
        return key == QStringLiteral("floe-client") ? new FloeContext : nullptr;
    }
};
#include <qt.moc>
