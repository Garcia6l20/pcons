# SPDX-License-Identifier: MIT
"""Two static libraries list the same module interface unit.

`one` is declared first and owns the shared object node. `two` has no other
scanned source, so it is left without a scan scope, and `consumer` cannot see
what it exports.

Remove `one` and the rest of the script builds and runs.
"""

from pcons import Project

project = Project("shared_module_across_libraries")
env = project.Environment(toolchain=["llvm", "c"])
env.cxx.set_standard("c++20")

project.StaticLibrary("one", env, sources=["src/util.cppm", "src/extra.cpp"])
two = project.StaticLibrary("two", env, sources=["src/util.cppm"])

consumer = project.Program("consumer", env, sources=["src/consumer.cpp"])
consumer.link(two)
