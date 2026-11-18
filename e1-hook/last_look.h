/* SPDX-License-Identifier: GPL-2.0 */
/*
 * The last look (PLAN.md, "Where things stand", the order after the strategy
 * review; RESEARCH.md, "The strategy review"): what CPU0 records if its own
 * last WFI returns, WD1's marker at a second time, and, in the run's second
 * form, a timed wake and a keyboard light that blinks. Arithmetic and
 * constants only, with no register access, for e1_hook.c and tests/ alike, as
 * wd1_mark.h is.
 *
 * Every run since run 11 has ended CPU0 in a deep WFI that nothing watches: a
 * WFI that returned was lost silently, and no record says the last core went
 * down at all. e1_hook last_look=1, on --m1-mark's recipe (run 17's, with none
 * of the later AP-made steps on B2's ops), changes nothing before the commit
 * but WD1's count, and adds one thing after CPU0's last record:
 *
 *   WD1's marker   bite 150 s as before, count 80 s where wd1_mark=1 writes
 *                  60 s: a marker that stands restarts the SoC about 70 s
 *                  after step 4, not 90. A restart that moves with it is
 *                  WD1's; one that stays at 90 s never was.
 *   the return     the first time CPU0's WFI returns, CPU0 sends seven bytes to
 *                  0x6008-0x600e (Corey's OK of 2026-11-16 for these PMU
 *                  bytes) in three commands, the safest reads first, and acks
 *                  nub-spmi0's vectors after each. The second and the
 *                  hundredth return send the first byte again with a new
 *                  count. All of it reads of words this module has read in
 *                  this state before; nothing is written but the PMU's bytes
 *                  and CPU0's own timer control (0, so that a timer that ended
 *                  one WFI doesn't end the next).
 *
 * The bytes, all 0 if the WFI never returned or the first command failed:
 *   0x6008  bit 7 set; bits 6:4 the time of the first return since the
 *           commit's last write, LL_T_MS's code; bits 1:0 how often it has
 *           returned: 1 once, 2 twice or more, 3 a hundred times or more.
 *           Bit 3 (LL_LOOKED) is gate_look=1's, below
 *   0x6009  bits 2:0 ISR_EL1's F, I and A at the first return; bit 3 CPU0's
 *           own timer was firing; bit 4 HS_A's bit 24; bits 5, 6, 7 HS_B's
 *           bits 0, 1 and 31
 *   0x600a  0x2922c0040's low byte (B2 clears its bit 1 at ops 4 to 6)
 *   0x600b  0x2922c0044's low byte (B2 polls its bits 2 and 3 at ops 1 and 3)
 *   0x600c  bit 7 set; bits 6:0 PHASE's low seven bits (0x2922c0110). The
 *           last byte of the second command: without its bit 7 the command
 *           isn't shown to have landed whole, and 0x6009-0x600c aren't read
 *   0x600d  bit 7 set; bit 6 FPWM0's power word read off; bits 5:0 how many
 *           of the 43 power words of SIO's subtree read off (sio_down.h's
 *           list, B2's ops 35 on)
 *   0x600e  bit 7 set; bits 4:0 the five PCIe power words read off
 *           (pcie_down.h's order); bit 5 ANS2's; bit 6 the PMP's
 * "Off" is an actual state of 0 (bits 7:4), which is what B2's 0 leaves.
 *
 * ll_wake=1, the second form (run B), adds three things to that run:
 *   the alarm      the PMU's RTC alarm 20 s after the commit's second, where
 *                  alarm=1 arms it an hour out. An SoC that slept comes out
 *                  through its reset vector, which here is the boot loader's:
 *                  a restart at about 20 s
 *   CPU0's timer   its own timer (the host's: CNTHP at EL2) set to fire 25 s
 *                  after step 10, unmasked. If the WFI returns for it, the
 *                  bytes above are a look at the engine 25 s in. A return
 *                  doesn't show the core was never down: Linux's own deep
 *                  idle here is ended by the core's timer too
 *   the light      in step 8, right before the commit: FPWM0's power word
 *                  asked on, and, if it reads on within 10 ms, the keyboard
 *                  backlight's PWM set to 100 ms on, 100 ms off (three writes,
 *                  as pwm-apple's apply makes them). B2 switches FPWM0 off as
 *                  the tenth of its 43 SIO words: a light that blinks until
 *                  the restart says B2 never got that far, one that stops says
 *                  when it did. Bit 6 of the alarm's byte at 0x6016 says the
 *                  three writes were made.
 * Run A (last_look=1 alone) arms none of the three, so that a WFI that returns
 * there means a pending source, and its ending compares with run 17's.
 *
 * gate_look=1, with either form (PLAN.md, after runs A and B, which left B2
 * before its power-downs with CPU0 in its last WFI), adds one thing:
 *   the look       in step 10, before CPU0's last WFI: once LL_LOOK_MS have
 *                  passed since the commit's last write, CPU0 reads what a
 *                  return would read and sends it itself, in two commands:
 *                  0x6008 := LL_LOOKED with 0x6009-0x600c, then 0x600d and
 *                  0x600e. The bytes are the return's, but for bit 3 of
 *                  0x6009: HS_A's bit 31, A2's raise for its first handshake
 *                  (no timer of CPU0's can be firing yet). A WFI that returns
 *                  after it sends 0x6008 alone, as above with LL_LOOKED kept,
 *                  and leaves the look's six bytes. K1 writes 0x6008 as
 *                  LL_ARMED, so a record of 04 and six zeros says a look
 *                  was to come and its first command never landed, where
 *                  seven zeros would read as a WFI that didn't return.
 * Runs 6 to 10 read the engine's words this way, CPU0 awake after the commit:
 * 0x2922c0044's bit 2 clear and bit 3 set, 0x2922c0040's bit 1 set, HS_A's bit
 * 31 up and bit 24 clear. Since run 11 CPU0 has gone straight down, and the
 * recipe has gained SIO's firmware stopped, the 20 power words, the cluster
 * words' ack and the fabric back at 3. This is that read under today's recipe.
 * It is a reading with a core up, which may itself hold B2's first poll: bit 2
 * set here takes the cores out of that poll; the old bits say nothing new, and
 * neither is the stall's own state after CPU0's WFI.
 *
 * The stages are --m1-mark's with other numbers, so that the decoder knows
 * which marker time and which alarm to expect.
 *
 * obs_gate=1 is not a form of last_look=1 but the same seven bytes in another
 * run: observe=1's, runs 2 and 3, where CPU7 stays up through the commit and
 * watches CPU0's and the E complex's power words read off (PLAN.md, after the
 * gate-look run). That run read 0x2922c0044 as 0x0b with CPU0 awake, bits 0
 * and 1 up where awake they are clear, and bit 2, B2's first poll, clear: a
 * fit for one bit per CPU complex, set when the complex is down. Here the
 * observer sits in another complex (P1) and reads the same word after CPU0
 * and its complex have gone, with the four power words beside it, twice:
 *   0x6008  bit 7 set; bits 0 to 3: CPU0's, ECPM's, PCPM0's and PCPM1's power
 *           words read off (actual state 0), read right before the words below
 *   0x6009  ll_b1()'s byte with nothing in bits 2:0: bit 3 HS_A's bit 31, bit
 *           4 its bit 24, bits 5, 6, 7 HS_B's bits 0, 1 and 31
 *   0x600a  0x2922c0040's low byte
 *   0x600b  0x2922c0044's low byte
 *   0x600c  bit 7 set; PHASE's low seven bits. The first command's last byte
 *   0x600d  0x2922c0044's low byte again, OG_LATER_MS later
 *   0x600e  bit 7 set; bits 6:3 the four power words read off again, in
 *           0x6008's order; bits 1:0 0x2922c0040's bits 1:0 then
 * K1 leaves 0x6008 as OG_ARMED, so a look that never landed shows. Reads only.
 */
#ifndef LAST_LOOK_H
#define LAST_LOOK_H

#ifndef __KERNEL__
#include <stdbool.h>
#include <stdint.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
#endif

#include "fabric_up.h"
#include "wd1_mark.h"

#define LL_AT			0x6008		/* the PMU's bytes 0x6008-0x600e */
#define LL_N			7

#define LL_WD1_COUNT		(80U * WD1_HZ)	/* 0x7270e000: the marker 70 s after step 4 */

#define LL_ALARM_SECS		20		/* ll_wake=1: the alarm, after the commit's second */
#define LL_TIMER_S		25		/* ll_wake=1: CPU0's timer, after step 10 */

#define LL_PWM			0x39b030000ULL	/* fpwm0, the keyboard backlight's PWM (pwm@39b030000) */
#define LL_PWM_CTRL		0x00
#define LL_PWM_OFF		0x18
#define LL_PWM_ON		0x1c
#define LL_PWM_GO		0x4021U		/* enable, update, output enable: what pwm-apple writes */
#define LL_BLINK_CYCLES		(WD1_HZ / 10)	/* 100 ms of the 24 MHz clock, on and off alike */
#define LL_FPWM0		0x190		/* its power word, in SIO's page: 0x28e580190 */
#define LL_HS_B			0x10c		/* in the engine's page: 0x2922c010c */
#define LL_BLINK_BIT		(1U << 6)	/* in 0x6016, the alarm's byte, which never sets it */

#define LL_PS_ACTUAL		0xf0U

#define LL_MARK			(1U << 7)
#define LL_LOOKED		(1U << 3)	/* in 0x6008: 0x6009-0x600e are gate_look=1's, read before CPU0's WFI */
#define LL_ARMED		(1U << 2)	/* 0x6008 as K1 leaves it with gate_look=1, until the look's own command */
#define LL_LOOK_MS		1000		/* gate_look=1: after the commit's last write */
#define LL_T_SHIFT		4
#define LL_T_MS			{ 10, 100, 1000, 5000, 15000, 22000, 30000 }
#define LL_T_LATE		7		/* 30 s or more */
#define LL_MANY			100

#define M1_LL_STAGE_PRE		0x09	/* in place of 0x52 */
#define M1_LL_STAGE_PEND	0x0a	/* in place of 0x51 */
#define M1_LL_STAGE		0x0b	/* in place of 0x53 */
#define M1_LLW_STAGE_PRE	0x06	/* ll_wake=1's */
#define M1_LLW_STAGE_PEND	0x07
#define M1_LLW_STAGE		0x08

#define OG_MARK			(1U << 7)
#define OG_ARMED		(1U << 6)	/* 0x6008 as K1 leaves it with obs_gate=1, until CPU7's first command */
#define OG_LATER_MS		500		/* the second sample, after the first */

/* The four power words, each true if it read off: 0x6008. */
static inline u8 og_b0(bool cpu0, bool ecpm, bool pcpm0, bool pcpm1)
{
	return (u8)(OG_MARK | (cpu0 ? 1U : 0) | (ecpm ? 1U << 1 : 0) | (pcpm0 ? 1U << 2 : 0) | (pcpm1 ? 1U << 3 : 0));
}

/* The second sample's last byte: the four again, and 0x2922c0040's bits 1:0. */
static inline u8 og_b6(u32 gate40, bool cpu0, bool ecpm, bool pcpm0, bool pcpm1)
{
	return (u8)(OG_MARK | (og_b0(cpu0, ecpm, pcpm0, pcpm1) & 0xf) << 3 | (gate40 & 3));
}

/* dt: ticks since the commit's last write; tps: ticks a second. */
static inline u8 ll_time_code(u64 dt, u64 tps)
{
	static const u16 ms[] = LL_T_MS;
	u8 i;

	for (i = 0; i < sizeof(ms) / sizeof(ms[0]); i++) {
		if (dt < tps * ms[i] / 1000)
			return i;
	}
	return LL_T_LATE;
}

static inline u8 ll_count_code(u32 n)
{
	return n >= LL_MANY ? 3 : n >= 2 ? 2 : n ? 1 : 0;
}

static inline u8 ll_b0(u32 n, u8 tcode)
{
	return (u8)(LL_MARK | (tcode & 7) << LL_T_SHIFT | ll_count_code(n));
}

/* isr: ISR_EL1's bits 8:6, as F, I, A in bits 0, 1, 2; tmr: the timer's ISTATUS; the two handshake words whole. */
static inline u8 ll_b1(u8 isr, bool tmr, u32 hs_a, u32 hs_b)
{
	return (u8)((isr & 7) | (tmr ? 1U << 3 : 0) | (hs_a & (1U << 24) ? 1U << 4 : 0) |
		    (hs_b & 1 ? 1U << 5 : 0) | (hs_b & 2 ? 1U << 6 : 0) | (hs_b & (1U << 31) ? 1U << 7 : 0));
}

/* PHASE's low seven bits under the mark: 0x600c, the second command's last byte. */
static inline u8 ll_b4(u32 phase)
{
	return (u8)(LL_MARK | (phase & 0x7f));
}

/* A power word whose actual state reads 0. */
static inline bool ll_off(u32 v)
{
	return !(v & LL_PS_ACTUAL);
}

/* n: how many of SIO's 43 read off; fpwm0: FPWM0's own. */
static inline u8 ll_b5(u8 n, bool fpwm0)
{
	return (u8)(LL_MARK | (fpwm0 ? 1U << 6 : 0) | (n & 0x3f));
}

/* pcie: the five PCIe words, one bit each, set if off; ans, pmp: ANS2's and the PMP's. */
static inline u8 ll_b6(u8 pcie, bool ans, bool pmp)
{
	return (u8)(LL_MARK | (pmp ? 1U << 6 : 0) | (ans ? 1U << 5 : 0) | (pcie & 0x1f));
}

/* fabric_up=1's stages (--m1-mark's) to the last look's; any other stays. */
static inline u8 ll_stage(u8 s, bool wake)
{
	switch (s) {
	case M1_FAB_STAGE_PRE:
		return wake ? M1_LLW_STAGE_PRE : M1_LL_STAGE_PRE;
	case M1_FAB_STAGE_PEND:
		return wake ? M1_LLW_STAGE_PEND : M1_LL_STAGE_PEND;
	case M1_FAB_STAGE:
		return wake ? M1_LLW_STAGE : M1_LL_STAGE;
	}
	return s;
}

#endif /* LAST_LOOK_H */
