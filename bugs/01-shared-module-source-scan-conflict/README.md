# Two targets sharing one module interface abort configure

Status: regression. Works on `main` (v0.28.0), fails on `feature/scanners`.

## What happens

Two `Program` targets list the same `.cppm` and use the same environment.
Configure stops with:

```
ERROR: Scanner 'cxx-modules': scan output build/obj.a/src/util.cppm.o.ddi
already has a producer (two scanners with the same info_suffix governing one
edge?).
```

On `main` the same script builds, and both programs run.

## Why

`CompileLinkFactory._object_cache` returns the same `FileNode` for
`src/util.cppm` in both targets, because the environment and the flags are
identical.

`collect_module_scopes` claims that node for target `a`.
`ScannerResolver._governed_edges` (`pcons/core/scan.py:586`) then walks target
`b` and finds the same node again, since it iterates
`target.intermediate_nodes` with no notion of who already owns the node.
`_make_scan_node` (`pcons/core/scan.py:634`) derives the `.ddi` path from the
object path, sees a producer on it, and raises.

The `.ddi` path is `build/obj.a/src/util.cppm.o.ddi` in both cases: it names
target `a` even while resolving target `b`, which is the clearest sign the
node is shared.

## Reproduce

```
../run.sh 01-shared-module-source-scan-conflict
```

Expected on a fixed build: configure succeeds, `build/a` prints `a=42` and
`build/b` prints `b=42`.

## Requires

clang++ with `clang-scan-deps`.
