# SPDX-License-Identifier: MIT
"""Compile .scene files into a .pack, embedding a digest of every pack it refs.

Usage: ``pack_scene.py <out.pack> <scene|refs-file>...``

The packer never parses a ``ref`` line. Which packs to read is decided by the
scanner and delivered in the ``.refs`` file collate writes for this edge, one
``<logical-name> <path-to-pack>`` line each -- so the referenced packs must
already be built, which is exactly the ordering the dyndep file enforces.
"""

import hashlib
import sys
from pathlib import Path


def main() -> int:
    out, *args = sys.argv[1:]
    scenes = [a for a in args if not a.endswith(".refs")]
    refs: dict[str, str] = {}
    for args_file in (a for a in args if a.endswith(".refs")):
        for line in Path(args_file).read_text(encoding="utf-8").splitlines():
            if line.strip():
                name, path = line.split(maxsplit=1)
                refs[name] = path

    lines = ["PACK"]
    for name in sorted(refs):
        digest = hashlib.sha256(Path(refs[name]).read_bytes()).hexdigest()[:16]
        lines.append(f"embeds {name} {digest}")
    for scene in scenes:
        for line in Path(scene).read_text(encoding="utf-8").splitlines():
            if line.strip() and line.split()[0] not in ("name", "ref"):
                lines.append(line)

    Path(out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
