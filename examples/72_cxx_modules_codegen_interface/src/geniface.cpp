// SPDX-License-Identifier: MIT
// Writes a C++20 module interface unit to argv[1].
#include <cstdio>

int main(int argc, char** argv) {
    if (argc < 2) {
        return 1;
    }
    FILE* out = std::fopen(argv[1], "w");
    if (out == nullptr) {
        return 1;
    }
    std::fputs("export module gen;\n\nexport int value() { return 7; }\n", out);
    std::fclose(out);
    return 0;
}
