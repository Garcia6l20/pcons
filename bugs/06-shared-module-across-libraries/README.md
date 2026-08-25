# A module interface shared by two libraries leaves one without exports

Status: regression. Works on `main` (v0.28.0), fails on `feature/scanners`,
both before Gary's review fixes (`06a7aba`) and after them (`b2543aa`).

## What happens

Two static libraries list `src/util.cppm`. `one` also has a source of its own,
`two` has nothing else. A program links `two` and imports the module:

```
../src/consumer.cpp:2:8: fatal error: module 'util' not found
```

On `main` the same script builds, and `consumer` prints `consumer=42`.

Remove the `one` library and it builds here too. So adding an unrelated second
library that happens to list the same module interface breaks a consumer that
worked.

## Why

The resolver's object cache gives both libraries one object node. `libtwo.a` is
archived from the object built under `one`:

```
build libone.a: ar_libcmd_... obj.one/src/util.cppm.o obj.one/src/extra.cpp.o
build libtwo.a: ar_libcmd_... obj.one/src/util.cppm.o
```

`ScannerResolver` gives that node to `one`, the first scope to claim it. `two`
then has no edge of its own left, and takes the early return added in
`pcons/core/scan.py:408`:

```python
edges = own_edges
if not edges:
    # Every governed edge belongs to another scope; there is nothing
    # of this target's own to scan, and its shared objects are
    # already ordered by their owners' dyndep files.
    return
```

Returning there skips the `project._scan_scopes[key] = scope_record` at the end
of the function, so no scope is ever recorded for `two`. The build directory
shows it: there are `...one.*` and `...consumer.*` files under
`build/scan/cxx-modules/` and no `...two.*` at all.

`consumer` builds its import list from its dependencies' scopes:

```python
import_scopes = [
    scope
    for dep in target.transitive_dependencies()
    if (scope := project._scan_scopes.get((scanner.name, dep.qualified_name)))
]
```

`two` is not in `_scan_scopes`, so the lookup yields nothing and the manifest
comes out with `"imports": []`. The compiler is then handed a modmap that never
mentions `util`.

The ordering claim in the comment holds: the shared object is built, and
`libtwo.a` contains it, so the link would succeed. What is missing is the
export side. A target that owns no edge still needs to re-export what its
owners provide.

## Reproduce

```
../run.sh 06-shared-module-across-libraries
```

Expected: `build/consumer` prints `consumer=42`.

Compare with a checkout of `main`:

```
PCONS=/path/to/main-worktree ../run.sh 06-shared-module-across-libraries
```

## Suggested direction

Record a scope for `two` instead of returning: one with no edges of its own,
whose imports are the owner scopes collected just above. Dependents then find
it and pick up the owner's exports through it, which is what the owner/import
rule already does for a target that has both its own edges and shared ones.

## Requires

clang++ with `clang-scan-deps`.
