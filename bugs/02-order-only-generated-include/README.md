# Generated headers became order-only for compiles that have no depfile

Status: regression. Correct on `main` (v0.28.0), wrong on `feature/scanners`.

## What happens

`app` links `helper`, and `helper` links a `Command` that writes
`build/gen.inc`. `src/val.s` does `.include "gen.inc"`.

Build, then change the value written into `gen.inc`, then rebuild:

- `main` recompiles `val.s.o` and the program prints the new value.
- `feature/scanners` does not recompile it, and the program keeps printing the
  old value.

The generated ninja edge shows it:

```
main:               build obj.app/src/val.s.o: cc_objcmd_... $topdir/src/val.s | gen.inc
feature/scanners:   build obj.app/src/val.s.o: cc_objcmd_... $topdir/src/val.s || gen.inc
```

## Why

`pcons/tools/compile_link.py` used to push a dependency's non-link outputs into
`inter.implicit_deps` for every intermediate node. The branch replaces that with
`_order_compiles_after_dependency_outputs`, which calls
`node.order_after(dep_aux)`, so ninja gets `||` instead of `|`.

Its docstring gives the reason:

> order-only, so regenerating the file doesn't recompile sources that never
> read it. From the first build onward the depfile reports the ones that did.

That holds only for handlers that emit a depfile. These do not:

| handler | file |
| --- | --- |
| `.s` (already preprocessed assembly) | `pcons/toolchains/unix.py:303` |
| MSVC `.rc` | `pcons/toolchains/_msvc_compat.py:108` |
| MASM `.asm` | `pcons/toolchains/_msvc_compat.py:111` |
| `metal` | `pcons/toolchains/llvm.py:440` |
| gfortran | `pcons/toolchains/gfortran.py:230` |

For those, nothing records the include, so the object never becomes dirty
again. The build is silently wrong: it succeeds and produces a stale binary.

`.S` (uppercase, preprocessed) is fine, it carries a depfile.

## Reproduce

```
../run.sh 02-order-only-generated-include
./build/app                     # prints 7

sed -i 's/VALUE = 7/VALUE = 9/' src/gen.py
ninja -C build
./build/app                     # prints 7 on this branch, 9 on main
```

Reset with `sed -i 's/VALUE = 9/VALUE = 7/' src/gen.py`.

## Suggested direction

Keep the order-only edge when the handler has a depfile, keep the implicit edge
when it does not. The handler is known at that point: `SourceHandler.depfile`
is `None` for exactly the cases above.

## Requires

gcc and GNU as, x86-64. The assembly is three instructions, any architecture
would do with a different mov.
