#pragma once

// Included by main.cpp only. The rebuild tests later give this header an
// `import`, which must reorder the build without re-running pcons.
inline bool check(int a, int b) {
    return a == b && a == 42;
}
