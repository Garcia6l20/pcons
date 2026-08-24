# SPDX-License-Identifier: MIT
"""The build directory's C++ module scan cache, now only a file name.

Scanning used to run at configure time, and its results were memoized here so
an unchanged translation unit was not rescanned. The per-TU scan edges the
:class:`~pcons.core.scan.Scanner` primitive generates make that cache
unnecessary: Ninja keeps each scan's answer, and its depfile decides when to
run again. What remains is the name, so ``pcons cache clear`` can still remove
the file an older pcons left in a build directory.
"""

from __future__ import annotations

CACHE_FILE = "pcons_scan_cache.json"
