# SPDX-License-Identifier: MIT
"""Discovered dependencies: the Scanner primitive.

A :class:`Scanner` declares that the true dependencies — and possibly extra
outputs — of some build edges are a function of their inputs' *content*,
computed at build time. C++20 modules are the canonical case (``import m;``
orders compiles), Fortran modules another; a user's own pipeline qualifies
whenever one built artifact must embed or precede another based on what a
source file says.

The contract that keeps this correct with generated sources:

    Configure may depend only on static facts — file names, suffixes, flags,
    the target DAG. Content-derived facts flow through scan -> collate at
    build time.

Per scanned target, the resolver wires:

- one **scan edge** per governed build edge: its scanned sources in, one
  scan-info JSON file out (see :mod:`pcons.core.collate` for the schema).
  A generated source is simply the scan edge's input, so the producer's
  ordering is inherited from the node graph — no phases, no existence
  checks, any number of generation stages.
- one **collate edge** for the target: scan infos + a configure-written
  manifest + the exports of scanned dependency targets in; a ninja *dyndep*
  file, an exports file, and optional per-edge args files out.
- each governed edge gets ``dyndep = <target's dyndep file>`` plus an
  implicit dep on the collate edge.

Because the dyndep file is per *target*, ordering between scopes follows the
target DAG and the graph stays acyclic by construction; ninja connects
cross-scope artifact references through dyndep implicit outputs, which are
global to the build.

Only the ninja generator can express dyndep; other generators refuse a
scanned project with a clear error rather than emitting a wrong build.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pcons.core.errors import PconsError
from pcons.core.node import FileNode
from pcons.core.subst import NodeVar, PathToken

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.project import Project
    from pcons.core.target import Target

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# Characters allowed in scope/file identifiers derived from target names.
_SCOPE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class ArgsFormat:
    """How the generic collate renders a per-edge args file.

    ``header`` lines are emitted verbatim; then one ``line`` per resolved
    module, with ``{name}`` and ``{path}`` substituted. A GCC module mapper
    is ``header=("$root .",), line="{name} {path}"``; a clang response file
    is ``line="-fmodule-file={name}={path}"``.
    """

    header: tuple[str, ...] = ()
    line: str = "{name} {path}"


@dataclass(frozen=True)
class EdgeArgsSpec:
    """A collate-written file each governed edge's command references.

    The file's *path* is static (the edge's primary output plus ``suffix``),
    so the reference — ``token``, carried via a per-edge ninja variable named
    ``var`` — is baked into the command at configure time; only the file's
    *content* is decided at build time by collate. This is how content-derived
    flags reach a command line that dyndep alone could never alter.
    """

    suffix: str = ".modmap"
    var: str = "SCAN_ARGS"
    token: str = "@$SCAN_ARGS"
    format: ArgsFormat = field(default_factory=ArgsFormat)
    include: Literal["requires", "requires+provides"] = "requires+provides"


@dataclass(frozen=True)
class Scanner:
    """A declared discovered-dependency scanner.

    Declare one, then :meth:`attach` it to targets before
    ``project.resolve()``. The resolver creates the scan and collate edges;
    the scanner itself is a value object and holds no project state.

    Attributes:
        name: Lowercase-hyphenated identifier (``"scene-refs"``); names the
            scan directory, rules, and error messages.
        source_suffixes: A governed edge is any build edge of an attached
            target with at least one source whose suffix is listed here;
            exactly those sources are scanned.
        scan_command: Command tokens for one scan edge. ``$SOURCE``/
            ``$SOURCES`` name the edge's scanned sources, ``$TARGET`` the
            scan-info file to write; ``$SRCDIR`` works as in
            ``env.Command``. Tokens may also be subst markers (e.g.
            :class:`~pcons.core.subst.NodeVar` for per-edge values supplied
            via ``scan_vars``). The command must write a scan-info JSON file
            (schema in :mod:`pcons.core.collate`). Scanners compare by
            value, so declare one and attach it everywhere — two inline
            declarations that differ only in a ``scan_vars`` lambda compare
            unequal (function identity) and are reported as conflicting.
        info_suffix: Appended to the governed edge's primary output path to
            name its scan-info file.
        scan_depfile: Optional depfile suffix (e.g. ``".d"``) the scan
            command writes, so edits to files the scan *read* re-run just
            that scan.
        scan_deps_style: ``"gcc"`` or ``"msvc"``; only used with
            ``scan_depfile``.
        scan_deps: Extra implicit deps of every scan edge — typically the
            scan tool script itself, as a project-relative path.
        scan_vars: Optional callable ``(env, scanned_sources, governed_node)
            -> dict[str, str]`` supplying per-edge ninja variables for the
            scan command, so edges with differing values still share one
            rule.
        collate_command: Override for the collate command tokens; ``None``
            uses the generic collate CLI (``python -m pcons.core.collate``).
            A custom collate receives ``--manifest <path>`` semantics of its
            own choosing — pcons appends nothing.
        provide_template: Artifact path for a provided logical name when the
            scan info gives none; ``{name}`` (sanitized), ``{scanner}`` and
            ``{scope}`` substitute.
        edge_args: Optional per-edge args file written by collate (see
            :class:`EdgeArgsSpec`).
        on_unresolved: What the generic collate does with a require no scope
            provides: ``"ignore"`` (may be satisfied externally), ``"warn"``,
            or ``"error"``.
    """

    name: str
    source_suffixes: tuple[str, ...]
    scan_command: tuple[Any, ...]
    info_suffix: str = ".scaninfo.json"
    scan_depfile: str | None = None
    scan_deps_style: Literal["gcc", "msvc"] = "gcc"
    scan_deps: tuple[str, ...] = ()
    scan_vars: Callable[[Any, list[FileNode], FileNode], dict[str, str]] | None = None
    collate_command: tuple[Any, ...] | None = None
    provide_template: str = "scan/{scanner}/provided/{name}"
    edge_args: EdgeArgsSpec | None = None
    on_unresolved: Literal["ignore", "warn", "error"] = "ignore"

    def __init__(
        self,
        name: str,
        *,
        source_suffixes: Sequence[str],
        scan_command: Sequence[Any],
        info_suffix: str = ".scaninfo.json",
        scan_depfile: str | None = None,
        scan_deps_style: Literal["gcc", "msvc"] = "gcc",
        scan_deps: Sequence[str] = (),
        scan_vars: Callable[[Any, list[FileNode], FileNode], dict[str, str]]
        | None = None,
        collate_command: Sequence[Any] | None = None,
        provide_template: str = "scan/{scanner}/provided/{name}",
        edge_args: EdgeArgsSpec | None = None,
        on_unresolved: Literal["ignore", "warn", "error"] = "ignore",
    ) -> None:
        # A hand-written __init__ (rather than the dataclass one) so sequence
        # arguments of any kind arrive as tuples and the instance stays
        # hashable/frozen.
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "source_suffixes", tuple(source_suffixes))
        object.__setattr__(self, "scan_command", tuple(scan_command))
        object.__setattr__(self, "info_suffix", info_suffix)
        object.__setattr__(self, "scan_depfile", scan_depfile)
        object.__setattr__(self, "scan_deps_style", scan_deps_style)
        object.__setattr__(self, "scan_deps", tuple(scan_deps))
        object.__setattr__(self, "scan_vars", scan_vars)
        object.__setattr__(
            self,
            "collate_command",
            tuple(collate_command) if collate_command is not None else None,
        )
        object.__setattr__(self, "provide_template", provide_template)
        object.__setattr__(self, "edge_args", edge_args)
        object.__setattr__(self, "on_unresolved", on_unresolved)
        self._validate()

    def _validate(self) -> None:
        if not _NAME_RE.match(self.name):
            raise PconsError(
                f"Scanner name {self.name!r} must be lowercase letters, "
                f"digits, and hyphens (like 'scene-refs')."
            )
        if not self.source_suffixes:
            raise PconsError(f"Scanner '{self.name}' needs source_suffixes.")
        for suffix in self.source_suffixes:
            if not suffix.startswith("."):
                raise PconsError(
                    f"Scanner '{self.name}': source suffix {suffix!r} must "
                    f"start with '.'."
                )
        if not self.scan_command:
            raise PconsError(f"Scanner '{self.name}' needs a scan_command.")
        if not self.info_suffix.startswith("."):
            raise PconsError(
                f"Scanner '{self.name}': info_suffix {self.info_suffix!r} "
                f"must start with '.'."
            )
        if self.scan_depfile is not None:
            if not self.scan_depfile.startswith("."):
                raise PconsError(
                    f"Scanner '{self.name}': scan_depfile "
                    f"{self.scan_depfile!r} must start with '.'."
                )
            if self.scan_depfile == self.info_suffix:
                raise PconsError(
                    f"Scanner '{self.name}': scan_depfile and info_suffix must differ."
                )
        if self.on_unresolved not in ("ignore", "warn", "error"):
            raise PconsError(
                f"Scanner '{self.name}': on_unresolved must be 'ignore', "
                f"'warn', or 'error'."
            )

    def attach(self, *targets: Target) -> None:
        """Scope this scanner over each target's build edges.

        Call before ``project.resolve()`` (toolchains may also attach during
        their ``after_resolve`` hook — the wiring pass runs after it).
        Attaching the same scanner to a target twice is a no-op; attaching a
        *different* scanner with the same name to the same target is an
        error, caught by the wiring pass.
        """
        for target in targets:
            if self not in target._scanners:
                target._scanners.append(self)


@dataclass
class ScanScope:
    """One (scanner, target) scope the wiring pass created."""

    scanner: Scanner
    target: Target
    manifest_rel: str
    dyndep_rel: str
    exports_rel: str
    collate_node: FileNode
    exports_node: FileNode
    governed: list[FileNode]


def _rule_ident(name: str) -> str:
    """A scanner name as a ninja-rule-safe identifier fragment."""
    return name.replace("-", "_")


class ScannerResolver:
    """Wires every attached scanner into the graph.

    Runs inside ``Resolver.resolve()``, after the toolchain ``after_resolve``
    hooks (so toolchain-attached scanners are visible) and before command
    expansion (so per-edge vars exist when compile templates expand).
    Processes targets in build order, so a dependency's exports node exists
    by the time a dependent imports it.
    """

    def __init__(self, project: Project) -> None:
        self.project = project
        # File-identifier collision guard: two qualified names must never
        # sanitize to the same scope id (their manifests would collide).
        self._scope_ids: dict[tuple[str, str], str] = {}

    def run(self, targets_in_build_order: Sequence[Target]) -> None:
        for target in targets_in_build_order:
            for scanner in target._scanners:
                self._wire(target, scanner)

    # ------------------------------------------------------------------
    # Wiring one (target, scanner) scope
    # ------------------------------------------------------------------

    def _wire(self, target: Target, scanner: Scanner) -> None:
        project = self.project
        key = (scanner.name, target.qualified_name)
        if key in project._scan_scopes:
            existing = project._scan_scopes[key].scanner
            if existing is scanner:
                return  # attached twice via different routes; already wired
            raise PconsError(
                f"Two conflicting scanners named '{scanner.name}' are "
                f"attached to target '{target.qualified_name}' — their "
                f"declarations differ. Declare the scanner once and attach "
                f"that one everywhere."
            )

        env = target._env
        if env is None:
            raise PconsError(
                f"Scanner '{scanner.name}' is attached to target "
                f"'{target.qualified_name}', which has no environment."
            )
        if target._pending_sources is not None:
            raise PconsError(
                f"Scanner '{scanner.name}': target '{target.qualified_name}' "
                f"passes Target objects as sources, which resolve too late "
                f"for scanning. Name the file paths instead — a generated "
                f"file's path carries its producer's ordering."
            )

        edges = self._governed_edges(target, scanner)
        if not edges:
            raise PconsError(
                f"Scanner '{scanner.name}' is attached to target "
                f"'{target.qualified_name}', but no build edge there has a "
                f"source matching {list(scanner.source_suffixes)}. Attach it "
                f"to the target that consumes those sources, or drop the "
                f"attach."
            )

        scope_id = self._scope_id(scanner, target)
        rel = project._path_resolver.make_execution_relative
        base_rel = f"scan/{scanner.name}"
        build_dir = project.build_dir
        # Node identity uses the canonical (possibly relative) build_dir
        # form; filesystem writes resolve it against the project root so
        # configure-time output doesn't depend on the process cwd.
        build_dir_fs = (
            build_dir if build_dir.is_absolute() else project.root_dir / build_dir
        )
        (build_dir_fs / base_rel).mkdir(parents=True, exist_ok=True)

        manifest_rel = f"{base_rel}/{scope_id}.manifest.json"
        dyndep_rel = f"{base_rel}/{scope_id}.dyndep"
        exports_rel = f"{base_rel}/{scope_id}.exports.json"

        # --- scan edges: one per governed edge -------------------------
        info_nodes: list[FileNode] = []
        manifest_edges: list[dict[str, Any]] = []
        for governed, scanned in edges:
            info_node = self._make_scan_node(target, scanner, env, governed, scanned)
            info_nodes.append(info_node)
            manifest_edges.append(
                self._manifest_edge(scanner, governed, info_node, rel)
            )

        # --- imports: exports of scanned dependency scopes --------------
        import_scopes = [
            scope
            for dep in target.transitive_dependencies()
            if (scope := project._scan_scopes.get((scanner.name, dep.qualified_name)))
        ]

        # --- manifest (configure-written; static facts only) -------------
        # Imported here, not at module level: `python -m pcons.core.collate`
        # is a build-edge subprocess, and a module-level import from this
        # module (which pcons/__init__ loads) would leave collate half-shadowed
        # in sys.modules when runpy executes it.
        from pcons.core.collate import MANIFEST_VERSION, write_text_if_changed

        manifest = {
            "version": MANIFEST_VERSION,
            "scanner": scanner.name,
            "scope": scope_id,
            "dyndep": dyndep_rel,
            "exports_out": exports_rel,
            "imports": [s.exports_rel for s in import_scopes],
            "provide_template": scanner.provide_template,
            "on_unresolved": scanner.on_unresolved,
            "edge_args": (
                {
                    "suffix": scanner.edge_args.suffix,
                    "var": scanner.edge_args.var,
                    "format": {
                        "header": list(scanner.edge_args.format.header),
                        "line": scanner.edge_args.format.line,
                    },
                    "include": scanner.edge_args.include,
                }
                if scanner.edge_args
                else None
            ),
            "edges": manifest_edges,
        }
        write_text_if_changed(
            build_dir_fs / manifest_rel,
            json.dumps(manifest, indent=1, sort_keys=True) + "\n",
        )

        # --- collate edge ------------------------------------------------
        collate_node = project.node(build_dir / dyndep_rel)
        exports_node = project.node(build_dir / exports_rel)
        if collate_node._build_info is not None:
            raise PconsError(
                f"Scanner '{scanner.name}': collate output {dyndep_rel} "
                f"already has a producer."
            )
        manifest_node = project.node(build_dir / manifest_rel)
        collate_inputs: list[FileNode] = [
            *info_nodes,
            manifest_node,
            *[s.exports_node for s in import_scopes],
        ]
        collate_node.add_inputs(collate_inputs)

        outputs: dict[str, Any] = {
            "dyndep": {"path": collate_node.path, "implicit": False},
            "exports": {"path": exports_node.path, "implicit": True},
        }
        if scanner.edge_args:
            for i, (governed, _) in enumerate(edges):
                args_path = governed.path.with_name(
                    governed.path.name + scanner.edge_args.suffix
                )
                outputs[f"args_{i}"] = {"path": args_path, "implicit": True}

        collate_cmd: list[Any]
        collate_vars: dict[str, str] = {}
        if scanner.collate_command is not None:
            collate_cmd = list(scanner.collate_command)
        else:
            # The manifest path rides on a per-edge variable, so every
            # scope's collate shares ONE ninja rule (a rule is identified
            # by its command text; a literal path here would mint a rule
            # per target).
            collate_cmd = [
                sys.executable,
                "-m",
                "pcons.core.collate",
                "--manifest",
                NodeVar("SCAN_MANIFEST"),
            ]
            collate_vars["SCAN_MANIFEST"] = manifest_rel
        collate_node._build_info = {
            "tool": f"collate_{_rule_ident(scanner.name)}",
            "command_var": "collatecmd",
            "command": collate_cmd,
            "sources": collate_inputs,
            "outputs": outputs,
            "restat": True,
            "description": f"COLLATE[{scanner.name}] $out",
            "env": env,
        }
        if collate_vars:
            collate_node._build_info["vars"] = collate_vars
        exports_node._build_info = {"primary_node": collate_node}
        env.register_node(collate_node)
        env.register_node(exports_node)

        # --- govern the edges --------------------------------------------
        for governed, _ in edges:
            bi = governed._build_info
            assert bi is not None
            existing_dyndep = bi.get("dyndep")
            if existing_dyndep not in (None, dyndep_rel):
                raise PconsError(
                    f"Build edge for {rel(governed.path)} is already governed "
                    f"by dyndep file '{existing_dyndep}'; scanner "
                    f"'{scanner.name}' cannot also govern it with "
                    f"'{dyndep_rel}'. One edge takes one dyndep file."
                )
            bi["dyndep"] = dyndep_rel
            if collate_node not in governed.implicit_deps:
                governed.implicit_deps.append(collate_node)
            if scanner.edge_args:
                args_rel = rel(governed.path) + scanner.edge_args.suffix
                node_vars = bi.get("vars")
                if node_vars is None:
                    node_vars = {}
                    bi["vars"] = node_vars
                node_vars[scanner.edge_args.var] = args_rel
                extra = bi.get("extra_command_flags")
                if extra is None:
                    extra = []
                    bi["extra_command_flags"] = extra
                extra.append(scanner.edge_args.token)

        project._scan_scopes[key] = ScanScope(
            scanner=scanner,
            target=target,
            manifest_rel=manifest_rel,
            dyndep_rel=dyndep_rel,
            exports_rel=exports_rel,
            collate_node=collate_node,
            exports_node=exports_node,
            governed=[g for g, _ in edges],
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _governed_edges(
        self, target: Target, scanner: Scanner
    ) -> list[tuple[FileNode, list[FileNode]]]:
        """(build node, its scanned sources) for every governed edge."""
        edges: list[tuple[FileNode, list[FileNode]]] = []
        seen: set[int] = set()
        for node in [*target.intermediate_nodes, *target.output_nodes]:
            if id(node) in seen:
                continue
            seen.add(id(node))
            bi = node._build_info
            if bi is None or "primary_node" in bi:
                continue
            scanned = [
                s
                for s in bi.get("sources", [])
                if isinstance(s, FileNode) and s.path.suffix in scanner.source_suffixes
            ]
            if scanned:
                edges.append((node, scanned))
        return edges

    def _scope_id(self, scanner: Scanner, target: Target) -> str:
        scope_id = _SCOPE_ID_RE.sub("_", target.qualified_name.replace("::", "."))
        claim = (scanner.name, scope_id)
        owner = self._scope_ids.get(claim)
        if owner is not None and owner != target.qualified_name:
            raise PconsError(
                f"Scanner '{scanner.name}': targets '{owner}' and "
                f"'{target.qualified_name}' both map to scope id "
                f"'{scope_id}'. Rename one target."
            )
        self._scope_ids[claim] = target.qualified_name
        return scope_id

    def _make_scan_node(
        self,
        target: Target,
        scanner: Scanner,
        env: Environment,
        governed: FileNode,
        scanned: list[FileNode],
    ) -> FileNode:
        from pcons.core.builder import _tokenize_one

        project = self.project
        info_path = governed.path.with_name(governed.path.name + scanner.info_suffix)
        info_node = project.node(info_path)
        if info_node._build_info is not None:
            raise PconsError(
                f"Scanner '{scanner.name}': scan output "
                f"{info_path} already has a producer (two scanners with the "
                f"same info_suffix governing one edge?)."
            )
        info_node.add_inputs(scanned)
        if scanner.scan_deps:
            info_node.depends([project.node(d) for d in scanner.scan_deps])

        tokens = [_tokenize_one(t) for t in scanner.scan_command]
        info_node._build_info = {
            "tool": f"scan_{_rule_ident(scanner.name)}",
            "command_var": "scancmd",
            "command": tokens,
            "sources": list(scanned),
            "restat": True,
            # Constant description: it participates in the ninja rule hash,
            # so a per-edge description would give every scan its own rule.
            "description": f"SCAN[{scanner.name}] $out",
            "env": env,
        }
        if scanner.scan_depfile:
            info_node._build_info["depfile"] = PathToken(suffix=scanner.scan_depfile)
            info_node._build_info["deps_style"] = scanner.scan_deps_style
        if scanner.scan_vars is not None:
            info_node._build_info["vars"] = scanner.scan_vars(env, scanned, governed)
        env.register_node(info_node)
        return info_node

    def _manifest_edge(
        self,
        scanner: Scanner,
        governed: FileNode,
        info_node: FileNode,
        rel: Callable[[Path | str], str],
    ) -> dict[str, Any]:
        bi = governed._build_info
        assert bi is not None
        all_targets = bi.get("all_targets") or [governed]
        declared = [rel(t.path) for t in all_targets if isinstance(t, FileNode)]
        edge: dict[str, Any] = {
            "out": rel(governed.path),
            "declared_outputs": declared or [rel(governed.path)],
            "info": rel(info_node.path),
            "provide_template": None,
        }
        if scanner.edge_args:
            edge["args_file"] = rel(governed.path) + scanner.edge_args.suffix
        return edge
