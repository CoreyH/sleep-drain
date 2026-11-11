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
 *   3. the 43 words read again, and the six gate words (as the first run read
 *      them; only 0x28e5a0050's goes into the byte now)
 *   4. the byte to 0x6016, with none of its three "back" bits
 *   5. all 43 written back, in the reverse of the order they were written
 *      (SIO first, SIO_CPU last), each as found less the bits the kernel never
 *      writes back (asc_probe.h's ASC_PS_NOT_WRITTEN), up to 10 ms for each to
 *      read as found (gate_test.h's gate_back()); then 0x28e5a0050 polled
 *      once more, up to 100 ms, to read open again
 *   6. the byte again, with what came back; the alarm's last command, after
 *      the commit, sends it a third time, as gates_mark=1 has it send its byte
 * If 0x28e5a0050 reads closed in step 0, which it hasn't in any run, nothing
 * is written: the byte says so, once, and the run goes on to the commit.
 * "As found", for the write-back's wait, is a word's target and actual state,
 * its auto-enable and bits 27:24 (called the auto floor before this layout).
 * The byte then says whether the first two came back, and whether the
 * auto-enable did.
 * The two was-gated flags, bits 8 and 9, are neither written back nor
 * compared: a word that gated in between keeps them set where the recipe had
 * them clear (SIO_CPU's bit 9, which the table's entry 61 clears as macOS
 * does; B2 writes it again itself at its op 49). The adversarial review of
 * #84 found that, the gate's last poll, and that nothing here checks
 * sio_stop=1's outcome: m1-crumbs gives a verdict only if 0x6012 says SIO's
 * firmware was stopped.
 * The commit then finds the subtree as the recipe leaves it, so the restart's
 * time reads as --m1-gates' does. Left off, B2's own raise of SIO_CPU would
 * wait under an off parent, as in run 3.
 *
 * Not made, on purpose: B2's cycle of GPIO's power word around this (its ops
 * 47 and 90; another page, whose gates are in 0x28e09c000: a later run can add
 * it), and B2's raise of SIO_CPU with its poll of SIO's ASC (ops 49-51: the
 * table's entry 61 has SIO_CPU on already, and the probe read that word).
 *
 * The byte, at 0x6016 (gates_mark.h's GATES_AT), under the run's own stages.
 * It has two layouts, told apart by bit 6, and this is its second form, under
 * stages of its own (below), so that the two forms' records can't be taken for
 * each other (Pullfrog on #86): the
 * first run's (b453922, 0x96, stages 0x3a, 0x3c, 0x3b) had one bit for "all 43 read 0 in target, actual
 * state and auto fields" and one for "all back, and the gate", and couldn't
 * say which half of either failed (RESEARCH.md, "The sio_down=1 run").
 *
 * Bit 6 clear: the subtree went off. Every one of the 43 read target 0 and
 * actual state 0 after the polls, every poll was met, and the gate was open
 * before the first write:
 *   bit 0     0x28e5a0050 read closed (bit 30) after its poll: the answer
 *   bits 2:1  what the 43 words' auto fields read after the 0 (enum siod_auto):
 *             3  all 0: the 0 reads back whole
 *             2  auto-enable (bit 28) 0 in all 43, and bits 27:24 either 0 or,
 *                in SIO, SIO_CPU or SIO_SPMI0-2 only, f: what those five read
 *                after any switch-off on file (below)
 *             1  auto-enable 0 in all 43, bits 27:24 something else somewhere
 *             0  an auto-enable still read set
 *   bit 3     all 43 read as found in target and actual state, each within
 *             10 ms of its write-back
 *   bit 4     all 43 read as found in auto-enable (bit 28) then. Bits 27:24
 *             aren't compared: they aren't a setting (below), and under
 *             auto-enable they follow the actual state, which bit 3 has
 *   bit 5     0x28e5a0050 read open again within 100 ms of the last write-back
 *   bit 7     always set
 * Bits 3 to 5 are never set in the first send.
 *
 * Bit 6 set: it didn't, and bits 5:0 say where:
 *   0-42  the first word, by its place in B2's order (SIOD_OFFS), whose target
 *         or actual state didn't read 0 after the polls. The gate's reading
 *         means nothing then, so it isn't kept
 *   62    all 43 read off, but a bit-11 poll wasn't met within its 100 ms
 *   63    0x28e5a0050 read closed before the first write: nothing was written
 * Nothing in this layout says what came back; the two sends are the same.
 *
 * Why 2 is its own code (RESEARCH.md, "The record laid out again"). Bits 27:24
 * aren't a setting that holds what was written. Of the 43, only those five have
 * ever read anything but 0 there. With auto-enable set the field has followed
 * the actual state in every read on file (SIO f/f, SIO_CPU 4/4, the lane above
 * SIO f/f awake and 0/0 once Linux's suspend had its children off); with
 * auto-enable clear it has read f (SIO and SIO_CPU after Linux's switch-off,
 * whose driver writes back what it read there; SIO_SPMI0-2 always). So a
 * literal 0 is expected to read back 0x0f000000 in those five, and B2 writes
 * the same 0 to the same words: code 2 is taken as "as B2's 0 leaves them",
 * and said as that.
 *
 * What bit 0 says: with B2's switch-off made by the AP, in the recipe's state,
 * the gate closed, or didn't. Either is an observation in the AP's state
 * before the commit, not B2's at op 140: the cores are up, A2's DCS and fabric
 * moves haven't been made, nor B2's own raises (Pullfrog on #83).
 *
 * The stages are the run's own from step 8's command on, gates_mark=1's with
 * the byte's meaning the one difference (siod_stage()): 0x3d for 0x4b, 0x3f
 * and 0x3e for 0x55 and 0x54.
 *
 * What it adds to the time between step 4, where WD1's marker is set, and the
 * commit: a few ms if every poll is met at once, 100 ms more for a gate that
 * stays open, and at worst about 5 s (42 polls and three of 100 ms, 43 waits
 * of 10 ms). The RTC's second is read before it, so a restart is timed from
 * before it too.
 *
 * A run that stops inside it leaves stage 0x3d with 0x6016 at 0 (before the
 * first send) or with none of the "back" bits (in the write-back), and restarts by the
 * panic (about 10 s) or by WD1's marker (about 90 s after step 4).
 */
#ifndef SIO_DOWN_H
#define SIO_DOWN_H

#ifndef __KERNEL__
#include <stdbool.h>
#include <stdint.h>
typedef uint8_t u8;
typedef uint16_t u16;
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

/* A power word's fields, as the byte sorts them: gate_test.h's GATE_PS_SET and GATE_PS_ACTUAL, cut the other way. */
#define SIOD_PS_STATE		(0xfU | GATE_PS_ACTUAL)		/* the target and the actual state */
#define SIOD_PS_AUTO_EN		(1U << 28)			/* auto-enable */
#define SIOD_PS_AUTO_ST		(0xfU << 24)			/* bits 27:24 */
#define SIOD_PS_AUTO		(SIOD_PS_AUTO_EN | SIOD_PS_AUTO_ST)
/* The five of the 43 whose bits 27:24 have read anything but 0, awake or after a switch-off: SIO, SIO_CPU, SIO_SPMI0-2. */
#define SIOD_AUTO_OFFS		{ 0x180, 0x188, 0x218, 0x220, 0x228 }

/* The byte with bit 6 clear: the subtree went off. */
#define SIOD_CLOSED		(1U << 0)
#define SIOD_AUTO_SHIFT		1
#define SIOD_AUTO_MASK		(3U << SIOD_AUTO_SHIFT)
#define SIOD_BACK_STATE		(1U << 3)
#define SIOD_BACK_EN		(1U << 4)
#define SIOD_BACK_GATE		(1U << 5)
#define SIOD_BACK_ALL		(SIOD_BACK_STATE | SIOD_BACK_EN | SIOD_BACK_GATE)
/* Bit 6 set: it didn't, and bits 5:0 are a place. */
#define SIOD_AT			(1U << 6)
#define SIOD_AT_MASK		0x3fU
#define SIOD_AT_POLL		62		/* all 43 read off, but a bit-11 poll wasn't met */
#define SIOD_AT_CLOSED		63		/* the gate read closed before the first write: nothing written */
#define SIOD_MARK		(1U << 7)

/* What the 43 words' auto fields read after the 0, in bits 2:1. */
enum siod_auto {
	SIOD_AUTO_EN_SET,	/* an auto-enable still read set */
	SIOD_AUTO_OTHER,	/* auto-enable 0 in all, bits 27:24 neither 0 nor the expected f somewhere */
	SIOD_AUTO_F,		/* auto-enable 0 in all, bits 27:24 f in some of SIOD_AUTO_OFFS' five and 0 in the rest */
	SIOD_AUTO_ZERO,		/* all 0 */
};

/* sio_down=1's stages, from step 8's command on, each for the one gates_mark=1 sends there. */
#define M1_SIOD_STAGE_PRE	0x3d	/* in place of 0x4b */
#define M1_SIOD_STAGE		0x3f	/* in place of 0x55 */
#define M1_SIOD_STAGE_PEND	0x3e	/* in place of 0x54 */

/* A power word is off in its power state: its target and its actual state read 0. What the gate can depend on. */
static inline bool siod_down(u32 v)
{
	return !(v & SIOD_PS_STATE);
}

/* off: a word's offset in SIO's page. True for the five whose bits 27:24 read f with auto-enable clear. */
static inline bool siod_has_auto(u16 off)
{
	static const u16 five[] = SIOD_AUTO_OFFS;
	unsigned int i;

	for (i = 0; i < sizeof(five) / sizeof(five[0]); i++) {
		if (off == five[i])
			return true;
	}
	return false;
}

/* off: SIOD_OFFS. now: the 43 words in that order, read after the polls. Their auto fields, as one of enum siod_auto. */
static inline u8 siod_auto(const u16 *off, const u32 *now)
{
	u8 c = SIOD_AUTO_ZERO;
	unsigned int i;

	for (i = 0; i < SIOD_N; i++) {
		if (now[i] & SIOD_PS_AUTO_EN)
			return SIOD_AUTO_EN_SET;
		if (!(now[i] & SIOD_PS_AUTO_ST))
			continue;
		if ((now[i] & SIOD_PS_AUTO_ST) != SIOD_PS_AUTO_ST || !siod_has_auto(off[i]))
			c = SIOD_AUTO_OTHER;
		else if (c == SIOD_AUTO_ZERO)
			c = SIOD_AUTO_F;
	}
	return c;
}

/*
 * One word after its write-back, against how it was found: SIOD_BACK_STATE if its target and actual state read the same,
 * SIOD_BACK_EN if its auto-enable does. That one is a setting, and the commit has to find it as the recipe left it
 * (Pullfrog on #86); bits 27:24 aren't, and aren't asked.
 */
static inline u8 siod_back(u32 v, u32 found)
{
	return (u8)(((v ^ found) & SIOD_PS_STATE ? 0 : SIOD_BACK_STATE) | ((v ^ found) & SIOD_PS_AUTO_EN ? 0 : SIOD_BACK_EN));
}

/* The byte for a switch-off that wasn't made, or didn't leave the subtree off: place is 0 to 42, SIOD_AT_POLL or SIOD_AT_CLOSED. */
static inline u8 siod_byte_at(unsigned int place)
{
	return (u8)(SIOD_MARK | SIOD_AT | (place & SIOD_AT_MASK));
}

/*
 * The byte. off: SIOD_OFFS. now: the 43 words in that order, read after the polls. polls: every bit-11 poll was met.
 * closed: 0x28e5a0050 read closed after its poll. back: SIOD_BACK_ALL's bits, 0 in the first send.
 */
static inline u8 siod_byte(const u16 *off, const u32 *now, bool polls, bool closed, u8 back)
{
	unsigned int i;

	for (i = 0; i < SIOD_N; i++) {
		if (!siod_down(now[i]))
			return siod_byte_at(i);
	}
	if (!polls)
		return siod_byte_at(SIOD_AT_POLL);
	return (u8)(SIOD_MARK | (closed ? SIOD_CLOSED : 0) | ((unsigned int)siod_auto(off, now) << SIOD_AUTO_SHIFT) | (back & SIOD_BACK_ALL));
}

/* The stage sio_down=1 sends where gates_mark=1 would send s; any other stage as it is. */
static inline u8 siod_stage(u8 s)
{
	return s == M1_GATES_STAGE_PRE ? M1_SIOD_STAGE_PRE : s == M1_GATES_STAGE ? M1_SIOD_STAGE :
	       s == M1_GATES_STAGE_PEND ? M1_SIOD_STAGE_PEND : s;
}

#endif /* SIO_DOWN_H */
