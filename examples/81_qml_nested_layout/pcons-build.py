# SPDX-License-Identifier: MIT
"""A QML module whose files sit in subdirectories.

Each qml_files entry is also the file's path inside the module resource,
the way qt_add_qml_module does it: "qml/pages/Detail.qml" is reachable at
qrc:/qt/qml/PconsNested/qml/pages/Detail.qml, and the qmldir names that
same path. Two files with one base name in different directories are a
hard error rather than one silently replacing the other.
"""

from pcons import Project, find_c_toolchain
from pcons.toolchains.qt import find_qt

project = Project("qml_nested")
env = project.Environment(toolchain=find_c_toolchain())
env.cxx.set_standard(17)

qt = find_qt(project, env, modules=["Qml"])

ui = project.QtQmlModule(
    "nested_ui",
    env,
    uri="PconsNested",
    qml_files=[
        "qml/Main.qml",
        "qml/pages/Detail.qml",
        "qml/widgets/Badge.qml",
    ],
    link=[qt.Qml],
)

app = project.QtProgram("qml_nested", env, sources=["src/main.cpp"], link=[qt.Qml])
app.link(ui)
