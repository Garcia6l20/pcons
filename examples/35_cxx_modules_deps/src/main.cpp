// SPDX-License-Identifier: MIT
// C++20 consumer: imports MyMod
import Mod1;
import Mod2;

#include <use.hpp>

int main() { return check(mod1::answer(), mod2::answer()) ? 0 : 1; }
