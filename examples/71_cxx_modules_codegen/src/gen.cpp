// SPDX-License-Identifier: MIT
// Writes a C++ source to argv[1]. The source it writes imports module `m`,
// so the generated file is itself a module consumer -- but issue #105
// deadlocked even when the generated source imported nothing at all.
#include <cstdio>

int main(int argc, char** argv) {
    if (argc < 2) {
        return 1;
    }
    FILE* out = std::fopen(argv[1], "w");
    if (out == nullptr) {
        return 1;
    }
    std::fputs("import m;\n\nint main() { return answer() - 42; }\n", out);
    std::fclose(out);
    return 0;
}
