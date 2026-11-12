/* SPDX-License-Identifier: GPL-2.0 */
/*
 * What lets the gate 0x28e5a0050 close: B2's switch-off of SIO's subtree with
 * its cycle of GPIO's power word in B2's place, and then the 22 words B2 writes
 * first, the gate polled after each (PLAN.md, "The order after the look";
 * RESEARCH.md, "sio_steps=1 as built"): the words, the order, the byte and the
 * run's stages. Arithmetic only, with no register access, for e1_hook.c and
 * tests/ alike, as sio_down.h is, whose 43 words and groups this uses.
 *
 * B2 waits at its op 140 for bit 30 of 0x28e5a0050, after switching SIO's
 * subtree off. Made by the AP (sio_down=1, twice), that switch-off left the
 * gate open. The PCIe run then showed a gate does close for the AP once what's
 * behind it is off, so the open one is a lead, and two things B2 does before
 * that poll haven't been made by the AP:
 *   - its cycle of GPIO's power word, 0x28e080120: 0x8000000f (on, with bit
 *     31, the reset) at its op 47, before it raises SIO_CPU, and 0 at its op
 *     90, right after the 0 to the first 38 of the 43 and before their polls;
 *     then bit 11 polled clear (15 tries, where the others have 255). GPIO's
 *     parents are PMS and SIO, and Linux's suspend leaves the word at 0x200,
 *     off: it's the pass through on-with-reset that's new;
 *   - its ops 8 to 29, before any power word: 0x28e03801c := 0x80101100, 1 to
 *     the seven words 0x28e0a0004 to 0x28e0a001c, 1 to the fourteen words
 *     0x28e5b0004 to 0x28e5b0038 (which read 0x80100100, 2 2 2 2 3 2 2 and
 *     2 2 2 2 2 2 2 2 0 0 2 2 1 1 awake and after noirq: the second look).
 *
 * So e1_hook sio_steps=1, on --m1-gates' recipe, has CPU0 do in step 8, after
 * the stage and the steps' outcomes and before the commit, in place of
 * gates_mark=1's reads:
 *   0. the 43 words read as found, and 0x28e5a0050 once. Closed already, which
 *      no run has had: nothing is written, the byte says so, on to the commit
 *   1. GPIO := 0x8000000f, with SIO still on as in B2, and up to 100 ms for
 *      its actual state to read f (B2 doesn't wait; it has a delay and its
 *      raise of SIO_CPU there, which isn't made: SIO_CPU is on already)
 *   2. sio_down=1's switch-off, B2's writes in B2's order and groups, with
 *      GPIO := 0 after the first group's writes and before its polls, and
 *      GPIO's bit 11 polled after them, each poll up to 100 ms
 *   3. 0x28e5a0064, then 0x28e5a0050, each polled for bit 30 for up to 100 ms;
 *      the 43 and GPIO read again; the byte to 0x6016, in its first layout
 *   4. only if all 43 went off, every poll met, and the gate stayed open:
 *      0x28e03801c := 0x80101100 and the byte; 1 to the seven and the byte;
 *      1 to the fourteen, in B2's order, and the byte; each word read back;
 *      0x28e5a0050 polled once more, up to 100 ms
 *   5. the byte in its second layout, without its "back" bit
 *   6. all 43 written back as sio_down=1 writes them back, in reverse, with up
 *      to 100 ms each to read as found (10 ms wasn't enough for one word in
 *      the second sio_down=1 run); 0x28e5a0050 polled to read open again
 *   7. the byte again, with what came back; the alarm's last command, after
 *      the commit, sends it once more
 * GPIO stays at B2's 0 and the 22 as B2 writes them: B2 writes the same values
 * to the same words itself, first thing, so the commit finds nothing there
 * that B2 wouldn't have put there. The 43 go back because B2's own raise of
 * SIO_CPU would otherwise wait under an off parent (run 3).
 *
 * The byte, at 0x6016 (gates_mark.h's GATES_AT), under the run's own stages.
 * Bit 7 is always set. Bit 6 tells its two layouts apart.
 *
 * Bit 6 clear: the pass isn't through yet.
 *   bit 0  0x28e5a0050 read closed after the switch-off with GPIO's cycle
 *   bit 2  all 43 read target 0 and actual state 0, and every bit-11 poll of
 *          theirs was met
 *   bit 3  GPIO's cycle was one: its actual state read f within 100 ms of the
 *          raise, its bit-11 poll was met, and it read target 0, actual state
 *          0 and bit 11 clear after the gates' polls
 *   bits 4 and 1, as a number from 0 to 3: how many of the three groups of
 *          the 22 are written and read back (0x28e03801c; the seven; the
 *          fourteen). A run that stops in one leaves the count before it
 *   bit 5  alone with bit 7 (0xa0): the gate read closed before the first
 *          write, and nothing was written
 * Bit 6 set: it is, and the byte is the result.
 *   bit 0  as above: closed after the switch-off with GPIO's cycle
 *   bit 1  0x28e5a0050 read closed after the 22. Never set with bit 0: the 22
 *          are only written if the gate was still open
 *   bit 2  as above
 *   bit 3  as above
 *   bit 4  the 22 were written and every one read back as written
 *          (0x28e03801c but for its bit 30, within 100 ms). Clear with bit 0
 *          set or bit 2 clear: they weren't written
 *   bit 5  all 43 read as found in target, actual state and auto-enable, each
 *          within 100 ms of its write-back, and 0x28e5a0050 read open again
 *          within 100 ms after the last. Not set in the first send of this
 *          layout
 *
 * What the gate's bits say: with B2's switch-off and its GPIO cycle made by
 * the AP, in the recipe's state, the gate closed, or with the 22 moved as
 * well, or with neither. Each is the AP's state before the commit, not B2's
 * at its op 140: the cores are up and A2's moves haven't been made (Pullfrog
 * on #83). The 22 are written after the switch-off here and before it in B2;
 * if the gate only follows them when they're in place first, this run reads
 * open and can't tell.
 *
 * The stages are the run's own from step 8's command on, gates_mark=1's with
 * the byte's meaning the one difference (sst_stage()): 0x15 for 0x4b, 0x17 and
 * 0x16 for 0x55 and 0x54.
 *
 * What it adds to the time between step 4, where WD1's marker is set, and the
 * commit: a few ms if every poll is met at once, 200 ms for a gate that stays
 * open through both polls, and at worst about 9.5 s (45 polls of 100 ms, three
 * gate polls, 43 waits of 100 ms). The RTC's second is read before it, so a
 * restart is timed from before it too.
 *
 * A run that stops inside it leaves stage 0x15 with 0x6016 at 0 (in the
 * switch-off or GPIO's cycle), in its first layout (in the 22: the count says
 * which group), or in its second without bit 5 (in the write-back), and
 * restarts by the panic (about 10 s) or by WD1's marker (about 90 s after
 * step 4).
 */
#ifndef SIO_STEPS_H
#define SIO_STEPS_H

#include "sio_down.h"

/* GPIO's power word, in the first power page 0x28e080000, and what B2's op 47 writes there: on, with the reset. */
#define SST_GPIO		0x120
#define SST_GPIO_UP		0x8000000fU

/* B2's op 8, in look.h's LOOK_PRE_CLK page: the value, and the one bit of the word that isn't compared on the read back. */
#define SST_CLK_VAL		0x80101100U
#define SST_CLK_PENDING		(1U << 30)
/* B2's ops 9 to 15 and 16 to 29: offsets in look.h's LOOK_PRE_A and LOOK_PRE_B pages, in B2's order; each is written 1. */
#define SST_A_N			7
#define SST_A_OFFS		{ 0x04, 0x08, 0x0c, 0x10, 0x14, 0x18, 0x1c }
#define SST_B_N			14
#define SST_B_OFFS		{ 0x04, 0x0c, 0x10, 0x14, 0x08, 0x18, 0x1c, 0x20, 0x24, 0x28, 0x2c, 0x30, 0x34, 0x38 }
#define SST_PRE_VAL		1U

/* What the pass found, as m1_sio_steps() keeps it, and the byte's second layout, bit for bit. */
#define SST_G1			(1U << 0)	/* the gate closed after the switch-off with GPIO's cycle */
#define SST_G2			(1U << 1)	/* the gate closed after the 22 */
#define SST_SUB			(1U << 2)	/* all 43 off, every poll of theirs met */
#define SST_GPIO_OK		(1U << 3)	/* GPIO came up, and read off and idle afterwards */
#define SST_PRE			(1U << 4)	/* the 22 written and read back as written */
#define SST_BACK		(1U << 5)	/* the 43 back as found, and the gate open again */
#define SST_RES			0x3fU
#define SST_FINAL		(1U << 6)
#define SST_MARK		(1U << 7)
/* The first layout: bits 0, 2 and 3 as above, the count of the 22's groups in bits 4 and 1, and bit 5 by itself. */
#define SST_GO_KEEP		(SST_G1 | SST_SUB | SST_GPIO_OK)
#define SST_GO_AT_LO		(1U << 1)
#define SST_GO_AT_HI		(1U << 4)
#define SST_GO_CLOSED		(1U << 5)
#define SST_GROUPS		3		/* 0x28e03801c; the seven; the fourteen */

/* sio_steps=1's stages, from step 8's command on, each for the one gates_mark=1 sends there. */
#define M1_SST_STAGE_PRE	0x15	/* in place of 0x4b */
#define M1_SST_STAGE		0x17	/* in place of 0x55 */
#define M1_SST_STAGE_PEND	0x16	/* in place of 0x54 */

/* A power word is off and idle: its target and its actual state read 0, and bit 11 is clear. */
static inline bool sst_idle(u32 v)
{
	return !(v & (SIOD_PS_STATE | SIOD_BUSY));
}

/* The 22 are written only after a switch-off that took, with the gate still open. */
static inline bool sst_second(u8 res)
{
	return (res & (SST_SUB | SST_G1)) == SST_SUB;
}

/* 0x28e03801c reads as B2's op 8 wrote it, its bit 30 aside. */
static inline bool sst_clk_ok(u32 v)
{
	return (v & ~SST_CLK_PENDING) == (SST_CLK_VAL & ~SST_CLK_PENDING);
}

/* The byte's first layout. res: SST_G1, SST_SUB and SST_GPIO_OK as they turned out. groups: how many of the 22's three are written, 0 to 3. */
static inline u8 sst_going(u8 res, unsigned int groups)
{
	return (u8)(SST_MARK | (res & SST_GO_KEEP) | (groups & 1 ? SST_GO_AT_LO : 0) | (groups & 2 ? SST_GO_AT_HI : 0));
}

/* The byte for a gate that read closed before the first write: nothing was written. */
static inline u8 sst_closed_before(void)
{
	return (u8)(SST_MARK | SST_GO_CLOSED);
}

/* The byte's second layout. res: SST_RES' bits as they turned out; SST_BACK clear in the send before the write-back. */
static inline u8 sst_final(u8 res)
{
	return (u8)(SST_MARK | SST_FINAL | (res & SST_RES));
}

/* The stage sio_steps=1 sends where gates_mark=1 would send s; any other stage as it is. */
static inline u8 sst_stage(u8 s)
{
	return s == M1_GATES_STAGE_PRE ? M1_SST_STAGE_PRE : s == M1_GATES_STAGE ? M1_SST_STAGE :
	       s == M1_GATES_STAGE_PEND ? M1_SST_STAGE_PEND : s;
}

#endif /* SIO_STEPS_H */
