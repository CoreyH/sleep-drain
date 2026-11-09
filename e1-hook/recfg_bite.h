/* SPDX-License-Identifier: GPL-2.0 */
/*
 * The feasibility run's one SRAM word (PLAN.md; RESEARCH.md, "A different
 * approach"): A2's WD2 bite, where it is, how to tell it's there, and the byte
 * CPU0 records after the commit. Arithmetic only, with no register access, for
 * e1_hook.c, recfg_peek.c, recfg_poke.c and tests/ alike, as m1_position.h is.
 *
 * The engine's sequences are in PongoOS's recfg format (recfg.h, MIT). A
 * Write32 op is a head word (bits 1:0 = 1, bits 5:2 the count less one, bits
 * 31:6 the page's address >> 10), then the registers' offsets >> 2, a byte
 * each, padded to a word, then the values. Every table-A sequence opens with
 * one such op on the watchdog page: WD2's control := 0, count := 0, bite :=
 * 30 s, control := 4 (RESET_EN). The words expected here are built from that,
 * not copied from the dump, which stays out of this repository.
 *
 * The sample, recfg_bite=1's byte at 0x6005, sent in one command with stage
 * 0x47 right after the commit and the alarm's commands:
 *   bits 1:0  WD2's bite (0x2922b0024): 0 another value, 1 the old 30 s, 2 the
 *             new 45 s, 3 the value it read awake at load (if that's neither)
 *   bits 5:2  PHASE (0x2922c0110) bits 3:0 (the sequences write 0 to 0xd; A2
 *             writes 4, 5 and 7)
 *   bit 6     WD2's control (0x2922b002c) has RESET_EN and its count
 *             (0x2922b0020) is within 0.25 s of CPU0's time since the commit
 *   bit 7     A2's bite word in the SRAM still read the new value in step 8,
 *             just before the commit
 */
#ifndef RECFG_BITE_H
#define RECFG_BITE_H

#ifndef __KERNEL__
#include <stdbool.h>
#include <stdint.h>
typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
#endif

#define RECFG_CFG_REG		0x2925c0200ULL	/* the table offset register's analogue */
#define RECFG_CFG_LOCK		0x2925c0214ULL	/* its lock's */
#define RECFG_SRAM		0x292f00000ULL
#define RECFG_TABLE		0x1000U		/* what the offset register reads: table A's place in the SRAM */
#define RECFG_A2_ENTRY		(RECFG_TABLE + 2 * 8)	/* table A's third pair: the entry's sequence */
#define RECFG_A2		0x2000U		/* where that points */
#define RECFG_SEQ_WORDS		6		/* A2's first op: head, offsets, four values */
#define RECFG_BITE		(RECFG_A2 + 4 * 4)	/* its third value */

#define RECFG_WDT		0x2922b0000ULL	/* the watchdog page */
#define RECFG_WD2_COUNT		0x20
#define RECFG_WD2_BITE		0x24
#define RECFG_WD2_CTRL		0x2c
#define RECFG_WD_RESET_EN	(1U << 2)

#define RECFG_BITE_OLD		0x2aea5400U	/* 720,000,000 ticks: 30 s at 24 MHz */
#define RECFG_BITE_NEW		0x405f7e00U	/* 1,080,000,000 ticks: 45 s */

#define RECFG_W32_HEAD(page, n)	((u32)(((page) >> 10) << 6) | (u32)((n) - 1) << 2 | 1U)
#define RECFG_OFFS4(a, b, c, d)	((u32)((a) >> 2) | (u32)((b) >> 2) << 8 | (u32)((c) >> 2) << 16 | (u32)((d) >> 2) << 24)

enum recfg_verdict {
	RECFG_OLD,		/* everything as expected, the bite 30 s */
	RECFG_NEW,		/* everything as expected, the bite 45 s */
	RECFG_BAD_BITE,		/* everything else as expected, another bite */
	RECFG_BAD_CFG,		/* the offset register doesn't read 0x1000 */
	RECFG_LOCKED,		/* its lock reads non-zero */
	RECFG_BAD_TABLE,	/* table A's entry 2 doesn't point at SRAM + 0x2000 */
	RECFG_BAD_SEQ,		/* A2 doesn't open with the WD2 op */
};

/*
 * cfg, lock: the two registers; entry: table A's entry 2, two words; seq: A2's
 * first RECFG_SEQ_WORDS words. The checks run in the order above, so a verdict
 * names the first thing that isn't as expected.
 */
static inline enum recfg_verdict recfg_bite_verdict(u32 cfg, u32 lock, const u32 *entry, const u32 *seq)
{
	if (cfg != RECFG_TABLE)
		return RECFG_BAD_CFG;
	if (lock)
		return RECFG_LOCKED;
	if (entry[0] != (u32)((RECFG_SRAM + RECFG_A2) >> 4) || entry[1])
		return RECFG_BAD_TABLE;
	if (seq[0] != RECFG_W32_HEAD(RECFG_WDT, 4) ||
	    seq[1] != RECFG_OFFS4(RECFG_WD2_CTRL, RECFG_WD2_COUNT, RECFG_WD2_BITE, RECFG_WD2_CTRL) ||
	    seq[2] || seq[3] || seq[5] != RECFG_WD_RESET_EN)
		return RECFG_BAD_SEQ;
	if (seq[4] == RECFG_BITE_OLD)
		return RECFG_OLD;
	if (seq[4] == RECFG_BITE_NEW)
		return RECFG_NEW;
	return RECFG_BAD_BITE;
}

static inline const char *recfg_verdict_str(enum recfg_verdict v)
{
	switch (v) {
	case RECFG_OLD:
		return "the old value, 30 s";
	case RECFG_NEW:
		return "the new value, 45 s";
	case RECFG_BAD_BITE:
		return "neither the old value nor the new one, in the op as expected";
	case RECFG_BAD_CFG:
		return "not checked: the table offset register's analogue doesn't read 0x1000";
	case RECFG_LOCKED:
		return "not checked: the lock's analogue reads non-zero";
	case RECFG_BAD_TABLE:
		return "not checked: table A's entry 2 doesn't point at the SRAM + 0x2000";
	case RECFG_BAD_SEQ:
		return "not checked: A2 doesn't open with the op that arms WD2";
	}
	return "not checked";
}

#ifndef __KERNEL__
/* The ten words the verdict needs, in the order recfg_peek's stage 6 and recfg_poke read them. */
#define RECFG_BITE_WORDS	(4 + RECFG_SEQ_WORDS)
static const struct recfg_word {
	uint64_t pa;
	const char *what;
} recfg_bite_words[RECFG_BITE_WORDS] = {
	{ RECFG_CFG_REG, "the table offset register's analogue" },
	{ RECFG_CFG_LOCK, "its lock's analogue" },
	{ RECFG_SRAM + RECFG_A2_ENTRY, "table A's entry 2: A2's address >> 4" },
	{ RECFG_SRAM + RECFG_A2_ENTRY + 4, "its second word" },
	{ RECFG_SRAM + RECFG_A2, "A2's first op: its head" },
	{ RECFG_SRAM + RECFG_A2 + 4, "the four registers' offsets >> 2" },
	{ RECFG_SRAM + RECFG_A2 + 8, "WD2's control" },
	{ RECFG_SRAM + RECFG_A2 + 12, "WD2's count" },
	{ RECFG_SRAM + RECFG_BITE, "WD2's bite" },
	{ RECFG_SRAM + RECFG_A2 + 20, "WD2's control" },
};
#endif

#define M1_BITE_OTHER	0
#define M1_BITE_OLD	1
#define M1_BITE_NEW	2
#define M1_BITE_AWAKE	3

/* awake: WD2's bite as the load read it. */
static inline u8 m1_bite_class(u32 bite, u32 awake)
{
	if (bite == RECFG_BITE_NEW)
		return M1_BITE_NEW;
	if (bite == RECFG_BITE_OLD)
		return M1_BITE_OLD;
	return bite == awake ? M1_BITE_AWAKE : M1_BITE_OTHER;
}

/* count_near: m1_pos_count_near() of WD2's count; sram: A2's bite word as step 8 read it. */
static inline u8 m1_recfg_sample(u32 bite, u32 awake, u32 phase, u32 wd_ctrl, bool count_near, u32 sram)
{
	return (u8)(m1_bite_class(bite, awake) | (phase & 0xf) << 2 |
		    ((wd_ctrl & RECFG_WD_RESET_EN) && count_near ? 1U << 6 : 0) |
		    (sram == RECFG_BITE_NEW ? 1U << 7 : 0));
}

#endif /* RECFG_BITE_H */
