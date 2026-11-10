/* SPDX-License-Identifier: GPL-2.0 */
/*
 * WD1 as a marker of how far the engine's entry pair gets (PLAN.md, "The order
 * after run 16"; RESEARCH.md, "After run 16"): the registers, the two values
 * written, and the two bits that say whether the marker stood. Arithmetic only,
 * with no register access, for e1_hook.c and tests/ alike, as wd2_bite.h is.
 *
 * WD1 is the wdt block's second watchdog, the kernel's own (apple_wdt). Awake
 * the kernel keeps it armed with a 30 s bite and pings it, and apple_wdt's
 * suspend callback stops it before e1_hook runs, so a marker can only be set
 * inside the replay. B2, the second sequence of the entry pair, zeroes WD1's
 * count at its op 223 of 246, after its whole power-down, and writes no
 * watchdog's control; A2 stops WD2 only at the pair's very end.
 *
 * So in step 4, a moment before the commit, e1_hook wd1_mark=1 writes WD1's bite
 * := 150 s, its count := 60 s, and then RESET_EN (the count write is new in kind:
 * the kernel and the engine only ever write 0 there). Corey was shown these three
 * writes on 2026-11-10 and answered with the standing go-ahead to build new steps
 * without waiting. With WD2's bite at 120 s (wd2_bite=1), the restart then reads:
 *   about 90 s after the commit   WD1, its count never zeroed: B2 didn't reach
 *                                 op 223
 *   about 120 s                   WD2: B2 zeroed WD1's count, and the pair
 *                                 stopped in its last twenty-odd ops (or WD1
 *                                 can't reset the SoC in this state, which this
 *                                 run alone doesn't exclude)
 *   about 150 s, or no restart    the pair finished (A2 stopped WD2), and WD1,
 *                                 zeroed at about the commit, ended it as a
 *                                 dead-man; no restart if WD1 doesn't count there
 * and only if the marker stood, and only as far as the run's last stage and
 * wd2_bite=1's sample bear a reading out: the time names no watchdog by itself
 * (m1-crumbs' mark_reading() has the conditions). The two bits, 7:6 of
 * fabric_up.h's byte, from a read of WD1 just before the commit:
 *   bit 0     its control showed RESET_EN and its bite read 150 s
 *   bit 1     its count read 60 s or up to 5 s more: the count write took, and
 *             the count ran on from it
 * Anything but both set: the restart's time says nothing about B2.
 */
#ifndef WD1_MARK_H
#define WD1_MARK_H

#ifndef __KERNEL__
#include <stdint.h>
typedef uint8_t u8;
typedef uint32_t u32;
#endif

#define WD1_WDT			0x2922b0000ULL	/* the watchdog page */
#define WD1_COUNT		0x10
#define WD1_BITE		0x14
#define WD1_CTRL		0x1c
#define WD1_RESET_EN		(1U << 2)

#define WD1_HZ			24000000U
#define WD1_MARK_BITE		(150U * WD1_HZ)	/* 0xd693a400 */
#define WD1_MARK_COUNT		(60U * WD1_HZ)	/* 0x55d4a800 */
#define WD1_MARK_SLACK		(5U * WD1_HZ)	/* how far the count may have run on by the look */

#define M1_WD1_ARMED		(1U << 0)
#define M1_WD1_LEAD		(1U << 1)
#define M1_WD1_STANDS		(M1_WD1_ARMED | M1_WD1_LEAD)

/* ctl, bite, count: WD1 as read just before the commit. */
static inline u8 m1_wd1_bits(u32 ctl, u32 bite, u32 count)
{
	return (u8)(((ctl & WD1_RESET_EN) && bite == WD1_MARK_BITE ? M1_WD1_ARMED : 0) |
		    (count >= WD1_MARK_COUNT && count - WD1_MARK_COUNT <= WD1_MARK_SLACK ? M1_WD1_LEAD : 0));
}

#endif /* WD1_MARK_H */
