# SPDX-License-Identifier: MIT
"""Concatenate files: ``concat.py <out> <in>...``."""

import sys
from pathlib import Path


def main() -> int:
    out, *parts = sys.argv[1:]
    text = "".join(Path(p).read_text(encoding="utf-8") for p in parts)
    Path(out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
