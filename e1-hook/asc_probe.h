/* SPDX-License-Identifier: GPL-2.0 */
/*
 * The probe of the three coprocessor words B2 polls (PLAN.md, "The order after
 * run 17"; RESEARCH.md, "After run 17"): the words, what the engine does around
 * them, and the record the probe leaves in the breadcrumbs in case the kernel
 * log doesn't make it back. Arithmetic only, with no register access, for
 * e1_hook.c and tests/ alike, as wd2_bite.h is.
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
 * run: at the prepare and prepare_late of the first suspend to get that far it
 * takes the recipe's own steps on SIO and the PMP, reads each word before and
 * after every step, again while bit 1 reads clear (B2's 255 tries, over about
 * 100 ms), logs each word in full, and then fails the suspend with -EIO, so the
 * Mac comes back awake. No key is written to the SMC, nothing is committed and
 * nothing replays. For ANS, whose domain Linux has off by then, it does what
 * B2's op 149 does, ANS2 := 0x0f00030f, and after the read writes the word back
 * as it found it, less the bits the kernel never writes back either.
 *
 * What a read here says is a lead, not where the pair stops: B2 polls later,
 * after its own raise of each domain and everything A2 and B2 do before it, and
 * what bit 1 means isn't known (Pullfrog on #75).
 *
 * The record, all through the kernel's path.
 *
 * Stage 0x0e goes to 0x6004 first, at the start of prepare, and from then on
 * 0x6010 has the step about to be taken (enum asc_at): a Mac that goes down
 * inside the probe, where the console is suspended and no watchdog runs, says
 * that it began and where it was (the adversarial review of #76).
 *
 * The three bytes go out at the end of prepare_late. Bit 7 of each says it
 * landed. "bit 1" below is bit 1 of the coprocessor's word at that moment, 0 too
 * if the word wasn't read because its domain didn't read on. For the read in the
 * state B2 finds, the last of each coprocessor, the byte also says whether it
 * was made: asc_sio_final() and the two beside it read that, 1 set, 0 clear, -1
 * not read, and both the probe's SUMMARY line and m1-crumbs go by them, so that
 * a read that wasn't made is never called clear (Pullfrog on #76). The kernel
 * log has every word in full.
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
 *                 read 1 and then 0; 6 ps_pmp read actual f at prepare_late,
 *                 for each read of the word that was made there
 *   0x6007, ANS   0 bit 1 at prepare. Its firmware isn't running then: the NVMe
 *                 driver's suspend has shut it down and cleared its CPU's RUN
 *                 (apple_nvme_suspend()), with ANS2 still on; 1 ANS2 read
 *                 actual f at prepare_late, before anything was written; 2 bit
 *                 1 at prepare_late, after the raise if one was needed: the
 *                 state B2's op 151 finds, read only with bit 1 or bit 3;
 *                 3 the raise was made and reached actual f; 4 bit 1 awake, at
 *                 the module's load: the one read with ANS's firmware running;
 *                 5 that read was made (ANS2 read actual f at load); 6 ANS2's
 *                 target and actual state read as found again within 10 ms of
 *                 the write-back (set too if it was never written)
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
#define ASC_TRIES		255		/* B2's count for each of the three waits */
#define ASC_ANS2_UP		0x0f00030fU	/* what B2's op 149 writes to ANS2 */
/*
 * What a whole-word write of a power word leaves out, as the kernel's driver does (pmgr-pwrstate.c): BUSY, bit 11,
 * which the hardware owns, and the two write-1-to-clear flags, bits 9 and 8.
 */
#define ASC_PS_NOT_WRITTEN	((1U << 11) | (1U << 9) | (1U << 8))
#define ASC_PS_STATE		0xffU		/* a power word's target, bits 3:0, and actual state, bits 7:4 */

#define ASC_PROBE_STAGE		0x0e	/* no run's: 0x01-0x05 are the replay's K steps, 0x10 and up its stages */
#define ASC_PROBE_MARK		(1U << 7)
#define ASC_PROBE_AT		0x6010	/* the PMU scratch byte with the step about to be taken */

/* The probe's steps, each written to ASC_PROBE_AT before it's taken. m1-crumbs has a line for each. */
enum asc_at {
	ASC_AT_NONE,		/* 0: nothing marked */
	ASC_AT_PREPARE,		/* prepare begun: SIO_CPU's table word */
	ASC_AT_SIO_RUN,		/* SIO's word, its firmware running */
	ASC_AT_ANS_PREP,	/* ANS's word, its firmware shut down, ANS2 on */
	ASC_AT_PMP_RUN,		/* the PMP's word, its firmware running */
	ASC_AT_SIO_IDLE,	/* SIO's idle pair */
	ASC_AT_SIO_IDLED,	/* SIO's word after it */
	ASC_AT_SIO_STOP,	/* SIO's RUN cleared */
	ASC_AT_SIO_STOPPED,	/* SIO's word after it */
	ASC_AT_NOIRQ,		/* prepare done: the kernel's noirq pass */
	ASC_AT_LATE,		/* prepare_late begun: the power words read */
	ASC_AT_ANS_UP,		/* ANS2 raised */
	ASC_AT_ANS_LATE,	/* ANS's word */
	ASC_AT_ANS_BACK,	/* ANS2 written back */
	ASC_AT_PMP_LATE,	/* the PMP's word before its idle pair */
	ASC_AT_PMP_IDLE,	/* the PMP's idle pair */
	ASC_AT_PMP_IDLED,	/* the PMP's word after it */
	ASC_AT_PMP_STOP,	/* the PMP's RUN cleared */
	ASC_AT_PMP_STOPPED,	/* the PMP's word after it */
	ASC_AT_SIO_UP,		/* SIO's parents and SIO_CPU raised */
	ASC_AT_SIO_LATE,	/* SIO's word */
	ASC_AT_DONE,		/* every step taken: the three bytes */
};

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

static inline u8 asc_ans_byte(bool prep, bool was_on, bool late, bool raised, bool load, bool load_read, bool back)
{
	return (u8)(ASC_PROBE_MARK | (prep ? 1U : 0) | (was_on ? 1U << 1 : 0) | (late ? 1U << 2 : 0) | (raised ? 1U << 3 : 0) |
		    (load ? 1U << 4 : 0) | (load_read ? 1U << 5 : 0) | (back ? 1U << 6 : 0));
}

/*
 * What a byte says bit 1 read in the state B2 finds: 1 set, 0 clear, -1 not read there. Not read: the byte didn't
 * land; the domain didn't read actual f for that read; or, for the PMP, its RUN wasn't cleared, so the state B2
 * finds was never made.
 */
static inline int asc_sio_final(u8 b)
{
	return !(b & ASC_PROBE_MARK) || !(b & (1U << 6)) ? -1 : !!(b & (1U << 3));
}

static inline int asc_pmp_final(u8 b)
{
	return !(b & ASC_PROBE_MARK) || !(b & (1U << 6)) || !(b & (1U << 5)) ? -1 : !!(b & (1U << 3));
}

static inline int asc_ans_final(u8 b)
{
	return !(b & ASC_PROBE_MARK) || !(b & ((1U << 1) | (1U << 3))) ? -1 : !!(b & (1U << 2));
}

static inline const char *asc_final_str(int final)
{
	return final < 0 ? "NOT READ" : final ? "SET" : "clear";
}

#endif /* ASC_PROBE_H */
