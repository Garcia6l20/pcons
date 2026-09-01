#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""One Qt Quick application, two Android ABIs, one APK.

examples/77_qt_android_apk packages the same application for one ABI. This
one adds x86_64 beside arm64-v8a, which is what shipping actually needs: a
package that installs on a phone and on an emulator.

What changes, and nothing else does:

1. One environment per ABI, each with its own ``android()`` preset and its
   own Qt built for that ABI. Both need a ``name``, which is what lets them
   hold a target called "qtapp", and a ``build_prefix``, which is what keeps
   their outputs apart.
2. ``android_deployment_settings()`` takes the mapping of ABI to
   environment and the ABI that is primary. Seven keys become maps of ABI
   to value; ``abi``, ``extraPrefixDirs`` and the host tools name the
   primary alone, and ``application-binary`` stays one plain name because
   androiddeployqt appends the ABI suffix itself.
3. Every ABI stages into the *same* output directory, the primary's:
   androiddeployqt owns one directory and reads one ``libs/<abi>/`` per ABI
   out of it. ``android_apk()`` takes the list and waits for all of them.

    PCONS_ANDROID_GRADLE=1 pcons     # the real package, Gradle and all

Without that variable the androiddeployqt edge runs with ``--no-build``,
which is what CI runs: it parses the settings, reads both staged libraries
and stops.

Needs an Android NDK and SDK, named by ANDROID_NDK_HOME (or
ANDROID_NDK_ROOT) and ANDROID_HOME (or ANDROID_SDK_ROOT), and **two** Qt
installs built for Android, named by PCONS_QT_ANDROID_ROOT and
PCONS_QT_ANDROID_ROOT_X86_64. A real Gradle run also needs network for the
first build and a JDK the Android Gradle plugin supports:
JAVA_HOME=/usr/lib/jvm/java-21-openjdk on a machine whose default is newer.
"""

import os
from pathlib import Path

from pcons import Project
from pcons.core.environment import Environment
from pcons.core.target import Target
from pcons.toolchains.presets import android
from pcons.toolchains.qt import find_qt
from pcons.toolchains.qt.android import android_deployment_settings, android_output_dir
from pcons.toolchains.qt.apk import android_apk, stage_application_library

PRIMARY = "arm64-v8a"
API = 28
PACKAGE = "org.pcons.qtmultiabi"


def _from_env(*names: str) -> Path:
    for name in names:
        value = os.environ.get(name)
        if value:
            return Path(value)
    raise SystemExit(f"Set one of {', '.join(names)} to build this example.")


def _newest(directory: Path) -> str:
    revisions = sorted(
        (p for p in directory.iterdir() if p.is_dir()),
        key=lambda p: tuple(
            int(part) if part.isdigit() else 0 for part in p.name.split(".")
        ),
    )
    if not revisions:
        raise SystemExit(f"Nothing installed under {directory}.")
    return revisions[-1].name


ndk = _from_env("ANDROID_NDK_HOME", "ANDROID_NDK_ROOT")
sdk = _from_env("ANDROID_HOME", "ANDROID_SDK_ROOT")
qt_roots = {
    PRIMARY: _from_env("PCONS_QT_ANDROID_ROOT"),
    "x86_64": _from_env("PCONS_QT_ANDROID_ROOT_X86_64"),
}

project = Project("qt_android_multi_abi")

envs: dict[str, Environment] = {}
apps: dict[str, Target] = {}
for abi, qt_root in qt_roots.items():
    env = project.Environment(toolchain="llvm", name=f"android-{abi}")
    env.build_prefix = f"android-{abi}"
    env.apply_cross_preset(android(ndk=str(ndk), arch=abi, api=API, sdk=str(sdk)))
    env.cxx.set_standard(17)

    qt = find_qt(project, env, modules=["Quick"], qt_root=qt_root, probe="qtpaths")

    ui = project.QtQmlModule(
        f"demo_ui_{abi}",
        env,
        uri="PconsMultiAbiDemo",
        qml_files=["qml/Main.qml"],
        link=[qt.Quick],
    )
    app = project.QtSharedLibrary(
        "qtapp", env, sources=["src/main.cpp"], link=[qt.Quick]
    )
    app.link(ui)

    envs[abi] = env
    apps[abi] = app

settings = android_deployment_settings(
    project,
    envs,
    app="qtapp",
    primary=PRIMARY,
    package_name=PACKAGE,
    package_source_dir="android",
    build_tools=_newest(sdk / "build-tools"),
)

package = android_output_dir(envs[PRIMARY], "qtapp")
staged = [
    stage_application_library(project, envs[abi], app=app, output=package)
    for abi, app in apps.items()
]

apk = android_apk(
    project,
    envs[PRIMARY],
    app="qtapp",
    settings=settings,
    staged=staged,
    no_build=os.environ.get("PCONS_ANDROID_GRADLE") != "1",
)

project.Default(*apps.values(), apk)
