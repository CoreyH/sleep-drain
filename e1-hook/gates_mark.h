/* SPDX-License-Identifier: GPL-2.0 */
/*
 * The six gate words read in the recipe's own state (PLAN.md, "The order after
 * the look"; RESEARCH.md, "The gate test's run"): which words, in which order,
 * the run's stages and the byte that records them. Arithmetic only, with no
 * register access, for e1_hook.c and tests/ alike, as wd1_mark.h is.
 *
 * B2, the second sequence of the engine's entry pair, switches things off and
 * then waits for bit 30 of six gate-status words (look.h has them), 0x28e5a0050
 * at its op 140, after SIO's subtree. Set is closed, what B2 waits for
 * (gate_test.h's GATE_CLOSED). The look read the six only in Linux's suspended
 * state, and the gate test found 0x28e5a0050 open there whatever it did, with
 * the fabric lane above SIO, AFNC2_LW1, already at macOS's value. Nothing has
 * read them in the state the recipe leaves: SIO's firmware and the PMP's
 * stopped, the cores parked, macOS's table and the power words out, the clocks
 * parked.
 *
 * So e1_hook gates_mark=1, on --m1-mark's recipe, has CPU0 read them in step
 * 8, in look.h's order, and then AFNC2_LW1, after the stage and the steps'
 * outcomes have gone out and right before the commit. It writes no register:
 * only the byte, to the PMU's scratch. The byte, at 0x6016:
 *   bits 0-5  bit 30 of 0x28e5a0050, 0x28e5a0064, 0x28e5a030c, 0x28e09c310,
 *             0x28e09c338 and 0x28e09c34c, in that order: set, closed
 *   bit 6     AFNC2_LW1 read exactly 0x1000030f, macOS's value at sleep entry
 *             (and ps_words=1's)
 *   bit 7     always set, so that the byte can't be taken for one never
 *             written: step 8's first command leaves 0x6016 at 0
 * What a bit says: that gate as CPU0 found it before the commit, before B2's
 * own writes. For 0x28e5a0050: read closed, B2's poll at op 140 is cleared, and
 * a2_pre=1 is next (PLAN.md). Read open, as it did in the one run (RESEARCH.md,
 * "The gates read's run"), it doesn't place the stall: the recipe leaves ps_sio,
 * UART_P, SPI_P and SIO_CPU on, as macOS has them, and B2 switches them off
 * itself before that poll.
 *
 * 0x6016 is the alarm's result in every run since run 4, and every byte is
 * spoken for by the run's end. The alarm's is the one the ending needs least:
 * the alarm is for an hour after the commit, WD1's marker restarts the Mac
 * long before that, and the alarm's own registers, read at the next boot, say
 * whether it was written as meant. So with gates_mark=1 the alarm's last
 * command, after the commit, writes this byte again in place of its result,
 * and what goes over SPMI after the commit is --m1-mark's, value aside. From
 * step 8's command on, then, the run has stages of its own, fabric_up=1's with
 * that one difference (gates_stage()): 0x4b for 0x52, 0x55 and 0x54 for 0x53
 * and 0x51.
 *
 * A read that faults or never returns. The reads sit right after stage 0x4b's
 * command and the steps' outcomes, SError still unmasked, with dsb and isb
 * before the byte goes out, so the record such a read leaves is stage 0x4b
 * with 0x6013's marker and 0x6016 still 0. A read that faults raises an SError
 * and the kernel panics (kernel.panic: a restart about 10 s later, if the
 * panic's restart works); one that never returns leaves WD1's marker to restart
 * the Mac, about 90 s after step 4. A byte whose own command failed leaves the
 * same record, and the run then went on with nothing more recorded: the
 * restart's time is the only way to tell them apart, and only in part.
 */
#ifndef GATES_MARK_H
#define GATES_MARK_H

#ifndef __KERNEL__
#include <stdint.h>
typedef uint8_t u8;
typedef uint32_t u32;
#endif

#include "look.h"
#include "gate_test.h"
#include "fabric_up.h"

/* The six words: look.h's two pages and offsets, in that order; then the lane, gate_test.h's, in SIO's page. */
#define GATES_N			6
#define GATES_AT		0x6016	/* the PMU scratch byte, in place of the alarm's result */

#define GATES_LANE_MACOS	(1U << 6)	/* AFNC2_LW1 read GATE_LANE_VAL */
#define GATES_MARK		(1U << 7)	/* always set */

/* gates_mark=1's stages, from step 8's command on, each for the one fabric_up=1 sends there (the comment above). */
#define M1_GATES_STAGE_PRE	0x4b	/* in place of 0x52 */
#define M1_GATES_STAGE		0x55	/* in place of 0x53 */
#define M1_GATES_STAGE_PEND	0x54	/* in place of 0x51 */

/* g: the six gate words, in the order above; lane: AFNC2_LW1. */
static inline u8 gates_byte(const u32 *g, u32 lane)
{
	u8 b = GATES_MARK;
	unsigned int i;

	for (i = 0; i < GATES_N; i++)
		if (g[i] & GATE_CLOSED)
			b |= 1U << i;
	if (lane == GATE_LANE_VAL)
		b |= GATES_LANE_MACOS;
	return b;
}

/* The stage gates_mark=1 sends where fabric_up=1 would send s; any other stage as it is. */
static inline u8 gates_stage(u8 s)
{
	return s == M1_FAB_STAGE_PRE ? M1_GATES_STAGE_PRE : s == M1_FAB_STAGE ? M1_GATES_STAGE :
	       s == M1_FAB_STAGE_PEND ? M1_GATES_STAGE_PEND : s;
}

#endif /* GATES_MARK_H */
