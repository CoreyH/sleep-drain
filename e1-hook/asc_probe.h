/* SPDX-License-Identifier: GPL-2.0 */
/*
 * The probe of the three coprocessor words B2 polls (PLAN.md, "The order after
 * run 17"; RESEARCH.md, "After run 17"): the words, what the engine does around
 * them, and the three bytes the probe leaves in the breadcrumbs in case the
 * kernel log doesn't make it back. Arithmetic only, with no register access,
 * for e1_hook.c and tests/ alike, as wd2_bite.h is.
 *
 * B2, the second sequence of the entry pair, powers three coprocessors' domains
 * up in turn in its power-down and waits each time, 255 tries, for bit 1 of the
 * first word of the coprocessor's mailbox block, its ASC + 0x8000:
 *   op 49-51     SIO_CPU := 0x0f00024f, actual f, then 0x39bc08000 bit 1
 *   op 149-151   ANS2 := 0x0f00030f, actual f, then 0x38f408000 bit 1
 *   op 157-159   ps_pmp := 0x0f00024f, actual f, then 0x28ec08000 bit 1
 * Nothing documents that word, and nothing here had read it. Run 17 says B2
 * doesn't reach its op 224 with every core down, and these three are where the
 * recipe leaves things least like macOS: SIO's firmware idled, its CPU's RUN
 * cleared, its power cut by genpd at noirq and raised again in step 5; the PMP's
 * idled and stopped after slpa; ANS as Linux's NVMe suspend leaves it.
 *
 * e1_hook probe=1 (e1-hook/probe) reads them in that state without a one-way
 * run: at the first suspend's prepare and prepare_late it takes the recipe's own
 * steps on SIO and the PMP, reads each word before and after every step, for up
 * to 100 ms when bit 1 reads clear, logs each word in full, and then fails the
 * suspend with -EIO, so the Mac comes back awake. No key is written to the SMC,
 * nothing is committed and nothing replays. For ANS, whose domain Linux has off
 * by then, it does what B2's op 149 does, ANS2 := 0x0f00030f, and after the read
 * writes the word back as it found it.
 *
 * What a read here says is a lead, not where the pair stops: B2 polls later,
 * after its own raise of each domain and everything A2 and B2 do before it, and
 * what bit 1 means isn't known (Pullfrog on #75).
 *
 * The bytes, written through the kernel's path at the end of prepare_late, stage
 * 0x0e at 0x6004 last. Bit 7 of each says it landed. "bit 1" below is bit 1 of
 * the coprocessor's word at that moment, 0 too if the word wasn't read because
 * its domain didn't read on.
 *   0x6005, SIO   0 bit 1 with its firmware running, at prepare, once SIO_CPU
 *                 has been put fully on with the table's own word (awake it
 *                 reads actual 4: its clock gates itself); 1 after its
 *                 idle pair; 2 after RUN was cleared; 3 at prepare_late, after
 *                 its parents and SIO_CPU were raised again: the state B2's op
 *                 51 finds; 4 the idle pair returned 0; 5 RUN read 1 and then
 *                 0; 6 SIO_CPU read actual f for that last read
 *   0x6006, PMP   0 bit 1 at prepare, running; 1 at prepare_late, before its
 *                 idle pair; 2 after the idle pair; 3 after RUN was cleared: the
 *                 state B2's op 159 finds; 4 the idle pair returned 0; 5 RUN
 *                 read 1 and then 0; 6 ps_pmp read actual f at prepare_late
 *   0x6007, ANS   0 bit 1 at prepare, running; 1 ANS2 read actual f at
 *                 prepare_late, before anything was written; 2 bit 1 at
 *                 prepare_late, after the raise if one was needed: the state
 *                 B2's op 151 finds; 3 the raise was made and reached actual f
 */
#ifndef ASC_PROBE_H
#define ASC_PROBE_H

#ifndef __KERNEL__
#include <stdbool.h>
#include <stdint.h>
typedef uint8_t u8;
typedef uint32_t u32;
#endif

#define ASC_SIO_MBOX		0x39bc08000ULL	/* SIO's ASC + 0x8000 */
#define ASC_ANS_MBOX		0x38f408000ULL	/* ANS's */
#define ASC_PMP_MBOX		0x28ec08000ULL	/* the PMP's */
#define ASC_READY		(1U << 1)	/* the bit B2 waits for; its meaning isn't known */
#define ASC_ANS2_UP		0x0f00030fU	/* what B2's op 149 writes to ANS2 */

#define ASC_PROBE_STAGE		0x0e	/* no run's: 0x01-0x05 are the replay's K steps, 0x10 and up its stages */
#define ASC_PROBE_MARK		(1U << 7)

/* A power word's actual state, bits 7:4, reads f: the domain is on. */
static inline bool asc_ps_on(u32 ps)
{
	return ((ps >> 4) & 0xf) == 0xf;
}

static inline u8 asc_sio_byte(bool run, bool idle, bool stopped, bool late, bool idle_ok, bool stop_ok, bool on)
{
	return (u8)(ASC_PROBE_MARK | (run ? 1U : 0) | (idle ? 1U << 1 : 0) | (stopped ? 1U << 2 : 0) | (late ? 1U << 3 : 0) |
		    (idle_ok ? 1U << 4 : 0) | (stop_ok ? 1U << 5 : 0) | (on ? 1U << 6 : 0));
}

static inline u8 asc_pmp_byte(bool run, bool late, bool idle, bool stopped, bool idle_ok, bool stop_ok, bool on)
{
	return (u8)(ASC_PROBE_MARK | (run ? 1U : 0) | (late ? 1U << 1 : 0) | (idle ? 1U << 2 : 0) | (stopped ? 1U << 3 : 0) |
		    (idle_ok ? 1U << 4 : 0) | (stop_ok ? 1U << 5 : 0) | (on ? 1U << 6 : 0));
}

static inline u8 asc_ans_byte(bool run, bool was_on, bool late, bool raised)
{
	return (u8)(ASC_PROBE_MARK | (run ? 1U : 0) | (was_on ? 1U << 1 : 0) | (late ? 1U << 2 : 0) | (raised ? 1U << 3 : 0));
}

#endif /* ASC_PROBE_H */
