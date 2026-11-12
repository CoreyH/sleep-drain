/* SPDX-License-Identifier: GPL-2.0 */
/*
 * B2's own switch-off of the PCIe power words, made by the AP before the commit
 * and left (PLAN.md, "The order after the look"; RESEARCH.md, "B2 read against
 * the M4 classes"): which words, in which order, the byte that records it and
 * the run's stages. Arithmetic only, with no register access, for e1_hook.c and
 * tests/ alike, as sio_down.h is.
 *
 * B2, the second sequence of the engine's entry pair, writes 0 to APCIE_ST_SYS,
 * APCIE_ST1_SYS and APCIE_GP_SYS (its ops 141 to 143), waits for bit 11 of each
 * to clear (144 to 146), writes 0 to APCIE_ST and APCIE_GP (147, 148) and,
 * after ANS2's cycle, waits for bit 30 of the gate word 0x28e09c34c (156).
 * Linux's suspend leaves the internal PCIe host alone (aurora's
 * apple_pcie_suspend_noirq() returns at once for a host that isn't tunneled),
 * so APCIE_GP_SYS reads on at the commit, in auto mode with its actual state f,
 * where macOS has it idle and gated (0x1000030f). On the M4 Pro and on T8140
 * the kernel's driver stops those ports before the commit, and a power word
 * lowered with something live behind it kept bit 11 there, which the engine
 * then waited on for good. Nothing here had made that 0, or read the word after
 * ps_words=1 writes it.
 *
 * So e1_hook pcie_down=1, on --m1-gates' recipe, has CPU0 do in step 8, after
 * the stage and the steps' outcomes and before the commit, in place of
 * gates_mark=1's reads:
 *   0. the five words read as found
 *   1. B2's ops 141 to 143: a literal 0 to the three _SYS words, in B2's
 *      order; then B2's polls, each word for bit 11 clear, up to 100 ms (B2's
 *      255 tries). One that isn't met is recorded and the pass goes on
 *   2. the three read again, and the byte to 0x6016 with what they say and
 *      neither of its last two bits
 *   3. B2's ops 147 and 148: a literal 0 to APCIE_ST and to APCIE_GP. B2
 *      doesn't wait for them; here each gets up to 100 ms to read target 0 and
 *      actual state 0, for the record
 *   4. 0x28e09c34c polled for bit 30 for up to 100 ms, APCIE_GP_SYS read once
 *      more, and the byte again, whole; the alarm's last command, after the
 *      commit, sends it a third time, as gates_mark=1 has it send its byte
 * Nothing is written back: the commit finds the five as B2's own writes would
 * leave them, so B2's 0 meets words that are already off, and a restart at
 * 120 s in place of 90 s would say B2 got past its op 224 (wd1_mark.h). No
 * PCIe register is touched, before or after: only the five power words and one
 * gate word, all in PMGR pages the run already has mapped. (The M4 Pro's
 * driver notes a bus hang from touching a port's windows with its domain off.)
 *
 * B2 writes a plain 0, which clears the word's auto-enable in the same write;
 * the M4 Pro's driver clears auto-enable and the bridge's bits by hand first.
 * This run makes B2's write, since B2's is what the engine will make.
 *
 * Not made: the ports' own stop (the link into L2, PERST#, the PHY's and the
 * port's clocks), which is the build after this one if a word latches here;
 * and B2's cycle of ANS2 between its ops 148 and 156 (the probe made it).
 *
 * The byte, at 0x6016 (gates_mark.h's GATES_AT), under the run's own stages:
 *   bit 0  APCIE_GP_SYS read with an actual state other than 0 as found: up,
 *          as both looks had it, and not gated as macOS has it
 *   bit 1  APCIE_GP_SYS read with bit 11 set as found, before any 0
 *   bit 2  APCIE_GP_SYS's poll was met: bit 11 read clear within 100 ms of
 *          the 0
 *   bit 3  APCIE_GP_SYS read target 0 and actual state 0 at the send's last
 *          read of it (after the polls in the first send; after the gate's
 *          poll in the second)
 *   bit 4  APCIE_ST_SYS and APCIE_ST1_SYS: both polls met, and both read
 *          target 0 and actual state 0 after them (Linux's suspend leaves
 *          them off already)
 *   bit 5  APCIE_ST and APCIE_GP both read target 0 and actual state 0 within
 *          100 ms of their 0. Never set in the first send
 *   bit 6  0x28e09c34c read closed (bit 30) within 100 ms after that. Never
 *          set in the first send. Open says little: the 21 words B2 sets to 1
 *          first may be what lets a gate close (RESEARCH.md)
 *   bit 7  always set
 * As the desk reading expects it if the 0 takes: 0xbd, or 0xfd with the gate
 * closed. Bit 2 clear, or bit 3 clear in the second send, is the latch.
 *
 * The stages are the run's own from step 8's command on, gates_mark=1's with
 * the byte's meaning the one difference (pcd_stage()): 0x1d for 0x4b, 0x1f and
 * 0x1e for 0x55 and 0x54.
 *
 * What it adds to the time between step 4, where WD1's marker is set, and the
 * commit: a few ms if every poll is met at once, and at worst about 0.6 s. The
 * RTC's second is read before it, so a restart is timed from before it too.
 *
 * A run that stops inside it leaves stage 0x1d with 0x6016 at 0 (before the
 * first send) or without bits 5 and 6 (before the second), and restarts by the
 * panic (about 10 s) or by WD1's marker (about 90 s after step 4).
 */
#ifndef PCIE_DOWN_H
#define PCIE_DOWN_H

#ifndef __KERNEL__
#include <stdbool.h>
#include <stdint.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
#endif

#include "gates_mark.h"

/*
 * The five words, offsets in the first power page 0x28e080000, in the order B2 writes them: APCIE_ST_SYS, APCIE_ST1_SYS,
 * APCIE_GP_SYS (polled); APCIE_ST, APCIE_GP (not polled by B2).
 */
#define PCD_N			5
#define PCD_ST_SYS		0x2e8
#define PCD_ST1_SYS		0x2f0
#define PCD_GP_SYS_OFF		0x320
#define PCD_ST			0x148
#define PCD_GP			0x150
#define PCD_OFFS		{ PCD_ST_SYS, PCD_ST1_SYS, PCD_GP_SYS_OFF, PCD_ST, PCD_GP }
#define PCD_SYS_N		3		/* the first three: written together, then each polled for bit 11 */
#define PCD_GP_SYS		2		/* APCIE_GP_SYS's place in PCD_OFFS */
#define PCD_BUSY		(1U << 11)	/* what B2 polls each _SYS word for: clear */
#define PCD_GATE		0x34c		/* in the second gate page: 0x28e09c34c, B2's op 156 */

/* A power word's target and actual state: off when both read 0. */
#define PCD_PS_STATE		(0xfU | GATE_PS_ACTUAL)

#define PCD_FOUND_UP		(1U << 0)
#define PCD_FOUND_BUSY		(1U << 1)
#define PCD_POLL		(1U << 2)
#define PCD_DOWN		(1U << 3)
#define PCD_ST_OK		(1U << 4)
#define PCD_PARENTS		(1U << 5)
#define PCD_CLOSED		(1U << 6)
#define PCD_MARK		(1U << 7)
#define PCD_SECOND		(PCD_PARENTS | PCD_CLOSED)	/* the second send's own bits */

/* pcie_down=1's stages, from step 8's command on, each for the one gates_mark=1 sends there. */
#define M1_PCD_STAGE_PRE	0x1d	/* in place of 0x4b */
#define M1_PCD_STAGE		0x1f	/* in place of 0x55 */
#define M1_PCD_STAGE_PEND	0x1e	/* in place of 0x54 */

/* A power word is off in its power state: its target and its actual state read 0. */
static inline bool pcd_down(u32 v)
{
	return !(v & PCD_PS_STATE);
}

/*
 * The byte. found: APCIE_GP_SYS before any write. now: the three _SYS words in PCD_OFFS' order, as last read. polls: bit n
 * set if word n's bit-11 poll was met. second: PCD_PARENTS and PCD_CLOSED as they turned out, or 0 in the first send.
 */
static inline u8 pcd_byte(u32 found, const u32 *now, u8 polls, u8 second)
{
	u8 b = PCD_MARK;

	if (found & GATE_PS_ACTUAL)
		b |= PCD_FOUND_UP;
	if (found & PCD_BUSY)
		b |= PCD_FOUND_BUSY;
	if (polls & (1U << PCD_GP_SYS))
		b |= PCD_POLL;
	if (pcd_down(now[PCD_GP_SYS]))
		b |= PCD_DOWN;
	if ((polls & 3) == 3 && pcd_down(now[0]) && pcd_down(now[1]))
		b |= PCD_ST_OK;
	return (u8)(b | (second & PCD_SECOND));
}

/* The stage pcie_down=1 sends where gates_mark=1 would send s; any other stage as it is. */
static inline u8 pcd_stage(u8 s)
{
	return s == M1_GATES_STAGE_PRE ? M1_PCD_STAGE_PRE : s == M1_GATES_STAGE ? M1_PCD_STAGE :
	       s == M1_GATES_STAGE_PEND ? M1_PCD_STAGE_PEND : s;
}

#endif /* PCIE_DOWN_H */
