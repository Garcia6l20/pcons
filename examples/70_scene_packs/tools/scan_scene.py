# SPDX-License-Identifier: MIT
"""Report what a set of .scene files provides and requires.

Usage: ``scan_scene.py <pack> <scene>... <scan-info.json>``, where ``<pack>``
is the pack these scenes are being compiled into. One scan edge covers one
pack, so it may be handed several scenes; their provides and requires merge.
The output is pcons's scan-info schema (see ``pcons/core/collate.py``).
"""

import json
import sys
from pathlib import Path


def main() -> int:
    pack, *scenes, out = sys.argv[1:]
    provides: list[str] = []
    requires: list[str] = []
    for scene in scenes:
        for line in Path(scene).read_text(encoding="utf-8").splitlines():
            match line.split():
                case ["name", name]:
                    provides.append(name)
                case ["ref", name]:
                    requires.append(name)
    info = {
        "version": 1,
        # Every scene in the pack is served by the pack, so they share a path.
        "provides": [{"name": name, "path": pack} for name in provides],
        # A scene packed alongside the one it refs needs nothing from outside.
        "requires": sorted(set(requires) - set(provides)),
    }
    Path(out).write_text(json.dumps(info, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
