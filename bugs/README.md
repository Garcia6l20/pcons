# Bug reproductions

Each directory is a standalone pcons project that shows one defect. Every
`README.md` states what happens, why, and how to run it.

Run one against the pcons on your PATH:

```
./run.sh 01-shared-module-source-scan-conflict
```

Run the same one against another checkout, to compare two branches:

```
PCONS=/path/to/pcons-worktree ./run.sh 01-shared-module-source-scan-conflict
```

The script prints the configure exit code and the ninja exit code, so a failure
at configure is easy to tell from a failure at build.

## Index

| # | Bug | Status | Where |
| --- | --- | --- | --- |
| 01 | Two targets sharing one `.cppm` abort configure | regression on `feature/scanners` | `pcons/core/scan.py:586` |
| 02 | Generated headers became order-only for compiles with no depfile | regression on `feature/scanners` | `pcons/tools/compile_link.py:912` |
| 03 | A scan edge does not inherit the deps of the edge it governs | present on `main` too | `pcons/core/scan.py:634` |
| 04 | Each toolchain's `after_resolve` scopes the whole project | present on `main` too | `llvm.py:501`, `gcc.py:338`, `msvc.py:908` |
| 05 | A missing `std.compat` source disables `import std;` on GCC | read only, no repro | `pcons/toolchains/gcc.py:551` |
| 06 | A module interface shared by two libraries leaves one without exports | regression, still open | `pcons/core/scan.py:408` |

"regression" means the reproduction works on `main` at v0.28.0 and fails on
`feature/scanners`. 02 is the one that produces a wrong binary without any
error message.

01 to 05 are fixed on `feature/scanners` as of `4f3309c`. 06 was found while
re-checking those fixes and is still open: it fails both before them
(`06a7aba`) and after. The "Where" column points at the code as it stood
when each bug was filed, so for 01 to 05 those lines have since moved.

## Requirements

| bug | needs |
| --- | --- |
| 01, 03, 06 | clang++ with `clang-scan-deps` |
| 02 | gcc and GNU as, x86-64 |
| 04 | g++ 15+ with module support, and clang++ with `clang-scan-deps` |

Checked with g++ 16.2.1, clang 22.1.8, ninja 1.13 on Linux.
