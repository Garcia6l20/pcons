# SPDX-License-Identifier: MIT
import sys
import time

time.sleep(2)

with open(sys.argv[1], "w") as f:
    f.write("#pragma once\nconstexpr int kGenerated = 7;\n")
