/* SPDX-License-Identifier: GPL-2.0 */
/*
 * position=1's two bytes per sample (e1_hook.c's m1 comment and PLAN.md say what
 * they're for). Arithmetic only, with no register access, so that
 * tests/m1_crumbs_test.py can compile this header on its own and run e1-hook/m1-crumbs'
 * decoder against the same bytes the module would send.
 *
 * Byte A:
 *   bits 3:0  PHASE (0x2922c0110) bits 3:0
 *   bit 4     HS_A (0x2925c4004) bit 31
 *   bit 5     HS_A bit 24
 *   bits 7:6  the state word (0x2922b801c): 0 is 0xa03, 1 is 1, 2 is 0xa02, 3 is
 *             any other value
 * Byte B:
 *   bits 2:0  SIO_CPU's word (0x28e580188) as a class, below
 *   bit 3     WD2's control (0x2922b002c) has RESET_EN
 *   bit 4     WD2's count (0x2922b0020) is within 0.25 s of CPU0's own time
 *             since the first commit write
 *   bit 5     0x2922c0044 bit 2
 *   bit 6     0x2922c0044 bit 3
 *   bit 7     0x2922c0040 bit 1
 */
#ifndef M1_POSITION_H
#define M1_POSITION_H

#ifndef __KERNEL__
#include <stdbool.h>
#include <stdint.h>
typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
#endif

#define M1_POS_RESET_EN		(1U << 2)	/* WD2's control: RESET_EN, as M1_WD_RESET_EN */

/* SIO_CPU's classes, tested in this order so that they can't overlap. */
#define M1_SIO_FAILED		0	/* all ones: a failed read */
#define M1_SIO_T0_BIT11		1	/* target 0, bit 11 set */
#define M1_SIO_T0		2	/* target 0, bit 11 clear */
#define M1_SIO_NOT_F		3	/* another target, 27:24 not f */
#define M1_SIO_F_NOT_ON		4	/* another target, 27:24 f, actual not f */
#define M1_SIO_F_ON		5	/* another target, 27:24 f, actual f */

static inline u8 m1_pos_sio_class(u32 v)
{
	if (v == 0xffffffffU)
		return M1_SIO_FAILED;
	if (!(v & 0xf))
		return (v & (1U << 11)) ? M1_SIO_T0_BIT11 : M1_SIO_T0;
	if (((v >> 24) & 0xf) != 0xf)
		return M1_SIO_NOT_F;
	return ((v >> 4) & 0xf) == 0xf ? M1_SIO_F_ON : M1_SIO_F_NOT_ON;
}

static inline u8 m1_pos_state_class(u32 v)
{
	switch (v) {
	case 0xa03:
		return 0;
	case 1:
		return 1;
	case 0xa02:
		return 2;
	}
	return 3;
}

static inline u8 m1_pos_byte_a(u32 phase, u32 hs_a, u32 state)
{
	return (u8)((phase & 0xf) | ((hs_a >> 31) & 1) << 4 | ((hs_a >> 24) & 1) << 5 |
		    m1_pos_state_class(state) << 6);
}

/* elapsed: CPU0's ticks since the first commit write; tps: the ticks in a second. */
static inline bool m1_pos_count_near(u32 count, u64 elapsed, u64 tps)
{
	u64 c = count;

	return (c > elapsed ? c - elapsed : elapsed - c) <= tps / 4;
}

static inline u8 m1_pos_byte_b(u32 sio, u32 wd_ctrl, bool count_near, u32 gate44, u32 gate40)
{
	return (u8)(m1_pos_sio_class(sio) | (wd_ctrl & M1_POS_RESET_EN ? 1U << 3 : 0) |
		    (count_near ? 1U << 4 : 0) | ((gate44 >> 2) & 1) << 5 | ((gate44 >> 3) & 1) << 6 |
		    ((gate40 >> 1) & 1) << 7);
}

#endif /* M1_POSITION_H */
