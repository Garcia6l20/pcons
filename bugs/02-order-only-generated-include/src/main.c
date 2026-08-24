// SPDX-License-Identifier: MIT
#include <stdio.h>
int get_value(void);
int helper(void);
int main(void) { printf("%d\n", get_value() * helper()); return 0; }
