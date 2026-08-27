# SPDX-License-Identifier: MIT
"""An interface target carries a dependency to whoever consumes it.

It has no build step of its own to order, so the ordering it declares is only
visible in its consumers. Before this, the declaration was accepted and dropped.
"""

import sys
from pathlib import Path

import pytest

from pcons.core.errors import PconsError
from pcons.core.project import Project


@pytest.fixture
def generated_header(tmp_path, gcc_toolchain):
    (tmp_path / "main.c").write_text('#include "gen.h"\nint main(void){return 0;}\n')
    project = Project("iface", root_dir=tmp_path)
    env = project.Environment(toolchain=gcc_toolchain)
    gen = env.Command(
        target="gen/gen.h",
        command=[sys.executable, "-c", "pass", "$TARGET"],
        name="gen",
    )
    iface = project.HeaderOnlyLibrary("headers")
    iface.public.include_dirs.append(project.build_dir / "gen")
    return project, env, gen, iface


def header_node(gen):
    return gen.output_nodes[0]


def compile_deps(target):
    node = target.intermediate_nodes[0]
    return node.implicit_deps + node.order_only_deps


class TestInterfaceDepends:
    def test_consumer_compiles_after_the_dependency(self, generated_header):
        project, env, gen, iface = generated_header
        iface.depends(gen)
        app = project.Program("app", env, sources=["main.c"])
        app.link(iface)

        project.resolve()

        assert header_node(gen) in compile_deps(app)

    def test_the_dependency_reaches_a_transitive_consumer(self, generated_header):
        project, env, gen, iface = generated_header
        iface.depends(gen)
        middle = project.StaticLibrary("middle", env, sources=["main.c"])
        middle.link(iface)
        app = project.Program("app", env, sources=["main.c"])
        app.link(middle)

        project.resolve()

        assert header_node(gen) in compile_deps(app)

    def test_it_matches_what_link_would_have_done(self, generated_header):
        project, env, gen, iface = generated_header
        iface.depends(gen)
        depending = project.Program("depending", env, sources=["main.c"])
        depending.link(iface)

        linking_iface = project.HeaderOnlyLibrary("headers_linked")
        linking_iface.link(gen)
        linking = project.Program("linking", env, sources=["main.c"])
        linking.link(linking_iface)

        project.resolve()

        assert compile_deps(depending) == compile_deps(linking)

    def test_a_file_dependency_is_refused(self, generated_header):
        _project, _env, _gen, iface = generated_header

        with pytest.raises(PconsError, match="cannot depend on the file"):
            iface.depends(Path("schema.json"))

    def test_a_compiled_target_keeps_the_dependency_to_itself(self, generated_header):
        project, env, gen, _iface = generated_header
        lib = project.StaticLibrary("lib", env, sources=["main.c"])
        lib.depends(gen)
        app = project.Program("app", env, sources=["main.c"])
        app.link(lib)

        project.resolve()

        assert header_node(gen) in compile_deps(lib)
        assert header_node(gen) not in compile_deps(app)
