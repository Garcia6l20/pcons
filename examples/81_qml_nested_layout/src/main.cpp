// SPDX-License-Identifier: MIT
#include <QCoreApplication>
#include <QQmlComponent>
#include <QQmlEngine>
#include <QUrl>

#include <cstdio>

int main(int argc, char *argv[]) {
    qInstallMessageHandler(
        [](QtMsgType, const QMessageLogContext &, const QString &msg) {
            std::printf("%s\n", qPrintable(msg));
            std::fflush(stdout);
        });

    QCoreApplication app(argc, argv);
    QQmlEngine engine;
    engine.addImportPath(QStringLiteral(":/qt/qml"));

    QQmlComponent root(&engine, QStringLiteral("PconsNested"),
                       QStringLiteral("Main"));
    QScopedPointer<QObject> loaded(root.create());
    if (loaded.isNull()) {
        std::printf("%s\n", qPrintable(root.errorString()));
        return 1;
    }

    const QUrl url(
        QStringLiteral("qrc:/qt/qml/PconsNested/qml/pages/Detail.qml"));
    QQmlComponent detail(&engine, url);
    QScopedPointer<QObject> instance(detail.create());
    if (instance.isNull()) {
        std::printf("%s\n", qPrintable(detail.errorString()));
        return 1;
    }
    std::printf("url %s is %s\n", qPrintable(url.toString()),
                qPrintable(instance->property("label").toString()));
    return 0;
}
