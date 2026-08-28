# SPDX-License-Identifier: MIT
"""A target is identified by its name and its environment (#96).

Two targets may share a name when both environments are named and the names
differ: Environment.build_prefix then keeps their files apart.
"""

from pathlib import Path

import pytest

from pcons.core.environment import Environment
from pcons.core.errors import PconsError
from pcons.core.project import Project


@pytest.fixture
def source(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "common.c").write_text("int f(void) { return 1; }\n")
    return src


def _two_envs(project, gcc_toolchain):
    envs = []
    for name in ("mcu", "host"):
        env = project.Environment(toolchain=gcc_toolchain, name=name)
        env.build_prefix = name
        envs.append(env)
    return envs


class TestEnvironmentName:
    def test_assignment_changes_the_name(self, test_project):  # noqa: F811
        env = Environment(name="mcu")
        env.name = "host"

        assert env.name == "host"
        assert "name" not in env._vars

    def test_it_still_substitutes(self, test_project):  # noqa: F811
        env = Environment(name="mcu")

        assert env.subst("building $name") == "building mcu"

    @pytest.mark.parametrize("bad", ["a@b", "x::y", "with space"])
    def test_invalid_characters_are_refused(self, test_project, bad):  # noqa: F811
        with pytest.raises(ValueError, match="Environment name"):
            Environment(name=bad)

        env = Environment(name="ok")
        with pytest.raises(ValueError, match="Environment name"):
            env.name = bad


class TestDuplicateNames:
    def test_two_named_environments_may_share_a_name(
        self, tmp_path, source, gcc_toolchain
    ):
        project = Project("p", root_dir=tmp_path)
        libs = [
            project.StaticLibrary("common", env, sources=["src/common.c"])
            for env in _two_envs(project, gcc_toolchain)
        ]

        project.resolve()

        paths = {node.path.as_posix() for lib in libs for node in lib.output_nodes}
        prefix = gcc_toolchain.get_output_prefix("static_library")
        suffix = gcc_toolchain.get_output_suffix("static_library")
        name = f"{prefix}common{suffix}"
        assert paths == {f"build/mcu/{name}", f"build/host/{name}"}

    def test_the_same_environment_twice_is_refused(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        project.StaticLibrary("common", env)

        with pytest.raises(ValueError, match="in environment 'mcu'"):
            project.StaticLibrary("common", env)

    def test_an_unnamed_environment_is_refused(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        named = project.Environment(toolchain=gcc_toolchain, name="mcu")
        unnamed = project.Environment(toolchain=gcc_toolchain)
        project.StaticLibrary("common", named)

        with pytest.raises(ValueError, match="named and different"):
            project.StaticLibrary("common", unnamed)

    def test_colliding_outputs_still_raise(self, tmp_path, source, gcc_toolchain):
        """Duplicate names are legal because the directories differ, not the names."""
        project = Project("p", root_dir=tmp_path)
        for name in ("mcu", "host"):
            env = project.Environment(toolchain=gcc_toolchain, name=name)
            project.StaticLibrary("common", env, sources=["src/common.c"])

        with pytest.raises(PconsError, match="both build"):
            project.resolve()


class TestEnvironmentNamesAreUnique:
    """A name says which environment a target was built in, so it identifies one."""

    def test_a_second_environment_of_that_name_is_refused(
        self, tmp_path, gcc_toolchain
    ):
        project = Project("p", root_dir=tmp_path)
        project.Environment(toolchain=gcc_toolchain, name="mcu")

        with pytest.raises(PconsError, match="already has an environment named"):
            project.Environment(toolchain=gcc_toolchain, name="mcu")

    def test_unnamed_environments_are_unconstrained(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        project.Environment(toolchain=gcc_toolchain)
        project.Environment(toolchain=gcc_toolchain)

        assert [env.name for env in project.environments] == [None, None]

    def test_a_sub_project_keeps_its_own_names(self, tmp_path, gcc_toolchain):
        """'child::app@host' is not 'p::app@host', so both may have a host."""
        project = Project("p", root_dir=tmp_path)
        project.Environment(toolchain=gcc_toolchain, name="host")
        (tmp_path / "child").mkdir()
        with project._enter_subdir("child"):
            child = Project("child", root_dir=tmp_path / "child")
            child.Environment(toolchain=gcc_toolchain, name="host")

        assert [env.name for env in child.environments] == ["host"]


class TestLookup:
    def test_an_ambiguous_name_raises(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        for env in _two_envs(project, gcc_toolchain):
            project.StaticLibrary("common", env)

        with pytest.raises(KeyError, match="common@mcu"):
            project.get_target("common")

    def test_a_single_match_still_resolves(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        lib = project.StaticLibrary("common", env)

        assert project.get_target("common") is lib
        assert project.get_target("p::common") is lib


class TestQualifiedSpelling:
    def test_it_names_the_environment(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        lib = project.StaticLibrary("common", env)

        assert lib.env is env
        assert lib.qualified_name == "p::common@mcu"

    def test_an_unnamed_environment_leaves_the_plain_name(
        self, tmp_path, gcc_toolchain
    ):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain)
        lib = project.StaticLibrary("common", env)

        assert lib.qualified_name == "p::common"
