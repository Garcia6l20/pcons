// SPDX-License-Identifier: MIT
#include <QGuiApplication>
#include <QJniObject>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QString>

int main(int argc, char *argv[])
{
    QGuiApplication app(argc, argv);

    const QJniObject greeting = QJniObject::callStaticObjectMethod(
        "org/pcons/qtmultiabi/AppInfo", "describe", "()Ljava/lang/String;");

    QQmlApplicationEngine engine;
    engine.rootContext()->setContextProperty("javaGreeting", greeting.toString());
    engine.loadFromModule("PconsMultiAbiDemo", "Main");
    if (engine.rootObjects().isEmpty())
        return 1;
    return app.exec();
}
