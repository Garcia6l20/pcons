# SPDX-License-Identifier: MIT
"""A scanned .cpp includes a header written by a Command.

The generator sleeps so the scan loses the race on purpose. Without the sleep
the header usually lands first and the build passes by luck.
"""

import sys

from pcons import Project

project = Project("scan_edge_missing_deps")
env = project.Environment(toolchain=["llvm", "c"])
env.cxx.set_standard("c++20")
env.cxx.includes.append(project.build_dir)

py = sys.executable.replace("\\", "/")
gen = env.Command(
    target=project.build_dir / "gen.h",
    source=["src/gen.py"],
    command=[py, "${SOURCES[0]}", "$TARGET"],
    name="genhdr",
)

app = project.Program("app", env, sources=["src/mod.cppm", "src/main.cpp"])
app.depends(gen)
