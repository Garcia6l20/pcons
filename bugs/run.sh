#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Run one bug reproduction, with the pcons checkout of your choice.
#
#   ./run.sh 01-shared-module-source-scan-conflict
#   PCONS=/path/to/other/checkout ./run.sh 01-shared-module-source-scan-conflict
#
# Without PCONS, `pcons` is taken from PATH. With PCONS, that checkout is put
# on PYTHONPATH and run as `python -m pcons`, which needs pcons' runtime
# dependencies importable (the repo venv does).

set -u

here=$(cd "$(dirname "$0")" && pwd)
bug=${1:?usage: run.sh <bug-directory>}

cd "$here/$bug" || exit 1
rm -rf build

if [ -n "${PCONS:-}" ]; then
    PYTHONPATH=$PCONS python -m pcons
else
    pcons
fi
echo "--- configure exit: $? ---"

ninja -C build
echo "--- ninja exit: $? ---"
