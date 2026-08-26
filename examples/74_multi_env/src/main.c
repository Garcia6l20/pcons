/* SPDX-License-Identifier: MIT */
#include <stdio.h>

#include "checksum.h"

int main(void)
{
    static const uint8_t frame[] = {1, 2, 3, 4};

    printf("%s checksum %u\n", checksum_flavor(),
           (unsigned)checksum(frame, sizeof(frame)));
    return 0;
}
