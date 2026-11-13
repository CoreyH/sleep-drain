/* SPDX-License-Identifier: GPL-2.0 */
/*
 * A2's three moves of its own, made by the AP before the commit and left
 * (PLAN.md, "The order after the look"; RESEARCH.md, "`a2_pre=1` as built"):
 * which words, what is written, what is waited for, the byte that records it
 * and the run's stages. Arithmetic only, with no register access, for e1_hook.c
 * and tests/ alike, as pcie_down.h is.
 *
 * A2, the first sequence of the engine's entry pair, once B2 has answered its
 * first handshake, makes three moves in the p-state page 0x28e20c000, each
 * followed by a wait with no timeout (its ops 11 to 16):
 *   DCS's word, 0x28e20c800       := 2, then bits 11:8 == 2 with bit 31 clear
 *   the fabric's, 0x28e20c000     := 2, then bits 11:8 == 2 with bit 31 clear
 *   the third word, 0x28e20c400   := 0, then bits 10:8 == 0 with bit 31 clear
 * Each write is the literal value, the whole word. None of the three moves has
 * been seen on this Mac: the recipe leaves DCS asked down to 5 (the table's last
 * write to it), fabric_up=1 leaves the fabric at 3, and the third word reads
 * 0x222 awake and after noirq.
 *
 * So e1_hook a2_pre=1, on --m1-gates' recipe, has CPU0 do in step 8, after the
 * stage and the steps' outcomes and before the commit, in place of
 * gates_mark=1's reads:
 *   0. the five ClkGen muxes the table parked in step 5 read once (a rider, not
 *      a move: nothing had read them after their write, only waited for bit 30
 *      to be clear, which a write that was dropped passes too; `probe --pre`
 *      found the AP's write refused by a word in their page), and the byte to
 *      0x6016 with its mark and that one bit
 *   1. A2's op 11, a literal 2 to DCS's word; up to 100 ms for A2's condition;
 *      the word read again, and the byte with DCS's two bits
 *   2. A2's op 13, a literal 2 to the fabric's word; the same; the byte with
 *      the fabric's two bits, and DCS's again from a new read
 *   3. A2's op 15, a literal 0 to the third word; the same; the byte, whole,
 *      all three read anew: the last read before the commit. The alarm's last
 *      command, after the commit, sends it once more, as gates_mark=1's is sent
 * Nothing is written back: the commit finds the three as A2's own writes would
 * leave them, so A2's writes meet words that are already there, and a restart
 * at 120 s in place of 90 s would say B2 got past its op 224 (wd1_mark.h).
 *
 * What it can say. All three taken and settled, and the restart still at 90 s:
 * A2's own steps are less likely to be where the pair stops, since the engine
 * finds them made. A move that takes and doesn't settle within the AP's 100 ms,
 * with CPU0 and the other cores as the recipe leaves them: a candidate, not
 * proof that A2 waits there for ever, since A2 makes the move later, with every
 * core down, and waits with no bound (Pullfrog on #79). A move whose value
 * doesn't read back: the AP's write was refused or changed back (the PMP owns
 * DCS under Linux), which says nothing about A2. CPU0 not coming through a move
 * (the byte stops short; WD1's marker restarts the Mac at 90 s) says that move
 * can't be made with a core running, and no more.
 *
 * The byte, at 0x6016 (gates_mark.h's GATES_AT), under the run's own stages:
 *   bit 0  NOT all five muxes read 0x80100000, the table's value. Set is the
 *          bad case on purpose, so that a byte in which everything went well
 *          isn't 0xff
 *   bit 1  DCS's word read asked for 2 (bits 3:0) at this send's read of it
 *   bit 2  DCS's word read as A2 waits for it, at this send's read of it
 *   bit 3  the fabric's word read asked for 2
 *   bit 4  the fabric's word read as A2 waits for it
 *   bit 5  the third word read asked for 0 (bits 3:0)
 *   bit 6  the third word read as A2 waits for it
 *   bit 7  always set
 * A move's two bits are 0 in the sends before it. In its own send they're from
 * the read after its wait; in later sends from a read just before the send, so
 * the last send says what the commit found. As the desk expects it if all three
 * take: 0xfe. 0x80 or 0x81 at the last stages: none took.
 *
 * The stages are the run's own from step 8's command on, gates_mark=1's with
 * the byte's meaning the one difference (a2p_stage()): 0x19 for 0x4b, 0x1b and
 * 0x1a for 0x55 and 0x54.
 *
 * What it adds to the time between step 4, where WD1's marker is set, and the
 * commit: a few ms if every wait is met at once, and at worst about 0.3 s. The
 * RTC's second is read before it, so a restart is timed from before it too.
 *
 * A run that stops inside it leaves stage 0x19 with 0x6016 at 0 (before the
 * first send) or short of the move it stopped in, and restarts by the panic
 * (about 10 s) or by WD1's marker (about 90 s after step 4). Stage 0x19 with a
 * byte that has no move's bits is one of two things the record can't tell
 * apart: stopped in DCS's move, or no write took and the commit's stage didn't
 * land.
 */
#ifndef A2_PRE_H
#define A2_PRE_H

#ifndef __KERNEL__
#include <stdbool.h>
#include <stdint.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
#endif

#include "gates_mark.h"

/* The three words, offsets from the fabric's word 0x28e20c000 (fabric_up.h's FAB_PS), in the order A2 writes them. */
#define A2P_N			3
#define A2P_DCS			0x800
#define A2P_FAB			0x000
#define A2P_THIRD		0x400
#define A2P_OFFS		{ A2P_DCS, A2P_FAB, A2P_THIRD }
#define A2P_VALS		{ 2, 2, 0 }		/* A2's ops 11, 13 and 15: the whole word */
#define A2P_ASKED		0xfU			/* bits 3:0: the p-state asked for */
#define A2P_MASKS		{ 0x80000f00U, 0x80000f00U, 0x80000700U }	/* A2's ops 12, 14 and 16 */
#define A2P_WANTS		{ 0x00000200U, 0x00000200U, 0x00000000U }

/* The rider: the five ClkGen-SOC muxes, in the page 0x28e038000, and the value the table writes to each. */
#define A2P_MUX_PAGE		0x28e038000ULL
#define A2P_MUX_N		5
#define A2P_MUX_VAL		0x80100000U

#define A2P_MUX_BAD		(1U << 0)
#define A2P_MARK		(1U << 7)
#define A2P_TOOK(i)		(1U << (1 + 2 * (i)))	/* move i's value read back */
#define A2P_SETTLED(i)		(1U << (2 + 2 * (i)))	/* move i read as A2 waits for it */

/* a2_pre=1's stages, from step 8's command on, each for the one gates_mark=1 sends there. */
#define M1_A2P_STAGE_PRE	0x19	/* in place of 0x4b */
#define M1_A2P_STAGE		0x1b	/* in place of 0x55 */
#define M1_A2P_STAGE_PEND	0x1a	/* in place of 0x54 */

/* Word i (in A2P_OFFS' order) reads asked for the value A2 writes to it. */
static inline bool a2p_took(unsigned int i, u32 v)
{
	static const u32 val[A2P_N] = A2P_VALS;

	return i < A2P_N && (v & A2P_ASKED) == val[i];
}

/* Word i reads as A2's wait after its write wants it. */
static inline bool a2p_settled(unsigned int i, u32 v)
{
	static const u32 mask[A2P_N] = A2P_MASKS, want[A2P_N] = A2P_WANTS;

	return i < A2P_N && (v & mask[i]) == want[i];
}

/*
 * The byte. mux_ok: all five muxes read A2P_MUX_VAL. v: the three words in A2P_OFFS' order, as last read. made: how
 * many of the moves have been made (0 to 3); a word past that leaves its two bits clear whatever it reads.
 */
static inline u8 a2p_byte(bool mux_ok, const u32 *v, unsigned int made)
{
	u8 b = A2P_MARK | (mux_ok ? 0 : A2P_MUX_BAD);
	unsigned int i;

	for (i = 0; i < made && i < A2P_N; i++) {
		if (a2p_took(i, v[i]))
			b |= A2P_TOOK(i);
		if (a2p_settled(i, v[i]))
			b |= A2P_SETTLED(i);
	}
	return b;
}

/* The stage a2_pre=1 sends where gates_mark=1 would send s; any other stage as it is. */
static inline u8 a2p_stage(u8 s)
{
	return s == M1_GATES_STAGE_PRE ? M1_A2P_STAGE_PRE : s == M1_GATES_STAGE ? M1_A2P_STAGE :
	       s == M1_GATES_STAGE_PEND ? M1_A2P_STAGE_PEND : s;
}

#endif /* A2_PRE_H */
