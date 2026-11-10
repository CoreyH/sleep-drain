/* SPDX-License-Identifier: GPL-2.0 */
/*
 * The fabric's p-state, put back where macOS has it before the table (PLAN.md,
 * "The order after run 16"; RESEARCH.md, "After run 16"): the word, the one
 * value written, and the byte that records how it went. Arithmetic only, with no
 * register access, for e1_hook.c and tests/ alike, as wd2_bite.h is.
 *
 * The word, 0x28e20c000 (aurora's apple-pmgr-misc calls it fabric-ps): bits 3:0
 * the p-state asked for, bits 7:4 the one it's at, bits 11:8 a third copy that
 * the engine's own wait reads, bit 31 busy. It reads 0x333 awake. This kernel's
 * suspend_noirq writes 1 to its low nibble at every suspend, and e1_hook runs
 * after that. A2's first step of its own then asks for 2 and waits, with no
 * timeout, for bits 11:8 to read 2 with bit 31 clear: written for a fabric that
 * comes down from 3, as macOS has it there (inferred), not up from 1 after the
 * clocks are parked.
 *
 * Corey's OK of 2026-11-10 is for this one word and one value: its low nibble
 * := 3, the value it has awake, after slpa and before the clocks are parked,
 * written as the kernel's own resume writes it (the word read, the nibble
 * replaced). e1_hook fabric_up=1 makes that write once, at the start of step 5.
 *
 * The record. From step 8's command on, a fabric_up=1 run's stages are its own
 * (0x52 in place of 0x4e; 0x53 and 0x51 in place of 0x57 and 0x56), because
 * 0x6007 changes meaning there, from the cores that held to this byte:
 *   bit 0     ps_sio, UART_P and SPI_P read on (sio_on=1's bit, as before)
 *   bit 1     all seven other cores wrote their stop bit and held
 *   bits 3:2  the word before the write: 0 another value, 1 0x111 (asked down
 *             to 1 and there), 2 asked down to 1 and not there yet, 3 0x333
 *             (never lowered)
 *   bit 4     within 10 ms of the write it read 0x333 with bit 31 clear
 *   bit 5     it read that again at the end of step 5, after the ClkGen park
 *             and PLL-off
 *   bits 7:6  wd1_mark=1's two bits (wd1_mark.h): 3 if WD1 stood as a marker
 *             just before the commit, 0 in a run without one
 * Step 7's command leaves the cores that held in the same place, 0xff as a rule,
 * and the byte is the last of stage 0x52's command: if that command landed
 * without it, the old 0xff would read as every bit set. The steps' outcomes at
 * 0x6012 go out right after that command and only if it didn't fail, so
 * m1-crumbs reads the byte only when their marker is at 0x6013.
 */
#ifndef FABRIC_UP_H
#define FABRIC_UP_H

#ifndef __KERNEL__
#include <stdbool.h>
#include <stdint.h>
typedef uint8_t u8;
typedef uint32_t u32;
#endif

#define FAB_PS			0x28e20c000ULL
#define FAB_ASKED		0xfU		/* bits 3:0: the p-state asked for */
#define FAB_AWAKE		3U		/* the one value written to it */
#define FAB_SUSPEND		1U		/* what suspend_noirq leaves there */
#define FAB_WORD		0x80000fffU	/* the three copies and busy */
#define FAB_AT_AWAKE		0x333U
#define FAB_AT_SUSPEND		0x111U

/* fabric_up=1's stages, from step 8's command on (the comment above). */
#define M1_FAB_STAGE_PRE	0x52	/* in place of 0x4e */
#define M1_FAB_STAGE		0x53	/* in place of 0x57 */
#define M1_FAB_STAGE_PEND	0x51	/* in place of 0x56 */

#define M1_FAB_PRE_OTHER	0
#define M1_FAB_PRE_DOWN		1
#define M1_FAB_PRE_ASKED	2
#define M1_FAB_PRE_AWAKE	3

/* What fabric_up=1 writes: the word as read, its low nibble replaced. */
#define FAB_UP(v)		(((v) & ~FAB_ASKED) | FAB_AWAKE)
static inline u32 m1_fab_up(u32 v)
{
	return FAB_UP(v);
}

/* The word reads as it does awake: every copy at 3, and not busy. */
static inline bool m1_fab_awake(u32 v)
{
	return (v & FAB_WORD) == FAB_AT_AWAKE;
}

/* The word before the write. */
static inline u8 m1_fab_pre(u32 v)
{
	if (m1_fab_awake(v))
		return M1_FAB_PRE_AWAKE;
	if ((v & FAB_WORD) == FAB_AT_SUSPEND)
		return M1_FAB_PRE_DOWN;
	return (v & FAB_ASKED) == FAB_SUSPEND ? M1_FAB_PRE_ASKED : M1_FAB_PRE_OTHER;
}

/*
 * held: 0x6007 as it was (bit 0 sio_on=1's, bits 7:1 the cores that held);
 * pre: the word before the write; set: m1_fab_awake() within 10 ms of it; end:
 * m1_fab_awake() at the end of step 5; wd1: m1_wd1_bits(), or 0 without
 * wd1_mark=1.
 */
static inline u8 m1_fab_byte(u8 held, u32 pre, bool set, bool end, u8 wd1)
{
	return (u8)((held & 1U) | ((held & 0xfeU) == 0xfeU ? 1U << 1 : 0) | (u32)m1_fab_pre(pre) << 2 |
		    (set ? 1U << 4 : 0) | (end ? 1U << 5 : 0) | ((u32)wd1 & 3U) << 6);
}

#endif /* FABRIC_UP_H */
