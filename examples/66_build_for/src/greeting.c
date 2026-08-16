/* SPDX-License-Identifier: MIT */
#include "greeting.h"

const char *greeting(void)
{
#if defined(ALT_BUILD)
    return "hello from the alt build";
#elif defined(DEFAULT_BUILD)
    return "hello from the default build";
#else
    return "hello from an unconfigured build";
#endif
}
