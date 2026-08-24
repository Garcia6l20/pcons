# A missing std.compat source disables `import std;` on GCC

Status: found by reading, not reproduced. Reproducing it needs a libstdc++
install that ships one of the two module sources and not the other.

No repro directory, this one is a code read.

## What happens

`GccToolchain._setup_std_modules` (`pcons/toolchains/gcc.py:551`) locates both
standard-library module sources in one loop and returns on the first miss:

```python
for logical in ("std", "std.compat"):
    src_path = _find_gcc_std_module_source(compiler_cmd, logical, base_flags)
    if src_path is None:
        return {}, (
            f"`import {logical};` needs the GCC standard-library "
            ...
        )
    sources[logical] = src_path
```

Two consequences when only `std.compat` cannot be located:

- `exports_by_key` comes back empty, so plain `import std;` stops working even
  though its source was found.
- The error text says `` `import std.compat;` needs ... ``, naming a module the
  project may never import. The user reads a message about code they did not
  write.

## Compare with the other toolchains

`LlvmToolchain` walks the same two logical names and skips a missing one
(`pcons/toolchains/llvm.py:752`):

```python
entry = modules.get(logical)
if entry is None:
    logger.warning("%s not in libc++ manifest %s; skipping", logical, manifest)
    continue
```

It warns and continues, so `std` still gets its edge when only `std.compat` is
absent. The MSVC path does the same.

## Suggested direction

Make GCC behave like the other two: warn on the missing logical name, continue
the loop, and describe the failure only for the module that is actually
missing. Report an error only when neither source can be found.
