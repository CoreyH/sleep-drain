/* SPDX-License-Identifier: GPL-2.0 */
/*
 * The 22 words by themselves (PLAN.md, "The order after the look"; RESEARCH.md,
 * "The sio_steps=1 run" and "pre_test=1 as built"): what e1_hook probe=1 look=1
 * pre_test=1 does after its second look, and the record it leaves. Constants
 * and arithmetic only, for e1_hook.c and tests/ alike; the words, B2's order
 * and B2's values are sio_steps.h's.
 *
 * B2 opens (its ops 8 to 29) with 0x28e03801c := 0x80101100 and 1 to the seven
 * words 0x28e0a0004 to 0x28e0a001c and the fourteen 0x28e5b0004 to 0x28e5b0038,
 * which read 0x80100100, 2 2 2 2 3 2 2 and 2 2 2 2 2 2 2 2 0 0 2 2 1 1 awake
 * and after noirq. sio_steps=1 wrote them in the recipe's state, after the
 * switch-off of SIO's subtree, and its one bit said they didn't all read back
 * as written (0x28e03801c within 100 ms, with bit 30, pending, clear). Which
 * didn't, and what it read, that byte couldn't hold. The engine itself never
 * reads the 21; B1, a wake sequence, polls bit 30 of 0x28e03801c clear after
 * its own write.
 *
 * So, after the look's second pass, with nothing stopped, in the suspend that
 * is then failed:
 *   1. the + 0 word of the two pages, 0x28e0a0000 and 0x28e5b0000, which B0 and
 *      B1 write 0x2000000 and nothing here had read (read awake at the load
 *      first, under this step's mark with LOOK_LOAD set), then
 *   2. the 22 read as found, and the six gate words;
 *   3. 0x28e03801c := 0x80101100, read at once and for up to 100 ms, until the
 *      whole word reads as written;
 *   4. 1 to the seven, in B2's order, and each read back;
 *   5. 1 to the fourteen, in B2's order, and each read back;
 *   6. 100 ms, then all 22, the two + 0 words and the six gates read again;
 *   7. 0x28e03801c written back as found, less bit 30, and up to 100 ms for it
 *      to read so: B1's order, which has that word first and the 21 near its
 *      end;
 *   8. the seven written back as found, in address order as B1 has them;
 *   9. the fourteen likewise, then up to 100 ms for all 21 to read as found;
 *  10. up to 100 ms more for 0x28e03801c, the gates and the two + 0 words once
 *      more, and the byte.
 * Every word is logged in full at each step.
 *
 * What it can say: which of the 22 take B2's value with every core up and
 * nothing of the recipe done, and what the others read. All take, bit 30
 * clears: in the sio_steps=1 run it was the recipe's state that differed. Bit
 * 30 stays set: that word's write doesn't finish with a core up. Some of the
 * 21 don't read 1: which, and what instead. What it can't: why.
 *
 * What it risks: the 22 are clock words (0x28e03801c is in the page of the
 * clock generators' muxes; the 21 sit beside per-device clock entries), and
 * B2 gives them these values with every core down. sio_steps=1's run wrote
 * all 22 with the cores parked and went on to its commit; whether the kernel
 * keeps running across them, and resumes with them put back, hasn't been
 * tried. The gate test was meant to come back too, and didn't. So the record
 * in the PMU is made to stand without the kernel log:
 *   - the look's stage, 0x0f, with the step about to be taken at 0x6010,
 *     PRT_PASS set in it;
 *   - 0x6005 and 0x6011-0x6016 zeroed at the start;
 *   - six bytes at 0x6011-0x6016, prt_pack(), before anything is written
 *     back, in one command with the mark of the step that follows,
 *     PRT_AT_BACK. That mark, or a later one without PRT_NO_SNAP, is what says
 *     the six bytes are this test's (as the gate test's are: Pullfrog on #80);
 *   - prt_byte() at 0x6005 at the end: what came back.
 */
#ifndef PRE_TEST_H
#define PRE_TEST_H

#include "look.h"
#include "sio_steps.h"

#ifndef __KERNEL__
typedef uint64_t u64;
#endif

/*
 * The 22, by address: 0 is 0x28e03801c; 1 to 7 the seven, from 0x28e0a0004; 8 to 21 the fourteen, from 0x28e5b0004.
 * The reads, the log, the record and the write-back go in this order; the writes of 1 go in B2's (SST_A_OFFS, SST_B_OFFS).
 */
#define PRT_N			22
#define PRT_SEL_N		21
#define PRT_A_FIRST		1
#define PRT_B_FIRST		8
static inline u64 prt_addr(unsigned int i)
{
	return !i ? LOOK_PRE_CLK + LOOK_PRE_CLK_OFF : i < PRT_B_FIRST ? LOOK_PRE_A + LOOK_PRE_FIRST + 4 * (i - PRT_A_FIRST) :
	       LOOK_PRE_B + LOOK_PRE_FIRST + 4 * (i - PRT_B_FIRST);
}

/* The value B2 writes to word i. */
static inline u32 prt_val(unsigned int i)
{
	return i ? SST_PRE_VAL : SST_CLK_VAL;
}

/* What word i is written back to: as found; 0x28e03801c less its pending bit, which isn't one to write. */
static inline u32 prt_back_val(unsigned int i, u32 found)
{
	return i ? found : found & ~SST_CLK_PENDING;
}

/*
 * What a read of word i says, in two bits. "With more" is the written value with other bits set, where a pending bit
 * would show: bit 30 for 0x28e03801c, anything above the low byte for the 21.
 */
enum prt_code {
	PRT_OTHER,		/* 0: none of the three below */
	PRT_WRITTEN,		/* as B2 writes it, exactly */
	PRT_FOUND,		/* as found, which isn't as written */
	PRT_WRITTEN_MORE,	/* 3: as written, with more set */
};
#define PRT_SEL_MORE		0xffffff00U
static inline unsigned int prt_code(unsigned int i, u32 v, u32 found)
{
	u32 more = i ? PRT_SEL_MORE : SST_CLK_PENDING;

	return v == prt_val(i) ? PRT_WRITTEN : v == found ? PRT_FOUND : (v & ~more) == prt_val(i) ? PRT_WRITTEN_MORE : PRT_OTHER;
}

/*
 * Six bytes for the PMU's 0x6011-0x6016, 48 bits, the lowest first:
 *   bits 0 to 41   prt_code() of each of the 21 at step 6's read, two bits each, word 1 lowest
 *   bits 42, 43    prt_code() of 0x28e03801c at the end of step 3's 100 ms
 *   bits 44, 45    the same at step 6's read
 *   bit 46         each of the 21 read the same right after its group's writes as at step 6
 *   bit 47         0x28e03801c read with bit 30, pending, set right after its write
 * was, first and later are the 22 as found, as read at once and as read at step 6; clk_poll is step 3's last read.
 */
#define PRT_PACK_N		6
static inline void prt_pack(const u32 *was, const u32 *first, const u32 *later, u32 clk_poll, u8 *p)
{
	unsigned int i;
	bool same = true;
	u64 bits = 0;

	for (i = 1; i < PRT_N; i++) {
		bits |= (u64)prt_code(i, later[i], was[i]) << (2 * (i - 1));
		if (first[i] != later[i])
			same = false;
	}
	bits |= (u64)prt_code(0, clk_poll, was[0]) << 42 | (u64)prt_code(0, later[0], was[0]) << 44;
	bits |= (same ? 1ULL << 46 : 0) | (first[0] & SST_CLK_PENDING ? 1ULL << 47 : 0);
	for (i = 0; i < PRT_PACK_N; i++)
		p[i] = (u8)(bits >> (8 * i));
}

/* The steps, each written to the look's 0x6010 before it's taken, with PRT_PASS set. m1-crumbs has a line for each. */
#define PRT_PASS		0x80
/* In a mark from PRT_AT_BACK on: the command that carries prt_pack()'s bytes wasn't acked, so 0x6011-0x6016 aren't this test's. gate_test.h's bit. */
#define PRT_NO_SNAP		0x40
enum prt_at {
	PRT_AT_NONE,
	PRT_AT_ZERO,		/* the two pages' + 0 words read; with LOOK_LOAD, at the load */
	PRT_AT_READ,		/* the 22 and the gates read as found */
	PRT_AT_CLK,		/* 0x28e03801c := 0x80101100 */
	PRT_AT_A,		/* 1 to the seven */
	PRT_AT_B,		/* 1 to the fourteen */
	PRT_AT_WAIT,		/* 100 ms, and everything read again */
	PRT_AT_BACK,		/* 0x28e03801c written back as found */
	PRT_AT_BACK_A,		/* the seven written back as found */
	PRT_AT_BACK_B,		/* the fourteen written back as found */
	PRT_AT_DONE,		/* 10: every step taken: the byte */
};

/*
 * The byte at 0x6005, after the write-back. Bit 7 says it landed. Bit 6: 0x28e03801c read as found, less bit 30,
 * with all 22 written back and up to 100 ms more. Bits 4:0: 0 if all 21 read as found within 100 ms of the last write-back, or the
 * number (1 to 21, as prt_addr() counts) of the first that didn't; bit 5: more than one didn't. 0xc0: all 22 back.
 */
#define PRT_MARK		(1U << 7)
#define PRT_CLK_BACK		(1U << 6)
#define PRT_MORE		(1U << 5)
#define PRT_ALL_BACK		(PRT_MARK | PRT_CLK_BACK)
static inline u8 prt_byte(const u32 *was, const u32 *back)
{
	unsigned int i, bad = 0, n = 0;

	for (i = 1; i < PRT_N; i++) {
		if (back[i] != was[i] && !n++)
			bad = i;
	}
	return (u8)(PRT_MARK | (back[0] == prt_back_val(0, was[0]) ? PRT_CLK_BACK : 0) | (n > 1 ? PRT_MORE : 0) | bad);
}

#endif /* PRE_TEST_H */
