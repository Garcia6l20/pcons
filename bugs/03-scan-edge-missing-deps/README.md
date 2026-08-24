# A scan edge does not inherit the deps of the edge it governs

Status: not a regression. `main` (v0.28.0) has the same hole, with a different
message. Filed because the branch rebuilds this machinery and could close it.

## What happens

`src/main.cpp` includes `gen.h`, written by a `Command`. On a clean build the
scan can start before the header exists.

`feature/scanners`:

```
../src/main.cpp:2:10: fatal error: 'gen.h' file not found
```

`main`, same cause, from the project-wide scan step:

```
FAILED: [code=1] cxx_modules.dyndep
.../src/main.cpp:2:10: fatal error: 'gen.h' file not found
```

A second `ninja` run succeeds on both branches, because the header exists by
then. `run.sh` shows this as a non-zero configure exit followed by a zero ninja
exit.

## Why

The compile edge waits for the header, the scan edge that governs it does not:

```
build obj.app/src/main.cpp.o.ddi: scan_cxx_modules_scancmd_... $topdir/src/main.cpp
build obj.app/src/main.cpp.o: cxx_modobjcmd_... $topdir/src/main.cpp | gen.h || scan/...dyndep
```

`_make_scan_node` (`pcons/core/scan.py:634`) calls
`info_node.add_inputs(scanned)` and adds `scanner.scan_deps`, and nothing else.
The implicit and order-only deps of the governed node are not copied over.

The scanner runs a real compiler front end, so it needs the same generated
inputs the compile needs.

## Reproduce

```
../run.sh 03-scan-edge-missing-deps
```

The sleep in `src/gen.py` is what makes it deterministic. Remove it and the
build usually passes, on both branches.

## Requires

clang++ with `clang-scan-deps`.
