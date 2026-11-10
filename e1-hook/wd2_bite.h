/* SPDX-License-Identifier: GPL-2.0 */
/*
 * WD2's bite, written directly (PLAN.md, "The order after run 14"; RESEARCH.md,
 * "M1 run 14"): the register, its three known values, and the byte CPU0 records
 * after the commit. Arithmetic only, with no register access, for e1_hook.c,
 * wd2_poke.c and tests/ alike, as recfg_bite.h and m1_position.h are.
 *
 * WD2 is the wdt block's third watchdog (upstream's apple_wdt.c names its words;
 * Linux drives only WD1). The engine's table-A sequences arm it: each opens by
 * writing control 0, count 0, bite 30 s, control 4 (RESET_EN), and closes by
 * writing control 0. Awake it isn't armed, and its bite reads 90 s on this Mac
 * (runs 2 and 3's load logs). Corey's OK of 2026-11-10 is for this one register
 * and one value, 120 s, written twice: once from user space, awake (wd2_poke: the
 * control runs 13 and 14 lacked), and once by CPU0 after the commit (e1_hook
 * wd2_bite=1). No other value is written, the 90 s included.
 *
 * The sample, wd2_bite=1's byte at 0x6005, sent in one command with its stage
 * (0x57, or 0x56 if CPU0 had an exception pending after the reads), once CPU0
 * has let the other cores go after the commit:
 *   bits 1:0  WD2's bite as read before the write: 0 another value, 1 the
 *             30 s every A sequence's opening writes (A2's, while PHASE reads 4
 *             or 5), 2 the new 120 s, 3 the value it read awake at load. The
 *             codes are chosen so that 0xfe, the parked cores, which stage 0x4e
 *             leaves in this byte, can't read as the sample that's expected
 *             (bit 0 is never set there)
 *   bit 2     WD2's control showed RESET_EN within 0.3 s, and so CPU0 made its
 *             one write: it makes none otherwise
 *   bit 3     the bite read the new value after that
 *   bit 4     the control still showed RESET_EN after that
 *   bit 5     the count read differently on a second read after that: WD2 was
 *             still counting
 *   bits 7:6  PHASE (0x2922c0110) bits 3:0, read last: 0 for 0, 1 for 4, 2 for
 *             5, 3 for anything else (the sequences write 0 to 0xd; A2 writes 4,
 *             5 and 7)
 */
#ifndef WD2_BITE_H
#define WD2_BITE_H

#ifndef __KERNEL__
#include <stdint.h>
typedef uint8_t u8;
typedef uint32_t u32;
#endif

#define WD2_WDT			0x2922b0000ULL	/* the watchdog page */
#define WD2_COUNT		0x20
#define WD2_BITE		0x24
#define WD2_CTRL		0x2c
#define WD2_RESET_EN		(1U << 2)

#define WD2_HZ			24000000U
#define WD2_BITE_A2		(30U * WD2_HZ)	/* 0x2aea5400: what every A sequence's opening writes */
#define WD2_BITE_AWAKE		(90U * WD2_HZ)	/* 0x80befc00: what it reads awake */
#define WD2_BITE_NEW		(120U * WD2_HZ)	/* 0xaba95000: the one value written here */

#define M1_WD2_PRE_OTHER	0
#define M1_WD2_PRE_A2		1
#define M1_WD2_PRE_NEW		2
#define M1_WD2_PRE_AWAKE	3

/* awake: WD2's bite as the load read it (the new value, if wd2_poke's write took). */
static inline u8 m1_wd2_pre(u32 bite, u32 awake)
{
	if (bite == WD2_BITE_A2)
		return M1_WD2_PRE_A2;
	if (bite == WD2_BITE_NEW)
		return M1_WD2_PRE_NEW;
	return bite == awake ? M1_WD2_PRE_AWAKE : M1_WD2_PRE_OTHER;
}

static inline u8 m1_wd2_phase(u32 phase)
{
	switch (phase & 0xf) {
	case 0:
		return 0;
	case 4:
		return 1;
	case 5:
		return 2;
	}
	return 3;
}

/*
 * pre: the bite before the write; ctl: WD2's control as last read in the wait
 * for RESET_EN (the write is made only if it shows); post, after: the bite and
 * the control read after it; c0, c1: the count, read twice after that.
 */
static inline u8 m1_wd2_sample(u32 pre, u32 awake, u32 ctl, u32 post, u32 after, u32 c0, u32 c1, u32 phase)
{
	return (u8)(m1_wd2_pre(pre, awake) | (ctl & WD2_RESET_EN ? 1U << 2 : 0) |
		    (post == WD2_BITE_NEW ? 1U << 3 : 0) | (after & WD2_RESET_EN ? 1U << 4 : 0) |
		    (c1 != c0 ? 1U << 5 : 0) | (u32)m1_wd2_phase(phase) << 6);
}

#endif /* WD2_BITE_H */
