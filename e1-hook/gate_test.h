/* SPDX-License-Identifier: GPL-2.0 */
/*
 * The gate test (PLAN.md, "The order after the look"; RESEARCH.md, "The look's
 * run" and "The gate test as built"): what e1_hook probe=1 look=1 gate=1 does
 * after its second look, and the record it leaves. Constants and arithmetic
 * only, for e1_hook.c and tests/ alike.
 *
 * B2, the second sequence of the engine's entry pair, switches SIO's whole
 * subtree off and then waits, 255 tries, for bit 30 of two gate words,
 * 0x28e5a0064 and 0x28e5a0050. The look found that bit 30 of a gate word follows
 * what's behind the gate, that 0x28e5a0064 reads set whatever is on, and that
 * 0x28e5a0050 reads clear after Linux's own suspend, with all of SIO's subtree
 * off but five UARTs nothing uses, left at 4 by the boot loader.
 *
 * One more thing stands between Linux's state and macOS's there. SIO's parent
 * is the fabric lane AFNC2_LW1, 0x28e580160. macOS has it at 0x1000030f at sleep
 * entry: on, with auto power-gating enabled and an auto floor of 0, so that it
 * may gate itself once nothing below it is on. Linux has 0x1f0000ff: the same
 * with a floor of f (it has been seen to gain its two was-gated flags across an
 * s2idle all the same, so a floor of f isn't known to keep it from gating).
 * ps_words=1 has written macOS's value in every run since run 12, with three
 * more words of the kind, and every one of those runs recorded that the four
 * didn't all read back as written. Which didn't, and what it read, the one bit
 * there doesn't say.
 *
 * So, after the look's second pass, in the suspend that is then failed:
 *   0. everything read as found, the lane's other children among it. The test
 *      isn't made, and nothing is written, if SIO itself reads on or one of the
 *      five UARTs has a target of f;
 *   1. the five UARTs := 0x200, ps_words=1's value for them, and up to 10 ms
 *      each for actual 0: all of SIO's subtree is then off, as after B2's op 138;
 *   2. up to 100 ms for bit 30 of 0x28e5a0050;
 *   3. the lane and the gate word read once more, so that neither a lane that
 *      had gated at Linux's own floor nor a gate that closed late by itself is
 *      put down to the write; then AFNC2_LW1 := 0x1000030f, read back at once
 *      and for up to 10 ms;
 *   4. up to 100 ms for bit 30 again, and AFNC2_LW1 read once more;
 *   5. AFNC2_LW1 written back as found, then the five UARTs, each less the bits
 *      the kernel never writes back (asc_probe.h's ASC_PS_NOT_WRITTEN), up to
 *      10 ms each; and the lane read last, with everything back, since its
 *      actual state follows what's below it.
 * Every word is logged in full at each step, with all six gate words.
 *
 * What it can say: whether the gate B2 waits on closes in Linux's state once
 * SIO's subtree is all off; if not, whether the lane takes macOS's value, and
 * whether the gate reads closed after that. "After", not "because of": one
 * before and one after is all there is. What it can't: the recipe's state is
 * further on (the PMP and SIO's firmware stopped, the cores parked, macOS's
 * table), and B2 polls after its own writes.
 *
 * What it risks, by design: if the gate closes, SIO's island loses power with
 * SIO's firmware running, which Linux's own suspend hasn't been seen to do here
 * (the gate read open after it). The kernel has no way to bring SIO's firmware
 * back, and the one resume tried with it gone, the probe's, didn't come back.
 * So the result this test is after may be the one it doesn't resume from, and
 * the record in the PMU is made to stand without the kernel log:
 *   - the look's stage, 0x0f, with the step about to be taken at 0x6010,
 *     GATE_PASS set in it;
 *   - 0x6005 zeroed at the start, and gate_byte() there at the end;
 *   - six bytes at 0x6011-0x6016 before anything is written back, gate_pack()
 *     of the lane three times: right before its write, after it, and after the
 *     second poll.
 */
#ifndef GATE_TEST_H
#define GATE_TEST_H

#ifndef __KERNEL__
#include <stdbool.h>
#include <stdint.h>
typedef uint8_t u8;
typedef uint32_t u32;
#endif

/* In SIO's page, 0x28e580000: the lane above SIO, and the five UARTs still at 4 after Linux's suspend. */
#define GATE_LANE		0x160		/* AFNC2_LW1 */
#define GATE_LANE_VAL		0x1000030fU	/* macOS's at sleep entry, ps_words=1's: on, auto on, auto floor 0 */
#define GATE_UARTS		{ 0x250, 0x258, 0x260, 0x268, 0x270 }	/* UART2, 3, 4, 6, 7 */
#define GATE_UART_N		5
#define GATE_UART_VAL		0x00000200U	/* ps_words=1's: target 0 */
/* Read as found, never written: SIO's own word, and the lane's other children in the device tree (DISPDFR_FE and BE, MIPI_DSI, ISP_SYS). */
#define GATE_KIDS		{ 0x180, 0x378, 0x380, 0x388, 0x3a8 }
#define GATE_KID_N		5
/* What a write of a power word sets, and so what "it took" compares: auto-enable, the auto floor, the target. */
#define GATE_PS_SET		((1U << 28) | (0xfU << 24) | 0xfU)
#define GATE_PS_ACTUAL		(0xfU << 4)

/* In the first gate page, 0x28e5a0000: the gate word B2 polls after SIO's subtree that reads clear after Linux's suspend. */
#define GATE_SIO		0x050
#define GATE_CLOSED		(1U << 30)

/* The steps, each written to the look's 0x6010 before it's taken, with GATE_PASS set. m1-crumbs has a line for each. */
#define GATE_PASS		0x20
enum gate_at {
	GATE_AT_NONE,
	GATE_AT_READ,		/* everything read as found */
	GATE_AT_UARTS,		/* the five UARTs := 0x200 */
	GATE_AT_WAIT1,		/* the gate polled, SIO's subtree all off */
	GATE_AT_LANE,		/* AFNC2_LW1 := 0x1000030f */
	GATE_AT_WAIT2,		/* the gate polled again */
	GATE_AT_LANE_BACK,	/* AFNC2_LW1 written back as found */
	GATE_AT_UARTS_BACK,	/* the five UARTs written back as found */
	GATE_AT_DONE,		/* every step taken: the byte */
};

/* A power word took a written value: its auto-enable, auto floor and target read as that value's. */
static inline bool gate_took(u32 v, u32 written)
{
	return !((v ^ written) & GATE_PS_SET);
}

/* A power word's actual state, in place, reads want: 0 is off. */
static inline bool gate_actual(u32 v, u32 want)
{
	return (v & GATE_PS_ACTUAL) == want;
}

/* A power word's target reads f: something has it on. */
static inline bool gate_wanted(u32 v)
{
	return (v & 0xfU) == 0xfU;
}

/*
 * Two bytes of a power word, for the PMU: p[0] its actual state and target, bits 7:0; p[1] its auto floor in bits
 * 3:0, auto-enable in bit 4, and bits 8, 9 and 11 of the word (was power-gated, was clock-gated, BUSY) in bits 5, 6 and 7.
 */
static inline void gate_pack(u32 v, u8 *p)
{
	p[0] = (u8)v;
	p[1] = (u8)(((v >> 24) & 0x1fU) | (v & (1U << 8) ? 1U << 5 : 0) | (v & (1U << 9) ? 1U << 6 : 0) | (v & (1U << 11) ? 1U << 7 : 0));
}

/* A power word is back as found: those three, and its actual state. */
static inline bool gate_back(u32 v, u32 found)
{
	return !((v ^ found) & (GATE_PS_SET | GATE_PS_ACTUAL));
}

/*
 * The byte at 0x6005. Bit 7 says it landed. 0: the five UARTs all read actual 0 within 10 ms; 1: bit 30 of
 * 0x28e5a0050 read set before the lane's write, within 100 ms of the subtree going off or at the read right before
 * that write; 2: AFNC2_LW1 took macOS's value within 10 ms; 3: bit 30 read set within 100 ms after that; 4:
 * AFNC2_LW1's actual state read 0 then; 5: AFNC2_LW1 read as found within 10 ms, with everything written back;
 * 6: all five UARTs read as found within 10 ms of their write-back. A test that wasn't made (step 0) leaves 0xe0:
 * nothing went off, and nothing had to come back.
 */
#define GATE_MARK		(1U << 7)
static inline u8 gate_byte(bool uarts_off, bool closed1, bool lane_took, bool closed2, bool lane_gated, bool lane_back, bool uarts_back)
{
	return (u8)(GATE_MARK | (uarts_off ? 1U : 0) | (closed1 ? 1U << 1 : 0) | (lane_took ? 1U << 2 : 0) | (closed2 ? 1U << 3 : 0) |
		    (lane_gated ? 1U << 4 : 0) | (lane_back ? 1U << 5 : 0) | (uarts_back ? 1U << 6 : 0));
}

#endif /* GATE_TEST_H */
