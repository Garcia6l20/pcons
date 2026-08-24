# SPDX-License-Identifier: MIT
"""One project, a gcc environment and an llvm environment.

Only the gcc target uses C++ modules. The llvm target is plain C++.
"""

from pcons import Project

project = Project("two_cxx_toolchains")

gcc_env = project.Environment(toolchain=["gcc", "c"])
gcc_env.cxx.set_standard("c++20")

llvm_env = project.Environment(toolchain=["llvm", "c"])
llvm_env.cxx.set_standard("c++20")

project.Program("with_gcc", gcc_env, sources=["src/mod.cppm", "src/main.cpp"])
project.Program("plain_llvm", llvm_env, sources=["src/plain.cpp"])
