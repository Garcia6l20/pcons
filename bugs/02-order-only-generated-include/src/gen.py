# SPDX-License-Identifier: MIT
import sys

VALUE = 7

with open(sys.argv[1], "w") as f:
    f.write(f".set VALUE, {VALUE}\n")
