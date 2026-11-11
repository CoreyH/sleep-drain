/* SPDX-License-Identifier: GPL-2.0 */
/*
 * B2's own switch-off of SIO's subtree, made by the AP before the commit, with
 * the gate polled as B2 polls it (PLAN.md, "The order after the look";
 * RESEARCH.md, "sio_down=1 as built"): which words, in which order, the byte
 * that records it and the run's stages. Arithmetic only, with no register
 * access, for e1_hook.c and tests/ alike, as gates_mark.h is.
 *
 * B2, the second sequence of the engine's entry pair, writes 0 to the 43 power
 * words of SIO's subtree (0x28e580180 to 0x28e5802d0), waits for bit 11 of
 * each to clear, and then waits for bit 30 of two gate words, 0x28e5a0064 and,
 * at its op 140, 0x28e5a0050. gates_mark=1 read that gate open right before
 * the commit, which says nothing: the recipe leaves ps_sio, UART_P, SPI_P and
 * SIO_CPU on, as macOS has them, and B2 switches them off itself. The one
 * reading with the subtree off is the gate test's, in Linux's suspended state
 * and with Linux's words (0x200 and 0x0f000200, not 0): open.
 *
 * So e1_hook sio_down=1, on --m1-gates' recipe, has CPU0 do in step 8, after
 * the stage and the steps' outcomes and before the commit, in place of
 * gates_mark=1's reads:
 *   0. the 43 words read as found, and 0x28e5a0050 once
 *   1. B2's writes, in B2's order and groups, every one a literal 0: 38 words
 *      (SIO_CPU first), then a poll of each for bit 11 clear, but UART_N,
 *      which B2 doesn't poll; SIO_ADMA, SPI_P and UART_P, and their polls;
 *      AUDIO_P and its poll; SIO and its poll. Each poll up to 100 ms (B2's
 *      255 tries); one that isn't met is recorded and the pass goes on
 *   2. 0x28e5a0064, then 0x28e5a0050, each polled for bit 30 for up to 100 ms
 *   3. the 43 words read again (target 0 and actual 0?), and the six gate words
 *   4. the byte to 0x6016, without SIOD_BACK
 *   5. all 43 written back, in the reverse of the order they were written
 *      (SIO first, SIO_CPU last), each as found less the bits the kernel never
 *      writes back (asc_probe.h's ASC_PS_NOT_WRITTEN), up to 10 ms for each to
 *      read as found (gate_test.h's gate_back())
 *   6. the byte again, with SIOD_BACK if all 43 read as found; the alarm's
 *      last command, after the commit, sends it a third time, as gates_mark=1
 *      has it send its byte
 * The commit then finds the subtree as the recipe leaves it, so the restart's
 * time reads as --m1-gates' does. Left off, B2's own raise of SIO_CPU would
 * wait under an off parent, as in run 3.
 *
 * Not made, on purpose: B2's cycle of GPIO's power word around this (its ops
 * 47 and 90; another page, whose gates are in 0x28e09c000: a later run can add
 * it), and B2's raise of SIO_CPU with its poll of SIO's ASC (ops 49-51: the
 * table's entry 61 has SIO_CPU on already, and the probe read that word).
 *
 * The byte, at 0x6016 (gates_mark.h's GATES_AT), under the run's own stages:
 *   bit 0  0x28e5a0050 read closed (bit 30) after its poll: the answer
 *   bit 1  0x28e5a0064 read closed after its poll
 *   bit 2  every one of the 42 bit-11 polls was met
 *   bit 3  all 43 words read target 0 and actual 0 after the polls
 *   bit 4  the other four gates read as --m1-gates' run found them:
 *          0x28e5a030c closed, 0x28e09c310 open, 0x28e09c338 closed,
 *          0x28e09c34c open
 *   bit 5  all 43 read back as found (never set in the first send)
 *   bit 6  0x28e5a0050 read closed before the first write
 *   bit 7  always set
 * What bit 0 says: with B2's switch-off made by the AP, in the recipe's state,
 * the gate closed, or didn't. Either is an observation in the AP's state
 * before the commit, not B2's at op 140: the cores are up, A2's DCS and fabric
 * moves haven't been made, nor B2's own raises (Pullfrog on #83).
 *
 * The stages are the run's own from step 8's command on, gates_mark=1's with
 * the byte's meaning the one difference (siod_stage()): 0x3a for 0x4b, 0x3c
 * and 0x3b for 0x55 and 0x54.
 *
 * What it adds to the time between step 4, where WD1's marker is set, and the
 * commit: a few ms if every poll is met at once, 100 ms more for a gate that
 * stays open, and at worst about 5 s (42 polls and two of 100 ms, 43 waits of
 * 10 ms). The RTC's second is read before it, so a restart is timed from
 * before it too.
 *
 * A run that stops inside it leaves stage 0x3a with 0x6016 at 0 (before the
 * first send) or without SIOD_BACK (in the write-back), and restarts by the
 * panic (about 10 s) or by WD1's marker (about 90 s after step 4).
 */
#ifndef SIO_DOWN_H
#define SIO_DOWN_H

#ifndef __KERNEL__
#include <stdbool.h>
#include <stdint.h>
typedef uint8_t u8;
typedef uint32_t u32;
#endif

#include "gates_mark.h"

/*
 * The 43 words, offsets in SIO's page 0x28e580000, in the order B2 writes them: SIO_CPU, I2C0-7, FPWM0-2, SIO_SPMI0-2,
 * AES, SPI0-4, UART_N, UART0-4, UART6, UART7, MCA0-3, DPA0-4; SIO_ADMA, SPI_P, UART_P; AUDIO_P; SIO.
 */
#define SIOD_N			43
#define SIOD_OFFS		{ 0x188, 0x1a8, 0x1b0, 0x1b8, 0x1c0, 0x1c8, 0x1d0, 0x1d8, 0x1e0, 0x190, 0x198, 0x1a0, 0x218, 0x220, 0x228, \
				  0x2d0, 0x1f0, 0x1f8, 0x200, 0x208, 0x210, 0x238, 0x240, 0x248, 0x250, 0x258, 0x260, 0x268, 0x270, 0x288, \
				  0x290, 0x298, 0x2a0, 0x2a8, 0x2b0, 0x2b8, 0x2c0, 0x2c8, \
				  0x280, 0x1e8, 0x230, \
				  0x278, \
				  0x180 }
/* B2's groups: each one's words written, then each one's polled. */
#define SIOD_GROUPS		{ 38, 3, 1, 1 }
#define SIOD_NOPOLL		0x238		/* UART_N: written, never polled */
#define SIOD_FIRST		0x180		/* the 43 are every word from here ... */
#define SIOD_LAST		0x2d0		/* ... to here, 8 apart */
#define SIOD_BUSY		(1U << 11)	/* what B2 polls each word for: clear */
#define SIOD_GATE_64		0x064		/* in the first gate page: 0x28e5a0064, polled before gate_test.h's GATE_SIO */

#define SIOD_CLOSED		(1U << 0)
#define SIOD_CLOSED_64		(1U << 1)
#define SIOD_POLLS		(1U << 2)
#define SIOD_OFF		(1U << 3)
#define SIOD_OTHERS		(1U << 4)
#define SIOD_BACK		(1U << 5)
#define SIOD_WAS_CLOSED		(1U << 6)
#define SIOD_MARK		(1U << 7)

/* sio_down=1's stages, from step 8's command on, each for the one gates_mark=1 sends there. */
#define M1_SIOD_STAGE_PRE	0x3a	/* in place of 0x4b */
#define M1_SIOD_STAGE		0x3c	/* in place of 0x55 */
#define M1_SIOD_STAGE_PEND	0x3b	/* in place of 0x54 */

/* A power word is off: its target and its actual state both read 0. */
static inline bool siod_off(u32 v)
{
	return !(v & (0xfU | GATE_PS_ACTUAL));
}

/*
 * g: the six gate words in gates_mark.h's order, read after both polls. polls: every bit-11 poll was met. off: all 43
 * words read off then. back: all 43 read back as found. was_closed: 0x28e5a0050 read closed before the first write.
 */
static inline u8 siod_byte(const u32 *g, bool polls, bool off, bool back, bool was_closed)
{
	u8 b = SIOD_MARK;

	if (g[0] & GATE_CLOSED)
		b |= SIOD_CLOSED;
	if (g[1] & GATE_CLOSED)
		b |= SIOD_CLOSED_64;
	if (polls)
		b |= SIOD_POLLS;
	if (off)
		b |= SIOD_OFF;
	if ((g[2] & GATE_CLOSED) && !(g[3] & GATE_CLOSED) && (g[4] & GATE_CLOSED) && !(g[5] & GATE_CLOSED))
		b |= SIOD_OTHERS;
	if (back)
		b |= SIOD_BACK;
	if (was_closed)
		b |= SIOD_WAS_CLOSED;
	return b;
}

/* The stage sio_down=1 sends where gates_mark=1 would send s; any other stage as it is. */
static inline u8 siod_stage(u8 s)
{
	return s == M1_GATES_STAGE_PRE ? M1_SIOD_STAGE_PRE : s == M1_GATES_STAGE ? M1_SIOD_STAGE :
	       s == M1_GATES_STAGE_PEND ? M1_SIOD_STAGE_PEND : s;
}

#endif /* SIO_DOWN_H */
