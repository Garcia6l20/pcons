# SPDX-License-Identifier: MIT
"""Two programs share one module interface unit in the same environment."""

from pcons import Project

project = Project("shared_module_source")
env = project.Environment(toolchain=["llvm", "c"])
env.cxx.set_standard("c++20")

project.Program("a", env, sources=["src/util.cppm", "src/a.cpp"])
project.Program("b", env, sources=["src/util.cppm", "src/b.cpp"])
