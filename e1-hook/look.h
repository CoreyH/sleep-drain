/* SPDX-License-Identifier: GPL-2.0 */
/*
 * The look (PLAN.md, "The order after the probe"; RESEARCH.md, "The probe's
 * run"): what e1_hook probe=1 look=1 reads, and the mark it leaves in the PMU
 * while it reads. Constants only, for e1_hook.c and tests/ alike.
 *
 * Run 17 says B2, the second sequence of the engine's entry pair, doesn't reach
 * its op 224 with every core down, and the probe says its three coprocessor
 * waits would pass. What else the pair waits on before that op depends on words
 * nothing here had read: the clock generators' (B2 writes the word at + 4 of
 * twelve of them and waits, with no timeout, for bit 31 of + 0 to clear on
 * eleven), six gate-status words (bit 30, after B2 has switched off what's
 * behind each), four words of the engine's own page beside the two already
 * read, the three p-state words A2 lowers, and the power words B2 switches off
 * and polls.
 *
 * With look=1 the probe stops nothing and writes no register. It reads those
 * words twice, awake at the module's load and after the noirq pass of the
 * suspend it then fails, and logs each in full. Nothing is stopped, so the
 * suspend should be an ordinary failed one, with the log coming back; that
 * hasn't been tried (the probe, which stopped two firmwares, didn't come back).
 *
 * What a word read here says: what Linux's own suspend leaves after noirq. The
 * recipe goes on from there (the DCPs shut down, SIO's firmware and the PMP's
 * stopped, the SMC idled, the PMP's requests withdrawn, the cores parked, and
 * macOS's table), and a word here may follow any of that; so it's the value the
 * engine finds only for a word none of those changes. Read against what the
 * engine's wake sequence leaves (the state its entry is written to start from;
 * that listing stays out of the repo), a word that differs is a lead.
 *
 * The mark: stage 0x0f at 0x6004 and the group about to be read at 0x6010,
 * through the kernel's path, so that a read that faults says which group it was
 * in; the load's look sets LOOK_LOAD in it as well, so that the mark also says
 * which of the two looks it was. The load's look zeroes both again once it's
 * through; the suspend's leaves LOOK_AT_DONE.
 *
 * Two of the words, + 0x008 and + 0x024 of the engine's page, the engine's
 * sequences write and never read. They're read last, in a group of their own,
 * after everything else has been logged.
 */
#ifndef LOOK_H
#define LOOK_H

#define LOOK_STAGE		0x0f	/* no run's, and not the probe's 0x0e */
#define LOOK_AT			0x6010	/* the PMU scratch byte with the group about to be read */

enum look_at {
	LOOK_AT_NONE,		/* 0: nothing marked */
	LOOK_AT_PSTATE,		/* the fabric's, 0x28e20c400 and DCS's p-state words */
	LOOK_AT_AON,		/* six words of the engine's page, 0x2922c0000, that its sequences read */
	LOOK_AT_POWER,		/* the power words B2 switches off and polls */
	LOOK_AT_GATES,		/* six gate-status words */
	LOOK_AT_CLKGEN,		/* fifteen clock generators' two words each */
	LOOK_AT_AON_W,		/* two words of the engine's page that its sequences only write */
	LOOK_AT_DONE,		/* every group read */
};
#define LOOK_LOAD		0x10	/* set in the mark by the load's look */

/*
 * The engine's page. Its sequences read these six: B1 leaves + 0x38, + 0x3c and + 0x40, B2 opens on + 0x44, + 0x10c is
 * HS_B and + 0x110 PHASE. And they write these two without ever reading them: + 0x008 (B1's last write, 1; B2's
 * first, 0) and + 0x024 (B1 0, B2 1).
 */
#define LOOK_AON_PAGE		0x2922c0000ULL
#define LOOK_AON_OFFS		{ 0x038, 0x03c, 0x040, 0x044, 0x10c, 0x110 }
#define LOOK_AON_W_OFFS		{ 0x008, 0x024 }

/* Power words in SIO's page, 0x28e580000: every one from SIO's own to AES's, 8 apart, and ANS2's. */
#define LOOK_SIO_PAGE		0x28e580000ULL
#define LOOK_SIO_FIRST		0x180
#define LOOK_SIO_LAST		0x2d0
#define LOOK_SIO_ANS2		0x3b8
/* And in the first page, 0x28e080000: AIC, DWI, GPIO, ps_pmp (read here as everywhere, never written) and the three PCIe words. */
#define LOOK_PS_PAGE		0x28e080000ULL
#define LOOK_PS_OFFS		{ 0x108, 0x110, 0x120, 0x2d8, 0x2e8, 0x2f0, 0x320 }

/* The six gate-status words B2 polls for bit 30, in two pages. */
#define LOOK_GATE_A		0x28e5a0000ULL
#define LOOK_GATE_A_OFFS	{ 0x050, 0x064, 0x30c }
#define LOOK_GATE_B		0x28e09c000ULL
#define LOOK_GATE_B_OFFS	{ 0x310, 0x338, 0x34c }

/* The clock generators: fifteen blocks 0x2000 apart. + 0 has the bit the engine waits on, bit 31; + 4 is the word it writes. */
#define LOOK_CLKGEN		0x28e000000ULL
#define LOOK_CLKGEN_STEP	0x2000U
#define LOOK_CLKGEN_N		15

#endif /* LOOK_H */
