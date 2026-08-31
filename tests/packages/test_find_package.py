# SPDX-License-Identifier: MIT
"""Tests for project.find_package() and add_package_finder().

Tests the high-level package discovery API that wraps the finder chain
and returns ImportedTarget objects.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pcons.core.errors import PackageNotFoundError
from pcons.core.project import Project
from pcons.packages.description import PackageDescription
from pcons.packages.finders.base import BaseFinder


class MockFinder(BaseFinder):
    """A test finder that returns pre-configured packages."""

    def __init__(self, packages: dict[str, PackageDescription] | None = None) -> None:
        self._packages = packages or {}

    @property
    def name(self) -> str:
        return "mock"

    def find(
        self,
        package_name: str,
        version: str | None = None,
        components: list[str] | None = None,
    ) -> PackageDescription | None:
        return self._packages.get(package_name)


class AlwaysUnavailableFinder(BaseFinder):
    """A finder that reports itself as unavailable."""

    @property
    def name(self) -> str:
        return "unavailable"

    def find(
        self,
        package_name: str,
        version: str | None = None,
        components: list[str] | None = None,
    ) -> PackageDescription | None:
        return None

    def is_available(self) -> bool:
        return False


def _make_pkg(
    name: str,
    version: str = "1.0.0",
    include_dirs: list[str] | None = None,
    libraries: list[str] | None = None,
) -> PackageDescription:
    return PackageDescription(
        name=name,
        version=version,
        include_dirs=include_dirs or [],
        library_dirs=[],
        libraries=libraries or [],
        defines=[],
        compile_flags=[],
        link_flags=[],
        prefix="",
        found_by="mock",
    )


class TestFindPackage:
    """Tests for project.find_package()."""

    def test_find_package_with_mock(self) -> None:
        """find_package should return an ImportedTarget."""
        project = Project("test")
        zlib = _make_pkg("zlib", include_dirs=["/usr/include"], libraries=["z"])
        project.add_package_finder(MockFinder({"zlib": zlib}))

        target = project.find_package("zlib")

        assert target is not None
        assert target.name == "zlib"
        # Should be registered as a project target
        assert project.get_target("zlib") is target

    def test_find_package_caching(self) -> None:
        """Repeated calls should return the same target."""
        project = Project("test")
        zlib = _make_pkg("zlib")
        project.add_package_finder(MockFinder({"zlib": zlib}))

        target1 = project.find_package("zlib")
        target2 = project.find_package("zlib")

        assert target1 is target2

    def test_find_package_not_found_required(self) -> None:
        """find_package should raise when required package not found."""
        project = Project("test")
        # Replace the entire finder chain with an empty mock so no
        # system finders can accidentally succeed
        from pcons.packages.finders.base import FinderChain

        project._package_finder_chain = FinderChain([MockFinder()])

        with pytest.raises(PackageNotFoundError, match="nonexistent-pkg-12345"):
            project.find_package("nonexistent-pkg-12345")

    def test_find_package_not_found_optional(self) -> None:
        """find_package should return None when optional package not found."""
        project = Project("test")
        from pcons.packages.finders.base import FinderChain

        project._package_finder_chain = FinderChain([MockFinder()])

        result = project.find_package("nonexistent-pkg-12345", required=False)

        assert result is None

    def test_find_package_with_version(self) -> None:
        """Version string should be passed to finders."""

        class VersionCheckFinder(BaseFinder):
            def __init__(self) -> None:
                self.received_version: str | None = None

            @property
            def name(self) -> str:
                return "version-check"

            def find(
                self,
                package_name: str,
                version: str | None = None,
                components: list[str] | None = None,
            ) -> PackageDescription | None:
                self.received_version = version
                return _make_pkg(package_name)

        project = Project("test")
        finder = VersionCheckFinder()
        project.add_package_finder(finder)

        project.find_package("openssl", version=">=3.0")

        assert finder.received_version == ">=3.0"

    def test_find_package_with_components(self) -> None:
        """Components should be passed to finders."""

        class ComponentCheckFinder(BaseFinder):
            def __init__(self) -> None:
                self.received_components: list[str] | None = None

            @property
            def name(self) -> str:
                return "component-check"

            def find(
                self,
                package_name: str,
                version: str | None = None,
                components: list[str] | None = None,
            ) -> PackageDescription | None:
                self.received_components = components
                return _make_pkg(package_name)

        project = Project("test")
        finder = ComponentCheckFinder()
        project.add_package_finder(finder)

        project.find_package("boost", components=["filesystem", "system"])

        assert finder.received_components == ["filesystem", "system"]

    def test_find_package_different_args_not_cached(self) -> None:
        """Different version/components should not return cached result."""
        project = Project("test")

        call_count = 0

        class CountingFinder(BaseFinder):
            @property
            def name(self) -> str:
                return "counting"

            def find(
                self,
                package_name: str,
                version: str | None = None,
                components: list[str] | None = None,
            ) -> PackageDescription | None:
                nonlocal call_count
                call_count += 1
                # Return unique packages with names based on args to avoid
                # target name collision
                suffix = f"_{version or 'none'}_{'-'.join(components or [])}"
                return _make_pkg(f"{package_name}{suffix}")

        project.add_package_finder(CountingFinder())

        project.find_package("pkg", version="1.0")
        project.find_package("pkg", version="2.0")

        assert call_count == 2


class TestFindPackageSystem:
    """find_package(system=True): the package's headers arrive as -isystem."""

    def test_it_moves_the_include_dirs(self) -> None:
        project = Project("test")
        doctest = _make_pkg("doctest", include_dirs=["/opt/doctest/include"])
        project.add_package_finder(MockFinder({"doctest": doctest}))

        target = project.find_package("doctest", system=True)

        assert target is not None
        assert list(target.public.include_dirs) == []
        assert list(target.public.system_include_dirs) == [Path("/opt/doctest/include")]

    def test_it_is_off_by_default(self) -> None:
        project = Project("test")
        zlib = _make_pkg("zlib", include_dirs=["/usr/include"])
        project.add_package_finder(MockFinder({"zlib": zlib}))

        target = project.find_package("zlib")

        assert target is not None
        assert list(target.public.include_dirs) == [Path("/usr/include")]

    def test_the_same_package_cannot_be_had_both_ways(self) -> None:
        project = Project("test")
        doctest = _make_pkg("doctest", include_dirs=["/opt/doctest/include"])
        project.add_package_finder(MockFinder({"doctest": doctest}))

        project.find_package("doctest")

        with pytest.raises(ValueError, match="already found with system=False"):
            project.find_package("doctest", system=True)

    def test_a_package_that_was_never_found_conflicts_with_nothing(self) -> None:
        """A cached None owns no target, so it cannot collide with one."""
        project = Project("test")
        doctest = _make_pkg("doctest", include_dirs=["/opt/doctest/include"])
        project.add_package_finder(MockFinder({"doctest": doctest}))

        assert project.find_package("missing", required=False) is None

        target = project.find_package("missing", system=True, required=False)
        assert target is None

    def test_it_still_caches(self) -> None:
        project = Project("test")
        doctest = _make_pkg("doctest", include_dirs=["/opt/doctest/include"])
        project.add_package_finder(MockFinder({"doctest": doctest}))

        first = project.find_package("doctest", system=True)
        second = project.find_package("doctest", system=True)

        assert first is second


class TestAddPackageFinder:
    """Tests for project.add_package_finder()."""

    def test_custom_finder_tried_first(self) -> None:
        """Custom finders should be tried before defaults."""
        project = Project("test")
        custom_pkg = _make_pkg("custom-lib", version="9.9.9")
        project.add_package_finder(MockFinder({"custom-lib": custom_pkg}))

        target = project.find_package("custom-lib")
        assert target is not None
        assert target.name == "custom-lib"

    def test_multiple_custom_finders(self) -> None:
        """Multiple custom finders should be tried in order."""
        project = Project("test")

        finder1 = MockFinder({"from-finder1": _make_pkg("from-finder1")})
        finder2 = MockFinder({"from-finder2": _make_pkg("from-finder2")})

        project.add_package_finder(finder1)
        project.add_package_finder(finder2)

        # finder2 was added last, so it should be first in the chain
        target = project.find_package("from-finder2")
        assert target is not None

    def test_unavailable_finder_skipped(self) -> None:
        """Unavailable finders should be skipped."""
        project = Project("test")
        project.add_package_finder(AlwaysUnavailableFinder())
        project.add_package_finder(
            MockFinder({"available-pkg": _make_pkg("available-pkg")})
        )

        target = project.find_package("available-pkg")
        assert target is not None


class TestPackageNotFoundError:
    """Tests for the PackageNotFoundError exception."""

    def test_basic_message(self) -> None:
        err = PackageNotFoundError("zlib")
        assert "zlib" in str(err)

    def test_with_version(self) -> None:
        err = PackageNotFoundError("openssl", version=">=3.0")
        assert "openssl" in str(err)
        assert ">=3.0" in str(err)


class CountingMockFinder(MockFinder):
    """A MockFinder that records how many lookups reached it."""

    def __init__(self, packages: dict[str, PackageDescription] | None = None) -> None:
        super().__init__(packages)
        self.calls = 0

    def find(
        self,
        package_name: str,
        version: str | None = None,
        components: list[str] | None = None,
    ) -> PackageDescription | None:
        self.calls += 1
        return super().find(package_name, version, components)


class TestFindPackagePerEnvironment:
    """A found package belongs to one environment, not to the project."""

    def test_two_environments_get_two_targets(self) -> None:
        project = Project("test")
        mcu = project.Environment(name="mcu")
        host = project.Environment(name="host")
        finder = CountingMockFinder({"zlib": _make_pkg("zlib")})
        project.add_package_finder(finder)

        for_mcu = project.find_package("zlib", env=mcu)
        for_host = project.find_package("zlib", env=host)

        assert for_mcu is not None
        assert for_host is not None
        assert for_mcu is not for_host
        assert for_mcu.qualified_name.endswith("zlib@mcu")
        assert for_host.qualified_name.endswith("zlib@host")
        assert finder.calls == 2

    def test_one_environment_twice_is_one_target(self) -> None:
        project = Project("test")
        mcu = project.Environment(name="mcu")
        finder = CountingMockFinder({"zlib": _make_pkg("zlib")})
        project.add_package_finder(finder)

        first = project.find_package("zlib", env=mcu)
        second = project.find_package("zlib", env=mcu)

        assert first is second
        assert finder.calls == 1

    def test_no_environment_resolves_to_the_inherited_one(self) -> None:
        """An omitted env must key the same slot the target inherits."""
        project = Project("test")
        host = project.Environment(name="host")
        finder = CountingMockFinder({"zlib": _make_pkg("zlib")})
        project.add_package_finder(finder)

        implicit = project.find_package("zlib")
        explicit = project.find_package("zlib", env=host)

        assert implicit is explicit
        assert finder.calls == 1

    def test_a_named_and_an_unnamed_environment_collide(self) -> None:
        project = Project("test")
        unnamed = project.Environment()
        named = project.Environment(name="host")
        project.add_package_finder(MockFinder({"zlib": _make_pkg("zlib")}))

        project.find_package("zlib", env=unnamed)

        with pytest.raises(ValueError, match="already exists"):
            project.find_package("zlib", env=named)

    def test_a_negative_result_is_cached_per_environment(self) -> None:
        """Not found in one environment says nothing about another."""
        project = Project("test")
        mcu = project.Environment(name="mcu")
        host = project.Environment(name="host")
        finder = CountingMockFinder({})
        project.add_package_finder(finder)
        missing = "nonexistent-pkg-12345"

        assert project.find_package(missing, env=mcu, required=False) is None
        assert finder.calls == 1

        assert project.find_package(missing, env=host, required=False) is None
        assert finder.calls == 2

        assert project.find_package(missing, env=mcu, required=False) is None
        assert finder.calls == 2

    def test_the_system_conflict_is_per_environment(self) -> None:
        project = Project("test")
        mcu = project.Environment(name="mcu")
        host = project.Environment(name="host")
        project.add_package_finder(
            MockFinder({"doctest": _make_pkg("doctest", include_dirs=["/opt/dt"])})
        )

        project.find_package("doctest", env=mcu)
        project.find_package("doctest", env=host, system=True)

        with pytest.raises(ValueError, match="in environment 'mcu'"):
            project.find_package("doctest", env=mcu, system=True)
