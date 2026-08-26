# SPDX-License-Identifier: MIT
"""An environment owns where its targets are built (#96).

``build_prefix`` moves everything the environment writes; the three
``*_directory`` settings place the final artifacts by kind below it.
"""

from pathlib import Path

import pytest

from pcons.core.errors import PconsError
from pcons.core.project import Project


@pytest.fixture
def source(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "common.c").write_text("int f(void) { return 1; }\n")
    return src


def _paths(target) -> set[str]:
    return {node.path.as_posix() for node in target.output_nodes}


def _object_paths(target) -> set[str]:
    return {node.path.as_posix() for node in target.intermediate_nodes}


class TestBuildPrefix:
    def test_moves_outputs_and_objects(self, tmp_path, source, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        env.build_prefix = "mcu"
        lib = project.StaticLibrary("common", env, sources=["src/common.c"])

        project.resolve()

        assert _paths(lib) == {"build/mcu/libcommon.a"}
        assert _object_paths(lib) == {"build/mcu/obj.common/src/common.c.o"}

    def test_two_environments_keep_one_name_apart(
        self, tmp_path, source, gcc_toolchain
    ):
        """The #96 shape, without output_name or output_prefix."""
        project = Project("p", root_dir=tmp_path)
        libs = []
        for name in ("mcu", "host"):
            env = project.Environment(toolchain=gcc_toolchain, name=name)
            env.build_prefix = name
            env.archive_directory = "lib"
            libs.append(
                project.StaticLibrary(f"common-{name}", env, sources=["src/common.c"])
            )
            libs[-1].output_name = "common"

        project.resolve()

        assert _paths(libs[0]) == {"build/mcu/lib/libcommon.a"}
        assert _paths(libs[1]) == {"build/host/lib/libcommon.a"}

    def test_env_build_dir_carries_the_prefix(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        env.build_prefix = "mcu"

        assert env.build_dir == Path("build/mcu")

    def test_setting_order_does_not_matter(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        first = project.Environment(toolchain=gcc_toolchain, name="a")
        first.build_prefix = "slice"
        first.build_dir = Path("build")

        second = project.Environment(toolchain=gcc_toolchain, name="b")
        second.build_dir = Path("build")
        second.build_prefix = "slice"

        assert first.build_dir == second.build_dir == Path("build/slice")

    def test_clearing_it_restores_the_build_dir(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        env.build_prefix = "mcu"
        env.build_prefix = None

        assert env.build_dir == Path("build")

    def test_subdirectory_offset_stays_inside_the_prefix(
        self, tmp_path, source, gcc_toolchain
    ):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        env.build_prefix = "mcu"
        (tmp_path / "sub" / "src").mkdir(parents=True)
        (tmp_path / "sub" / "src" / "thing.c").write_text("int g(void) { return 2; }\n")
        with project._enter_subdir("sub"):
            lib = project.StaticLibrary("thing", env, sources=["src/thing.c"])

        project.resolve()

        assert _paths(lib) == {"build/mcu/sub/libthing.a"}

    def test_command_outputs_follow(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="host")
        env.build_prefix = "host"
        (tmp_path / "in.txt").write_text("x")

        bare = env.Command(
            target="gen/version.h",
            source="in.txt",
            command=["cp", "$SOURCE", "$TARGET"],
            name="bare",
        )
        prefixed = env.Command(
            target=project.build_dir / "gen/other.h",
            source="in.txt",
            command=["cp", "$SOURCE", "$TARGET"],
            name="prefixed",
        )

        project.resolve()

        assert _paths(bare) == {"build/host/gen/version.h"}
        assert _paths(prefixed) == {"build/host/gen/other.h"}

    @pytest.mark.parametrize(
        "attr", ["build_prefix", "runtime_directory", "library_directory"]
    )
    def test_absolute_is_refused(self, tmp_path, gcc_toolchain, attr):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain)

        with pytest.raises(PconsError, match=attr):
            setattr(env, attr, tmp_path / "elsewhere")

    def test_escaping_the_build_dir_is_refused(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain)

        with pytest.raises(PconsError, match="archive_directory"):
            env.archive_directory = "../outside"


class TestOutputDirectories:
    def test_each_kind_lands_in_its_own(self, tmp_path, source, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        env.build_prefix = "mcu"
        env.archive_directory = "lib"
        env.library_directory = "lib"
        env.runtime_directory = "bin"

        (source / "main.c").write_text("int main(void) { return 0; }\n")
        archive = project.StaticLibrary("a", env, sources=["src/common.c"])
        shared = project.SharedLibrary("s", env, sources=["src/common.c"])
        program = project.Program("p", env, sources=["src/main.c"])

        project.resolve()

        assert _paths(archive) == {"build/mcu/lib/liba.a"}
        assert _paths(shared) == {"build/mcu/lib/libs.so"}
        assert _paths(program) == {"build/mcu/bin/p"}

    def test_objects_do_not_move(self, tmp_path, source, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        env.build_prefix = "mcu"
        env.archive_directory = "lib"
        lib = project.StaticLibrary("common", env, sources=["src/common.c"])

        project.resolve()

        assert _object_paths(lib) == {"build/mcu/obj.common/src/common.c.o"}

    def test_unknown_target_type_has_no_directory(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain)
        env.runtime_directory = "bin"

        assert env.output_directory_for("command") is None
        assert env.output_directory_for(None) is None

    def test_output_prefix_still_renames_the_file(
        self, tmp_path, source, gcc_toolchain
    ):
        """The two settings do not fight: one is a directory, one a filename."""
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        env.build_prefix = "mcu"
        env.archive_directory = "lib"
        lib = project.StaticLibrary("common", env, sources=["src/common.c"])
        lib.output_prefix = ""

        project.resolve()

        assert _paths(lib) == {"build/mcu/lib/common.a"}


class TestWindowsImportLibrary:
    def test_it_goes_to_the_archive_directory(
        self, tmp_path, source, gcc_toolchain, monkeypatch
    ):
        """CMake sends a DLL to RUNTIME/LIBRARY and its import lib to ARCHIVE.

        sys.platform is patched around resolve() only, because tool detection
        reads it too and shutil.which cannot answer for a platform it is not on.
        """
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="win")
        env.build_prefix = "win"
        env.library_directory = "bin"
        env.archive_directory = "lib"
        shared = project.SharedLibrary("s", env, sources=["src/common.c"])

        monkeypatch.setattr("sys.platform", "win32")
        project.resolve()

        outputs = shared.output_nodes[0]._build_info["outputs"]
        assert outputs["primary"]["path"].as_posix() == "build/win/bin/libs.so"
        assert outputs["import_lib"]["path"].as_posix() == "build/win/lib/libs.lib"
