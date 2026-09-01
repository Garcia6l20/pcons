# SPDX-License-Identifier: MIT
"""Two targets that moc the same header and link together (no Qt needed)."""

from __future__ import annotations

import logging

import pytest

from pcons.core.project import Project

from ._qt_test_utils import cxx_env_with_qt


def _header(name: str, includes: str = "") -> str:
    return (
        f"#pragma once\n#include <QObject>\n{includes}"
        f"class {name} : public QObject {{ Q_OBJECT }};\n"
    )


@pytest.fixture
def shared_dir_tree(tmp_path, monkeypatch):
    """A program and a QML module whose sources share one directory.

    ``main.cpp`` includes ``Controller.hpp`` by quoted name with no include
    directory configured, so it resolves through the same-directory fallback,
    and the walk goes on into ``src/sub``, a second directory the program
    names nowhere.
    """
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "src"
    sub = src / "sub"
    sub.mkdir(parents=True)
    (sub / "Helper.hpp").write_text(_header("Helper"))
    (src / "Controller.hpp").write_text(
        _header("Controller", '#include "sub/Helper.hpp"\n')
    )
    (src / "Controller.cpp").write_text('#include "Controller.hpp"\n')
    (src / "main.cpp").write_text(
        '#include "Controller.hpp"\nint main() { return 0; }\n'
    )
    return tmp_path


class TestDuplicateMoc:
    def test_the_shared_directory_split_is_reported(self, shared_dir_tree, caplog):
        project = Project(
            "dup", root_dir=shared_dir_tree, build_dir=shared_dir_tree / "build"
        )
        env = cxx_env_with_qt(project)
        module = project.QtQmlModule(
            "mod", env, uri="a.b", sources=["src/Controller.cpp"]
        )
        app = project.QtProgram("app", env, sources=["src/main.cpp"])
        app.link(module)

        with caplog.at_level(logging.WARNING):
            project.resolve()

        assert "Controller.hpp" in caplog.text
        assert "'app'" in caplog.text and "'mod'" in caplog.text

    def test_the_transitive_hop_is_reported(self, shared_dir_tree, caplog):
        project = Project(
            "dup", root_dir=shared_dir_tree, build_dir=shared_dir_tree / "build"
        )
        env = cxx_env_with_qt(project)
        module = project.QtQmlModule(
            "mod", env, uri="a.b", sources=["src/Controller.cpp"]
        )
        app = project.QtProgram("app", env, sources=["src/main.cpp"])
        app.link(module)

        with caplog.at_level(logging.WARNING):
            project.resolve()

        assert "Helper.hpp" in caplog.text

    def test_no_moc_on_the_reached_header_does_not_stop_the_walk(
        self, shared_dir_tree, caplog
    ):
        """Excluding Controller.hpp leaves the deeper duplicate in place."""
        project = Project(
            "dup", root_dir=shared_dir_tree, build_dir=shared_dir_tree / "build"
        )
        env = cxx_env_with_qt(project)
        module = project.QtQmlModule(
            "mod", env, uri="a.b", sources=["src/Controller.cpp"]
        )
        app = project.QtProgram(
            "app", env, sources=["src/main.cpp"], no_moc=["src/Controller.hpp"]
        )
        app.link(module)

        with caplog.at_level(logging.WARNING):
            project.resolve()

        assert "Helper.hpp" in caplog.text


class TestNoFalsePositive:
    def test_a_split_that_mocs_each_header_once_is_quiet(
        self, tmp_path, monkeypatch, caplog
    ):
        monkeypatch.chdir(tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        (src / "Controller.hpp").write_text(_header("Controller"))
        (src / "Controller.cpp").write_text('#include "Controller.hpp"\n')
        (src / "Window.hpp").write_text(_header("Window"))
        (src / "main.cpp").write_text(
            '#include "Window.hpp"\nint main() { return 0; }\n'
        )

        project = Project("ok", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = cxx_env_with_qt(project)
        module = project.QtQmlModule(
            "mod", env, uri="a.b", sources=["src/Controller.cpp"]
        )
        app = project.QtProgram("app", env, sources=["src/main.cpp"])
        app.link(module)

        with caplog.at_level(logging.WARNING):
            project.resolve()

        assert "run moc on" not in caplog.text
        assert "Controller.hpp" not in caplog.text

    def test_two_targets_that_never_link_are_quiet(self, tmp_path, monkeypatch, caplog):
        """The same header moc'ed by two programs is two separate links."""
        monkeypatch.chdir(tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        (src / "Shared.hpp").write_text(_header("Shared"))
        (src / "one.cpp").write_text(
            '#include "Shared.hpp"\nint main() { return 0; }\n'
        )
        (src / "two.cpp").write_text(
            '#include "Shared.hpp"\nint main() { return 0; }\n'
        )

        project = Project("two", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = cxx_env_with_qt(project)
        project.QtProgram("one", env, sources=["src/one.cpp"])
        project.QtProgram("two", env, sources=["src/two.cpp"])

        with caplog.at_level(logging.WARNING):
            project.resolve()

        assert "run moc on" not in caplog.text
        assert "Shared.hpp" not in caplog.text
