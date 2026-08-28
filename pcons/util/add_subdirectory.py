import runpy
from pathlib import Path
from types import SimpleNamespace
from typing import overload

from pcons.core.environment import Environment as Env
from pcons.core.invocation import RUN_NAME
from pcons.core.project import Project


@overload
def add_subdirectory(
    subdir: str | Path,
    pick: list[str],
    *,
    project: Project | None = None,
    env: Env | None = None,
) -> tuple: ...


@overload
def add_subdirectory(
    subdir: str | Path,
    pick: None = None,
    *,
    project: Project | None = None,
    env: Env | None = None,
) -> SimpleNamespace: ...


def add_subdirectory(
    subdir: str | Path,
    pick: list[str] | None = None,
    *,
    project: Project | None = None,
    env: Env | None = None,
) -> tuple | SimpleNamespace:
    """Adds a subdirectory to the project.

    Looks for a ``pcons-build.py`` file in the specified subdirectory and
    executes it in the context of the current project.
    Any name assigned at module scope in that script is *exported*: it becomes
    an attribute of the returned ``SimpleNamespace``, so callers can write ``ns.my_lib``
    instead of looking up targets by string.

    Example:

        # subdir/pcons-build.py
        my_lib = project.StaticLibrary("my_lib", env, sources=["lib.c"])

        # parent pcons-build.py
        sub = add_subdirectory("subdir")
        app.link(sub.my_lib)

        # Or, with pick:
        my_lib, = add_subdirectory("subdir", pick=["my_lib"])
        app.link(my_lib)

    Args:
        subdir: The subdirectory, relative to the anchoring project's
            current directory.
        pick: Names to return from the subdirectory script, instead of
            everything.
        project: The project to add the subdirectory to. Defaults to the
            current project; in a script with several top-level projects,
            name the one you mean (or call ``project.add_subdirectory()``).
        env: The environment the included tree builds in. It becomes the
            default environment for the duration of the call, so a script
            reading ``project.parent.default_environment`` gets it. Include
            the same directory once per environment to build it twice::

                add_subdirectory("sub", env=host)
                add_subdirectory("sub", env=mcu)

            The two environments need different ``build_prefix`` values,
            and both must be named, or their targets collide.

    Returns:
        - If ``pick`` is not specified, a ``SimpleNamespace`` whose attributes
          are all module-level names defined in the subdirectory script.
        - If ``pick`` is specified, a tuple containing only the listed names
          (in order), e.g. ``lib, hdr = add_subdirectory("sub", pick=["lib", "hdr"])``.
    """
    if project is None:
        project = Project.current()
    subdir_path = project.current_dir / subdir
    script = subdir_path / "pcons-build.py"
    if not script.exists():
        raise FileNotFoundError(f"No pcons-build.py found in {subdir_path}")

    # runpy doesn't touch sys.modules, so the automatic scan can't see this
    # script; register it directly or editing it wouldn't re-run pcons.
    project.add_configure_dependency(script)

    with project._enter_subdir(subdir, env=env):
        module = runpy.run_path(str(script), run_name=RUN_NAME)
        if pick is not None:
            return tuple(module[name] for name in pick)
        else:
            return SimpleNamespace(**module)
