// Native baseline application. Receipts observe real widgets; no text is injected.
#include <QApplication>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSaveFile>
#include <QTextEdit>
#include <QWidget>
#include <gnu/libc-version.h>

static void writeReceipt(const QString &path, const QJsonDocument &document) {
    QSaveFile file(path);
    const auto bytes = document.toJson(QJsonDocument::Compact);
    if (!file.open(QIODevice::WriteOnly) || file.write(bytes) != bytes.size() || !file.commit())
        qFatal("Could not record native qualification receipt");
}

int main(int argc, char **argv) {
    QApplication app(argc, argv);
    if (app.arguments().size() != 2)
        return 64;
    const auto receipt = app.arguments()[1];
    QWidget window;
    window.setWindowTitle(QStringLiteral("Floe native Qt baseline qualification"));
    window.resize(640, 320);
    window.setStyleSheet(QStringLiteral("QTextEdit { background-color: #3b759f; }"));
    QHBoxLayout layout(&window);
    QTextEdit first, second;
    layout.addWidget(&first);
    layout.addWidget(&second);
    auto save = [&] {
        writeReceipt(receipt, QJsonDocument(QJsonArray{first.toPlainText(), second.toPlainText()}));
    };
    QObject::connect(&first, &QTextEdit::textChanged, &window, save);
    QObject::connect(&second, &QTextEdit::textChanged, &window, save);
    window.show();
    first.setFocus();
    window.activateWindow();
    save();
    writeReceipt(receipt + QStringLiteral(".runtime.json"), QJsonDocument(QJsonObject{
        {QStringLiteral("qt"), QString::fromLatin1(qVersion())},
        {QStringLiteral("glibc"), QString::fromLatin1(gnu_get_libc_version())},
        {QStringLiteral("pid"), qint64(QCoreApplication::applicationPid())},
        {QStringLiteral("platform"), QGuiApplication::platformName()}}));
    return app.exec();
}
