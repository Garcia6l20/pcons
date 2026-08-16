#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating target.build_for(): one target, several environments.

A library shared between two builds is declared once and built twice, with
target.build_for(env) producing a second Target bound to another environment.
Its outputs go under that environment's name, so the two never collide:

    build/libgreeting.a         built with env
    build/alt/libgreeting.a     built with alt

Where a line sits decides which build gets it. Written before the build_for()
call, a source or a usage requirement is shared; after it, it belongs to the
original alone; on the returned target, to the copy.

Environment settings are never copied -- the compiler command, env.cc.flags and
env.cc.defines come from the environment each target is bound to. That is what
makes the same source produce two different libraries here.
"""

from pcons import Project

project = Project("build_for_example")

src_dir = project.root_dir / "src"

# Two environments over the same toolchain, differing by one define. A real
# project would more likely pair a cross environment with a host one; the
# principle is the same and this builds anywhere.
env = project.Environment(toolchain="c")

alt = project.Environment(toolchain="c", name="alt")
alt.cc.defines.append("ALT_BUILD=1")

# Everything up to the build_for() call is shared by both builds.
greeting = project.StaticLibrary("greeting", env, sources=["src/greeting.c"])
greeting.public.include_dirs.append(src_dir)

greeting_alt = greeting.build_for(alt)

# ... and everything after it is not. This define reaches the default build
# only; greeting_alt was already taken.
greeting.public.defines.append("DEFAULT_BUILD=1")

app = project.Program("app", env, sources=["src/main.c"])
app.link(greeting)

app_alt = project.Program("app_alt", alt, sources=["src/main.c"])
app_alt.link(greeting_alt)

project.Default(app, app_alt)
