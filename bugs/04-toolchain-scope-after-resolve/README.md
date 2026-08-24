# Each toolchain's after_resolve scopes the whole project

Status: not a regression, `main` (v0.28.0) mis-scopes the same way. What the
branch changes is the failure: it now stops at configure with a message that
blames the user.

## What happens

Two environments, one gcc and one llvm, in one project. Only the gcc target
uses modules.

`main` configures, then the build fails because clang module flags land on the
g++ command line:

```
g++: error: unrecognized command-line option '-fmodule-output=cxx_modules/<hash>/mod.pcm'
g++: error: unrecognized command-line option '-fprebuilt-module-path=cxx_modules/<hash>'
```

`feature/scanners` stops at configure:

```
ERROR: Two conflicting scanners named 'cxx-modules' are attached to target
'two_cxx_toolchains::with_gcc' - their declarations differ. Declare the scanner
once and attach that one everywhere.
```

The user declared no scanner. There is nothing in the build script to fix.

The g++ message above is quoted in English; g++ localizes it.

## Why

Each toolchain's `after_resolve` receives the project-wide
`source_obj_by_language` and passes it straight to `collect_module_scopes`:

| toolchain | line |
| --- | --- |
| llvm | `pcons/toolchains/llvm.py:501` |
| gcc | `pcons/toolchains/gcc.py:338` |
| msvc | `pcons/toolchains/msvc.py:908` |

So the llvm toolchain claims objects that the gcc toolchain built, and the
other way round. Both then attach their own `cxx-modules` scanner to the same
target, with different scan commands, and `ScannerResolver` rejects the pair.

The map should be filtered to the objects whose environment actually uses this
toolchain before `collect_module_scopes` sees it.

## Reproduce

```
../run.sh 04-toolchain-scope-after-resolve
```

Expected on a fixed build: `build/with_gcc` prints `42` and `build/plain_llvm`
prints `plain`.

## Requires

g++ 15+ with module support, and clang++ with `clang-scan-deps`.
