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
 * ll_retn=1, on ll_wake=1 and never with gate_look=1 (RESEARCH.md, "The desk
 * step of 2026-11-20", what is left of the idea, (a)), changes how CPU0 takes
 * that WFI, and nothing before it.
 * Every run so far has ended with all cores in a deep WFI and B2 stuck before
 * its power-downs, in a state no core can read. CYC_OVRD's bit 0 is XNU's
 * disWfiRetn, "disable WFI retention": the recipe sets it (0x3000001), as
 * macOS does, and a WFI then can't come back at the next instruction with its
 * state kept. Linux's own idle on this chip has bits 25:24 at 3 with bit 0
 * clear, and comes back on the core's timer. Untried: the recipe's last core
 * taking its last WFI that way, ACC_OVRD's deep-sleep fields set as in every
 * run. If its complex (E) still goes down in the engine's sense and CPU0
 * comes back, CPU0 can read what the engine did meanwhile:
 *   the WFI        CPU0's CYC_OVRD is written 0x3000000 in place of 0x3000001
 *                  (LLR_CYC_RETN: bits 25:24, bit 0 left clear), for its last
 *                  WFI and until its look. ll_wake=1's timer, LL_TIMER_S out,
 *                  is what is to end it. One system register's value, on CPU0
 *                  alone: the other cores' deep WFI is as it was
 *   empty returns  a retention WFI can return with nothing pending (Linux's
 *                  idle loops on ISR_EL1 reading 0). Each such return is
 *                  counted and the WFI taken again, up to LLR_SPURIOUS_MAX of
 *                  them, with nothing read but ISR_EL1 and nothing sent
 *   the look       at the first return with ISR_EL1 non-zero, or at the
 *                  LLR_SPURIOUS_MAX'th empty one: once, what a first return
 *                  of last_look=1's does (the clock, ISR_EL1, the timer's
 *                  status, its control written 0), and three commands in that
 *                  record's order and manner, the second with two reads CPU0
 *                  hasn't made in this state: the E complex's state word
 *                  (0x28e09a000) and EACC's soc-cluster word (0x28e098000),
 *                  which the observer, CPU7, read after a commit (the second
 *                  form above). They come last among that command's reads,
 *                  after the words CPU0 has read in this state before
 *   afterwards     CYC_OVRD's bit 0 set again (0x3000001), and every later
 *                  WFI is the recipe's own: its returns are counted and sent
 *                  to 0x6008 as last_look=1's are
 * The record's own form, known by the run's stages (the third set below):
 *   0x6008  as last_look=1's: bit 7; bits 6:4 the time's code of the return
 *           the look was made at; bits 1:0 the count, the look's return as
 *           the first (the empty ones before it aren't in it)
 *   0x6009  as last_look=1's, ll_b1(): ISR_EL1's F, I and A, CPU0's timer
 *           firing, HS_A's bit 24, HS_B's bits 0, 1 and 31
 *   0x600a  the E complex's state word, low byte (0x44 awake since boot: a
 *           complex the sequencer hasn't cycled; a down sequence ends on
 *           0x1a, an up sequence on 0x40)
 *   0x600b  bits 3:0 0x2922c0044's low nibble; bits 5:4 0x2922c0040's bits
 *           1:0 (3 awake: B2 clears bit 1 at its ops 4 to 6); bit 6 "wide":
 *           one of the state word above bit 7, 0x2922c0040 above bit 1, PHASE
 *           above bit 3 and EACC's word above bit 6 has a bit these bytes
 *           don't carry (llr_wide()); bit 7: the look was made at the
 *           LLR_SPURIOUS_MAX'th empty return, not for something pending
 *   0x600c  bit 7 set; bits 6:4 EACC's bits 6:4; bits 3:0 PHASE's low
 *           nibble. The second command's last byte: without its bit 7 none
 *           of 0x6009-0x600c is read
 *   0x600d  as last_look=1's, ll_b5()
 *   0x600e  as last_look=1's, ll_b6(). These two, the third command, were
 *           lost across the restart in three runs: best effort
 * All 0 if the WFI never returned, or never with anything pending before the
 * restart, or the first command failed. K1 zeroes the seven as in any
 * last_look=1 run. Nothing is written but the PMU's bytes, CPU0's timer
 * control and its CYC_OVRD: no MMIO write is new.
 *
 * The stages are --m1-mark's with other numbers, so that the decoder knows
 * which marker time and which alarm to expect, and with ll_retn=1 which form
 * the record has.
 *
 * obs_gate=1 is not a form of last_look=1 but the same seven bytes in another
 * run: observe=1's, runs 2 and 3, where CPU7 stays up through the commit and
 * watches CPU0's and the E complex's power words read off (PLAN.md, after the
 * gate-look run). That run read 0x2922c0044 as 0x0b with CPU0 awake, bits 0
 * and 1 up where awake they are clear, and bit 2, B2's first poll, clear: a
 * fit for one bit per CPU complex, set when the complex is down. The observer
 * sits in another complex (P1) and looks after CPU0 and its complex have
 * gone. Its record has had two forms.
 *
 * The first (the run of 2026-11-18; m1-crumbs still reads it; bits 6:4 of
 * 0x6008 clear), the engine's words beside the four power words, twice:
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
 * It read 0x09: bit 0 P complex 0's, bit 1 P complex 1's, and bit 2 clear
 * with CPU0's and ECPM's power words off.
 *
 * The second, what the module sends now (OG_CLUS, bit 4 of 0x6008): the
 * complexes' own words. Each CPU complex has a down sequence in the reconfig
 * SRAM, run by the clusters' sequencer and not by the entry pair: it waits,
 * untimed, for bit 6 of the complex's soc-cluster word, writes 0x51 to the
 * complex's state word (0x28e09a000 + 4n), waits, untimed, for bit 5 of that
 * to clear, steps it through 0x53, 0x5b and 0x1b, and ends by writing 0x1a.
 * An up sequence ends by writing 0x40. So the state word says whether a
 * complex's own sequence ran to its end, which its power word doesn't, and
 * P complex 0, which reads off, is there to compare the E complex with:
 *   0x6008  bit 7 and OG_CLUS set; bits 0 to 3 as in the first form
 *   0x6009  bit 7 set; EACC's soc-cluster word (0x28e098000), bits 6:0. The
 *           first command's last byte: words CPU7 has read after a commit
 *           before, sent before a state word is touched
 *   0x600d  the E complex's state word, 0x28e09a000, low byte, right then
 *   0x600e  bit 7 set; bits 6:4 PACC0's soc-cluster word's bits 6:4; bits 3:0
 *           0x2922c0044's, both as read for the first command. The second
 *           command's last byte
 *   0x600a  the E complex's state word again, OG_LATER_MS later
 *   0x600b  P complex 0's, 0x28e09a004, low byte, then
 *   0x600c  bit 7 set; bit 6: one of the four words has a bit above what its
 *           byte carries (og_wide()); bits 5:3 EACC's bits 6:4 then; bits 2:0
 *           0x2922c0044's then. The third command's last byte
 * 0x600d and 0x600e were lost across the restart in both runs that wrote
 * them, so they get the first sample and the five that have always come
 * through get the later one: a sequence that is only slow must not read as
 * one that is held. The state words have never been read on this Mac: the
 * module reads them awake at its load first, where a read that faults costs a
 * safe boot and nothing else.
 * K1 leaves 0x6008 as OG_ARMED, so a look that never landed shows, and zeroes
 * the seven bytes in every observe=1 run, with obs_gate=1 or not: CPU7's stages
 * are an ordinary observer's too, and a record left there would read as its.
 * Reads only.
 *
 * wake_all=1 is neither of these: one byte, 0x6008 alone, in a run of the
 * gates_mark=1 family, which last_look=1 refuses and which has no observer
 * (PLAN.md, after the a2_pre=1 run with the light). Step 7 writes the seven AP
 * wake-mask words, 0x292004008 to 0x292004020, where a set bit masks a
 * source. The recipe's values leave source 54 open (NUB.SPMISw3IRQ, the PMU's
 * interrupt: word 1's bit 22), and word 6 is macOS's own 0x00001fff, which
 * leaves ids 0xcd to 0xdf open (its bits 31:13, none with a name). With
 * wake_all=1 all seven are written 0xffffffff: if a pending source that the
 * masks let through is what holds the engine before its power-downs, that
 * closes every one. Right after, still in step 7, CPU0 reads the seven back,
 * then the seven words after them (0x292004024 on: the AP's wake status,
 * inferred). The byte they make goes out in step 8, after that step's own
 * record (the RTC's second and 0x6010-0x6016): a command that fails stops
 * every later one, and this one is new. It is:
 *   0x6008  bits 7:6 set (WA_MARK); bit 5 (WA_NAMED): words 0 to 5 read back
 *           0xffffffff and word 6's bits 12:0 read back all set, every id
 *           with a name; bit 4 (WA_UNNAMED): word 6's bits 31:13 read back
 *           all set too (whether those bits exist isn't known: macOS's value
 *           leaves them clear); bit 3 always 0; bits 2:0 how many of the
 *           seven status words read non-zero then (which ones isn't kept)
 * K1 zeroes the seven bytes and leaves 0x6008 as WA_ARMED, so a byte that
 * never landed shows; 0x6009-0x600e stay 0. (K1 zeroes them in every run
 * since this option, with it or not: a byte or a mark an earlier run left
 * would read as a later run's under the stages the two share.) The byte's two
 * top bits tell it from K1's three marks (0x04, 0x20, 0x40); the stage tells
 * it from the other two records, whose first byte has bit 7 too. wa_ok() is
 * all that m1_pmu_ok() lets a wake_all=1 run write there, beside the zeros.
 * With every source masked, a Mac whose entry completed would wake for no
 * key and no lid: it comes back only by holding the power button.
 * Reads only, apart from the PMU's byte, but neither read has been made in
 * this state before. Every m1 run has written the seven masks in step 7, and
 * they have been read only awake, at the load. The status words have been
 * read around the commit only in observe=1's runs, which have none of
 * hold=1, sio_stop=1, ps_words=1 and atc_down=1. A read that faulted would
 * panic there, SError still unmasked: the record would be step 7's stage
 * (0x39; 0xb9 with pmp_out=1's bit) with WA_ARMED at 0x6008, and the restart
 * would come about 10 s later, if the panic's restart works.
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
#define M1_LLR_STAGE_PRE	0x14	/* ll_retn=1's: the record at 0x6008-0x600e has its own form */
#define M1_LLR_STAGE_PEND	0x18
#define M1_LLR_STAGE		0x1c

#define LLR_CYC_DEEP		0x3000001ULL	/* CYC_OVRD as the recipe's deep WFI has it: bits 25:24, and bit 0, XNU's disWfiRetn */
#define LLR_CYC_NO_RETN		(1ULL << 0)	/* that bit: "disable WFI retention" */
#define LLR_CYC_RETN		(LLR_CYC_DEEP & ~LLR_CYC_NO_RETN)	/* 0x3000000: ll_retn=1, CPU0's last WFI until its look */
#define LLR_SPURIOUS_MAX	1000		/* ll_retn=1: the look is made at this many returns with nothing pending */
#define LLR_EMPTY		(1U << 7)	/* in 0x600b: it was */
#define LLR_WIDE		(1U << 6)	/* in 0x600b: llr_wide() */

#define OG_MARK			(1U << 7)
#define OG_ARMED		(1U << 6)	/* 0x6008 as K1 leaves it with obs_gate=1, until CPU7's first command */
#define OG_LATER_MS		500		/* the second sample, after the first */
#define OG_CLUS			(1U << 4)	/* 0x6008: the record's second form, the complexes' own words */
/* The complexes' state words, from the soc-cluster page (0x28e098000): 0x28e09a000 + 4n. P complex 1's is read awake only. */
#define OG_ST_E			0x2000
#define OG_ST_P0		0x2004
#define OG_ST_P1		0x2008

#define WA_WORDS			7		/* the AP wake-mask words, and the status words after them */
#define WA_W6_NAMED		0x00001fffU	/* word 6's ids with a name: bits 12:0, macOS's own value for the word */
#define WA_ARMED		(1U << 5)	/* 0x6008 as K1 leaves it with wake_all=1, until the byte's command in step 8 */
#define WA_MARK			0xc0U		/* 0x6008, wake_all=1's byte: bits 7:6, both */
#define WA_NAMED		(1U << 5)	/* under WA_MARK: words 0 to 5 and word 6's bits 12:0 read back all set */
#define WA_UNNAMED		(1U << 4)	/* under WA_MARK: word 6's bits 31:13 read back all set too */

/*
 * wake_all=1's byte at 0x6008. named: words 0 to 5 read back 0xffffffff and word 6's bits 12:0 all set; unnamed: word 6's
 * bits 31:13 all set; status: how many of the seven status words read non-zero. Bit 3 stays 0.
 */
static inline u8 wa_byte(bool named, bool unnamed, unsigned int status)
{
	return (u8)(WA_MARK | (named ? WA_NAMED : 0) | (unnamed ? WA_UNNAMED : 0) | (status > 7 ? 7 : status));
}

/* What a wake_all=1 run may write to 0x6008 by itself: K1's mark, or a byte wa_byte() makes (bits 7:6 set, bit 3 clear). */
static inline bool wa_ok(u8 v)
{
	return v == WA_ARMED || ((v & WA_MARK) == WA_MARK && !(v & (1U << 3)));
}

/* The four power words, each true if it read off: 0x6008. */
static inline u8 og_b0(bool cpu0, bool ecpm, bool pcpm0, bool pcpm1)
{
	return (u8)(OG_MARK | (cpu0 ? 1U : 0) | (ecpm ? 1U << 1 : 0) | (pcpm0 ? 1U << 2 : 0) | (pcpm1 ? 1U << 3 : 0));
}

/* 0x6009: EACC's soc-cluster word, bits 6:0, under the mark. */
static inline u8 og_c1(u32 eacc)
{
	return (u8)(OG_MARK | (eacc & 0x7f));
}

/* Whether one of the four words has a bit above what its byte carries: bits 6:0 of a soc-cluster word, 7:0 of a state word. */
static inline bool og_wide(u32 eacc, u32 pacc0, u32 st_e, u32 st_p0)
{
	return ((eacc | pacc0) >> 7) || ((st_e | st_p0) >> 8);
}

/* 0x600c: the mark, og_wide(), EACC's bits 6:4, and 0x2922c0044's bits 2:0, at the later sample. */
static inline u8 og_c4(bool wide, u32 eacc, u32 gate44)
{
	return (u8)(OG_MARK | (wide ? 1U << 6 : 0) | (eacc >> 4 & 7) << 3 | (gate44 & 7));
}

/* 0x600e: the mark, PACC0's bits 6:4, and 0x2922c0044's low nibble, at the first sample. */
static inline u8 og_c6(u32 pacc0, u32 gate44)
{
	return (u8)(OG_MARK | (pacc0 >> 4 & 7) << 4 | (gate44 & 0xf));
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

/* ll_retn=1's record. 0x600a: the E complex's state word, low byte. */
static inline u8 llr_b2(u32 st_e)
{
	return (u8)st_e;
}

/* Whether one of the four words has a bit the bytes don't carry: the state word above bit 7, 0x2922c0040 above bit 1, PHASE above bit 3, EACC's above bit 6. */
static inline bool llr_wide(u32 st_e, u32 gate40, u32 phase, u32 eacc)
{
	return (st_e >> 8) || (gate40 >> 2) || (phase >> 4) || (eacc >> 7);
}

/* 0x600b: 0x2922c0044's low nibble, 0x2922c0040's bits 1:0, llr_wide(), and whether the look was made at the last empty return. */
static inline u8 llr_b3(u32 gate44, u32 gate40, bool wide, bool empty)
{
	return (u8)((gate44 & 0xf) | (gate40 & 3) << 4 | (wide ? LLR_WIDE : 0) | (empty ? LLR_EMPTY : 0));
}

/* 0x600c: the mark, EACC's bits 6:4 and PHASE's low nibble: the second command's last byte. */
static inline u8 llr_b4(u32 eacc, u32 phase)
{
	return (u8)(LL_MARK | (eacc >> 4 & 7) << 4 | (phase & 0xf));
}

/* fabric_up=1's stages (--m1-mark's) to the last look's, ll_wake=1's or, first, ll_retn=1's; any other stays. */
static inline u8 ll_stage(u8 s, bool wake, bool retn)
{
	switch (s) {
	case M1_FAB_STAGE_PRE:
		return retn ? M1_LLR_STAGE_PRE : wake ? M1_LLW_STAGE_PRE : M1_LL_STAGE_PRE;
	case M1_FAB_STAGE_PEND:
		return retn ? M1_LLR_STAGE_PEND : wake ? M1_LLW_STAGE_PEND : M1_LL_STAGE_PEND;
	case M1_FAB_STAGE:
		return retn ? M1_LLR_STAGE : wake ? M1_LLW_STAGE : M1_LL_STAGE;
	}
	return s;
}

#endif /* LAST_LOOK_H */
