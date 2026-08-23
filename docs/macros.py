# SPDX-License-Identifier: MIT
"""MkDocs macros hook — exposes template variables in markdown.

Variables:
    {{ version }}          — version string with optional git dev info
    {{ toolchain_table }}  — auto-generated markdown table of registered toolchains
    {{ builder_table }}    — auto-generated markdown table of registered builders
    {{ source_types_table }} — auto-generated markdown table of source file types
"""

import logging
import re
import subprocess
import sys
from pathlib import Path


def _get_version() -> str:
    """Get version string, with git info for unreleased builds.

    On a tagged release:  "0.6.0"
    Past a tag:           "0.6.0.dev3 (g9abe7cc, 2026-01-30)"
    No tags at all:       "0.6.0.dev (9abe7cc, 2026-01-30)"
    Git unavailable:      "0.6.0"
    """
    # Parse version from pcons/__init__.py without importing
    init_file = Path(__file__).parent.parent / "pcons" / "__init__.py"
    version = "unknown"
    for line in init_file.read_text().splitlines():
        m = re.match(r'^__version__\s*=\s*["\']([^"\']+)["\']', line)
        if m:
            version = m.group(1)
            break

    # Try git describe to detect unreleased commits
    try:
        desc = subprocess.check_output(
            ["git", "describe", "--tags", "--long", "--always"],
            cwd=Path(__file__).parent.parent,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        # git describe --long gives "v0.6.0-3-g9abe7cc" or just "9abe7cc"
        m = re.match(r"v?[\d.]+-(\d+)-g([0-9a-f]+)", desc)
        if m:
            commits_past = int(m.group(1))
            short_hash = m.group(2)
            if commits_past > 0:
                # Get commit date
                date = subprocess.check_output(
                    ["git", "log", "-1", "--format=%cs"],
                    cwd=Path(__file__).parent.parent,
                    stderr=subprocess.DEVNULL,
                    text=True,
                ).strip()
                return f"{version}.dev{commits_past} ({short_hash}, {date})"
        # else: exactly on a tag, just use __version__
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    return version


# ── Platform name mapping ────────────────────────────────────────────────────

_PLATFORM_DISPLAY = {
    "linux": "Linux",
    "darwin": "macOS",
    "win32": "Windows",
}


def _format_platforms(platforms: list[str]) -> str:
    """Convert sys.platform values to human-readable names."""
    if not platforms:
        return "Any"
    return ", ".join(_PLATFORM_DISPLAY.get(p, p) for p in platforms)


# ── Toolchain table ──────────────────────────────────────────────────────────


def _get_toolchain_table() -> str:
    """Generate a markdown table of all registered toolchains."""
    rows: list[dict[str, str]] = []

    for entry in _all_toolchain_entries():
        finder = f"`{entry.finder}`" if entry.finder else ""
        rows.append(
            {
                "name": entry.toolchain_class.__name__.removesuffix("Toolchain"),
                "aliases": ", ".join(f"`{a}`" for a in entry.aliases),
                "category": entry.category,
                "check_command": f"`{entry.check_command}`",
                "platforms": _format_platforms(entry.platforms),
                "description": entry.description,
                "finder": finder,
            }
        )

    # Sort: C toolchains first (GCC/LLVM before MSVC/Clang-CL), then others
    category_order = {"c": 0, "cuda": 1, "wasm": 2, "python": 3}
    # Within C category, prefer well-known names first
    c_name_order = {"Gcc": 0, "Llvm": 1, "Msvc": 2, "ClangCl": 3}
    rows.sort(
        key=lambda r: (
            category_order.get(r["category"], 99),
            c_name_order.get(r["name"], 99),
            r["name"],
        )
    )

    # Build markdown table
    lines = [
        "| Toolchain | Finder | Platforms | Description |",
        "|-----------|--------|-----------|-------------|",
    ]
    for r in rows:
        lines.append(
            f"| **{r['name']}** | {r['finder']} "
            f"| {r['platforms']} | {r['description']} |"
        )

    return "\n".join(lines)


# ── Builder table ────────────────────────────────────────────────────────────


def _get_builder_table() -> str:
    """Generate a markdown table of all registered builders."""
    pcons_root = Path(__file__).parent.parent
    if str(pcons_root) not in sys.path:
        sys.path.insert(0, str(pcons_root))

    from pcons.core.builder_registry import BuilderRegistry

    rows: list[dict[str, str]] = []
    for name, reg in sorted(BuilderRegistry.all().items()):
        # Clean up the description: first sentence only
        desc = reg.description.strip().split("\n")[0].rstrip(".")
        platforms = _format_platforms(reg.platforms) if reg.platforms else "All"
        rows.append(
            {
                "name": name,
                "target_type": reg.target_type.replace("_", " ").title(),
                "platforms": platforms,
                "description": desc,
            }
        )

    lines = [
        "| Builder | Type | Platforms | Description |",
        "|---------|------|-----------|-------------|",
    ]
    for r in rows:
        # Use non-breaking spaces in method name to prevent wrapping
        method = f"project.{r['name']}()"
        lines.append(
            f"| `{method}` | {r['target_type']} "
            f"| {r['platforms']} | {r['description']} |"
        )

    return "\n".join(lines)


# ── Source file type table ───────────────────────────────────────────────────

# Suffixes a toolchain's builders accept that are not *source* file types:
# link inputs, compiler intermediates, and files a scanner reads.
_NOT_SOURCE_SUFFIXES = {
    ".o",
    ".obj",
    ".a",
    ".lib",
    ".res",
    ".air",
    ".so",
    ".dll",
    ".dylib",
    ".h",
    ".hh",
    ".hpp",
    ".hxx",
    ".json",
}

# Documented source types, in table order. A suffix a toolchain reports that
# appears neither here nor in _NOT_SOURCE_SUFFIXES is rendered as an
# UNDOCUMENTED row and warned about, so a new source type cannot reach users
# without the docs build noticing.
_SOURCE_DESCRIPTIONS: dict[str, str] = {
    ".c": "C source",
    ".cpp": "C++ source",
    ".cxx": "C++ source",
    ".cc": "C++ source",
    ".c++": "C++ source",
    ".C": "C++ source",
    ".cppm": "C++20 module interface unit",
    ".ixx": "C++20 module interface unit",
    ".cxxm": "C++20 module interface unit",
    ".c++m": "C++20 module interface unit",
    ".m": "Objective-C",
    ".mm": "Objective-C++",
    ".s": "Assembly (preprocessed)",
    ".S": "Assembly (needs C preprocessor)",
    ".asm": "MASM assembly",
    ".rc": "Windows resource",
    ".cu": "CUDA source",
    ".f": "Fortran, fixed form",
    ".for": "Fortran, fixed form",
    ".ftn": "Fortran, fixed form",
    ".f90": "Fortran, free form",
    ".f95": "Fortran, free form",
    ".f03": "Fortran, free form",
    ".f08": "Fortran, free form",
    ".f18": "Fortran, free form",
    ".F": "Fortran, needs C preprocessor",
    ".F90": "Fortran, needs C preprocessor",
    ".swift": "Swift source",
    ".pyx": "Cython source",
    ".metal": "Metal shader (macOS)",
    ".ui": "Qt Designer form",
    ".qrc": "Qt resource collection",
    ".ts": "Qt translation source",
    ".tex": "LaTeX document",
}

# Display names, in the order toolchains are listed within a cell.
_TOOLCHAIN_DISPLAY = {
    "Gcc": "GCC",
    "Llvm": "LLVM",
    "Msvc": "MSVC",
    "ClangCl": "Clang-CL",
    "Emscripten": "Emscripten",
    "Wasi": "WASI",
    "Cuda": "CUDA",
    "Gfortran": "gfortran",
    "Swift": "Swift",
    "Cython": "Cython",
    "Qt": "Qt",
    "Latex": "LaTeX",
}


def _all_toolchain_entries() -> list:
    """Every registered toolchain, one entry per class, lazy ones materialized."""
    import importlib

    pcons_root = Path(__file__).parent.parent
    if str(pcons_root) not in sys.path:
        sys.path.insert(0, str(pcons_root))

    from pcons.tools.toolchain import toolchain_registry

    for lazy in set(toolchain_registry.lazy_declarations().values()):
        try:
            importlib.import_module(lazy.module)
        except ImportError as exc:
            logging.getLogger("mkdocs.macros").warning(
                "macros: toolchain module %s failed to import (%s); it will be "
                "missing from the generated tables",
                lazy.module,
                exc,
            )
    import pcons.contrib.latex.toolchain  # noqa: F401

    seen: set[type] = set()
    entries = []
    for entry in toolchain_registry._toolchains.values():
        if entry.toolchain_class not in seen:
            seen.add(entry.toolchain_class)
            entries.append(entry)
    return entries


def _collect_source_suffixes() -> dict[str, set[str]]:
    """Map each source suffix to the toolchains that compile it.

    Toolchains answer in one of two ways, so both are consulted:

    - `get_source_handler()`, the resolver's own dispatch. A suffix counts
      only when the handling tool belongs to this toolchain — the compiler
      drivers accept far more than they own (gfortran compiles C), and
      listing that would be noise.
    - builder `src_suffixes`, for toolchains that dispatch purely through
      builders (Cython, Qt, LaTeX) and implement no handler. Suffixes another
      toolchain already claims are skipped, so Qt's moc reading `.cpp` does
      not make Qt a C++ compiler.
    """
    suffixes: dict[str, set[str]] = {}
    entries = _all_toolchain_entries()
    by_handler: list = []

    for entry in entries:
        name = entry.toolchain_class.__name__.removesuffix("Toolchain")
        display = _TOOLCHAIN_DISPLAY.get(name, name)
        toolchain = entry.create_toolchain()
        own_tools = set(toolchain.tools)
        owned = {
            suffix
            for suffix in toolchain.source_suffixes()
            if (h := toolchain.get_source_handler(suffix)) and h.tool_name in own_tools
        }
        if owned:
            for suffix in owned:
                suffixes.setdefault(suffix, set()).add(display)
        else:
            by_handler.append((display, toolchain))

    claimed = set(suffixes)
    for display, toolchain in by_handler:
        for tool in toolchain.tools.values():
            for builder in tool.builders().values():
                for suffix in builder.src_suffixes:
                    if suffix not in claimed and suffix not in _NOT_SOURCE_SUFFIXES:
                        suffixes.setdefault(suffix, set()).add(display)

    return suffixes


def _get_source_types_table() -> str:
    """Generate a markdown table of source file types, grouped by description."""
    import logging

    found = _collect_source_suffixes()
    log = logging.getLogger("mkdocs.macros")

    undocumented = sorted(set(found) - set(_SOURCE_DESCRIPTIONS))
    if undocumented:
        log.warning(
            "macros: source suffixes handled but not documented: %s "
            "— add them to _SOURCE_DESCRIPTIONS (or _NOT_SOURCE_SUFFIXES) "
            "in docs/macros.py",
            ", ".join(undocumented),
        )
    stale = sorted(set(_SOURCE_DESCRIPTIONS) - set(found))
    if stale:
        log.warning(
            "macros: documented source suffixes no toolchain handles: %s "
            "— remove them from _SOURCE_DESCRIPTIONS in docs/macros.py",
            ", ".join(stale),
        )

    def cell(names: set[str]) -> str:
        ordered = sorted(
            names,
            key=lambda n: (
                list(_TOOLCHAIN_DISPLAY.values()).index(n)
                if n in _TOOLCHAIN_DISPLAY.values()
                else 99
            ),
        )
        return ", ".join(ordered)

    # Group consecutive documented suffixes sharing a description and toolchains
    rows: list[tuple[list[str], str, str]] = []
    for suffix, description in _SOURCE_DESCRIPTIONS.items():
        if suffix not in found:
            continue
        toolchains = cell(found[suffix])
        if rows and rows[-1][1] == description and rows[-1][2] == toolchains:
            rows[-1][0].append(suffix)
        else:
            rows.append(([suffix], description, toolchains))

    for suffix in undocumented:
        rows.append(
            ([suffix], "**UNDOCUMENTED — see docs/macros.py**", cell(found[suffix]))
        )

    lines = [
        "| Extension | Description | Toolchains |",
        "|-----------|-------------|------------|",
    ]
    for group, description, toolchains in rows:
        exts = ", ".join(f"`{s}`" for s in group)
        lines.append(f"| {exts} | {description} | {toolchains} |")
    return "\n".join(lines)


# ── MkDocs entry point ───────────────────────────────────────────────────────


def define_env(env):
    """Define template variables for mkdocs-macros."""
    env.variables["version"] = _get_version()
    env.variables["toolchain_table"] = _get_toolchain_table()
    env.variables["builder_table"] = _get_builder_table()
    env.variables["source_types_table"] = _get_source_types_table()
