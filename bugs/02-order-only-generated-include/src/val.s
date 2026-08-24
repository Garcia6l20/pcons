# SPDX-License-Identifier: MIT
.include "gen.inc"
.globl get_value
.text
get_value:
	movl $VALUE, %eax
	ret
