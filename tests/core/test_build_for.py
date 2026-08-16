# SPDX-License-Identifier: MIT
"""Tests for Target.build_for(): one target built for several environments."""

from pathlib import Path

import pytest

from pcons.core.project import Project
from pcons.core.target import Target


@pytest.fixture
def project(tmp_path, gcc_toolchain):
    project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
    (tmp_path / "common.c").write_text("int common(void) { return 0; }\n")
    (tmp_path / "extra.c").write_text("int extra(void) { return 1; }\n")
    return project


@pytest.fixture
def envs(project, gcc_toolchain):
    native = project.Environment(toolchain=gcc_toolchain)
    host = project.Environment(toolchain=gcc_toolchain, name="host")
    for env in (native, host):
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"
    return native, host


class TestBuildForCreation:
    def test_copy_shares_the_name_and_gets_the_env_subdir(self, project, envs):
        native, host = envs
        common = project.StaticLibrary("common", native, sources=["common.c"])
        copy = common.build_for(host)

        assert copy.name == "common"
        assert copy._env is host
        assert copy.qualified_name == "test::common@host"
        assert copy.build_dir == project.build_dir / "host"
        assert copy.target_type == common.target_type
        assert copy._builder_name == common._builder_name

    def test_is_idempotent(self, project, envs):
        native, host = envs
        common = project.StaticLibrary("common", native, sources=["common.c"])

        assert common.build_for(host) is common.build_for(host)

    def test_rejects_the_targets_own_environment(self, project, envs):
        native, _host = envs
        common = project.StaticLibrary("common", native, sources=["common.c"])

        with pytest.raises(ValueError, match="already built with this environment"):
            common.build_for(native)

    def test_rejects_an_unnamed_environment(self, project, envs, gcc_toolchain):
        native, _host = envs
        other = project.Environment(toolchain=gcc_toolchain)
        common = project.StaticLibrary("common", native, sources=["common.c"])

        with pytest.raises(ValueError, match="needs a named environment"):
            common.build_for(other)

    def test_rejects_deriving_from_a_copy(self, project, envs, gcc_toolchain):
        native, host = envs
        third = project.Environment(toolchain=gcc_toolchain, name="third")
        common = project.StaticLibrary("common", native, sources=["common.c"])
        copy = common.build_for(host)

        with pytest.raises(ValueError, match="itself a build_for"):
            copy.build_for(third)


class TestBuildForCopiesContents:
    def test_sources_and_requirements_reach_the_copy(self, project, envs):
        native, host = envs
        common = project.StaticLibrary("common", native, sources=["common.c"])
        common.public.include_dirs.append(project.root_dir)
        common.private.compile_flags.append("-DPRIVATE=1")
        copy = common.build_for(host)

        project.resolve()

        assert [s.path for s in copy.sources] == [s.path for s in common.sources]
        assert list(copy.public.include_dirs) == [project.root_dir]
        assert list(copy.private.compile_flags) == ["-DPRIVATE=1"]

    def test_content_added_after_build_for_stays_on_the_source(self, project, envs):
        """Where a line sits decides which builds get it."""
        native, host = envs
        common = project.StaticLibrary("common", native, sources=["common.c"])
        common.public.defines.append("SHARED=1")

        copy = common.build_for(host)

        common.add_sources(["extra.c"])
        common.public.defines.append("NATIVE_ONLY=1")
        copy.public.defines.append("HOST_ONLY=1")

        project.resolve()

        assert len(common.sources) == 2
        assert len(copy.sources) == 1
        assert list(common.public.defines) == ["SHARED=1", "NATIVE_ONLY=1"]
        assert list(copy.public.defines) == ["SHARED=1", "HOST_ONLY=1"]

    def test_output_naming_set_on_the_copy_wins(self, project, envs):
        native, host = envs
        common = project.StaticLibrary("common", native, sources=["common.c"])
        common.output_name = "shared"
        copy = common.build_for(host)
        copy.output_name = "hostside"

        project.resolve()

        assert copy.output_name == "hostside"
        assert common.output_name == "shared"

    def test_output_naming_is_inherited_when_unset(self, project, envs):
        native, host = envs
        common = project.StaticLibrary("common", native, sources=["common.c"])
        common.output_name = "shared"
        common.output_suffix = ".lib"
        copy = common.build_for(host)

        project.resolve()

        assert copy.output_name == "shared"
        assert copy.output_suffix == ".lib"

    def test_per_source_env_override_survives(self, project, envs, gcc_toolchain):
        native, host = envs
        third = project.Environment(toolchain=gcc_toolchain, name="third")
        common = project.StaticLibrary("common", native)
        common.add_sources(["common.c"], env=third)
        copy = common.build_for(host)

        project.resolve()

        assert list(copy._source_envs.values()) == [third]

    def test_dependencies_reach_the_copy(self, project, envs):
        native, host = envs
        common = project.StaticLibrary("common", native, sources=["common.c"])
        helper = Target("helper")
        common.depends(helper)
        copy = common.build_for(host)

        project.resolve()

        assert helper in copy._implicit_target_deps


class TestBuildForBuildsBothVariants:
    def test_each_variant_gets_its_own_objects_and_archive(self, project, envs):
        native, host = envs
        common = project.StaticLibrary("common", native, sources=["common.c"])
        copy = common.build_for(host)

        project.resolve()

        assert common.intermediate_nodes[0].path == Path("build/obj.common/common.c.o")
        assert copy.intermediate_nodes[0].path == Path(
            "build/host/obj.common/common.c.o"
        )
        assert common.output_nodes[0].path == Path("build/libcommon.a")
        assert copy.output_nodes[0].path == Path("build/host/libcommon.a")

    def test_variants_do_not_share_object_nodes(self, project, envs):
        native, host = envs
        common = project.StaticLibrary("common", native, sources=["common.c"])
        copy = common.build_for(host)

        project.resolve()

        assert common.intermediate_nodes[0] is not copy.intermediate_nodes[0]


class TestBuildForRebindsDependencies:
    def test_a_linked_dep_is_rebound_to_its_own_copy(self, project, envs):
        native, host = envs
        core = project.StaticLibrary("core", native, sources=["extra.c"])
        common = project.StaticLibrary("common", native, sources=["common.c"])
        common.link(core)

        core_host = core.build_for(host)
        common_host = common.build_for(host)

        project.resolve()

        assert list(common.public.link_libs) == [core]
        assert list(common_host.public.link_libs) == [core_host]

    def test_a_dep_already_in_the_new_environment_is_kept(self, project, envs):
        native, host = envs
        core = project.StaticLibrary("core", host, sources=["extra.c"])
        common = project.StaticLibrary("common", native, sources=["common.c"])
        common.link(core)
        common_host = common.build_for(host)

        project.resolve()

        assert list(common_host.public.link_libs) == [core]

    def test_a_dep_with_no_copy_raises(self, project, envs):
        native, host = envs
        core = project.StaticLibrary("core", native, sources=["extra.c"])
        common = project.StaticLibrary("common", native, sources=["common.c"])
        common.link(core)
        common.build_for(host)

        with pytest.raises(ValueError, match="depends on 'core'"):
            project.resolve()

    def test_an_env_less_dep_is_kept(self, project, envs):
        native, host = envs
        helper = Target("helper")
        helper._env = None
        common = project.StaticLibrary("common", native, sources=["common.c"])
        common.depends(helper)
        common_host = common.build_for(host)

        project.resolve()

        assert helper in common_host._implicit_target_deps

    def test_link_order_is_preserved(self, project, envs):
        native, host = envs
        first = project.StaticLibrary("first", native, sources=["extra.c"])
        second = project.StaticLibrary("second", native, sources=["extra.c"])
        common = project.StaticLibrary("common", native, sources=["common.c"])
        common.link(first, "m", second)

        first_host = first.build_for(host)
        second_host = second.build_for(host)
        common_host = common.build_for(host)

        project.resolve()

        assert list(common_host.public.link_libs) == [first_host, "m", second_host]


class TestBuildForOrdering:
    def test_dependency_copies_may_be_declared_in_either_order(self, project, envs):
        """Content is snapshotted at the call, but a dependency is not."""
        native, host = envs
        core = project.StaticLibrary("core", native, sources=["extra.c"])
        common = project.StaticLibrary("common", native, sources=["common.c"])
        common.link(core)

        common_host = common.build_for(host)
        core_host = core.build_for(host)  # after, and still found

        project.resolve()

        assert list(common_host.public.link_libs) == [core_host]
