/* SPDX-License-Identifier: GPL-2.0 */
/*
 * The three CPU complexes' cluster words written last, right before the commit,
 * and the time from them to CPU0's last record kept (PLAN.md, "The order after
 * the look"; RESEARCH.md, "`acc_last=1` as built"): which entries, the time's
 * code, the byte that records it and the run's stages. Arithmetic only, with no
 * register access, for e1_hook.c and tests/ alike, as a2_pre.h is.
 *
 * macOS's table writes the soc-cluster words of EACC, PACC0 and PACC1
 * (0x28e098000 := 0, 0x28e098004 := 0x20, 0x28e098008 := 0x20: target 0, and
 * for the P complexes a 1 to the up-done flag) as its entries 52 to 54 of 74,
 * with every other core off long since. The replay has written them there too,
 * in step 5, and then taken the rest of the table, the 20 power words, the
 * other cores' stop requests and holds, a dozen PMU commands and step 8's reads
 * or moves (up to 9.5 s of them in --m1-sio-steps) before the commit, the
 * alarm, the release and CPU0's own WFI. Naeem Malik's second driver for T8103
 * (malik-na/linux-aurora, m1power/s2r-rs1, s2r_seq.rs; read with Corey's OK of
 * 2026-11-13) says of that chip: "the chip starts its own block at the first
 * cluster word and every core has to be in its wait soon after", with every
 * wait after them capped "far below the 52 ms the chip was shown to allow".
 * Nothing shows T6000 has such a limit, and nothing here had timed that span.
 *
 * So e1_hook acc_last=1, on --m1-gates' recipe:
 *   - step 5 leaves entries 52 to 54 out. The table's other entries, their
 *     waits, the power words, the stop requests and holds and steps 7 and 8
 *     run as they did, without those three written
 *   - step 8, in place of gates_mark=1's reads: the five ClkGen muxes the table
 *     parked read once each (the rider: --m1-a2-pre found that not all five
 *     read the table's value, and its one bit didn't say which), and the byte
 *     to 0x6016 with one bit a mux and the time's code at 7, "not yet"
 *   - step 9, with every exception masked, right before the commit's write:
 *     the clock read, then the three words written with the table's own values
 *     in the table's order. Nothing between them and the commit
 *   - after the commit: the alarm's commands, the release and wd2_bite=1's
 *     sample and stage as before. Then the clock again, and the byte once more
 *     with the time from before the first cluster word in its code: the run's
 *     last command, after the stage that used to be the last
 * No new address and no new value: the same three writes, later. What changes
 * is their place, after the table's entries 55 to 72 (the other clusters'
 * words, SIO_CPU, the muxes' park, PLL-off) where macOS has them before, which
 * is T8103's order as Naeem has it (the parks, then the cluster words, then the
 * timer).
 *
 * The time's code, bits 7:5 of the byte:
 *   0  never (a byte of 0: nothing was sent)
 *   1  under 5 ms    2  under 10 ms    3  under 20 ms    4  under 50 ms
 *   5  under 100 ms  6  100 ms or more
 *   7  not yet: the send before the cluster words, and the alarm's
 * What is timed: from CPU0's read of the clock just before the first cluster
 * word to its read after wd2_bite=1's stage went out. Inside it: the three
 * writes, the commit, the alarm's commands, the release of the other cores
 * (so it bounds their way to their WFI from above, less their own last few
 * instructions), up to 300 ms for the engine to arm WD2, the bite's write and
 * the stage's command. Not in it: this byte's own command, the acks and CPU0's
 * WFI after them.
 *
 * Bits 4:0: mux n, in the table's order (0x28e038164, 16c, 168, 15c, 170), read
 * 0x80100000 at step 8. A bit that is clear doesn't say what the mux read; the
 * load logs all five awake.
 *
 * What it can say. The restart at about 120 s, or none: B2 got past its op 224
 * with nothing else changed, and the place of those three writes is what the
 * runs lacked. The restart still at 90 s with a code of 4 or less: the three
 * were written under 50 ms before CPU0's last record and the pair still
 * stopped, so a limit of T8103's size isn't what ends the runs (a shorter one
 * on T6000 isn't excluded, nor the unrecorded tail). With a code of 5 or 6 the
 * run didn't get under T8103's figure and doesn't test it: what took the time
 * (WD2's arming is the long bound) is trimmed next.
 *
 * The stages are the run's own from step 8's command on, gates_mark=1's with
 * the byte's meaning the one difference (acl_stage()): 0x11 for 0x4b, 0x13 and
 * 0x12 for 0x55 and 0x54. A run that stops after its first send leaves stage
 * 0x11 with the code at 7; one whose last command failed, or that stopped
 * between the stage and it, leaves 0x13 or 0x12 with the code still 7.
 */
#ifndef ACC_LAST_H
#define ACC_LAST_H

#ifndef __KERNEL__
#include <stdbool.h>
#include <stdint.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
#endif

#include "gates_mark.h"

/* The three entries of the post-slpa table, their page and what the table writes to each: EACC, PACC0, PACC1. */
#define ACL_FIRST		52
#define ACL_N			3
#define ACL_PAGE		0x28e098000ULL
#define ACL_OFFS		{ 0x0, 0x4, 0x8 }
#define ACL_VALS		{ 0x0, 0x20, 0x20 }

/* The rider's muxes: as many as a2_pre.h has, one bit each, in the table's order. */
#define ACL_MUX_N		5
#define ACL_MUX_MASK		0x1fU

/* The time's code, in the byte's bits 7:5. */
#define ACL_T_SHIFT		5
#define ACL_T_NEVER		0
#define ACL_T_LATE		6	/* 100 ms or more */
#define ACL_T_PRE		7	/* not yet: sent before the cluster words */
#define ACL_T_MS		{ 5, 10, 20, 50, 100 }	/* codes 1 to 5: under each */

/* acc_last=1's stages, from step 8's command on, each for the one gates_mark=1 sends there. */
#define M1_ACL_STAGE_PRE	0x11	/* in place of 0x4b */
#define M1_ACL_STAGE		0x13	/* in place of 0x55 */
#define M1_ACL_STAGE_PEND	0x12	/* in place of 0x54 */

/* Entry i of the post-slpa table is one of the three step 5 leaves for step 9. */
static inline bool acl_entry(unsigned int i)
{
	return i >= ACL_FIRST && i < ACL_FIRST + ACL_N;
}

/* The code for a span of dt ticks of a clock with tps ticks a second: 1 to 6, never 0 or 7. */
static inline u8 acl_code(u64 dt, u64 tps)
{
	static const u16 ms[] = ACL_T_MS;
	u8 i;

	for (i = 0; i < sizeof(ms) / sizeof(ms[0]); i++) {
		if (dt < tps * ms[i] / 1000)
			return (u8)(i + 1);
	}
	return ACL_T_LATE;
}

/* The byte. mux: bit n set if mux n read the table's value. code: ACL_T_PRE before the cluster words, acl_code() after. */
static inline u8 acl_byte(u8 mux, u8 code)
{
	return (u8)((mux & ACL_MUX_MASK) | (code & 7) << ACL_T_SHIFT);
}

/* The stage acc_last=1 sends where gates_mark=1 would send s; any other stage as it is. */
static inline u8 acl_stage(u8 s)
{
	return s == M1_GATES_STAGE_PRE ? M1_ACL_STAGE_PRE : s == M1_GATES_STAGE ? M1_ACL_STAGE :
	       s == M1_GATES_STAGE_PEND ? M1_ACL_STAGE_PEND : s;
}

#endif /* ACC_LAST_H */
