# SPDX-License-Identifier: MIT
"""C++20 module dependency scanner for Ninja dyndep.

Supports three scanner styles:
- "clang": uses clang-scan-deps (P1689R5 format)
- "msvc":  uses cl.exe /scanDependencies <file> (P1689R5 format)
- "gcc":   uses g++ with -fdeps-format=p1689r5 and reads a deps JSON file

The scan runs twice, for two different consumers:

- At configure time, inline in each toolchain's ``after_resolve``: the scan
  output drives flag injection (``-x c++-module``, ``/interface`` vs
  ``/internalPartition``, keyed BMI outputs), which only configure can do —
  Ninja dyndep can modify deps and outputs, never flags.
- At build time, as the edge that writes ``cxx_modules.dyndep``, run as::

      python -m pcons.toolchains.cxx_module_scanner \\
          --manifest cxx_modules.manifest.json --out cxx_modules.dyndep

  from the build directory, against a manifest configure wrote. A TU's
  import set is a function of the TU *and every header it includes*, and
  headers are not configure dependencies — so the dyndep is recomputed
  where header edits can re-trigger it, and a header that gains an
  ``import`` reorders the build without re-running pcons. The scan cache
  makes the second run cheap: a TU none of whose prerequisites changed is
  not rescanned.

All paths in the output are relative to the build directory (where Ninja
runs).
"""

from __future__ import annotations

# argparse, not click: a build-edge subprocess, where click costs ~14ms of
# import per invocation. The CLI here is internal, typed only by pcons's
# own generators.
import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from pcons.toolchains._scan_cache import ScanCache, compiler_binary, parse_depfile

logger = logging.getLogger(__name__)


class CxxModuleScannerNotFound(RuntimeError):
    """Raised when the C++ module scanner executable is not on PATH.

    Let this propagate so configure fails loudly instead of producing
    empty/silent scans.
    """


def _write_text_if_changed(path: Path, text: str) -> None:
    """Write *text* to *path* only when content differs.

    Fast-path no-op uses a matching ``<path>.sha256`` digest file.
    If the digest file is missing or stale, use a size check first and
    only fall back to a byte-for-byte compare for equal-size candidates.
    """
    data = text.encode("utf-8")
    digest = hashlib.sha256(data).digest()
    digest_file = path.with_suffix(path.suffix + ".sha256")

    if path.exists():
        if (
            path.stat().st_size == len(data)
            and digest_file.exists()
            and digest_file.read_bytes() == digest
        ):
            return

    path.write_bytes(data)
    digest_file.write_bytes(digest)


def clang_scan_command(
    scanner: str,
    fmt: str,
    compiler: str,
    compile_flags: list[str],
    src: str,
    obj: str,
) -> list[str]:
    """The argv that scans one TU with clang-scan-deps, in *fmt*.

    Run twice per TU: ``p1689`` for the module graph, then ``make`` for the
    files the TU reads — clang-scan-deps emits no file inputs in its p1689
    output, and the prerequisite list is what decides when a cached result,
    and the dyndep built from it, go stale.
    """
    return [
        scanner,
        f"-format={fmt}",
        "--",
        compiler,
        *compile_flags,
        "-c",
        src,
        "-o",
        obj,
    ]


def run_scan_deps(
    scanner: str,
    compiler: str,
    compile_flags: list[str],
    src: str,
    obj: str,
    prereqs_out: list[str] | None = None,
) -> dict[str, Any] | None:
    """Run clang-scan-deps on a single source file and return P1689R5 JSON.

    Args:
        scanner: Path/name of the clang-scan-deps executable.
        compiler: Compiler command (e.g., "clang++").
        compile_flags: List of compiler flags.
        src: Absolute path to the source file.
        obj: Object file path (relative to build dir).
        prereqs_out: When given, extended with every file the TU reads, from
            a second, make-format scan. Left alone when either scan fails —
            an empty list tells the caller there is nothing to cache by.

    Returns:
        Parsed P1689R5 JSON dict, or None on failure.
    """
    cmd = clang_scan_command(scanner, "p1689", compiler, compile_flags, src, obj)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(
            f"Warning: clang-scan-deps failed for {src}: {e.stderr}",
            file=sys.stderr,
        )
        return None
    except FileNotFoundError as e:
        raise CxxModuleScannerNotFound(
            f"C++ module scanner '{scanner}' not found on PATH.\n"
            "  C++20 modules require clang-scan-deps (shipped with LLVM/Clang).\n"
            "  Install hints:\n"
            "    macOS:        brew install llvm  (then add the keg's bin to PATH)\n"
            "    Ubuntu/Deb:   apt install clang-tools  (or a recent LLVM via apt.llvm.org)\n"
            "    Fedora/RHEL:  dnf install clang-tools-extra\n"
            "    Windows:      winget install LLVM.LLVM  (or use the LLVM installer)\n"
            "  Or set env.cxx.scan_deps to the full path of your clang-scan-deps."
        ) from e

    try:
        p1689 = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(
            f"Warning: could not parse clang-scan-deps output for {src}: {e}",
            file=sys.stderr,
        )
        return None

    if prereqs_out is not None:
        deps = subprocess.run(
            clang_scan_command(scanner, "make", compiler, compile_flags, src, obj),
            capture_output=True,
            text=True,
        )
        if deps.returncode == 0:
            prereqs_out.extend(parse_depfile(deps.stdout))
        else:
            logger.debug("clang-scan-deps make-format scan failed for %s", src)
    return p1689


def msvc_scan_command(
    compiler: str,
    compile_flags: list[str],
    src: str,
    scan_json: str,
    deps_json: str,
) -> list[str]:
    """The argv that scans one TU with cl.exe.

    One invocation, two outputs: ``/scanDependencies`` writes the P1689
    module graph, ``/sourceDependencies`` the files the TU reads — the
    latter is what decides when a cached result, and the dyndep built from
    it, go stale.
    """
    return [
        compiler,
        "/scanDependencies",
        scan_json,
        "/sourceDependencies",
        deps_json,
        *compile_flags,
        src,
    ]


def run_scan_deps_msvc(
    compiler: str,
    compile_flags: list[str],
    src: str,
    prereqs_out: list[str] | None = None,
) -> dict[str, Any] | None:
    """Run cl.exe /scanDependencies on a single source file and return P1689R5 JSON.

    Args:
        compiler: Path/name of cl.exe.
        compile_flags: List of compiler flags (e.g. ["/nologo", "/std:c++20"]).
        src: Absolute path to the source file.
        prereqs_out: When given, extended with every file the TU reads. cl
            reports them in the same invocation via ``/sourceDependencies``,
            and they are exactly what decides whether a cached result is
            still good. Left alone on a failed scan.

    Returns:
        Parsed P1689R5 JSON dict, or None on failure.
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        deps_path = f.name

    try:
        cmd = msvc_scan_command(compiler, compile_flags, src, tmp_path, deps_path)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(
                f"Warning: cl.exe /scanDependencies failed for {src}: {result.stderr}",
                file=sys.stderr,
            )
            return None
        try:
            p1689 = json.loads(Path(tmp_path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(
                f"Warning: could not parse cl.exe /scanDependencies output for {src}: {e}",
                file=sys.stderr,
            )
            return None
        if prereqs_out is not None:
            try:
                data = json.loads(Path(deps_path).read_text(encoding="utf-8-sig"))
                version = str(data.get("Version", ""))
                if not version.startswith("1."):
                    # An unknown layout could parse and yield an incomplete
                    # list, which would go stale silently. Better an uncached
                    # scan than a prerequisite list this code has never seen.
                    logger.debug(
                        "Unknown /sourceDependencies version %r for %s; not caching",
                        version,
                        src,
                    )
                else:
                    payload = data.get("Data", {})
                    found = [payload.get("Source", src) or src]
                    includes = payload.get("Includes", [])
                    if isinstance(includes, list):
                        found.extend(str(p) for p in includes)
                    prereqs_out.extend(found)
            except (OSError, ValueError) as e:
                # No prerequisites means no way to tell when this result goes
                # stale, so the caller must not cache it.
                logger.debug("No /sourceDependencies output for %s: %s", src, e)
        return p1689
    except FileNotFoundError as e:
        raise CxxModuleScannerNotFound(
            f"MSVC compiler '{compiler}' not found on PATH.\n"
            "  C++20 module scanning needs cl.exe to invoke /scanDependencies.\n"
            "  On Windows, run a Visual Studio Developer Command Prompt, or\n"
            '  source vcvars64.bat (e.g. "C:\\Program Files\\Microsoft Visual\n'
            '  Studio\\2022\\Community\\VC\\Auxiliary\\Build\\vcvars64.bat") in\n'
            "  the shell that invokes pcons-build.py — that puts cl.exe and\n"
            "  the rest of the MSVC toolchain on PATH for the configure step."
        ) from e
    finally:
        Path(tmp_path).unlink(missing_ok=True)
        Path(deps_path).unlink(missing_ok=True)


def gcc_scan_command(
    compiler: str,
    compile_flags: list[str],
    src: str,
    obj: str,
    deps_json: str,
    depfile: str,
) -> list[str]:
    """The argv that scans one TU with GCC.

    ``-fdirectives-only`` is here because only the module declarations are
    wanted, so the preprocessor does not have to expand every macro in the
    translation unit. Worth 32% of the scan on a real C++26 project, and it
    cannot change the build: this flag is on the scan command only, never on a
    compile line, so no BMI is produced with it and no BMI-compatibility key
    sees it (`bmi_key_for_flags` hashes a TU's compile flags).

    ``-o os.devnull`` because the scan wants the p1689 JSON, not the
    preprocessed text, and ``-E`` writes megabytes of it per TU: 3.2 MB to
    extract 91 bytes on a real C++26 source. ``os.devnull`` rather than a
    literal, since this runner is used on Windows too, where it is NUL.
    """
    return [
        compiler,
        *compile_flags,
        "-E",
        "-x",
        "c++",
        src,
        "-MT",
        obj,
        "-MD",
        "-MF",
        depfile,
        "-fmodules",
        f"-fdeps-file={deps_json}",
        f"-fdeps-target={obj}",
        "-fdeps-format=p1689r5",
        "-fdirectives-only",
        "-o",
        os.devnull,
    ]


@lru_cache(maxsize=3)
def scan_recipe(scanner_style: str) -> str:
    """What a style's scan command is, with everything per-TU left out.

    The scan cache keys on this, so a pcons that scans differently asks a new
    question instead of trusting an answer the old command produced. Derived
    from the commands themselves rather than hand-maintained constants: a
    flag added there cannot be forgotten here.
    """
    if scanner_style == "gcc":
        return "\0".join(gcc_scan_command("", [], "", "", "", ""))
    if scanner_style == "msvc":
        return "\0".join(msvc_scan_command("", [], "", "", ""))
    return "\0".join(
        clang_scan_command("", "p1689", "", [], "", "")
        + clang_scan_command("", "make", "", [], "", "")
    )


def run_scan_deps_gcc(
    compiler: str,
    compile_flags: list[str],
    src: str,
    obj: str,
    prereqs_out: list[str] | None = None,
) -> dict[str, Any] | None:
    """Run GCC p1689 scan and return parsed JSON.

    Args:
        compiler: Path/name of g++.
        compile_flags: List of compiler flags (e.g. ["-std=c++23"]).
        src: Absolute path to the source file.
        obj: Object file path relative to the build directory.
        prereqs_out: When given, extended with every file the scan read. GCC
            writes them because the command already asks for a depfile, and
            they are exactly what decides whether a cached result is still
            good. Left alone on a failed scan.

    Returns:
        Parsed P1689R5 JSON dict, or None on failure.
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f_deps:
        deps_json = f_deps.name
    with tempfile.NamedTemporaryFile(suffix=".d", delete=False) as f_depfile:
        depfile = f_depfile.name

    try:
        cmd = gcc_scan_command(compiler, compile_flags, src, obj, deps_json, depfile)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(
                f"Warning: GCC p1689 scan failed for {src}: {result.stderr}",
                file=sys.stderr,
            )
            return None

        try:
            p1689 = json.loads(Path(deps_json).read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(
                f"Warning: could not parse GCC p1689 output for {src}: {e}",
                file=sys.stderr,
            )
            return None
        except OSError as e:
            print(
                f"Warning: could not read GCC p1689 output for {src}: {e}",
                file=sys.stderr,
            )
            return None

        if prereqs_out is not None:
            try:
                prereqs_out.extend(
                    parse_depfile(Path(depfile).read_text(encoding="utf-8"))
                )
            except OSError as e:
                # No depfile means no way to tell when this result goes stale,
                # so the caller must not cache it. An empty list says that.
                logger.debug("No depfile for %s: %s", src, e)
        return p1689
    except FileNotFoundError as e:
        raise CxxModuleScannerNotFound(
            f"GCC compiler '{compiler}' not found on PATH.\n"
            "  C++20 module scanning needs g++ with p1689 support.\n"
            "  Install hints:\n"
            "    Ubuntu/Deb:   apt install g++\n"
            "    Fedora/RHEL:  dnf install gcc-c++\n"
            "    macOS:        brew install gcc"
        ) from e
    finally:
        Path(deps_json).unlink(missing_ok=True)
        Path(depfile).unlink(missing_ok=True)


# =============================================================================
# Configure-time API: TU scan specs, results, manifest, and the dyndep edge.
#
# Toolchains' after_resolve() invokes the scanner inline so its output can
# drive flag injection (e.g. /internalPartition) — Ninja dyndep can only
# modify deps/outputs, not flags. The dyndep file itself is written at build
# time by regenerate_dyndep() (the `python -m` entry point below), so header
# edits that change a TU's imports re-trigger it without re-running pcons.
# =============================================================================

MANIFEST_FILE = "cxx_modules.manifest.json"


@dataclass
class TuScanSpec:
    """Inputs to scan a single translation unit.

    Attributes:
        src: Absolute path to the source file.
        obj_rel: Object file path relative to the build directory.
        compiler: Compiler executable (e.g., "clang++", "cl.exe").
        compile_flags: Compiler flags including any module-related flags
            that the scanner needs to see (-x c++-module, /interface, etc.).
    """

    src: Path
    obj_rel: str
    compiler: str
    compile_flags: list[str] = field(default_factory=list)


@dataclass
class TuScanResult:
    """Parsed P1689R5 scan output for a single translation unit.

    Properties expose the bits of the scan output that drive flag injection
    and dyndep generation, hiding the JSON shape.
    """

    spec: TuScanSpec
    p1689: dict[str, Any] | None  # None if the scan failed

    @property
    def _primary_provides(self) -> dict[str, Any] | None:
        """First entry in rules[0].provides, or None if this isn't a module-providing TU."""
        if self.p1689 is None:
            return None
        rules = self.p1689.get("rules", [])
        if not isinstance(rules, list) or not rules:
            return None
        first_rule = rules[0]
        if not isinstance(first_rule, dict):
            return None
        provides = first_rule.get("provides", [])
        if not isinstance(provides, list) or not provides:
            return None
        first = provides[0]
        return first if isinstance(first, dict) else None

    @property
    def is_module_provider(self) -> bool:
        """True if this TU produces a module (interface or partition impl)."""
        return self._primary_provides is not None

    @property
    def is_interface(self) -> bool:
        """True for primary interfaces and partition interfaces.

        False for internal partition implementation units (which on MSVC
        require the /internalPartition flag). Per P1689R5 the field defaults
        to True if absent.
        """
        prov = self._primary_provides
        if prov is None:
            return False
        return bool(prov.get("is-interface", True))

    @property
    def logical_name(self) -> str:
        """Logical module name (e.g., "jt.Math" or "jt.Math:BigUInt.Impl")."""
        prov = self._primary_provides
        if prov is None:
            return ""
        name = prov.get("logical-name", "")
        return name if isinstance(name, str) else ""

    @property
    def required_logical_names(self) -> list[str]:
        """Logical names of all imported modules across all rules."""
        if self.p1689 is None:
            return []
        rules = self.p1689.get("rules", [])
        if not isinstance(rules, list):
            return []
        names: list[str] = []
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            requires = rule.get("requires", [])
            if not isinstance(requires, list):
                continue
            for req in requires:
                if not isinstance(req, dict):
                    continue
                ln = req.get("logical-name", "")
                if isinstance(ln, str) and ln:
                    names.append(ln)
        return names


def module_file_for(logical_name: str, mod_dir: str, extension: str) -> str:
    """Compute the IFC/PCM path for a given logical module name.

    Replaces ':' with '-' so partition names produce valid filenames
    (e.g., "jt.Math:BigUInt.Impl" -> "{mod_dir}/jt.Math-BigUInt.Impl.ifc").
    """
    safe = logical_name.replace(":", "-")
    return f"{mod_dir}/{safe}{extension}"


def select_modules_scope(
    source_obj_by_language: dict[str, list[tuple[Path, Any]]],
) -> tuple[list[tuple[Path, Any]], list[tuple[Path, Any]]]:
    """Filter C++ TUs to those in envs that have module scanning enabled.

    A C++ environment opts in to module scanning either:
      - Implicitly: the env has at least one source whose suffix is in
        CXX_MODULE_INTERFACE_SUFFIXES (so the resolver tagged it as
        `cxx_module`).
      - Explicitly: `env.cxx.modules = True`, for module units in
        `.cpp`/`.cc` files (e.g. fmt's primary interface in `.cc`, or a
        target whose only module use is `import std;`).

    Returns:
        (cxx_module_pairs, cxx_pairs) restricted to qualifying envs. If
        no env qualifies, both lists are empty and the toolchain's
        after_resolve should early-return.
    """
    cxx_module_pairs = source_obj_by_language.get("cxx_module", []) or []
    cxx_pairs = source_obj_by_language.get("cxx", []) or []

    qualifying_env_ids: set[int] = set()

    # Implicit opt-in: any env with an extension-tagged module source.
    for _, obj_node in cxx_module_pairs:
        bi = getattr(obj_node, "_build_info", None)
        if bi is None:
            continue
        env = bi.get("env")
        if env is not None:
            qualifying_env_ids.add(id(env))

    # Explicit opt-in: env.cxx.modules == True.
    for _, obj_node in list(cxx_module_pairs) + list(cxx_pairs):
        bi = getattr(obj_node, "_build_info", None)
        if bi is None:
            continue
        env = bi.get("env")
        if env is None:
            continue
        cxx = getattr(env, "cxx", None)
        if cxx is not None and bool(getattr(cxx, "modules", False)):
            qualifying_env_ids.add(id(env))

    def _belongs(obj_node: Any) -> bool:
        bi = getattr(obj_node, "_build_info", None)
        if bi is None:
            return False
        env = bi.get("env")
        return env is not None and id(env) in qualifying_env_ids

    return (
        [pair for pair in cxx_module_pairs if _belongs(pair[1])],
        [pair for pair in cxx_pairs if _belongs(pair[1])],
    )


def spec_cache_key(spec: TuScanSpec, scanner_style: str) -> str:
    """The scan cache key for one TU under one scanner style."""
    return ScanCache.key(
        scan_recipe(scanner_style),
        spec.compiler,
        spec.compile_flags,
        str(spec.src),
        spec.obj_rel,
    )


def _run_scan(
    spec: TuScanSpec,
    scanner: str,
    scanner_style: str,
    prereqs_out: list[str],
) -> dict[str, Any] | None:
    """Run the runner *scanner_style* names on one TU."""
    if scanner_style == "msvc":
        return run_scan_deps_msvc(
            spec.compiler, spec.compile_flags, str(spec.src), prereqs_out=prereqs_out
        )
    if scanner_style == "gcc":
        return run_scan_deps_gcc(
            spec.compiler,
            spec.compile_flags,
            str(spec.src),
            spec.obj_rel,
            prereqs_out=prereqs_out,
        )
    return run_scan_deps(
        scanner,
        spec.compiler,
        spec.compile_flags,
        str(spec.src),
        spec.obj_rel,
        prereqs_out=prereqs_out,
    )


def _scan_one(
    spec: TuScanSpec,
    scanner: str,
    scanner_style: str,
    cache: ScanCache | None = None,
) -> TuScanResult:
    """Scan one TU with the runner its style names.

    With a *cache*, a TU whose every prerequisite is untouched since the last
    run skips the compiler entirely. Every runner reports what its scan read
    (GCC via the scan's own depfile, cl via /sourceDependencies, clang via a
    second make-format pass), so every style participates.
    """
    key = None if cache is None else spec_cache_key(spec, scanner_style)
    if cache is not None and key is not None:
        hit = cache.get(key)
        if hit is not None:
            return TuScanResult(spec=spec, p1689=hit)

    prereqs: list[str] = []
    started_ns = time.time_ns()
    p1689 = _run_scan(spec, scanner, scanner_style, prereqs)

    # No prerequisites means nothing to invalidate against, so the result
    # is used but not stored. The tool binaries ride along so an in-place
    # upgrade invalidates the answers the old ones gave.
    if cache is not None and key is not None and p1689 is not None and prereqs:
        tools = {spec.compiler, scanner} - {""}
        binaries = [b for tool in sorted(tools) if (b := compiler_binary(tool))]
        cache.put(
            key,
            p1689,
            [*prereqs, *binaries],
            scan_started_ns=started_ns,
        )
    return TuScanResult(spec=spec, p1689=p1689)


def _scan_workers(count: int) -> int:
    """How many scans to have in flight.

    One per core, not the pool default of cores + 4: each scan is a compiler
    preprocessing a whole translation unit, so oversubscribing costs memory and
    buys nothing. Threads rather than processes because every scan is a
    `subprocess.run` that releases the GIL for its whole duration.

    ``-j`` caps it: a user who asked for four jobs asked for four compilers at
    a time, and configure runs them just as the build does. The CLI passes the
    value down as ``PCONS_JOBS``.
    """
    limit = _requested_jobs() or os.cpu_count() or 1
    return max(1, min(count, limit))


def _requested_jobs() -> int | None:
    """``-j`` as the CLI recorded it, or None when it said nothing usable."""
    raw = os.environ.get("PCONS_JOBS")
    if not raw:
        return None
    try:
        jobs = int(raw)
    except ValueError:
        return None
    return jobs if jobs > 0 else None


def scan_translation_units(
    specs: list[TuScanSpec],
    scanner: str,
    scanner_style: str = "clang",
    cache: ScanCache | None = None,
) -> list[TuScanResult]:
    """Run the scanner on each TU and return parsed results.

    Args:
        specs: Per-TU scan inputs.
        scanner: Path to clang-scan-deps (clang style) or cl.exe (msvc style).
        scanner_style: "clang" or "msvc".
        cache: Where results are kept between runs, and where this pass adds
            its own. Without one every TU is rescanned. The caller owns it,
            so several passes share one load and one save.

    Returns:
        One TuScanResult per spec, in order. result.p1689 is None if scanning
        that TU failed (a warning is written to stderr by the runner).
    """
    if len(specs) < 2:
        return [_scan_one(spec, scanner, scanner_style, cache) for spec in specs]

    with ThreadPoolExecutor(max_workers=_scan_workers(len(specs))) as pool:
        # map preserves input order, which build_module_map and the dyndep
        # writer both rely on. as_completed would not.
        return list(
            pool.map(lambda s: _scan_one(s, scanner, scanner_style, cache), specs)
        )


@dataclass
class StdModuleFlagSpec:
    """Categorizes which user flags to carry onto the `import std;` compile.

    The standard-library module's `.pcm` / `.ifc` is consumed by user TUs,
    so it must agree with them on every flag that affects ABI or what
    the standard library's headers expose. Build systems can't pass *all*
    user flags (some break the std-module compile — `-Werror`, user
    `-I`s, unrelated `-D`s) so we filter:

    Attributes:
        exact: full-flag matches that are pure passthrough
            (e.g. ``"-fno-rtti"``).
        prefixes: flag prefixes that carry a value as one token
            (e.g. ``("-std=", "-stdlib=", "-isysroot=")``).
        paired: flags that take the value as the *next* token
            (e.g. ``{"-target", "-isysroot"}`` — passed as
            ``-target X``).
        define_prefix: the toolchain's define flag prefix (``"-D"`` or
            ``"/D"``); used together with ``define_glob_prefixes`` to
            select user defines that must propagate.
        define_glob_prefixes: macro-name prefixes whose ``-Dfoo[=...]``
            invocations carry through. Used for stdlib feature-test
            macros — ``("_LIBCPP_",)`` for libc++, ``("_HAS_",
            "_ITERATOR_DEBUG_LEVEL", "_CONTAINER_DEBUG_LEVEL")`` for
            MSVC's STL.
    """

    exact: frozenset[str]
    prefixes: tuple[str, ...]
    paired: frozenset[str]
    define_prefix: str
    define_glob_prefixes: tuple[str, ...]


def select_std_module_flags(
    base_flags: list[str], spec: StdModuleFlagSpec
) -> list[str]:
    """Pick ABI-affecting flags from the user's compile flags.

    Walks ``base_flags`` once. Per the spec, copies exact-match flags,
    prefix-match flags (with their values), pair flags (with the
    following token), and stdlib-relevant ``-D`` defines. Order is
    preserved.
    """
    out: list[str] = []
    i = 0
    while i < len(base_flags):
        f = base_flags[i]
        if f in spec.exact:
            out.append(f)
            i += 1
            continue
        if spec.prefixes and f.startswith(spec.prefixes):
            out.append(f)
            i += 1
            continue
        if f in spec.paired and i + 1 < len(base_flags):
            out.extend([f, base_flags[i + 1]])
            i += 2
            continue
        if spec.define_prefix and f.startswith(spec.define_prefix):
            macro = f[len(spec.define_prefix) :]
            if spec.define_glob_prefixes and macro.startswith(
                spec.define_glob_prefixes
            ):
                out.append(f)
        i += 1
    return out


def bmi_key_for_flags(flags: list[str], spec: StdModuleFlagSpec) -> str:
    """Compute a short hash identifying a BMI-compatibility class.

    A Binary Module Interface (``.gcm`` / ``.pcm`` / ``.ifc``) can only be
    consumed by translation units compiled with matching BMI-sensitive flags
    (the C++ dialect, ABI knobs, stdlib feature macros, etc. — exactly the set
    ``spec`` selects). Two TUs that agree on every such flag may share one
    compiled module interface; TUs that differ on any of them must each get
    their own. Keying the BMI's on-disk directory by this hash lets the same
    module interface be reused across targets when compatible
    (``cxx_modules/<key>/provider.gcm``) and kept separate when not.

    The hash is order-independent: BMI compatibility does not depend on the
    order BMI-sensitive flags appear on the command line.
    """
    relevant = select_std_module_flags(list(flags), spec)
    canonical = "\0".join(sorted(relevant))
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]


def merge_scan_compile_flags(
    base_flags: list[str],
    context: Any,
    extra_flags: tuple[str, ...] = (),
    *,
    iprefix: str = "-I",
    isysprefix: str = "-isystem",
    dprefix: str = "-D",
    root: Path | None = None,
) -> list[str]:
    """Build a per-TU compile-flag list for module scanning.

    Starts from *base_flags*, injects *extra_flags* (deduped, e.g. GCC's
    ``-fmodules``), then appends the build context's flags (deduped),
    includes, and defines, in that order.

    With *root*, relative include paths are anchored there and made
    absolute. The scan runs twice with these flags — at configure time from
    the project root, and again from the build directory by the dyndep
    edge — and the scan cache keys on them, so both runs must agree on a
    form that works from anywhere.
    """

    def include_path(inc: Any) -> str:
        if root is not None and not os.path.isabs(str(inc)):
            return str(root / str(inc))
        return str(inc)

    seen = set(base_flags)
    compile_flags = list(base_flags)
    for flag in extra_flags:
        if flag not in seen:
            compile_flags.append(flag)
            seen.add(flag)
    if context:
        for flag in context.flags:
            if flag not in seen:
                compile_flags.append(flag)
                seen.add(flag)
        for inc in context.includes:
            compile_flags.append(f"{iprefix}{include_path(inc)}")
        # Vendored SDK headers live here; without them the scanner can't
        # preprocess the TU it is scanning.
        for inc in getattr(context, "system_includes", ()):
            compile_flags.append(f"{isysprefix}{include_path(inc)}")
        for define in context.defines:
            compile_flags.append(f"{dprefix}{define}")
    return compile_flags


@dataclass
class ModulePassSetup:
    """Scaffolding shared by every toolchain's C++ modules pass.

    Built by :func:`setup_module_pass`; ``spec_to_obj`` and ``obj_key`` are
    filled by :func:`add_tu_spec` as the toolchain builds its scan specs.
    """

    cxx_module_pairs: list[tuple[Path, Any]]
    cxx_pairs: list[tuple[Path, Any]]
    build_dir: Path
    moddir: str
    dyndep_path: Path
    dyndep_rel: str
    first_env: Any
    cxx_tool: Any
    compiler_cmd: str
    base_flags: list[str]
    spec_to_obj: dict[int, Any] = field(default_factory=dict)
    obj_key: dict[int, str] = field(default_factory=dict)

    @property
    def all_cxx_pairs(self) -> list[tuple[Path, Any]]:
        return self.cxx_module_pairs + self.cxx_pairs


def setup_module_pass(
    project: Any,
    source_obj_by_language: dict[str, list[tuple[Path, Any]]],
    default_compiler: str,
) -> ModulePassSetup | None:
    """Select the modules scope and gather paths/compiler for a modules pass.

    Returns None when no environment participates. The compiler command and
    base flags come from the first participating object's environment.

    Every selected source becomes a configure dependency: what a TU provides
    or imports drives flag injection (``-x c++-module``, keyed BMI outputs),
    which only re-running pcons can change. The dyndep file needs no such
    help — it is regenerated at build time from a fresh scan — but the flags
    on the compile lines are configure's alone.
    """
    cxx_module_pairs, cxx_pairs = select_modules_scope(source_obj_by_language)
    if not cxx_module_pairs and not cxx_pairs:
        return None

    for src, _obj in cxx_module_pairs + cxx_pairs:
        project.add_configure_dependency(src)

    build_dir = project.build_dir
    moddir = "cxx_modules"
    (build_dir / moddir).mkdir(parents=True, exist_ok=True)

    first_obj = (cxx_module_pairs + cxx_pairs)[0][1]
    build_info = getattr(first_obj, "_build_info", None)
    first_env = build_info.get("env") if build_info else None
    cxx_tool = getattr(first_env, "cxx", None) if first_env else None
    compiler_cmd = str(getattr(cxx_tool, "cmd", default_compiler) or default_compiler)
    base_flags = list(getattr(cxx_tool, "flags", None) or [])

    return ModulePassSetup(
        cxx_module_pairs=cxx_module_pairs,
        cxx_pairs=cxx_pairs,
        build_dir=build_dir,
        moddir=moddir,
        dyndep_path=build_dir / "cxx_modules.dyndep",
        dyndep_rel="cxx_modules.dyndep",
        first_env=first_env,
        cxx_tool=cxx_tool,
        compiler_cmd=compiler_cmd,
        base_flags=base_flags,
    )


def add_tu_spec(
    setup: ModulePassSetup,
    src: Path,
    obj_node: Any,
    compile_flags: list[str],
    flag_spec: StdModuleFlagSpec,
) -> TuScanSpec:
    """Create a scan spec for one TU and register the object's BMI key."""
    spec = TuScanSpec(
        src=src.resolve(),
        obj_rel=str(obj_node.path.relative_to(setup.build_dir)).replace("\\", "/"),
        compiler=setup.compiler_cmd,
        compile_flags=compile_flags,
    )
    setup.spec_to_obj[id(spec)] = obj_node
    setup.obj_key[id(obj_node)] = bmi_key_for_flags(compile_flags, flag_spec)
    return spec


def write_module_manifest(
    path: Path,
    setup: ModulePassSetup,
    results: list[TuScanResult],
    std_obj_nodes: dict[str, Any],
    bmi_ext: str,
    scanner: str,
    scanner_style: str,
) -> None:
    """Record what the build-time dyndep edge needs to redo the scan.

    One entry per participating TU. Std-module entries carry their p1689
    verbatim instead of scan inputs: what a standard-library module provides
    depends only on the toolchain, and a toolchain change re-runs pcons (its
    sources are configure dependencies), so re-scanning them at build time
    would answer a question that cannot have changed.
    """
    std_ids = {id(node) for node in std_obj_nodes.values()}
    tus: list[dict[str, Any]] = []
    for r in results:
        obj_node = setup.spec_to_obj.get(id(r.spec))
        if obj_node is None:
            continue
        key = setup.obj_key[id(obj_node)]
        if id(obj_node) in std_ids:
            tus.append({"obj": r.spec.obj_rel, "key": key, "p1689": r.p1689})
        else:
            tus.append(
                {
                    "src": str(r.spec.src),
                    "obj": r.spec.obj_rel,
                    "key": key,
                    "compiler": r.spec.compiler,
                    "flags": list(r.spec.compile_flags),
                }
            )
    manifest = {
        "version": 1,
        "scanner": scanner,
        "scanner_style": scanner_style,
        "moddir": setup.moddir,
        "bmi_ext": bmi_ext,
        "std_logicals": sorted(std_obj_nodes),
        "tus": tus,
    }
    _write_text_if_changed(path, json.dumps(manifest, indent=1, sort_keys=True))


def finish_module_pass(
    project: Any,
    setup: ModulePassSetup,
    results: list[TuScanResult],
    provider_obj: dict[tuple[str, str], Any],
    std_obj_nodes: dict[str, Any],
    bmi_ext: str,
    scanner: str,
    scanner_style: str,
) -> None:
    """Wire the modules pass into the build graph, deferring the dyndep.

    The tail every toolchain's modules pass shares: per-key BMI dirs, the
    scan manifest, a build edge that (re)writes the dyndep file, ``dyndep``
    + implicit deps on every participating object (std objects included),
    and std objects linked into importing targets.

    The dyndep file is written by the edge, not here: a TU's imports depend
    on every header it includes, and headers are not configure dependencies,
    so a header that gains an ``import`` must be able to reorder the build
    without a reconfigure. The edge re-runs this module against the
    manifest; its depfile carries what the scans read, and the scan cache
    keeps the re-run cheap.
    """
    for key in set(setup.obj_key.values()):
        (setup.build_dir / setup.moddir / key).mkdir(parents=True, exist_ok=True)

    # The same consistency checks the build-time edge performs, but a
    # failure here is a configure error with context, not a broken build
    # later. The entries themselves are recomputed by the edge.
    build_keyed_entries(
        results, setup.spec_to_obj, setup.obj_key, provider_obj, setup.moddir, bmi_ext
    )

    manifest_path = setup.build_dir / MANIFEST_FILE
    write_module_manifest(
        manifest_path, setup, results, std_obj_nodes, bmi_ext, scanner, scanner_style
    )

    from pcons.core.subst import PathToken

    std_ids = {id(node) for node in std_obj_nodes.values()}
    scan_sources: dict[int, Any] = {}
    for r in results:
        obj_node = setup.spec_to_obj.get(id(r.spec))
        if obj_node is None or id(obj_node) in std_ids:
            continue
        src_node = project.node(r.spec.src)
        scan_sources[id(src_node)] = src_node

    dyndep_node = project.node(setup.dyndep_path)
    manifest_node = project.node(manifest_path)
    scan_inputs = [*scan_sources.values(), manifest_node]
    dyndep_node.add_inputs(scan_inputs)
    dyndep_node._build_info = {
        "tool": "cxx_scan",
        "command_var": "dyndepcmd",
        "description": "SCAN C++ modules",
        "sources": scan_inputs,
        "command": [
            sys.executable,
            "-m",
            "pcons.toolchains.cxx_module_scanner",
            "--manifest",
            MANIFEST_FILE,
            "--out",
            setup.dyndep_rel,
        ],
        "depfile": PathToken(suffix=".d"),
        "deps_style": "gcc",
        "restat": True,
    }
    if setup.first_env is not None:
        setup.first_env.register_node(dyndep_node)

    participants = [obj for _, obj in setup.all_cxx_pairs]
    participants.extend(std_obj_nodes.values())
    for obj_node in participants:
        bi = getattr(obj_node, "_build_info", None)
        if bi is not None:
            bi["dyndep"] = setup.dyndep_rel
        if dyndep_node not in obj_node.implicit_deps:
            obj_node.implicit_deps.append(dyndep_node)

    if std_obj_nodes:
        wire_std_into_targets(project, results, setup.spec_to_obj, std_obj_nodes)


def wire_std_into_targets(
    project: Any,
    results: list[TuScanResult],
    spec_to_obj: dict[int, Any],
    std_obj_nodes: dict[str, Any],
) -> None:
    """Add std/std.compat .obj files to the link inputs of importing targets.

    For every project target, looks at which `import std;` / `import std.compat;`
    requirements its TUs have (via the scan results) and appends the
    corresponding synthesized std-module .obj to the target's
    intermediate_nodes (so the link rule sees it) and to its output nodes'
    explicit_deps (so the build graph has the dependency).

    Toolchain-agnostic: works for both MSVC (.obj files) and clang (.o files)
    so long as the caller supplied a {logical_name: obj_node} map.
    """
    obj_id_to_required: dict[int, set[str]] = {}
    for r in results:
        obj_node = spec_to_obj.get(id(r.spec))
        if obj_node is None:
            continue
        obj_id_to_required[id(obj_node)] = set(r.required_logical_names)

    for target in project.targets:
        target_required: set[str] = set()
        for obj_node in target.intermediate_nodes:
            target_required.update(obj_id_to_required.get(id(obj_node), set()))
        for logical, std_obj_node in std_obj_nodes.items():
            if logical in target_required:
                if std_obj_node not in target.intermediate_nodes:
                    target.intermediate_nodes.append(std_obj_node)
                for output_node in target.output_nodes:
                    if std_obj_node not in output_node.explicit_deps:
                        output_node.explicit_deps.append(std_obj_node)


def keyed_bmi_path(logical_name: str, moddir: str, key: str, extension: str) -> str:
    """BMI path for a logical module in its compatibility class's directory.

    E.g. ``keyed_bmi_path("provider", "cxx_modules", "49eea...", ".pcm")`` ->
    ``cxx_modules/49eea.../provider.pcm``.
    """
    return module_file_for(logical_name, f"{moddir}/{key}", extension)


def map_module_providers(
    results: list[TuScanResult],
    spec_to_obj: dict[int, Any],
    obj_key: dict[int, str],
    moddir: str,
    bmi_ext: str,
) -> dict[tuple[str, str], str]:
    """Map ``(bmi_key, logical_name)`` -> providing object path.

    Walks the module-providing scan results and records which object compiles
    each logical module within each BMI-compatibility class. Raises
    RuntimeError if two *different* objects provide the same module with
    BMI-equivalent flags — both would write the same keyed BMI path.

    Results whose spec is not registered in ``spec_to_obj`` are skipped.
    """
    provider_obj: dict[tuple[str, str], str] = {}
    for r in results:
        if not r.is_module_provider:
            continue
        obj_node = spec_to_obj.get(id(r.spec))
        if obj_node is None:
            continue
        key = obj_key[id(obj_node)]
        slot = (key, r.logical_name)
        if slot in provider_obj and provider_obj[slot] != r.spec.obj_rel:
            raise RuntimeError(
                f"Module '{r.logical_name}' is compiled into two different "
                f"objects ({provider_obj[slot]} and {r.spec.obj_rel}) with "
                f"BMI-equivalent flags, so both would write the same "
                f"{keyed_bmi_path(r.logical_name, moddir, key, bmi_ext)}. "
                f"Give them distinct BMI-sensitive flags or build the "
                f"interface in one place."
            )
        provider_obj[slot] = r.spec.obj_rel
    return provider_obj


def build_keyed_entries(
    results: list[TuScanResult],
    spec_to_obj: dict[int, Any],
    obj_key: dict[int, str],
    provider_obj: dict[tuple[str, str], str],
    moddir: str,
    bmi_ext: str,
) -> list[tuple[str, list[str], list[str]]]:
    """Build dyndep entries with provides/requires keyed per compatibility class.

    Each TU's provided and required modules resolve to BMI paths in its own
    class's ``cxx_modules/<key>/`` directory (a BMI is only consumable by TUs
    whose BMI-sensitive flags match).

    Raises RuntimeError if a TU imports a module whose compiled interface
    exists only in *other* compatibility classes — the import could never be
    satisfied, and the compile-time error would be far less clear. Imports of
    modules not provided anywhere in the project are passed through silently
    (they may be satisfied externally).
    """
    entries: list[tuple[str, list[str], list[str]]] = []
    provided_anywhere = {logical for _, logical in provider_obj}
    for r in results:
        obj_node = spec_to_obj.get(id(r.spec))
        if obj_node is None:
            continue
        key = obj_key[id(obj_node)]
        provides: list[str] = []
        if r.is_module_provider:
            provides.append(keyed_bmi_path(r.logical_name, moddir, key, bmi_ext))
        requires: list[str] = []
        for ln in r.required_logical_names:
            if (key, ln) in provider_obj:
                requires.append(keyed_bmi_path(ln, moddir, key, bmi_ext))
            elif ln in provided_anywhere:
                others = sorted(
                    obj for (_, logical), obj in provider_obj.items() if logical == ln
                )
                raise RuntimeError(
                    f"Module '{ln}' is imported by {r.spec.obj_rel}, but its "
                    f"compiled interface is only built with different "
                    f"BMI-sensitive flags (by {', '.join(others)}). A module "
                    f"interface is only consumable by TUs whose BMI-sensitive "
                    f"flags (C++ dialect, ABI options) match. Compile the "
                    f"interface with this TU's flags too (e.g. add its source "
                    f"to the importing target), or align the targets' flags."
                )
        entries.append((r.spec.obj_rel, provides, requires))
    return entries


def write_dyndep_entries(
    entries: list[tuple[str, list[str], list[str]]],
    out_path: str | Path,
) -> None:
    """Write a Ninja dyndep file from pre-resolved (obj, provides, requires).

    Each entry is ``(obj_rel, provides_paths, requires_paths)`` where the
    provides/requires are build-dir-relative BMI paths. Toolchains that map
    one logical module name to different BMI paths per compatibility class
    (GCC's per-key ``cxx_modules/<key>/`` layout) build entries directly,
    since a single ``{logical: path}`` map cannot express that.
    """
    lines = ["ninja_dyndep_version = 1", ""]
    for obj_rel, provides_pcms, requires_pcms in sorted(entries, key=lambda e: e[0]):
        implicit_out = (
            " | " + " ".join(sorted(set(provides_pcms))) if provides_pcms else ""
        )
        implicit_in = (
            " | " + " ".join(sorted(set(requires_pcms))) if requires_pcms else ""
        )
        lines.append(f"build {obj_rel}{implicit_out}: dyndep{implicit_in}")
        lines.append("")

    _write_text_if_changed(Path(out_path), "\n".join(lines))


def _write_scan_depfile(
    path: Path,
    target: str,
    specs: list[TuScanSpec],
    scanner_style: str,
    cache: ScanCache | None,
) -> None:
    """Everything whose edit should re-run the dyndep edge, in make syntax.

    Every scan reports what it read, and the scan cache stores that list per
    TU; the union is exactly the set of headers whose edits can change the
    dyndep.

    A TU whose scan raced an edit has no cache entry to draw on; its source
    still appears, and the next reconfigure restores full coverage.
    """
    prereqs: set[str] = set()
    for spec in specs:
        prereqs.add(str(spec.src))
        if cache is not None:
            prereqs.update(cache.prereqs(spec_cache_key(spec, scanner_style)) or [])

    def escape(p: str) -> str:
        return p.replace("\\", "/").replace(" ", "\\ ")

    deps = " ".join(escape(p) for p in sorted(prereqs))
    path.write_text(f"{escape(target)}: {deps}\n", encoding="utf-8")


def regenerate_dyndep(manifest: dict[str, Any], out_rel: str) -> int:
    """Re-scan the manifest's TUs and rewrite the dyndep file.

    The build-time half of the modules pass, run from the build directory as
    the edge ``finish_module_pass`` created. Returns a process exit code;
    refusing (nonzero) leaves the previous dyndep in place and fails the
    build loudly, which beats writing entries a failed scan cannot vouch
    for.
    """
    scanner = str(manifest.get("scanner", ""))
    scanner_style = str(manifest.get("scanner_style", "clang"))
    moddir = str(manifest.get("moddir", "cxx_modules"))
    bmi_ext = str(manifest.get("bmi_ext", ".pcm"))
    std_logicals = {str(s) for s in manifest.get("std_logicals", [])}

    # Each spec stands in for its own object node: the keyed-entry helpers
    # only ever id() the object and read obj_rel off the spec, and at build
    # time there is no node graph to offer them.
    fixed: list[TuScanResult] = []
    specs: list[TuScanSpec] = []
    spec_to_obj: dict[int, Any] = {}
    obj_key: dict[int, str] = {}
    for entry in manifest.get("tus", []):
        obj_rel = str(entry["obj"])
        if "p1689" in entry:
            spec = TuScanSpec(src=Path(obj_rel), obj_rel=obj_rel, compiler="")
            fixed.append(TuScanResult(spec=spec, p1689=entry["p1689"]))
        else:
            spec = TuScanSpec(
                src=Path(str(entry["src"])),
                obj_rel=obj_rel,
                compiler=str(entry.get("compiler", "")),
                compile_flags=[str(f) for f in entry.get("flags", [])],
            )
            specs.append(spec)
        spec_to_obj[id(spec)] = spec
        obj_key[id(spec)] = str(entry["key"])

    cache = ScanCache(Path.cwd())
    scanned = scan_translation_units(specs, scanner, scanner_style, cache)
    cache.save()

    failed = sorted(str(r.spec.src) for r in scanned if r.p1689 is None)
    if failed:
        print(
            "pcons module scan: could not scan "
            + ", ".join(failed)
            + " (see warnings above); keeping the previous dyndep file",
            file=sys.stderr,
        )
        return 1

    results = fixed + scanned
    try:
        provider_obj = map_module_providers(
            results, spec_to_obj, obj_key, moddir, bmi_ext
        )
        # A header can add `import std;` to a TU, but only configure can
        # synthesize the std module's build. Say so, rather than letting the
        # compile fail on a module nobody provides.
        required = {ln for r in results for ln in r.required_logical_names}
        new_std = (required & {"std", "std.compat"}) - std_logicals
        if new_std:
            print(
                f"pcons module scan: `import {sorted(new_std)[0]};` appeared "
                "since the last configure (likely via a header edit). "
                "Run pcons to set up the standard-library module build.",
                file=sys.stderr,
            )
            return 1
        entries = build_keyed_entries(
            results, spec_to_obj, obj_key, provider_obj, moddir, bmi_ext
        )
    except RuntimeError as e:
        print(f"pcons module scan: {e}", file=sys.stderr)
        return 1

    for key in {obj_key[id(s)] for s in spec_to_obj.values()}:
        Path(moddir, key).mkdir(parents=True, exist_ok=True)
    write_dyndep_entries(entries, Path(out_rel))
    _write_scan_depfile(Path(out_rel + ".d"), out_rel, specs, scanner_style, cache)
    return 0


def main() -> int:
    """Entry point when run as python -m pcons.toolchains.cxx_module_scanner."""
    parser = argparse.ArgumentParser(
        description="Regenerate the Ninja dyndep file for C++20 modules"
    )
    parser.add_argument(
        "--manifest",
        required=True,
        help="scan manifest written at configure time (relative to build dir)",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="dyndep file to write (relative to build dir)",
    )
    args = parser.parse_args()

    try:
        manifest: dict[str, Any] = json.loads(
            Path(args.manifest).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as e:
        print(f"Error reading manifest {args.manifest}: {e}", file=sys.stderr)
        return 1
    if not isinstance(manifest, dict):
        print(f"Error: {args.manifest} is not a module scan manifest", file=sys.stderr)
        return 1

    try:
        return regenerate_dyndep(manifest, args.out)
    except CxxModuleScannerNotFound as e:
        print(f"pcons module scan: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
