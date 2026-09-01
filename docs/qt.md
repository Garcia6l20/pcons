# Qt

pcons has first-class Qt 6 support: discovery of Qt modules and tools,
automatic moc/uic/rcc, and high-level builders that make a Qt Widgets
application a five-line build script.

```python
from pcons import Project, find_c_toolchain
from pcons.toolchains.qt import find_qt

project = Project("myapp")
env = project.Environment(toolchain=find_c_toolchain())
env.cxx.set_standard(17)

qt = find_qt(project, env, modules=["Widgets", "Network"])

app = project.QtProgram(
    "myapp",
    env,
    sources=["main.cpp", "mainwindow.cpp", "mainwindow.ui", "icons.qrc"],
    link=[qt.Widgets, qt.Network],
)
```

That's the whole build. `.ui` and `.qrc` files go straight into
`sources`; classes with `Q_OBJECT` are found automatically; all platform
quirks (macOS frameworks, MSVC's `/Zc:__cplusplus /permissive-`, Windows
debug `d`-suffix libraries) are handled by `find_qt`.

## How it's better than CMake's AUTOMOC

pcons deliberately fixes the well-known pain points of CMake + Qt:

| CMake + Qt | pcons |
|---|---|
| Opaque `<target>_autogen` step; mystery rebuilds | Every moc/uic/rcc run is a plain, visible ninja edge (`ninja -t commands`) |
| `mocs_compilation.cpp` aggregate: touching one moc'ed header recompiles all moc output | Each `moc_*.cpp` is its own translation unit |
| Build-time source scanning on every build | Scan happens once, when pcons generates; builds run zero scanning |
| Silent no-op when a `.cpp` has `Q_OBJECT` but no `#include "foo.moc"` | Hard error at generate time, with the exact line to add |
| Adding `Q_OBJECT` without re-running CMake → undefined-vtable link errors | A cheap guard edge fails the build with *"re-run pcons"* naming the file |

Incremental correctness comes from the tools' own depfiles: moc runs with
`--output-dep-file` (re-runs when any transitively-included header
changes), rcc with `--depfile` (re-runs when a file listed in the .qrc
changes), and uic is a pure `input → output` rule.

## Discovery: find_qt()

```python
qt = find_qt(
    project,
    env,
    modules=["Widgets"],  # short names; Core is always included
    version=">=6.4",  # optional constraint
    qt_root="/opt/Qt/6.7.0/gcc_64",  # optional; also $PCONS_QT_ROOT
    probe="auto",  # "auto" | "pkg-config" | "qtpaths"
    private_headers=["Core"],  # opt-in to QtCore/x.y.z/private
)

qt.version  # "6.9.3"
qt.Widgets  # ImportedTarget — use in link=[...] or app.link(...)
qt.tool_path("lupdate")
```

Probing order:

1. **pkg-config** (`Qt6Core.pc`, `Qt6Widgets.pc`, ...) — present on Linux
   distributions and Homebrew macOS; handles framework linking.
2. **qtpaths/qmake introspection** (`qtpaths6 -query`) — for installs
   without pkg-config files, e.g. the official Qt installer and Windows.
   This route resolves inter-module dependencies from a built-in table, so
   asking for `Qml` also brings in `Network` and `QmlIntegration`. The
   latter is header-only: it contributes an include directory, which every
   `QML_ELEMENT` header needs, and links nothing.

### Choosing the probe

`probe=` runs one of them instead of both. `"auto"` is the default and the
order above; `"pkg-config"` and `"qtpaths"` run that probe alone and fail
if it finds nothing.

A cross Qt is the reason this exists. Such an install has two halves: the
libraries and headers you link against, built for the target, and the
moc/uic/rcc binaries, built for the machine running the build. pkg-config
cannot describe that split. Its `libexecdir` is `${prefix}/bin`, which for
a Windows target Qt holds Windows executables, so `qt.tool_path("moc")`
comes back `None` and nothing explains why. `qtpaths6 -query` on the same
install reports `QT_HOST_BINS` and `QT_HOST_LIBEXECS`, which is where the
runnable tools are, and the qtpaths probe prefers them.

So when the target Qt ships complete `.pc` files, pkg-config wins the
`"auto"` race with the wrong answer, and the way out is to ask for the
other probe:

```python
host_qt = find_qt(project, host, modules=["Widgets"])
cross_qt = find_qt(project, cross, modules=["Widgets"],
                   qt_root="/opt/qt6-mingw", probe="qtpaths")
```

`qt_root` alone does not do this: it *scopes* the pkg-config search to
that prefix rather than skipping it, so pointing it at the cross Qt only
makes pkg-config answer with more confidence.

`qt_module_available()` is unaffected: it asks whether a module is
installed at all, not which install a target builds against, so it always
tries both probes and takes no `probe` argument.

Passing `env` adds the `qt` toolchain to the environment (tool paths for
moc/uic/rcc), enabling the builders below. Discovery is cached per project
and per environment name; call `find_qt` again with the same environment to
add modules, and once per environment to build for two of them:

```python
host_qt = find_qt(project, host, modules=["Widgets"])
mcu_qt = find_qt(project, mcu, modules=["Core"])
```

Each environment gets its own install and its own module targets, told
apart by `Qt6Core@host` and `Qt6Core@mcu`. Two environments without names
share one install, because nothing tells them apart.

A Qt target belongs to the environment it was declared in, like any other
target, so one name can be built for both:

```python
project.QtProgram("app", host, sources=["main.cpp"], link=[host_qt.Widgets])
project.QtProgram("app", mcu, sources=["main.cpp"], link=[mcu_qt.Core])
```

They are `app@host` and `app@mcu`, in separate build directories.

## The automoc scan

`QtProgram` scans the target's sources, their same-basename headers, and
the closure of project-local `#include "..."` files for `Q_OBJECT`,
`Q_GADGET`, and `Q_NAMESPACE` — at **generate time**, mtime-cached, never
during the build. Unlike CMake's line-anchored regex, declarations like
`class C : public QObject { Q_OBJECT };` on one line are found too.

Because the scan runs when pcons runs, a header that *gains* `Q_OBJECT`
afterward would be missed — so each Qt target also gets a tiny
`scan.ok` build edge whose depfile covers every scanned file and
directory. When the scan result would change, the build stops:

```
pcons Qt: the moc scan for target 'myapp' is out of date:
  src/newthing.h now needs moc (header gained a Qt macro)

Re-run pcons to regenerate the build files.
```

Escape hatches: `automoc=False`, `autouic=False`, `autorcc=False`, and
`no_moc=["src/weird.h"]`.

### `no_moc` does not stop the walk

`no_moc` excludes a file from moc *generation*. The scan still opens it
and still follows its `#include` lines, so a `Q_OBJECT` header behind an
excluded one still gets a moc edge. Excluding one header moves the
problem one include deeper rather than removing it.

What ends the walk is a header the target's sources never include. A
quoted `#include "Controller.hpp"` resolves against the including file's
own directory first, exactly as the compiler does it, so two targets
whose sources share a directory reach each other's headers with no
include directory configured at all.

When two targets that link together both moc one header, its meta-object
code is compiled twice and the link fails on duplicate
`staticMetaObject` and `qt_static_metacall` symbols in generated files
nobody wrote. pcons reports that at generate time, with the include
chain each target reached the header through:

```
Qt targets 'app' and 'ui_module' both run moc on src/sub/Helper.hpp and
link together, so its meta-object code is compiled once per target ...
  'app' reaches it through src/main.cpp -> src/Controller.hpp -> src/sub/Helper.hpp
  'ui_module' reaches it through src/Controller.cpp -> src/Controller.hpp -> src/sub/Helper.hpp
```

The fix is to give the class one owner: keep the module's headers in a
directory the other target's sources do not include from. The same
header moc'ed by two targets that never meet at a link is two separate
programs sharing a source file, which is fine, and is not reported.

A `.cpp` file with `Q_OBJECT` needs its moc output included at the end
of the file (`#include "myfile.moc"`); pcons errors at generate time if
the include is missing.

## Resources without .qrc XML

```python
res = project.QtResources(
    "assets", env, files=["images/*.png", "data/config.json"], prefix="/"
)
app.link(res)
```

pcons synthesizes the `.qrc` (globs expanded, aliases relative to the
project root), runs rcc with a depfile, and returns an object target.
Files are reachable as `:/images/logo.png` etc.

!!! note "Static libraries"
    Resources compiled into a *static* library need
    `Q_INIT_RESOURCE(name);` in the consuming application, or the
    linker may drop the auto-registration object.

## Low-level builders

The Meson-style explicit API, for when you want full control (this is
exactly what QtProgram automates):

```python
moc_cpp = env.qt.Moc(sources="mainwindow.h")  # → moc_mainwindow.cpp
dot_moc = env.qt.Moc(sources="widget.cpp")  # → widget.moc
ui_hdr = env.qt.Uic(sources="mainwindow.ui")  # → ui_mainwindow.h
res_cpp = env.qt.Rcc(sources="icons.qrc", name="icons")

app = project.Program("myapp", env, sources=["main.cpp", moc_cpp[0], res_cpp[0]])
app.link(qt.Widgets)
app.depends(ui_hdr[0])
env.cxx.includes.append(str(project.build_dir / "qt.gen"))
```

moc needs Qt's include paths and defines to parse headers
(`env.qt.mocincludes`, `env.qt.mocdefines`); QtProgram fills them from
the targets you `link=`.

Related `env.qt` variables: `mocflags`, `uicflags`, `rccflags`,
`mocpredefs` (compiler-builtin macros via `--include moc_predefs.h`,
generated automatically for GCC/Clang).

## Generated file layout

| What | Where |
|---|---|
| QtProgram("app", ...) codegen | `build/qt.app/<source-relative-dir>/` |
| Low-level builders (default) | `build/qt.gen/<source-relative-dir>/` |
| QtResources | `build/qt.res/` |
| Scan manifest + stamp | `build/qt.app/scan-manifest.json`, `scan.ok` |

## Current limitations

Worth knowing before porting a large CMake project:

- **Flags are captured when the Qt target is created.** `QtProgram`
  snapshots the environment (and computes moc's view of the world) at
  the call; `env.cxx.defines.append(...)` *after* the call doesn't reach
  that target. Pass Qt modules via `link=[...]` at construction — moc
  needs their include paths, and a later `app.link(qt.Widgets)` is too
  late for moc (pcons warns when this happens).
- **Windows debug builds:** the `d`-suffixed Qt libraries are selected
  by the variant at `find_qt()` time — call `find_qt()` *after*
  `env.set_variant()`, and build debug and release in separate pcons
  runs (not as two variants of one project).
- **Prebuilt Qt-based SDKs:** the automoc scan follows includes into
  directories you list in `env.cxx.includes` — including out-of-project
  ones. Headers from a *prebuilt* Qt-based SDK reached that way would
  get spurious moc edges; `no_moc=[...]` suppresses the edge on each
  header you name, and the scan still walks through them, so name every
  such header the walk reaches. (Libraries found via
  `find_package`/`find_qt` are excluded automatically.)
- **pcons cannot see what the target Qt was built with.** moc, uic and
  rcc run on the build machine, from the host Qt, and their output is
  compiled against the Qt you link. When the two were configured
  differently the generated code can reference something the target does
  not have: rcc compresses with zstd by default, and a target Qt built
  without zstd then fails to link on `qResourceFeatureZstd`. Neither
  `qtpaths6 --query` nor pkg-config reports a Qt install's feature set
  (only paths, mkspec and version), so pcons cannot detect this. Spell
  the flag out on the environment that needs it, which is per
  environment like everything else:

  ```python
  cross.qt.rccflags.append("--no-zstd")
  ```

  The target Qt's feature list is in
  `<prefix>/mkspecs/qconfig.pri` (`QT.global.enabled_features`) and in
  `<prefix>/include/QtCore/qconfig.h` (`QT_FEATURE_zstd`) if you need to
  check which way it was built.
- **Not yet implemented:** qmlcachegen AOT compilation, QML plugin
  libraries / singletons / subdirectory QML files, static-Qt plugin
  imports (`Q_IMPORT_PLUGIN`), per-file resource compression options and
  big-resource two-pass rcc, lupdate's automatic per-target source
  collection, and Designer plugin builds. Branch switches that change
  the source list need a pcons re-run (there is no CMake-style
  self-regeneration yet); the scan guard reports this for moc changes.

## Platform notes

- **macOS**: framework builds (Homebrew, official installer) link with
  `-F`/`-framework` automatically. On Apple Silicon with Qt < 6.10,
  `find_qt` also pre-includes `<arm_acle.h>` to work around
  `qyieldcpu.h`'s bare `__yield()` (fixed upstream in Qt 6.10).
- **Windows**: MSVC and clang-cl get `/Zc:__cplusplus /permissive-`
  (required by Qt headers); debug variants link the `d`-suffixed
  libraries; moc runs with `--compiler-flavor msvc`.
- **Linux**: distro Qt (apt/dnf/pacman) is found via pkg-config; the
  official installer via `qtpaths` or `qt_root=`/`$PCONS_QT_ROOT`.
- **Android**: Qt names every library after the ABI, so an
  `android_arm64_v8a` install holds `libQt6Core_arm64-v8a.so` and no
  unsuffixed file. `find_qt` reads the suffix off the install and links
  `-lQt6Core_arm64-v8a`. The module target keeps its plain name,
  `qt.Core`. Qt reports no ABI of its own, so this needs the `qtpaths`
  probe and a real install to read.

## QML modules

`QtQmlModule` bundles QML files and `QML_ELEMENT` C++ classes into a
module the engine loads by URI:

```python
qt = find_qt(project, env, modules=["Qml"])

ui = project.QtQmlModule(
    "app_ui",
    env,
    uri="com.example.app",
    version="1.0",
    qml_files=["qml/Main.qml"],
    sources=["src/backend.cpp"],  # classes marked QML_ELEMENT
    link=[qt.Qml],
)

app = project.QtProgram("app", env, sources=["src/main.cpp"], link=[qt.Qml])
app.link(ui)
```

```cpp
QQmlApplicationEngine engine;
engine.loadFromModule("com.example.app", "Main");   // that's it
```

One call replaces CMake's `qt_add_qml_module` plumbing: moc emits JSON
metadata, `qmltyperegistrar` generates the type registrations (plus a
`.qmltypes` for tooling), a `qmldir` is synthesized, and everything
embeds under `:/qt/qml/<uri>/` — the engine's default import path. The
module builds as an *object* target, so linking it into the app can't
dead-strip the registrations — no plugin/backing-target split, no
`Q_INIT_RESOURCE`, no import-path setup.

A QML file starting with `pragma Singleton` is declared `singleton` in the
qmldir, so the engine hands out the instance rather than the type. Nothing
to pass: the pragma is read from the file, and editing one re-runs pcons so
the qmldir keeps up. A generated QML file that does not exist yet when the
build is described reads as not a singleton.

Not yet included: `qmlcachegen` ahead-of-time QML compilation (the
embedded QML runs through the normal engine path — functionally
identical, slightly slower startup) and separate QML plugin libraries.
`qml_files` entries are embedded under their base name, so a nested layout
is flattened and two files with one base name collide.

## Translations

```python
tr = project.QtTranslations(
    "i18n",
    env,
    ts_files=["i18n/app_de.ts", "i18n/app_fr.ts"],
    lupdate_sources=["src/main.cpp", "src/mainwindow.cpp"],
)
app.link(tr)
```

Each `.ts` catalog compiles with lrelease and embeds under `:/i18n/`:

```cpp
QTranslator translator;
translator.load(QLocale(), "app", "_", ":/i18n");
QCoreApplication::installTranslator(&translator);
```

Refreshing the catalogs from sources is **`ninja lupdate`** — a utility
target that is *never* part of the default build or `ninja all`, because
it writes into the source tree. (This uses `target.build_by_default =
False`, available for any utility target.)

## Deployment

```python
project.QtDeploy("deploy", env, app=app, bundle="MyApp.app")  # macOS
project.QtDeploy("deploy", env, app=app, deploy_dir="deploy")  # Windows
```

`ninja deploy` runs macdeployqt (fixes up a `.app` bundle in place —
build the bundle first, e.g. with `pcons.contrib.bundle` or Install
targets) or windeployqt (copies DLLs/plugins next to the executable).
Never part of the default build. Linux deployment is out of scope —
use linuxdeploy/appimagetool on the installed tree.

!!! note "Homebrew Qt and macdeployqt"
    macdeployqt is most reliable with the official Qt installer. With
    Homebrew's framework layout it can leave stray `@rpath` references
    (e.g. QtGui → QtDBus) — a known macdeployqt limitation that affects
    CMake builds identically.

### Android: the androiddeployqt settings file

`androiddeployqt` is driven by a JSON file that Qt's CMake writes. pcons
writes it too:

```python
from pcons.toolchains.presets import android
from pcons.toolchains.qt.android import android_deployment_settings

env.apply_cross_preset(android(ndk=NDK, api=35, sdk=SDK))
qt = find_qt(project, env, modules=["Core"])
app = project.SharedLibrary("myapp", env, sources=["main.cpp"])

settings = android_deployment_settings(
    project,
    env,
    app=app,
    package_name="org.example.myapp",
    package_source_dir="android",
    permissions=["android.permission.INTERNET"],
)
```

It is written at configure time, like `configure_file`, and the path is
returned. Everything in it comes from the cross preset and from the Qt
found for that environment, so `android()` must be given `sdk=` as well as
`ndk=`.

`package_source_dir` is a directory of Android sources -- the manifest,
Java, resources -- that androiddeployqt overlays on Qt's own templates. It
is made absolute against the project root, because androiddeployqt reads it
directly rather than through a build directory. Assembling that directory
is the caller's: `project.Install()` is one way to build it out of a shared
tree and a per-application one. `permissions` takes bare names; the
`[{"name": ...}]` shape the file wants is not the caller's business.

`build_tools` reads as optional and is not: androiddeployqt detects no
build-tools revision of its own, so without it gradle.properties gets an
empty `androidBuildToolsVersion` and Gradle stops with `Invalid revision`.
Naming the highest revision installed under the SDK is what
`examples/77_qt_android_apk` does.

The tools androiddeployqt runs itself -- `rcc`, `qmlimportscanner`,
`qmldom` -- are named in the file, and they are **host** programs. A Qt for
Android ships none of them, and `qtpaths --query` on such an install reports
`QT_HOST_BINS` and `QT_HOST_LIBEXECS` pointing at the host Qt beside it, so
`find_qt` already answers with the right directories. `qmldom` is not in
every Qt build and is left out when it is missing.

This covers one ABI and one application. Two things pcons never writes,
because androiddeployqt works them out itself with the built application in
front of it: the transitive Qt library set, which it reads out of the staged
`.so`, and the QML imports, which it finds by running `qmlimportscanner`.
`android-deploy-plugins` is left out for the same reason — absent, Qt's own
XML decides which plugin directories are bundled, which makes a larger
package and never a broken one.

#### QML

androiddeployqt runs `qmlimportscanner` to decide which Qt QML modules to
bundle, and the scanner reads the filesystem. So pcons writes
`qml-root-path`: the QML source directory of every `QtQmlModule` built in
this environment. Nothing is asked of the build script -- a module already
knows where its QML is.

`qml-skip-import-scanning` is written only when the environment has no
`QtQmlModule` at all. An application with QML gets the scan.

A `QtQmlModule`'s generated `qmldir` is embedded in a resource, and pcons
writes it flat, so an import path resolves none of the application's own
modules. Measured against Qt 6.11.1: that costs nothing as long as every
module's QML source directory is a root path. The scanner then reports the
same Qt modules either way, and the application's own module is reported
with no path -- which is right, since it is in the resource and there is
nothing on disk to bundle.

#### Staging the application library

androiddeployqt reads the application out of `<output>/libs/<abi>/` and does
not put it there. Its own dependency libraries it does copy, out of the
`extraLibraryDirs` the settings file names, but not the application itself.
With nothing staged it exits 0 and reports success, having packaged no
application at all, so this step is not optional:

```python
from pcons.toolchains.qt.apk import stage_application_library

staged = stage_application_library(project, env, app=app)
```

One copy, into `<build>/<app>/libs/<abi>/lib<app>_<abi>.so`. The ABI suffix
is what androiddeployqt looks for, and the directory is named after the
application because androiddeployqt owns the whole of it -- two applications
built in one environment each get their own.

pcons copies rather than building the application straight into the staging
directory the way Qt's CMake does. One environment then builds any number of
applications instead of needing one environment each because of a packaging
detail.

#### Building the package

```python
from pcons.toolchains.qt.apk import android_apk

apk = android_apk(project, env, app=app, settings=settings)
```

The staging above happens on its own when `android_apk` is not given a
`staged=`, so a package cannot be built without the application in it.
androiddeployqt is a host tool, and the Qt located for an Android
environment already answers with the host directories, so nothing has to be
told where it is.

The package lands at
`<output>/build/outputs/apk/debug/<output>-debug.apk`. Gradle names it after
the **output directory**, not after the application, and `release=True`
gives `<output>/build/outputs/apk/release/<output>-release-unsigned.apk` --
unsigned, so it installs on nothing until it is signed. The debug default is
signed with Gradle's own debug key and installs.

`no_build=True` passes `--no-build`, which is androiddeployqt's "install a
package built earlier" mode rather than a way to stop before Gradle: on its
own it writes nothing at all. The target is a stamp and is not built by
default.

!!! note "This edge runs a build system"
    androiddeployqt drives Gradle, and pcons's first rule is configuration
    rather than execution. It is still the right call -- the alternative is
    owning Qt's Java, its manifest merging and its resource pipeline -- but
    the cost is real. The first run downloads the Gradle distribution and
    the Android Gradle plugin from the network, which takes minutes; later
    runs use the cache. With a warm cache, Qt 6.11.1: 19 s and 35 Gradle
    tasks for a debug package. No system Gradle is needed, androiddeployqt
    brings its own wrapper. Gradle needs a JDK it supports: 21 works, 26
    fails inside the Android Gradle plugin.

#### Signing a release package

A release package comes out of androiddeployqt unsigned and installs on
nothing. `sign_apk` signs it with `apksigner`, on an edge of its own:

```python
from pcons.toolchains.qt.apk import android_apk, sign_apk

apk = android_apk(project, env, app=app, settings=settings, release=True)
signed = sign_apk(
    project,
    env,
    app=app,
    apk=apk,
    keystore="/etc/keys/release.jks",
    alias="upload",
    store_password="env:MYAPP_KEYSTORE_PASS",
)
```

**The password is named, never given.** `store_password` is an apksigner
password source: `env:NAME` reads it from the environment the build runs
in, `file:PATH` from the first line of a file. Either way the generated
build file holds a variable name or a path, and apksigner reads the value
itself when the edge runs. `pass:<password>` is refused, because pcons
would write it into `build.ninja`, and so is `stdin`, because a build edge
has no console to answer a prompt.

`alias` is needed only when the keystore holds more than one key.
`key_password` names where the private key password comes from, in the same
two forms; left out, apksigner opens the key with the keystore password.
Two `file:` sources may not be the same file: apksigner reads one password
per line from a file and fails on the second with `end of file reached`.

The keystore is an implicit dependency of the edge, so a new keystore
re-signs. It is never generated: without `keystore=` the call raises and
names the argument, rather than falling back to a debug key. Debug packages
need none of this -- Gradle signs them with its own debug key, and
`android_apk` without `release=True` produces one that installs.

The signed package lands beside the unsigned one, at
`<output>/build/outputs/apk/release/<output>-release-signed.apk`, which is
the name Qt's own `--sign` gives it.

!!! note "Why not androiddeployqt --sign"
    androiddeployqt signs on its own with `--sign <keystore> <alias>`, and
    it does read `QT_ANDROID_KEYSTORE_STORE_PASS` and
    `QT_ANDROID_KEYSTORE_KEY_PASS` from the build-time environment, so that
    path would keep the password out of the build file too. A separate edge
    wins on three counts: changing the keystore re-signs instead of
    re-running Gradle, the build file says where the password comes from
    instead of depending on ambient variables nothing declares, and
    `--sign` renames the package androiddeployqt writes, which would make
    the path depend on a signing argument.

#### The worked example

`examples/77_qt_android_apk` is the whole path end to end: a Qt Quick
application, a `QtQmlModule` cross-compiled with the host Qt's moc and rcc,
one Java class in the package source directory, a manifest naming
`QtActivity`, the settings file, the staging and androiddeployqt.

Three things it needs that nothing probes for:

- `PCONS_QT_ANDROID_ROOT`, the Qt built for Android. `find_qt` is given it
  as `qt_root=` with `probe="qtpaths"` -- the pkg-config probe would answer
  with a libexec directory full of target executables.
- a build-tools revision, see above.
- for a real Gradle run, a JDK the Android Gradle plugin supports. 21 works;
  26 fails in the `androidJdkImage` transform. That is a property of the
  machine, so the example does not choose one: set `JAVA_HOME` if the
  default is newer.

CI runs it with `no_build=True`, which proves everything pcons writes --
the settings, the staged `lib<app>_<abi>.so`, the command line -- without
the network and the minutes Gradle's first run costs. `PCONS_ANDROID_GRADLE=1`
switches the example to a real package.

### Packaging into installers

Deployed Qt apps compose with the installer generators in
`pcons.contrib.installers` (both flows are tested):

```python
# macOS: .app -> macdeployqt -> .pkg
pkg = installers_macos.create_pkg(
    project,
    env,
    name="MyApp",
    version="1.0.0",
    identifier="com.example.myapp",
    sources=["build/MyApp.app"],
)
pkg.depends(deploy)

# Windows: windeployqt dir -> .msix (the directory stages as a
# subfolder, so the executable path includes it)
msix = installers_windows.create_msix(
    project,
    env,
    name="MyApp",
    version="1.0.0.0",
    publisher="CN=Example",
    sources=["build/deploy"],
    executable="deploy\\myapp.exe",
)
msix.depends(deploy)
```

Build in two steps so packaging always sees the deployed tree:
`ninja deploy && ninja MyApp-1.0.0.pkg`.

## Examples

- `examples/52_qt_widgets` — the high-level QtProgram flow.
- `examples/53_qt_explicit` — the explicit Moc/Rcc flow.
- `examples/54_qt_qml` — a QML module with C++ types.
- `examples/55_qt_translations` — embedded catalogs + `ninja lupdate`.
- `examples/56_qt_deploy` — a relocatable .app via `ninja deploy`.
